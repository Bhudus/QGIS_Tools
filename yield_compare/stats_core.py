# -*- coding: utf-8 -*-
"""
Statistics engine for the Yield Compare plugin.

Port of the computational parts of the VB6 "Yield Statistics" program
(prjYields.vbp / frmShapeToXYZ.frm).  This module has no QGIS dependency
so it can be tested on its own; it needs only the Python standard library
and numpy (which ships with every QGIS install).

Conventions carried over from the VB code
-----------------------------------------
* "Exceedance" quantiles: values are sorted in DESCENDING order and the
  value at 1-based position ceil(n*p) is returned, so exceedance(0.05) is
  the value exceeded by ~5 % of the points (VB: txt05), exceedance(0.95)
  the value exceeded by ~95 % (VB: txt95).
* Gaussian exceedance uses a normal distribution truncated at zero
  (yields cannot be negative): the value x such that
  P(Y > x | Y > 0) = e.
* Robust estimates use Rousseeuw's least-median-of-squares followed by
  two passes of hard 2.5-sigma reweighting, as in the VB routines
  Regression(itype=2) and Median().
"""

import math
from statistics import NormalDist

import numpy as np

STD_NORMAL = NormalDist()

# ---------------------------------------------------------------------------
# Special functions (pure Python; replaces the ALGLIB .bas modules)
# ---------------------------------------------------------------------------

_EPS = 1e-15
_FPMIN = 1e-300


def _betacf(a, b, x):
    """Continued fraction for the incomplete beta function (Lentz)."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < _EPS:
            break
    return h


def incomplete_beta(a, b, x):
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
           + a * math.log(x) + b * math.log1p(-x))
    bt = math.exp(lbt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _gamma_p(a, x):
    """Regularised lower incomplete gamma P(a, x)."""
    if x <= 0.0:
        return 0.0
    gln = math.lgamma(a)
    if x < a + 1.0:
        ap, s, d = a, 1.0 / a, 1.0 / a
        for _ in range(10000):
            ap += 1.0
            d *= x / ap
            s += d
            if abs(d) < abs(s) * _EPS:
                break
        return s * math.exp(-x + a * math.log(x) - gln)
    # continued fraction for Q, return 1-Q
    b = x + 1.0 - a
    c = 1.0 / _FPMIN
    d = 1.0 / b
    h = d
    for i in range(1, 10000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = b + an / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < _EPS:
            break
    return 1.0 - math.exp(-x + a * math.log(x) - gln) * h


def chi2_sf(x, df):
    """Upper-tail probability of the chi-square distribution."""
    if x <= 0:
        return 1.0
    return max(0.0, 1.0 - _gamma_p(df / 2.0, x / 2.0))


def t_cdf(t, df):
    """Student's t cumulative distribution."""
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    tail = 0.5 * incomplete_beta(df / 2.0, 0.5, x)
    return 1.0 - tail if t > 0 else tail


def t_two_tailed(t, df):
    p = t_cdf(t, df)
    return 2.0 * min(p, 1.0 - p)


def f_cdf(x, d1, d2):
    """F distribution cumulative distribution."""
    if x <= 0:
        return 0.0
    return incomplete_beta(d1 / 2.0, d2 / 2.0, d1 * x / (d1 * x + d2))


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

def exceedance_value(sorted_desc, p):
    """Value exceeded by a fraction p of the data (VB quantile rule)."""
    n = len(sorted_desc)
    if n == 0:
        return float("nan")
    if p <= 0:
        return sorted_desc[0]
    if p >= 1:
        return sorted_desc[-1]
    k = round(n * p, 9)
    k2 = math.floor(k)
    idx = k2 if k == k2 else k2 + 1
    idx = min(max(idx, 1), n)
    return float(sorted_desc[idx - 1])


def gaussian_exceedance(mean, sd, e):
    """Value x with P(Y > x | Y > 0) = e for Y ~ N(mean, sd)."""
    if e <= 0 or e >= 1 or sd <= 0:
        return float("nan")
    q0 = STD_NORMAL.cdf(-mean / sd)          # P(Y < 0)
    p = q0 + (1.0 - e) * (1.0 - q0)          # P(Y < x)
    p = min(max(p, 1e-15), 1 - 1e-15)
    return mean + STD_NORMAL.inv_cdf(p) * sd


def _wls(x, y, w=None):
    """(Weighted) least squares y = a + b x, as in VB Regression path 6000."""
    if w is None:
        w = np.ones_like(y)
    sn = w.sum()
    if sn < 3:
        return None
    mx = (w * x).sum() / sn
    my = (w * y).sum() / sn
    vx = ((w * x * x).sum() - sn * mx * mx) / (sn - 1)
    vy = ((w * y * y).sum() - sn * my * my) / (sn - 1)
    vxy = ((w * x * y).sum() - sn * mx * my) / (sn - 1)
    if vx <= 0:
        return None
    b = vxy / vx
    a = my - b * mx
    slope_var = (vy - b * vxy) / (sn - 2) / vx
    r2 = vxy * vxy / vx / vy if vy > 0 else float("nan")
    resid = y - (a + b * x)
    resid_var = (w * resid * resid).sum() / (sn - 2)
    return dict(intercept=float(a), slope=float(b),
                slope_var=float(max(slope_var, 0.0)), r2=float(r2),
                n=float(sn), resid_var=float(resid_var))


def _reweight(r2, n_params, minmed):
    """Two-pass hard rejection weights (|r/s| <= 2.5) from the VB code."""
    n = len(r2)
    s0 = 1.4826 * (1.0 + 5.0 / max(n - n_params, 1)) * math.sqrt(minmed)
    if s0 <= 0:
        return np.ones(n)
    w = (r2 / (s0 * s0) <= 6.25).astype(float)
    bot = w.sum() - n_params
    if bot < 1:
        return np.ones(n)
    s0v = (w * r2).sum() / bot
    if s0v <= 0:
        return w
    return (r2 / s0v <= 6.25).astype(float)


def _lms_line(x, y, slope0, n_iter):
    """Least-median-of-squares line by scanning slope angles.

    For every trial slope the LMS-optimal intercept is found exactly
    (midpoint of the shortest half of the residuals) instead of the
    random intercept jitter used in the VB code.
    """
    n = len(y)
    h = n // 2 + 1
    th0 = math.atan(slope0)
    best = (float("inf"), 0.0, slope0)
    n_iter = max(int(n_iter), 10)
    for th in np.linspace(th0 - math.pi / 2, th0 + math.pi / 2, n_iter + 2)[1:-1]:
        b = math.tan(th)
        r = np.sort(y - b * x)
        widths = r[h - 1:] - r[:n - h + 1]
        i = int(np.argmin(widths))
        half = widths[i] / 2.0
        crit = half * half                          # median squared residual
        if crit < best[0]:
            best = (crit, (r[i] + r[i + h - 1]) / 2.0, b)
    return best  # (min median r^2, intercept, slope)


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------

