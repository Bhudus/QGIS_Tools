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
            z_limits=None, thresholds=None):
    """Filter one field's records the way every VB loop does.

    * records with missing values are skipped (new: VB had no NULLs)
    * optional 'Confine to range' on a constraint column z
    * thresholds are applied to the *scaled* yield
    Returns numpy arrays y, x (x is None when no regressor is given).
    """
    ys, xs = [], []
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
        ys.append(y)
        xs.append(x)
    y = np.asarray(ys, dtype=float)
    x = np.asarray(xs, dtype=float) if x_raw is not None else None
    return y, x


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


def slope_tests(s1, s2, use_rls=False):
    """t-tests: each slope vs 0, and slope 1 vs slope 2."""
    key = "rls" if use_rls else "ols"
    if key not in s1 or key not in s2:
        return None
    out = {}
    for tag, s in (("1", s1[key]), ("2", s2[key])):
        df = s["n"] - 2
        t = s["slope"] / math.sqrt(s["slope_var"]) if s["slope_var"] > 0 else float("inf")
        out["t" + tag], out["df" + tag] = t, df
        out["p" + tag] = t_two_tailed(t, df) if math.isfinite(t) else 0.0
    v = s1[key]["slope_var"] + s2[key]["slope_var"]
    t12 = abs(s1[key]["slope"] - s2[key]["slope"]) / math.sqrt(v) if v > 0 else 0.0
    df12 = out["df1"] + out["df2"]
    out.update(t12=t12, df12=df12, p12=t_two_tailed(t12, df12))
    return out


# ---------------------------------------------------------------------------
# Two-field comparison tests
# ---------------------------------------------------------------------------

def welch(m1, s1, n1, m2, s2, n2):
    v1, v2 = s1 * s1 / n1, s2 * s2 / n2
    t = (m1 - m2) / math.sqrt(v1 + v2)
    c = v1 / (v1 + v2)
    df = (n1 - 1) * (n2 - 1) / ((n2 - 1) * c * c + (n1 - 1) * (1 - c) ** 2)
    return abs(t), df, t_two_tailed(t, df)


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


def analyse(f1, f2, regression=False, rls=False, rls_iter=500):
    """f1/f2: dicts with keys y, x (arrays from prepare) and exceed_prob."""
    s1 = field_statistics(f1["y"], f1.get("x"), regression, rls, rls_iter,
                          f1.get("exceed_prob"))
    s2 = field_statistics(f2["y"], f2.get("x"), regression, rls, rls_iter,
                          f2.get("exceed_prob"))
    out = {"field1": s1, "field2": s2}
    out["means"] = welch(s1["mean"], s1["sd"], s1["n"], s2["mean"], s2["sd"], s2["n"])
    out["medians"] = welch(s1["median"], s1["robust_sd"], s1["robust_n"],
                           s2["median"], s2["robust_sd"], s2["robust_n"])
    out["variances"] = variance_test(s1["sd"], s1["n"], s2["sd"], s2["n"])
    lo = min(s1["min"], s2["min"])
    hi = max(s1["max"], s2["max"])
    out["histogram"] = histogram_test(f1["y"], f2["y"], lo, hi)
    if regression:
        out["slopes"] = slope_tests(s1, s2, use_rls=rls)
    return out


def reset_thresholds(y1_untrimmed, y2_untrimmed, e):
    """VB cmdResetThresholds: trim a fraction e from each tail.

    Upper threshold = larger of the two fields' e-exceedance values,
    lower threshold = smaller of the two (1-e)-exceedance values.
    """
    uppers, lowers = [], []
    for y in (y1_untrimmed, y2_untrimmed):
        yd = np.sort(y)[::-1]
        if e <= 0 or e >= 1:
            uppers.append(yd[0])
            lowers.append(yd[-1])
        else:
            uppers.append(exceedance_value(yd, e))
            lowers.append(exceedance_value(yd, 1 - e))
    return float(min(lowers)), float(max(uppers))
