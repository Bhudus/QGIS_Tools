"""Bill of materials and cost estimate for an Illini tile network (pure Python).

Quantities come from the network itself:
- pipe: length by pipe type and nominal size, plus a waste allowance;
- at each junction, a tee or a wye for each pipe that joins (by the angle it
  joins at), sized run x branch, and a reducer where the run changes size;
- an end cap at the upper end of every pipe;
- at the outlet, a rigid outlet pipe and a rodent guard of the outlet size;
- structures and installation from the quantities and rates you enter.

Prices come from an editable table of rows {category, item, size, unit, price,
practice}; see lookup() for how a row is found for each item.
"""
import csv
import io
import math

WYE_MAX_ANGLE = 67.5          # a branch joining at up to this angle is a wye, else a tee
ELBOW_MIN_ANGLE = 22.5        # a pipe carried on by another turning more than this: elbow

CATEGORIES = ("Pipe", "Fittings", "Outlet", "Structures", "Installation", "Other")
DEFAULT_STRUCTURES = ("Inspection port", "Water control structure", "Surface inlet")
PRACTICE = {"Pipe": "606", "Fittings": "606", "Outlet": "606", "Installation": "606",
            "Structures": "587", "Other": ""}
PRICE_FIELDS = ("category", "item", "size", "unit", "price", "practice")


def _get(attrs, name):
    if name in attrs:
        v = attrs[name]
    else:
        low = name.lower()
        v = next((val for k, val in attrs.items() if str(k).lower() == low), None)
    if v is None:
        return None
    try:
        if isinstance(v, float) and math.isnan(v):
            return None
    except TypeError:
        pass
    return v


def _num(attrs, name):
    v = _get(attrs, name)
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _unit(dx, dy):
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-12 else None


def _angle(u, w):
    """Angle between two directions, degrees (0 = same direction)."""
    c = max(-1.0, min(1.0, u[0] * w[0] + u[1] * w[1]))
    return math.degrees(math.acos(c))


def size_key(*sizes):
    return "x".join(str(int(s)) for s in sizes)


def size_text(key):
    if key in ("*", "", None):
        return ""
    return " × ".join(key.split("x")) + " in"


