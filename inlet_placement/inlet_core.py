"""Surface and blind inlet placement (pure Python + numpy, no QGIS).

For every depression (a polygon over the DEM):

* the pond: its lowest cell (where the inlet goes), the pond level (the
  sink-filled DEM there), its depth and stored volume;
* its direct drainage area: the cells whose runoff reaches the depression,
  found by routing over the DEM with every depression an outlet (runoff from
  depressions that fill and spill into it is not added);
* the design flow: the pond drained within a drawdown time, or the drainage
  area at a surface-water coefficient, whichever is larger;
* the inlet: a surface inlet (riser) with an orifice plate sized to pass the
  design flow at full pond depth, or a blind inlet (a gravel and sand filter)
  with the filter area to pass it;
* the lead pipe: straight to the nearest point on a tile line, its invert set
  from the tile there up at a grade between the minimum and maximum slopes,
  and its size from Manning's equation flowing full;
* the tile check: down from where it joins, each pipe's design flow plus the
  inlet flows above it, against its full-flow capacity.

Units: feet, inches, cubic feet per second, acres (Routine K's units).
"""
import math

import numpy as np

from . import sinkfill

NOMINAL_SIZES = [3, 4, 5, 6, 8, 10, 12, 15, 18, 21, 24, 30, 36, 42]
G = 32.174                                        # ft/s2
CU_FT_PER_CU_YD = 27.0
SQ_FT_PER_ACRE = 43560.0
MATERIALS = ["Single Wall", "Smooth Wall", "Clay or Concrete"]


# ------------------------------------------------------------------ grid
class Grid:
    """Cells of size `cell` (ft); x0 the left edge, y0 the top edge."""

    def __init__(self, x0, y0, cell, nrows, ncols):
        self.x0, self.y0, self.cell = float(x0), float(y0), float(cell)
        self.nrows, self.ncols = int(nrows), int(ncols)
        self.shape = (self.nrows, self.ncols)

    def xs(self):
        return self.x0 + (np.arange(self.ncols) + 0.5) * self.cell

    def ys(self):
        return self.y0 - (np.arange(self.nrows) + 0.5) * self.cell

    def centre(self, i, j):
        return self.x0 + (j + 0.5) * self.cell, self.y0 - (i + 0.5) * self.cell

    def index(self, x, y):
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


# ------------------------------------------------------------- ponds
def label_regions(mask):
    """8-connected regions of a boolean grid: (labels, count)."""
    rows, cols = mask.shape
    lab = np.zeros(mask.shape, dtype=np.int64)
    flat = mask.ravel()
    lf = lab.ravel()
    n = 0
    for start in np.flatnonzero(flat):
        if lf[start]:
            continue
        n += 1
        lf[start] = n
        stack = [int(start)]
        while stack:
            c = stack.pop()
            i, j = divmod(c, cols)
            for di in (-1, 0, 1):
                ii = i + di
                if ii < 0 or ii >= rows:
                    continue
                for dj in (-1, 0, 1):
                    jj = j + dj
                    if (di or dj) and 0 <= jj < cols:
                        k = ii * cols + jj
                        if flat[k] and not lf[k]:
                            lf[k] = n
                            stack.append(k)
    return lab, n


def pond_stats(dem, valid, labels, n, cell, filled=None):
    """Per depression 1..n: lowest cell, pond level, depth, and the volume (cu
    ft) and area (sq ft) of the whole pond it lies in (the connected cells the
    sink fill raises, which can reach past the depression's polygon)."""
    if filled is None:
        filled = sinkfill.fill_sinks_wang_liu(np.where(valid, dem, 0.0), ~valid, cell)
    d = np.where(valid, np.clip(filled - dem, 0.0, None), 0.0)
    wet = d > 1e-6
    ponds, _ = label_regions(wet)
    out = []
    for k in range(1, n + 1):
        m = (labels == k) & valid
        if not m.any():
            out.append(None)
            continue
        z = np.where(m, dem, np.inf)
        flat = int(np.argmin(z))
        i, j = divmod(flat, dem.shape[1])
        ids = np.unique(ponds[m])
        ids = ids[ids > 0]
        pond = np.isin(ponds, ids) if ids.size else m & wet
        # the water level: the fill over the pond (the lowest cell when dry)
        level = float(filled[pond].max()) if pond.any() else float(dem[i, j])
        out.append({"i": i, "j": j, "low": float(dem[i, j]), "level": level,
                    "depth": level - float(dem[i, j]),
                    "volume": float(d[pond].sum()) * cell * cell,
                    "pond_area": float(pond.sum()) * cell * cell,
                    "poly_area": float(m.sum()) * cell * cell})
    return out


