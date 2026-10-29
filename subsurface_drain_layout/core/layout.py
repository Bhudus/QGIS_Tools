"""Automated subsurface (tile) drainage layout.

Pure numpy engine: no QGIS imports, so it can be tested on its own.

Inputs are a DEM grid, the field boundary rings, an outlet point and a set of
design parameters. All lengths and elevations are in metres. The engine:

1. smooths the DEM and derives the fall-line and contour directions;
2. routes collectors (main, submains or a header) from the outlet;
3. builds a lateral direction field for the chosen pattern, orientation and
   shape, and places evenly spaced laterals in it (Jobard-Lefer style
   streamline seeding, so spacing holds even where laterals curve);
4. designs pipe inverts from the upstream ends down to the outlet at the
   minimum grade and cover, and flags anything too deep or unconnected.
"""
import heapq
import math
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .geometry import (
    SegmentHash,
    cumulative_length,
    dist_to_segments,
    label_regions,
    moving_average,
    nearest_vertex,
    polyline_length,
    rasterize_rings,
    resample,
    ring_segments,
    segments_cross,
    simplify,
    smooth,
    tangents,
)

PATTERN_PARALLEL = "parallel"
PATTERN_HERRINGBONE = "herringbone"
ORIENT_ACROSS = "across"  # laterals roughly parallel to the contours
ORIENT_ALONG = "along"    # laterals roughly perpendicular to the contours
SHAPE_STRAIGHT = "straight"
SHAPE_CONTOUR = "contour"  # laterals bend with the terrain


class LayoutError(Exception):
    pass


class LayoutCancelled(Exception):
    pass


@dataclass
class LayoutParams:
    spacing: float = 15.0
    pattern: str = PATTERN_PARALLEL
    orientation: str = ORIENT_ACROSS
    shape: str = SHAPE_CONTOUR
    herringbone_angle: float = 60.0      # degrees between lateral and main
    contour_grade: float = 0.002         # target lateral grade, contour-following (m/m)
    min_lateral_grade: float = 0.001     # m/m
    min_main_grade: float = 0.001        # m/m
    min_cover: float = 0.75              # m, ground to invert at the shallowest point
    max_depth: float = 1.5               # m, ground to invert
    outlet_invert: Optional[float] = None
    setback: Optional[float] = None      # default: spacing / 2
    smooth_radius: Optional[float] = None  # default: spacing
    parallelism: float = 0.9            # curved laterals: 0 = follow terrain, 1 = offset of neighbour
    main_stop_frac: float = 0.02         # main ends where contributing area < this share of field
    submain_frac: float = 0.10           # branches above this share become submains (0 = none)
    min_lateral_length: Optional[float] = None  # default: spacing
    max_connector: Optional[float] = None       # default: 2.5 * spacing


# ====================================================================== engine