def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def prepare(y_raw, x_raw=None, z_raw=None, scale_y=1.0, scale_x=1.0,
            z_limits=None, thresholds=None, coords=None, groups=None):
    """Filter one field's records the way every VB loop does.

    * records with missing values (or, when coords are given, without a
      location) are skipped
    * optional 'Confine to range' on a constraint column z
    * thresholds are applied to the *scaled* yield
    Returns numpy arrays y, x, xy (x is None when no regressor is given,
    xy is None when no coordinates are given; xy has shape (n, 2)), or
    y, x, xy, g when a groups (harvest pass) column is given.
    """
    ys, xs, pts, gs = [], [], [], []
    for i, yv in enumerate(y_raw):
        y = _num(yv)
        if y is None:
            continue
        y *= scale_y
        x = None
        if x_raw is not None:
            x = _num(x_raw[i])
            if x is None:
                continue
            x *= scale_x
        if z_limits is not None and z_raw is not None:
            z = _num(z_raw[i])
            if z is None or z < z_limits[0] or z > z_limits[1]:
                continue
        if thresholds is not None and (y < thresholds[0] or y > thresholds[1]):
            continue
        if coords is not None:
            if coords[i] is None:
                continue
        if groups is not None:
            g = groups[i]
            if g is None or (isinstance(g, float) and math.isnan(g)):
                continue
            try:
                g = float(g)
            except (TypeError, ValueError):
                g = str(g)
            gs.append(g)
        if coords is not None:
            pts.append(coords[i])
        ys.append(y)
        xs.append(x)
    y = np.asarray(ys, dtype=float)
    x = np.asarray(xs, dtype=float) if x_raw is not None else None
    xy = np.asarray(pts, dtype=float).reshape(-1, 2) if coords is not None else None
    if groups is not None:
        g = np.asarray(gs) if gs and not isinstance(gs[0], str) else \
            np.unique(np.asarray(gs, dtype=object).astype(str), return_inverse=True)[1]
        return y, x, xy, g
    return y, x, xy


# ---------------------------------------------------------------------------
# Spatial autocorrelation
#
# Neighbouring yield-monitor points are not independent, so ordinary tests
# treat thousands of points as thousands of independent observations and
# find "significant" differences everywhere.  The correlation between two
# points i, j is modelled as
#
#     rho_ij = s_pass * [i, j in the same harvest pass]
#            + s_sp   * exp(-3 d_ij / range)
#
# s_pass: share of the variance between harvest passes (optional, needs a
#         pass / track column), estimated by the method of moments;
# s_sp:   share of the remaining variance that is spatially correlated,
#         from an exponential variogram (nugget + partial sill) fitted to the
#         residuals after removing a planar trend and the pass means.
#
# The model is then used two ways:
# * the effective number of independent points  n_eff = n / (1 + (n-1) rho)
#   (rho = average correlation between pairs) for tests of a field's mean,
#   variance and histogram (Clifford, Richardson & Hemon 1989; Dutilleul 1993);
# * the variance of a contrast sum(c_i y_i) = sigma^2 sum_ij c_i c_j rho_ij
#   for comparisons within a field (over vs between drains, regression
#   slopes), where noise shared by both groups cancels.
# ---------------------------------------------------------------------------

_SEED = 20261126
VARIOGRAM_POINTS = 3000      # global subsample for the variogram
LOCAL_CLUSTERS = 15          # local clusters of neighbours for short lags
LOCAL_SIZE = 400             # points per local cluster
CORR_POINTS = 2500           # subsample for averaging pair correlations
N_LAG_BINS = 20
STRUCTURE_PERMUTATIONS = 99  # permutations for the tests of spatial structure
STRUCTURE_ALPHA = 0.01


def _subsample(n, m, rng):
    return np.arange(n) if n <= m else np.sort(rng.choice(n, m, replace=False))


def _pairs(xy, chunk=300):
    """Yield (i0, i1, dist) for row blocks, dist[k, j] = |xy[i0+k] - xy[j]|."""
    n = len(xy)
    for i0 in range(0, n, chunk):
        i1 = min(n, i0 + chunk)
        dx = xy[i0:i1, 0][:, None] - xy[None, :, 0]
        dy = xy[i0:i1, 1][:, None] - xy[None, :, 1]
        yield i0, i1, np.sqrt(dx * dx + dy * dy)


def detrend(xy, r):
    """Residuals from a least-squares plane a + b x + c y."""
    if len(r) < 10:
        return r - r.mean()
    c = xy - xy.mean(axis=0)
    A = np.column_stack([np.ones(len(r)), c])
    coef, *_ = np.linalg.lstsq(A, r, rcond=None)
    return r - A @ coef


def pass_component(r, groups):
    """Between-pass variance (method of moments) and pass-centred residuals."""
    if groups is None:
        return 0.0, r
    labels, inv = np.unique(groups, return_inverse=True)
    k = len(labels)
    if k < 3:
        return 0.0, r
    cnt = np.bincount(inv)
    gm = np.bincount(inv, r) / cnt
    within = r - gm[inv]
    s2w = float((within ** 2).sum() / max(len(r) - k, 1))
    n0 = (len(r) - (cnt ** 2).sum() / len(r)) / (k - 1)     # unbalanced ANOVA
    msb = float((cnt * (gm - r.mean()) ** 2).sum() / (k - 1))
    s2p = max((msb - s2w) / n0, 0.0)
    return s2p, within


def _pair_set(xy, rng, n_global=VARIOGRAM_POINTS, n_clusters=LOCAL_CLUSTERS,
              size=LOCAL_SIZE):
    """Point pairs for the variogram: all pairs of a global random subsample
    (up to half the field diagonal apart) plus all pairs inside small local
    clusters of neighbouring points, so short lags (along a pass, between
    adjacent passes) are well represented however large the data set."""
    n = len(xy)
    ext = xy.max(axis=0) - xy.min(axis=0)
    max_d = 0.5 * float(math.hypot(ext[0], ext[1]))
    I, J, D = [], [], []

    def add(idx, lim):
        p = xy[idx]
        for i0, i1, d in _pairs(p):
            k = np.arange(i0, i1)[:, None]
            j = np.arange(len(p))[None, :]
            mask = (j > k) & (d <= lim)
            kk, jx = np.nonzero(mask)
            I.append(idx[kk + i0])
            J.append(idx[jx])
            D.append(d[mask].astype(np.float32))

    add(_subsample(n, n_global, rng), max_d)
    if n > n_global:
        for c in rng.choice(n, n_clusters, replace=False):
            d0 = np.hypot(xy[:, 0] - xy[c, 0], xy[:, 1] - xy[c, 1])
            near = np.argsort(d0)[:size]
            add(near, float(d0[near[-1]]))
    return np.concatenate(I), np.concatenate(J), np.concatenate(D), max_d