_D8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
_DIST = tuple(math.sqrt(2.0) if a and b else 1.0 for a, b in _D8)


def drainage_areas(dem, valid, labels, n, cell, progress=None):
    """Direct drainage area (sq ft) of each depression 1..n, and the label grid
    of the cells draining to each (0: to the edge of the DEM).

    Every depression is an outlet: the DEM is filled (with a slight gradient so
    flats drain) toward the DEM's edges and the depressions, and each cell
    follows its steepest descent to one of them.
    """
    dep = labels > 0
    nod = ~valid | dep
    zr = sinkfill.fill_sinks_wang_liu(np.where(nod, 0.0, dem), nod, cell, min_slope_deg=0.01,
                                      progress=progress)
    rows, cols = dem.shape
    zp = np.full((rows + 2, cols + 2), np.inf)
    zp[1:-1, 1:-1] = np.where(nod, np.inf, zr)
    lp = np.zeros((rows + 2, cols + 2), dtype=np.int64)
    lp[1:-1, 1:-1] = labels
    best = np.zeros((rows, cols))
    rec = np.full((rows, cols), -1, dtype=np.int64)
    rr, cc = np.mgrid[0:rows, 0:cols]
    # an outlet cell beside a depression drains into it (the lowest such neighbour)
    into = np.zeros((rows, cols), dtype=np.int64)
    into_z = np.full((rows, cols), np.inf)
    dp = np.full((rows + 2, cols + 2), np.inf)
    dp[1:-1, 1:-1] = np.where(dep & valid, dem, np.inf)
    for (dr, dc), dist in zip(_D8, _DIST):
        zn = zp[1 + dr:rows + 1 + dr, 1 + dc:cols + 1 + dc]
        with np.errstate(invalid="ignore"):
            s = (zr - zn) / dist
        better = s > best
        best = np.where(better, s, best)
        rec = np.where(better, (rr + dr) * cols + (cc + dc), rec)
        ln = lp[1 + dr:rows + 1 + dr, 1 + dc:cols + 1 + dc]
        dn = dp[1 + dr:rows + 1 + dr, 1 + dc:cols + 1 + dc]
        take = (ln > 0) & (dn < into_z)
        into = np.where(take, ln, into)
        into_z = np.where(take, dn, into_z)
    rec[nod] = -1
    rec = rec.ravel()
    flat_lab = np.where(dep, labels, 0).ravel().astype(np.int64)
    outlet = (rec < 0) & valid.ravel() & ~dep.ravel()
    flat_lab[outlet] = into.ravel()[outlet]
    parent = np.where(rec >= 0, rec, np.arange(rec.size))
    for _ in range(64):
        nxt = parent[parent]
        if np.array_equal(nxt, parent):
            break
        parent = nxt
    lab = flat_lab[parent].reshape(rows, cols)
    lab[~valid] = 0
    areas = np.bincount(lab.ravel(), minlength=n + 1)[1:].astype(float) * cell * cell
    return areas, lab


# ------------------------------------------------------------ hydraulics
def roughness(material, d_in):
    if material == 0:
        return 0.015 if d_in <= 8 else (0.017 if d_in <= 12 else 0.020)
    return 0.011 if material == 1 else 0.013


def full_capacity(d_in, slope, material):
    """Full-pipe Manning flow (cfs) of an inside diameter (in) at a slope."""
    if d_in <= 0 or slope <= 0:
        return 0.0
    d = d_in / 12.0
    n = roughness(material, d_in)
    return (1.49 / n) * (math.pi * d * d / 4.0) * (d / 4.0) ** (2.0 / 3.0) * math.sqrt(slope)


def size_for(q, slope, material, smallest=4):
    """Smallest nominal size (in) that carries q (cfs) flowing full at slope."""
    for s in NOMINAL_SIZES:
        if s < smallest:
            continue
        if full_capacity(s, slope, material) >= q:
            return s
    return None


def orifice_diameter(q, head, cd=0.6):
    """Orifice diameter (in) passing q (cfs) at head (ft)."""
    if q <= 0 or head <= 0:
        return None
    a = q / (cd * math.sqrt(2.0 * G * head))
    return math.sqrt(4.0 * a / math.pi) * 12.0


