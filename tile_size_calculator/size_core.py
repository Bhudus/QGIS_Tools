"""Resize the pipes of an Illini Subsurface Drain Layout network (pure Python).

Each line of the network is a Routine K sizing segment. Its flow is the tile
length draining through it (FLOW_LENGTH, ft) x the drain spacing (ft) x its
drainage coefficient (D_COEFF, inch/day), and its pipe is sized for that flow
at SIZE_SLOPE with Manning's equation for a full pipe (see sizing.py). Only the
drainage coefficient changes here: it can be one value for every line, one per
Strahler order (TILE_ORDER), or set pipe by pipe on top of the coefficients the
layer already has.

The pipe type (Manning's n) can also be set line by line: `sections` {fid: type}
for the lines you set, else one type for every line, else the type each line
has in its PIPE_TYPE field.
"""
import math

from . import sizing as K

REQUIRED = ("TILE_ORDER", "FLOW_LENGTH", "SIZE_SLOPE")
MAX_SIZE_FLAG = "needs more than {} in pipe".format(K.AVAILABLE_NOMINAL_SIZES[-1])
MODES = ("single", "order", "pipe")


class SizeError(Exception):
    pass


def _get(attrs, name):
    """Case-insensitive lookup; None when missing or NULL."""
    if name in attrs:
        v = attrs[name]
    else:
        low = name.lower()
        v = next((val for k, val in attrs.items() if str(k).lower() == low), None)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return None if math.isnan(f) else v


def _num(attrs, name):
    v = _get(attrs, name)
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def field_name(attrs, name):
    """The layer's own spelling of a field name, or None."""
    low = name.lower()
    return next((k for k in attrs if str(k).lower() == low), None)


def check_records(records):
    """Raise SizeError unless the lines carry the Routine K inputs."""
    if not records:
        raise SizeError("The layer has no lines.")
    missing = [n for n in REQUIRED if field_name(records[0]["attrs"], n) is None]
    if missing:
        raise SizeError("The layer has no {} field(s). Use the Drainage network layer of "
                        "Illini Subsurface Drain Layout, run with Sizing on."
                        .format(", ".join(missing)))


def pipe_key(attrs, fallback):
    """The pipe a line belongs to: its pipe_id, else the line itself."""
    pid = _get(attrs, "pipe_id")
    return ("pipe", pid) if pid is not None else ("line", fallback)


def pipe_label(attrs):
    return str(_get(attrs, "pipe") or "line {}".format(_get(attrs, "id")))


def layer_spacing(records):
    """Drain spacing (ft) of the network: DRAIN_SPACE, else from D_AREA and FLOW_LENGTH
    (D_AREA = FLOW_LENGTH x spacing / 43560); the most common value, or None."""
    vals = []
    for r in records:
        a = r["attrs"]
        sp = _num(a, "DRAIN_SPACE")
        if sp is None:
            area, fl = _num(a, "D_AREA"), _num(a, "FLOW_LENGTH")
            if area is not None and fl and fl > 50:
                sp = area * 43560.0 / fl
        if sp is not None and sp > 0:
            vals.append(int(math.floor(sp + 0.5)))
    if not vals:
        return None
    return max(set(vals), key=vals.count)


def orders_present(records):
    return sorted({int(_num(r["attrs"], "TILE_ORDER")) for r in records
                   if _num(r["attrs"], "TILE_ORDER") is not None})


def coefficient(rec, mode, single, by_order=None, overrides=None):
    """Drainage coefficient (inch/day) for one line under the chosen mode.

    single: one value for every line ("single"), and the fallback for an order
    missing from by_order ("order"). "pipe": overrides {pipe_key: value} for the
    pipes you set; every other line keeps its current D_COEFF (or single)."""
    a = rec["attrs"]
    if mode == "order":
        o = _num(a, "TILE_ORDER")
        return (by_order or {}).get(int(o) if o is not None else None, single)
    if mode == "pipe":
        k = pipe_key(a, rec.get("fid"))
        if overrides and k in overrides:
            return overrides[k]
        cur = _num(a, "D_COEFF")
        return cur if cur is not None and cur > 0 else single
    return single


def material_of(rec, default=None, sections=None):
    """Pipe type (index into sizing.MATERIALS) of one line: its section setting, else
    `default` if given, else the layer's PIPE_TYPE, else single wall."""
    if sections and rec.get("fid") in sections:
        return sections[rec["fid"]]
    if default is not None:
        return default
    v = _get(rec["attrs"], "PIPE_TYPE")
    if v is not None:
        low = str(v).strip().lower()
        for i, name in enumerate(K.MATERIALS):
            if name.lower() == low:
                return i
    return 0


def pipe_sections(records, key):
    """The lines of one pipe from its downstream end up: [(fid, from station, to
    station)], stations along the pipe from its downstream end in length units."""
    lines = [r for r in records if pipe_key(r["attrs"], r.get("fid")) == key]
    by_to = {}
    for r in lines:
        by_to.setdefault(_get(r["attrs"], "to_node"), []).append(r)
    froms = {_get(r["attrs"], "from_node") for r in lines}
    # the downstream end: a line whose to_node is not the from_node of another line
    ends = [r for r in lines if _get(r["attrs"], "to_node") not in froms] or lines[:1]
    out, used, s = [], set(), 0.0
    cur = ends[0] if ends else None
    while cur is not None and id(cur) not in used:
        used.add(id(cur))
        L = _num(cur["attrs"], "length") or 0.0
        out.append((cur.get("fid"), s, s + L))
        s += L
        nxt = [r for r in by_to.get(_get(cur["attrs"], "from_node"), []) if id(r) not in used]
        cur = nxt[0] if nxt else None
    for r in lines:                          # anything not on the chain, in layer order
        if id(r) not in used:
            L = _num(r["attrs"], "length") or 0.0
            out.append((r.get("fid"), s, s + L))
            s += L
    return out


