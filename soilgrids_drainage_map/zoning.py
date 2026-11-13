"""Turn SoilGrids layer grids into soil zones (no QGIS imports).

SoilGrids gives sand, clay and bulk density at six standard depths. Each
pixel is put in a zone named by its USDA texture above the drains and below
them (down to the impermeable layer, or the 2 m bottom of SoilGrids), e.g.
"silt loam over clay loam". Pixels in the same zone are averaged, layer by
layer, to give the zone's profile.
"""
from .texture import classify

DEPTHS = [(0, 5), (5, 15), (15, 30), (30, 60), (60, 100), (100, 200)]
DEPTH_LABELS = [f"{a}-{b}cm" for a, b in DEPTHS]
PROPERTIES = ("sand", "clay", "bdod")
NODATA = -32768


def pixel_profile(raw, i, j):
    """Profile at pixel (i, j) as a list of (top, bottom, sand, silt, clay, bd),
    or None if any layer has no data.

    raw: {"sand": [6 arrays], "clay": [...], "bdod": [...]} of SoilGrids
    integers (sand and clay in g/kg, bulk density in cg/cm3).
    """
    prof = []
    for k, (top, bot) in enumerate(DEPTHS):
        s, c, b = (int(raw[p][k][i][j]) for p in PROPERTIES)
        if NODATA in (s, c, b) or b <= 0 or s + c <= 0 or s < 0 or c < 0:
            return None
        sand, clay, bd = s / 10.0, c / 10.0, b / 100.0
        silt = max(100.0 - sand - clay, 0.0)
        prof.append((top, bot, sand, silt, clay, bd))
    return prof


def interval_texture(profile, top_cm, bottom_cm):
    """USDA texture class of the thickness-weighted mean between two depths."""
    bottom_cm = min(bottom_cm, DEPTHS[-1][1])
    tot = sand = silt = clay = 0.0
    for top, bot, s, si, c, _ in profile:
        w = min(bot, bottom_cm) - max(top, top_cm)
        if w > 0:
            tot += w
            sand += s * w
            silt += si * w
            clay += c * w
    if tot <= 0:
        return None
    return classify(sand / tot, silt / tot, clay / tot)


def zone_key(profile, drain_depth_m, barrier_m):
    above = interval_texture(profile, 0.0, drain_depth_m * 100.0)
    below = None
    if barrier_m > drain_depth_m and drain_depth_m * 100.0 < DEPTHS[-1][1]:
        below = interval_texture(profile, drain_depth_m * 100.0, barrier_m * 100.0)
    return (above, below)


def zone_name(key):
    above, below = key
    if below is None or below == above:
        return above
    return f"{above} over {below}"


def mean_profile(profiles, weights):
    """Weighted mean of several profiles, layer by layer."""
    total = float(sum(weights))
    out = []
    for k, (top, bot) in enumerate(DEPTHS):
        vals = [sum(p[k][n] * w for p, w in zip(profiles, weights)) / total for n in (2, 3, 4, 5)]
        out.append((top, bot, *vals))
    return out
