"""Field summary: soils, drain footage and cost, and design spacings (no QGIS imports).

Two design spacings are given for each field:

- Equal drain length: the single spacing that needs the same total drain
  length as installing each soil at its own spacing,
  L = total area / sum(area_i / L_i). Drain length per acre is inversely
  proportional to spacing, so this is the area-weighted harmonic mean.
- Narrowest soil: the narrowest spacing among soils that cover at least a set
  share of the field, so small inclusions don't control the design. If no
  soil reaches that share, the largest soil is used.

Spacings are in the output units (ft or m), already rounded per soil. The
design spacings are rounded the same way.
"""
from .recalc import US, drain_length

# Summary table fields: (US name, metric name, kind, length, precision, key)
SUMMARY_FIELDS = [
    ("field", "field", "str", 20, 0, "group"),
    ("item", "item", "str", 100, 0, "item"),
    ("area_ac", "area_ha", "dbl", 12, 2, "area"),
    ("share_pct", "share_pct", "dbl", 6, 1, "share"),
    ("spacing_ft", "spacing_m", "int", 6, 0, "spacing"),
    ("spc_lo_ft", "spc_lo_m", "int", 6, 0, "spacing_lo"),
    ("spc_hi_ft", "spc_hi_m", "int", 6, 0, "spacing_hi"),
    ("len_ft", "len_m", "int", 10, 0, "length"),
    ("cost", "cost", "dbl", 14, 0, "cost"),
    ("subirr_ft", "subirr_m", "int", 6, 0, "subirr"),
    ("sub_lo_ft", "sub_lo_m", "int", 6, 0, "subirr_lo"),
    ("sub_hi_ft", "sub_hi_m", "int", 6, 0, "subirr_hi"),
    ("sublen_ft", "sublen_m", "int", 10, 0, "sub_length"),
    ("sub_cost", "sub_cost", "dbl", 14, 0, "sub_cost"),
]

SPACING_KEYS = ("spacing", "spacing_lo", "spacing_hi", "subirr", "subirr_lo", "subirr_hi")


def _round_step(units, v):
    if v is None:
        return None
    step = 5 if units == US else 1
    return int(max(step, step * int(v / step + 0.5)))


def _equal_length(units, soils, key):
    """Spacing giving the same drain length as each soil at its own spacing."""
    with_spacing = [s for s in soils if s.get(key)]
    if not with_spacing or any(not s.get(key) for s in soils if s.get("spacing")):
        return None
    area = sum(s["area"] for s in with_spacing)
    inverse = sum(s["area"] / s[key] for s in with_spacing)
    return _round_step(units, area / inverse) if inverse > 0 else None


def summarize(units, polygons, threshold_pct, cost_per_length):
    """Summary rows for each field.

    polygons: dicts with group, soil, area (acres or ha) and the rounded
        spacings (SPACING_KEYS), in output units.
    Returns a list of row dicts keyed like SUMMARY_FIELDS' last item.
    """
    def costed(row):
        for length_key, cost_key in (("length", "cost"), ("sub_length", "sub_cost")):
            v = row.get(length_key)
            row[cost_key] = v * cost_per_length if v is not None and cost_per_length is not None else None
            if v is not None:
                row[length_key] = int(round(v))
        return row

    rows = []
    groups = {}
    for p in polygons:
        groups.setdefault(p["group"], []).append(p)
    for group in sorted(groups, key=str):
        polys = groups[group]
        field_area = sum(p["area"] or 0.0 for p in polys)

        # One row per soil and spacing (several polygons of a soil are added together).
        soils = {}
        for p in polys:
            key = (p["soil"] or "(no name)",) + tuple(p.get(k) for k in SPACING_KEYS)
            s = soils.setdefault(key, {k: p.get(k) for k in SPACING_KEYS})
            s["item"] = key[0]
            s["area"] = s.get("area", 0.0) + (p["area"] or 0.0)
        soils = sorted(soils.values(), key=lambda s: -s["area"])
        designed = [s for s in soils if s.get("spacing")]
        for s in soils:
            row = {"group": str(group), "item": s["item"], "area": round(s["area"], 2),
                   "share": round(100.0 * s["area"] / field_area, 1) if field_area else None}
            row.update({k: s.get(k) for k in SPACING_KEYS})
            row["length"] = drain_length(units, s["area"], s.get("spacing"))
            row["sub_length"] = drain_length(units, s["area"], s.get("subirr"))
            rows.append(costed(row))

        if not designed:
            continue
        design_area = sum(s["area"] for s in designed)
        total = {"group": str(group), "item": "All soils, each at its own spacing",
                 "area": round(design_area, 2),
                 "share": round(100.0 * design_area / field_area, 1) if field_area else None,
                 "length": sum(drain_length(units, s["area"], s["spacing"]) for s in designed),
                 "sub_length": (sum(drain_length(units, s["area"], s["subirr"]) for s in designed)
                                if all(s.get("subirr") for s in designed) else None)}
        rows.append(costed(total))

        eq = {"group": str(group), "item": "Design: equal drain length", "area": round(design_area, 2),
              "share": total["share"]}
        eq.update({k: _equal_length(units, designed, k) for k in SPACING_KEYS})
        eq["length"] = drain_length(units, design_area, eq["spacing"])
        eq["sub_length"] = drain_length(units, design_area, eq["subirr"])
        rows.append(costed(eq))

        big = [s for s in designed if field_area and 100.0 * s["area"] / field_area >= threshold_pct]
        if big:
            pick = min(big, key=lambda s: s["spacing"])
            sub_pick = min((s for s in big if s.get("subirr")), key=lambda s: s["subirr"], default=None)
            label = f"Design: narrowest soil over {threshold_pct:g}% ({pick['item']})"
        else:
            pick = sub_pick = designed[0]
            label = f"Design: largest soil, none over {threshold_pct:g}% ({pick['item']})"
        nr = {"group": str(group), "item": label[:100], "area": round(design_area, 2), "share": total["share"],
              "spacing": pick["spacing"], "spacing_lo": pick.get("spacing_lo"),
              "spacing_hi": pick.get("spacing_hi")}
        if sub_pick is not None:
            nr.update(subirr=sub_pick.get("subirr"), subirr_lo=sub_pick.get("subirr_lo"),
                      subirr_hi=sub_pick.get("subirr_hi"))
        nr["length"] = drain_length(units, design_area, nr["spacing"])
        nr["sub_length"] = drain_length(units, design_area, nr.get("subirr"))
        rows.append(costed(nr))
    return rows


def describe(units, rows):
    """Plain-text lines for the Processing log."""
    u = "ft" if units == US else "m"
    a = "ac" if units == US else "ha"
    lines = []
    for r in rows:
        bits = [f"{r['item']}: {r['area']:,.2f} {a}"]
        if r.get("spacing"):
            rng = (f" ({r['spacing_lo']}–{r['spacing_hi']})"
                   if r.get("spacing_lo") and r.get("spacing_hi") else "")
            bits.append(f"drain spacing {r['spacing']} {u}{rng}")
        if r.get("length") is not None:
            bits.append(f"{r['length']:,} {u} of drain"
                        + (f", cost {r['cost']:,.0f}" if r.get("cost") is not None else ""))
        if r.get("subirr"):
            bits.append(f"subirrigation {r['subirr']} {u}")
        lines.append(f"[{r['group']}] " + "; ".join(bits))
    return lines