# ---------------------------------------------------------------- quantities
def quantities(lines, sizes=None, waste_pct=0.0, outlet_length=20.0, rodent_guard=True,
               structures=None, installation=True, length_unit="ft"):
    """Bill of materials quantities.

    lines: [{"fid", "attrs", "xy": [(x, y), ...]}] drawn upstream -> downstream,
           with attrs from_node, to_node, pipe_id, NOMINAL, PIPE_TYPE, length.
    sizes: optional {fid: (nominal, pipe type)} that replace the layer's values
           (the Tile Size Calculator's preview).
    structures: {item: quantity} entered by hand.
    Returns {"items": [{category, item, size, unit, qty, note}], "fittings":
    [{x, y, type, size, label}], "outlet": {...} or None}.
    """
    sizes = sizes or {}
    recs = []
    for r in lines:
        a = r["attrs"]
        nom, typ = sizes.get(r.get("fid"), (None, None))
        nom = nom if nom is not None else _num(a, "NOMINAL")
        typ = typ if typ is not None else (_get(a, "PIPE_TYPE") or "Single Wall")
        recs.append({"fid": r.get("fid"), "a": a, "xy": r.get("xy") or [],
                     "nom": int(nom) if nom is not None else None, "type": str(typ),
                     "from": _get(a, "from_node"), "to": _get(a, "to_node"),
                     "pipe": _get(a, "pipe_id"), "len": _num(a, "length") or 0.0})
    items = {}

    def add(cat, item, size, unit, qty, note=""):
        k = (cat, item, size, unit)
        if k not in items:
            items[k] = {"category": cat, "item": item, "size": size, "unit": unit,
                        "qty": 0.0, "note": note}
        items[k]["qty"] += qty

    # pipe by type and size, with waste
    for r in recs:
        if r["nom"] is None:
            continue
        add("Pipe", r["type"], size_key(r["nom"]), length_unit,
            r["len"] * (1.0 + waste_pct / 100.0),
            "includes {:g}% waste".format(waste_pct) if waste_pct else "")
        if installation:
            add("Installation", "Installation", size_key(r["nom"]), length_unit, r["len"])

    out_by = {}
    in_by = {}
    for r in recs:
        out_by.setdefault(r["from"], []).append(r)
        in_by.setdefault(r["to"], []).append(r)
    fittings = []

    def first_dir(r):
        xy = r["xy"]
        return _unit(xy[1][0] - xy[0][0], xy[1][1] - xy[0][1]) if len(xy) >= 2 else None

    def last_dir(r):
        xy = r["xy"]
        return _unit(xy[-1][0] - xy[-2][0], xy[-1][1] - xy[-2][1]) if len(xy) >= 2 else None

    def point(r, end):
        xy = r["xy"]
        return xy[-1] if (xy and end == "to") else (xy[0] if xy else (None, None))

    nodes = set(out_by) | set(in_by)
    outlet = None
    for node in nodes:
        outs, ins = out_by.get(node, []), in_by.get(node, [])
        if not ins and outs:
            # upper end of a pipe
            for d in outs:
                if d["nom"] is not None:
                    add("Fittings", "End cap", size_key(d["nom"]), "each", 1)
                    x, y = point(d, "from")
                    fittings.append({"x": x, "y": y, "type": "End cap",
                                     "size": size_key(d["nom"])})
            continue
        if not outs:
            # the outlet: the line(s) that end here go to the ditch
            for u in ins:
                outlet = u if outlet is None or (u["nom"] or 0) > (outlet["nom"] or 0) \
                    else outlet
            continue
        d = max(outs, key=lambda r: r["nom"] or 0)            # the run, downstream
        x, y = point(d, "from")
        run = d["nom"]
        if run is None:
            continue
        host = [u for u in ins if u["pipe"] is not None and u["pipe"] == d["pipe"]]
        if host:
            host = host[0]
        else:
            # the pipe starts here: the straightest pipe joining carries on into it
            def turn(u):
                a1, a2 = last_dir(u), first_dir(d)
                return _angle(a1, a2) if a1 and a2 else 0.0
            host = min(ins, key=turn)
            if turn(host) > ELBOW_MIN_ANGLE:
                key = size_key(min(run, host["nom"] or run))
                add("Fittings", "Elbow", key, "each", 1)
                fittings.append({"x": x, "y": y, "type": "Elbow", "size": key,
                                 "angle": turn(host)})
        for t in ins:
            if t is host:
                continue
            u1, u2 = last_dir(t), first_dir(d)
            ang = _angle(u1, u2) if u1 and u2 else 90.0
            kind = "Wye" if ang <= WYE_MAX_ANGLE else "Tee"
            br = t["nom"] or run
            key = size_key(run, br)
            add("Fittings", kind, key, "each", 1)
            fittings.append({"x": x, "y": y, "type": kind, "size": key, "angle": ang})
        if host["nom"] is not None and host["nom"] != run:
            key = size_key(max(run, host["nom"]), min(run, host["nom"]))
            add("Fittings", "Reducer", key, "each", 1)
            fittings.append({"x": x, "y": y, "type": "Reducer", "size": key})

    out_info = None
    if outlet is not None and outlet["nom"] is not None:
        key = size_key(outlet["nom"])
        x, y = point(outlet, "to")
        out_info = {"x": x, "y": y, "size": key}
        if outlet_length > 0:
            add("Outlet", "Outlet pipe (rigid)", key, length_unit, outlet_length)
            fittings.append({"x": x, "y": y, "type": "Outlet pipe", "size": key})
        if rodent_guard:
            add("Outlet", "Rodent guard", key, "each", 1)
    for name, qty in (structures or {}).items():
        if qty:
            add("Structures", name, "*", "each", qty)
    order = {c: i for i, c in enumerate(CATEGORIES)}
    out = sorted(items.values(), key=lambda it: (
        order.get(it["category"], 99), it["item"],
        [int(s) for s in it["size"].split("x")] if it["size"] not in ("*", "") else [0]))
    for f in fittings:
        f["label"] = "{} {}".format(f["type"], size_text(f["size"])).strip()
    return {"items": out, "fittings": fittings, "outlet": out_info}


# ---------------------------------------------------------------- prices
def lookup(prices, category, item, size):
    """The price row for an item: same category and item (any case), and size
    exactly, else the first (run) size of a fitting, else size "*"; None if absent."""
    cands = [p for p in prices if str(p.get("category", "")).lower() == category.lower()
             and str(p.get("item", "")).strip().lower() == item.strip().lower()]
    tries = [size]
    if "x" in size:
        tries.append(size.split("x")[0])
    tries.append("*")
    for s in tries:
        for p in cands:
            if str(p.get("size", "")).strip().replace(" ", "").lower() == s:
                return p
    return None


def missing_rows(prices, items):
    """Price rows to add (blank price) so every item in the bill has a row."""
    out, seen = [], set()
    for it in items:
        if lookup(prices, it["category"], it["item"], it["size"]) is None:
            k = (it["category"], it["item"], it["size"])
            if k not in seen:
                seen.add(k)
                out.append({"category": it["category"], "item": it["item"],
                            "size": it["size"], "unit": it["unit"], "price": None,
                            "practice": PRACTICE.get(it["category"], "")})
    return out


