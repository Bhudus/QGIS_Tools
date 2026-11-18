"""Design calculations for the drainage and subirrigation reports (numpy, no QGIS)."""
import math

import numpy as np

from .sinkfill import fill_sinks_wang_liu
from . import terrain

ROUTING_EPS_Z = 1e-4      # rise per cell on flats of the routing surface (as in Depressions
                          # and Laylines)
FT2_PER_AC = 43560.0


# ---------------------------------------------------------------- pipes
def round_to(v, step):
    return round(v / step) * step


def pipe_schedule(pipes):
    """pipes: dicts id, kind, size, a ((n, 4) x, y, z ft, ground ft; x/y in ft).
    Returns rows: id, kind, size, length (ft, nearest 10), slope (%, nearest 0.05),
    depth range (ft) and label text."""
    order = {"main": 0, "submain": 1, "lateral": 2}
    rows = []
    for p in pipes:
        a = np.asarray(p["a"], float)
        if len(a) < 2:
            continue
        L = float(np.hypot(np.diff(a[:, 0]), np.diff(a[:, 1])).sum())
        if L <= 0:
            continue
        z = a[:, 2]
        slope = abs(z[0] - z[-1]) / L * 100.0 if np.isfinite(z[[0, -1]]).all() else None
        d = a[:, 3] - a[:, 2]
        d = d[np.isfinite(d)]
        rec = {"id": p.get("id"), "kind": (p.get("kind") or "tile"), "size": p.get("size") or "",
               "length": max(10.0, round_to(L, 10.0)), "length_exact": L,
               "slope": round_to(slope, 0.05) if slope is not None else None,
               "depth_min": float(d.min()) if len(d) else None,
               "depth_max": float(d.max()) if len(d) else None}
        bits = [rec["size"]] if rec["size"] else []
        bits.append("{:,.0f} ft".format(rec["length"]))
        if rec["slope"] is not None:
            bits.append("{:.2f}%".format(rec["slope"]))
        rec["label"] = " ".join(bits)
        rows.append(rec)
    rows.sort(key=lambda r: (order.get(str(r["kind"]).lower(), 3), str(r["id"])))
    return rows


def depth_range(arrays):
    """(min, max) depth (ground - tile) over polyline vertices, ft."""
    lo, hi = math.inf, -math.inf
    for a in arrays:
        a = np.asarray(a, float)
        d = a[:, 3] - a[:, 2]
        d = d[np.isfinite(d)]
        if len(d):
            lo, hi = min(lo, d.min()), max(hi, d.max())
    return (lo, hi) if lo <= hi else None


def irrigated_buffer(spacing, recommended):
    """IL subirrigation rule: half the lateral spacing outside the tile; if the spacing
    exceeds two thirds of the recommended spacing, a third of the recommended spacing.
    Returns (distance ft, explanation)."""
    if recommended and spacing and spacing > 2.0 / 3.0 * recommended:
        d = recommended / 3.0
        return d, ("lateral spacing {:.0f} ft exceeds two thirds of the recommended {:.0f} "
                   "ft: tile buffered by a third of the recommended spacing ({:.0f} ft)"
                   .format(spacing, recommended, d))
    if spacing:
        return spacing / 2.0, ("tile buffered by half the lateral spacing ({:.0f} ft / 2 = "
                               "{:.0f} ft)".format(spacing, spacing / 2.0))
    if recommended:
        return recommended / 2.0, ("tile buffered by half the recommended spacing "
                                   "({:.0f} ft)".format(recommended / 2.0))
    return None, ""


def impacted_mask(zone_mask, area_mask, ground, control, interval):
    """Cells impacted by a structure: its zone within the drained/irrigated area; when
    the structures are set more than 1 ft apart, only ground within 1 ft above the
    control elevation."""
    m = zone_mask & area_mask & np.isfinite(ground)
    if interval is not None and interval > 1.0:
        m &= ground <= control + 1.0
    return m


