"""Grid and geometry helpers for the drainage layout engine (numpy only)."""
import math

import numpy as np


class Grid:
    """Regular raster grid. (x0, y0) is the upper-left corner, rows run south."""

    def __init__(self, x0, y0, cell, nrows, ncols):
        self.x0 = float(x0)
        self.y0 = float(y0)
        self.cell = float(cell)
        self.nrows = int(nrows)
        self.ncols = int(ncols)

    @property
    def shape(self):
        return (self.nrows, self.ncols)

    def xs(self):
        return self.x0 + (np.arange(self.ncols) + 0.5) * self.cell

    def ys(self):
        return self.y0 - (np.arange(self.nrows) + 0.5) * self.cell

    def centers(self):
        return np.meshgrid(self.xs(), self.ys())

    def cell_of(self, x, y):
        j = int(math.floor((x - self.x0) / self.cell))
        i = int(math.floor((self.y0 - y) / self.cell))
        if 0 <= i < self.nrows and 0 <= j < self.ncols:
            return i, j
        return None

    def center_of(self, i, j):
        return (self.x0 + (j + 0.5) * self.cell, self.y0 - (i + 0.5) * self.cell)

    def lookup(self, arr, x, y, default=None):
        ij = self.cell_of(x, y)
        if ij is None:
            return default
        return arr[ij]

    def bilinear(self, arr, x, y):
        """Bilinear sample of a 2-D array at a world point; nan outside."""
        fi = (self.y0 - y) / self.cell - 0.5
        fj = (x - self.x0) / self.cell - 0.5
        i0 = int(math.floor(fi))
        j0 = int(math.floor(fj))
        if i0 < 0 or j0 < 0 or i0 >= self.nrows - 1 or j0 >= self.ncols - 1:
            # clamp at the outer half cell
            if -1 <= i0 < self.nrows and -1 <= j0 < self.ncols:
                i0 = min(max(i0, 0), self.nrows - 2)
                j0 = min(max(j0, 0), self.ncols - 2)
                fi = min(max(fi, i0), i0 + 1)
                fj = min(max(fj, j0), j0 + 1)
            else:
                return float("nan")
        ti = fi - i0
        tj = fj - j0
        a = arr[i0, j0]
        b = arr[i0, j0 + 1]
        c = arr[i0 + 1, j0]
        d = arr[i0 + 1, j0 + 1]
        return (a * (1 - tj) + b * tj) * (1 - ti) + (c * (1 - tj) + d * tj) * ti


# ---------------------------------------------------------------- polygons

def ring_segments(rings):
    """Concatenate ring edges into an (n, 4) array x1, y1, x2, y2."""
    segs = []
    for ring in rings:
        r = np.asarray(ring, dtype=float)
        if len(r) < 3:
            continue
        if not np.allclose(r[0], r[-1]):
            r = np.vstack([r, r[:1]])
        segs.append(np.hstack([r[:-1], r[1:]]))
    if not segs:
        return np.zeros((0, 4))
    return np.vstack(segs)


def rasterize_rings(grid, rings):
    """Even-odd fill of polygon rings (outer rings and holes, any number of parts)."""
    segs = ring_segments(rings)
    mask = np.zeros(grid.shape, dtype=bool)
    if len(segs) == 0:
        return mask
    x1, y1, x2, y2 = segs.T
    ys = grid.ys()
    for i, y in enumerate(ys):
        c = (y1 > y) != (y2 > y)
        if not c.any():
            continue
        xc = x1[c] + (y - y1[c]) * (x2[c] - x1[c]) / (y2[c] - y1[c])
        xc.sort()
        for a, b in zip(xc[0::2], xc[1::2]):
            j0 = int(math.ceil((a - grid.x0) / grid.cell - 0.5))
            j1 = int(math.floor((b - grid.x0) / grid.cell - 0.5))
            j0 = max(j0, 0)
            j1 = min(j1, grid.ncols - 1)
            if j1 >= j0:
                mask[i, j0:j1 + 1] = True
    return mask


