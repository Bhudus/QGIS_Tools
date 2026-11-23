"""Nearest-pipe allocation on a raster grid (jump flooding, numpy only)."""
import numpy as np


def nearest_sample(grid, mask, sx, sy):
    """For each cell in `mask`, the index of the nearest sample point and its distance.

    grid: object with x0, y0 (upper-left corner), cell, nrows, ncols.
    Returns (idx, dist) arrays of the grid shape; idx is -1 outside the mask.
    Uses the jump flooding algorithm (JFA + one extra pass of step 1), which gives
    the exact nearest point for all but a negligible number of cells.
    """
    nr, nc = grid.nrows, grid.ncols
    best = np.full((nr, nc), -1, dtype=np.int64)
    sx = np.asarray(sx, dtype=float)
    sy = np.asarray(sy, dtype=float)
    j = np.floor((sx - grid.x0) / grid.cell).astype(np.int64)
    i = np.floor((grid.y0 - sy) / grid.cell).astype(np.int64)
    inside = (i >= 0) & (i < nr) & (j >= 0) & (j < nc)
    idx = np.nonzero(inside)[0]
    if len(idx) == 0:
        return best, np.full((nr, nc), np.inf)
    xc = grid.x0 + (np.arange(nc) + 0.5) * grid.cell
    yc = grid.y0 - (np.arange(nr) + 0.5) * grid.cell
    X, Y = np.meshgrid(xc, yc)
    # seed cells: keep the sample nearest the cell centre
    d0 = np.hypot(sx[idx] - X[i[idx], j[idx]], sy[idx] - Y[i[idx], j[idx]])
    order = np.argsort(-d0)                 # nearest written last wins
    best[i[idx][order], j[idx][order]] = idx[order]

    def dist(b):
        out = np.full(b.shape, np.inf)
        ok = b >= 0
        out[ok] = np.hypot(sx[b[ok]] - X[ok], sy[b[ok]] - Y[ok])
        return out

    bd = dist(best)
    step = 1
    while step * 2 < max(nr, nc):
        step *= 2
    steps = []
    while step >= 1:
        steps.append(step)
        step //= 2
    steps.append(1)
    for k in steps:
        for di in (-k, 0, k):
            for dj in (-k, 0, k):
                if di == 0 and dj == 0:
                    continue
                cand = np.full((nr, nc), -1, dtype=np.int64)
                r0, r1 = max(0, di), nr + min(0, di)
                c0, c1 = max(0, dj), nc + min(0, dj)
                if r1 <= r0 or c1 <= c0:
                    continue
                cand[r0 - di:r1 - di, c0 - dj:c1 - dj] = best[r0:r1, c0:c1]
                cd = dist(cand)
                better = cd < bd
                best[better] = cand[better]
                bd[better] = cd[better]
    best[~mask] = -1
    bd[~mask] = np.inf
    return best, bd


class Grid:
    """Regular raster grid. (x0, y0) is the upper-left corner; rows run south."""

    def __init__(self, x0, y0, cell, nrows, ncols):
        self.x0, self.y0, self.cell = float(x0), float(y0), float(cell)
        self.nrows, self.ncols = int(nrows), int(ncols)

    @property
    def shape(self):
        return (self.nrows, self.ncols)

    def centers(self):
        xs = self.x0 + (np.arange(self.ncols) + 0.5) * self.cell
        ys = self.y0 - (np.arange(self.nrows) + 0.5) * self.cell
        return np.meshgrid(xs, ys)