def default_prices(items=None):
    """A starting price table with no prices: the rows the bill needs, plus the
    standard structures and a mobilization line."""
    rows = [{"category": "Structures", "item": s, "size": "*", "unit": "each",
             "price": None, "practice": "587" if "control" in s else "606"}
            for s in DEFAULT_STRUCTURES]
    rows.append({"category": "Other", "item": "Mobilization", "size": "*", "unit": "lump sum",
                 "price": None, "practice": ""})
    if items:
        rows += missing_rows(rows, items)
    return rows


def estimate(items, prices, markup_pct=0.0, tax_pct=0.0, extra=None):
    """Priced lines and totals.

    extra: {item: quantity} for "Other" rows priced per lump sum or each (e.g.
    Mobilization: 1). Returns {"lines": [... + price, amount, practice, priced],
    "subtotal", "markup", "tax", "total", "by_practice", "unpriced"}."""
    lines = []
    for it in list(items) + [{"category": "Other", "item": k, "size": "*", "unit": "",
                              "qty": q, "note": ""} for k, q in (extra or {}).items() if q]:
        p = lookup(prices, it["category"], it["item"], it["size"])
        price = None
        if p is not None and p.get("price") not in (None, ""):
            try:
                price = float(p["price"])
            except (TypeError, ValueError):
                price = None
        unit = it["unit"] or (p.get("unit") if p else "")
        amount = None if price is None else round(price * it["qty"], 2)
        lines.append(dict(it, unit=unit, price=price, amount=amount,
                          practice=(p.get("practice") if p else "") or
                          PRACTICE.get(it["category"], ""), priced=price is not None))
    sub = round(sum(l["amount"] or 0.0 for l in lines), 2)
    markup = round(sub * markup_pct / 100.0, 2)
    tax = round((sub + markup) * tax_pct / 100.0, 2)
    by_p = {}
    for l in lines:
        by_p.setdefault(l["practice"] or "", 0.0)
        by_p[l["practice"] or ""] += l["amount"] or 0.0
    return {"lines": lines, "subtotal": sub, "markup": markup, "tax": tax,
            "total": round(sub + markup + tax, 2),
            "by_practice": {k: round(v, 2) for k, v in sorted(by_p.items())},
            "unpriced": sum(1 for l in lines if not l["priced"])}


def prices_to_csv(prices):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=PRICE_FIELDS, extrasaction="ignore")
    w.writeheader()
    for p in prices:
        w.writerow({k: ("" if p.get(k) is None else p.get(k)) for k in PRICE_FIELDS})
    return buf.getvalue()


def prices_from_csv(text):
    """Price rows from CSV text with (at least) category, item, size and price columns."""
    rows = []
    rd = csv.DictReader(io.StringIO(text))
    fields = {f.strip().lower(): f for f in (rd.fieldnames or [])}
    need = ("category", "item", "price")
    if not all(n in fields for n in need):
        raise ValueError("The price file needs columns category, item, size, unit, price "
                         "(and optionally practice).")
    for r in rd:
        row = {k: (r.get(fields[k]) or "").strip() if k in fields else "" for k in PRICE_FIELDS}
        if not row["category"] and not row["item"]:
            continue
        row["size"] = (row["size"] or "*").replace(" ", "").replace("×", "x").lower()
        row["size"] = row["size"].replace("in", "").replace('"', "") or "*"
        try:
            row["price"] = float(str(row["price"]).replace("$", "").replace(",", "")) \
                if row["price"] != "" else None
        except ValueError:
            row["price"] = None
        rows.append(row)
    return rows


def bill_csv(est, header=None):
    """The priced bill as CSV text (with optional header lines)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    for k, v in (header or {}).items():
        w.writerow([k, v])
    if header:
        w.writerow([])
    w.writerow(["Category", "Item", "Size", "Quantity", "Unit", "Unit price", "Amount",
                "Practice", "Note"])
    for l in est["lines"]:
        w.writerow([l["category"], l["item"], size_text(l["size"]), round(l["qty"], 2),
                    l["unit"], "" if l["price"] is None else "{:.2f}".format(l["price"]),
                    "" if l["amount"] is None else "{:.2f}".format(l["amount"]),
                    l["practice"], l.get("note", "")])
    w.writerow([])
    for k in ("subtotal", "markup", "tax", "total"):
        w.writerow(["", k.capitalize(), "", "", "", "", "{:.2f}".format(est[k])])
    return buf.getvalue()