def dist_to_segments(px, py, segs, budget=4_000_000):
    """Minimum distance from points to a set of segments, chunked over segments."""
    px = np.asarray(px, dtype=float)
    py = np.asarray(py, dtype=float)
    out = np.full(px.shape, np.inf)
    if len(segs) == 0 or px.size == 0:
        return out
    step = max(1, budget // max(px.size, 1))
    for s in range(0, len(segs), step):
        x1, y1, x2, y2 = (segs[s:s + step, k][None, :] for k in range(4))
        dx = x2 - x1
        dy = y2 - y1
        l2 = dx * dx + dy * dy
        l2 = np.where(l2 == 0, 1e-12, l2)
        t = ((px[:, None] - x1) * dx + (py[:, None] - y1) * dy) / l2
        t = np.clip(t, 0.0, 1.0)
        ex = px[:, None] - (x1 + t * dx)
        ey = py[:, None] - (y1 + t * dy)
        d = np.sqrt(ex * ex + ey * ey).min(axis=1)
        np.minimum(out, d, out=out)
    return out


def nearest_vertex(px, py, vx, vy, budget=6_000_000):
    """Index of, and distance to, the nearest of a set of vertices for each point."""
    px = np.asarray(px, dtype=float)
    py = np.asarray(py, dtype=float)
    idx = np.zeros(px.shape, dtype=np.int64)
    dist = np.full(px.shape, np.inf)
    if len(vx) == 0:
        return idx, dist
    step = max(1, budget // max(len(vx), 1))
    for s in range(0, px.size, step):
        dx = px[s:s + step, None] - vx[None, :]
        dy = py[s:s + step, None] - vy[None, :]
        d2 = dx * dx + dy * dy
        k = np.argmin(d2, axis=1)
        idx[s:s + step] = k
        dist[s:s + step] = np.sqrt(d2[np.arange(len(k)), k])
    return idx, dist


# ---------------------------------------------------------------- filters

def _box_sum(a, r, axis):
    a = np.moveaxis(a, axis, -1)
    pad = [(0, 0)] * (a.ndim - 1) + [(r + 1, r)]
    c = np.cumsum(np.pad(a, pad, mode="constant"), axis=-1)
    out = c[..., 2 * r + 1:] - c[..., :-2 * r - 1]
    return np.moveaxis(out, -1, axis)


def smooth(z, valid, sigma_cells):
    """Approximate Gaussian smoothing (three box passes) that ignores invalid cells."""
    w = int(round(math.sqrt(4.0 * sigma_cells ** 2 + 1.0)))
    if w < 3:
        out = np.where(valid, z, np.nan)
        return out
    r = w // 2
    a = np.where(valid, z, 0.0).astype(float)
    m = valid.astype(float)
    for _ in range(3):
        for axis in (0, 1):
            a = _box_sum(a, r, axis)
            m = _box_sum(m, r, axis)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(m > 1e-9, a / np.where(m > 1e-9, m, 1.0), np.nan)
    return out


def label_regions(free):
    """4-connected component labels of a boolean array; -1 where not free."""
    nr, nc = free.shape
    lab = np.full(nr * nc, -1, dtype=np.int64)
    fl = free.ravel()
    free_list = fl.tolist()
    lab_list = lab.tolist()
    current = 0
    for start in np.flatnonzero(fl).tolist():
        if lab_list[start] >= 0:
            continue
        lab_list[start] = current
        stack = [start]
        while stack:
            c = stack.pop()
            i, j = divmod(c, nc)
            if j > 0:
                n = c - 1
                if free_list[n] and lab_list[n] < 0:
                    lab_list[n] = current
                    stack.append(n)
            if j < nc - 1:
                n = c + 1
                if free_list[n] and lab_list[n] < 0:
                    lab_list[n] = current
                    stack.append(n)
            if i > 0:
                n = c - nc
                if free_list[n] and lab_list[n] < 0:
                    lab_list[n] = current
                    stack.append(n)
            if i < nr - 1:
                n = c + nc
                if free_list[n] and lab_list[n] < 0:
                    lab_list[n] = current
                    stack.append(n)
        current += 1
    return np.asarray(lab_list, dtype=np.int64).reshape(nr, nc)


# ---------------------------------------------------------------- polylines

def polyline_length(pts):
    p = np.asarray(pts, dtype=float)
    if len(p) < 2:
        return 0.0
    return float(np.hypot(*np.diff(p, axis=0).T).sum())


def cumulative_length(pts):
    p = np.asarray(pts, dtype=float)
    if len(p) < 2:
        return np.zeros(len(p))
    return np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(p, axis=0).T))])


