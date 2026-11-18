# -*- coding: utf-8 -*-
"""
Pure-numpy hydrology core for the Drainage Outlet plugin.

No QGIS / GDAL imports here so the logic can be unit-tested on its own.

Grid conventions
----------------
* Arrays are 2-D (rows, cols), row 0 = north.
* Cells are addressed by flat index  i = r * ncols + c.
* ``rcv[i]`` is the flat index of the D8 receiver of cell i, or -1 when the
  cell drains off the valid area (watershed outlet / DEM edge / nodata).
"""
import heapq
import math

import numpy as np

# D8 neighbour offsets (dr, dc) and their distance factor
_OFFSETS = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]
_DIST = [1.0, math.sqrt(2), 1.0, math.sqrt(2), 1.0, math.sqrt(2), 1.0, math.sqrt(2)]


def _shift(a, dr, dc, fill):
    """Return array b where b[r, c] = a[r + dr, c + dc] (fill outside)."""
    out = np.full_like(a, fill)
    nr, nc = a.shape
    r0, r1 = max(0, -dr), min(nr, nr - dr)
    c0, c1 = max(0, -dc), min(nc, nc - dc)
    out[r0:r1, c0:c1] = a[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
    return out


def boundary_cells(valid):
    """Valid cells that touch the grid edge or an invalid cell (8-connected)."""
    edge = np.zeros_like(valid)
    for dr, dc in _OFFSETS:
        edge |= ~_shift(valid, dr, dc, False)
    return edge & valid


def fill_depressions(dem, valid, epsilon=1e-4, feedback=None):
    """
    Priority-flood depression filling with an epsilon gradient across flats
    (Barnes et al., 2014). Every valid non-boundary cell ends up with at least
    one strictly lower neighbour, so D8 routing never stalls in pits or flats.

    ``dem`` float array, ``valid`` bool array. Returns float64 filled DEM
    (invalid cells = +inf).
    """
    nr, nc = dem.shape
    z = np.where(valid, dem.astype(np.float64), np.inf)
    zf = z.ravel().tolist()                       # python list = fast scalar access
    vf = valid.ravel().tolist()
    closed = [not v for v in vf]

    heap = []
    seeds = np.flatnonzero(boundary_cells(valid).ravel())
    for i in seeds.tolist():
        heap.append((zf[i], i))
        closed[i] = True
    heapq.heapify(heap)

    total = int(valid.sum()) or 1
    done = 0
    step = max(1, total // 50)
    offs = [(dr, dc) for dr, dc in _OFFSETS]
    while heap:
        zc, i = heapq.heappop(heap)
        r, c = divmod(i, nc)
        for dr, dc in offs:
            rr, cc = r + dr, c + dc
            if rr < 0 or rr >= nr or cc < 0 or cc >= nc:
                continue
            j = rr * nc + cc
            if closed[j]:
                continue
            closed[j] = True
            if zf[j] <= zc:
                zf[j] = zc + epsilon
            heapq.heappush(heap, (zf[j], j))
        done += 1
        if feedback is not None and done % step == 0:
            if feedback(done / total):
                raise RuntimeError("Cancelled")
    return np.array(zf, dtype=np.float64).reshape(nr, nc)


def flow_directions(zfill, valid, cellsize=1.0):
    """
    D8 steepest-descent receivers. Returns int64 flat-index array ``rcv``
    (-1 = drains off the valid area).
    """
    nr, nc = zfill.shape
    zv = np.where(valid, zfill, np.inf)
    best = np.zeros(zfill.shape, dtype=np.float64)   # best slope so far (>0 only)
    best_k = np.full(zfill.shape, -1, dtype=np.int64)
    for k, (dr, dc) in enumerate(_OFFSETS):
        zn = _shift(zv, dr, dc, np.inf)
        with np.errstate(invalid="ignore"):
            slope = (zv - zn) / (_DIST[k] * cellsize)
        slope = np.where(np.isfinite(slope), slope, -np.inf)
        better = slope > best
        best = np.where(better, slope, best)
        best_k = np.where(better, k, best_k)

    rows, cols = np.indices(zfill.shape)
    dr = np.array([o[0] for o in _OFFSETS] + [0])[best_k]
    dc = np.array([o[1] for o in _OFFSETS] + [0])[best_k]
    rcv = (rows + dr) * nc + (cols + dc)
    rcv = np.where((best_k >= 0) & valid, rcv, -1)
    return rcv.ravel().astype(np.int64)


def flow_accumulation(zfill, valid, rcv, weights=None):
    """
    Weighted flow accumulation (count of upstream cells incl. itself when
    weights is None). Uses descending filled elevation as topological order,
    which is valid because every receiver is strictly lower.
    """
    w = np.ones(zfill.size) if weights is None else weights.ravel().astype(np.float64)
    w = np.where(valid.ravel(), w, 0.0)
    idx = np.flatnonzero(valid.ravel())
    order = idx[np.argsort(-zfill.ravel()[idx], kind="stable")]
    acc = w.tolist()
    r = rcv.tolist()
    for i in order.tolist():
        j = r[i]
        if j >= 0:
            acc[j] += acc[i]
    return np.array(acc).reshape(zfill.shape)


def field_outlets(field, stream, zfill, rcv):
    """
    Find where water leaves the field.

    A field cell is an *outlet* when its receiver is outside the field, is a
    stream cell, or is -1. Accumulation is computed only over field cells, so
    each outlet carries the number of field cells that drain through it.

    Returns list of (flat_index, n_cells) sorted by n_cells desc.
    """
    src = (field & ~stream).ravel()
    idx = np.flatnonzero(src)
    if idx.size == 0:
        return []
    srcl = src.tolist()
    order = idx[np.argsort(-zfill.ravel()[idx], kind="stable")]
    acc = {}
    r = rcv.tolist()
    outlets = {}
    for i in order.tolist():
        a = acc.get(i, 0) + 1
        j = r[i]
        if j >= 0 and srcl[j]:
            acc[j] = acc.get(j, 0) + a
        else:
            outlets[i] = a
    return sorted(outlets.items(), key=lambda t: -t[1])


def trace_path(start, rcv, stream, max_steps=10_000_000):
    """
    Follow D8 receivers from ``start`` until a stream cell is reached or the
    flow leaves the grid. Returns (cells, reason) where reason is
    'stream' or 'edge'.
    """
    s = stream.ravel()
    cells = [start]
    i = start
    if s[i]:
        return cells, "stream"
    for _ in range(max_steps):
        j = int(rcv[i])
        if j < 0:
            return cells, "edge"
        cells.append(j)
        if s[j]:
            return cells, "stream"
        i = j
    return cells, "edge"


def field_final_outlets(field, stream, rcv):
    """
    Natural outlet(s) of a field on its boundary.

    Every field cell is followed downstream until the flow reaches a stream
    cell *outside* the field or leaves the grid. The last field cell on that
    route is where the water leaves the field for good: flow that exits and
    re-enters a concave field is not counted twice, and streams inside the
    field (or stream buffers overlapping its edge) do not stop the routing.

    Returns list of (outlet_flat_index, n_field_cells_draining_to_it),
    largest first.
    """
    f = field.ravel().tolist()
    stop_stream = (stream & ~field).ravel().tolist()
    r = rcv.tolist()
    last = {}                      # cell -> last field cell downstream (incl. itself) or -1
    counts = {}
    for start in np.flatnonzero(field.ravel()).tolist():
        path = []
        i = start
        while i >= 0 and i not in last and not stop_stream[i]:
            path.append(i)
            i = r[i]
        after = last[i] if (i >= 0 and i in last) else -1
        for c in reversed(path):
            if after == -1 and f[c]:
                after = c
            last[c] = after
        o = last[start]
        if o >= 0:
            counts[o] = counts.get(o, 0) + 1
    return sorted(counts.items(), key=lambda t: -t[1])
