# -*- coding: utf-8 -*-
"""
Minimum curvature gridding with optional tension (pure numpy, no QGIS imports).

The surface z(x, y) satisfies, away from the data,

        (1 - T) * Laplacian(Laplacian(z)) - T * Laplacian(z) = 0

with T = 0 giving the classical minimum curvature (biharmonic) surface and
T = 1 a Laplace (membrane) surface.  The equation is discretised on the
output grid with the usual 13-point stencil and solved by successive
over-relaxation (SOR) on a coarse-to-fine sequence of grids
(Briggs 1974; Swain 1976; Smith & Wessel 1990).

Implementation notes
--------------------
* A best-fit plane is removed from the data first and added back at the end.
  The plane is a null-space solution of the equation, so extrapolation
  beyond the data tends towards a plane rather than a constant.
* Every data point constrains its nearest grid node.  The constraint value is
  corrected with the local surface gradient (first order Taylor expansion), so
  the final surface honours the data much better than simple nearest-node
  snapping.  Several points falling in one cell are averaged.
* The relaxation is a vectorised Gauss-Seidel/SOR sweep using a 5-colouring of
  the grid (colour = (row + 2*col) mod 5), which is the smallest colouring in
  which no two nodes of one colour appear in each other's 13-point stencil.
* Boundary condition: odd reflection about the edge nodes (zero second
  derivative normal to the edge), i.e. linear extrapolation into ghost cells.
"""
import math

import numpy as np


class Canceled(Exception):
    """Raised when the caller requests cancellation."""


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def _fit_plane(x, y, z):
    """Least squares plane. Returns a function plane(x, y) (broadcasting)."""
    x0, y0 = float(x.mean()), float(y.mean())
    sx = float(np.ptp(x)) or 1.0
    sy = float(np.ptp(y)) or 1.0
    A = np.column_stack((np.ones_like(x), (x - x0) / sx, (y - y0) / sy))
    coef = np.linalg.lstsq(A, z, rcond=None)[0]

    def plane(xx, yy):
        return coef[0] + coef[1] * (xx - x0) / sx + coef[2] * (yy - y0) / sy

    return plane


def _pad(P):
    """Refresh the two ghost layers around the interior by odd reflection."""
    P[1, 2:-2] = 2.0 * P[2, 2:-2] - P[3, 2:-2]
    P[0, 2:-2] = 2.0 * P[2, 2:-2] - P[4, 2:-2]
    P[-2, 2:-2] = 2.0 * P[-3, 2:-2] - P[-4, 2:-2]
    P[-1, 2:-2] = 2.0 * P[-3, 2:-2] - P[-5, 2:-2]
    P[:, 1] = 2.0 * P[:, 2] - P[:, 3]
    P[:, 0] = 2.0 * P[:, 2] - P[:, 4]
    P[:, -2] = 2.0 * P[:, -3] - P[:, -4]
    P[:, -1] = 2.0 * P[:, -3] - P[:, -5]


def _sh(sl, d):
    return slice(sl.start + d, sl.stop + d, sl.step)


def _upsample(Zc, nr, nc):
    """Bilinear resampling of a grid with cells twice as large onto (nr, nc)."""
    nrc, ncc = Zc.shape

    def axis(n, nsrc):
        f = np.clip((np.arange(n) + 0.5) / 2.0 - 0.5, 0.0, nsrc - 1.0)
        i0 = np.floor(f).astype(np.intp)
        i1 = np.minimum(i0 + 1, nsrc - 1)
        return i0, i1, f - i0

    r0, r1, wr = axis(nr, nrc)
    c0, c1, wc = axis(nc, ncc)
    top = Zc[np.ix_(r0, c0)] * (1.0 - wc)[None, :] + Zc[np.ix_(r0, c1)] * wc[None, :]
    bot = Zc[np.ix_(r1, c0)] * (1.0 - wc)[None, :] + Zc[np.ix_(r1, c1)] * wc[None, :]
    return top * (1.0 - wr)[:, None] + bot * wr[:, None]