def resample(pts, step):
    """Resample a polyline at a uniform arc-length step, keeping both ends."""
    p = np.asarray(pts, dtype=float)
    if len(p) < 2:
        return p.copy()
    s = cumulative_length(p)
    keep = np.concatenate([[True], np.diff(s) > 1e-9])
    p, s = p[keep], s[keep]
    if len(p) < 2:
        return p.copy()
    n = max(1, int(math.ceil(s[-1] / step)))
    t = np.linspace(0.0, s[-1], n + 1)
    return np.column_stack([np.interp(t, s, p[:, 0]), np.interp(t, s, p[:, 1])])


def moving_average(pts, window):
    """Symmetric moving average with a shrinking window so the endpoints stay fixed."""
    p = np.asarray(pts, dtype=float)
    n = len(p)
    k = window // 2
    if n < 3 or k < 1:
        return p.copy()
    i = np.arange(n)
    ki = np.minimum(np.minimum(k, i), n - 1 - i)
    cs = np.vstack([np.zeros((1, 2)), np.cumsum(p, axis=0)])
    return (cs[i + ki + 1] - cs[i - ki]) / (2 * ki + 1)[:, None]


def simplify(pts, tol, fixed=(), return_index=False):
    """Douglas-Peucker simplification; vertices listed in `fixed` are always kept.

    With return_index, also returns the indices of the kept vertices.
    """
    p = np.asarray(pts, dtype=float)
    n = len(p)
    if n < 3:
        return (p.copy(), np.arange(n)) if return_index else p.copy()
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    anchors = sorted({0, n - 1} | {int(i) for i in fixed if 0 <= int(i) < n})
    for i in anchors:
        keep[i] = True
    stack = list(zip(anchors[:-1], anchors[1:]))
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ax, ay = p[a]
        bx, by = p[b]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        seg = p[a + 1:b]
        if l2 == 0:
            d = np.hypot(seg[:, 0] - ax, seg[:, 1] - ay)
        else:
            t = np.clip(((seg[:, 0] - ax) * dx + (seg[:, 1] - ay) * dy) / l2, 0, 1)
            d = np.hypot(seg[:, 0] - (ax + t * dx), seg[:, 1] - (ay + t * dy))
        k = int(np.argmax(d))
        if d[k] > tol:
            m = a + 1 + k
            keep[m] = True
            stack.append((a, m))
            stack.append((m, b))
    if return_index:
        return p[keep], np.flatnonzero(keep)
    return p[keep]


def tangents(pts):
    """Unit tangent at each vertex (direction of increasing index)."""
    p = np.asarray(pts, dtype=float)
    if len(p) < 2:
        return np.zeros_like(p)
    t = np.gradient(p, axis=0)
    n = np.hypot(t[:, 0], t[:, 1])
    n[n == 0] = 1.0
    return t / n[:, None]


