"""Drainage intensity of a tile network (pure Python + numpy, no QGIS).

For every cell of a grid over the field:

1. the nearest drain: its distance, the nearest point on it and the drain
   depth there (ground minus tile, from the network's M and Z values);
2. the local half spacing R: along the line from that nearest point out
   through the cell, the distance to the drainage divide, where another drain
   becomes nearer, or to the field edge (a no-flow boundary, which mirrors
   the strip); the local spacing is L = 2R;
3. the drainage intensity of that strip, in length per day, by

   Hooghoudt (water table between the drains, steady state):
       q = (8 Kb de m + 4 Ka m^2) / L^2
       m   height of the water table midway between the drains above the
           drains (drain depth less the midpoint water-table depth),
       de  equivalent depth (van der Molen and Wesseling, 1991) of the
           barrier depth below the drains, for spacing L and drain radius r;

   Kirkham (ponded surface, steady state; as in DRAINMOD):
       q = 4 pi K (t + b - r) / (g L)
       g = 2 ln[tan(pi (2b - r) / 4h) / tan(pi r / 4h)]
           + 2 sum_m ln[(cosh(pi m L / 2h) + cos(pi r / 2h))
                        (cosh(pi m L / 2h) - cos(pi (2b - r) / 2h))
                        / ((cosh(pi m L / 2h) - cos(pi r / 2h))
                           (cosh(pi m L / 2h) + cos(pi (2b - r) / 2h)))]
       t   ponded depth, b drain depth below the surface, h depth from the
           surface to the barrier, K the profile's conductivity.

Internal units: metres and metres per day.
"""
import math

import numpy as np


class Grid:
    """Cells of size `cell`; x0 the left edge, y0 the top edge (north up)."""

    def __init__(self, x0, y0, cell, nrows, ncols):
        self.x0, self.y0, self.cell = float(x0), float(y0), float(cell)
        self.nrows, self.ncols = int(nrows), int(ncols)
        self.shape = (self.nrows, self.ncols)

    def xs(self):
        return self.x0 + (np.arange(self.ncols) + 0.5) * self.cell

    def ys(self):
        return self.y0 - (np.arange(self.nrows) + 0.5) * self.cell

    def centres(self):
        return np.meshgrid(self.xs(), self.ys())

    def index(self, x, y):
        """Row, column of points (arrays), and whether they fall on the grid."""
        j = np.floor((np.asarray(x) - self.x0) / self.cell).astype(int)
        i = np.floor((self.y0 - np.asarray(y)) / self.cell).astype(int)
        ok = (i >= 0) & (i < self.nrows) & (j >= 0) & (j < self.ncols)
        return np.clip(i, 0, self.nrows - 1), np.clip(j, 0, self.ncols - 1), ok


def rasterize_rings(grid, rings):
    """Even-odd fill of polygon rings (outer rings and holes)."""
    mask = np.zeros(grid.shape, dtype=bool)
    segs = []
    for r in rings:
        r = np.asarray(r, float)
        if len(r) < 3:
            continue
        if np.hypot(*(r[0] - r[-1])) > 0:
            r = np.vstack([r, r[:1]])
        segs.append(np.hstack([r[:-1], r[1:]]))
    if not segs:
        return mask
    x1, y1, x2, y2 = np.vstack(segs).T
    for i, y in enumerate(grid.ys()):
        c = (y1 > y) != (y2 > y)
        if not c.any():
            continue
        xc = np.sort(x1[c] + (y - y1[c]) * (x2[c] - x1[c]) / (y2[c] - y1[c]))
        for a, b in zip(xc[0::2], xc[1::2]):
            j0 = max(int(math.ceil((a - grid.x0) / grid.cell - 0.5)), 0)
            j1 = min(int(math.floor((b - grid.x0) / grid.cell - 0.5)), grid.ncols - 1)
            if j1 >= j0:
                mask[i, j0:j1 + 1] = True
    return mask


# --------------------------------------------------------------- drains
def drain_segments(lines):
    """Segments of the drains: lines are (n, 3) arrays of x, y, drain depth.
    Returns an (m, 6) array x1, y1, x2, y2, depth1, depth2."""
    out = []
    for a in lines:
        a = np.asarray(a, float)
        if len(a) < 2:
            continue
        seg = np.hstack([a[:-1, :2], a[1:, :2], a[:-1, 2:3], a[1:, 2:3]])
        seg = seg[np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]) > 1e-9]
        if len(seg):
            out.append(seg)
    return np.vstack(out) if out else np.zeros((0, 6))