# ---------------------------------------------------------------- network
def nearest_on_lines(x, y, lines, eligible=None):
    """Nearest point on the lines (list of dicts with xyz (n,3) arrays).
    Returns (distance, line index, segment index, t, point, z there)."""
    best = (math.inf, None, None, None, None, None)
    for k, L in enumerate(lines):
        if eligible is not None and not eligible[k]:
            continue
        p = L["xyz"]
        if len(p) < 2:
            continue
        a, b = p[:-1, :2], p[1:, :2]
        d = b - a
        L2 = (d * d).sum(axis=1)
        L2[L2 == 0] = 1e-12
        t = np.clip(((x - a[:, 0]) * d[:, 0] + (y - a[:, 1]) * d[:, 1]) / L2, 0.0, 1.0)
        q = a + t[:, None] * d
        dist = np.hypot(q[:, 0] - x, q[:, 1] - y)
        s = int(np.argmin(dist))
        if dist[s] < best[0]:
            z = p[s, 2] + t[s] * (p[s + 1, 2] - p[s, 2])
            best = (float(dist[s]), k, s, float(t[s]), (float(q[s, 0]), float(q[s, 1])),
                    float(z))
    return best


def design_lead(inlet_xy, ground_in, join_xy, join_z, sample_ground, min_cover, min_slope,
                max_slope, step):
    """Grade of a straight lead from the inlet down to the tile.

    The invert at the inlet is put at the minimum cover where the grade that
    gives lies between the minimum and maximum slopes; flatter than the minimum,
    the lead runs at the minimum slope (the inlet end then has less cover);
    steeper than the maximum, at the maximum (the inlet end is deeper).
    Returns dict: length, slope, inv_in, inv_out, min_cover (along it), flags.
    """
    x0, y0 = inlet_xy
    x1, y1 = join_xy
    L = math.hypot(x1 - x0, y1 - y0)
    flags = []
    if L <= 0:
        return {"length": 0.0, "slope": min_slope, "inv_in": join_z, "inv_out": join_z,
                "min_cover": ground_in - join_z, "flags": ["inlet on the tile"], "pts": None}
    want = (ground_in - min_cover - join_z) / L
    if want < min_slope:
        s = min_slope
        flags.append("too little fall: lead at the minimum slope, less cover at the inlet")
    elif want > max_slope:
        s = max_slope
    else:
        s = want
    inv_in = join_z + s * L
    n = max(2, int(math.ceil(L / step)) + 1)
    t = np.linspace(0.0, 1.0, n)
    xs, ys = x0 + t * (x1 - x0), y0 + t * (y1 - y0)
    gr = sample_ground(xs, ys)
    inv = inv_in + t * (join_z - inv_in)
    cover = gr - inv
    ok = np.isfinite(cover)
    mc = float(cover[ok].min()) if ok.any() else float("nan")
    if ok.any() and mc < min_cover - 0.05:
        flags.append("cover along the lead below {:.1f} ft (least {:.2f} ft)".format(
            min_cover, mc))
    return {"length": L, "slope": s, "inv_in": inv_in, "inv_out": join_z, "min_cover": mc,
            "flags": flags, "pts": np.column_stack([xs, ys, inv, gr])}


def downstream(links_by_id, start_id, limit=100000):
    """Link ids from start_id down to the outlet (by ds_link)."""
    out, seen = [], set()
    k = start_id
    while k and k in links_by_id and k not in seen and len(out) < limit:
        out.append(k)
        seen.add(k)
        k = links_by_id[k].get("ds_link") or 0
    return out


def tile_check(links_by_id, inlet_joins):
    """Each pipe below an inlet: its design flow plus the inlet flows above it.

    links_by_id: id -> dict(flow (cfs), size (in), slope (ft/ft), material).
    inlet_joins: list of (link id, inlet flow cfs).
    Returns dict id -> dict(added, total, capacity, ratio, needs (size or None)).
    """
    added = {}
    for lid, q in inlet_joins:
        for k in downstream(links_by_id, lid):
            added[k] = added.get(k, 0.0) + q
    out = {}
    for k, qa in added.items():
        L = links_by_id[k]
        cap = full_capacity(L["size"], L["slope"], L["material"]) if L["size"] else 0.0
        tot = (L["flow"] or 0.0) + qa
        ratio = tot / cap if cap > 0 else float("inf")
        need = None
        if ratio > 1.0 and L["slope"] > 0:
            need = size_for(tot, L["slope"], L["material"], smallest=L["size"] or 4)
        out[k] = {"added": qa, "total": tot, "capacity": cap, "ratio": ratio, "needs": need}
    return out
