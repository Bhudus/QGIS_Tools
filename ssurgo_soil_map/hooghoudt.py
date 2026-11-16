"""Drain spacing from the Hooghoudt equation, using SSURGO horizon data.

General steady-state form, with heights measured above the drains:

    q L^2 = 8 Kb d |Hm - Hw| + 4 Ka |Hm^2 - Hw^2|

    Hw  water level at the drains (height above the drain)
    Hm  water table midway between drains (height above the drain)

Drainage: Hw = 0 and, with the water table at the surface, Hm = drain depth,
giving the usual q L^2 = 8 Kb d h + 4 Ka h^2.
Subirrigation (inverse Hooghoudt): water is held at Hw in the drains and flows
out to the midplane, Hw > Hm, and q is the upward flux (ET) to be supplied.

    q   drainage coefficient, or subirrigation rate, length/day
    L   drain spacing
    Ka  hydraulic conductivity above drain level
    Kb  hydraulic conductivity below drain level
    (Ka is averaged over the saturated zone above the drains, Kb from the
    drains down to the impermeable layer.)
    D   depth of the impermeable layer below drain level
    d   Hooghoudt equivalent depth, a function of L, D and the drain radius r

d is computed with the closed-form approximation of van der Molen and
Wesseling (1991). Because d depends on L, the equation is solved for L
numerically. This module has no QGIS imports so it can be tested on its own.

Internal units are metres and metres per day.
"""
import math

M_PER_FT = 0.3048
M_PER_IN = 0.0254
CM_PER_FT = 30.48
# SSURGO ksat_r is in micrometres per second.
M_PER_DAY_PER_UM_PER_S = 86400e-6  # 0.0864


def _f_of_x(x):
    """F(x) from van der Molen and Wesseling (1991), x = 2*pi*D/L."""
    if x < 0.5:
        # Small-x form; the series converges too slowly here.
        return math.pi ** 2 / (4.0 * x) + math.log(x / (2.0 * math.pi))
    total = 0.0
    n = 1
    while True:
        e = math.exp(-2.0 * n * x)
        term = 4.0 * e / (n * (1.0 - e))
        total += term
        if term < 1e-12:
            break
        n += 2
    return total


def equivalent_depth(L, D, r):
    """Hooghoudt equivalent depth d (same length units for all arguments)."""
    if D <= 0:
        return 0.0
    x = 2.0 * math.pi * D / L
    denom = math.log(L / (math.pi * r)) + _f_of_x(x)
    if denom <= 0:
        return D
    return min(D, math.pi * L / (8.0 * denom))


def drain_spacing(q, h, ka, kb, D, r, hw=0.0):
    """Solve q L^2 = 8 Kb d(L) |h - hw| + 4 Ka |h^2 - hw^2| for L.

    h is the midplane water table and hw the water level at the drains, both
    as heights above the drain (hw = 0 for drainage). All lengths in metres,
    q, ka and kb in metres per day. Returns L in metres, or None when there
    is no solution (no flow, no head difference or no conductivity).
    """
    dh = abs(h - hw)
    dh2 = abs(h * h - hw * hw)
    if q <= 0 or dh <= 0 or ka is None or ka < 0:
        return None
    kb = kb or 0.0
    D = max(D or 0.0, 0.0)

    def residual(L):
        return q * L * L - 8.0 * kb * equivalent_depth(L, D, r) * dh - 4.0 * ka * dh2

    lo = max(4.0 * r, 0.01)  # the formula needs L well above the drain size
    if residual(lo) >= 0:
        return None  # spacing would be smaller than the drain itself
    hi = max(1.0, 2.0 * lo)
    while residual(hi) < 0:
        hi *= 2.0
        if hi > 1e7:
            return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if residual(mid) < 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-6 * hi:
            break
    return 0.5 * (lo + hi)


def layer_average_ksat(horizons, top_cm, bottom_cm):
    """Thickness-weighted mean Ksat (um/s) of the horizons between two depths.

    horizons: iterable of (top_cm, bottom_cm, ksat_um_s). Horizons with no
    Ksat are skipped. Arithmetic weighting is used because flow toward the
    drains is mostly horizontal, through the layers in parallel.
    Returns None if no horizon with a Ksat overlaps the interval.
    """
    total = 0.0
    thick = 0.0
    for top, bot, k in horizons:
        if top is None or bot is None or k is None:
            continue
        overlap = min(bot, bottom_cm) - max(top, top_cm)
        if overlap > 0:
            total += k * overlap
            thick += overlap
    return total / thick if thick > 0 else None