# ---------------------------------------------------------------- terrain
def depressions_and_runon(z, field_mask, cell_ft, depth_thr=0.75, min_dep_ac=0.05,
                          min_runon_ac=0.5, progress=None, is_canceled=None):
    """Depressions inside the field and run-on areas outside it, from a DEM in ft.

    Depressions: cells of the field deeper than `depth_thr` below the Wang & Liu filled
    surface, 8-connected. Run-on: cells outside the field whose D8 flow path (over the
    filled surface with a small gradient) enters the field.
    Returns dict: dep_labels, deps (list of stats), run_labels, runs (stats),
    filled, edge (True if run-on reaches the edge of the DEM window)."""
    nod = ~np.isfinite(z)
    zz = np.where(nod, 0.0, z)
    cell_ac = cell_ft * cell_ft / FT2_PER_AC
    filled = fill_sinks_wang_liu(zz, nod, cell_ft, 0.0, 1.0,
                                 progress=(lambda f: progress(0.4 * f)) if progress else None,
                                 is_canceled=is_canceled)
    depth = terrain.depression_depth(zz, filled, nod)
    dmask = (depth > depth_thr) & field_mask & ~nod
    dlab, n = terrain.label_regions(dmask)
    stats = terrain.region_stats(dlab, n, depth, filled, cell_ft * cell_ft)
    deps = []
    keep = np.zeros(n + 1, bool)
    for s in stats:
        s["area_ac"] = s["cells"] * cell_ac
        s["volume_cy"] = s["volume"] / 27.0
        if s["area_ac"] >= min_dep_ac:
            deps.append(s)
            keep[s["id"]] = True
    dlab = np.where(keep[dlab], dlab, 0)
    deps.sort(key=lambda s: -s["area_ac"])

    eps_deg = math.degrees(math.atan(ROUTING_EPS_Z / cell_ft))
    zr = fill_sinks_wang_liu(filled, nod, cell_ft, eps_deg, 1.0,
                             progress=(lambda f: progress(0.4 + 0.4 * f)) if progress else None,
                             is_canceled=is_canceled)
    rec = terrain.d8_receivers(zr, nod)
    valid = np.flatnonzero(~nod.ravel())
    order = valid[np.argsort(zr.ravel()[valid], kind="stable")]       # low -> high
    reach = field_mask.ravel().astype(bool).tolist()
    rec_l = rec.tolist()
    k = 0
    for c in order.tolist():
        k += 1
        if is_canceled is not None and not k & 0xFFFF and is_canceled():
            raise RuntimeError("Canceled.")
        if not reach[c]:
            r = rec_l[c]
            if r >= 0 and reach[r]:
                reach[c] = True
    reach = np.asarray(reach).reshape(z.shape)
    rmask = reach & ~field_mask & ~nod
    rlab, rn = terrain.label_regions(rmask)
    runs = []
    keep = np.zeros(rn + 1, bool)
    cells = np.bincount(rlab.ravel(), minlength=rn + 1)
    edge = np.zeros(z.shape, bool)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    edge |= _next_to(nod)
    edge_lab = set(np.unique(rlab[edge & rmask]).tolist())
    for i in range(1, rn + 1):
        a = cells[i] * cell_ac
        if a >= min_runon_ac:
            runs.append({"id": i, "cells": int(cells[i]), "area_ac": a,
                         "edge": i in edge_lab})
            keep[i] = True
    rlab = np.where(keep[rlab], rlab, 0)
    runs.sort(key=lambda s: -s["area_ac"])
    if progress:
        progress(1.0)
    return {"dep_labels": dlab, "deps": deps, "run_labels": rlab, "runs": runs,
            "filled": filled, "routing": zr, "rec": rec, "nodata": nod,
            "edge": any(r["edge"] for r in runs)}


def _next_to(mask):
    out = np.zeros_like(mask)
    r, c = mask.shape
    p = np.pad(mask, 1)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr or dc:
                out |= p[1 + dr:r + 1 + dr, 1 + dc:c + 1 + dc]
    return out & ~mask


def coarsen(z, factor):
    """Block mean (NaN-aware) by an integer factor."""
    if factor <= 1:
        return z
    r, c = (z.shape[0] // factor) * factor, (z.shape[1] // factor) * factor
    b = z[:r, :c].reshape(r // factor, factor, c // factor, factor)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(b, axis=(1, 3))


# ---------------------------------------------------------------- map sheets
def sheet_centers(xmin, ymin, xmax, ymax, w, h, overlap=0.05):
    """Centres of a grid of map sheets (each w x h map units) covering the rectangle,
    with a small overlap, row by row from the north-west."""
    sw, sh = w * (1 - overlap), h * (1 - overlap)
    nx = max(1, int(math.ceil((xmax - xmin - w * overlap) / sw - 1e-9)))
    ny = max(1, int(math.ceil((ymax - ymin - h * overlap) / sh - 1e-9)))
    cx0 = (xmin + xmax) / 2.0 - (nx - 1) * sw / 2.0
    cy0 = (ymin + ymax) / 2.0 + (ny - 1) * sh / 2.0
    return [(cx0 + i * sw, cy0 - j * sh) for j in range(ny) for i in range(nx)]


def sheet_scale_for_pages(count_at, max_scale, fit_scale, max_pages, scales=None):
    """Scale for map sheets limited to `max_pages` pages.

    count_at(scale) -> number of (non-empty) sheets at that scale. Starting at
    `max_scale`, steps through the standard scales until the sheets fit in `max_pages`;
    returns (scale, sheets) with sheets = None when one page at `fit_scale` is used."""
    from .compute import NICE_SCALES
    if max_pages and max_pages <= 1:
        return fit_scale, None
    n = count_at(max_scale)
    if not max_pages or n <= max_pages:
        return max_scale, n
    for s in (scales or NICE_SCALES):
        if s <= max_scale:
            continue
        if s >= fit_scale:
            break
        n = count_at(s)
        if n <= max_pages:
            return s, n
    return fit_scale, None