def segments_cross(ax, ay, bx, by, segs, eps=1e-6):
    """True if segment a-b properly crosses any of segs (n, 4); end touches are ignored."""
    if len(segs) == 0:
        return False
    segs = np.asarray(segs, dtype=float)
    rx, ry = bx - ax, by - ay
    qx, qy = segs[:, 0], segs[:, 1]
    sx, sy = segs[:, 2] - qx, segs[:, 3] - qy
    rxs = rx * sy - ry * sx
    ok = np.abs(rxs) > 1e-12
    if not ok.any():
        return False
    den = np.where(ok, rxs, 1.0)
    t = ((qx - ax) * sy - (qy - ay) * sx) / den
    u = ((qx - ax) * ry - (qy - ay) * rx) / den
    return bool((ok & (t > eps) & (t < 1 - eps) & (u > eps) & (u < 1 - eps)).any())


class SegmentHash:
    """Spatial hash of polyline segments for nearest-distance queries."""

    def __init__(self, bucket):
        self.b = float(bucket)
        self.d = {}

    def segments_near(self, ax, ay, bx, by):
        """All stored segments in buckets along a-b, as an (n, 4) array."""
        L = math.hypot(bx - ax, by - ay)
        n = max(1, int(math.ceil(L / (0.5 * self.b))))
        keys = set()
        for k in range(n + 1):
            kx, ky = self._key(ax + (bx - ax) * k / n, ay + (by - ay) * k / n)
            for ix in (kx - 1, kx, kx + 1):
                for iy in (ky - 1, ky, ky + 1):
                    keys.add((ix, iy))
        out = []
        for key in keys:
            for s in self.d.get(key, ()):
                out.append(s[:4])
        return np.array(out) if out else np.zeros((0, 4))

    def _key(self, x, y):
        return (int(math.floor(x / self.b)), int(math.floor(y / self.b)))

    def add_polyline(self, pts, owner, region):
        for k in range(len(pts) - 1):
            x1, y1 = pts[k]
            x2, y2 = pts[k + 1]
            key = self._key(0.5 * (x1 + x2), 0.5 * (y1 + y2))
            self.d.setdefault(key, []).append((x1, y1, x2, y2, owner, region, k))

    def nearest(self, x, y, region, rmax):
        """Closest segment within rmax in the same region: (dist, owner, k, t) or None."""
        kx, ky = self._key(x, y)
        reach = int(math.ceil(rmax / self.b))
        best = None
        bd = rmax
        for ix in range(kx - reach, kx + reach + 1):
            for iy in range(ky - reach, ky + reach + 1):
                lst = self.d.get((ix, iy))
                if not lst:
                    continue
                for x1, y1, x2, y2, owner, reg, k in lst:
                    if region is not None and reg != region:
                        continue
                    dx = x2 - x1
                    dy = y2 - y1
                    l2 = dx * dx + dy * dy
                    if l2 > 0:
                        t = ((x - x1) * dx + (y - y1) * dy) / l2
                        t = 0.0 if t < 0 else (1.0 if t > 1 else t)
                    else:
                        t = 0.0
                    ex = x - (x1 + t * dx)
                    ey = y - (y1 + t * dy)
                    dd = math.sqrt(ex * ex + ey * ey)
                    if dd < bd:
                        bd = dd
                        best = (dd, owner, k, t)
        return best



def _onto_chords(p, K):
    """Move the points between consecutive kept vertices K onto the straight chord
    between them, at the same fraction of the path length (point count unchanged)."""
    q = p.copy()
    s = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(p, axis=0).T))])
    for a, b in zip(K[:-1], K[1:]):
        if b <= a + 1 or s[b] <= s[a]:
            continue
        f = (s[a + 1:b] - s[a]) / (s[b] - s[a])
        q[a + 1:b] = p[a] + f[:, None] * (p[b] - p[a])
    return q



def _turn(u, w):
    """Signed change of direction from unit vector u to unit vector w (radians)."""
    return math.atan2(u[0] * w[1] - u[1] * w[0], u[0] * w[0] + u[1] * w[1])


def _unit(v):
    n = float(np.hypot(*v))
    return v / n if n > 1e-12 else None