def _variogram(r, I, J, b, nb, sel):
    g = 0.5 * (r[I[sel]] - r[J[sel]]) ** 2
    bs = b[sel]
    return np.bincount(bs, g, nb), np.bincount(bs, None, nb)


def _perm_test(r, I, J, b, nb, sel, rng, groups=None):
    """Permutation p-value for spatial structure in the pairs `sel`: is the
    semivariogram further from flat than when the values are shuffled
    (among all points, or within each pass when groups is given)?"""
    idx = np.nonzero(sel)[0]
    if len(idx) > 1_200_000:
        idx = rng.choice(idx, 1_200_000, replace=False)
    Ii, Jj, bb = I[idx], J[idx], b[idx]
    cnt = np.bincount(bb, None, nb)
    ok = cnt >= 30
    if ok.sum() < 4:
        return 1.0
    w = cnt[ok] / cnt[ok].max()

    def stat(v):
        g = np.bincount(bb, 0.5 * (v[Ii] - v[Jj]) ** 2, nb)[ok] / cnt[ok]
        m = (w * g).sum() / w.sum()
        return float((w * (g - m) ** 2).sum())

    s0 = stat(r)
    if groups is not None:
        order = np.argsort(groups, kind="stable")
        starts = np.flatnonzero(np.r_[True, groups[order][1:] != groups[order][:-1]])
        ends = np.r_[starts[1:], len(order)]
    exceed = 0
    for _ in range(STRUCTURE_PERMUTATIONS):
        if groups is None:
            v = r[rng.permutation(len(r))]
        else:
            v = r.copy()
            for s_, e_ in zip(starts, ends):
                seg = order[s_:e_]
                v[seg] = r[seg[rng.permutation(len(seg))]]
        if stat(v) >= s0:
            exceed += 1
    return (exceed + 1.0) / (STRUCTURE_PERMUTATIONS + 1.0)


def _vg_dict(gs, cs, hs, max_d):
    ok = cs >= 30
    if ok.sum() < 4:
        return None
    return dict(h=hs[ok] / cs[ok], gamma=gs[ok] / cs[ok], counts=cs[ok], max_dist=max_d)


def empirical_variogram(xy, r, seed=_SEED):
    """Semivariogram of r over all pairs (kept for callers that want one)."""
    if len(r) < 30:
        return None
    rng = np.random.default_rng(seed)
    I, J, D, max_d = _pair_set(xy, rng)
    edges = np.concatenate([[0.0], np.geomspace(max_d / 300.0, max_d, N_LAG_BINS)])
    nb = len(edges) - 1
    b = np.clip(np.searchsorted(edges, D, side="right") - 1, 0, nb - 1)
    sel = np.ones(len(I), bool)
    g, c = _variogram(r, I, J, b, nb, sel)
    return _vg_dict(g, c, np.bincount(b, D, nb), max_d)


def fit_exponential(vg):
    """Weighted least-squares fit of gamma(h) = c0 + c1 (1 - exp(-3 h / a)),
    with the range a no longer than 1.5 x the largest lag."""
    h, g, w = vg["h"], vg["gamma"], vg["counts"]
    w = w / w.max()
    best = None
    for a in np.geomspace(max(h.min(), 1e-9) / 2.0, 1.5 * vg["max_dist"], 150):
        f = 1.0 - np.exp(-3.0 * h / a)
        cands = []
        A = np.array([[w.sum(), (w * f).sum()], [(w * f).sum(), (w * f * f).sum()]])
        rhs = np.array([(w * g).sum(), (w * f * g).sum()])
        try:
            c0, c1 = np.linalg.solve(A, rhs)
            if c0 >= 0 and c1 >= 0:
                cands.append((c0, c1))
        except np.linalg.LinAlgError:
            pass
        cands.append((0.0, max((w * f * g).sum() / (w * f * f).sum(), 0.0)))
        for c0, c1 in cands:
            sse = float((w * (g - c0 - c1 * f) ** 2).sum())
            if best is None or sse < best[0]:
                best = (sse, float(c0), float(c1), float(a))
    _, c0, c1, a = best
    return dict(nugget=c0, psill=c1, range=a, sill=c0 + c1)


def _fit_excess(h, target, w, max_d):
    """Fit target(h) ~ s * exp(-3 h / a), s >= 0 (extra along-pass correlation)."""
    w = w / w.max()
    best = (float((w * target ** 2).sum()), 0.0, 1.0)
    for a in np.geomspace(max(h.min(), 1e-9) / 2.0, 1.5 * max_d, 100):
        f = np.exp(-3.0 * h / a)
        s_ = max(float((w * f * target).sum() / (w * f * f).sum()), 0.0)
        sse = float((w * (target - s_ * f) ** 2).sum())
        if sse < best[0]:
            best = (sse, s_, float(a))
    return best[1], best[2]


