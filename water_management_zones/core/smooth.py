"""Smooth zone boundaries on a raster of zone labels (numpy only).

Each zone's indicator (1 inside, 0 outside) is blurred, resampled bilinearly
onto a finer grid, and every fine cell takes the zone with the largest blurred
value. Because every cell gets exactly one zone, the smoothed zones still tile
the field with no gaps or overlaps; the blur rounds the corners, removes
speckle and straightens the pixel staircase, and the finer grid lets the
boundaries cut across the original cells.
"""
import numpy as np


def _box_mean(a, r):
    """Mean over a (2r+1) square, zero padded."""
    if r <= 0:
        return a
    k = 2 * r + 1
    p = np.pad(a, ((r + 1, r), (r + 1, r)))
    c = p.cumsum(0).cumsum(1)
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return s / float(k * k)


def _blur(a, r):
    """Two box passes (close to a Gaussian of about r/2 cells)."""
    h = max(1, int(round(r / 2.0)))
    return _box_mean(_box_mean(a, h), h)


def _upsample(a, f):
    """Bilinear resample of a to f times the resolution (cell-centre aligned)."""
    if f == 1:
        return a
    nr, nc = a.shape
    fi = (np.arange(nr * f) + 0.5) / f - 0.5
    fj = (np.arange(nc * f) + 0.5) / f - 0.5
    i0 = np.clip(np.floor(fi).astype(int), 0, nr - 1)
    j0 = np.clip(np.floor(fj).astype(int), 0, nc - 1)
    i1 = np.clip(i0 + 1, 0, nr - 1)
    j1 = np.clip(j0 + 1, 0, nc - 1)
    ti = np.clip(fi - i0, 0, 1)[:, None]
    tj = np.clip(fj - j0, 0, 1)[None, :]
    top = a[i0][:, j0] * (1 - tj) + a[i0][:, j1] * tj
    bot = a[i1][:, j0] * (1 - tj) + a[i1][:, j1] * tj
    return top * (1 - ti) + bot * ti


def smooth_zones(zr, radius, factor=2, fine_mask=None):
    """Smooth the boundaries of a zone raster.

    zr: int array, zones 1..n, 0 outside.
    radius: smoothing radius in cells (0 = no smoothing; only resampled).
    factor: output resolution multiplier (1, 2, 3...).
    fine_mask: bool array of the output shape (the field at the finer
        resolution); default: zr > 0 repeated.

    Returns (labels at the finer resolution, ids of zones too small to survive
    smoothing, which are pasted back unsmoothed).
    """
    f = max(1, int(factor))
    nr, nc = zr.shape
    if fine_mask is None:
        fine_mask = np.repeat(np.repeat(zr > 0, f, 0), f, 1)
    coarse_rep = np.repeat(np.repeat(zr, f, 0), f, 1)
    best = np.zeros((nr * f, nc * f), dtype=np.int32)
    bestv = np.zeros((nr * f, nc * f))
    ids = np.unique(zr[zr > 0])
    pad = int(radius) + 3
    for z in ids:
        rows = np.nonzero((zr == z).any(1))[0]
        cols = np.nonzero((zr == z).any(0))[0]
        r0, r1 = max(0, rows[0] - pad), min(nr, rows[-1] + pad + 1)
        c0, c1 = max(0, cols[0] - pad), min(nc, cols[-1] + pad + 1)
        ind = (zr[r0:r1, c0:c1] == z).astype(float)
        v = _upsample(_blur(ind, radius) if radius > 0 else ind, f)
        sl = (slice(r0 * f, r1 * f), slice(c0 * f, c1 * f))
        win_best, win_v = best[sl], bestv[sl]
        upd = v > win_v
        win_best[upd] = z
        win_v[upd] = v[upd]
    out = np.where(fine_mask, best, 0)
    # fine cells inside the field no zone reached (field corners): nearest original
    hole = fine_mask & (out == 0)
    out[hole] = coarse_rep[hole]
    # zones lost to smoothing (thinner than the blur): paste them back unsmoothed
    kept = np.unique(out[out > 0])
    lost = np.setdiff1d(ids, kept)
    for z in lost:
        m = coarse_rep == z
        out[m & fine_mask] = z
    return out, [int(z) for z in lost]