def _bend_path(A, B, u_in, u_out, total, max_turn):
    """A path from A (arriving along u_in) to B (leaving along u_out) whose every
    bend turns by at most max_turn: a straight cut, else a polygonal arc with
    evenly spaced headings and chord lengths solved to end exactly at B."""
    d = B - A
    ud = _unit(d)
    if ud is None:
        return None
    if abs(_turn(u_in, ud)) <= max_turn + 1e-6 and abs(_turn(ud, u_out)) <= max_turn + 1e-6:
        return np.array([A, B])
    th0 = math.atan2(u_in[1], u_in[0])
    nb0 = max(2, int(math.ceil(abs(total) / max_turn - 1e-9)))
    for nb in range(nb0, nb0 + 4):
        phi = total / nb
        U = np.array([[math.cos(th0 + j * phi), math.sin(th0 + j * phi)]
                      for j in range(1, nb)])
        M = U.T                                        # 2 x (nb - 1)
        k = float(np.hypot(*d)) / max(float(np.hypot(*U.sum(axis=0))), 1e-12)
        c0 = np.full(nb - 1, k)
        c = c0 + np.linalg.pinv(M) @ (d - M @ c0)
        if np.hypot(*(M @ c - d)) > 1e-6 * max(1.0, float(np.hypot(*d))):
            continue
        if np.all(c > 1e-9):
            pts = [A]
            for cj, uj in zip(c, U):
                pts.append(pts[-1] + cj * uj)
            pts[-1] = B
            return np.array(pts)
    return None


def limit_bends(pts, max_turn, tol=0.0, t_max=None, t_min=0.0, straighten=None,
                trim_start=0.0, passes=6):
    """Round every bend sharper than allowed, keeping the number of points.

    pts: dense polyline (N x 2); only its last point is held in place, and its
    first point unless trim_start > 0, so the caller moves anything attached
    to its other points (by index) with them.
    First the line is straightened: its points are put on the chords of its
    Douglas-Peucker simplification at tolerance `straighten`, which removes
    grid staircases. A bend is a vertex of the simplification (tolerance tol)
    where the direction changes by more than max_turn (radians). The corner is
    cut at distance T back along each leg, T = min(t_max, 0.45 x the leg), and
    the points in between are moved, in order, onto a straight cut or an
    evenly bent polygonal arc (see _bend_path). Two sharp bends joined by a
    leg too short to cut are done together, from the far side of each. A sharp
    bend at the end of a first leg shorter than trim_start is removed by
    trimming that leg off (the free upstream end of a lateral). A bend whose
    legs are too short (T < t_min) is left as it is.

    Returns (new points, number of bends rounded, number left sharper than max_turn).
    """
    p = np.array(pts, dtype=float)
    n = len(p)
    if n < 3 or max_turn <= 0:
        return p, 0, 0
    if straighten:
        _, K = simplify(p, straighten, return_index=True)
        p = _onto_chords(p, [int(k) for k in K])
    rounded = left = 0
    tmax = float("inf") if t_max is None else t_max
    for _ in range(passes):
        s = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(p, axis=0).T))])
        _, K = simplify(p, tol, return_index=True)
        K = [int(k) for k in K]
        nk = len(K)
        U = [_unit(p[K[j + 1]] - p[K[j]]) for j in range(nk - 1)]
        turns = [0.0] + [(_turn(U[j - 1], U[j]) if U[j - 1] is not None and U[j] is not None
                          else 0.0) for j in range(1, nk - 1)] + [0.0]
        leg = [s[K[j + 1]] - s[K[j]] for j in range(nk - 1)]
        q = p.copy()
        changed = False
        left = 0
        last_hi = -1.0
        m = 1
        while m < nk - 1:
            if abs(turns[m]) <= max_turn + 1e-6:
                m += 1
                continue
            v = K[m]
            # trim a short first leg at a free start
            if m == 1 and trim_start > 0 and leg[0] < trim_start and s[v] > last_hi:
                # the trimmed points sit in order between the bend and the next point
                f = np.linspace(0.0, 0.5, v + 1)
                q[:v + 1] = p[v] + f[:, None] * (p[v + 1] - p[v])
                last_hi = s[v]
                rounded += 1
                changed = True
                m += 1
                continue
            T_in, T_out = min(tmax, 0.45 * leg[m - 1]), min(tmax, 0.45 * leg[m])
            w, total, u_out = m, turns[m], U[m]
            if min(T_in, T_out) < t_min and m + 1 < nk - 1 and leg[m] <= leg[m - 1]:
                # join with the next bend across the short leg
                w = m + 1
                total = turns[m] + turns[w]
                T_out = min(tmax, 0.45 * leg[w])
                u_out = U[w]
            T = min(T_in, T_out)
            if w == m:
                T_in = T_out = T
            lo, hi = s[v] - T_in, s[K[w]] + T_out
            if min(T_in, T_out) < t_min or lo <= last_hi:
                left += 1
                m = w + 1
                continue
            A = p[v] - T_in * U[m - 1]
            B = p[K[w]] + T_out * u_out
            path = _bend_path(A, B, U[m - 1], u_out, total, max_turn)
            idx = [i for i in range(K[m - 1] + 1, K[w + 1]) if lo < s[i] < hi]
            if path is None or len(idx) < 2:
                left += 1
                m = w + 1
                continue
            sa = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(path, axis=0).T))])
            for i in idx:
                f = (s[i] - lo) / (hi - lo) * sa[-1]
                q[i] = [np.interp(f, sa, path[:, 0]), np.interp(f, sa, path[:, 1])]
            q[idx[0]], q[idx[-1]] = A, B          # the cut's ends are points of the line
            last_hi = hi
            rounded += 1
            changed = True
            m = w + 1
        p = q
        if not changed:
            break
    return p, rounded, left