def fit_model(xy, resid, groups=None, seed=_SEED):
    """Fit the correlation model to residuals (see the section comment):
    a planar trend is removed, then the between-pass variance, then a
    variogram over pairs in different passes (or all pairs, without a pass
    column) and, with a pass column, the extra correlation along each pass.
    Each spatial part is kept only if a permutation test finds it."""
    n = len(resid)
    if n < 30:
        return None
    rng = np.random.default_rng(seed)
    r = detrend(xy, resid)
    s2p, within = pass_component(r, groups)
    s2w = float(within.var())
    total = s2p + s2w
    if total <= 0:
        return None
    I, J, D, max_d = _pair_set(xy, rng)
    edges = np.concatenate([[0.0], np.geomspace(max_d / 300.0, max_d, N_LAG_BINS)])
    nb = len(edges) - 1
    b = np.clip(np.searchsorted(edges, D, side="right") - 1, 0, nb - 1)
    hsum_all = np.bincount(b, D, nb)
    if groups is not None:
        same = groups[I] == groups[J]
        cross = ~same
    else:
        same = None
        cross = np.ones(len(I), bool)

    # correlation between all points (fitted on pairs in different passes)
    gs, cs = _variogram(within, I, J, b, nb, cross)
    hs = np.bincount(b[cross], D[cross], nb)
    vg = _vg_dict(gs, cs, hs, max_d)
    fit = fit_exponential(vg) if vg is not None else None
    p_x = 1.0
    s_xw, a_x = 0.0, 1.0
    if fit is not None and fit["psill"] > 0 and fit["sill"] > 0:
        p_x = _perm_test(within, I, J, b, nb, cross, rng)
        if p_x <= STRUCTURE_ALPHA:
            s_xw, a_x = min(fit["psill"] / fit["sill"], 1.0), fit["range"]

    # extra correlation along each pass
    vg_same = None
    p_a = 1.0
    s_aw, a_a = 0.0, 1.0
    if same is not None and same.any():
        g2, c2 = _variogram(within, I, J, b, nb, same)
        vg_same = _vg_dict(g2, c2, np.bincount(b[same], D[same], nb), max_d)
        if vg_same is not None:
            rho_same = 1.0 - vg_same["gamma"] / s2w
            excess = rho_same - s_xw * np.exp(-3.0 * vg_same["h"] / a_x)
            s_aw, a_a = _fit_excess(vg_same["h"], excess, vg_same["counts"], max_d)
            s_aw = min(s_aw, 1.0 - s_xw)
            if s_aw > 0:
                p_a = _perm_test(within, I, J, b, nb, same, rng, groups)
                if p_a > STRUCTURE_ALPHA:
                    s_aw = 0.0
    del hsum_all
    s_x = s_xw * s2w / total
    s_a = s_aw * s2w / total
    s_p = s2p / total
    return dict(pass_share=s_p, spatial_share=s_x, range=a_x,
                along_share=s_a, along_range=a_a,
                nugget_share=max(1.0 - s_p - s_x - s_a, 0.0),
                sill=total, pass_var=s2p, within_var=s2w,
                has_groups=groups is not None,
                vg=vg, vg_same=vg_same, vg_fit=fit,
                structure_p=p_x, along_p=p_a)


def _correlated(model):
    return model is not None and (model["spatial_share"] > 0 or model["pass_share"] > 0
                                  or model["along_share"] > 0)


def model_corr(model, d, same=None):
    """Distance part of the correlation between two points (the pass part is
    added exactly elsewhere); `same` marks pairs in the same pass."""
    out = np.zeros_like(d, dtype=float)
    if model is None:
        return out
    if model["spatial_share"] > 0:
        out = out + model["spatial_share"] * np.exp(-3.0 * d / model["range"])
    if model["along_share"] > 0 and same is not None:
        out = out + same * (model["along_share"] * np.exp(-3.0 * d / model["along_range"]))
    return out


def _group_pair_sum(groups, c=None):
    """Sum over ordered pairs i != j in the same group of c_i c_j (c = 1 if None)."""
    _, inv = np.unique(groups, return_inverse=True)
    if c is None:
        n = np.bincount(inv).astype(float)
        return float((n * (n - 1)).sum())
    s = np.bincount(inv, c)
    s2 = np.bincount(inv, c * c)
    return float((s * s - s2).sum())


def _distance_sum(xy, model, groups, c, rng):
    """Estimate sum over ordered pairs i != j of c_i c_j model_corr(d_ij)
    from a random subsample (c = 1 if None)."""
    n = len(xy)
    if model["spatial_share"] <= 0 and (model["along_share"] <= 0 or groups is None):
        return 0.0
    idx = _subsample(n, CORR_POINTS, rng)
    m = len(idx)
    if m < 2:
        return 0.0
    p = xy[idx]
    cc = np.ones(m) if c is None else c[idx]
    gg = groups[idx] if groups is not None else None
    tot = 0.0
    for i0, i1, d in _pairs(p):
        k = np.arange(i0, i1)[:, None]
        j = np.arange(m)[None, :]
        same = (gg[i0:i1][:, None] == gg[None, :]) if gg is not None else None
        blk = cc[i0:i1, None] * cc[None, :] * model_corr(model, d, same)
        tot += float(blk[j != k].sum())
    return tot * n * (n - 1) / (m * (m - 1))


def mean_pair_correlation(xy, model, groups=None, seed=_SEED):
    n = len(xy)
    if not _correlated(model) or n < 2:
        return 0.0
    tot = 0.0
    if model["pass_share"] > 0 and groups is not None:
        tot += model["pass_share"] * _group_pair_sum(groups)
    tot += _distance_sum(xy, model, groups, None, np.random.default_rng(seed + 1))
    return tot / (n * (n - 1))


def n_effective(n, rho):
    if n < 2 or rho <= 0:
        return float(n)
    return float(min(n, max(2.0, n / (1.0 + (n - 1) * rho))))


def spatial_summary(xy, resid, model=None, groups=None):
    """Fit (unless given) the correlation model and return n_eff etc."""
    n = len(resid)
    if model is None:
        model = fit_model(xy, resid, groups)
    rho = mean_pair_correlation(xy, model, groups)
    return dict(n=n, rho=rho, n_eff=n_effective(n, rho), model=model,
                variogram=model["vg"] if model else None)


def contrast_variance(xy, c, model, sigma2, groups=None, seed=_SEED):
    """Variance of sum(c_i * y_i) under the correlation model.

    Var = sigma2 * (sum c_i^2 + sum_{i != j} c_i c_j rho_ij).  The pass part
    of the double sum is exact; the distance part is estimated from a random
    subsample of the points and scaled up.  Unlike an effective sample size,
    this lets noise that two interleaved groups share (e.g. strips over and
    between drains) cancel in their difference.
    """
    nz = c != 0
    cz = c[nz]
    n = len(cz)
    diag = float((cz * cz).sum())
    if not _correlated(model) or n < 2:
        return sigma2 * diag
    gz = groups[nz] if groups is not None else None
    off = 0.0
    if model["pass_share"] > 0 and gz is not None:
        off += model["pass_share"] * _group_pair_sum(gz, cz)
    off += _distance_sum(xy[nz], model, gz, cz, np.random.default_rng(seed + 2))
    # the nugget part is uncorrelated, so the variance cannot fall below it
    return sigma2 * max(diag + off, diag * model["nugget_share"], 1e-12 * diag)


def contrast_df(xy, mask, model, groups=None):
    """Degrees of freedom for a contrast: effective number of the points
    involved, less 2."""
    n = int(mask.sum())
    if not _correlated(model):
        return max(n - 2, 1)
    g = groups[mask] if groups is not None else None
    return max(n_effective(n, mean_pair_correlation(xy[mask], model, g)) - 2, 1.0)