class _Level(object):
    """Grid geometry, data constraints and stencil for one resolution."""

    def __init__(self, m, cell, xmin, ymax, nr, nc, px, py, w, tension):
        self.m, self.nr, self.nc = m, nr, nc
        cs = cell * m
        fc = (px - xmin) / cs - 0.5
        fr = (ymax - py) / cs - 0.5
        c = np.clip(np.rint(fc), 0, nc - 1).astype(np.intp)
        r = np.clip(np.rint(fr), 0, nr - 1).astype(np.intp)
        self.pdc = fc - c          # offset of the point from its node, in cells
        self.pdr = fr - r
        self.pz = w
        self.ci = r + 2            # padded indices of each point's node
        self.cj = c + 2

        uniq, inv = np.unique(r * nc + c, return_inverse=True)
        self.inv = inv.ravel()
        self.counts = np.bincount(self.inv).astype(np.float64)
        self.fi = uniq // nc + 2
        self.fj = uniq % nc + 2

        # 1 for free nodes, 0 for nodes constrained by data
        self.free = np.ones((nr + 4, nc + 4))
        self.free[self.fi, self.fj] = 0.0

        # stencil coefficients; the tension is relative to the finest cell size
        a = 1.0 - tension
        b = tension * m * m
        d = 20.0 * a + 4.0 * b
        self.k1 = (8.0 * a + b) / d
        self.k2 = -2.0 * a / d
        self.k3 = -a / d

        # five-colour block decomposition
        self.blocks = []
        for k in range(5):
            lst = []
            for r0 in range(min(5, nr)):
                c0 = (3 * (k - r0)) % 5
                if c0 >= nc:
                    continue
                rs = slice(2 + r0, 2 + nr, 5)
                cc = slice(2 + c0, 2 + nc, 5)
                s1 = ((_sh(rs, -1), cc), (_sh(rs, 1), cc), (rs, _sh(cc, -1)), (rs, _sh(cc, 1)))
                s2 = ((_sh(rs, -1), _sh(cc, -1)), (_sh(rs, -1), _sh(cc, 1)),
                      (_sh(rs, 1), _sh(cc, -1)), (_sh(rs, 1), _sh(cc, 1)))
                s3 = ((_sh(rs, -2), cc), (_sh(rs, 2), cc), (rs, _sh(cc, -2)), (rs, _sh(cc, 2)))
                lst.append(((rs, cc), s1, s2, s3))
            self.blocks.append(lst)

    def relax(self, P, omega, max_iter, tol, on_iter, cancelled):
        """SOR iterations on padded array P (in place). Returns (iters, maxdiff)."""
        k1, k2, k3 = self.k1, self.k2, self.k3
        free = self.free
        ci, cj = self.ci, self.cj
        maxdiff = 0.0
        it = 0
        for it in range(1, max_iter + 1):
            if cancelled():
                raise Canceled()

            # re-evaluate constraint values with the current surface gradient
            gc = 0.5 * (P[ci, cj + 1] - P[ci, cj - 1])
            gr = 0.5 * (P[ci + 1, cj] - P[ci - 1, cj])
            adj = self.pz - gc * self.pdc - gr * self.pdr
            fv = np.bincount(self.inv, weights=adj) / self.counts
            maxdiff = float(np.abs(fv - P[self.fi, self.fj]).max())
            P[self.fi, self.fj] = fv
            _pad(P)

            for blocks in self.blocks:
                for (rs, cc), s1, s2, s3 in blocks:
                    ctr = P[rs, cc]
                    t1 = P[s1[0]] + P[s1[1]] + P[s1[2]] + P[s1[3]]
                    t2 = P[s2[0]] + P[s2[1]] + P[s2[2]] + P[s2[3]]
                    t3 = P[s3[0]] + P[s3[1]] + P[s3[2]] + P[s3[3]]
                    delta = k1 * t1
                    if k2:
                        delta += k2 * t2
                        delta += k3 * t3
                    delta -= ctr
                    delta *= omega
                    delta *= free[rs, cc]
                    ctr += delta
                    md = float(np.abs(delta).max())
                    if md > maxdiff:
                        maxdiff = md
                _pad(P)

            if on_iter is not None:
                on_iter(it, maxdiff)
            if it >= 3 and maxdiff < tol:
                break
        return it, maxdiff