def nearest_drain(grid, segs, reach, mask=None, progress=None):
    """Distance to the nearest drain, the nearest point on it, its depth there
    and the direction of that segment, for cells within `reach` of a drain.

    Returns dict of arrays: dist (inf beyond reach), px, py, depth, tx, ty.
    """
    X, Y = grid.centres()
    shape = grid.shape
    dist = np.full(shape, np.inf)
    px = np.full(shape, np.nan)
    py = np.full(shape, np.nan)
    dep = np.full(shape, np.nan)
    tx = np.zeros(shape)
    ty = np.zeros(shape)
    c = grid.cell
    n = len(segs)
    for k, (x1, y1, x2, y2, d1, d2) in enumerate(segs):
        j0 = max(int((min(x1, x2) - reach - grid.x0) / c), 0)
        j1 = min(int((max(x1, x2) + reach - grid.x0) / c) + 1, grid.ncols)
        i0 = max(int((grid.y0 - max(y1, y2) - reach) / c), 0)
        i1 = min(int((grid.y0 - min(y1, y2) + reach) / c) + 1, grid.nrows)
        if j1 <= j0 or i1 <= i0:
            continue
        xs = X[i0:i1, j0:j1]
        ys = Y[i0:i1, j0:j1]
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        t = np.clip(((xs - x1) * dx + (ys - y1) * dy) / L2, 0.0, 1.0)
        qx, qy = x1 + t * dx, y1 + t * dy
        d = np.hypot(xs - qx, ys - qy)
        win = dist[i0:i1, j0:j1]
        better = (d < win) & (d <= reach)
        if mask is not None:
            better &= mask[i0:i1, j0:j1]
        if better.any():
            win[better] = d[better]
            px[i0:i1, j0:j1][better] = qx[better]
            py[i0:i1, j0:j1][better] = qy[better]
            dep[i0:i1, j0:j1][better] = (d1 + t * (d2 - d1))[better]
            L = math.sqrt(L2)
            tx[i0:i1, j0:j1][better] = dx / L
            ty[i0:i1, j0:j1][better] = dy / L
        if progress is not None and k % 500 == 0:
            progress(k / max(n, 1))
    return {"dist": dist, "px": px, "py": py, "depth": dep, "tx": tx, "ty": ty}


def half_spacing(grid, near, reach, mask):
    """Distance from each cell's nearest drain out to the drainage divide (or
    the field edge), along the line from the drain through the cell.

    Returns (R, edge): R in metres (reach where nothing is found within it),
    edge True where the strip ends at the field edge rather than at a divide.
    """
    D = near["dist"]
    X, Y = grid.centres()
    c = grid.cell
    R = np.full(grid.shape, np.nan)
    edge = np.zeros(grid.shape, dtype=bool)
    cells = np.isfinite(D) & mask
    ii, jj = np.nonzero(cells)
    if ii.size == 0:
        return R, edge
    d0 = D[ii, jj]
    ox, oy = near["px"][ii, jj], near["py"][ii, jj]
    # direction: from the drain out through the cell; on the drain, square to it
    ux, uy = X[ii, jj] - ox, Y[ii, jj] - oy
    on = d0 < 0.5 * c
    nx, ny = -near["ty"][ii, jj], near["tx"][ii, jj]
    res = np.full(ii.size, np.nan)
    is_edge = np.zeros(ii.size, dtype=bool)
    for sign in ((1.0,) if not on.any() else (1.0, -1.0)):
        dx = np.where(on, sign * nx, ux / np.maximum(d0, 1e-12))
        dy = np.where(on, sign * ny, uy / np.maximum(d0, 1e-12))
        r_side = np.full(ii.size, reach)
        e_side = np.zeros(ii.size, dtype=bool)
        alive = np.ones(ii.size, dtype=bool)
        if sign < 0:
            alive &= on
        step = 0.5 * c
        t = np.maximum(d0, 0.0)
        t_prev = t.copy()
        while alive.any():
            t = t_prev + step
            sx, sy = ox + dx * t, oy + dy * t
            ri, rj, ok = grid.index(sx, sy)
            inside = ok & mask[ri, rj]
            ds = np.where(inside, D[ri, rj], np.inf)
            # left the field: the strip ends at the edge
            out = alive & ~inside
            r_side[out] = t_prev[out] + 0.5 * step
            e_side[out] = True
            # another drain is nearer: the divide lies between
            cross = alive & inside & (ds < t - 0.75 * c)
            r_side[cross] = np.maximum(0.5 * (t[cross] + ds[cross]), t_prev[cross])
            alive &= ~(out | cross)
            far = alive & (t >= reach)
            r_side[far] = reach
            alive &= ~far
            t_prev = t
        if sign > 0:
            res, is_edge = r_side, e_side
        else:
            pick = on & (r_side < res)
            res[pick] = r_side[pick]
            is_edge[pick] = e_side[pick]
    R[ii, jj] = res
    edge[ii, jj] = is_edge
    return R, edge


