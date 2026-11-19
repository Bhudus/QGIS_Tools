"""Zonal subirrigation layout: drainage subsystems contained within zones.

Pure numpy, no QGIS imports. Builds on the Illini layout engine
(`DrainageLayout`): the collectors, lateral placement, bend limiting,
Routine J burying and Routine K sizing are unchanged. What this adds:

1. Zones. The field is cut into elevation bands one interval high (on the
   smoothed terrain), each connected patch its own zone, with patches smaller
   than the smallest zone merged into the neighbour they share most border
   with; or the zones come from a label grid (e.g. rasterized zone polygons).
   Zones are numbered from the lowest (1) up.
2. Containment. Laterals keep the zone setback from every zone boundary, never
   cross one, and only join collectors in their own zone, so every lateral
   lies wholly in one zone and drains to that zone's collector.
3. Zone submains. A zone that no collector reaches gets its own submain,
   routed along its low ground from the point nearest the collectors of a
   lower zone, and a solid (unperforated) connector from there to that
   collector.
4. Control structures. A structure goes wherever a collector carries water
   out of a zone: where a main or submain crosses into the next zone, where a
   zone submain enters its connector, and at the outlet. Each zone's water
   level is held by its own structure(s), so each zone is one subsystem.
5. Boards and water supply. Each zone's board is set so the managed water
   table sits `wt_depth` below the zone's low ground. Each zone reports the
   share of its land where the water table is within `control_depth` of the
   ground, the share of its tile below the board (tile that can deliver water)
   and the water supply rate for a given daily rate.

All lengths and elevations are in metres.
"""
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .geometry import (
    cumulative_length,
    label_regions,
    moving_average,
    nearest_vertex,
    polyline_length,
    resample,
    segments_cross,
)
from .layout import ORIENT_ACROSS, DrainageLayout, LayoutError

# water-table status codes, per cell
ST_NONE, ST_OK, ST_DEEP, ST_WET, ST_UNDRAINED = 0, 1, 2, 3, 4
STATUS_LABELS = {
    ST_OK: "Water table within the control depth",
    ST_DEEP: "Water table too deep (high ground)",
    ST_WET: "Water table above the ground (too wet)",
    ST_UNDRAINED: "More than half a spacing from a tile",
}

M3_PER_DAY_TO_GPM = 264.172052 / 1440.0
M3_PER_DAY_TO_LPS = 1.0 / 86.4


@dataclass
class ZoneParams:
    interval: float = 0.3048              # m, zone elevation interval (band height)
    min_zone_area: float = 4046.86        # m^2, smaller patches join a neighbour
    zone_setback: Optional[float] = None  # m, lateral setback from zone boundaries (default spacing / 2)
    min_run: Optional[float] = None       # m, a collector stays in a zone at least this far (default spacing)
    wt_depth: float = 1.5 * 0.3048        # m, managed water table below the zone's low ground
    control_depth: float = 3.0 * 0.3048   # m, deepest water table that still counts as controlled
    p_low: float = 5.0                    # percentile of zone ground taken as its low ground
    supply_rate: float = 0.3 * 0.0254     # m/day of water supplied over the zone area
    zone_submains: bool = True            # zones no collector reaches get their own submain


# ------------------------------------------------------------------ zones

def points_in_rings(x, y, rings):
    """Even-odd point-in-polygon test of points (x, y) against a set of rings
    (outer rings and holes alike)."""
    x = np.atleast_1d(np.asarray(x, dtype=float))
    y = np.atleast_1d(np.asarray(y, dtype=float))
    inside = np.zeros(x.shape, dtype=bool)
    for r in rings:
        r = np.asarray(r, dtype=float)
        if len(r) < 3:
            continue
        x1, y1 = r[:, 0], r[:, 1]
        x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
        for k in range(len(r)):
            ya, yb = y1[k], y2[k]
            if ya == yb:
                continue
            cross = (ya > y) != (yb > y)
            if not cross.any():
                continue
            xc = x1[k] + (y - ya) * (x2[k] - x1[k]) / (yb - ya)
            inside ^= cross & (x < xc)
    return inside


def label_equal(zr):
    """4-connected patches of equal positive value: labels 1..n, 0 where zr <= 0."""
    nr, nc = zr.shape
    flat = zr.ravel().tolist()
    lab = [0] * len(flat)
    cur = 0
    for start in np.flatnonzero(zr.ravel() > 0).tolist():
        if lab[start]:
            continue
        cur += 1
        v = flat[start]
        lab[start] = cur
        stack = [start]
        while stack:
            c = stack.pop()
            i, j = divmod(c, nc)
            for n, ok in ((c - 1, j > 0), (c + 1, j < nc - 1), (c - nc, i > 0),
                          (c + nc, i < nr - 1)):
                if ok and not lab[n] and flat[n] == v:
                    lab[n] = cur
                    stack.append(n)
    return np.asarray(lab, dtype=np.int64).reshape(nr, nc)


def _pairs(lab):
    """Adjacent cell pairs (a, b) with different positive labels, both orders."""
    a1, b1 = lab[:, :-1].ravel(), lab[:, 1:].ravel()
    a2, b2 = lab[:-1, :].ravel(), lab[1:, :].ravel()
    a = np.concatenate([a1, a2])
    b = np.concatenate([b1, b2])
    m = (a > 0) & (b > 0) & (a != b)
    a, b = a[m], b[m]
    return np.concatenate([a, b]), np.concatenate([b, a])