def spatial_slope(xy, x, resid, model, weights=None, groups=None):
    """Slope variance and df of a regression on x with correlated errors
    (resid: residuals of the fit; weights: 0/1 points kept)."""
    keep = np.ones(len(x), bool) if weights is None else weights.astype(bool)
    xk = x[keep]
    sxx = float(((xk - xk.mean()) ** 2).sum())
    c = np.zeros(len(x))
    c[keep] = (xk - xk.mean()) / sxx
    sigma2 = float((resid[keep] ** 2).sum() / max(keep.sum() - 2, 1))
    var = contrast_variance(xy, c, model, sigma2, groups)
    return var, contrast_df(xy, keep, model, groups)


def t_ppf(q, df):
    """Inverse of t_cdf by bisection."""
    lo, hi = -1e4, 1e4
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# Per-field statistics
# ---------------------------------------------------------------------------

def field_statistics(y, x=None, regression=False, rls=False, rls_iter=500,
                     exceed_prob=None):
    """All single-field quantities shown on the VB form."""
    n = len(y)
    if n < 4:
        raise ValueError("Fewer than 4 usable points after filtering.")
    res = {"n": n}
    mean = float(y.mean())
    sd = float(y.std(ddof=1))
    res.update(mean=mean, sd=sd, min=float(y.min()), max=float(y.max()))

    # normality (Jarque-Bera)
    v = (y - mean) / sd
    skew = float((v ** 3).mean())
    kurt = float((v ** 4).mean() - 3.0)
    jb = n / 6.0 * (skew ** 2 + kurt ** 2 / 4.0)
    res.update(skewness=skew, kurtosis=kurt, jb_stat=jb, jb_p=chi2_sf(jb, 2))

    # non-parametric summaries (VB NonParametric)
    yd = np.sort(y)[::-1]
    q75 = exceedance_value(yd, 0.25)    # upper quartile
    q25 = exceedance_value(yd, 0.75)    # lower quartile
    res.update(median=exceedance_value(yd, 0.5),
               iqr=q75 - q25,
               iq_ratio=(q75 / q25) if q25 != 0 else float("nan"),
               emp_05=exceedance_value(yd, 0.05),
               emp_95=exceedance_value(yd, 0.95),
               gauss_05=gaussian_exceedance(mean, sd, 0.05),
               gauss_95=gaussian_exceedance(mean, sd, 0.95))
    if exceed_prob is not None:
        res["exceed_prob"] = exceed_prob
        res["gauss_exceed"] = gaussian_exceedance(mean, sd, exceed_prob) \
            if 0 < exceed_prob < 1 else (res["max"] if exceed_prob <= 0 else res["min"])
        res["emp_exceed"] = exceedance_value(yd, exceed_prob)

    # robust location / scale (VB Median)
    r2 = (y - res["median"]) ** 2
    w = _reweight(r2, 2, float(np.median(r2)))
    sw = w.sum()
    rmean = float((w * y).sum() / sw)
    rsd = float(math.sqrt(((w * y * y).sum() - sw * rmean * rmean) / (sw - 1)))
    res.update(robust_mean=rmean, robust_sd=rsd, robust_n=float(sw))

    # regression (VB Regression itype 1 and 2)
    if regression and x is not None:
        ols = _wls(x, y)
        if ols is None:
            raise ValueError("Regressor column has no variation.")
        res["ols"] = ols
        res["x_min"], res["x_max"] = float(x.min()), float(x.max())
        if rls:
            minmed, a0, b0 = _lms_line(x, y, ols["slope"], rls_iter)
            r2 = (y - a0 - b0 * x) ** 2
            w = _reweight(r2, 2, minmed)
            fit = _wls(x, y, w) or ols
            fit["lms_intercept"], fit["lms_slope"] = float(a0), float(b0)
            fit["weights"] = w
            res["rls"] = fit
    return res


# ---------------------------------------------------------------------------
# Two-field comparison tests
# ---------------------------------------------------------------------------

def welch(m1, s1, n1, m2, s2, n2):
    v1, v2 = s1 * s1 / n1, s2 * s2 / n2
    t = (m1 - m2) / math.sqrt(v1 + v2)
    c = v1 / (v1 + v2)
    df = (n1 - 1) * (n2 - 1) / ((n2 - 1) * c * c + (n1 - 1) * (1 - c) ** 2)
    return abs(t), df, t_two_tailed(t, df)


def mean_difference(m1, s1, n1, m2, s2, n2, level=0.95):
    """m1 - m2 with a Welch confidence interval."""
    diff = m1 - m2
    se = math.sqrt(s1 * s1 / n1 + s2 * s2 / n2)
    _, df, _ = welch(m1, s1, n1, m2, s2, n2) if se > 0 else (0, 1, 1)
    half = t_ppf(0.5 + level / 2.0, df) * se
    return dict(diff=diff, lo=diff - half, hi=diff + half, se=se, df=df)


def variance_test(s1, n1, s2, n2):
    r = (s1 * s1) / (s2 * s2)
    d1, d2 = n1 - 1, n2 - 1
    p = f_cdf(r, d1, d2)
    return min(r, 1.0 / r), d1, d2, 2.0 * min(p, 1.0 - p)


def histogram_test(y1, y2, lo, hi, bins=30):
    """Chi-square style comparison of two 30-bin histograms (VB code)."""
    edges = np.linspace(lo, hi, bins + 1)
    c1, _ = np.histogram(np.clip(y1, lo, hi), edges)
    c2, _ = np.histogram(np.clip(y2, lo, hi), edges)
    n1, n2 = len(y1), len(y2)
    stat, used = 0.0, 0
    for a, b in zip(c1, c2):
        if a and b:
            stat += (a / n1 - b / n2) ** 2 / (a / n1 / n1 + b / n2 / n2)
            used += 1
    df = max(used - 1, 1)
    return dict(stat=stat, df=df, p=chi2_sf(stat, df),
                edges=edges, counts1=c1, counts2=c2)


def _slope_test(fit, var=None, df=None):
    """t-test of a slope against 0 (var/df override the independent-errors
    values when spatial correlation is accounted for)."""
    if var is None:
        var, df = fit["slope_var"], fit["n"] - 2
    t = fit["slope"] / math.sqrt(var) if var > 0 else float("inf")
    p = t_two_tailed(t, df) if math.isfinite(t) else 0.0
    return dict(t=abs(t), df=df, p=p, var=var)


def _test(name, stat, df, p, p_naive=None, note=""):
    return dict(name=name, stat=stat, df=df, p=p,
                p_naive=p if p_naive is None else p_naive, note=note)


def holm(ps):
    """Holm step-down adjusted p-values (controls the chance of any false
    'difference' among several comparisons)."""
    m = len(ps)
    order = sorted(range(m), key=lambda i: ps[i])
    adj = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * ps[i]))
        adj[i] = running
    return adj


