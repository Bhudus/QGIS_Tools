"""Illini Main Tile Locator engine: find a tile main for a chosen lateral orientation.

Pure numpy, no QGIS imports, so it can be tested on its own. All lengths and
elevations are in metres; grades are fractions (m/m).

Laterals are to run either parallel to the contours (across the slope) or
perpendicular to them (down the slope). The engine

1. smooths the DEM and derives the fall line and contour directions (as in the
   Illini Subsurface Drain Layout plugin);
2. traces, once, the path a lateral would take from every part of the field:
   along the contours (both ways, turned just enough downhill to hold the target
   lateral grade) or down the fall line, curved with the terrain or straight on
   the field's dominant bearing. Along each path it tracks how fast neighbouring
   laterals converge, so laterals that would bunch up in a draw are known;
3. builds candidate mains from the outlet: the low-ground (thalweg) line, a
   fall-line or contour-following trace, straight mains over a fan of
   bearings, headers along the low side and dog-leg mains that run along the
   low side before turning up the slope;
4. scores every candidate: the drainable area whose laterals reach it with net
   fall and without converging, less the main's length and any length that
   cannot be buried within the depth and slope limits (Routine J of the Illini
   Drainage Tools);
5. trims the upstream end of the best main while that improves the score, and
   buries it with Routine J.
"""
import heapq
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .bury import BuryParams, Segment, bury_network
from .geometry import (
    cumulative_length,
    dist_to_segments,
    moving_average,
    polyline_length,
    rasterize_rings,
    resample,
    ring_segments,
    smooth,
)
from collections import deque

LAT_PARALLEL = "parallel"            # laterals parallel to the contours (across the slope)
LAT_PERPENDICULAR = "perpendicular"  # laterals perpendicular to the contours (down the slope)
SHAPE_CONTOUR = "contour"            # laterals bend with the terrain
SHAPE_STRAIGHT = "straight"          # laterals on one bearing
ALIGN_BEST = "best"                  # consider every candidate
ALIGN_TERRAIN = "terrain"            # mains that bend with the terrain only
ALIGN_STRAIGHT = "straight"          # straight (or two-leg straight) mains only

# coverage raster codes
ST_SETBACK = 0
ST_SERVED = 1
ST_CONVERGE = 2
ST_NOREACH = 3
ST_UPHILL = 4
STATUS_LABELS = {
    ST_SETBACK: "Boundary setback (no laterals)",
    ST_SERVED: "Laterals drain to the main",
    ST_CONVERGE: "Laterals converge before the main (needs a submain)",
    ST_NOREACH: "Laterals do not reach the main",
    ST_UPHILL: "Laterals would run uphill to the main",
}


class LocatorError(Exception):
    pass


class LocatorCancelled(Exception):
    pass


# ====================================================================== tile capacity

MANNING_N = {"plastic": 0.013, "clay_concrete": 0.012}


def lateral_capacity_length(intensity, spacing, diameter, slope, manning_n=0.013):
    """Longest lateral a tile can carry flowing full (the maxLength routine, Table 6).

    Manning's equation for a full circular pipe, solved for the length that
    drains a strip one spacing wide at the given drainage intensity:

        L = 10000 / (DI * S * n) * (D * s**0.1875 / 51.7) ** (1 / 0.375)

    with DI in mm/day, S in m, D in mm and s in m/m, giving L in m. Arguments
    here are in metres (intensity in m/day) and the result is in metres.
    """
    di = float(intensity) * 1000.0
    d = float(diameter) * 1000.0
    if di <= 0 or spacing <= 0 or d <= 0 or slope <= 0 or manning_n <= 0:
        raise ValueError("Drainage intensity, spacing, diameter, slope and Manning's n "
                         "must all be positive.")
    return 10000.0 / (di * float(spacing) * manning_n) * (
        d * float(slope) ** 0.1875 / 51.7) ** (1.0 / 0.375)


# defaults: 3/8 in/day, 80 ft spacing, 4 in corrugated plastic tubing on 0.1%
DEFAULT_MAX_LATERAL = lateral_capacity_length(0.375 * 0.0254, 80 * 0.3048, 4 * 0.0254, 0.001,
                                              MANNING_N["plastic"])


@dataclass
class LocatorParams:
    lateral_orientation: str = LAT_PARALLEL
    lateral_shape: str = SHAPE_CONTOUR
    alignment: str = ALIGN_BEST
    spacing: float = 80 * 0.3048         # lateral spacing: setback, smoothing, scoring
    max_lateral_length: Optional[float] = DEFAULT_MAX_LATERAL   # None = no limit
    contour_grade: float = 0.002         # target grade of contour-parallel laterals
    min_lateral_grade: float = 0.001     # net fall a lateral needs to reach the main
    min_main_grade: float = 0.001        # main minimum slope (Routine J)
    max_grade: float = 0.05              # maximum slope (Routine J)
    target_upper: float = 3.25 * 0.3048  # Routine J target band
    target_lower: float = 4.25 * 0.3048
    min_cover: float = 2.5 * 0.3048      # absolute upper tile depth
    max_depth: float = 6.0 * 0.3048      # absolute lower tile depth
    outlet_offset: float = 0.0
    lower_outlet: bool = False
    grade_break: float = 50 * 0.3048     # longest burying segment
    outlet_invert: Optional[float] = None
    setback: Optional[float] = None      # default: spacing / 2
    smooth_radius: Optional[float] = None  # default: spacing
    bearing_step: float = 5.0            # straight mains: bearing step, degrees
    main_weight: float = 0.5             # score cost per metre of main, in spacings
    problem_weight: float = 2.0          # score cost per metre that cannot be buried, in spacings
    converge_ratio: float = 0.33         # laterals closer than this share of the spacing merge
    main_stop_frac: float = 0.02         # thalweg main ends below this share of the field
    add_submains: bool = False           # add submains until the field is covered
    max_submains: int = 20
    units: str = "m"


# ====================================================================== helpers

def _bilinear_many(grid, arr, x, y):
    """Vectorised bilinear sample; nan outside the grid."""
    fi = (grid.y0 - y) / grid.cell - 0.5
    fj = (x - grid.x0) / grid.cell - 0.5
    i0 = np.floor(fi).astype(np.int64)
    j0 = np.floor(fj).astype(np.int64)
    out = np.full(x.shape, np.nan)
    ok = (i0 >= -1) & (j0 >= -1) & (i0 < grid.nrows) & (j0 < grid.ncols)
    if not ok.any():
        return out
    i0c = np.clip(i0, 0, grid.nrows - 2)
    j0c = np.clip(j0, 0, grid.ncols - 2)
    ti = np.clip(fi - i0c, 0.0, 1.0)
    tj = np.clip(fj - j0c, 0.0, 1.0)
    a = arr[i0c, j0c]
    b = arr[i0c, j0c + 1]
    c = arr[i0c + 1, j0c]
    d = arr[i0c + 1, j0c + 1]
    v = (a * (1 - tj) + b * tj) * (1 - ti) + (c * (1 - tj) + d * tj) * ti
    out[ok] = v[ok]
    return out


