# -*- coding: utf-8 -*-
"""Pure NumPy terrain routines for depressions and laylines (no SAGA, no SciPy).

* depression depth   = filled DEM - unfilled DEM
* depression regions = 8-connected groups of cells deeper than a threshold
* laylines           = D8 flow channels: cells whose contributing area exceeds a
                       threshold, linked cell-to-cell downstream and split into
                       segments at junctions, each with its Strahler order.

Flow is routed on a *routing surface*: the filled DEM, re-filled with a tiny
gradient so every cell has a strictly lower neighbour (flats and filled
depressions then drain toward their spill point). See sinkfill.py.
"""

import math
from collections import deque

import numpy as np

# D8 neighbour offsets (row, col) and the distance factor of each
_D8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
_DIST = tuple(math.sqrt(2.0) if dr and dc else 1.0 for dr, dc in _D8)


def depression_depth(dem, filled, nodata_mask):
    """filled - dem, 0 where NoData; small negative float noise clamped to 0."""
    d = np.where(nodata_mask, 0.0, filled.astype(np.float64) - dem.astype(np.float64))
    d[d < 0] = 0.0
    return d


def d8_receivers(zr, nodata_mask):
    """Steepest-descent receiver of each cell as a flat index, -1 = outlet / NoData.

    ``zr`` should be a routing surface in which every non-outlet cell has a
    strictly lower neighbour (e.g. a Wang & Liu fill with a small gradient).
    Distances are in cell units, which does not change which neighbour is steepest.
    """
    rows, cols = zr.shape
    zp = np.full((rows + 2, cols + 2), np.inf)
    zp[1:-1, 1:-1] = np.where(nodata_mask, np.inf, zr)
    best = np.zeros((rows, cols))
    rec = np.full((rows, cols), -1, dtype=np.int64)
    rr, cc = np.mgrid[0:rows, 0:cols]
    for (dr, dc), dist in zip(_D8, _DIST):
        zn = zp[1 + dr:rows + 1 + dr, 1 + dc:cols + 1 + dc]
        with np.errstate(invalid="ignore"):
            s = (zr - zn) / dist                       # -inf where neighbour is NoData
        better = s > best
        best = np.where(better, s, best)
        rec = np.where(better, (rr + dr) * cols + (cc + dc), rec)
    rec[nodata_mask] = -1
    return rec.ravel()


def accumulate(zr, nodata_mask, rec, channel_cells, is_canceled=None):
    """Flow accumulation (in cells, each cell counts itself) and Strahler order.

    Cells are visited from high to low on the routing surface, so every cell's
    upstream cells are done before it. Strahler order is computed only for
    cells with accumulation >= ``channel_cells`` (0 elsewhere).
    Returns (acc, strahler) as flat arrays.
    """
    n = zr.size
    valid = ~nodata_mask.ravel()
    idx = np.flatnonzero(valid)
    order = idx[np.argsort(-zr.ravel()[idx], kind="stable")]
    acc = np.zeros(n, dtype=np.float64)
    acc[idx] = 1.0
    acc_l = acc.tolist()
    rec_l = rec.tolist()
    strahler = [0] * n
    maxo = [0] * n
    cnt = [0] * n
    thr = float(channel_cells)
    k = 0
    for c in order.tolist():
        k += 1
        if is_canceled is not None and not k & 0xFFFF and is_canceled():
            raise RuntimeError("Canceled.")
        a = acc_l[c]
        r = rec_l[c]
        if a >= thr:
            m = maxo[c]
            s = 1 if m == 0 else (m + 1 if cnt[c] >= 2 else m)
            strahler[c] = s
            if r >= 0:
                if s > maxo[r]:
                    maxo[r] = s
                    cnt[r] = 1
                elif s == maxo[r]:
                    cnt[r] += 1
        if r >= 0:
            acc_l[r] += a
    return np.asarray(acc_l), np.asarray(strahler, dtype=np.int32)


def channel_segments(rec, acc, strahler, channel_cells):
    """Split the channel network into segments between heads, junctions and outlets.

    Returns a list of dicts: cells (flat indices, upstream -> downstream; a
    segment ending at a junction includes the junction cell so lines connect),
    strahler, acc_top and acc_bottom (cells).
    """
    chan = acc >= channel_cells
    ci = np.flatnonzero(chan)
    up = np.zeros(acc.size, dtype=np.int32)
    r = rec[ci]
    ok = r >= 0
    np.add.at(up, r[ok][chan[r[ok]]], 1)
    starts = ci[(up[ci] == 0) | (up[ci] >= 2)]
    rec_l = rec.tolist()
    segs = []
    for s in starts.tolist():
        cells = [s]
        c = s
        while True:
            r = rec_l[c]
            if r < 0 or not chan[r]:
                break
            cells.append(r)
            if up[r] >= 2:
                break
            c = r
        last = cells[-2] if (len(cells) > 1 and up[cells[-1]] >= 2) else cells[-1]
        if len(cells) < 2:
            continue                                   # single-cell stub at an outlet
        segs.append({"cells": cells, "strahler": int(strahler[s]),
                     "acc_top": float(acc[s]), "acc_bottom": float(acc[last])})
    return segs


def label_regions(mask):
    """8-connected labels of a boolean mask. Returns (labels int32, count)."""
    rows, cols = mask.shape
    lab = np.zeros((rows, cols), dtype=np.int32)
    n = 0
    m = mask.tolist()
    lab_l = lab.tolist()
    for r0, c0 in zip(*np.nonzero(mask)):
        r0, c0 = int(r0), int(c0)
        if lab_l[r0][c0]:
            continue
        n += 1
        lab_l[r0][c0] = n
        q = deque([(r0, c0)])
        while q:
            r, c = q.popleft()
            for dr, dc in _D8:
                rr, cc = r + dr, c + dc
                if 0 <= rr < rows and 0 <= cc < cols and m[rr][cc] and not lab_l[rr][cc]:
                    lab_l[rr][cc] = n
                    q.append((rr, cc))
    return np.asarray(lab_l, dtype=np.int32), n


def region_stats(labels, n, depth, filled, cell_area):
    """Per-region cell count, max/mean depth, storage volume and pond (spill) level."""
    flat = labels.ravel()
    sel = flat > 0
    lab = flat[sel]
    d = depth.ravel()[sel]
    f = filled.ravel()[sel].astype(np.float64)
    cells = np.bincount(lab, minlength=n + 1)
    dsum = np.bincount(lab, weights=d, minlength=n + 1)
    dmax = np.zeros(n + 1)
    np.maximum.at(dmax, lab, d)
    pond = np.full(n + 1, -np.inf)
    np.maximum.at(pond, lab, f)
    out = []
    for k in range(1, n + 1):
        if cells[k] == 0:
            continue
        out.append({"id": k, "cells": int(cells[k]), "max_depth": float(dmax[k]),
                    "mean_depth": float(dsum[k] / cells[k]),
                    "volume": float(dsum[k] * cell_area), "pond_elev": float(pond[k])})
    return out
