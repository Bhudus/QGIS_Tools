"""Recalculate drain and subirrigation spacings, drain footage and cost (no QGIS imports).

Reads the output of SSURGO drainage soil map or SoilGrids drainage soil map
(or of this tool). Works in metres internally; the user's values and the
output fields are in US customary or metric units.
"""
from .hooghoudt import parse_profile, spacing_for_layers

M_PER_FT = 0.3048
MM_PER_IN = 25.4
SQ_M_PER_ACRE = 4046.8564224

US, METRIC = 0, 1

# Fields this tool writes, per unit system: (name, kind, length, precision, key).
NEW_FIELDS = {
    US: [
        ("drain_ft", "dbl", 8, 2, "drain"),
        ("dc_inday", "dbl", 8, 3, "coef"),
        ("mid_ft", "dbl", 8, 2, "mid"),
        ("barrier_ft", "dbl", 8, 2, "barrier"),
        ("ka_ftday", "dbl", 12, 3, "ka"),
        ("kb_ftday", "dbl", 12, 3, "kb"),
        ("spacing_ft", "int", 6, 0, "spacing"),
        ("spc_lo_ft", "int", 6, 0, "spacing_lo"),
        ("spc_hi_ft", "int", 6, 0, "spacing_hi"),
        ("ic_inday", "dbl", 8, 3, "rate"),
        ("hw_ft", "dbl", 8, 2, "hw"),
        ("hm_ft", "dbl", 8, 2, "hm"),
        ("ka_sub", "dbl", 12, 3, "ka_sub"),
        ("subirr_ft", "int", 6, 0, "subirr"),
        ("sub_lo_ft", "int", 6, 0, "subirr_lo"),
        ("sub_hi_ft", "int", 6, 0, "subirr_hi"),
        ("area_ac", "dbl", 12, 2, "area"),
        ("len_ft", "int", 10, 0, "length"),
        ("sublen_ft", "int", 10, 0, "sub_length"),
        ("cost", "dbl", 14, 0, "cost"),
        ("sub_cost", "dbl", 14, 0, "sub_cost"),
    ],
    METRIC: [
        ("drain_m", "dbl", 8, 2, "drain"),
        ("dc_mmday", "dbl", 8, 2, "coef"),
        ("mid_m", "dbl", 8, 2, "mid"),
        ("barrier_m", "dbl", 8, 2, "barrier"),
        ("ka_mday", "dbl", 12, 3, "ka"),
        ("kb_mday", "dbl", 12, 3, "kb"),
        ("spacing_m", "int", 6, 0, "spacing"),
        ("spc_lo_m", "int", 6, 0, "spacing_lo"),
        ("spc_hi_m", "int", 6, 0, "spacing_hi"),
        ("ic_mmday", "dbl", 8, 2, "rate"),
        ("hw_m", "dbl", 8, 2, "hw"),
        ("hm_m", "dbl", 8, 2, "hm"),
        ("ka_sub", "dbl", 12, 3, "ka_sub"),
        ("subirr_m", "int", 6, 0, "subirr"),
        ("sub_lo_m", "int", 6, 0, "subirr_lo"),
        ("sub_hi_m", "int", 6, 0, "subirr_hi"),
        ("area_ha", "dbl", 12, 2, "area"),
        ("len_m", "int", 10, 0, "length"),
        ("sublen_m", "int", 10, 0, "sub_length"),
        ("cost", "dbl", 14, 0, "cost"),
        ("sub_cost", "dbl", 14, 0, "sub_cost"),
    ],
}

# Spacing fields from the soil plugins or an earlier run, replaced in the output.
REPLACED = {f[0] for fs in NEW_FIELDS.values() for f in fs}

SOIL_NAME_FIELDS = ("muname", "soil")


def length_to_m(units, v):
    return None if v is None else (v * M_PER_FT if units == US else v)


def m_to_length(units, v):
    return None if v is None else (v / M_PER_FT if units == US else v)


def rate_to_mm(units, v):
    return None if v is None else (v * MM_PER_IN if units == US else v)


def kday_to_units(units, v):
    return None if v is None else (v / M_PER_FT if units == US else v)


def round_spacing(units, metres):
    """Nearest 5 ft (US) or 1 m (metric), halves up, never below one step."""
    if metres is None:
        return None
    if units == US:
        return int(max(5, 5 * int(metres / M_PER_FT / 5 + 0.5)))
    return int(max(1, int(metres + 0.5)))


def layer_barrier_m(attrs):
    """Depth to the impermeable layer stored on a soil polygon, in metres."""
    if attrs.get("barrier_m") is not None:
        return float(attrs["barrier_m"])
    if attrs.get("barrier_ft") is not None:
        return float(attrs["barrier_ft"]) * M_PER_FT
    return None


