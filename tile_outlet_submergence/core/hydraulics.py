"""Circular-pipe hydraulics and drain-spacing relations (SI units, numpy only)."""
import math

import numpy as np

K_SI = 1.0                       # Manning constant, SI


def roughness(d_in, pipe_type=0):
    """Manning's n by pipe type and inside diameter (inches), as in Illini Drainage
    Tools: single-wall corrugated 0.015 to 8 in, 0.017 to 12 in, 0.020 above;
    smooth wall 0.011; clay or concrete 0.013."""
    d = np.asarray(d_in, dtype=float)
    if pipe_type == 1:
        return np.full(d.shape, 0.011)
    if pipe_type == 2:
        return np.full(d.shape, 0.013)
    return np.where(d <= 8, 0.015, np.where(d <= 12, 0.017, 0.020))


def section(y, D):
    """Flow area and hydraulic radius of a circular pipe of diameter D at depth y."""
    y = np.clip(np.asarray(y, float), 1e-9, None)
    D = np.asarray(D, float)
    yy = np.minimum(y, D)
    th = 2.0 * np.arccos(np.clip(1.0 - 2.0 * yy / D, -1.0, 1.0))
    A = D * D / 8.0 * (th - np.sin(th))
    P = D * th / 2.0
    R = np.where(P > 0, A / np.maximum(P, 1e-12), 0.0)
    return A, R


def friction_slope(Q, y, D, n):
    """Manning friction slope for flow Q at depth y (full pipe when y >= D)."""
    A, R = section(y, D)
    conv = K_SI / n * A * np.power(np.maximum(R, 1e-12), 2.0 / 3.0)
    return (np.asarray(Q, float) / np.maximum(conv, 1e-15)) ** 2


def full_capacity(D, n, S):
    A = math.pi * np.asarray(D, float) ** 2 / 4.0
    R = np.asarray(D, float) / 4.0
    return K_SI / n * A * np.power(R, 2.0 / 3.0) * np.sqrt(np.maximum(S, 0.0))


def normal_depth(Q, S, D, n, iters=40):
    """Normal depth (vectorised). Where the pipe can't carry Q at slope S in open
    channel flow (or S <= 0), returns D: the pipe runs full."""
    Q = np.asarray(Q, float)
    S = np.asarray(S, float)
    D = np.asarray(D, float) * np.ones_like(Q)
    n = np.asarray(n, float) * np.ones_like(Q)
    out = np.array(D, dtype=float)
    ok = (S > 0) & (Q > 0)
    # the largest open-channel discharge is at about 0.938 D
    ymax = 0.938 * D
    A, R = section(ymax, D)
    qmax = K_SI / n * A * np.power(R, 2.0 / 3.0) * np.sqrt(np.maximum(S, 0.0))
    ok &= Q < qmax
    lo = np.zeros_like(Q)
    hi = ymax.copy()
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        A, R = section(mid, D)
        q = K_SI / n * A * np.power(np.maximum(R, 1e-12), 2.0 / 3.0) * np.sqrt(np.maximum(S, 0))
        big = q > Q
        hi = np.where(big, mid, hi)
        lo = np.where(big, lo, mid)
    out[ok] = (0.5 * (lo + hi))[ok]
    out[(Q <= 0) & (S > 0)] = 0.0
    return out


def equivalent_depth(d, L, r):
    """Hooghoudt equivalent depth (Moody's approximation), all in metres.
    d: depth from drain to the impermeable layer; L: drain spacing; r: drain radius."""
    if d <= 0:
        return 0.0
    if d / L <= 0.3:
        return d / (1.0 + d / L * (8.0 / math.pi * math.log(d / r) - 3.4))
    return L * math.pi / (8.0 * (math.log(L / r) - 1.15))


def hooghoudt_shape(h, de):
    """Drainage rate up to a constant K/L^2: 8 de h + 4 h^2 (h >= 0)."""
    h = np.maximum(np.asarray(h, float), 0.0)
    return 8.0 * de * h + 4.0 * h * h
