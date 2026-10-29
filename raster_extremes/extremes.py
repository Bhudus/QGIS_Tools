"""Pure-numpy core: tracks min/max values and their cell positions
across a raster read in horizontal strips. No QGIS imports, so it can be
unit-tested on its own."""

import numpy as np


class ExtremeTracker:
    """Accumulates the global min and max of a raster, strip by strip,
    keeping (row, col) of every cell that holds the extreme value
    (up to ``max_cells`` positions per extreme)."""

    def __init__(self, max_cells=1000):
        self.max_cells = max_cells
        self.valid_count = 0
        self.min_value = None
        self.max_value = None
        self.min_cells = []
        self.max_cells_list = []
        self.min_count = 0
        self.max_count = 0

    def update(self, arr, row_offset, invalid=None):
        """arr: 2-D array for a strip; row_offset: index of its first row;
        invalid: optional boolean array (True = NoData)."""
        a = np.asarray(arr)
        if np.issubdtype(a.dtype, np.floating):
            valid = np.isfinite(a)
        else:
            valid = np.ones(a.shape, dtype=bool)
        if invalid is not None:
            valid &= ~invalid

        n = int(valid.sum())
        if n == 0:
            return
        self.valid_count += n

        vals = a[valid]
        lo = vals.min().item()
        hi = vals.max().item()

        # --- minimum ---
        if self.min_value is None or lo < self.min_value:
            self.min_value, self.min_cells, self.min_count = lo, [], 0
        if lo == self.min_value:
            r, c = np.nonzero(valid & (a == a.dtype.type(lo)))
            self.min_count += len(r)
            room = self.max_cells - len(self.min_cells)
            if room > 0:
                self.min_cells.extend(
                    (int(rr) + row_offset, int(cc)) for rr, cc in zip(r[:room], c[:room]))

        # --- maximum ---
        if self.max_value is None or hi > self.max_value:
            self.max_value, self.max_cells_list, self.max_count = hi, [], 0
        if hi == self.max_value:
            r, c = np.nonzero(valid & (a == a.dtype.type(hi)))
            self.max_count += len(r)
            room = self.max_cells - len(self.max_cells_list)
            if room > 0:
                self.max_cells_list.extend(
                    (int(rr) + row_offset, int(cc)) for rr, cc in zip(r[:room], c[:room]))


def nodata_mask(arr, nodata_value=None, ranges=()):
    """Boolean mask of NoData cells. ``ranges`` is a list of
    (min, max, include_min, include_max) tuples (user-defined NoData)."""
    a = np.asarray(arr)
    mask = np.zeros(a.shape, dtype=bool)
    if nodata_value is not None and not (isinstance(nodata_value, float) and np.isnan(nodata_value)):
        try:
            nd = a.dtype.type(nodata_value)
            mask |= a == nd
        except (OverflowError, ValueError):
            pass  # NoData value not representable in this dtype
    for lo, hi, inc_lo, inc_hi in ranges:
        m = np.ones(a.shape, dtype=bool)
        if lo is not None and not np.isnan(lo):
            m &= (a >= lo) if inc_lo else (a > lo)
        if hi is not None and not np.isnan(hi):
            m &= (a <= hi) if inc_hi else (a < hi)
        mask |= m
    return mask


def cell_center(extent_xmin, extent_ymax, xres, yres, row, col):
    """Map coordinates of the centre of cell (row, col) for a north-up raster."""
    return (extent_xmin + (col + 0.5) * xres,
            extent_ymax - (row + 0.5) * yres)