def resize(records, mode, single, by_order=None, overrides=None, material=None,
           spacing_ft=None, sections=None):
    """New Routine K values for every line.

    Returns (rows, summary). Each row: {"fid", "pipe", "kind", "order", "length",
    "old": {...}, "new": {D_COEFF, CUM_SEG_FLOW, ACTUAL_SIZE, NOMINAL, D_AREA,
    DRAIN_SPACE}, "changed": nominal size changed, "flag": new flag or None if the
    layer has no flag field}. summary: length by nominal size before and after
    (in the layer's length units), lines resized larger/smaller, the spacing."""
    check_records(records)
    if mode not in MODES:
        raise SizeError("Unknown mode {}".format(mode))
    sp = spacing_ft if spacing_ft else layer_spacing(records)
    if not sp:
        raise SizeError("The drain spacing is not known: enter it.")
    rows = []
    row_recs = []
    before, after = {}, {}
    larger = smaller = 0
    for rec in records:
        a = rec["attrs"]
        fl, slope = _num(a, "FLOW_LENGTH"), _num(a, "SIZE_SLOPE")
        dc = coefficient(rec, mode, single, by_order, overrides)
        if fl is None or slope is None or dc is None:
            continue
        mat = material_of(rec, material, sections)
        r = K.size_segment(fl, slope, sp, mat, dc)
        L = _num(a, "length") or 0.0
        old_nom = _num(a, "NOMINAL")
        old = {k: _get(a, k) for k in ("D_COEFF", "CUM_SEG_FLOW", "ACTUAL_SIZE", "NOMINAL",
                                       "PIPE_TYPE")}
        new = {"D_COEFF": round(dc, 4), "D_AREA": round(r["D_AREA"], 4),
               "DRAIN_SPACE": r["drain_space"], "CUM_SEG_FLOW": round(r["CUM_SEG_FLOW"], 6),
               "ACTUAL_SIZE": round(r["ACTUAL_SIZE"], 3) if r["ACTUAL_SIZE"] != float("inf")
               else 9999.0, "NOMINAL": r["NOMINAL"], "PIPE_TYPE": K.MATERIALS[mat]}
        flag = None
        k_flag = field_name(a, "flag")
        if k_flag is not None:
            parts = [p.strip() for p in str(a.get(k_flag) or "").split(";")
                     if p.strip() and p.strip() != MAX_SIZE_FLAG
                     and not p.strip().startswith(K.HELD_FLAG.split("{}")[0])]
            if r["ACTUAL_SIZE"] >= K.AVAILABLE_NOMINAL_SIZES[-1]:
                parts.append(MAX_SIZE_FLAG)
            flag = "; ".join(parts)
        if old_nom is not None:
            before[int(old_nom)] = before.get(int(old_nom), 0.0) + L
            larger += r["NOMINAL"] > old_nom
            smaller += r["NOMINAL"] < old_nom
        after[r["NOMINAL"]] = after.get(r["NOMINAL"], 0.0) + L
        rows.append({"fid": rec.get("fid"), "pipe": pipe_label(a),
                     "kind": str(_get(a, "kind") or ""),
                     "key": pipe_key(a, rec.get("fid")),
                     "order": int(_num(a, "TILE_ORDER")), "length": L, "old": old, "new": new,
                     "changed": old_nom is None or r["NOMINAL"] != int(old_nom), "flag": flag,
                     "material": mat})
        row_recs.append(rec)
    # no pipe smaller than a pipe draining into it
    fr = [_get(rec["attrs"], "from_node") for rec in row_recs]
    to = [_get(rec["attrs"], "to_node") for rec in row_recs]
    if rows and all(v is not None for v in fr + to):
        by_to = {}
        for i, t in enumerate(to):
            by_to.setdefault(t, []).append(i)
        held = K.hold_sizes([by_to.get(f, []) for f in fr], [r["new"]["NOMINAL"] for r in rows])
        for i, (row, h) in enumerate(zip(rows, held)):
            if h is None or h <= row["new"]["NOMINAL"]:
                continue
            L = _num(row_recs[i]["attrs"], "length") or 0.0
            old_n = row["new"]["NOMINAL"]
            after[old_n] -= L
            after[h] = after.get(h, 0.0) + L
            row["new"]["NOMINAL"] = h
            o = _num(row_recs[i]["attrs"], "NOMINAL")
            if o is not None:
                larger += (h > o) - (old_n > o)
                smaller -= (old_n < o) - (h < o)
            row["changed"] = o is None or h != int(o)
            if row["flag"] is not None:
                row["flag"] = "; ".join(p for p in (row["flag"], K.HELD_FLAG.format(h)) if p)
        after = {k: v for k, v in after.items() if v > 1e-9}
    summary = {"before": dict(sorted(before.items())), "after": dict(sorted(after.items())),
               "larger": larger, "smaller": smaller, "spacing": sp, "lines": len(rows)}
    return rows, summary


def row_differs(row):
    """True when writing this row would change the layer."""
    o, n = row["old"], row["new"]
    if row["changed"]:
        return True
    if o["D_COEFF"] is None or abs(float(o["D_COEFF"]) - n["D_COEFF"]) > 1e-9:
        return True
    return o["PIPE_TYPE"] is None or str(o["PIPE_TYPE"]) != n["PIPE_TYPE"]


def changes_for(row, attrs):
    """{layer field name: value} to write for one row (only fields the layer has)."""
    out = {}
    for k, v in row["new"].items():
        name = field_name(attrs, k)
        if name is not None:
            out[name] = v
    if row["flag"] is not None:
        out[field_name(attrs, "flag")] = row["flag"]
    return out