def welch_anova(means, sds, ns):
    """Welch's heteroscedastic one-way ANOVA.  Returns F, df1, df2, p."""
    k = len(means)
    w = np.array([n / (s * s) if s > 0 else 1e12 for s, n in zip(sds, ns)])
    m = np.asarray(means, float)
    ns = np.asarray(ns, float)
    mw = (w * m).sum() / w.sum()
    a = (w * (m - mw) ** 2).sum() / (k - 1)
    lam = ((1.0 - w / w.sum()) ** 2 / np.maximum(ns - 1, 1e-9)).sum()
    F = a / (1.0 + 2.0 * (k - 2) * lam / (k * k - 1))
    df1, df2 = k - 1.0, (k * k - 1.0) / (3.0 * lam) if lam > 0 else 1e9
    return float(F), df1, float(df2), max(0.0, 1.0 - f_cdf(F, df1, df2))


def bartlett(sds, ns):
    """Bartlett's test of equal variances (nu_i = n_i - 1).  Returns T, df, p."""
    nu = np.asarray(ns, float) - 1.0
    v = np.asarray(sds, float) ** 2
    N = nu.sum()
    sp = (nu * v).sum() / N
    k = len(v)
    T = (N * math.log(sp) - (nu * np.log(v)).sum()) / \
        (1.0 + ((1.0 / nu).sum() - 1.0 / N) / (3.0 * (k - 1)))
    return float(T), k - 1, chi2_sf(T, k - 1)


def homogeneity_test(ys, lo, hi, bins=30):
    """Chi-square test that k samples share one histogram (30 bins)."""
    edges = np.linspace(lo, hi, bins + 1)
    counts = [np.histogram(np.clip(y, lo, hi), edges)[0] for y in ys]
    O = np.array(counts, float)
    used = O.sum(axis=0) > 0
    O = O[:, used]
    E = O.sum(axis=1, keepdims=True) * O.sum(axis=0, keepdims=True) / O.sum()
    stat = float(((O - E) ** 2 / E).sum())
    df = max((O.shape[1] - 1) * (len(ys) - 1), 1)
    return dict(stat=stat, df=df, p=chi2_sf(stat, df), edges=edges, counts=counts)


def wald_equal(est, var, dfs=None):
    """Test that k estimates (with variances) are all equal.

    Without dfs: Wald chi-square with k-1 df.  With dfs (the degrees of
    freedom behind each variance): the statistic / (k-1) is referred to an
    F distribution with Satterthwaite denominator df, which allows for the
    variances themselves being estimated from few effective points (with
    k = 2 this is Welch's t-test).  Returns stat, df, p.
    """
    est, var = np.asarray(est, float), np.asarray(var, float)
    w = 1.0 / var
    m = (w * est).sum() / w.sum()
    X = float((w * (est - m) ** 2).sum())
    k = len(est)
    if dfs is None:
        return X, k - 1, chi2_sf(X, k - 1)
    dfs = np.asarray(dfs, float)
    d2 = float(var.sum() ** 2 / (var ** 2 / dfs).sum())
    F = X / (k - 1)
    return F, f"{k - 1}, {d2:.1f}", max(0.0, 1.0 - f_cdf(F, k - 1, d2))