def merge_small(lab, min_cells):
    """Merge patches smaller than min_cells into the neighbour they share most border with."""
    lab = lab.copy()
    for _ in range(200):
        n = int(lab.max())
        if n == 0:
            break
        cnt = np.bincount(lab.ravel(), minlength=n + 1)
        small = cnt < min_cells
        small[0] = False
        if not small.any() or (cnt[1:] > 0).sum() <= 1:
            break
        a, b = _pairs(lab)
        keep = small[a]
        a, b = a[keep], b[keep]
        if len(a) == 0:
            break
        code = a * (n + 1) + b
        u, c = np.unique(code, return_counts=True)
        ua, ub = u // (n + 1), u % (n + 1)
        # prefer large neighbours, then the longest shared border
        score = c + np.where(small[ub], 0, 10 ** 9)
        order = np.lexsort((-score, ua))
        first = np.r_[True, ua[order][1:] != ua[order][:-1]]
        src, dst = ua[order][first], ub[order][first]
        remap = np.arange(n + 1)
        # merge the smallest patches first; never merge into a patch that is itself moving
        moving = set()
        for s_, d_ in sorted(zip(src.tolist(), dst.tolist()), key=lambda t: cnt[t[0]]):
            if d_ in moving or s_ in moving:
                continue
            remap[s_] = d_
            moving.add(s_)
        if not moving:
            break
        lab = remap[lab]
        # relabel to 1..n
        ids = np.unique(lab[lab > 0])
        r2 = np.zeros(int(lab.max()) + 1, dtype=np.int64)
        r2[ids] = np.arange(1, len(ids) + 1)
        lab = r2[lab]
    return lab


def fill_from_neighbours(lab, mask):
    """Give unlabelled cells inside mask the label of a labelled 4-neighbour, repeatedly."""
    lab = lab.copy()
    for _ in range(max(lab.shape)):
        hole = mask & (lab == 0)
        if not hole.any():
            break
        best = np.zeros_like(lab)
        for sl_src, sl_dst in (((slice(None), slice(1, None)), (slice(None), slice(None, -1))),
                               ((slice(None), slice(None, -1)), (slice(None), slice(1, None))),
                               ((slice(1, None), slice(None)), (slice(None, -1), slice(None))),
                               ((slice(None, -1), slice(None)), (slice(1, None), slice(None)))):
            nb = np.zeros_like(lab)
            nb[sl_dst] = lab[sl_src]
            best = np.where((best == 0) & (nb > 0), nb, best)
        upd = hole & (best > 0)
        if not upd.any():
            break
        lab[upd] = best[upd]
    return lab


def elevation_bands(zs, mask, interval, min_cells):
    """Elevation-band zones on the smoothed terrain (labels 1..n, 0 outside)."""
    m = mask & np.isfinite(zs)
    if not m.any():
        return np.zeros(mask.shape, dtype=np.int64)
    base = float(np.min(zs[m]))
    zr = np.zeros(mask.shape, dtype=np.int64)
    zr[m] = np.floor((zs[m] - base) / interval).astype(np.int64) + 1
    lab = label_equal(zr)
    if min_cells > 1:
        lab = merge_small(lab, min_cells)
    return lab


def runs_of(labels):
    """[start, end] index runs of equal labels."""
    runs = []
    s = 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[s]:
            runs.append([s, i - 1, int(labels[s])])
            s = i
    return runs


def smooth_runs(labels, s, min_run):
    """Relabel runs of a positive zone shorter than min_run (arc length) with a
    neighbouring run's zone, the shortest run first, so that a collector that
    wanders along a zone boundary does not get a structure at every wiggle."""
    labels = np.asarray(labels).copy()
    if len(labels) < 2 or min_run <= 0:
        return labels
    for _ in range(len(labels)):
        runs = runs_of(labels)
        if len(runs) < 2:
            break

        def rlen(r):
            a, b = r[0], r[1]
            lo = s[a - 1] if a > 0 else s[a]
            hi = s[b + 1] if b < len(s) - 1 else s[b]
            return 0.5 * ((s[a] - lo) + (hi - s[b])) + (s[b] - s[a])

        cand = []
        for k, r in enumerate(runs):
            if r[2] <= 0:
                continue
            L = rlen(r)
            if L >= min_run:
                continue
            nbs = [runs[q] for q in (k - 1, k + 1) if 0 <= q < len(runs) and runs[q][2] > 0]
            if nbs:
                cand.append((L, k, nbs))
        if not cand:
            break
        L, k, nbs = min(cand, key=lambda t: t[0])
        tgt = max(nbs, key=rlen)[2]
        r = runs[k]
        labels[r[0]:r[1] + 1] = tgt
    return labels


# ------------------------------------------------------------------ engine

