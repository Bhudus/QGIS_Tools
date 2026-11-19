# -*- coding: utf-8 -*-
"""
NoData-aware Gaussian smoothing of a DEM (normalized convolution).

NoData cells and cells outside the grid don't count: each output cell is the
weighted mean of its *valid* neighbours only, so edges and holes don't drag
elevations toward zero. NoData cells stay NoData.
"""

import math

import numpy as np


def gaussian_kernel(sigma):
    radius = max(1, int(math.ceil(3.0 * sigma)))
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    return k / k.sum(), radius


def _conv_axis(a, k, radius, axis):
    """1D convolution along `axis` with zero padding (separable pass)."""
    pad = [(0, 0), (0, 0)]
    pad[axis] = (radius, radius)
    ap = np.pad(a, pad, mode="constant")
    out = np.zeros_like(a)
    n = a.shape[axis]
    for i, w in enumerate(k):
        if axis == 0:
            out += w * ap[i:i + n, :]
        else:
            out += w * ap[:, i:i + n]
    return out


def gaussian_smooth(dem, sigma, nodata_mask=None):
    """Return a smoothed copy of `dem`. `sigma` is in pixels (0 = no change)."""
    dem = np.asarray(dem, dtype=np.float64)
    if sigma <= 0:
        return dem.copy()
    invalid = ~np.isfinite(dem)
    if nodata_mask is not None:
        invalid |= np.asarray(nodata_mask, dtype=bool)
    valid = (~invalid).astype(np.float64)
    data = np.where(invalid, 0.0, dem)

    k, r = gaussian_kernel(sigma)
    num = _conv_axis(_conv_axis(data, k, r, 0), k, r, 1)
    den = _conv_axis(_conv_axis(valid, k, r, 0), k, r, 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[invalid] = dem[invalid]
    return out
