"""Standalone checks for the numpy solver (no QGIS needed):  python tests/test_core.py"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from mincurv_core import minimum_curvature_grid  # noqa: E402


def surface(x, y):
    return 50 + 0.3 * x - 0.1 * y + 20 * np.sin(x / 25) * np.cos(y / 35)


def bilinear(Z, x, y, cell, ymax):
    fc, fr = x / cell - 0.5, (ymax - y) / cell - 0.5
    c0, r0 = np.floor(fc).astype(int), np.floor(fr).astype(int)
    wc, wr = fc - c0, fr - r0
    return (Z[r0, c0] * (1 - wc) * (1 - wr) + Z[r0, c0 + 1] * wc * (1 - wr)
            + Z[r0 + 1, c0] * (1 - wc) * wr + Z[r0 + 1, c0 + 1] * wc * wr)


def main():
    rng = np.random.default_rng(1)
    W, H, cell = 100.0, 80.0, 0.5
    px, py = rng.uniform(0, W, 500), rng.uniform(0, H, 500)
    Z = minimum_curvature_grid(px, py, surface(px, py), 0, H, cell, int(H / cell), int(W / cell),
                               max_iter=1000, tol=1e-5)
    tx, ty = rng.uniform(5, W - 5, 1000), rng.uniform(5, H - 5, 1000)
    rmse = np.sqrt(np.mean((bilinear(Z, tx, ty, cell, H) - surface(tx, ty)) ** 2))
    print('hold-out RMSE: %.4f' % rmse)
    assert rmse < 0.1

    # a plane must be reproduced exactly
    x, y = rng.uniform(0, 10, 20), rng.uniform(0, 10, 20)
    Zp = minimum_curvature_grid(x, y, 2 + 3 * x - y, 0, 10, 0.5, 20, 20)
    xs, ys = 0.25 + 0.5 * np.arange(20), 9.75 - 0.5 * np.arange(20)
    assert np.abs(Zp - (2 + 3 * xs[None, :] - ys[:, None])).max() < 1e-9
    print('OK')


if __name__ == '__main__':
    main()