class ZonalLayout(DrainageLayout):
    def __init__(self, dem, valid, grid, rings, outlet, params, zparams=None,
                 zones=None, progress=None, cancel=None, mains=None):
        super().__init__(dem, valid, grid, rings, outlet, params,
                         progress=progress, cancel=cancel, mains=mains)
        self.zp = zparams or ZoneParams()
        self.zones_in = None if zones is None else np.asarray(zones, dtype=np.int64)
        self.struct_cuts = {}

    # ------------------------------------------------------------ pipeline
    def run(self):
        self._progress(2, "Preparing terrain")
        self._prepare()
        self._progress(8, "Delineating zones")
        self._make_zones()
        self._progress(15, "Routing collectors")
        self._build_collectors()
        self._all_vertex_zones()
        if self.zp.zone_submains:
            self._progress(22, "Routing zone submains")
            self._zone_submains()
        self._progress(30, "Building lateral direction field")
        self._collector_rasters()
        self._direction_field()
        self._progress(40, "Placing laterals")
        self._place_laterals()
        if self.zp.zone_submains:
            forced, before = self._poorly_served()
            n0 = len(self.cols)
            nsub0 = getattr(self, "n_zone_submains", 0)
            if forced and self._zone_submains(forced):
                self._progress(60, "Placing laterals again, with the new zone submains")
                first = (self.lats, self.hash, self.tried, self.region, self.dcoll_r,
                         self.near, self.Dx, self.Dy, self.cv_x, self.cv_y, self.cv_c,
                         self.cv_v, self.cv_tx, self.cv_ty, self.cv_zone, self.cseg,
                         getattr(self, "n_slivers", 0), nsub0)
                self._collector_rasters()
                self._direction_field()
                self._place_laterals()
                _, after = self._poorly_served()
                if after > 0.7 * before:
                    # the new submains did not help enough: keep the first layout
                    self.cols = self.cols[:n0]
                    (self.lats, self.hash, self.tried, self.region, self.dcoll_r,
                     self.near, self.Dx, self.Dy, self.cv_x, self.cv_y, self.cv_c,
                     self.cv_v, self.cv_tx, self.cv_ty, self.cv_zone, self.cseg,
                     self.n_slivers, self.n_zone_submains) = first
        self._limit_bends()
        self._all_vertex_zones()
        self._check_laterals()
        self._find_structures()
        self._progress(85, "Designing inverts")
        self._design()
        self._progress(92, "Evaluating zones")
        res = self._zonal_results()
        if self.p.tile_raster:
            res["tile_raster"] = self.tile_raster()
        return res

    # ------------------------------------------------------------ zones
    def _make_zones(self):
        g, zp = self.g, self.zp
        S = self.S
        self.zsetback = zp.zone_setback if zp.zone_setback is not None else 0.5 * S
        self.min_run = zp.min_run if zp.min_run is not None else S
        field = self.field
        min_cells = max(1, int(round(zp.min_zone_area / g.cell ** 2)))
        if self.zones_in is not None:
            lab = np.where(field, self.zones_in, 0)
            lab[lab < 0] = 0
            nf = int(field.sum())
            gap = int((field & (lab == 0)).sum())
            if lab.max() == 0:
                raise LayoutError("The zone polygons do not overlap the field.")
            # each connected piece of a zone polygon is its own zone
            lab = label_equal(lab)
            if gap:
                if gap > 0.01 * nf:
                    self._warn("{:.0f}% of the field lies outside the zone polygons; it "
                               "joins the neighbouring zones.".format(100.0 * gap / nf))
                lab = fill_from_neighbours(lab, field)
            n0 = int(lab.max())
            lab = merge_small(lab, min_cells)
            if int(lab.max()) < n0:
                self._warn("{} zone piece(s) smaller than the smallest zone joined a "
                           "neighbouring zone.".format(n0 - int(lab.max())))
        else:
            if zp.interval <= 0:
                raise LayoutError("The zone elevation interval must be positive.")
            lab = elevation_bands(self.zs, field, zp.interval, min_cells)
            lab = fill_from_neighbours(lab, field)
        # number the zones from the lowest up (median smoothed ground)
        ids = np.unique(lab[lab > 0])
        if len(ids) == 0:
            raise LayoutError("No zones could be formed in the field.")
        med = np.array([float(np.median(self.zfill[lab == i])) for i in ids])
        order = ids[np.argsort(med, kind="stable")]
        remap = np.zeros(int(lab.max()) + 1, dtype=np.int64)
        remap[order] = np.arange(1, len(order) + 1)
        self.zl = remap[lab]
        self.nz = len(order)
        self.zone_med = np.zeros(self.nz + 1)
        self.zone_med[1:] = np.sort(med)
        # distance of every field cell to the nearest zone boundary
        X, Y = self.X, self.Y
        mids = []
        zl = self.zl
        for (a, b, xa, ya, xb, yb) in (
                (zl[:, :-1], zl[:, 1:], X[:, :-1], Y[:, :-1], X[:, 1:], Y[:, 1:]),
                (zl[:-1, :], zl[1:, :], X[:-1, :], Y[:-1, :], X[1:, :], Y[1:, :])):
            m = (a > 0) & (b > 0) & (a != b)
            if m.any():
                mids.append(np.column_stack([0.5 * (xa[m] + xb[m]), 0.5 * (ya[m] + yb[m])]))
        self.zdist = np.full(g.shape, np.inf)
        if mids:
            M = np.vstack(mids)
            fi = np.nonzero(field)
            _, d = nearest_vertex(X[fi], Y[fi], M[:, 0], M[:, 1])
            self.zdist[fi] = d
        if self.nz > 60:
            self._warn("The field has {} zones; consider a larger zone interval or a "
                       "larger smallest zone.".format(self.nz))

    def zone_at(self, x, y):
        return int(self.g.lookup(self.zl, x, y, 0))

    def _vertex_zones(self, pts):
        pts = np.asarray(pts, dtype=float)
        lab = np.array([self.zone_at(x, y) for x, y in pts], dtype=np.int64)
        return smooth_runs(lab, cumulative_length(pts), self.min_run)

    def _all_vertex_zones(self):
        for c in self.cols:
            if c.get("solid"):
                c["vzone"] = np.full(len(c["pts"]), -1, dtype=np.int64)
            else:
                c["vzone"] = self._vertex_zones(c["pts"])

    # ------------------------------------------------------------ zone submains
    def _flood(self, mask, start):
        """Priority flood over mask from start (i, j): order, parent, accumulation."""
        g = self.g
        nr, nc = g.shape
        P = nc + 2
        zp = np.full((nr + 2, nc + 2), np.inf)
        zp[1:-1, 1:-1] = np.where(np.isfinite(self.zs), self.zs, np.inf)
        mp = np.zeros((nr + 2, nc + 2), dtype=bool)
        mp[1:-1, 1:-1] = mask
        zl_ = zp.ravel().tolist()
        ml = mp.ravel().tolist()
        visited = [False] * len(zl_)
        parent = [-1] * len(zl_)
        offs = (-P - 1, -P, -P + 1, -1, 1, P - 1, P, P + 1)
        s0 = (start[0] + 1) * P + (start[1] + 1)
        import heapq
        heap = [(zl_[s0], 0, s0)]
        visited[s0] = True
        order = []
        cnt = 1
        while heap:
            pr, _, c = heapq.heappop(heap)
            order.append(c)
            for o in offs:
                n = c + o
                if ml[n] and not visited[n]:
                    visited[n] = True
                    parent[n] = c
                    zn = zl_[n]
                    heapq.heappush(heap, (zn if zn > pr else pr, cnt, n))
                    cnt += 1
        acc = {}
        for c in order:
            acc[c] = 1
        for c in reversed(order):
            pa = parent[c]
            if pa >= 0:
                acc[pa] += acc[c]
        return order, parent, acc, P, s0

    def _stem(self, mask, start):
        """Main stem of the drainage tree of mask, from start upstream: points
        upstream -> downstream, ending at the centre of the start cell."""
        order, parent, acc, P, s0 = self._flood(mask, start)
        children = {}
        for c in order[1:]:
            children.setdefault(parent[c], []).append(c)
        stop = max(3, 0.02 * len(order))
        path = [s0]
        c = s0
        while c in children:
            nxt = max(children[c], key=lambda q: acc[q])
            if acc[nxt] < stop:
                break
            c = nxt
            path.append(c)
        pts = []
        for n in path[::-1]:
            i, j = divmod(n, P)
            pts.append(self.g.center_of(i - 1, j - 1))
        return np.array(pts, dtype=float)

    def _contour_alignment(self, pts):
        """Mean |cos| between a line and the contour direction along it
        (1 = runs along the contours, 0 = straight down the slope)."""
        pts = np.asarray(pts, float)
        if len(pts) < 3 or polyline_length(pts) < self.S:
            return 1.0
        q = resample(pts, max(self.g.cell, 0.25 * self.S))
        d = np.diff(q, axis=0)
        n = np.hypot(d[:, 0], d[:, 1])
        ok = n > 0
        vals = []
        for (x, y), (dx, dy), L in zip(0.5 * (q[1:] + q[:-1]), d, n):
            if L <= 0:
                continue
            u = self._vec(self.fx, self.fy, x, y)
            if u is None:
                continue
            cx, cy = -u[1], u[0]
            vals.append(abs(dx * cx + dy * cy) / L)
        return float(np.mean(vals)) if vals and ok.any() else 1.0

    def _climb(self, mask, p0):
        """Trace up the fall line from p0 while inside mask: points upstream ->
        downstream, ending at p0. Where the ground is flat or the trace would
        leave the mask, it turns by up to 60 degrees to stay inside."""
        h = self.h
        x, y = p0
        pts = [p0]
        prev = None
        turns = [0.0]
        for a in range(15, 61, 15):
            turns += [math.radians(a), -math.radians(a)]
        z_best = self.g.bilinear(self.zs, x, y)
        k_best = 0
        window = max(3, int(math.ceil(self.S / h)))
        for _ in range(int(20000 / h)):
            u = self._vec(self.fx, self.fy, x, y)
            if u is None:
                u = prev                         # flat spot: carry straight on
            else:
                u = (-u[0], -u[1])               # uphill
            if u is None:
                break
            step = None
            for a in turns:
                ca, sa = math.cos(a), math.sin(a)
                dx, dy = ca * u[0] - sa * u[1], sa * u[0] + ca * u[1]
                if prev is not None and dx * prev[0] + dy * prev[1] < 0.5:
                    continue
                if self.g.lookup(mask, x + h * dx, y + h * dy, False):
                    step = (dx, dy)
                    break
            if step is None:
                break
            x, y = x + h * step[0], y + h * step[1]
            pts.append((x, y))
            prev = step
            zc = self.g.bilinear(self.zs, x, y)
            if zc == zc and (z_best != z_best or zc > z_best + 1e-6):
                z_best, k_best = zc, len(pts) - 1
            elif len(pts) - 1 - k_best >= window:
                break                            # sliding along an edge without climbing
        pts = pts[:k_best + 1] if k_best > 0 else pts
        return np.array(pts[::-1], dtype=float)

    def _collector_segments(self):
        cs = []
        for c in self.cols:
            pts = np.asarray(c["pts"], float)
            if len(pts) > 1:
                cs.append(np.hstack([pts[:-1], pts[1:]]))
        return np.vstack(cs) if cs else np.zeros((0, 4))

    def _zone_submains(self, forced=None):
        """Give every zone no collector reaches its own submain. With `forced`
        ({zone: mask}), route a submain in each given zone through the masked
        part (the part its existing collectors serve poorly) instead."""
        g, S = self.g, self.S
        # a zone is served when a collector runs through the part of it where
        # laterals can go (inside the field setback and the zone setback)
        open_ = self.inner & (self.zdist >= self.zsetback)
        run = {}
        for c in self.cols:
            if c.get("solid"):
                continue
            pts = np.asarray(c["pts"], float)
            if len(pts) < 2:
                continue
            seg = np.hypot(*np.diff(pts, axis=0).T)
            mid = 0.5 * (pts[1:] + pts[:-1])
            for (x, y), L, zv in zip(mid, seg, c["vzone"][1:]):
                if zv > 0 and self.g.lookup(open_, x, y, False):
                    run[int(zv)] = run.get(int(zv), 0.0) + float(L)
        served = {z for z, L in run.items() if L >= 2.0 * g.cell}
        if forced is not None:
            served = {z for z in range(1, self.nz + 1) if z not in forced}
        min_cells = max(10, int(2.0 * S * S / g.cell ** 2))
        win = max(3, int(round(S / g.cell)) | 1)
        added = 0
        for z in range(1, self.nz + 1):              # lowest zone first
            if z in served:
                continue
            M = self.inner & (self.zl == z)
            if forced is not None:
                M = M & forced[z]
            # keep the submain out of the zone setback, where no lateral could join it
            M_open = M & (self.zdist >= self.zsetback)
            if int(M_open.sum()) >= min_cells:
                M = M_open
            if int(M.sum()) < min_cells:
                continue
            # host vertices: collectors of zones no higher than this one
            hv = []
            for k, c in enumerate(self.cols):
                if c.get("solid"):
                    continue
                vz = c["vzone"]
                for v in range(len(vz)):
                    if vz[v] > 0 and self.zone_med[vz[v]] <= self.zone_med[z] + 1e-9:
                        hv.append((k, v))
            if not hv:
                hv = [(k, v) for k, c in enumerate(self.cols) if not c.get("solid")
                      for v in range(len(c["pts"]))]
            hx = np.array([self.cols[k]["pts"][v][0] for k, v in hv])
            hy = np.array([self.cols[k]["pts"][v][1] for k, v in hv])
            # the exit lies on the zone's low ground (lowest quarter of its range)
            zm = self.zs[M]
            lo, hi = float(np.nanmin(zm)), float(np.nanmax(zm))
            low = M & (self.zs <= lo + 0.25 * max(hi - lo, 1e-6))
            ii, jj = np.nonzero(low)
            idx, d = nearest_vertex(self.X[ii, jj], self.Y[ii, jj], hx, hy)
            cseg = self._collector_segments()
            choice = None
            for q in np.argsort(d)[:40]:
                ex = (float(self.X[ii[q], jj[q]]), float(self.Y[ii[q], jj[q]]))
                k, v = hv[int(idx[q])]
                hp = self.cols[k]["pts"][v]
                if d[q] < 0.5 * S or not segments_cross(ex[0], ex[1], hp[0], hp[1], cseg):
                    choice = ((int(ii[q]), int(jj[q])), ex, k, v, float(d[q]))
                    break
            if choice is None:
                self._warn("Zone {} has no collector and no clear route to one; it gets no "
                           "subsystem.".format(z))
                continue
            cell, ex, hk, hvx, dist = choice
            # the submain follows the zone's low ground (drainage-tree stem); with
            # laterals along the contours it must cross them, so where the stem runs
            # along the contours (a band on even ground) it climbs the fall line instead
            stem = self._stem(M, cell)
            if ((self.p.orientation == ORIENT_ACROSS or self.fixed_b is not None)
                    and self._contour_alignment(stem) > 0.6):
                stem = self._climb(M, ex)
            if len(stem) >= 3:
                stem = moving_average(stem, win)
            if len(stem) < 2:
                stem = np.array([ex, ex], dtype=float)
            stem[-1] = ex
            hp = np.array(self.cols[hk]["pts"][hvx], dtype=float)
            if dist < 0.5 * S:
                # next to a lower collector: the submain joins it directly
                pts = resample(np.vstack([stem, hp]), self.hc) if polyline_length(stem) > 0 \
                    else np.array([ex, hp])
                pts[-1] = hp
                self.cols.append({"pts": pts, "kind": "submain", "to": (hk, hvx),
                                  "zone": z, "zone_sub": True})
                self.cols[-1]["vzone"] = self._vertex_zones(pts)
            else:
                sub = resample(stem, self.hc) if polyline_length(stem) > 0 \
                    else np.array([ex, ex + 1e-3])
                sub[-1] = ex
                conn = resample(np.array([ex, hp]), self.hc)
                conn[0], conn[-1] = ex, hp
                ci = len(self.cols) + 1
                self.cols.append({"pts": sub, "kind": "submain", "to": (ci, 0), "zone": z,
                                  "zone_sub": True})
                self.cols[-1]["vzone"] = self._vertex_zones(sub)
                self.cols.append({"pts": conn, "kind": "connector", "to": (hk, hvx),
                                  "solid": True, "zone_from": z})
                self.cols[-1]["vzone"] = np.full(len(conn), -1, dtype=np.int64)
            served.add(z)
            added += 1
        self.n_zone_submains = getattr(self, "n_zone_submains", 0) + added
        return added

    def _poorly_served(self):
        """Zones where many laterals reach a collector only through a long
        connector, or not at all: {zone: mask of the land more than two
        spacings from the zone's own collectors}."""
        S = self.S
        bad, tot = {}, {}
        for L in self.lats:
            pts = np.asarray(L["pts"], float)
            zs = [self.zone_at(x, y) for x, y in pts[:-1]]
            zs = [z for z in zs if z > 0]
            if not zs:
                continue
            z = int(np.bincount(zs).argmax())
            ln = polyline_length(pts)
            tot[z] = tot.get(z, 0.0) + ln
            if L["conn"] is None or any(f.startswith("connector") for f in L["flags"]):
                bad[z] = bad.get(z, 0.0) + ln
        out = {}
        total_bad = float(sum(bad.values()))
        for z, b in bad.items():
            if b >= 0.2 * tot[z] and b >= 6.0 * S:
                far = np.nan_to_num(self.dcoll_r, nan=1e9) > 2.0 * S
                M = far & (self.zl == z) & self.inner
                if M.sum() * self.g.cell ** 2 >= 4.0 * S * S:
                    out[z] = far
        return out, total_bad

    # ------------------------------------------------------------ rasters
    def _collector_rasters(self):
        super()._collector_rasters()
        g = self.g
        dall = self.dcoll_r.copy()
        self.cv_zone = np.concatenate([np.asarray(c["vzone"]) for c in self.cols])
        fi_mask = self.field
        dz = np.full(g.shape, np.nan)
        nz = self.near.copy()
        for z in range(1, self.nz + 1):
            cells = fi_mask & (self.zl == z)
            if not cells.any():
                continue
            vsel = np.flatnonzero(self.cv_zone == z)
            if len(vsel) == 0:
                continue
            idx, d = nearest_vertex(self.X[cells], self.Y[cells],
                                    self.cv_x[vsel], self.cv_y[vsel])
            dz[cells] = d
            nz[cells] = vsel[idx]
        other = fi_mask & (self.zl == 0)
        dz[other] = dall[other]
        self.dcoll_r = dz
        self.near = nz
        # laterals keep the zone setback from zone boundaries and stay in their zone
        free = (self.inner & (np.nan_to_num(dall, nan=0) >= 0.8 * g.cell)
                & (self.zdist >= self.zsetback))
        region = np.full(g.shape, -1, dtype=np.int64)
        off = 0
        for z in range(0, self.nz + 1):
            r = label_regions(free & (self.zl == z))
            m = r >= 0
            if m.any():
                region[m] = r[m] + off
                off = int(region.max()) + 1
        self.region = region

    def snap_collector(self, x, y):
        """Nearest collector vertex in the same zone (any collector if none)."""
        z = self.zone_at(x, y)
        sel = None
        if z > 0:
            sel = np.flatnonzero(self.cv_zone == z)
            if len(sel) == 0:
                sel = None
        if sel is None:
            sel = np.flatnonzero(self.cv_zone != -1)
            if len(sel) == 0:
                sel = np.arange(len(self.cv_x))
        d = np.hypot(self.cv_x[sel] - x, self.cv_y[sel] - y)
        k = int(sel[int(np.argmin(d))])
        return float(d.min()), (float(self.cv_x[k]), float(self.cv_y[k])), \
            int(self.cv_c[k]), int(self.cv_v[k])

    # ------------------------------------------------------------ laterals
    def _seed_status(self, cid, t, side, s, pts, tg):
        c = self.cols[cid]
        if c.get("solid"):
            return "bad", None             # no laterals off a solid connector
        st, info = super()._seed_status(cid, t, side, s, pts, tg)
        if st == "ok":
            v = min(int(np.searchsorted(s, t)), len(s) - 1)
            (sx, sy), _, _ = info
            if self.zone_at(sx, sy) != int(c["vzone"][v]):
                return "bad", None         # the seed and the collector there are in different zones
        return st, info

    def _add_lateral(self, pts, reg, origin, conn, flags, bearing=None):
        # a short infill lateral that needs a long connector is a sliver left
        # beside a zone boundary: leave it out rather than add a stub
        if (origin == "infill" and any(f.startswith("connector") for f in flags)
                and polyline_length(pts) < 2.0 * self.S):
            self.tried.add_polyline([pts[0], pts[0]], -1, reg)
            self.n_slivers = getattr(self, "n_slivers", 0) + 1
            return None
        return super()._add_lateral(pts, reg, origin, conn, flags, bearing)

    def _check_laterals(self):
        """Zone of every lateral; flag laterals that join another zone's collector."""
        n_other = 0
        for L in self.lats:
            pts = np.asarray(L["pts"], float)
            body = pts[:-1] if len(pts) > 2 else pts
            zs = [self.zone_at(x, y) for x, y in body]
            zs = [z for z in zs if z > 0]
            L["zone"] = int(np.bincount(zs).argmax()) if zs else 0
        # resolve the zone each lateral discharges into (through tees)
        for lid, L in enumerate(self.lats):
            conn = L["conn"]
            jz = None
            seen = 0
            while conn is not None and seen < 1000:
                if conn[0] == "collector":
                    c = self.cols[conn[1]]
                    jz = int(c["vzone"][conn[2]]) if not c.get("solid") else -1
                    break
                conn = self.lats[conn[1]]["conn"]
                seen += 1
            L["joins_zone"] = jz
            if L["zone"] > 0 and jz is not None and jz != L["zone"]:
                n_other += 1
                L["flags"].append("drains to a collector in zone {}".format(jz)
                                  if jz > 0 else "drains to a connector")
        self.n_cross_zone = n_other
        if n_other:
            self._warn("{} lateral(s) drain to a collector in another zone; their zone is "
                       "not held by its own structure there.".format(n_other))

    # ------------------------------------------------------------ structures
    def _find_structures(self):
        """Control structures wherever a collector carries water out of a zone."""
        structs = []
        cuts = {}

        def add(k, v, zu, zd, why):
            c = self.cols[k]
            v = int(v)
            structs.append({"col": k, "v": v, "zone": int(zu), "to_zone": int(zd),
                            "xy": tuple(np.asarray(c["pts"][v], float)), "why": why})
            n = len(c["pts"])
            if 0 < v < n - 1:
                cuts.setdefault(k, set()).add(v)

        for k, c in enumerate(self.cols):
            n = len(c["pts"])
            if c.get("solid"):
                to = c["to"]
                hz = int(self.cols[to[0]]["vzone"][to[1]]) if to is not None else 0
                add(k, 0, c["zone_from"], max(hz, 0), "zone submain outlet")
                continue
            vz = c["vzone"]
            for i in range(1, n):
                if vz[i] != vz[i - 1] and vz[i - 1] > 0:
                    add(k, i - 1, vz[i - 1], max(int(vz[i]), 0), "zone boundary")
            if vz[n - 1] > 0:
                to = c["to"]
                if to is None:
                    add(k, n - 1, vz[n - 1], 0, "outlet")
                else:
                    host = self.cols[to[0]]
                    if host.get("solid"):
                        continue          # the connector's own structure holds this zone
                    hz = int(host["vzone"][to[1]])
                    if hz != vz[n - 1]:
                        add(k, n - 1, vz[n - 1], max(hz, 0), "junction")
        # every structure inside the field boundary: one outside (the outlet
        # beyond the field edge, or a zone crossing within half a cell of it)
        # slides up its pipe to the first vertex inside the field in its zone
        kept = []
        moved = dropped = 0
        for st in structs:
            c = self.cols[st["col"]]
            pts = np.asarray(c["pts"], float)
            if points_in_rings(pts[st["v"], 0], pts[st["v"], 1], self.rings)[0]:
                kept.append(st)
                continue
            if c.get("solid"):
                order = range(st["v"] + 1, len(pts))       # down the connector
                same = lambda v: True                       # noqa: E731
            else:
                order = range(st["v"] - 1, -1, -1)         # up the pipe
                same = lambda v, c=c, z=st["zone"]: int(c["vzone"][v]) == z  # noqa: E731
            cand = [v for v in order if same(v)]
            ins = points_in_rings(pts[cand, 0], pts[cand, 1], self.rings) if cand else []
            hit = [v for v, ok in zip(cand, ins) if ok]
            if not hit:
                dropped += 1
                continue
            v = hit[0]
            if c.get("solid") and v == len(pts) - 1:
                dropped += 1
                continue
            st = dict(st, v=v, xy=tuple(pts[v]))
            kept.append(st)
            moved += 1
        if dropped:
            self._warn("{} control structure(s) had no place on their pipe inside the field "
                       "and were left out.".format(dropped))
        self.n_structs_moved = moved
        structs = kept
        cuts = {}
        for st in structs:
            n = len(self.cols[st["col"]]["pts"])
            if 0 < st["v"] < n - 1:
                cuts.setdefault(st["col"], set()).add(st["v"])
        # one structure per spot (a structure at a junction serves both pipes)
        uniq = []
        for s in structs:
            dup = False
            for u in uniq:
                if u["zone"] == s["zone"] and math.hypot(u["xy"][0] - s["xy"][0],
                                                         u["xy"][1] - s["xy"][1]) < 0.5 * self.g.cell:
                    dup = True
                    break
            if not dup:
                uniq.append(s)
        self.structs = uniq
        self.struct_cuts = cuts

    def _junction_sets(self):
        col_fixed, lat_fixed = super()._junction_sets()
        for k, vs in self.struct_cuts.items():
            col_fixed[k] |= set(vs)
        return col_fixed, lat_fixed

    # ------------------------------------------------------------ burying
    def _zone_boards(self):
        """Board (stop-log) elevation of every zone: the managed water table,
        wt_depth below the zone's low ground (its p_low percentile, or the
        ground at a structure of the zone if lower)."""
        zp = self.zp
        ground = np.where(self.valid, self.dem, np.nan)
        by_zone = {}
        for st in self.structs:
            by_zone.setdefault(st["zone"], []).append(st)
        boards = {}
        for z in range(1, self.nz + 1):
            gz = ground[self.field & (self.zl == z)]
            gz = gz[np.isfinite(gz)]
            if gz.size == 0:
                continue
            plo = float(np.percentile(gz, zp.p_low))
            s_ground = [self._ground_at(st) for st in by_zone.get(z, [])]
            low = min([plo] + [v for v in s_ground if v == v])
            boards[z] = low - zp.wt_depth
        self.zone_board = boards
        return boards

    def _design(self):
        self._zone_boards()                 # the zone-level burying aims at them
        super()._design()
        if self.p.bury_method != "zone_level":
            self._zone_boards()             # with the ground sampled along the pipes

    def _bury_targets(self, P, pp):
        """Level within zones: in each zone the tile aims for one level line, at
        the middle of the target depth band below the zone's low ground (so a
        fixed depth below the zone's board), and is kept at or below the board
        where the limits allow, so all of it can take water in from the
        structure. Connectors and land outside the zones keep the usual aim."""
        mid, ceil = super()._bury_targets(P, pp)
        if self.p.bury_method != "zone_level":
            return mid, ceil
        o = pp["obj"]
        n = len(pp["z"])
        if o.get("solid"):
            return mid, ceil
        if "vzone" in o and len(o["vzone"]) == n:
            vz = np.asarray(o["vzone"], int)
        else:
            vz = np.full(n, int(o.get("zone", 0) or 0))
        p, zp = self.p, self.zp
        mid_d = 0.5 * (p.target_upper + p.target_lower)
        mid = np.array(mid, float)
        ceil = np.full(n, np.nan)
        for z in np.unique(vz):
            b = self.zone_board.get(int(z)) if z > 0 else None
            if b is None:
                continue
            m = vz == z
            mid[m] = b + zp.wt_depth - mid_d          # level, below the zone's low ground
            ceil[m] = b
        return mid, (ceil if np.isfinite(ceil).any() else None)

    # ------------------------------------------------------------ evaluation
    def _evaluate_zones(self):
        zp, g = self.zp, self.g
        ca = g.cell ** 2
        ground = np.where(self.valid, self.dem, np.nan)
        cov = self.covered if hasattr(self, "covered") else np.ones(g.shape, bool)
        status = np.zeros(g.shape, dtype=np.int8)
        zones = []
        by_zone = {}
        for s in self.structs:
            by_zone.setdefault(s["zone"], []).append(s)
        lat_by_zone = {}
        for L in self.lats:
            lat_by_zone.setdefault(L.get("zone", 0), []).append(L)
        col_len_zone = {}
        for c in self.cols:
            if c.get("solid") or "s" not in c:
                continue
            s = np.asarray(c["s"])
            vz = c["vzone"]
            seg = np.diff(s)
            for z, Ls in zip(vz[1:], seg):
                if z > 0:
                    col_len_zone[int(z)] = col_len_zone.get(int(z), 0.0) + float(Ls)
        for z in range(1, self.nz + 1):
            m = self.field & (self.zl == z)
            gz = ground[m]
            gz = gz[np.isfinite(gz)]
            if gz.size == 0:
                continue
            plo = float(np.percentile(gz, zp.p_low))
            ss = by_zone.get(z, [])
            board = self.zone_board.get(z) if getattr(self, "zone_board", None) else None
            if board is None:
                board = self._zone_boards().get(z)
            wt_below = ground - board
            st = np.full(g.shape, ST_OK, dtype=np.int8)
            st[wt_below > zp.control_depth] = ST_DEEP
            st[wt_below < 0] = ST_WET
            st[~cov] = ST_UNDRAINED
            st[~np.isfinite(ground)] = ST_NONE
            status[m] = st[m]
            n = max(int(m.sum()), 1)
            cnt = {c_: int((st[m] == c_).sum()) for c_ in (ST_OK, ST_DEEP, ST_WET, ST_UNDRAINED)}
            # tile below the board can take water in from the structure
            lat_len = sub_len = 0.0
            for L in lat_by_zone.get(z, []):
                inv, s = np.asarray(L["inv"]), np.asarray(L["s"])
                if len(s) < 2:
                    continue
                seg = np.diff(s)
                mid = 0.5 * (inv[1:] + inv[:-1])
                lat_len += float(seg.sum())
                sub_len += float(seg[mid <= board].sum())
            area = n * ca
            supply = area * zp.supply_rate                     # m3/day
            flags = []
            if not ss:
                flags.append("no control structure")
            if lat_len == 0:
                flags.append("no laterals")
            elif sub_len < 0.5 * lat_len:
                flags.append("most tile above the board")
            for s in ss:
                if s["to_zone"] > 0 and self.zone_med[s["to_zone"]] > self.zone_med[z] + \
                        0.5 * zp.interval:
                    flags.append("drains into higher zone {}".format(s["to_zone"]))
            zones.append({
                "zone": z, "cells": int(m.sum()), "area_m2": area,
                "zmin": float(gz.min()), "p_low": plo, "median": float(np.median(gz)),
                "p_high": float(np.percentile(gz, 100.0 - zp.p_low)), "zmax": float(gz.max()),
                "range": float(np.percentile(gz, 100.0 - zp.p_low) - plo),
                "board": board, "structures": len(ss),
                "lateral_m": lat_len, "collector_m": col_len_zone.get(z, 0.0),
                "laterals": len(lat_by_zone.get(z, [])),
                "tile_below_board_pct": 100.0 * sub_len / lat_len if lat_len else 0.0,
                "pct_ok": 100.0 * cnt[ST_OK] / n, "pct_deep": 100.0 * cnt[ST_DEEP] / n,
                "pct_wet": 100.0 * cnt[ST_WET] / n, "pct_undrained": 100.0 * cnt[ST_UNDRAINED] / n,
                "supply_m3_day": supply, "supply_gpm": supply * M3_PER_DAY_TO_GPM,
                "supply_lps": supply * M3_PER_DAY_TO_LPS,
                "flag": "; ".join(dict.fromkeys(flags)),
            })
        boards = {zr["zone"]: zr["board"] for zr in zones}
        structures = []
        for i, s in enumerate(self.structs, start=1):
            c = self.cols[s["col"]]
            inv = float(np.asarray(c["inv"])[s["v"]])
            gr = float(np.asarray(c["z"])[s["v"]])
            b = boards.get(s["zone"], float("nan"))
            flags = []
            if b == b and b <= inv:
                flags.append("board below the tile invert: the zone cannot be raised")
            if b == b and b > gr:
                flags.append("board above the ground at the structure")
            if s["to_zone"] > 0 and self.zone_med[s["to_zone"]] > self.zone_med[s["zone"]] + \
                    0.5 * zp.interval:
                flags.append("drains into a higher zone")
            structures.append({
                "id": i, "zone": s["zone"], "to_zone": s["to_zone"], "xy": s["xy"],
                "kind": s["why"], "col": s["col"], "ground": gr, "invert": inv,
                "board": b, "board_height": b - inv, "board_depth": gr - b,
                "flag": "; ".join(flags)})
        self.zone_rows = zones
        self.structure_rows = structures
        self.status = status
        return zones, structures

    def _ground_at(self, s):
        c = self.cols[s["col"]]
        if "z" in c:
            return float(np.asarray(c["z"])[s["v"]])
        v = self.g.bilinear(self.zfill, *s["xy"])
        return v

    # ------------------------------------------------------------ results
    def _zonal_results(self):
        res = self._results()                     # also computes coverage
        zones, structures = self._evaluate_zones()
        # pipe names: zone submains and connectors
        names = {}
        mains = subs = 0
        for k, c in enumerate(self.cols):
            if c["kind"] == "main":
                mains += 1
                names[k] = "main" if mains == 1 else "main {}".format(mains)
            elif c["kind"] == "submain":
                subs += 1
                names[k] = "submain {}".format(subs)
            else:
                subs += 1
                names[k] = "submain {}".format(subs)
        new = {}
        for k, c in enumerate(self.cols):
            if c.get("zone_sub"):
                new[names[k]] = "zone {} submain".format(c["zone"])
            elif c.get("solid"):
                new[names[k]] = "zone {} connector".format(c["zone_from"])
        for C in res["collectors"]:
            a = C["attrs"]
            a["name"] = new.get(a["name"], a["name"])
            a["flows_to"] = new.get(a["flows_to"], a["flows_to"])
        for L in res["laterals"]:
            a = L["attrs"]
            a["flows_to"] = new.get(a["flows_to"], a["flows_to"])
        for lid, L in enumerate(self.lats):
            res["laterals"][lid]["attrs"]["zone"] = L.get("zone", 0)
        ncol = len(self.cols)
        for it in res["network"]:
            a = it["attrs"]
            a["pipe"] = new.get(a["pipe"], a["pipe"])
            pid = a["pipe_id"] - 1
            if pid < ncol:
                c = self.cols[pid]
                if c.get("solid"):
                    a["zone"] = 0
                else:
                    pts = np.asarray(it["coords"], float)
                    mids = 0.5 * (pts[1:] + pts[:-1]) if len(pts) > 1 else pts
                    zs = [self.zone_at(x, y) for x, y in mids]
                    zs = [z for z in zs if z > 0]
                    a["zone"] = int(np.bincount(zs).argmax()) if zs else 0
            else:
                a["zone"] = self.lats[pid - ncol].get("zone", 0)
        s = res["summary"]
        s.update({
            "zones": self.nz,
            "structures": len(structures),
            "zone_submains": getattr(self, "n_zone_submains", 0),
            "cross_zone_laterals": getattr(self, "n_cross_zone", 0),
            "supply_gpm": sum(z["supply_gpm"] for z in zones),
            "supply_lps": sum(z["supply_lps"] for z in zones),
        })
        tot = sum(z["cells"] for z in zones) or 1
        for key in ("pct_ok", "pct_deep", "pct_wet", "pct_undrained"):
            s[key] = sum(z[key] * z["cells"] for z in zones) / tot
        for z in zones:
            if "no control structure" in z["flag"]:
                self._warn("Zone {} has no control structure.".format(z["zone"]))
        nb = sum(1 for st in structures if "below the tile" in st["flag"])
        if nb:
            self._warn("{} structure(s) have a board below the tile invert there; deepen the "
                       "tile or reduce the water-table depth.".format(nb))
        res["zones"] = zones
        res["structures"] = structures
        res["zone_grid"] = self.zl
        res["status_grid"] = self.status
        res["warnings"] = list(self.warnings)
        return res


def run_zonal_layout(dem, valid, grid, rings, outlet, params, zparams=None, zones=None,
                     progress=None, cancel=None, mains=None):
    return ZonalLayout(dem, valid, grid, rings, outlet, params, zparams=zparams,
                       zones=zones, progress=progress, cancel=cancel, mains=mains).run()