def analyse_many(fields, regression=False, rls=False, rls_iter=500, spatial=True,
                 distance=None):
    """Compare two or more fields.

    fields: list of dicts with keys y, x, xy, g (arrays from prepare) and
    exceed_prob, and for the distance-to-tile analysis d (distance of each
    point to the nearest tile line).  distance: dict(reach=..., n_bins=...)
    or None; reach is half the drain spacing, one value or one per field.
    spatial: correct every test for spatial autocorrelation (needs xy).

    Returns dict with
      fields:  per-field statistics
      tests:   tests across all fields (with 2 fields, the two-field tests)
      pairs:   every pair of fields, each test with Holm-adjusted p-values
      histogram, distance, distance_test, spatial, k
    """
    k = len(fields)
    if k < 2:
        raise ValueError("Choose at least two fields.")
    stats = [field_statistics(f["y"], f.get("x"), regression, rls, rls_iter,
                              f.get("exceed_prob")) for f in fields]
    use_sp = spatial and all(f.get("xy") is not None for f in fields)
    ne = []
    for f, s in zip(fields, stats):
        if use_sp:
            sp = spatial_summary(f["xy"], f["y"] - s["mean"], groups=f.get("g"))
            s["spatial"] = sp
            ne.append(sp["n_eff"])
        else:
            ne.append(float(s["n"]))
    ns = [float(s["n"]) for s in stats]
    out = {"fields": stats, "spatial": use_sp, "k": k}

    # regression slopes: each against 0, allowing for autocorrelation
    key = "rls" if rls else "ols"
    kind = "reweighted LS" if rls else "least squares"
    if regression:
        for f, s in zip(fields, stats):
            fit = s[key]
            var = df = None
            if use_sp:
                w = fit["weights"] if key == "rls" else None
                resid = f["y"] - fit["intercept"] - fit["slope"] * f["x"]
                keep = np.ones(len(resid), bool) if w is None else w.astype(bool)
                g = f.get("g")
                model = fit_model(f["xy"][keep], resid[keep],
                                  g[keep] if g is not None else None)
                var, df = spatial_slope(f["xy"], f["x"], resid, model, w, g)
                fit["spatial_df"] = df
            s["slope_test"] = (_slope_test(fit, var, df), _slope_test(fit))

    # ---- every pair
    pairs = []
    for i in range(k):
        for j in range(i + 1, k):
            a, b = stats[i], stats[j]
            fa, fb = fields[i], fields[j]
            nea, neb, na, nb = ne[i], ne[j], ns[i], ns[j]
            t = {}
            st, df, p = welch(a["mean"], a["sd"], nea, b["mean"], b["sd"], neb)
            _, _, p0 = welch(a["mean"], a["sd"], na, b["mean"], b["sd"], nb)
            t["means"] = dict(stat=st, df=df, p=p, p_naive=p0)
            diff = mean_difference(a["mean"], a["sd"], nea, b["mean"], b["sd"], neb)
            diff["pct"] = 100.0 * diff["diff"] / b["mean"] if b["mean"] else float("nan")
            rna, rnb = a["robust_n"] * nea / na, b["robust_n"] * neb / nb
            st, df, p = welch(a["median"], a["robust_sd"], rna, b["median"], b["robust_sd"], rnb)
            _, _, p0 = welch(a["median"], a["robust_sd"], a["robust_n"],
                             b["median"], b["robust_sd"], b["robust_n"])
            t["medians"] = dict(stat=st, df=df, p=p, p_naive=p0)
            f_, d1, d2, p = variance_test(a["sd"], nea, b["sd"], neb)
            _, _, _, p0 = variance_test(a["sd"], na, b["sd"], nb)
            t["variances"] = dict(stat=f_, df=f"{d1:.0f}, {d2:.0f}", p=p, p_naive=p0)
            h = histogram_test(fa["y"], fb["y"], min(a["min"], b["min"]),
                               max(a["max"], b["max"]))
            fac = (1.0 / na + 1.0 / nb) / (1.0 / nea + 1.0 / neb)
            h["stat_adj"] = h["stat"] * fac
            h["p_adj"] = chi2_sf(h["stat_adj"], h["df"])
            t["histograms"] = dict(stat=h["stat_adj"], df=h["df"], p=h["p_adj"],
                                   p_naive=h["p"])
            if regression:
                (xa, ya), (xb, yb) = a["slope_test"], b["slope_test"]
                ba, bb = a[key]["slope"], b[key]["slope"]

                def dtest(u, v):
                    var = u["var"] + v["var"]
                    t12 = abs(ba - bb) / math.sqrt(var) if var > 0 else 0.0
                    d12 = u["df"] + v["df"]
                    return t12, d12, t_two_tailed(t12, d12)
                st, df, p = dtest(xa, xb)
                _, _, p0 = dtest(ya, yb)
                t["slopes"] = dict(stat=st, df=df, p=p, p_naive=p0)
            pairs.append(dict(i=i, j=j, diff=diff, tests=t, histogram=h))
    for name in pairs[0]["tests"]:
        for fld in ("p", "p_naive"):
            adj = holm([pr["tests"][name][fld] for pr in pairs])
            for pr, v in zip(pairs, adj):
                pr["tests"][name][fld + "_adj"] = v
    out["pairs"] = pairs

    # ---- tests across all fields
    tests = []
    if k == 2:
        t = pairs[0]["tests"]
        tests.append(_test("Equivalence of means (Welch t)", t["means"]["stat"],
                           t["means"]["df"], t["means"]["p"], t["means"]["p_naive"]))
        tests.append(_test("Equivalence of medians (robust Welch t)", t["medians"]["stat"],
                           t["medians"]["df"], t["medians"]["p"], t["medians"]["p_naive"]))
        tests.append(_test("Equivalence of variances (F)", t["variances"]["stat"],
                           t["variances"]["df"], t["variances"]["p"],
                           t["variances"]["p_naive"]))
        tests.append(_test("Equivalence of histograms (χ², 30 bins)",
                           t["histograms"]["stat"], t["histograms"]["df"],
                           t["histograms"]["p"], t["histograms"]["p_naive"]))
    else:
        F, d1, d2, p = welch_anova([s["mean"] for s in stats], [s["sd"] for s in stats], ne)
        _, _, _, p0 = welch_anova([s["mean"] for s in stats], [s["sd"] for s in stats], ns)
        tests.append(_test("Equal means, all fields (Welch ANOVA, F)", F,
                           f"{d1:.0f}, {d2:.1f}", p, p0))
        rn = [s["robust_n"] * e / n for s, e, n in zip(stats, ne, ns)]
        F, d1, d2, p = welch_anova([s["median"] for s in stats],
                                   [s["robust_sd"] for s in stats], rn)
        _, _, _, p0 = welch_anova([s["median"] for s in stats],
                                  [s["robust_sd"] for s in stats],
                                  [s["robust_n"] for s in stats])
        tests.append(_test("Equal medians, all fields (robust Welch ANOVA, F)", F,
                           f"{d1:.0f}, {d2:.1f}", p, p0))
        T, df, p = bartlett([s["sd"] for s in stats], ne)
        _, _, p0 = bartlett([s["sd"] for s in stats], ns)
        tests.append(_test("Equal variances, all fields (Bartlett χ²)", T, df, p, p0))
        h = homogeneity_test([f["y"] for f in fields], min(s["min"] for s in stats),
                             max(s["max"] for s in stats))
        fac = sum(1.0 / n for n in ns) / sum(1.0 / e for e in ne)
        tests.append(_test("Equal histograms, all fields (χ², 30 bins)", h["stat"] * fac,
                           h["df"], chi2_sf(h["stat"] * fac, h["df"]), h["p"]))
    if regression:
        for i, s in enumerate(stats):
            adj, naive = s["slope_test"]
            tests.append(_test(f"Slope, Field {i + 1} vs 0 ({kind})", adj["t"], adj["df"],
                               adj["p"], naive["p"]))
        if k == 2:
            t = pairs[0]["tests"]["slopes"]
            tests.append(_test("Slope, Field 1 vs Field 2", t["stat"], t["df"], t["p"],
                               t["p_naive"]))
        else:
            X, df, p = wald_equal([s[key]["slope"] for s in stats],
                                  [s["slope_test"][0]["var"] for s in stats],
                                  [s["slope_test"][0]["df"] for s in stats])
            _, _, p0 = wald_equal([s[key]["slope"] for s in stats],
                                  [s["slope_test"][1]["var"] for s in stats])
            tests.append(_test("Equal slopes, all fields (F)", X, df, p, p0))
    out["tests"] = tests

    # histogram of every field on common bins, for the plot
    lo = min(s["min"] for s in stats)
    hi = max(s["max"] for s in stats)
    edges = np.linspace(lo, hi, 31)
    out["histogram"] = dict(edges=edges, counts=[
        np.histogram(np.clip(f["y"], lo, hi), edges)[0] for f in fields])

    # ---- distance to tile
    if distance:
        reach = distance["reach"]
        reaches = list(reach) if isinstance(reach, (tuple, list)) else [reach] * k
        profs = []
        for f, s, r in zip(fields, stats, reaches):
            if f.get("d") is None:
                profs.append(None)
                continue
            model = s["spatial"]["model"] if use_sp else None
            profs.append(distance_profile(f["y"], f["d"], f.get("xy"), r,
                                          distance.get("n_bins", 8), s["mean"], model,
                                          use_sp, f.get("g")))
        out["distance"] = profs
        # is the loss midway (relative to over the tile) the same in every field?
        ok = [(i, p["mid_vs_near"]) for i, p in enumerate(profs)
              if p and "error" not in p and p.get("mid_vs_near")]
        if len(ok) >= 2:
            est = [m["pct"] for _, m in ok]
            var = [(m["se"] / m["mean_a"] * 100.0) ** 2 for _, m in ok]
            var0 = [(m["se_naive"] / m["mean_a"] * 100.0) ** 2 for _, m in ok]
            X, df, p = wald_equal(est, var, [m["df"] for _, m in ok])
            _, _, p0 = wald_equal(est, var0)
            out["distance_test"] = _test(
                "Same midway loss (as % of yield over tile) in "
                + ("both fields" if len(ok) == 2 else "all fields") + " (F)",
                X, df, p, p0)
            out["distance_test"]["fields"] = [i for i, _ in ok]
    return out