def spacing_for_component(horizons, barrier_cm, drain_depth_ft, coef_in_day, drain_diam_in,
                          mid_ft=None, drain_level_ft=0.0, ranges=None):
    """Drain spacing for one soil component, in US units.

    mid_ft: midplane water table height above the drain (default: at the
        surface, i.e. the drain depth). drain_level_ft: water level held at
        the drains, height above the drain (0 for drainage). For
        subirrigation pass both, with drain_level_ft > mid_ft, and the
        subirrigation rate as coef_in_day.

    horizons: list of (top_cm, bottom_cm, ksat_um_s) from SSURGO chorizon.
    ranges: optional list of (ksat_l, ksat_h) in um/s, one per horizon, saved
        in ksat_prof for the Drain spacing recalculator's spacing range.
    barrier_cm: depth to the impermeable layer (restrictive layer, or the bottom
        of the soil profile when there is none).
    Returns a dict: ka_ftday, kb_ftday, barrier_ft, spacing_ft (any may be None).
    """
    out = {"ka_ftday": None, "kb_ftday": None, "barrier_ft": None, "spacing_ft": None,
           "ksat_prof": None}
    if not horizons:
        return out
    ranges = ranges if ranges and len(ranges) == len(horizons) else [(None, None)] * len(horizons)
    conv = lambda v: v * M_PER_DAY_PER_UM_PER_S if v is not None else None  # noqa: E731
    out["ksat_prof"] = format_profile(
        [(t, b, conv(k), conv(lo), conv(hi)) for (t, b, k), (lo, hi) in zip(horizons, ranges)])
    drain_cm = drain_depth_ft * CM_PER_FT
    if barrier_cm is None:
        barrier_cm = max((b for _, b, _ in horizons if b is not None), default=None)
    if barrier_cm is not None:
        out["barrier_ft"] = barrier_cm / CM_PER_FT

    if mid_ft is None:
        mid_ft = drain_depth_ft
    top_ft = max(drain_depth_ft - max(mid_ft, drain_level_ft), 0.0)  # top of saturated zone
    ka = layer_average_ksat(horizons, top_ft * CM_PER_FT, drain_cm)
    kb = None
    if barrier_cm is not None and barrier_cm > drain_cm:
        kb = layer_average_ksat(horizons, drain_cm, barrier_cm)
    if ka is None:
        return out
    ka_md = ka * M_PER_DAY_PER_UM_PER_S
    kb_md = kb * M_PER_DAY_PER_UM_PER_S if kb is not None else None
    out["ka_ftday"] = ka_md / M_PER_FT
    out["kb_ftday"] = kb_md / M_PER_FT if kb_md is not None else None

    D_m = max((barrier_cm or 0.0) - drain_cm, 0.0) / 100.0 if kb_md is not None else 0.0
    L = drain_spacing(
        q=coef_in_day * M_PER_IN,
        h=mid_ft * M_PER_FT,
        hw=drain_level_ft * M_PER_FT,
        ka=ka_md,
        kb=kb_md or 0.0,
        D=D_m,
        r=drain_diam_in * M_PER_IN / 2.0,
    )
    if L is not None:
        out["spacing_ft"] = L / M_PER_FT
    return out


def format_profile(layers):
    """Layer-by-layer Ksat as text, e.g. "0-30:0.778/0.259/2.59;30-203:0.346/0.1/1.2".

    layers: (top_cm, bottom_cm, ksat_m_day) or (top_cm, bottom_cm, ksat_m_day,
    low, high). Depths in cm, Ksat in m/day, whatever units the rest of the
    layer uses, so the Drain spacing recalculator can read output from either
    soil plugin. Low and high Ksat are written only when every layer has
    them. Layers with no Ksat are left out. Fits a 254-character shapefile
    text field: if the ranges don't fit, they are dropped.
    """
    rows = []
    for L in sorted(layers, key=lambda L: (L[0] is None, L[0])):
        top, bot, k = L[0], L[1], L[2]
        if top is None or bot is None or k is None or bot <= top:
            continue
        lo, hi = (L[3], L[4]) if len(L) >= 5 else (None, None)
        rows.append((top, bot, k, lo, hi))
    with_range = bool(rows) and all(lo is not None and hi is not None for *_, lo, hi in rows)
    for use_range in ((True, False) if with_range else (False,)):
        text = ";".join(
            f"{top:g}-{bot:g}:{k:.4g}" + (f"/{lo:.4g}/{hi:.4g}" if use_range else "")
            for top, bot, k, lo, hi in rows)
        if len(text) <= 254:
            return text
    return None


def parse_profile(text):
    """Read a ksat_prof field into three profiles: representative, low and high.

    Each is [(top_cm, bottom_cm, ksat_m_day), ...]. Low and high are [] unless
    every layer has them. Everything is [] if the text can't be read.
    """
    rep, low, high = [], [], []
    if not text or not isinstance(text, str):
        return rep, low, high
    ranged = True
    for part in text.split(";"):
        try:
            depths, ks = part.split(":")
            top, bot = (float(v) for v in depths.split("-"))
            vals = [float(v) for v in ks.split("/")]
        except ValueError:
            return [], [], []
        if bot <= top or any(v < 0 for v in vals):
            continue
        rep.append((top, bot, vals[0]))
        if len(vals) == 3:
            low.append((top, bot, vals[1]))
            high.append((top, bot, vals[2]))
        else:
            ranged = False
    if not ranged:
        low, high = [], []
    return rep, low, high