class DrainageLayout:
    def __init__(self, dem, valid, grid, rings, outlet, params,
                 progress=None, cancel=None):
        self.dem = np.asarray(dem, dtype=float)
        self.valid = np.asarray(valid, dtype=bool) & np.isfinite(self.dem)
        self.g = grid
        self.rings = rings
        self.outlet = (float(outlet[0]), float(outlet[1]))
        self.p = params
        self._progress_cb = progress
        self._cancel_cb = cancel
        self.warnings = []

    # ------------------------------------------------------------ utilities
    def _progress(self, pct, msg):
        if self._cancel_cb is not None and self._cancel_cb():
            raise LayoutCancelled()
        if self._progress_cb is not None:
            self._progress_cb(pct, msg)

    def _warn(self, msg):
        self.warnings.append(msg)

    def inside(self, x, y):
        return bool(self.g.lookup(self.inner, x, y, False))

    def region_at(self, x, y):
        r = self.g.lookup(self.region, x, y, -1)
        return int(r)

    def _vec(self, ax, ay, x, y):
        u = self.g.bilinear(ax, x, y)
        v = self.g.bilinear(ay, x, y)
        n = math.hypot(u, v)
        if not (n == n) or n < 0.5:
            return None
        return (u / n, v / n)

    def D(self, x, y):
        return self._vec(self.Dx, self.Dy, x, y)

    def dcoll(self, x, y):
        d = self.g.bilinear(self.dcoll_r, x, y)
        return d if d == d else 1e9

    def ground(self, pts):
        z = np.array([self.g.bilinear(self.zfill, x, y) for x, y in pts])
        return z

    # ------------------------------------------------------------ pipeline
    def run(self):
        self._progress(2, "Preparing terrain")
        self._prepare()
        self._progress(15, "Routing collectors")
        self._build_collectors()
        self._progress(30, "Building lateral direction field")
        self._collector_rasters()
        self._direction_field()
        self._progress(40, "Placing laterals")
        self._place_laterals()
        self._progress(85, "Designing inverts")
        self._design()
        self._progress(95, "Summarising")
        return self._results()

    # ------------------------------------------------------------ prepare
    def _prepare(self):
        g, p = self.g, self.p
        S = float(p.spacing)
        if S <= 0:
            raise LayoutError("Lateral spacing must be positive.")
        self.S = S
        self.h = min(g.cell, S / 6.0)          # tracing step
        self.hc = g.cell / 2.0                 # collector vertex spacing
        self.d_sep = S
        self.d_test = 0.5 * S
        self.setback = p.setback if p.setback is not None else 0.5 * S
        self.lmin = p.min_lateral_length if p.min_lateral_length is not None else S
        self.max_conn = p.max_connector if p.max_connector is not None else 2.5 * S

        X, Y = g.centers()
        self.X, self.Y = X, Y
        field = rasterize_rings(g, self.rings)
        self.field_full = field
        self.field = field & self.valid
        nf = int(field.sum())
        if nf == 0:
            raise LayoutError("The field boundary does not overlap the DEM grid.")
        if self.field.sum() < 0.95 * nf:
            self._warn("The DEM has no data over {:.0f}% of the field; those parts are "
                       "left undrained.".format(100 * (1 - self.field.sum() / nf)))
        segs = ring_segments(self.rings)
        fi = np.nonzero(self.field)
        db = dist_to_segments(X[fi], Y[fi], segs)
        self.inner = np.zeros(g.shape, dtype=bool)
        self.inner[fi] = db >= self.setback
        if self.inner.sum() < 20:
            raise LayoutError("Nothing is left of the field after the boundary setback.")
        self.field_area = float(self.field_full.sum()) * g.cell ** 2

        sigma = (p.smooth_radius if p.smooth_radius is not None else S) / 2.0 / g.cell
        self.zs = smooth(self.dem, self.valid, sigma)
        self.zfill = np.where(self.valid, self.dem, self.zs)
        gr, gc = np.gradient(self.zs)
        gx = gc / g.cell
        gy = -gr / g.cell
        slope = np.hypot(gx, gy)
        with np.errstate(invalid="ignore", divide="ignore"):
            fx = np.where(slope > 1e-9, -gx / slope, 0.0)
            fy = np.where(slope > 1e-9, -gy / slope, 0.0)
        self.slope = np.nan_to_num(slope)
        self.fx = np.nan_to_num(fx)
        self.fy = np.nan_to_num(fy)

        # dominant (axial) contour direction, slope weighted
        m = self.inner & (self.slope > 1e-6)
        if m.sum() < 10:
            m = self.inner
        cx, cy = -self.fy[m], self.fx[m]
        th = np.arctan2(cy, cx)
        w = self.slope[m] + 1e-12
        th0 = 0.5 * math.atan2(float((w * np.sin(2 * th)).sum()),
                               float((w * np.cos(2 * th)).sum()))
        uc = (math.cos(th0), math.sin(th0))
        uf = (uc[1], -uc[0])
        if float((gx[m] * uf[0] + gy[m] * uf[1]).sum()) > 0:
            uf = (-uf[0], -uf[1])
        self.uc, self.uf = uc, uf
        self.mean_slope = float(self.slope[self.inner].mean())

        # snap the outlet to the drainable area
        ii, jj = np.nonzero(self.inner)
        d = np.hypot(X[ii, jj] - self.outlet[0], Y[ii, jj] - self.outlet[1])
        k = int(np.argmin(d))
        self.o_cell = (int(ii[k]), int(jj[k]))
        self.o_snap = (float(X[ii[k], jj[k]]), float(Y[ii[k], jj[k]]))
        if d[k] > 3 * S:
            self._warn("The outlet is {:.0f} m from the drainable part of the field; "
                       "the main is extended to reach it.".format(d[k]))

    # ------------------------------------------------------------ collectors
    def _flood_tree(self):
        """Priority-flood from the outlet over the drainable area.

        Every cell gets a parent along the lowest spill route to the outlet,
        which yields a drainage tree that passes through depressions.
        """
        g = self.g
        nr, nc = g.shape
        P = nc + 2
        zp = np.full((nr + 2, nc + 2), np.inf)
        zp[1:-1, 1:-1] = np.where(np.isfinite(self.zs), self.zs, np.inf)
        mp = np.zeros((nr + 2, nc + 2), dtype=bool)
        mp[1:-1, 1:-1] = self.inner
        zl = zp.ravel().tolist()
        ml = mp.ravel().tolist()
        visited = [False] * len(zl)
        parent = [-1] * len(zl)
        offs = (-P - 1, -P, -P + 1, -1, 1, P - 1, P, P + 1)
        start = (self.o_cell[0] + 1) * P + (self.o_cell[1] + 1)
        heap = [(zl[start], 0, start)]
        visited[start] = True
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
                    zn = zl[n]
                    heapq.heappush(heap, (zn if zn > pr else pr, cnt, n))
                    cnt += 1
        acc = [0] * len(zl)
        for c in order:
            acc[c] = 1
        for c in reversed(order):
            pa = parent[c]
            if pa >= 0:
                acc[pa] += acc[c]
        return order, parent, acc, P, start

    def _thalweg_collectors(self):
        order, parent, acc, P, start = self._flood_tree()
        nreach = len(order)
        if nreach < 0.9 * self.inner.sum():
            self._warn("{:.0f}% of the field cannot drain to this outlet without "
                       "crossing the setback zone and gets no main."
                       .format(100 * (1 - nreach / self.inner.sum())))
        stop = max(3.0, self.p.main_stop_frac * nreach)
        sub = self.p.submain_frac * nreach if self.p.submain_frac > 0 else float("inf")
        children = {}
        for c in order[1:]:
            children.setdefault(parent[c], []).append(c)
        best = {pa: max(chs, key=lambda q: acc[q]) for pa, chs in children.items()}

        def up(c):
            path = [c]
            while c in best and acc[best[c]] >= stop:
                c = best[c]
                path.append(c)
            return path

        def to_xy(path):
            pts = []
            for n in path:
                i, j = divmod(n, P)
                pts.append(self.g.center_of(i - 1, j - 1))
            return np.array(pts[::-1])  # upstream -> downstream

        paths = [(up(start), None)]
        q = deque([0])
        while q:
            k = q.popleft()
            path = paths[k][0]
            first = 0 if k == 0 else 1          # path[0] of a branch is its junction
            for idx in range(first, len(path) - 1):
                node, nxt = path[idx], path[idx + 1]
                for ch in children.get(node, []):
                    if ch == nxt or acc[ch] < sub:
                        continue
                    br = [node] + up(ch)
                    if (len(br) - 1) * self.g.cell < 2 * self.S:
                        continue
                    paths.append((br, k))
                    q.append(len(paths) - 1)

        win = max(3, int(round(self.S / self.g.cell)) | 1)
        cols = []
        for k, (path, host) in enumerate(paths):
            pts = moving_average(to_xy(path), win)
            if polyline_length(pts) < self.S and k > 0:
                continue
            cols.append({"pts": resample(pts, self.hc),
                         "kind": "main" if host is None else "submain",
                         "to": None if host is None else (host, None),
                         "src": k})
        # re-index hosts after any dropped branches
        remap = {c["src"]: i for i, c in enumerate(cols)}
        out = []
        for c in cols:
            if c["to"] is not None:
                if c["to"][0] not in remap:
                    continue
                c["to"] = (remap[c["to"][0]], None)
            out.append(c)
        if not out or polyline_length(out[0]["pts"]) < 0.5 * self.S:
            raise LayoutError("Could not route a main from the outlet; check that the "
                              "outlet lies at or near a low point of the field.")
        return out

    def _trace_field(self, p0, ax, ay, max_len=1e6, slide=60.0):
        """Trace a collector through a direction field.

        Where the ideal direction would leave the drainable area the collector
        slides along the edge, turning by up to `slide` degrees.
        """
        pts = [p0]
        x, y = p0
        prev_ideal = None
        prev_step = None
        L = 0.0
        cos_turn = math.cos(math.radians(40))
        h = self.h
        turns = [0.0]
        for a in range(10, int(slide) + 1, 10):
            turns += [math.radians(a), -math.radians(a)]
        while L < max_len:
            v = self._vec(ax, ay, x, y)
            if v is None:
                break
            if prev_ideal is not None and v[0] * prev_ideal[0] + v[1] * prev_ideal[1] < cos_turn:
                break
            v2 = self._vec(ax, ay, x + 0.5 * h * v[0], y + 0.5 * h * v[1]) or v
            step = None
            for a in turns:
                ca, sa = math.cos(a), math.sin(a)
                dx = ca * v2[0] - sa * v2[1]
                dy = sa * v2[0] + ca * v2[1]
                if prev_step is not None and dx * prev_step[0] + dy * prev_step[1] < 0.5:
                    continue
                if self.inside(x + h * dx, y + h * dy):
                    step = (dx, dy)
                    break
            if step is None:
                break
            x, y = x + h * step[0], y + h * step[1]
            pts.append((x, y))
            prev_ideal = v2
            prev_step = step
            L += h
        return np.array(pts)

    def _parallel_collectors(self):
        p = self.p
        curved = p.shape == SHAPE_CONTOUR
        shp = self.g.shape
        upx, upy = -self.fx, -self.fy                 # uphill fall line
        cx, cy = -self.fy, self.fx                    # contour tangent
        arms = []
        if p.orientation == ORIENT_ACROSS:
            # main runs up the slope; laterals cross it along the contours
            if curved:
                ax, ay = upx, upy
            else:
                ax, ay = np.full(shp, -self.uf[0]), np.full(shp, -self.uf[1])
            arms.append(self._trace_field(self.o_snap, ax, ay))
        else:
            # header runs across the slope, rising gently away from the outlet
            for sgn in (1.0, -1.0):
                if curved:
                    with np.errstate(invalid="ignore", divide="ignore"):
                        a = np.arcsin(np.clip(1.25 * p.min_main_grade /
                                              np.maximum(self.slope, 1e-9), 0, 1))
                    ax = np.cos(a) * sgn * cx + np.sin(a) * upx
                    ay = np.cos(a) * sgn * cy + np.sin(a) * upy
                else:
                    ax = np.full(shp, sgn * self.uc[0])
                    ay = np.full(shp, sgn * self.uc[1])
                arms.append(self._trace_field(self.o_snap, ax, ay))
        arms = [a for a in arms if polyline_length(a) >= self.S]
        if not arms:
            return []
        arms.sort(key=polyline_length, reverse=True)
        cols = []
        for k, a in enumerate(arms):
            pts = a[::-1]
            if curved:
                pts = moving_average(resample(pts, self.g.cell),
                                     max(3, int(round(self.S / self.g.cell)) | 1))
            cols.append({"pts": resample(pts, self.hc), "kind": "main",
                         "to": None if k == 0 else (0, None)})
        return cols

    def _build_collectors(self):
        p = self.p
        cols = []
        if p.pattern == PATTERN_PARALLEL:
            cols = self._parallel_collectors()
            if not cols:
                self._warn("A parallel main could not be run from this outlet into the "
                           "field; the main follows the low ground instead.")
        if not cols:
            cols = self._thalweg_collectors()
        # outlet connector on the main
        main = cols[0]
        if math.hypot(main["pts"][-1][0] - self.outlet[0],
                      main["pts"][-1][1] - self.outlet[1]) > 0.25 * self.g.cell:
            main["pts"] = resample(np.vstack([main["pts"], [self.outlet]]), self.hc) \
                if len(main["pts"]) > 1 else np.array([main["pts"][0], self.outlet])
            main["pts"][-1] = self.outlet
        # resolve junction vertices
        for c in cols[1:]:
            host = cols[c["to"][0]]["pts"]
            end = c["pts"][-1]
            d = np.hypot(host[:, 0] - end[0], host[:, 1] - end[1])
            if c["kind"] == "main":
                # second header arm joins at the snapped outlet, not the connector
                d = np.hypot(host[:, 0] - self.o_snap[0], host[:, 1] - self.o_snap[1])
            v = int(np.argmin(d))
            c["pts"] = resample(np.vstack([c["pts"][:-1], host[v]]), self.hc)
            c["pts"][-1] = host[v]
            c["to"] = (c["to"][0], v)
        self.cols = cols

    def _collector_rasters(self):
        allx, ally, cid, vid, tx, ty = [], [], [], [], [], []
        for k, c in enumerate(self.cols):
            pts = c["pts"]
            t = tangents(pts)
            allx.append(pts[:, 0])
            ally.append(pts[:, 1])
            tx.append(t[:, 0])
            ty.append(t[:, 1])
            cid.append(np.full(len(pts), k))
            vid.append(np.arange(len(pts)))
        self.cv_x = np.concatenate(allx)
        self.cv_y = np.concatenate(ally)
        self.cv_c = np.concatenate(cid)
        self.cv_v = np.concatenate(vid)
        self.cv_tx = np.concatenate(tx)
        self.cv_ty = np.concatenate(ty)
        cs = []
        for c in self.cols:
            pts = c["pts"]
            if len(pts) > 1:
                cs.append(np.hstack([pts[:-1], pts[1:]]))
        self.cseg = np.vstack(cs) if cs else np.zeros((0, 4))
        sub = np.arange(0, len(self.cv_x), 2)
        fi = np.nonzero(self.field)
        idx, d = nearest_vertex(self.X[fi], self.Y[fi], self.cv_x[sub], self.cv_y[sub])
        self.dcoll_r = np.full(self.g.shape, np.nan)
        self.dcoll_r[fi] = d
        self.near = np.full(self.g.shape, -1, dtype=np.int64)
        self.near[fi] = sub[idx]
        free = self.inner & (np.nan_to_num(self.dcoll_r, nan=0) >= 0.8 * self.g.cell)
        self.region = label_regions(free)

    def snap_collector(self, x, y):
        d = np.hypot(self.cv_x - x, self.cv_y - y)
        k = int(np.argmin(d))
        return float(d[k]), (float(self.cv_x[k]), float(self.cv_y[k])), \
            int(self.cv_c[k]), int(self.cv_v[k])

    # ------------------------------------------------------------ direction field
    def _direction_field(self):
        p = self.p
        fi = np.nonzero(self.field)
        px, py = self.X[fi], self.Y[fi]
        nidx = self.near[fi]
        vx = self.cv_x[nidx] - px
        vy = self.cv_y[nidx] - py
        vn = np.hypot(vx, vy)
        vn[vn < 1e-9] = 1.0
        vx, vy = vx / vn, vy / vn
        mx, my = self.cv_tx[nidx], self.cv_ty[nidx]
        fx, fy = self.fx[fi], self.fy[fi]
        cx, cy = -fy, fx
        S = self.slope[fi]
        w = np.clip(S / 0.002, 0.0, 1.0)

        if p.pattern == PATTERN_HERRINGBONE and p.shape == SHAPE_STRAIGHT:
            b = math.radians(min(max(p.herringbone_angle, 15.0), 90.0))
            dx = math.sin(b) * vx + math.cos(b) * mx
            dy = math.sin(b) * vy + math.cos(b) * my
        elif p.shape == SHAPE_STRAIGHT:
            u = self.uc if p.orientation == ORIENT_ACROSS else self.uf
            s = np.sign(u[0] * vx + u[1] * vy)
            s[s == 0] = 1.0
            dx, dy = s * u[0], s * u[1]
        elif p.orientation == ORIENT_ACROSS:
            s = np.sign(cx * vx + cy * vy)
            s[s == 0] = 1.0
            with np.errstate(invalid="ignore", divide="ignore"):
                a = np.arcsin(np.clip(p.contour_grade / np.maximum(S, 1e-9), 0.0, 1.0))
            dx = np.cos(a) * s * cx + np.sin(a) * fx
            dy = np.cos(a) * s * cy + np.sin(a) * fy
            dx, dy = w * dx + (1 - w) * vx, w * dy + (1 - w) * vy
        else:
            dx, dy = w * fx + (1 - w) * vx, w * fy + (1 - w) * vy
        n = np.hypot(dx, dy)
        n[n < 1e-9] = 1.0
        self.Dx = np.zeros(self.g.shape)
        self.Dy = np.zeros(self.g.shape)
        self.Dx[fi] = dx / n
        self.Dy[fi] = dy / n

    # ------------------------------------------------------------ laterals
    def _guide_geom(self, guide):
        if guide is None or self.parallel <= 0:
            return None
        G = np.asarray(self.lats[guide]["pts"], dtype=float)
        if len(G) < 2:
            return None
        A = G[:-1]
        B = G[1:]
        AB = B - A
        L2 = (AB * AB).sum(axis=1)
        L2[L2 == 0] = 1e-12
        return A, AB, L2, len(A)

    def _steer(self, x, y, step, geom):
        """Blend a step direction toward running parallel to the guide lateral at one spacing."""
        A, AB, L2, n = geom
        t = ((x - A[:, 0]) * AB[:, 0] + (y - A[:, 1]) * AB[:, 1]) / L2
        tc = np.clip(t, 0.0, 1.0)
        qx = A[:, 0] + tc * AB[:, 0]
        qy = A[:, 1] + tc * AB[:, 1]
        d2 = (x - qx) ** 2 + (y - qy) ** 2
        k = int(np.argmin(d2))
        # beyond either end of the guide: no reference, follow the terrain
        if (k == 0 and t[k] < 0) or (k == n - 1 and t[k] > 1):
            return step
        dist = math.sqrt(float(d2[k]))
        S = self.S
        if dist > 2.5 * S or dist < 1e-9:
            return step
        w = self.parallel * (1.0 if dist <= 1.5 * S else (2.5 * S - dist) / S)
        tl = math.sqrt(float(L2[k]))
        tx, ty = AB[k, 0] / tl, AB[k, 1] / tl
        if tx * step[0] + ty * step[1] < 0:
            tx, ty = -tx, -ty
        nx, ny = (x - qx[k]) / dist, (y - qy[k]) / dist
        corr = max(-0.5, min(0.5, (S - dist) / S))
        gx, gy = tx + corr * nx, ty + corr * ny
        sx, sy = (1 - w) * step[0] + w * gx, (1 - w) * step[1] + w * gy
        sn = math.hypot(sx, sy)
        if sn < 1e-9:
            return step
        return (sx / sn, sy / sn)

    def _trace(self, p0, sigma, reg, fixed=None, guide=None):
        """Trace a lateral through D (or along a fixed bearing for straight laterals).

        sigma=+1 traces downstream, -1 upstream. With a guide lateral, curved
        laterals are steered to run parallel to it at one spacing.
        """
        geom = self._guide_geom(guide) if fixed is None else None
        pts = [p0]
        x, y = p0
        prev = None
        h = self.h
        cos_turn = math.cos(math.radians(50))
        dc_prev = self.dcoll(x, y)
        L = 0.0
        turned = 0.0
        prev_step = None
        while L < 20000:
            if fixed is not None:
                k2 = fixed
            else:
                k1 = self.D(x, y)
                if k1 is None:
                    return pts, "singular", None
                if prev is not None and k1[0] * prev[0] + k1[1] * prev[1] < cos_turn:
                    return pts, "turn", None
                k2 = self.D(x + 0.5 * h * sigma * k1[0], y + 0.5 * h * sigma * k1[1]) or k1
                if prev is not None and k2[0] * prev[0] + k2[1] * prev[1] < cos_turn:
                    return pts, "turn", None
            step = (sigma * k2[0], sigma * k2[1])
            if geom is not None:
                step = self._steer(x, y, step, geom)
            if prev_step is not None and fixed is None:
                # judge turning on the path actually taken, so guided laterals cannot orbit
                dot = prev_step[0] * step[0] + prev_step[1] * step[1]
                if dot < cos_turn:
                    return pts, "turn", None
                cross = prev_step[0] * step[1] - prev_step[1] * step[0]
                turned += math.asin(max(-1.0, min(1.0, cross)))
                if abs(turned) > self.max_turn:
                    return pts, "loop", None
            qx, qy = x + h * step[0], y + h * step[1]
            if not self.inside(qx, qy):
                return pts, "boundary", None
            dc = self.dcoll(qx, qy)
            if sigma > 0 and dc < h:
                pts.append((qx, qy))
                return pts, "collector", None
            if self.region_at(qx, qy) != reg:
                if sigma > 0 and dc < 2 * self.g.cell:
                    pts.append((qx, qy))
                    return pts, "collector", None
                return pts, "region", None
            if sigma < 0 and dc < self.d_test and dc < dc_prev - 1e-9:
                return pts, "collector_near", None
            hit = self.hash.nearest(qx, qy, reg, self.d_test)
            if hit is not None:
                return pts, "lateral", hit
            pts.append((qx, qy))
            prev = k2
            prev_step = step
            x, y = qx, qy
            dc_prev = dc
            L += h
        return pts, "maxlen", None

    def _extend_to_collector(self, p, d):
        x, y = p
        step = 0.5 * self.g.cell
        for _ in range(12):
            if self.dcoll(x, y) < 0.75 * self.g.cell:
                dist, q, cid, v = self.snap_collector(x, y)
                return q, cid, v
            x += step * d[0]
            y += step * d[1]
        return None

    def _seed_status(self, cid, t, side, s, pts, tg):
        x = float(np.interp(t, s, pts[:, 0]))
        y = float(np.interp(t, s, pts[:, 1]))
        tx = float(np.interp(t, s, tg[:, 0]))
        ty = float(np.interp(t, s, tg[:, 1]))
        tn = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / tn * side, tx / tn * side
        off = 1.5 * self.g.cell
        sx, sy = x + off * nx, y + off * ny
        if not self.inside(sx, sy):
            return "bad", None
        reg = self.region_at(sx, sy)
        if reg < 0:
            return "bad", None
        if self.straight and self.p.pattern == PATTERN_HERRINGBONE:
            b = math.radians(min(max(self.p.herringbone_angle, 15.0), 90.0))
            dx = -math.sin(b) * nx + math.cos(b) * tx / tn
            dy = -math.sin(b) * ny + math.cos(b) * ty / tn
            dn = math.hypot(dx, dy)
            d = (dx / dn, dy / dn)
        else:
            d = self.D(sx, sy)
        if d is None or (-d[0] * nx - d[1] * ny) < 0.2:
            return "bad", None
        if self.hash.nearest(sx, sy, reg, self.d_sep) is not None:
            return "prox", None
        if self.tried.nearest(sx, sy, reg, 0.2 * self.d_sep) is not None:
            return "bad", None
        return "ok", ((sx, sy), reg, d)

    def _add_lateral(self, pts, reg, origin, conn, flags, bearing=None):
        lid = len(self.lats)
        self.lats.append({"pts": pts, "region": reg, "origin": origin,
                          "conn": conn, "flags": flags, "bearing": bearing})
        self.hash.add_polyline(pts, lid, reg)
        return lid

    def _seed_from_collectors(self):
        for cid, col in enumerate(self.cols):
            pts = col["pts"]
            if len(pts) < 2:
                continue
            s = cumulative_length(pts)
            tg = tangents(pts)
            for side in (1.0, -1.0):
                t = 0.0
                t_prox = None
                last = None
                while t <= s[-1]:
                    st, info = self._seed_status(cid, t, side, s, pts, tg)
                    if st == "prox":
                        t_prox = t
                    elif st == "ok":
                        if t_prox is not None:   # refine to the exact spacing
                            lo, hi = t_prox, t
                            for _ in range(7):
                                mid = 0.5 * (lo + hi)
                                st2, info2 = self._seed_status(cid, mid, side, s, pts, tg)
                                if st2 == "ok":
                                    hi, info = mid, info2
                                else:
                                    lo = mid
                            t = hi
                        t_prox = None
                        (sx, sy), reg, d = info
                        fixed = d if self.straight else None
                        guide = last if (last is not None and
                                         self.lats[last]["region"] == reg) else None
                        up, status, _ = self._trace((sx, sy), -1.0, reg, fixed, guide)
                        line = up[::-1]
                        junction = self._extend_to_collector((sx, sy), d)
                        if junction is None:
                            dist, q, c2, v2 = self.snap_collector(sx, sy)
                            junction = (q, c2, v2)
                        q, c2, v2 = junction
                        line = line + [q]
                        if polyline_length(line) >= self.lmin:
                            last = self._add_lateral(line, reg, "primary",
                                                     ("collector", c2, v2), [], fixed)
                        else:
                            self.tried.add_polyline([(sx, sy), (sx, sy)], -1, reg)
                    else:
                        t_prox = None
                    t += self.h

    def _infill(self):
        queue = deque(range(len(self.lats)))
        stride = max(1, int(round(0.5 * self.d_sep / self.h)))
        cap = 50000
        while queue and len(self.lats) < cap:
            lid = queue.popleft()
            if lid % 50 == 0:
                self._progress(40 + min(40, 40 * len(self.lats) / max(1, self.expected)),
                               "Placing laterals ({})".format(len(self.lats)))
            pts = self.lats[lid]["pts"]
            bearing = self.lats[lid]["bearing"]
            for k in range(0, len(pts), stride):
                x, y = pts[k]
                d = bearing if bearing is not None else self.D(x, y)
                if d is None:
                    continue
                for sgn in (1.0, -1.0):
                    cx = x - sgn * self.d_sep * d[1]
                    cy = y + sgn * self.d_sep * d[0]
                    if not self.inside(cx, cy):
                        continue
                    reg = self.region_at(cx, cy)
                    if reg < 0 or self.dcoll(cx, cy) < 2 * self.h:
                        continue
                    if self.hash.nearest(cx, cy, reg, 0.97 * self.d_sep) is not None:
                        continue
                    if self.tried.nearest(cx, cy, reg, 0.25 * self.d_sep) is not None:
                        continue
                    new = self._infill_from((cx, cy), reg, bearing, lid)
                    if new is not None:
                        queue.append(new)

    def _clear(self, a, b):
        """True if a straight connector a-b crosses no lateral or collector."""
        ax, ay = a
        bx, by = b
        if segments_cross(ax, ay, bx, by, self.hash.segments_near(ax, ay, bx, by)):
            return False
        cs = self.cseg
        lo_x, hi_x = min(ax, bx), max(ax, bx)
        lo_y, hi_y = min(ay, by), max(ay, by)
        m = ((np.maximum(cs[:, 0], cs[:, 2]) >= lo_x) & (np.minimum(cs[:, 0], cs[:, 2]) <= hi_x) &
             (np.maximum(cs[:, 1], cs[:, 3]) >= lo_y) & (np.minimum(cs[:, 1], cs[:, 3]) <= hi_y))
        return not segments_cross(ax, ay, bx, by, cs[m])

    def _tee_target(self, hit):
        _, host, k, t = hit
        hp = self.lats[host]["pts"]
        v = k if t < 0.5 else k + 1
        return host, v, hp[v]

    def _infill_from(self, c, reg, bearing=None, guide=None):
        up, _, _ = self._trace(c, -1.0, reg, bearing, guide)
        dn, status, hit = self._trace(c, 1.0, reg, bearing, guide)
        line = up[::-1] + dn[1:]
        flags = []
        conn = None
        end = line[-1]
        if status == "collector":
            dist, q, cid, v = self.snap_collector(*end)
            line.append(q)
            conn = ("collector", cid, v)
        elif status == "lateral":
            host, v, q = self._tee_target(hit)
            if self._clear(end, q):
                line.append(q)
                conn = ("lateral", host, v)
        if conn is None:
            # open end: short straight connector to a collector, else a tee
            dist, q, cid, v = self.snap_collector(*end)
            if dist <= self.max_conn and self._clear(end, q):
                line.append(q)
                conn = ("collector", cid, v)
                flags.append("connector {:.0f} m".format(dist))
            else:
                hit = self.hash.nearest(end[0], end[1], reg, self.max_conn)
                if hit is not None:
                    host, v, q = self._tee_target(hit)
                    if self._clear(end, q):
                        line.append(q)
                        conn = ("lateral", host, v)
                        if hit[0] > self.d_test * 1.05:
                            flags.append("connector {:.0f} m".format(hit[0]))
            if conn is None:
                flags.append("unconnected: needs its own collector")
        if polyline_length(line) < self.lmin:
            self.tried.add_polyline([c, c], -1, reg)
            return None
        return self._add_lateral(line, reg, "infill", conn, flags, bearing)

    def _place_laterals(self):
        self.straight = self.p.shape == SHAPE_STRAIGHT
        self.parallel = min(max(float(self.p.parallelism), 0.0), 1.0)
        self.max_turn = math.radians(120.0)   # stop laterals circling a knoll
        bucket = self.d_sep * 1.1 + self.h
        self.hash = SegmentHash(bucket)
        self.tried = SegmentHash(bucket)
        self.lats = []
        self.expected = max(1.0, self.inner.sum() * self.g.cell ** 2 / self.S / (4 * self.S))
        self._seed_from_collectors()
        self._infill()

    # ------------------------------------------------------------ inverts
    @staticmethod
    def _invert_profile(z, s, cover, grade, caps):
        inv = np.empty(len(z))
        for i in range(len(z)):
            v = z[i] - cover
            if i > 0:
                v = min(v, inv[i - 1] - grade * (s[i] - s[i - 1]))
            c = caps.get(i)
            if c is not None:
                v = min(v, c)
            inv[i] = v
        return inv

    def _design(self):
        p = self.p
        # laterals: tees first
        tees = {}
        for lid, L in enumerate(self.lats):
            if L["conn"] and L["conn"][0] == "lateral":
                tees.setdefault(L["conn"][1], []).append(lid)
        done = [False] * len(self.lats)

        def solve(lid, depth=0):
            if done[lid]:
                return
            caps = {}
            for t in tees.get(lid, []):
                if depth < 200:
                    solve(t, depth + 1)
                v = self.lats[t]["conn"][2]
                ds = self.lats[t].get("inv")
                if ds is not None:
                    caps[v] = min(caps.get(v, 1e18), ds[-1])
            L = self.lats[lid]
            pts = np.asarray(L["pts"])
            z = self.ground(pts)
            s = cumulative_length(pts)
            L["z"], L["s"] = z, s
            L["inv"] = self._invert_profile(z, s, p.min_cover, p.min_lateral_grade, caps)
            done[lid] = True

        for lid in range(len(self.lats)):
            solve(lid)

        # collectors: deepest branches first
        def tree_depth(k):
            d = 0
            while self.cols[k]["to"] is not None and d < 100:
                k = self.cols[k]["to"][0]
                d += 1
            return d

        caps = [dict() for _ in self.cols]
        nlat = [0] * len(self.cols)
        for L in self.lats:
            if L["conn"] and L["conn"][0] == "collector":
                c, v = L["conn"][1], L["conn"][2]
                caps[c][v] = min(caps[c].get(v, 1e18), L["inv"][-1])
                nlat[c] += 1
        for k in sorted(range(len(self.cols)), key=tree_depth, reverse=True):
            c = self.cols[k]
            pts = c["pts"]
            z = self.ground(pts)
            s = cumulative_length(pts)
            c["z"], c["s"] = z, s
            c["inv"] = self._invert_profile(z, s, p.min_cover, p.min_main_grade, caps[k])
            c["nlat"] = nlat[k]
            if c["to"] is not None:
                h, v = c["to"]
                caps[h][v] = min(caps[h].get(v, 1e18), c["inv"][-1])

    # ------------------------------------------------------------ results
    def _pipe_attrs(self, z, s, inv):
        L = float(s[-1]) if len(s) else 0.0
        depth = z - inv
        return {
            "length_m": round(L, 1),
            "ground_us": round(float(z[0]), 3),
            "ground_ds": round(float(z[-1]), 3),
            "terrain_grade_pct": round(100 * float(z[0] - z[-1]) / L, 3) if L > 0 else 0.0,
            "invert_us": round(float(inv[0]), 3),
            "invert_ds": round(float(inv[-1]), 3),
            "design_grade_pct": round(100 * float(inv[0] - inv[-1]) / L, 3) if L > 0 else 0.0,
            "max_depth_m": round(float(np.nanmax(depth)), 2),
            "depth_ds_m": round(float(depth[-1]), 2),
        }

    def _coverage(self):
        g = self.g
        r = 0.5 * self.S / g.cell
        R = int(math.ceil(r))
        oi, oj = np.mgrid[-R:R + 1, -R:R + 1]
        keep = oi ** 2 + oj ** 2 <= r * r
        oi, oj = oi[keep], oj[keep]
        cov = np.zeros(g.shape, dtype=bool)
        lines = [np.asarray(L["pts"]) for L in self.lats] + [c["pts"] for c in self.cols]
        for pts in lines:
            q = resample(pts, g.cell)
            i = np.floor((g.y0 - q[:, 1]) / g.cell).astype(int)
            j = np.floor((q[:, 0] - g.x0) / g.cell).astype(int)
            ii = (i[:, None] + oi[None, :]).ravel()
            jj = (j[:, None] + oj[None, :]).ravel()
            ok = (ii >= 0) & (ii < g.nrows) & (jj >= 0) & (jj < g.ncols)
            cov[ii[ok], jj[ok]] = True
        self.covered = cov
        return (float((cov & self.inner).sum()) / max(1, self.inner.sum()),
                float((cov & self.field_full).sum()) / max(1, self.field_full.sum()))

    def _results(self):
        p = self.p
        tol = 0.25 * self.g.cell
        names = {}
        mains = subs = 0
        for k, c in enumerate(self.cols):
            if c["kind"] == "main":
                mains += 1
                names[k] = "main" if mains == 1 else "main {}".format(mains)
            else:
                subs += 1
                names[k] = "submain {}".format(subs)

        col_fixed = [set() for _ in self.cols]
        lat_fixed = [set() for _ in self.lats]
        for L in self.lats:
            if L["conn"] and L["conn"][0] == "collector":
                col_fixed[L["conn"][1]].add(L["conn"][2])
            elif L["conn"]:
                lat_fixed[L["conn"][1]].add(L["conn"][2])
        for c in self.cols:
            if c["to"] is not None:
                col_fixed[c["to"][0]].add(c["to"][1])

        collectors = []
        for k, c in enumerate(self.cols):
            a = self._pipe_attrs(c["z"], c["s"], c["inv"])
            flags = []
            if a["max_depth_m"] > p.max_depth:
                flags.append("deeper than {:.2f} m".format(p.max_depth))
            if c["to"] is None and p.outlet_invert is not None:
                short = p.outlet_invert - a["invert_ds"]
                if short > 0:
                    flags.append("invert {:.2f} m below outlet".format(short))
            a.update({"col_id": k + 1, "name": names[k], "kind": c["kind"],
                      "flows_to": "outlet" if c["to"] is None else names[c["to"][0]],
                      "n_laterals": c.get("nlat", 0), "flag": "; ".join(flags)})
            collectors.append({"coords": simplify(c["pts"], tol, col_fixed[k]), "attrs": a})

        laterals = []
        n_unconn = n_deep = 0
        for lid, L in enumerate(self.lats):
            a = self._pipe_attrs(L["z"], L["s"], L["inv"])
            flags = list(L["flags"])
            if a["max_depth_m"] > p.max_depth:
                flags.append("deeper than {:.2f} m".format(p.max_depth))
                n_deep += 1
            if L["conn"] is None:
                n_unconn += 1
                to = ""
            elif L["conn"][0] == "collector":
                to = names[L["conn"][1]]
            else:
                to = "lateral {}".format(L["conn"][1] + 1)
            a.update({"lat_id": lid + 1, "origin": L["origin"], "flows_to": to,
                      "flag": "; ".join(flags)})
            laterals.append({"coords": simplify(np.asarray(L["pts"]), tol, lat_fixed[lid]),
                             "attrs": a})

        cov, cov_field = self._coverage()
        lat_len = sum(l["attrs"]["length_m"] for l in laterals)
        col_len = sum(c["attrs"]["length_m"] for c in collectors)
        area_ha = self.field_area / 1e4
        if n_unconn:
            self._warn("{} lateral(s) have no collector downstream; the low ground they "
                       "drain to needs its own submain or outlet.".format(n_unconn))
        if n_deep:
            self._warn("{} lateral(s) exceed the maximum depth; consider a contour-"
                       "following layout, a lower outlet or a submain.".format(n_deep))
        for c in collectors:
            if "below outlet" in c["attrs"]["flag"]:
                self._warn("The main reaches the outlet {} — the outlet is not deep "
                           "enough for this layout.".format(c["attrs"]["flag"]))
        az = (90 - math.degrees(math.atan2(self.uc[1], self.uc[0]))) % 180
        summary = {
            "field_area_ha": round(area_ha, 2),
            "laterals": len(laterals),
            "lateral_length_m": round(lat_len, 0),
            "collector_length_m": round(col_len, 0),
            "total_length_m": round(lat_len + col_len, 0),
            "length_per_ha_m": round((lat_len + col_len) / area_ha, 0) if area_ha else 0,
            "coverage_pct": round(100 * float(cov), 1),
            "field_coverage_pct": round(100 * float(cov_field), 1),
            "mean_slope_pct": round(100 * self.mean_slope, 2),
            "contour_azimuth_deg": round(az, 1),
            "unconnected_laterals": n_unconn,
            "deep_laterals": n_deep,
        }
        network = self._network(names, col_fixed, lat_fixed,
                                [c["attrs"]["flag"] for c in collectors],
                                [l["attrs"]["flag"] for l in laterals], tol)
        summary["network_links"] = len(network)
        return {"laterals": laterals, "collectors": collectors, "network": network,
                "summary": summary, "warnings": list(self.warnings)}

    # ------------------------------------------------------------ network
    def _network(self, names, col_fixed, lat_fixed, col_flags, lat_flags, tol):
        """Merge all pipes into one node-link network.

        Every pipe is split at the vertices where other pipes join it, so each
        link runs node to node and flows from `from_node` to `to_node`. Links
        carry their downstream link, the pipe length upstream of their lower
        end and the area drained by the laterals upstream (lateral length x
        spacing), which is what pipe sizing needs.
        """
        node_ids = {}
        alias = {}

        def node(key):
            seen = 0
            while key in alias and seen < 100:
                key = alias[key]
                seen += 1
            if key not in node_ids:
                node_ids[key] = len(node_ids) + 1
            return node_ids[key]

        node(("outlet",))
        pipes = []   # (kind, name, pts, z, s, inv, split, end_key, flag, is_lateral)
        for k, c in enumerate(self.cols):
            n = len(c["pts"])
            end = ("outlet",) if c["to"] is None else ("c", c["to"][0], c["to"][1])
            pipes.append((c["kind"], names[k], c["pts"], c["z"], c["s"], c["inv"],
                          col_fixed[k], ("c", k), end, col_flags[k], False, n))
        for lid, L in enumerate(self.lats):
            n = len(L["pts"])
            if L["conn"] is None:
                end = ("l", lid, n - 1)
            elif L["conn"][0] == "collector":
                end = ("c", L["conn"][1], L["conn"][2])
            else:
                end = ("l", L["conn"][1], L["conn"][2])
            pipes.append(("lateral", "lateral {}".format(lid + 1), np.asarray(L["pts"]),
                          L["z"], L["s"], L["inv"], lat_fixed[lid], ("l", lid), end,
                          lat_flags[lid], True, n))

        for p in pipes:   # a pipe's last vertex is the node it discharges into
            pkey, end, n = p[7], p[8], p[11]
            if end != pkey + (n - 1,):
                alias[pkey + (n - 1,)] = end

        links = []
        for pid, (kind, name, pts, z, s, inv, fixed, pkey, end, flag, is_lat, n) \
                in enumerate(pipes, start=1):
            cuts = sorted({0, n - 1} | {int(v) for v in fixed if 0 < int(v) < n - 1})
            for a, b in zip(cuts[:-1], cuts[1:]):
                if s[b] - s[a] < 1e-6:
                    continue
                to_key = end if b == n - 1 else pkey + (b,)
                attrs = self._pipe_attrs(z[a:b + 1], s[a:b + 1] - s[a], inv[a:b + 1])
                attrs.update({"pipe_id": pid, "kind": kind, "pipe": name,
                              "from_node": node(pkey + (a,)), "to_node": node(to_key),
                              "flag": flag})
                links.append({"coords": simplify(pts[a:b + 1], tol), "attrs": attrs,
                              "lateral": is_lat})

        # downstream link and accumulated upstream quantities
        out_of = {}
        for i, L in enumerate(links):
            out_of[L["attrs"]["from_node"]] = i
        up_len = [L["attrs"]["length_m"] for L in links]
        up_lat = [L["attrs"]["length_m"] if L["lateral"] else 0.0 for L in links]
        tot_len = list(up_len)
        tot_lat = list(up_lat)
        for i, L in enumerate(links):
            j = out_of.get(L["attrs"]["to_node"])
            seen = 0
            while j is not None and j != i and seen < len(links):
                tot_len[j] += up_len[i]
                tot_lat[j] += up_lat[i]
                j = out_of.get(links[j]["attrs"]["to_node"])
                seen += 1
        net = []
        for i, L in enumerate(links):
            a = L["attrs"]
            j = out_of.get(a["to_node"])
            a.update({"id": i + 1,
                      "ds_link": j + 1 if j is not None else 0,
                      "upstream_m": round(tot_len[i], 1),
                      "drained_ha": round(tot_lat[i] * self.S / 1e4, 3)})
            net.append({"coords": L["coords"], "attrs": a})
        # flag every link whose flow path never reaches the outlet
        outlet = node(("outlet",))
        reaches = {}
        for i in range(len(net)):
            path = []
            j = i
            ok = False
            while j is not None and len(path) <= len(net):
                if j in reaches:
                    ok = reaches[j]
                    break
                path.append(j)
                to = net[j]["attrs"]["to_node"]
                if to == outlet:
                    ok = True
                    break
                j = out_of.get(to)
            for k in path:
                reaches[k] = ok
        for i, L in enumerate(net):
            if not reaches.get(i, False) and "unconnected" not in L["attrs"]["flag"]:
                f = L["attrs"]["flag"]
                L["attrs"]["flag"] = (f + "; " if f else "") + "does not reach the outlet"
        return net


def run_layout(dem, valid, grid, rings, outlet, params, progress=None, cancel=None):
    return DrainageLayout(dem, valid, grid, rings, outlet, params,
                          progress=progress, cancel=cancel).run()
