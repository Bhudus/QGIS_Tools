"""Run-on areas of a field from a DEM (pure Python + NumPy, no QGIS).

Run-on is the land outside a field whose surface water flows into it. Water is routed
by D8 steepest descent over the Wang & Liu filled DEM, re-filled with a tiny gradient
so flats and filled depressions drain toward their spill point (as in Depressions and
Laylines and Design Reports). A cell outside the field is run-on when its flow path
enters the field.

Run-on can be split two ways:
* by inlet: each area is the land whose water enters the field at one place. Boundary
  cells (just outside the field, draining into it) that carry at least `inlet_ac` are
  concentrated inlets; touching ones form one inlet. The stretches of boundary between
  inlets take sheet flow, each stretch one area;
* connected: 8-connected groups of run-on cells.
"""
import math

import numpy as np

from . import terrain
from .sinkfill import fill_sinks_wang_liu

ROUTING_EPS_Z = 1e-4          # rise per cell on flats of the routing surface
FT2_PER_AC = 43560.0


def routing(z, cell_ft, progress=None, is_canceled=None):
    """Routing surface and D8 receivers of a DEM in ft (NaN = no data).
    Returns dict: nodata, filled, zr, rec, order (cells low to high)."""
    nod = ~np.isfinite(z)
    zz = np.where(nod, 0.0, z)
    filled = fill_sinks_wang_liu(zz, nod, cell_ft, 0.0, 1.0,
                                 progress=(lambda f: progress(0.45 * f)) if progress else None,
                                 is_canceled=is_canceled)
    eps_deg = math.degrees(math.atan(ROUTING_EPS_Z / cell_ft))
    zr = fill_sinks_wang_liu(filled, nod, cell_ft, eps_deg, 1.0,
                             progress=(lambda f: progress(0.45 + 0.45 * f)) if progress
                             else None, is_canceled=is_canceled)
    rec = terrain.d8_receivers(zr, nod)
    valid = np.flatnonzero(~nod.ravel())
    order = valid[np.argsort(zr.ravel()[valid], kind="stable")]
    if progress:
        progress(1.0)
    return {"nodata": nod, "filled": filled, "zr": zr, "rec": rec, "order": order}


def accumulation(route, is_canceled=None):
    """Flow accumulation in cells (each cell counts itself), flat array."""
    rec = route["rec"].tolist()
    acc = (~route["nodata"]).ravel().astype(float).tolist()
    k = 0
    for c in route["order"][::-1].tolist():        # high to low
        k += 1
        if is_canceled is not None and not k & 0xFFFF and is_canceled():
            raise RuntimeError("Canceled.")
        r = rec[c]
        if r >= 0:
            acc[r] += acc[c]
    return np.asarray(acc)


def _edge_cells(nod):
    edge = np.zeros(nod.shape, bool)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    r, c = nod.shape
    p = np.pad(nod, 1)
    near = np.zeros_like(nod)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr or dc:
                near |= p[1 + dr:r + 1 + dr, 1 + dc:c + 1 + dc]
    return (edge | near) & ~nod