# ---------------------------------------------------------- soil profile
def parse_ksat_profile(text):
    """Layers from a ksat_prof field ("top-bottom:k/low/high;..." with depths
    in cm and k in m/day): list of (top_m, bottom_m, k_m_day)."""
    layers = []
    if not text:
        return layers
    for part in str(text).split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        rng, vals = part.split(":", 1)
        try:
            top, bot = (float(v) / 100.0 for v in rng.split("-", 1))
            k = float(vals.split("/")[0])
        except ValueError:
            continue
        if bot > top and k >= 0:
            layers.append((top, bot, k))
    return layers


def layer_mean(layers, top, bottom):
    """Thickness-weighted conductivity of the layers between two depths (m)."""
    tot = w = 0.0
    for t, b, k in layers:
        o = min(b, bottom) - max(t, top)
        if o > 0:
            tot += k * o
            w += o
    return tot / w if w > 0 else None


def layer_mean_v(layers, top, bottom):
    """layer_mean for arrays of depths (nan where no layer overlaps)."""
    top = np.asarray(top, float)
    bottom = np.asarray(bottom, float)
    tot = np.zeros(np.broadcast(top, bottom).shape)
    w = np.zeros_like(tot)
    for t, b, k in layers:
        o = np.clip(np.minimum(b, bottom) - np.maximum(t, top), 0.0, None)
        tot += k * o
        w += o
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(w > 0, tot / np.where(w > 0, w, 1.0), np.nan)


# ------------------------------------------------------------ equations
def equivalent_depth(L, D, r):
    """Hooghoudt equivalent depth (van der Molen and Wesseling, 1991), arrays."""
    L = np.asarray(L, float)
    D = np.asarray(D, float)
    out = np.zeros(np.broadcast(L, D).shape)
    ok = (D > 0) & (L > 0)
    if not ok.any():
        return out
    Lk, Dk = np.broadcast_to(L, out.shape)[ok], np.broadcast_to(D, out.shape)[ok]
    x = 2.0 * math.pi * Dk / Lk
    F = np.empty_like(x)
    small = x < 0.5
    F[small] = math.pi ** 2 / (4.0 * x[small]) + np.log(x[small] / (2.0 * math.pi))
    big = ~small
    if big.any():
        xb = x[big]
        tot = np.zeros_like(xb)
        for n in range(1, 200, 2):
            e = np.exp(-2.0 * n * xb)
            term = 4.0 * e / (n * (1.0 - e))
            tot += term
            if term.max() < 1e-12:
                break
        F[big] = tot
    denom = np.log(Lk / (math.pi * r)) + F
    de = np.where(denom > 0, math.pi * Lk / (8.0 * np.where(denom > 0, denom, 1.0)), Dk)
    out[ok] = np.minimum(Dk, de)
    return out


def hooghoudt(L, b, wt, ka, kb, barrier, r):
    """Drainage intensity (m/day) with the midpoint water table at depth wt.
    L spacing, b drain depth, barrier depth below the surface (all m)."""
    m = np.maximum(np.asarray(b, float) - wt, 0.0)
    D = np.maximum(np.asarray(barrier, float) - b, 0.0)
    de = equivalent_depth(L, D, r)
    with np.errstate(divide="ignore", invalid="ignore"):
        q = (8.0 * kb * de * m + 4.0 * ka * m * m) / (np.asarray(L, float) ** 2)
    return np.where(np.isfinite(q), q, np.nan)


def kirkham(L, b, t, k, barrier, r):
    """Ponded drainage intensity (m/day), Kirkham's equation as in DRAINMOD.
    b drain depth, barrier depth below the surface, t ponded depth (all m)."""
    L = np.asarray(L, float)
    b = np.asarray(b, float)
    shape = np.broadcast(L, b, np.asarray(barrier, float), np.asarray(k, float)).shape
    L = np.broadcast_to(L, shape).astype(float)
    b = np.broadcast_to(b, shape).astype(float)
    k = np.broadcast_to(np.asarray(k, float), shape)
    # the barrier must lie below the drain: at least a drain diameter
    h = np.maximum(np.broadcast_to(np.asarray(barrier, float), shape), b + 2.0 * r)
    a = math.pi * (2.0 * b - r) / (4.0 * h)
    g = 2.0 * np.log(np.tan(a) / np.tan(math.pi * r / (4.0 * h)))
    cr = np.cos(math.pi * r / (2.0 * h))
    cd = np.cos(math.pi * (2.0 * b - r) / (2.0 * h))
    for m in range(1, 400):
        arg = math.pi * m * L / (2.0 * h)
        big = arg > 300
        c = np.cosh(np.minimum(arg, 300))
        term = np.log((c + cr) * (c - cd) / ((c - cr) * (c + cd)))
        term = np.where(big, 0.0, term)
        g = g + 2.0 * term
        if np.nanmax(np.abs(term)) < 1e-12:
            break
    with np.errstate(divide="ignore", invalid="ignore"):
        q = 4.0 * math.pi * k * (t + b - r) / (g * L)
    return np.where(np.isfinite(q) & (q >= 0), q, np.nan)


