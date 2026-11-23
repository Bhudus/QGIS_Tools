"""Bilinear sampling of a grid (numpy only)."""
import numpy as np


def bilinear(grid, z, x, y):
    """Bilinear sample of grid array z at points (NaN outside or on NaN cells)."""
    x = np.atleast_1d(np.asarray(x, dtype=float))
    y = np.atleast_1d(np.asarray(y, dtype=float))
    fj = (x - grid.x0) / grid.cell - 0.5
    fi = (grid.y0 - y) / grid.cell - 0.5
    j0 = np.clip(np.floor(fj).astype(int), 0, grid.ncols - 1)
    i0 = np.clip(np.floor(fi).astype(int), 0, grid.nrows - 1)
    j1 = np.clip(j0 + 1, 0, grid.ncols - 1)
    i1 = np.clip(i0 + 1, 0, grid.nrows - 1)
    tj = np.clip(fj - j0, 0, 1)
    ti = np.clip(fi - i0, 0, 1)
    a, b, c, d = z[i0, j0], z[i0, j1], z[i1, j0], z[i1, j1]
    vals = np.stack([a, b, c, d])
    w = np.stack([(1 - ti) * (1 - tj), (1 - ti) * tj, ti * (1 - tj), ti * tj])
    ok = np.isfinite(vals)
    w = np.where(ok, w, 0.0)
    ws = w.sum(0)
    out = np.where(ws > 0, (np.where(ok, vals, 0.0) * w).sum(0) / np.maximum(ws, 1e-12),
                   np.nan)
    outside = (fj < -0.5) | (fj > grid.ncols - 0.5) | (fi < -0.5) | (fi > grid.nrows - 0.5)
    out[outside] = np.nan
    return out