def field_runon(route, field_mask, cell_ft, split="inlet", min_ac=0.0, acc=None,
                inlet_ac=2.0, is_canceled=None):
    """Run-on of one field.

    Returns dict:
      labels  int32 grid, area id of each run-on cell kept (0 elsewhere);
      areas   list of dicts id, cells, area_ac, kind ("inlet", "sheet" or
              "connected"), open (reaches the DEM edge or no data, so the area is a
              minimum), inlet (row, col) of the boundary cell outside the field that
              carries the most of the area's water in, inlet_ac (acres upstream of it),
              inlet_cells (cells of the area flowing straight into the field);
      total_ac  all run-on, including areas below min_ac;
      dropped_ac  run-on in areas below min_ac;
      field_ac."""
    nod = route["nodata"]
    shape = nod.shape
    rec = route["rec"]
    fm = field_mask.ravel().astype(bool) & ~nod.ravel()
    cell_ac = cell_ft * cell_ft / FT2_PER_AC
    n = fm.size
    reach = fm.tolist()
    rec_l = rec.tolist()
    # cells outside the field whose receiver is inside it: where water crosses in
    entry = np.zeros(n, bool)
    ok = (rec >= 0) & ~fm
    idx = np.flatnonzero(ok)
    entry[idx[fm[rec[idx]]]] = True
    if acc is None:
        acc = accumulation(route, is_canceled)
    inlet_lab = np.zeros(n, np.int32)
    kinds = {}
    if split == "inlet":
        # concentrated inlets: boundary cells carrying at least inlet_ac; the stretches of
        # boundary between them take sheet flow
        conc = entry & (acc * cell_ac >= inlet_ac)
        sheet = entry & ~conc
        lab_c, n_c = terrain.label_regions(conc.reshape(shape))
        lab_s, n_s = terrain.label_regions(sheet.reshape(shape))
        inlet_lab = np.where(lab_c.ravel() > 0, lab_c.ravel(),
                             np.where(lab_s.ravel() > 0, lab_s.ravel() + n_c, 0)).astype(np.int32)
        kinds = {i: ("inlet" if i <= n_c else "sheet") for i in range(1, n_c + n_s + 1)}
    inl = inlet_lab.tolist()
    k = 0
    for c in route["order"].tolist():                 # low to high
        k += 1
        if is_canceled is not None and not k & 0xFFFF and is_canceled():
            raise RuntimeError("Canceled.")
        if reach[c]:
            continue
        r = rec_l[c]
        if r >= 0 and reach[r]:
            reach[c] = True
            if not inl[c]:
                inl[c] = inl[r]
    reach = np.asarray(reach) & ~fm
    if split == "inlet":
        labels = np.where(reach, np.asarray(inl, np.int32), 0)
        n_lab = int(labels.max()) if labels.size else 0
    else:
        lab, n_lab = terrain.label_regions(reach.reshape(shape))
        labels = lab.ravel()
    cells = np.bincount(labels, minlength=n_lab + 1)
    edge = _edge_cells(nod).ravel()
    open_ids = set(np.unique(labels[edge & (labels > 0)]).tolist())
    areas, keep = [], np.zeros(n_lab + 1, bool)
    dropped = 0.0
    ent_ids = labels[entry]
    ent_idx = np.flatnonzero(entry)
    for i in range(1, n_lab + 1):
        if cells[i] == 0:
            continue
        a = cells[i] * cell_ac
        if a < min_ac:
            dropped += a
            continue
        mine = ent_idx[ent_ids == i]
        if len(mine):
            best = mine[np.argmax(acc[mine])]
            inlet = divmod(int(best), shape[1])
            in_ac = float(acc[best]) * cell_ac
        else:
            inlet, in_ac = None, 0.0
        areas.append({"id": i, "cells": int(cells[i]), "area_ac": a, "open": i in open_ids,
                      "inlet": inlet, "inlet_ac": in_ac, "inlet_cells": int(len(mine)),
                      "kind": kinds.get(i, "connected")})
        keep[i] = True
    # number the kept areas 1..n, largest first
    areas.sort(key=lambda s: -s["area_ac"])
    remap = np.zeros(n_lab + 1, np.int32)
    for new, s in enumerate(areas, start=1):
        remap[s["id"]] = new
        s["id"] = new
    labels = np.where(keep[labels], remap[labels], 0).reshape(shape)
    return {"labels": labels, "areas": areas,
            "total_ac": float(reach.sum()) * cell_ac, "dropped_ac": dropped,
            "field_ac": float(fm.sum()) * cell_ac}


def area_raster(labels, areas):
    """Grid of each run-on cell's area (acres); NaN outside run-on."""
    lut = np.full(max([s["id"] for s in areas] + [0]) + 1, np.nan)
    for s in areas:
        lut[s["id"]] = s["area_ac"]
    out = lut[labels]
    out[labels == 0] = np.nan
    return out


def coarsen(z, factor):
    """Block mean (NaN-aware) by an integer factor."""
    if factor <= 1:
        return z
    import warnings
    r, c = (z.shape[0] // factor) * factor, (z.shape[1] // factor) * factor
    b = z[:r, :c].reshape(r // factor, factor, c // factor, factor)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(b, axis=(1, 3))


def polygon_mask(rings, x0, y0, dx, dy, nrows, ncols):
    """Cells (centres) inside a polygon given as rings [(x, y), ...] (even-odd rule).
    x0, y0 = upper-left corner, dy < 0."""
    xs = x0 + (np.arange(ncols) + 0.5) * dx
    ys = y0 + (np.arange(nrows) + 0.5) * dy
    inside = np.zeros((nrows, ncols), bool)
    for ring in rings:
        r = np.asarray(ring, float)
        if len(r) < 3:
            continue
        xa, ya = r[:, 0], r[:, 1]
        xb, yb = np.roll(xa, -1), np.roll(ya, -1)
        lo, hi = min(ya.min(), yb.min()), max(ya.max(), yb.max())
        for i in range(nrows):
            y = ys[i]
            if y < lo or y > hi:
                continue
            cross = (ya > y) != (yb > y)
            if not cross.any():
                continue
            xi = xa[cross] + (y - ya[cross]) * (xb[cross] - xa[cross]) / (yb[cross] - ya[cross])
            xi.sort()
            cnt = np.searchsorted(xi, xs, side="right")
            inside[i] ^= (cnt % 2 == 1)
    return inside