def analyse(f1, f2, regression=False, rls=False, rls_iter=500, spatial=True,
            distance=None):
    """Compare two fields (see analyse_many); the original two-field result."""
    r = analyse_many([f1, f2], regression, rls, rls_iter, spatial, distance)
    pr = r["pairs"][0]
    out = {"field1": r["fields"][0], "field2": r["fields"][1], "spatial": r["spatial"],
           "tests": r["tests"], "difference": pr["diff"], "histogram": pr["histogram"],
           "pairs": r["pairs"]}
    for key in ("distance", "distance_test"):
        if key in r:
            out[key] = r[key]
    return out


# ---------------------------------------------------------------------------
# Yield against distance to the nearest tile line
# ---------------------------------------------------------------------------

MIN_GROUP = 20     # fewest points for a group to be compared


def _compare_groups(y, xy, a, b, model, use_sp, groups=None):
    """Mean of group b minus mean of group a, with CI and test.

    With use_sp the variance of the difference comes from the fitted
    correlation model (contrast_variance), so patches of good or poor ground
    that both groups share cancel out, as they should.
    """
    na, nb = int(a.sum()), int(b.sum())
    if na < MIN_GROUP or nb < MIN_GROUP:
        return None
    ya, yb = y[a], y[b]
    ma, mb = float(ya.mean()), float(yb.mean())
    sa, sb = float(ya.std(ddof=1)), float(yb.std(ddof=1))
    diff = mb - ma
    _, _, p0 = welch(mb, sb, nb, ma, sa, na)
    if use_sp and model is not None:
        c = np.zeros(len(y))
        c[b] = 1.0 / nb
        c[a] = -1.0 / na
        pooled = ((na - 1) * sa * sa + (nb - 1) * sb * sb) / (na + nb - 2)
        se = math.sqrt(contrast_variance(xy, c, model, pooled, groups))
        df = contrast_df(xy, a | b, model, groups)
    else:
        se = math.sqrt(sa * sa / na + sb * sb / nb)
        _, df, _ = welch(mb, sb, nb, ma, sa, na)
    t = diff / se if se > 0 else float("inf")
    p = t_two_tailed(t, df) if math.isfinite(t) else 0.0
    half = t_ppf(0.975, df) * se
    return dict(mean_a=ma, mean_b=mb, n_a=na, n_b=nb, diff=diff,
                lo=diff - half, hi=diff + half, se=se,
                se_naive=math.sqrt(sa * sa / na + sb * sb / nb),
                pct=100.0 * diff / ma if ma else float("nan"),
                t=abs(t), df=df, p=p, p_naive=p0)


def distance_profile(y, d, xy, reach, n_bins, field_mean, model=None, use_sp=False,
                     groups=None):
    """Yield in bands of distance from the nearest tile line.

    reach: half the drain spacing (the farthest a point between two
    parallel laterals can be from tile).  Points farther than that are
    treated as beyond the reach of the tile.
    """
    within = d <= reach
    out = dict(reach=reach, n=len(y), n_within=int(within.sum()),
               n_beyond=int((~within).sum()))
    if out["n_within"] < 2 * MIN_GROUP:
        out["error"] = "Too few points within half a drain spacing of the tile."
        return out
    sp = use_sp and _correlated(model)
    mean_w = float(y[within].mean())
    edges = np.linspace(0.0, reach, n_bins + 1)
    bins = []
    for i in range(n_bins):
        hi_ok = (d < edges[i + 1]) if i < n_bins - 1 else (d <= edges[i + 1])
        m = within & (d >= edges[i]) & hi_ok
        nb = int(m.sum())
        row = dict(lo=edges[i], hi=edges[i + 1], n=nb, mean=float("nan"),
                   rel=float("nan"), ci=float("nan"), rel_ci=float("nan"))
        if nb >= 3:
            mean = float(y[m].mean())
            # CI of the band's difference from the average within reach
            g = _compare_groups(y, xy, within & ~m, m, model, sp, groups) \
                if nb >= MIN_GROUP and (within & ~m).sum() >= MIN_GROUP else None
            ci = (g["hi"] - g["diff"]) if g else float("nan")
            row.update(mean=mean, rel=100.0 * mean / field_mean, ci=ci,
                       rel_ci=100.0 * ci / field_mean)
        bins.append(row)
    out["bins"] = bins
    out["mean_within"] = mean_w

    # over the tile (inner quarter) vs midway between drains (outer quarter)
    near = within & (d <= 0.25 * reach)
    mid = within & (d >= 0.75 * reach)
    out["mid_vs_near"] = _compare_groups(y, xy, near, mid, model, sp, groups)

    # straight-line trend of yield with distance, within reach
    fit = _wls(d[within], y[within])
    if fit is not None:
        naive = _slope_test(fit)
        st = naive
        if sp:
            resid = y[within] - fit["intercept"] - fit["slope"] * d[within]
            var, df = spatial_slope(xy[within], d[within], resid, model, None,
                                    groups[within] if groups is not None else None)
            st = _slope_test(fit, var, df)
        half = t_ppf(0.975, st["df"]) * math.sqrt(st["var"])
        out["trend"] = dict(slope=fit["slope"], intercept=fit["intercept"],
                            lo=fit["slope"] - half, hi=fit["slope"] + half,
                            change=fit["slope"] * reach,
                            change_pct=100.0 * fit["slope"] * reach / fit["intercept"]
                            if fit["intercept"] else float("nan"),
                            t=st["t"], df=st["df"], p=st["p"], p_naive=naive["p"])

    # beyond the reach of the tile vs within it
    out["beyond_vs_within"] = _compare_groups(y, xy, within, ~within, model, sp, groups)
    return out


def reset_thresholds(*args):
    """VB cmdResetThresholds: trim a fraction e from each tail.

    reset_thresholds(y1, y2, ..., e): upper threshold = largest of the
    fields' e-exceedance values, lower threshold = smallest of their
    (1-e)-exceedance values.
    """
    *ys, e = args
    uppers, lowers = [], []
    for y in ys:
        yd = np.sort(y)[::-1]
        if e <= 0 or e >= 1:
            uppers.append(yd[0])
            lowers.append(yd[-1])
        else:
            uppers.append(exceedance_value(yd, e))
            lowers.append(exceedance_value(yd, 1 - e))
    return float(min(lowers)), float(max(uppers))