def _azimuth(dx, dy):
    """Compass bearing (degrees from north, clockwise) of a direction."""
    return (90.0 - math.degrees(math.atan2(dy, dx))) % 360.0


def _rot(v, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return (c * v[0] - s * v[1], s * v[0] + c * v[1])


# ====================================================================== engine

class MainLocator:
    def __init__(self, dem, valid, grid, rings, outlet, params, progress=None, cancel=None):
        self.dem = np.asarray(dem, dtype=float)
        self.valid = np.asarray(valid, dtype=bool) & np.isfinite(self.dem)
        self.g = grid
        self.rings = rings
        if outlet is None:
            raise LocatorError("An outlet location is required.")
        self.outlet = (float(outlet[0]), float(outlet[1]))
        self.p = params
        self._progress_cb = progress
        self._cancel_cb = cancel
        self.warnings = []

    # ------------------------------------------------------------ utilities
    def _progress(self, pct, msg):
        if self._cancel_cb is not None and self._cancel_cb():
            raise LocatorCancelled()
        if self._progress_cb is not None:
            self._progress_cb(pct, msg)

    def _warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    def _fmt(self, metres, decimals=0):
        if self.p.units == "ft":
            return "{:.{}f} ft".format(metres / 0.3048, decimals)
        return "{:.{}f} m".format(metres, decimals)

    def inside(self, x, y):
        return bool(self.g.lookup(self.inner, x, y, False))

    def _vec(self, ax, ay, x, y):
        u = self.g.bilinear(ax, x, y)
        v = self.g.bilinear(ay, x, y)
        n = math.hypot(u, v)
        if not (n == n) or n < 0.5:
            return None
        return (u / n, v / n)

    def ground(self, pts):
        z = np.array([self.g.bilinear(self.zfill, x, y) for x, y in pts], dtype=float)
        bad = ~np.isfinite(z)
        if bad.any():
            if bad.all():
                raise LocatorError("The main lies entirely outside the DEM.")
            s = cumulative_length(pts)
            z[bad] = np.interp(s[bad], s[~bad], z[~bad])
            self.off_dem = True
        return z

    def _cells(self, x, y):
        """Flat cell index of points; -1 outside the grid."""
        g = self.g
        i = np.floor((g.y0 - y) / g.cell).astype(np.int64)
        j = np.floor((x - g.x0) / g.cell).astype(np.int64)
        ok = (i >= 0) & (i < g.nrows) & (j >= 0) & (j < g.ncols)
        return np.where(ok, i * g.ncols + j, -1)

    # ------------------------------------------------------------ pipeline
    def run(self):
        self._progress(2, "Preparing terrain")
        self._prepare()
        self._progress(10, "Tracing lateral paths")
        self._trace_laterals()
        self._progress(35, "Building candidate mains")
        cands = self._candidates()
        if not cands:
            raise LocatorError("No candidate main could be run from this outlet into the "
                               "field. Check that the outlet lies at or near the field edge "
                               "on low ground, and that the boundary setback leaves room.")
        self._progress(45, "Scoring {} candidate mains".format(len(cands)))
        scored = []
        for k, c in enumerate(cands):
            if k % 5 == 0:
                self._progress(45 + 35 * k / len(cands),
                               "Scoring candidate mains ({}/{})".format(k + 1, len(cands)))
            ev = self._evaluate(c)
            if ev is not None:
                scored.append(ev)
        if not scored:
            raise LocatorError("None of the candidate mains could be evaluated.")
        scored.sort(key=lambda e: e["score"], reverse=True)
        self._progress(82, "Trimming the best main")
        best = self._trim(scored[0])
        if best is not scored[0]:
            scored.insert(0, best)
        net = None
        if self.p.add_submains:
            net = self._add_submains(best)
        self._progress(96, "Summarising")
        return self._results(best, scored, net)

    # ------------------------------------------------------------ prepare
    def _prepare(self):
        g, p = self.g, self.p
        S = float(p.spacing)
        if S <= 0:
            raise LocatorError("Lateral spacing must be positive.")
        self.S = S
        self.h = g.cell                       # tracing step
        self.hc = g.cell / 2.0                # main vertex spacing
        self.setback = p.setback if p.setback is not None else 0.5 * S
        self.depth_allow = max(0.0, p.max_depth - p.min_cover)

        X, Y = g.centers()
        self.X, self.Y = X, Y
        field = rasterize_rings(g, self.rings)
        self.field_full = field
        self.field = field & self.valid
        nf = int(field.sum())
        if nf == 0:
            raise LocatorError("The field boundary does not overlap the DEM.")
        if self.field.sum() < 0.95 * nf:
            self._warn("The DEM has no data over {:.0f}% of the field; that part is left "
                       "out.".format(100 * (1 - self.field.sum() / nf)))
        segs = ring_segments(self.rings)
        fi = np.nonzero(self.field)
        db = dist_to_segments(X[fi], Y[fi], segs)
        self.inner = np.zeros(g.shape, dtype=bool)
        self.inner[fi] = db >= self.setback
        if self.inner.sum() < 20:
            raise LocatorError("Nothing is left of the field after the boundary setback.")
        self.field_area = float(self.field_full.sum()) * g.cell ** 2
        self.inner_area = float(self.inner.sum()) * g.cell ** 2

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
        self.uc, self.uf = uc, uf             # contour bearing; downhill bearing
        self.mean_slope = float(self.slope[self.inner].mean())

        # snap the outlet to the drainable area
        ii, jj = np.nonzero(self.inner)
        d = np.hypot(X[ii, jj] - self.outlet[0], Y[ii, jj] - self.outlet[1])
        k = int(np.argmin(d))
        self.o_cell = (int(ii[k]), int(jj[k]))
        self.o_snap = (float(X[ii[k], jj[k]]), float(Y[ii[k], jj[k]]))
        self.o_dist = float(d[k])
        if d[k] > 3 * S:
            self._warn("The outlet is {} from the drainable part of the field; the main "
                       "is extended to reach it.".format(self._fmt(d[k])))
        zo = g.bilinear(self.zfill, *self.outlet)
        if zo != zo:
            zo = float(self.zs[self.o_cell])
        self.z_outlet = float(zo)
        low = float((self.zs[self.inner] < self.z_outlet - 0.05).mean())
        if low > 0.05:
            self._warn("{:.0f}% of the drainable area lies below the ground at the outlet; "
                       "tile there may not drain to this outlet.".format(100 * low))

    # ------------------------------------------------------------ lateral paths
    def _direction_fields(self):
        """Unit lateral direction grids, one per tracing family, and their divergence."""
        p = self.p
        shp = self.g.shape
        fams = []
        curved = p.lateral_shape == SHAPE_CONTOUR
        flat = self.slope < 2e-5
        if p.lateral_orientation == LAT_PERPENDICULAR:
            if curved:
                dx = np.where(flat, 0.0, self.fx)
                dy = np.where(flat, 0.0, self.fy)
            else:
                dx = np.full(shp, self.uf[0])
                dy = np.full(shp, self.uf[1])
            fams.append((dx, dy))
        else:
            cx, cy = -self.fy, self.fx
            for sgn in (1.0, -1.0):
                if curved:
                    with np.errstate(invalid="ignore", divide="ignore"):
                        a = np.arcsin(np.clip(p.contour_grade / np.maximum(self.slope, 1e-9),
                                              0.0, 1.0))
                    dx = np.cos(a) * sgn * cx + np.sin(a) * self.fx
                    dy = np.cos(a) * sgn * cy + np.sin(a) * self.fy
                    dx = np.where(flat, 0.0, dx)
                    dy = np.where(flat, 0.0, dy)
                else:
                    dx = np.full(shp, sgn * self.uc[0])
                    dy = np.full(shp, sgn * self.uc[1])
                fams.append((dx, dy))
        out = []
        for dx, dy in fams:
            if curved:
                div = (np.gradient(dx, axis=1) - np.gradient(dy, axis=0)) / self.g.cell
                div = np.where(self.field, np.nan_to_num(div), 0.0)
            else:
                div = None
            out.append((dx, dy, div))
        return out, curved

    def _trace_laterals(self):
        """Trace, from every block of the drainable area, the path a lateral would take.

        Candidate-independent, so it is done once; each candidate main is then
        scored by where these paths first meet it.
        """
        g, p = self.g, self.p
        ii, jj = np.nonzero(self.inner)
        n_in = len(ii)
        k = max(1, int(math.ceil(math.sqrt(n_in / 20000.0))))
        blk = (ii // k) * (g.ncols // k + 1) + (jj // k)
        ub, first, counts = np.unique(blk, return_index=True, return_counts=True)
        self.blk_k = k
        self.inner_ii, self.inner_jj = ii, jj
        self.inner_blk = np.searchsorted(ub, blk)        # block of every inner cell
        pi, pj = ii[first], jj[first]
        self.pt_w = counts.astype(float) * g.cell ** 2   # area each point stands for
        px = g.x0 + (pj + 0.5) * g.cell
        py = g.y0 - (pi + 0.5) * g.cell
        P = len(px)

        maxlen = p.max_lateral_length
        if not maxlen:
            r = np.vstack(self.rings)
            maxlen = 1.5 * float(np.hypot(np.ptp(r[:, 0]), np.ptp(r[:, 1])))
        h = self.h
        T = int(min(max(math.ceil(maxlen / h), 2), max(2, 6_000_000 // max(P, 1))))
        self.T = T
        self.ncell = g.nrows * g.ncols
        inner_f = np.concatenate([self.inner.ravel(), [False]])
        zs_f = np.concatenate([np.nan_to_num(self.zs).ravel(), [0.0]])
        fams, curved = self._direction_fields()
        nf = len(fams)
        self.paths = []
        logmin = math.log(max(self.p.converge_ratio, 1e-3))
        for fnum, (dxg, dyg, div) in enumerate(fams):
            idx = np.full((P, T), self.ncell, dtype=np.int32)
            zp = np.zeros((P, T), dtype=np.float32)
            conv = np.full(P, T, dtype=np.int32)
            x, y = px.copy(), py.copy()
            active = np.ones(P, dtype=bool)
            logw = np.zeros(P)
            zhist = None
            for t in range(T):
                if t % 25 == 0:
                    self._progress(10 + 25 * (fnum + t / T) / nf, "Tracing lateral paths")
                a = np.nonzero(active)[0]
                if len(a) == 0:
                    break
                c = self._cells(x[a], y[a])
                c = np.where(c < 0, self.ncell, c)
                ok = inner_f[c] | (t == 0)
                a, c = a[ok], c[ok]
                active[:] = False
                active[a] = True
                if len(a) == 0:
                    break
                idx[a, t] = c
                zp[a, t] = zs_f[c]
                if curved and t % 8 == 0:
                    # stuck in a pit or on a flat: the path stops falling
                    if zhist is not None and t > 0:
                        stuck = (zhist[a] - zs_f[c]) < 1e-4
                        a2 = a[stuck]
                        active[a2] = False
                    zhist = np.zeros(P) if zhist is None else zhist
                    zhist[a] = zs_f[c]
                a = a[active[a]]
                if len(a) == 0:
                    break
                xa, ya = x[a], y[a]
                u1 = _bilinear_many(g, dxg, xa, ya)
                v1 = _bilinear_many(g, dyg, xa, ya)
                n1 = np.hypot(u1, v1)
                good = np.isfinite(n1) & (n1 > 0.5)
                n1 = np.where(good, n1, 1.0)
                u1, v1 = np.nan_to_num(u1 / n1), np.nan_to_num(v1 / n1)
                xm, ym = xa + 0.5 * h * u1, ya + 0.5 * h * v1
                u2 = _bilinear_many(g, dxg, xm, ym)
                v2 = _bilinear_many(g, dyg, xm, ym)
                n2 = np.hypot(u2, v2)
                good2 = np.isfinite(n2) & (n2 > 0.5)
                n2 = np.where(good2, n2, 1.0)
                u2 = np.where(good2, u2 / n2, u1)
                v2 = np.where(good2, v2 / n2, v1)
                # laterals cannot turn sharply back on themselves
                good &= (u1 * u2 + v1 * v2) > 0.5
                x[a] = xa + h * u2
                y[a] = ya + h * v2
                if div is not None:
                    dv = _bilinear_many(g, div, xm, ym)
                    logw[a] += np.nan_to_num(dv) * h
                    newly = (logw[a] < logmin) & (conv[a] == T)
                    conv[a[newly]] = t + 1
                active[a[~good]] = False
            self.paths.append({"idx": idx, "z": zp, "conv": conv})
        self.pt_x, self.pt_y = px, py
        self.pt_z0 = zs_f[self._cells(px, py)]

    # ------------------------------------------------------------ candidates
    def _flood_main(self, fracs):
        """Low-ground (thalweg) main: the largest branch of the priority-flood tree."""
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
        acc = {}
        for c in order:
            acc[c] = 1
        for c in reversed(order):
            pa = parent[c]
            if pa >= 0:
                acc[pa] += acc[c]
        children = {}
        for c in order[1:]:
            children.setdefault(parent[c], []).append(c)
        self.reach_frac = len(order) / max(1, self.inner.sum())
        self.flood = {"parent": parent, "P": P, "start": start}
        out = []
        for frac in fracs:
            stop = max(3.0, frac * len(order))
            path = [start]
            c = start
            while c in children:
                nxt = max(children[c], key=lambda q: acc[q])
                if acc[nxt] < stop:
                    break
                path.append(nxt)
                c = nxt
            pts = [g.center_of(n // P - 1, n % P - 1) for n in path]
            out.append(np.array(pts[::-1]))          # upstream -> downstream
        return out

    def _trace_field(self, p0, ax, ay, max_len=1e6, slide=60.0, min_dot=0.5):
        """Trace a main through a direction field, sliding along the field edge."""
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
            if prev_step is not None and min_dot <= 0.0:
                # edge following: aim between the ideal and the last step, to round corners
                bx, by = v2[0] + prev_step[0], v2[1] + prev_step[1]
                bn = math.hypot(bx, by)
                if bn > 1e-9 and not self.inside(x + h * v2[0], y + h * v2[1]):
                    v2 = (bx / bn, by / bn)
            step = None
            for a in turns:
                ca, sa = math.cos(a), math.sin(a)
                dx = ca * v2[0] - sa * v2[1]
                dy = sa * v2[0] + ca * v2[1]
                if prev_step is not None and dx * prev_step[0] + dy * prev_step[1] < min_dot:
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

    def _ray(self, p0, u):
        """Straight line from p0 along u while it stays in the drainable area."""
        x, y = p0
        n = 0
        h = self.h
        while n < 100000 and self.inside(x + h * u[0], y + h * u[1]):
            x += h * u[0]
            y += h * u[1]
            n += 1
        return np.array([p0, (x, y)]) if n > 0 else np.array([p0])

    def _smooth_line(self, pts):
        if len(pts) < 3:
            return np.asarray(pts, dtype=float)
        win = max(3, int(round(self.S / self.g.cell)) | 1)
        q = moving_average(resample(pts, self.g.cell), win)
        q[0] = pts[0]
        q[-1] = pts[-1]
        return q

    def _candidates(self):
        """Candidate mains. Each arm runs upstream -> downstream and ends at the snapped outlet.

        The same set is built for both lateral orientations; the scoring, which
        traces the laterals, decides which suits the field.
        """
        p = self.p
        S = self.S
        o = self.o_snap
        upx, upy = -self.fx, -self.fy
        cx, cy = -self.fy, self.fx
        cands = []

        def add(name, method, curved, arms, bearing=None):
            arms = [np.asarray(a, dtype=float) for a in arms if len(a) >= 2]
            arms = [a for a in arms if polyline_length(a) >= S]
            if not arms:
                return
            cands.append({"name": name, "method": method, "curved": curved,
                          "arms": arms, "bearing": bearing})

        # low ground
        fracs = (p.main_stop_frac, 0.25 * p.main_stop_frac)
        try:
            for frac, thal in zip(fracs, self._flood_main(fracs)):
                add("Low ground (thalweg), ending at {:g}% of the field".format(100 * frac),
                    "thalweg", True, [self._smooth_line(thal)])
        except Exception:   # noqa: BLE001
            pass

        # fall line, uphill from the outlet
        fl = self._trace_field(o, upx, upy)
        add("Fall line from the outlet", "fall line", True, [self._smooth_line(fl)[::-1]])

        # contour-following headers, rising gently away from the outlet; the edge
        # variant keeps going around field corners
        with np.errstate(invalid="ignore", divide="ignore"):
            a = np.arcsin(np.clip(1.25 * p.min_main_grade / np.maximum(self.slope, 1e-9), 0, 1))
        headers, edge_headers = [], []
        for sgn in (1.0, -1.0):
            ax = np.cos(a) * sgn * cx + np.sin(a) * upx
            ay = np.cos(a) * sgn * cy + np.sin(a) * upy
            headers.append(self._smooth_line(self._trace_field(o, ax, ay)))   # outlet -> out
            edge_headers.append(self._smooth_line(
                self._trace_field(o, ax, ay, slide=90.0, min_dot=0.0)))
        for hs, tag in ((headers, ""), (edge_headers, ", following the field edge")):
            add("Header along the contours (both ways{})".format(tag), "header", True,
                [h[::-1] for h in hs])
            for h in hs:
                if len(h) >= 2:
                    add("Header along the contours toward {:.0f} deg{}".format(
                        _azimuth(h[min(len(h) - 1, 5)][0] - o[0],
                                 h[min(len(h) - 1, 5)][1] - o[1]), tag),
                        "header", True, [h[::-1]])

        # straight mains: single rays over the full circle, and lines through the outlet
        step = max(1.0, float(p.bearing_step))
        n = int(round(360.0 / step))
        rays = []
        for k in range(n):
            b = k * step
            u = (math.sin(math.radians(b)), math.cos(math.radians(b)))
            rays.append((b, self._ray(o, u)))
        for b, r in rays:
            add("Straight, toward {:.0f} deg".format(b), "straight", False, [r[::-1]], b)
        for k in range(n // 2):
            b1, r1 = rays[k]
            b2, r2 = rays[k + n // 2]
            if polyline_length(r1) >= S and polyline_length(r2) >= S:
                add("Straight through the outlet, {:.0f}-{:.0f} deg".format(b1, b2),
                    "straight", False, [r1[::-1], r2[::-1]], b1)

        # dog-legs: along the low side, then up the slope
        up = (-self.uf[0], -self.uf[1])
        straight_headers = [self._ray(o, self.uc), self._ray(o, (-self.uc[0], -self.uc[1]))]
        for hdr, curved in [(headers[0], True), (headers[1], True),
                            (straight_headers[0], False), (straight_headers[1], False)]:
            if len(hdr) < 2 or polyline_length(hdr) < 2 * S:
                continue
            hres = resample(hdr, self.hc)
            s = cumulative_length(hres)
            for frac in (0.2, 0.35, 0.5, 0.65, 0.8):
                v = int(np.argmin(np.abs(s - frac * s[-1])))
                if s[v] < S:
                    continue
                leg = hres[:v + 1][::-1]              # turn point -> outlet
                q = tuple(hres[v])
                for up_curved in (True, False):
                    if up_curved:
                        upl = self._smooth_line(self._trace_field(q, upx, upy))
                    else:
                        upl = self._ray(q, up)
                    if polyline_length(upl) < S:
                        continue
                    arm = np.vstack([upl[::-1], leg[1:]])
                    add("Dog-leg: {:.0f}% along the low side toward {:.0f} deg, then {} "
                        "up the slope".format(
                            100 * frac, _azimuth(q[0] - o[0], q[1] - o[1]),
                            "following the fall line" if up_curved else "straight"),
                        "dog-leg", curved or up_curved, [arm])

        if p.alignment == ALIGN_TERRAIN:
            cands = [c for c in cands if c["curved"]]
        elif p.alignment == ALIGN_STRAIGHT:
            cands = [c for c in cands if not c["curved"]]
        # longest arm first: it becomes the main, the others join it at the outlet
        for c in cands:
            c["arms"].sort(key=polyline_length, reverse=True)
        return cands

    # ------------------------------------------------------------ evaluation
    def _assemble(self, arms):
        """Resample arms; add the connector to the true outlet on the first arm."""
        a0 = resample(arms[0], self.hc)
        jv = len(a0) - 1                               # snapped outlet on arm 0
        if math.hypot(self.outlet[0] - a0[-1][0], self.outlet[1] - a0[-1][1]) > 0.25 * self.g.cell:
            conn = resample(np.array([a0[-1], self.outlet]), self.hc)
            a0 = np.vstack([a0, conn[1:]])
            a0[-1] = self.outlet
        out = [a0]
        for a in arms[1:]:
            r = resample(a, self.hc)
            r[-1] = a0[jv]
            out.append(r)
        return out, jv

    def _mask(self, arms):
        g = self.g
        m = np.zeros(g.shape, dtype=bool)
        for a in arms:
            q = resample(a, 0.5 * g.cell)
            c = self._cells(q[:, 0], q[:, 1])
            c = c[c >= 0]
            m.ravel()[c] = True
        d = m.copy()                                   # dilate 3 x 3
        d[1:, :] |= m[:-1, :]
        d[:-1, :] |= m[1:, :]
        e = d.copy()
        e[:, 1:] |= d[:, :-1]
        e[:, :-1] |= d[:, 1:]
        return np.concatenate([e.ravel(), [False]])

    def _coverage(self, arms):
        """Status of every evaluation point for laterals draining to this main."""
        mask = self._mask(arms)
        h = self.h
        P = len(self.pt_x)
        served = np.zeros(P, dtype=bool)
        code = np.full(P, ST_NOREACH, dtype=np.int8)
        lat_len = np.full(P, np.inf)
        for path in self.paths:
            hit = mask[path["idx"]]
            anyhit = hit.any(axis=1)
            first = np.argmax(hit, axis=1)
            rows = np.arange(P)
            L = first * h
            fall = self.pt_z0 - path["z"][rows, first]
            # the tile can also gain grade from depth, from minimum cover at the upper
            # end down to the maximum depth at the main
            okfall = fall + self.depth_allow >= self.p.min_lateral_grade * L - 1e-6
            conv_ok = first < path["conv"]
            s = anyhit & conv_ok & okfall
            served |= s
            lat_len = np.where(s, np.minimum(lat_len, L), lat_len)
            # reasons, most telling first
            code = np.where(~served & anyhit & ~okfall, ST_UPHILL, code)
            code = np.where(~served & anyhit & okfall & ~conv_ok & (code != ST_UPHILL),
                            ST_CONVERGE, code)
        code = np.where(served, ST_SERVED, code)
        return code, lat_len

    def _bury(self, arms, jv, ends=None):
        """Bury a network with the Routine J scheme.

        Arm 0 ends at the outlet. Every other arm joins arm ends[k] = (arm, vertex);
        by default the other arms of the main join arm 0 at vertex jv.
        """
        p = self.p
        if ends is None:
            ends = [None] + [(0, jv)] * (len(arms) - 1)
        pipes = []
        for k, a in enumerate(arms):
            pipes.append({"pts": a, "z": self.ground(a), "s": cumulative_length(a),
                          "fixed": set(), "end": ends[k]})
        for pp in pipes:
            if pp["end"] is not None:
                pipes[pp["end"][0]]["fixed"].add(int(pp["end"][1]))
        alias = {}
        for P, pp in enumerate(pipes):
            if pp["end"] is not None:
                alias[(P, len(pp["pts"]) - 1)] = pp["end"]

        def canon(key):
            seen = 0
            while key in alias and seen < 100:
                key = alias[key]
                seen += 1
            return key

        Lb = max(float(p.grade_break), 1e-3)
        segs, meta, by_down = [], [], {}
        for P, pp in enumerate(pipes):
            n = len(pp["pts"])
            s, z = pp["s"], pp["z"]
            cuts = sorted({0, n - 1} | {int(v) for v in pp["fixed"] if 0 < int(v) < n - 1})
            B = set(cuts)
            for a, b in zip(cuts[:-1], cuts[1:]):
                m = int(math.ceil((s[b] - s[a]) / Lb - 1e-9))
                for k in range(1, m):
                    target = s[a] + k * (s[b] - s[a]) / m
                    i = a + int(np.argmin(np.abs(s[a:b + 1] - target)))
                    if a < i < b:
                        B.add(i)
            B = sorted(B)
            pp["B"] = B
            for upv, dnv in zip(B[:-1], B[1:]):
                if s[dnv] - s[upv] < 1e-6:
                    continue
                seg = Segment(canon((P, dnv)), canon((P, upv)), float(z[dnv]), float(z[upv]),
                              float(s[dnv] - s[upv]), p.min_main_grade)
                by_down.setdefault(seg.down_key, []).append(len(segs))
                segs.append(seg)
                meta.append((P, upv, dnv))
        root = canon((0, len(pipes[0]["pts"]) - 1))
        order, used = [], set()
        queue = deque([root])
        while queue:
            node = queue.popleft()
            for i in by_down.get(node, ()):
                if i in used:
                    continue
                used.add(i)
                order.append(i)
                queue.append(segs[i].up_key)
        order += [i for i in range(len(segs)) if i not in used]
        rows = [segs[i] for i in order]
        if rows:
            rows[0].outlet = True
        bp = BuryParams(upper_depth=p.target_upper, lower_depth=p.target_lower,
                        abs_upper=p.min_cover, abs_lower=p.max_depth, max_slope=p.max_grade,
                        offset=p.outlet_offset, lower_outlet=p.lower_outlet)
        results, flagged, outlet_depth, start_depth, tiles = bury_network(
            rows, bp, cancel=self._cancel_cb)
        mid = 0.5 * (p.target_upper + p.target_lower)
        seg_rows = []
        problem = 0.0
        for n_ord, (i, res) in enumerate(zip(order, results), start=1):
            P, upv, dnv = meta[i]
            notes = res[4]
            seg_rows.append({"arm": P, "up": upv, "dn": dnv, "order": n_ord,
                             "length": segs[i].length, "ground_up": segs[i].up_ground,
                             "ground_dn": segs[i].down_ground, "tile_up": res[1],
                             "tile_dn": res[0], "in_slope": res[2], "out_slope": res[3],
                             "notes": notes})
            if any(("grade" in t.lower()) or ("connected" in t) for t in notes):
                problem += segs[i].length
        for P, pp in enumerate(pipes):
            B, s, z = pp["B"], pp["s"], pp["z"]
            tB = np.array([tiles.get(canon((P, i)), z[i] - mid) for i in B], dtype=float)
            pp["inv"] = np.interp(s, s[B], tB)
            depth = z - pp["inv"]
            bad = (depth > p.max_depth + 0.01) | (depth < p.min_cover - 0.01)
            if bad.any() and len(s) > 1:
                ds = np.gradient(s)
                problem += float(ds[bad].sum())
        return pipes, seg_rows, problem, outlet_depth, start_depth

    def _evaluate(self, cand, arms=None):
        arms_in = cand["arms"] if arms is None else arms
        arms_in = [a for a in arms_in if len(a) >= 2 and polyline_length(a) > 0]
        if not arms_in:
            return None
        arms_full, jv = self._assemble(arms_in)
        code, lat_len = self._coverage(arms_full)
        try:
            pipes, seg_rows, problem, od, sd = self._bury(arms_full, jv)
        except LocatorError:
            return None
        served = float(self.pt_w[code == ST_SERVED].sum())
        L = sum(polyline_length(a) for a in arms_full)
        S = self.S
        score = served - self.p.main_weight * S * L - self.p.problem_weight * S * problem
        return {"cand": cand, "arms_in": arms_in, "arms": arms_full, "jv": jv,
                "code": code, "lat_len": lat_len, "served_area": served,
                "served_pct": 100.0 * served / max(self.inner_area, 1e-9),
                "length": L, "problem": problem, "score": score, "pipes": pipes,
                "seg_rows": seg_rows, "outlet_depth": od, "start_depth": sd}

    def _trim(self, ev):
        """Shorten the upstream end of each arm while that raises the score."""
        best = ev
        S = self.S
        for k in range(len(best["arms_in"])):
            arm = best["arms_in"][k]
            Ltot = polyline_length(arm)
            step = max(S, Ltot / 12.0)
            improved = None
            d = step
            while d < Ltot - S:
                r = resample(arm, self.hc)
                s = cumulative_length(r)
                keep = s >= d
                cut = r[keep]
                if len(cut) < 2:
                    break
                trial_arms = list(best["arms_in"])
                trial_arms[k] = cut
                tr = self._evaluate(best["cand"], trial_arms)
                if tr is not None and tr["score"] > (improved or best)["score"] + 1e-6:
                    improved = tr
                d += step
            if improved is not None:
                best = dict(improved)
        if best is not ev:
            c = dict(best["cand"])
            c["name"] = c["name"] + " (trimmed)"
            best["cand"] = c
        return best

    # ------------------------------------------------------------ submains
    def _net_score(self, arms, ends):
        """Coverage, burial and score of a whole network (main arms plus submains)."""
        code, lat_len = self._coverage(arms)
        try:
            pipes, seg_rows, problem, od, sd = self._bury(arms, None, ends)
        except LocatorError:
            return None
        served = float(self.pt_w[code == ST_SERVED].sum())
        L = sum(polyline_length(a) for a in arms)
        score = (served - self.p.main_weight * self.S * L
                 - self.p.problem_weight * self.S * problem)
        return {"arms": arms, "ends": ends, "code": code, "lat_len": lat_len,
                "served_area": served, "length": L, "problem": problem, "score": score,
                "pipes": pipes, "seg_rows": seg_rows, "outlet_depth": od}

    def _unserved_clusters(self, code):
        """Connected patches of drainable cells whose laterals do not reach the network."""
        g = self.g
        U = np.zeros(g.shape, dtype=bool)
        cell_code = code[self.inner_blk]
        bad = cell_code != ST_SERVED
        U[self.inner_ii[bad], self.inner_jj[bad]] = True
        nc = g.ncols
        flat = U.ravel()
        seen = np.zeros(flat.shape, dtype=bool)
        clusters = []
        for start in np.nonzero(flat)[0]:
            if seen[start]:
                continue
            stack = [int(start)]
            seen[start] = True
            members = []
            while stack:
                c = stack.pop()
                members.append(c)
                i, j = divmod(c, nc)
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        ii, jj = i + di, j + dj
                        if 0 <= ii < g.nrows and 0 <= jj < nc:
                            n = ii * nc + jj
                            if flat[n] and not seen[n]:
                                seen[n] = True
                                stack.append(n)
            clusters.append(np.array(members))
        clusters.sort(key=len, reverse=True)
        return clusters

    def _nearest_vertex(self, arms, x, y):
        best = (0, 0, float("inf"))
        for k, a in enumerate(arms):
            d = np.hypot(a[:, 0] - x, a[:, 1] - y)
            v = int(np.argmin(d))
            if d[v] < best[2]:
                best = (k, v, float(d[v]))
        return best

    def _flood_path(self, cell, netmask):
        """Low-ground route from a cell down the flood tree until it meets the network."""
        fl = self.flood
        P = fl["P"]
        parent = fl["parent"]
        nc = self.g.ncols
        c = (cell // nc + 1) * P + (cell % nc + 1)
        pts = []
        steps = 0
        while c >= 0 and steps < 200000:
            i, j = c // P - 1, c % P - 1
            if netmask[i * nc + j]:
                break
            pts.append(self.g.center_of(i, j))
            c = parent[c]
            steps += 1
        if c < 0:
            return None
        return np.array(pts) if pts else None

    def _sub_candidates(self, arms, cluster):
        """Candidate submains into one unserved patch. Each runs upstream -> junction."""
        g, S = self.g, self.S
        netmask = self._mask(arms)[:-1]
        nc = g.ncols
        zc = np.nan_to_num(self.zs).ravel()[cluster]
        order = np.argsort(zc)
        picks = {int(cluster[order[int(round(q * (len(order) - 1)))]])
                 for q in (1.0, 0.85, 0.6, 0.3, 0.0)}
        ci, cj = cluster // nc, cluster % nc
        cx, cy = g.x0 + (cj.mean() + 0.5) * g.cell, g.y0 - (ci.mean() + 0.5) * g.cell
        d = np.hypot(g.x0 + (cj + 0.5) * g.cell - cx, g.y0 - (ci + 0.5) * g.cell - cy)
        picks.add(int(cluster[int(np.argmin(d))]))
        lowest = int(cluster[order[0]])
        out = []

        def add(name, pts, end):
            if pts is None or len(pts) < 2 or polyline_length(pts) < S:
                return
            out.append({"name": name, "pts": np.asarray(pts, dtype=float), "end": end})

        for t in picks:
            tx, ty = g.center_of(t // nc, t % nc)
            path = None
            if getattr(self, "flood", None) is not None:
                path = self._flood_path(t, netmask)
            if path is not None and len(path) >= 1:
                k, v, _ = self._nearest_vertex(arms, *path[-1])
                jp = arms[k][v]
                line = np.vstack([path, [jp]])
                line = self._smooth_line(line)
                line[-1] = jp
                add("Submain along the low ground", line, (k, v))
                if t == lowest:
                    # headers from the junction, along the contours
                    for sgn in (1.0, -1.0):
                        ax, ay = self._sub_hdr[sgn]
                        tr = self._trace_field(tuple(jp), ax, ay)
                        if len(tr) >= 2:
                            add("Submain header along the contours",
                                self._smooth_line(tr)[::-1], (k, v))
            # straight to the nearest point of the network
            k, v, dist = self._nearest_vertex(arms, tx, ty)
            jp = arms[k][v]
            line = resample(np.array([[tx, ty], jp]), g.cell)
            if all(self.inside(x, y) for x, y in line[:-2]):
                add("Straight submain", line, (k, v))
        return out

    def _add_submains(self, best):
        """Greedily add the submain that most improves the network score, until none helps."""
        p, S = self.p, self.S
        with np.errstate(invalid="ignore", divide="ignore"):
            a = np.arcsin(np.clip(1.25 * p.min_main_grade / np.maximum(self.slope, 1e-9), 0, 1))
        self._sub_hdr = {sgn: (np.cos(a) * sgn * -self.fy + np.sin(a) * -self.fx,
                               np.cos(a) * sgn * self.fx + np.sin(a) * -self.fy)
                         for sgn in (1.0, -1.0)}
        if getattr(self, "flood", None) is None:
            try:
                self._flood_main((p.main_stop_frac,))
            except Exception:   # noqa: BLE001
                self.flood = None
        n_main = len(best["arms"])
        arms = list(best["arms"])
        ends = [None] + [(0, best["jv"])] * (n_main - 1)
        cur = self._net_score(arms, ends)
        if cur is None:
            return None
        subs = []
        min_area = max(4 * S * S, 0.002 * self.inner_area)
        for it in range(int(p.max_submains)):
            self._progress(84 + 11 * it / max(1, p.max_submains),
                           "Adding submains ({} so far)".format(len(subs)))
            clusters = [c for c in self._unserved_clusters(cur["code"])
                        if len(c) * self.g.cell ** 2 >= min_area][:5]
            if not clusters:
                break
            best_ev, best_c = None, None
            for cl in clusters:
                for c in self._sub_candidates(arms, cl):
                    ev = self._net_score(arms + [resample(c["pts"], self.hc)],
                                         ends + [c["end"]])
                    if ev is not None and (best_ev is None or ev["score"] > best_ev["score"]):
                        best_ev, best_c = ev, c
            if best_ev is None or best_ev["score"] <= cur["score"] + S * S:
                break
            # trim the upper end while that helps
            pts = resample(best_c["pts"], self.hc)
            Ltot = polyline_length(pts)
            step = max(S, Ltot / 8.0)
            dcut = step
            sc = cumulative_length(pts)
            while dcut < Ltot - S:
                cut = pts[sc >= dcut]
                if len(cut) < 2:
                    break
                ev = self._net_score(arms + [cut], ends + [best_c["end"]])
                if ev is not None and ev["score"] > best_ev["score"] + 1e-6:
                    best_ev = ev
                dcut += step
            gain = best_ev["served_area"] - cur["served_area"]
            arms = best_ev["arms"]
            ends = best_ev["ends"]
            subs.append({"name": best_c["name"], "gain": gain})
            cur = best_ev
        cur["subs"] = subs
        cur["n_main"] = n_main
        return cur

    # ------------------------------------------------------------ results
    def _results(self, best, scored, net=None):
        p = self.p
        S = self.S
        subs_on = net is not None and len(net.get("subs", [])) > 0
        if subs_on:
            # the main as buried with its submains joined to it
            n_main = net["n_main"]
            best["pipes"] = net["pipes"][:n_main]
            best["seg_rows"] = [r for r in net["seg_rows"] if r["arm"] < n_main]
            best["outlet_depth"] = net["outlet_depth"]
        names = ["main"] + ["main arm {}".format(k + 1) for k in range(1, len(best["arms"]))]
        cand = best["cand"]
        lat_dir = {LAT_PARALLEL: "parallel to contours", LAT_PERPENDICULAR:
                   "perpendicular to contours"}[p.lateral_orientation]
        if p.lateral_orientation == LAT_PARALLEL:
            lat_bearing = _azimuth(*self.uc) % 180.0
        else:
            lat_bearing = _azimuth(*self.uf)
        notes_by_arm = {}
        for r in best["seg_rows"]:
            for t in r["notes"]:
                lst = notes_by_arm.setdefault(r["arm"], [])
                if t not in lst:
                    lst.append(t)
        mains = []
        for k, (pp, nm) in enumerate(zip(best["pipes"], names)):
            z, s, inv = pp["z"], pp["s"], pp["inv"]
            L = float(s[-1])
            depth = z - inv
            flags = []
            if float(np.nanmax(depth)) > p.max_depth + 0.01:
                flags.append("deeper than " + self._fmt(p.max_depth, 2))
            if float(np.nanmin(depth)) < p.min_cover - 0.01:
                flags.append("cover under {} between grade breaks".format(
                    self._fmt(p.min_cover, 2)))
            if k == 0 and p.outlet_invert is not None and p.outlet_invert - inv[-1] > 0:
                flags.append("invert {} below outlet".format(
                    self._fmt(p.outlet_invert - inv[-1], 2)))
            pts = pp["pts"]
            attrs = {
                "main_id": k + 1, "name": nm, "method": cand["name"],
                "flows_to": "outlet" if k == 0 else "main",
                "length_m": round(L, 2),
                "ground_us": round(float(z[0]), 3), "ground_ds": round(float(z[-1]), 3),
                "terrain_grade_pct": round(100 * float(z[0] - z[-1]) / L, 3) if L else 0.0,
                "invert_us": round(float(inv[0]), 3), "invert_ds": round(float(inv[-1]), 3),
                "design_grade_pct": round(100 * float(inv[0] - inv[-1]) / L, 3) if L else 0.0,
                "max_depth_m": round(float(np.nanmax(depth)), 3),
                "min_depth_m": round(float(np.nanmin(depth)), 3),
                "depth_ds_m": round(float(depth[-1]), 3),
                "bearing_deg": round(_azimuth(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]), 1),
                "lateral_dir": lat_dir,
                "lateral_bearing_deg": round(lat_bearing, 1),
                "bury_check": "; ".join(notes_by_arm.get(k, [])) or "OK",
                "flag": "; ".join(flags),
            }
            mains.append({"coords": pts, "attrs": attrs, "z": z, "s": s, "inv": inv})

        segments = []
        for sid, r in enumerate(sorted(best["seg_rows"], key=lambda q: q["order"]), start=1):
            pp = best["pipes"][r["arm"]]
            pts = pp["pts"][r["up"]:r["dn"] + 1]
            segments.append({"coords": pts, "attrs": {
                "seg_id": sid, "main_id": r["arm"] + 1, "BURY_ORDER": r["order"],
                "length_m": round(r["length"], 3),
                "ground1": round(r["ground_up"], 3), "ground2": round(r["ground_dn"], 3),
                "ElevDepth1": round(r["tile_up"], 3) if r["tile_up"] is not None else None,
                "BuryDepth1": (round(r["ground_up"] - r["tile_up"], 3)
                               if r["tile_up"] is not None else None),
                "ElevDepth2": round(r["tile_dn"], 3) if r["tile_dn"] is not None else None,
                "BuryDepth2": (round(r["ground_dn"] - r["tile_dn"], 3)
                               if r["tile_dn"] is not None else None),
                "InSlope": round(r["in_slope"], 6) if r["in_slope"] is not None else None,
                "OutSlope": round(r["out_slope"], 6) if r["out_slope"] is not None else None,
                "BURY_CHECK": "; ".join(r["notes"]) or "OK"}})

        # coverage raster of the chosen main
        cov = np.full(self.g.shape, 255, dtype=np.uint8)
        cov[self.field] = ST_SETBACK
        cov[self.inner_ii, self.inner_jj] = best["code"][self.inner_blk]

        # distinct alternatives only: drop candidates lying within half a spacing of
        # a better one (e.g. a main and its trimmed version)
        def pts_of(ev):
            return np.vstack([resample(a, self.g.cell) for a in ev["arms"]])

        def segs_of(ev):
            return np.vstack([np.hstack([a[:-1], a[1:]]) for a in ev["arms"]])

        distinct = []
        for ev in scored:
            pa = pts_of(ev)
            dup = False
            for other in distinct:
                d1 = dist_to_segments(pa[:, 0], pa[:, 1], segs_of(other)).mean()
                po = pts_of(other)
                d2 = dist_to_segments(po[:, 0], po[:, 1], segs_of(ev)).mean()
                if max(d1, d2) < 0.5 * S:
                    dup = True
                    break
            if not dup:
                distinct.append(ev)
        scored = distinct
        candidates = []
        for rank, ev in enumerate(scored, start=1):
            depth = np.concatenate([pp["z"] - pp["inv"] for pp in ev["pipes"]])
            notes = []
            for r in ev["seg_rows"]:
                for t in r["notes"]:
                    if t not in notes:
                        notes.append(t)
            pp0 = ev["pipes"][0]
            L0 = float(pp0["s"][-1]) or 1.0
            candidates.append({"coords": ev["arms"], "attrs": {
                "arms": len(ev["arms"]),
                "max_depth_m": round(float(np.nanmax(depth)), 3),
                "min_depth_m": round(float(np.nanmin(depth)), 3),
                "outlet_depth_m": (round(ev["outlet_depth"], 3)
                                   if ev["outlet_depth"] is not None else None),
                "design_grade_pct": round(100 * float(pp0["inv"][0] - pp0["inv"][-1]) / L0, 3),
                "bury_check": "; ".join(notes) or "OK",
                "rank": rank, "name": ev["cand"]["name"], "method": ev["cand"]["method"],
                "served_pct": round(ev["served_pct"], 2),
                "length_m": round(ev["length"], 1),
                "problem_m": round(ev["problem"], 1),
                "score": round(ev["score"] / 1e4, 4),
                "chosen": ev is best}})

        # submains
        submains = []
        if subs_on:
            n_main = net["n_main"]
            arm_names = (["main"] + ["main arm {}".format(k + 1) for k in range(1, n_main)]
                         + ["submain {}".format(k + 1) for k in range(len(net["subs"]))])
            sub_notes = {}
            for r in net["seg_rows"]:
                for t in r["notes"]:
                    lst = sub_notes.setdefault(r["arm"], [])
                    if t not in lst:
                        lst.append(t)
            for k in range(n_main, len(net["pipes"])):
                pp = net["pipes"][k]
                z, s_, inv = pp["z"], pp["s"], pp["inv"]
                L = float(s_[-1]) or 1.0
                depth = z - inv
                flags = []
                if float(np.nanmax(depth)) > p.max_depth + 0.01:
                    flags.append("deeper than " + self._fmt(p.max_depth, 2))
                if float(np.nanmin(depth)) < p.min_cover - 0.01:
                    flags.append("cover under {} between grade breaks".format(
                        self._fmt(p.min_cover, 2)))
                sb = net["subs"][k - n_main]
                submains.append({"coords": pp["pts"], "attrs": {
                    "sub_id": k - n_main + 1, "name": arm_names[k],
                    "joins": arm_names[net["ends"][k][0]], "method": sb["name"],
                    "length_m": round(float(s_[-1]), 2),
                    "ground_us": round(float(z[0]), 3), "ground_ds": round(float(z[-1]), 3),
                    "invert_us": round(float(inv[0]), 3),
                    "invert_ds": round(float(inv[-1]), 3),
                    "design_grade_pct": round(100 * float(inv[0] - inv[-1]) / L, 3),
                    "max_depth_m": round(float(np.nanmax(depth)), 3),
                    "min_depth_m": round(float(np.nanmin(depth)), 3),
                    "gain_pct": round(100 * sb["gain"] / max(self.inner_area, 1e-9), 2),
                    "bury_check": "; ".join(sub_notes.get(k, [])) or "OK",
                    "flag": "; ".join(flags)}})

        main_code = best["code"]
        main_served = 100.0 * float(self.pt_w[main_code == ST_SERVED].sum()) / max(
            self.inner_area, 1e-9)
        code = net["code"] if subs_on else best["code"]
        tgt = "the main and submains" if subs_on else "the main"
        share = {c: 100.0 * float(self.pt_w[code == c].sum()) / max(self.inner_area, 1e-9)
                 for c in (ST_SERVED, ST_CONVERGE, ST_NOREACH, ST_UPHILL)}
        lat_src = net["lat_len"] if subs_on else best["lat_len"]
        lat = lat_src[np.isfinite(lat_src)]
        if share[ST_CONVERGE] > 5:
            self._warn("Laterals converge before reaching {} over {:.0f}% of the "
                       "drainable area (draws or swales){}.".format(
                           tgt, share[ST_CONVERGE],
                           "" if subs_on else "; add submains there"))
        if share[ST_UPHILL] > 5:
            self._warn("Over {:.0f}% of the drainable area laterals would have to run uphill "
                       "to {}; that ground needs its own collector or outlet.".format(
                           share[ST_UPHILL], tgt))
        if share[ST_NOREACH] > 10:
            self._warn("Laterals from {:.0f}% of the drainable area do not reach {}{}; "
                       "consider {}a second outlet or the other lateral "
                       "orientation.".format(share[ST_NOREACH], tgt,
                                             " within the maximum lateral length "
                                             "({})".format(self._fmt(p.max_lateral_length))
                                             if p.max_lateral_length else "",
                                             "" if subs_on else "submains, "))
        if getattr(self, "reach_frac", 1.0) < 0.9:
            self._warn("{:.0f}% of the field cannot drain to this outlet without crossing "
                       "the setback zone.".format(100 * (1 - self.reach_frac)))
        if best["problem"] > 0.5 * S:
            self._warn("{} of the main cannot be buried within the depth and slope limits; "
                       "see bury_check and the main segments.".format(
                           self._fmt(best["problem"])))
        if getattr(self, "off_dem", False):
            self._warn("Part of the main runs beyond the DEM; ground there is carried along "
                       "from the nearest DEM data.")
        for m in mains:
            if "below outlet" in m["attrs"]["flag"]:
                self._warn("The main reaches the outlet below the given outlet invert.")

        summary = {
            "field_area_ha": round(self.field_area / 1e4, 3),
            "drainable_area_ha": round(self.inner_area / 1e4, 3),
            "method": cand["name"],
            "main_length_m": round(best["length"], 1),
            "served_pct": round(share[ST_SERVED], 2),
            "converge_pct": round(share[ST_CONVERGE], 2),
            "noreach_pct": round(share[ST_NOREACH], 2),
            "uphill_pct": round(share[ST_UPHILL], 2),
            "mean_lateral_run_m": round(float(lat.mean()), 1) if len(lat) else 0.0,
            "longest_lateral_run_m": round(float(lat.max()), 1) if len(lat) else 0.0,
            "problem_length_m": round(best["problem"], 1),
            "outlet_depth_m": (round(best["outlet_depth"], 3)
                               if best["outlet_depth"] is not None else None),
            "mean_slope_pct": round(100 * self.mean_slope, 3),
            "contour_bearing_deg": round(_azimuth(*self.uc) % 180.0, 1),
            "downhill_bearing_deg": round(_azimuth(*self.uf), 1),
            "lateral_bearing_deg": round(lat_bearing, 1),
            "candidates": len(scored),
        }
        summary["main_served_pct"] = round(main_served, 2)
        summary["submains"] = len(submains)
        summary["submain_length_m"] = round(sum(sm["attrs"]["length_m"] for sm in submains), 1)
        if subs_on:
            cov[self.inner_ii, self.inner_jj] = net["code"][self.inner_blk]
        return {"mains": mains, "segments": segments, "candidates": candidates,
                "submains": submains, "coverage": cov, "summary": summary,
                "warnings": list(self.warnings)}


def run_locator(dem, valid, grid, rings, outlet, params, progress=None, cancel=None):
    return MainLocator(dem, valid, grid, rings, outlet, params,
                       progress=progress, cancel=cancel).run()
