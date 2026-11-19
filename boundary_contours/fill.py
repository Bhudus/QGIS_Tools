# -*- coding: utf-8 -*-
"""
Depression (sink) filling with the Priority-Flood + FIFO algorithm.

Barnes, Lehman & Mulla (2014), "Priority-Flood: An optimal depression-filling
and watershed-labeling algorithm for digital elevation models",
Computers & Geosciences 62:117-127.

Pits are filled flat to their spill elevation (no artificial gradient), which
is what you want for contouring: a filled pit produces no closed "bullseye"
contours. Cells on the raster edge and cells next to NoData act as outlets.

Pure Python + numpy, so it needs no extra QGIS providers. Speed is roughly
0.5 to 2 million cells per second, depending on the terrain.
"""

import heapq
from collections import deque

import numpy as np


class FillCanceled(Exception):
    pass


def fill_depressions(dem, nodata_mask=None, progress=None, is_canceled=None):
    """
    Return a filled copy of `dem` (2D array). `nodata_mask` is a boolean
    array, True where the DEM has no data; those cells stay unchanged.

    `progress(fraction)` and `is_canceled() -> bool` are optional callbacks.
    """
    dem = np.asarray(dem, dtype=np.float64)
    rows, cols = dem.shape
    if nodata_mask is None:
        nodata_mask = ~np.isfinite(dem)
    else:
        nodata_mask = np.asarray(nodata_mask, dtype=bool) | ~np.isfinite(dem)

    W = cols + 2
    # Padded grids: a ring of "closed" cells around the DEM means neighbour
    # lookups never need bounds checks.
    z_p = np.zeros((rows + 2, W), dtype=np.float64)
    z_p[1:-1, 1:-1] = dem
    blocked = np.ones((rows + 2, W), dtype=bool)
    blocked[1:-1, 1:-1] = nodata_mask

    # Seeds: valid cells that touch the edge or a NoData cell (8-connected)
    near_blocked = np.zeros_like(blocked)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            near_blocked[1:-1, 1:-1] |= blocked[1 + dr:rows + 1 + dr, 1 + dc:cols + 1 + dc]
    seeds = near_blocked & ~blocked

    z = z_p.ravel().tolist()               # Python list: fast scalar access
    closed = bytearray(blocked.ravel().astype(np.uint8).tobytes())
    seed_idx = np.flatnonzero(seeds.ravel())
    for i in seed_idx.tolist():
        closed[i] = 1
    heap = [(z[i], i) for i in seed_idx.tolist()]
    heapq.heapify(heap)
    pit = deque()

    offsets = (-W - 1, -W, -W + 1, -1, 1, W - 1, W, W + 1)
    total = int((~nodata_mask).sum()) or 1
    done = 0
    report_every = 100000
    next_report = report_every

    heappop, heappush = heapq.heappop, heapq.heappush
    pit_pop, pit_push = pit.popleft, pit.append

    while heap or pit:
        if pit:
            c = pit_pop()
            zc = z[c]
        else:
            zc, c = heappop(heap)
        done += 1
        if done >= next_report:
            next_report += report_every
            if is_canceled is not None and is_canceled():
                raise FillCanceled()
            if progress is not None:
                progress(done / total)
        for o in offsets:
            n = c + o
            if closed[n]:
                continue
            closed[n] = 1
            zn = z[n]
            if zn <= zc:
                z[n] = zc          # raise to spill level
                pit_push(n)
            else:
                heappush(heap, (zn, n))

    out = np.asarray(z, dtype=np.float64).reshape(rows + 2, W)[1:-1, 1:-1]
    out = out.copy()
    out[nodata_mask] = dem[nodata_mask]
    return out