# ----------------------------------------------------------------- run
def drainage_intensity(grid, mask, segs, soil, method, reach, r, wt=0.0, ponded=0.0,
                       progress=None):
    """Drainage intensity over the field.

    soil: dict of grids ka, kb, barrier (m/day, m) and optionally layer
    profiles: `profiles` (a list of parse_ksat_profile results) and
    `profile_id` (an int grid indexing it, -1 where none); method "hooghoudt"
    or "kirkham". Returns dict of grids: q (m/day), spacing (m), depth (m),
    dist (m), edge (bool), beyond (bool: no divide or edge within reach).
    """
    near = nearest_drain(grid, segs, reach, mask,
                         None if progress is None else (lambda f: progress(0.6 * f)))
    if progress is not None:
        progress(0.6)
    R, edge = half_spacing(grid, near, reach, mask)
    if progress is not None:
        progress(0.85)
    L = 2.0 * R
    b = near["depth"]
    ka, kb, barrier = soil["ka"].copy(), soil["kb"].copy(), soil["barrier"]
    pid = soil.get("profile_id")
    profiles = soil.get("profiles") or []
    if pid is not None and profiles:
        # layer conductivities for each cell's own drain depth
        top = max(wt, 0.0)
        for p, lay in enumerate(profiles):
            cells = (pid == p) & np.isfinite(b) & mask
            if not lay or not cells.any():
                continue
            bc = b[cells]
            a = layer_mean_v(lay, np.full(bc.shape, top), bc)
            ka[cells] = np.where(np.isfinite(a), a, ka[cells])
            bar = barrier[cells]
            v = layer_mean_v(lay, bc, bar)
            kb[cells] = np.where(np.isfinite(v) & (bar > bc), v, kb[cells])
    if method == "kirkham":
        with np.errstate(invalid="ignore"):
            hb = np.maximum(barrier, b)
            K = np.where(hb > 0, (ka * np.minimum(b, hb) + kb * (hb - np.minimum(b, hb))) /
                         np.where(hb > 0, hb, 1.0), ka)
        q = kirkham(np.where(np.isfinite(L), L, 1.0), np.where(np.isfinite(b), b, 1.0),
                    ponded, K, barrier, r)
    else:
        q = hooghoudt(np.where(np.isfinite(L), L, 1.0), np.where(np.isfinite(b), b, 1.0),
                      wt, ka, kb, barrier, r)
    found = np.isfinite(L) & np.isfinite(b) & mask
    q = np.where(found, q, np.nan)
    beyond = mask & (~np.isfinite(near["dist"]) | (np.isfinite(R) & (R >= reach - 1e-9)))
    if progress is not None:
        progress(1.0)
    return {"q": q, "spacing": np.where(found, L, np.nan),
            "depth": np.where(found, b, np.nan), "dist": near["dist"],
            "edge": edge & mask, "beyond": beyond}


def summary(res, mask, target, cell):
    """Area-weighted figures over the field (q and target in m/day)."""
    q = res["q"]
    area = mask.sum() * cell * cell
    vals = q[mask & np.isfinite(q)]
    beyond = int((res["beyond"] & mask).sum())
    below = int((mask & np.isfinite(q) & (q < target)).sum()) + int(
        (mask & ~np.isfinite(q)).sum())
    out = {"area_m2": float(area), "cells": int(mask.sum()),
           "mean": float(vals.mean()) if vals.size else float("nan"),
           "median": float(np.median(vals)) if vals.size else float("nan"),
           "p10": float(np.percentile(vals, 10)) if vals.size else float("nan"),
           "below_pct": 100.0 * below / max(int(mask.sum()), 1),
           "beyond_pct": 100.0 * beyond / max(int(mask.sum()), 1)}
    sp = res["spacing"][mask & np.isfinite(res["spacing"]) & ~res["beyond"]]
    out["spacing_median"] = float(np.median(sp)) if sp.size else float("nan")
    return out
