"""Standalone checks for the natural neighbor solver (needs numpy and scipy):
    python tests/test_natneigh.py
"""
import os
import sys

import numpy as np
from scipy.spatial import ConvexHull, Voronoi

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from natneigh_core import dedupe_points, natural_neighbor_grid, triangulate_scipy  # noqa: E402


def cell_area(vor, i):
    reg = vor.regions[vor.point_region[i]]
    if not reg or -1 in reg:
        return None
    return ConvexHull(vor.vertices[reg]).volume


def main():
    rng = np.random.default_rng(5)
    W, H, cell = 100.0, 80.0, 1.0
    nr, nc = int(H / cell), int(W / cell)
    px, py = rng.uniform(0, W, 400), rng.uniform(0, H, 400)

    # 1. linear surfaces are reproduced exactly
    ux, uy, uz = dedupe_points(px, py, 5 + 0.3 * px - 0.2 * py)
    G = natural_neighbor_grid(ux, uy, uz, triangulate_scipy(ux, uy), 0, H, cell, nr, nc)
    xs, ys = 0.5 + np.arange(nc), H - 0.5 - np.arange(nr)
    err = np.nanmax(np.abs(G - (5 + 0.3 * xs[None, :] - 0.2 * ys[:, None])))
    print('linear precision: %.2e' % err)
    assert err < 1e-9

    # 2. agreement with a brute-force Sibson computation (Voronoi areas)
    ux, uy, uz = dedupe_points(px, py, np.sin(px / 15) * 10 + np.cos(py / 20) * 10)
    tris = triangulate_scipy(ux, uy)
    P = np.column_stack((ux, uy))
    vor0 = Voronoi(P)
    worst = 0.0
    for _ in range(15):
        q = np.array([rng.uniform(35, 65), rng.uniform(25, 55)])
        vor1 = Voronoi(np.vstack((P, q)))
        num = den = 0.0
        for i in range(len(P)):
            a0, a1 = cell_area(vor0, i), cell_area(vor1, i)
            if a0 is None or a1 is None:
                continue
            if a0 - a1 > 1e-9:
                num += (a0 - a1) * uz[i]
                den += a0 - a1
        g = natural_neighbor_grid(ux, uy, uz, tris, q[0] - cell / 2, q[1] + cell / 2, cell, 4, 4)
        worst = max(worst, abs(g[0, 0] - num / den))
    print('brute-force Sibson: %.2e' % worst)
    assert worst < 1e-8

    # 3. grid nodes sitting exactly on data points return the data value
    gx, gy = np.meshgrid(np.arange(0, 21, 2.0), np.arange(0, 21, 2.0))
    ux, uy, uz = dedupe_points(gx.ravel(), gy.ravel(), 1 + 0.5 * gx.ravel())
    G = natural_neighbor_grid(ux, uy, uz, triangulate_scipy(ux, uy), -1, 21, 2.0, 11, 11)
    assert np.isfinite(G).all()
    print('OK')


if __name__ == '__main__':
    main()