def densify(pts, step):
    """Add points so that no segment is longer than step.

    Returns (new points, index map): old point i is new point map[i].
    """
    p = np.asarray(pts, dtype=float)
    out, idx = [p[0]], [0]
    for i in range(1, len(p)):
        L = float(np.hypot(*(p[i] - p[i - 1])))
        k = int(math.ceil(L / step - 1e-9)) if step > 0 else 1
        for j in range(1, k):
            out.append(p[i - 1] + (p[i] - p[i - 1]) * (j / k))
        out.append(p[i])
        idx.append(len(out) - 1)
    return np.array(out), np.array(idx, dtype=int)


def refine_turns(pts, K, max_turn, min_span=0.0, max_iter=10000):
    """Add back points to a simplification (kept indices K of pts) until no kept
    vertex turns by more than max_turn, or no point is left to add next to it.
    Each step adds, in the longer span next to the offending vertex, the point
    farthest from that span's chord. Spans shorter than min_span are not split,
    so a bend that could not be rounded is not broken into noise."""
    p = np.asarray(pts, dtype=float)
    K = sorted(int(k) for k in K)
    for _ in range(max_iter):
        changed = False
        j = 1
        while j < len(K) - 1:
            a, v, b = K[j - 1], K[j], K[j + 1]
            u, w = _unit(p[v] - p[a]), _unit(p[b] - p[v])
            if u is None or w is None or abs(_turn(u, w)) <= max_turn + 1e-9:
                j += 1
                continue
            best = None
            for lo, hi in sorted(((a, v), (v, b)), key=lambda r: -(r[1] - r[0])):
                d = p[hi] - p[lo]
                L = float(np.hypot(*d))
                if hi - lo < 2 or L < min_span:
                    continue
                seg = p[lo + 1:hi] - p[lo]
                dev = (np.abs(seg[:, 0] * d[1] - seg[:, 1] * d[0]) / L if L > 0
                       else np.hypot(seg[:, 0], seg[:, 1]))
                best = lo + 1 + int(np.argmax(dev))
                break
            if best is None:
                j += 1
                continue
            K.insert(j if best < v else j + 1, best)
            changed = True
        if not changed:
            break
    return np.array(K, dtype=int)