def layer_profile(attrs, drain_m, barrier_m):
    """Ksat profiles (representative, low, high), each [(top_cm, bottom_cm, m/day)],
    and where they came from. Low and high are [] when the layer has no range.

    Uses ksat_prof when present. Older layers only have Ka and Kb for the
    drain depth they were made with; then Ka is applied above the new drain
    depth and Kb below it, with no range.
    """
    rep, low, high = parse_profile(attrs.get("ksat_prof"))
    if rep:
        return rep, low, high, "profile"
    if attrs.get("ka_mday") is not None:
        ka, kb = attrs.get("ka_mday"), attrs.get("kb_mday")
    elif attrs.get("ka_ftday") is not None:
        ka = attrs["ka_ftday"] * M_PER_FT
        kb = attrs["kb_ftday"] * M_PER_FT if attrs.get("kb_ftday") is not None else None
    else:
        return [], [], [], None
    drain_cm = drain_m * 100.0
    layers = [(0.0, drain_cm, float(ka))]
    if barrier_m is not None and barrier_m * 100.0 > drain_cm:
        layers.append((drain_cm, barrier_m * 100.0, float(kb if kb is not None else ka)))
    return layers, [], [], "ka_kb"


def recalc(attrs, s):
    """Spacings for one soil polygon.

    attrs: field name -> value for the polygon.
    s: scenario in metres and mm/day: drain, coef, mid (None = surface),
       barrier (None = use the layer's), diam_mm, rate, hw, hm.
    Returns (values keyed like NEW_FIELDS' last item, in metres, m/day and
    mm/day; profile source or None).
    """
    barrier = s["barrier"] if s["barrier"] is not None else layer_barrier_m(attrs)
    mid = s["mid"] if s["mid"] is not None else s["drain"]
    out = {"drain": s["drain"], "coef": s["coef"], "mid": mid, "barrier": barrier,
           "rate": s["rate"], "hw": s["hw"], "hm": s["hm"],
           "ka": None, "kb": None, "spacing": None, "ka_sub": None, "subirr": None,
           "spacing_lo": None, "spacing_hi": None, "subirr_lo": None, "subirr_hi": None}
    if barrier is None:
        return out, None
    rep, low, high, source = layer_profile(attrs, s["drain"], barrier)
    if not rep:
        return out, None

    def drain(layers):
        return spacing_for_layers(layers, barrier, s["drain"], s["coef"], s["diam_mm"], mid_m=mid)

    def subirr(layers):
        return spacing_for_layers(layers, barrier, s["drain"], s["rate"], s["diam_mm"],
                                  mid_m=s["hm"], drain_level_m=s["hw"])

    dr, sub = drain(rep), subirr(rep)
    out.update(ka=dr["ka"], kb=dr["kb"], spacing=dr["spacing_m"],
               ka_sub=sub["ka"], subirr=sub["spacing_m"])
    if low and high:  # lower Ksat gives the narrower spacing
        out.update(spacing_lo=drain(low)["spacing_m"], spacing_hi=drain(high)["spacing_m"],
                   subirr_lo=subirr(low)["spacing_m"], subirr_hi=subirr(high)["spacing_m"])
    return out, source


def area_units(units, sq_m):
    return None if sq_m is None else (sq_m / SQ_M_PER_ACRE if units == US else sq_m / 10000.0)


def drain_length(units, area, spacing):
    """Drain length (ft or m) to drain an area (acres or ha) at a spacing (ft or m)."""
    if area is None or not spacing:
        return None
    return area * (SQ_M_PER_ACRE / M_PER_FT ** 2 if units == US else 10000.0) / spacing


def add_footage(units, values, area_sq_m, cost_per_length):
    """Area, drain lengths and costs for one polygon, at the rounded spacings."""
    area = area_units(units, area_sq_m)
    length = drain_length(units, area, round_spacing(units, values.get("spacing")))
    sub_length = drain_length(units, area, round_spacing(units, values.get("subirr")))
    values.update(
        area=area, length=length, sub_length=sub_length,
        cost=length * cost_per_length if length is not None and cost_per_length is not None else None,
        sub_cost=(sub_length * cost_per_length
                  if sub_length is not None and cost_per_length is not None else None))
    return values


def field_values(units, values):
    """Values for NEW_FIELDS[units], converted and rounded."""
    conv = {
        "drain": lambda v: m_to_length(units, v), "mid": lambda v: m_to_length(units, v),
        "barrier": lambda v: m_to_length(units, v), "hw": lambda v: m_to_length(units, v),
        "hm": lambda v: m_to_length(units, v),
        "coef": lambda v: None if v is None else (v / MM_PER_IN if units == US else v),
        "rate": lambda v: None if v is None else (v / MM_PER_IN if units == US else v),
        "ka": lambda v: kday_to_units(units, v), "kb": lambda v: kday_to_units(units, v),
        "ka_sub": lambda v: kday_to_units(units, v),
        "spacing": lambda v: round_spacing(units, v), "subirr": lambda v: round_spacing(units, v),
        "spacing_lo": lambda v: round_spacing(units, v), "spacing_hi": lambda v: round_spacing(units, v),
        "subirr_lo": lambda v: round_spacing(units, v), "subirr_hi": lambda v: round_spacing(units, v),
        "area": lambda v: v,
        "length": lambda v: None if v is None else int(round(v)),
        "sub_length": lambda v: None if v is None else int(round(v)),
        "cost": lambda v: v, "sub_cost": lambda v: v,
    }
    out = []
    for name, kind, _, prec, key in NEW_FIELDS[units]:
        v = conv[key](values.get(key))
        if v is not None and kind == "dbl":
            v = round(v, prec)
        out.append(v)
    return out