# ----------------------------------------------------------------------------
# public API
# ----------------------------------------------------------------------------
def minimum_curvature_grid(px, py, pz, xmin, ymax, cell, nrows, ncols,
                           tension=0.0, max_iter=500, tol=1e-4, omega=1.4,
                           progress=None, log=None, cancelled=None):
    """
    Interpolate scattered points on a regular grid.

    Cell (row, col) has its centre at
    (xmin + (col + 0.5) * cell, ymax - (row + 0.5) * cell).

    Parameters
    ----------
    px, py, pz : array-like   data coordinates and values
    xmin, ymax : float        upper-left corner of the grid
    cell       : float        square cell size
    nrows, ncols : int        grid dimensions (each >= 4)
    tension    : float        0 (minimum curvature) .. 1 (Laplace)
    max_iter   : int          iteration cap per grid level
    tol        : float        stop when the largest change in one iteration is
                              below tol * (range of the detrended data)
    omega      : float        over-relaxation factor, 1 <= omega < 2
    progress   : callable(fraction)         optional
    log        : callable(str)              optional
    cancelled  : callable() -> bool         optional

    Returns
    -------
    numpy.ndarray of shape (nrows, ncols), float64
    """
    progress = progress or (lambda f: None)
    log = log or (lambda s: None)
    cancelled = cancelled or (lambda: False)

    if nrows < 4 or ncols < 4:
        raise ValueError("The output grid must be at least 4 x 4 cells.")
    if not 0.0 <= tension <= 1.0:
        raise ValueError("Tension must be between 0 and 1.")
    if not 1.0 <= omega < 2.0:
        raise ValueError("Relaxation factor must satisfy 1 <= omega < 2.")

    px = np.asarray(px, dtype=np.float64)
    py = np.asarray(py, dtype=np.float64)
    pz = np.asarray(pz, dtype=np.float64)

    xmax = xmin + ncols * cell
    ymin = ymax - nrows * cell
    keep = ((px >= xmin) & (px <= xmax) & (py >= ymin) & (py <= ymax)
            & np.isfinite(px) & np.isfinite(py) & np.isfinite(pz))
    px, py, pz = px[keep], py[keep], pz[keep]
    if px.size < 3:
        raise ValueError("At least 3 points inside the extent are required.")

    # --- detrend ------------------------------------------------------------
    plane = _fit_plane(px, py, pz)
    w = pz - plane(px, py)
    wrange = float(np.ptp(w))
    xs = xmin + (np.arange(ncols) + 0.5) * cell
    ys = ymax - (np.arange(nrows) + 0.5) * cell
    trend = plane(xs[None, :], ys[:, None])
    if wrange <= 0.0 or wrange < 1e-12 * max(1.0, float(np.abs(pz).max())):
        return trend                       # data are (numerically) planar
    tol_abs = tol * wrange

    # --- grid hierarchy (coarse -> fine) ---------------------------------------
    factors = [1]
    while True:
        m = factors[-1] * 2
        if min(math.ceil(nrows / m), math.ceil(ncols / m)) < 8:
            break
        factors.append(m)
    factors.reverse()

    weights = [math.ceil(nrows / m) * math.ceil(ncols / m) for m in factors]
    wsum = float(sum(weights))
    done = 0.0

    P = None
    for m, wt in zip(factors, weights):
        nr, nc = int(math.ceil(nrows / m)), int(math.ceil(ncols / m))
        lvl = _Level(m, cell, xmin, ymax, nr, nc, px, py, w, tension)

        Pn = np.zeros((nr + 4, nc + 4))
        if P is not None:
            Pn[2:-2, 2:-2] = _upsample(P[2:-2, 2:-2], nr, nc)
        P = Pn
        _pad(P)

        base, span = done / wsum, wt / wsum

        def on_iter(it, md, base=base, span=span):
            progress(base + span * min(1.0, it / float(max_iter)))

        it, md = lvl.relax(P, omega, max_iter, tol_abs, on_iter, cancelled)
        log("Grid %d x %d (cell = %g): %d iteration(s), last max change %.3g%s"
            % (nc, nr, cell * m, it, md,
               "" if md < tol_abs else "  [iteration limit reached]"))
        done += wt
        progress(done / wsum)

    return P[2:-2, 2:-2] + trend
