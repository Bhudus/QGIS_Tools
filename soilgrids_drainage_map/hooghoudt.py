"""Drain spacing from the Hooghoudt equation (metric), using SoilGrids layers.

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


def layer_average_ksat(layers, top_cm, bottom_cm):
    """Thickness-weighted mean Ksat (m/day) of the layers between two depths.

    layers: iterable of (top_cm, bottom_cm, ksat_m_day). Layers with no Ksat
    are skipped. Arithmetic weighting is used because flow toward the drains
    is mostly horizontal, through the layers in parallel.
    Returns None if no layer with a Ksat overlaps the interval.
    """
    total = 0.0
    thick = 0.0
    for top, bot, k in layers:
        if top is None or bot is None or k is None:
            continue
        overlap = min(bot, bottom_cm) - max(top, top_cm)
        if overlap > 0:
            total += k * overlap
            thick += overlap
    return total / thick if thick > 0 else None


def spacing_for_layers(layers, barrier_m, drain_depth_m, coef_mm_day, drain_diam_mm,
                       mid_m=None, drain_level_m=0.0):
    """Drain spacing for one zone, in metric units.

    layers: list of (top_cm, bottom_cm, ksat_m_day). The deepest layer is
        extended down to the impermeable layer if that is deeper.
    barrier_m: depth to the impermeable layer (m).
    mid_m: midplane water table height above the drain (default: at the
        surface, i.e. the drain depth). drain_level_m: water level held at
        the drains, height above the drain (0 for drainage). For
        subirrigation pass both, with drain_level_m > mid_m, and the
        irrigation coefficient as coef_mm_day.
    Returns a dict: ka, kb (m/day), spacing_m (any may be None).
    """
    out = {"ka": None, "kb": None, "spacing_m": None}
    layers = [L for L in layers if L[2] is not None]
    if not layers:
        return out
    barrier_cm = barrier_m * 100.0
    deepest = max(layers, key=lambda L: L[1])
    if barrier_cm > deepest[1]:
        layers = [L for L in layers if L is not deepest] + [(deepest[0], barrier_cm, deepest[2])]
    drain_cm = drain_depth_m * 100.0
    if mid_m is None:
        mid_m = drain_depth_m
    top_cm = max(drain_depth_m - max(mid_m, drain_level_m), 0.0) * 100.0  # top of saturated zone

    ka = layer_average_ksat(layers, top_cm, drain_cm)
    kb = layer_average_ksat(layers, drain_cm, barrier_cm) if barrier_cm > drain_cm else None
    out["ka"], out["kb"] = ka, kb
    if ka is None:
        return out
    D = max(barrier_cm - drain_cm, 0.0) / 100.0 if kb is not None else 0.0
    out["spacing_m"] = drain_spacing(
        q=coef_mm_day / 1000.0,
        h=mid_m,
        hw=drain_level_m,
        ka=ka,
        kb=kb or 0.0,
        D=D,
        r=drain_diam_mm / 2000.0,
    )
    return out
