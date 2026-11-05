# -*- coding: utf-8 -*-
"""Sink filling after Wang & Liu (2006).

Wang, L. and Liu, H. (2006). An efficient method for identifying and filling
surface depressions in digital elevation models for hydrologic analysis and
modelling. International Journal of Geographical Information Science 20(2):
193-213.

The method grows inward from the DEM's outlets (the edge cells, and cells
next to NoData) in order of their *spill elevation*, using a priority queue.
Each newly reached neighbour gets spill = max(own elevation, spill of the cell
it was reached from + epsilon), where epsilon is the rise needed to keep a
minimum slope across the filled area (0 gives flat fills). Every cell then
drains to an outlet. Pure Python + NumPy, no SAGA needed.
"""

import heapq
import math

import numpy as np


def fill_sinks_wang_liu(z, nodata_mask, cell_size, min_slope_deg=0.0,
                        z_per_xy=1.0, progress=None, is_canceled=None):
    """Return a filled copy of ``z``.

    z           2-D float array of elevations
    nodata_mask 2-D bool array, True where there is no data
    cell_size   cell width in horizontal units
    min_slope_deg  minimum slope kept across filled areas, degrees (0 = flat)
    z_per_xy    elevation units per horizontal unit (e.g. 1 for ft/ft;
                3.2808 for z in ft on a metre grid)
    progress    optional callable(fraction 0-1)
    is_canceled optional callable() -> bool
    """
    rows, cols = z.shape
    W = cols + 2
    valid = np.zeros((rows + 2, W), dtype=bool)
    valid[1:-1, 1:-1] = ~nodata_mask
    zp = np.zeros((rows + 2, W), dtype=np.float64)
    zp[1:-1, 1:-1] = np.where(nodata_mask, 0.0, z)

    # outlets: valid cells with at least one invalid (or off-grid) neighbour
    inv = ~valid
    near_inv = np.zeros_like(valid)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr or dc:
                near_inv[1:-1, 1:-1] |= inv[1 + dr:rows + 1 + dr, 1 + dc:cols + 1 + dc]
    seeds = np.flatnonzero(valid & near_inv)

    zf = zp.ravel().tolist()                       # python lists: fast scalar access
    closed = bytearray((~valid).ravel().astype(np.uint8).tobytes())
    eps = math.tan(math.radians(max(0.0, min_slope_deg))) * cell_size * z_per_xy
    eps_d = eps * math.sqrt(2.0)
    nbrs = ((-W - 1, eps_d), (-W, eps), (-W + 1, eps_d), (-1, eps),
            (1, eps), (W - 1, eps_d), (W, eps), (W + 1, eps_d))

    heap = [(zf[i], i) for i in seeds.tolist()]
    for i in seeds.tolist():
        closed[i] = 1
    heapq.heapify(heap)

    total = int(valid.sum()) or 1
    done = 0
    pop, push = heapq.heappop, heapq.heappush
    while heap:
        s, i = pop(heap)
        done += 1
        if not done & 0xFFFF:
            if is_canceled is not None and is_canceled():
                raise RuntimeError("Canceled.")
            if progress is not None:
                progress(done / total)
        for o, e in nbrs:
            n = i + o
            if closed[n]:
                continue
            closed[n] = 1
            t = s + e
            zn = zf[n]
            if zn < t:
                zn = t
                zf[n] = t
            push(heap, (zn, n))

    out = np.asarray(zf, dtype=np.float64).reshape(rows + 2, W)[1:-1, 1:-1]
    out = out.astype(z.dtype, copy=True)
    out[nodata_mask] = z[nodata_mask]
    return out
