"""Read a LANDRAIN network file (.NET) and build an Illini network from it (pure Python).

LANDRAIN (G. Sands, P. Petersen and R. Gaddis, 1985; a module of LANDIMPROVE)
saved a drainage network as plain text, in feet, in local field coordinates:

    name                                     e.g. NET4
    xmin xmax ymin ymax                      extent, local feet
    npoints
    x y surface invert flag                  one line per point
    nlinks
    from to flag size_class                  one line per link; points numbered from 1
    ... 16 design settings

The two elevations are the ground surface and the tile invert at the point (they
are equal in a system that has not been designed yet). In a designed system each
link runs from the outlet up: the invert rises from its first point to its second.
size_class is 1 for the smallest pipe; 0 when not sized. The third link value is a
0/1 flag of unknown meaning (all 1 on a short separate line that may be existing
tile); it is kept as LR_FLAG.

The design settings, as far as they are known: 0, an option string (e.g. 101111),
minimum and maximum grade, the lower and upper limits of tile depth, the outlet
invert range, Manning's n for three size ranges (0.015, 0.017, 0.020), the drainage
coefficient (inch/day), 1.4, 0, the drain spacing (ft) and a tolerance. The format
was worked out from Richard Cooke's NetConvert routines and the Site 3 files.
"""
import math
import re

from . import sizing as K

LANDRAIN_SIZES = [3, 4, 5, 6, 8, 10, 12, 15, 18, 24, 36]   # size class 1, 2, ... (inches)


class LandrainError(Exception):
    pass


def parse(text):
    """The contents of a .NET file as a dict."""
    toks = [t for t in re.split(r"[,\s]+", text.strip()) if t != ""]
    try:
        name = toks[0]
        extent = [float(v) for v in toks[1:5]]
        n = int(float(toks[5]))
        pts = []
        k = 6
        for _ in range(n):
            x, y, surf, inv, flag = (float(v) for v in toks[k:k + 5])
            pts.append({"x": x, "y": y, "surface": surf, "invert": inv, "flag": int(flag)})
            k += 5
        nl = int(float(toks[k]))
        k += 1
        links = []
        for _ in range(nl):
            a, b, f, c = (int(float(v)) for v in toks[k:k + 4])
            links.append({"from": a, "to": b, "flag": f, "size_class": c})
            k += 4
    except (IndexError, ValueError) as e:
        raise LandrainError("Not a LANDRAIN network file, or it is cut short ({}).".format(e))
    for l in links:
        if not (1 <= l["from"] <= n and 1 <= l["to"] <= n):
            raise LandrainError("A link refers to point {} of {}.".format(
                max(l["from"], l["to"]), n))
    trailer = toks[k:]
    nums = []
    for t in trailer[2:]:
        try:
            nums.append(float(t))
        except ValueError:
            nums.append(None)

    def num(i):
        return nums[i] if i < len(nums) else None

    settings = {"options": trailer[1] if len(trailer) > 1 else "",
                "min_grade": num(0), "max_grade": num(1),
                "depth_lower": num(2), "depth_upper": num(3),
                "outlet_low": num(4), "outlet_high": num(5),
                "manning_n": [v for v in (num(6), num(7), num(8)) if v is not None],
                "drainage_coeff": num(9), "spacing": num(12)}
    designed = any(abs(p["surface"] - p["invert"]) > 0.01 for p in pts)
    sized = any(l["size_class"] > 0 for l in links)
    return {"name": name, "extent": extent, "points": pts, "links": links,
            "trailer": trailer, "settings": settings, "designed": designed, "sized": sized}


def place(p, origin=(0.0, 0.0), local_origin=(0.0, 0.0), rotation_deg=0.0, scale=1.0):
    """Map position of a local point: the local origin goes to `origin`, the local
    axes are turned counter-clockwise by rotation_deg, and feet are multiplied by
    scale (map units per foot)."""
    dx, dy = p["x"] - local_origin[0], p["y"] - local_origin[1]
    c, s = math.cos(math.radians(rotation_deg)), math.sin(math.radians(rotation_deg))
    return (origin[0] + scale * (c * dx - s * dy), origin[1] + scale * (s * dx + c * dy))


def _outlet(net, outlet_xy=None, xy=None):
    """Index (0-based) of the outlet point: the end point nearest outlet_xy if given;
    else, in a designed system, the point no link runs to (the start of the links);
    else the end point with the lowest invert (or ground)."""
    pts, links = net["points"], net["links"]
    deg = [0] * len(pts)
    for l in links:
        deg[l["from"] - 1] += 1
        deg[l["to"] - 1] += 1
    ends = [i for i, d in enumerate(deg) if d == 1] or [i for i, d in enumerate(deg) if d]
    if outlet_xy is not None and xy is not None:
        return min(ends, key=lambda i: math.hypot(xy[i][0] - outlet_xy[0],
                                                  xy[i][1] - outlet_xy[1]))
    if net["designed"]:
        tos = {l["to"] - 1 for l in links}
        starts = [i for i in ends if i not in tos and deg[i]]
        if len(starts) == 1:
            return starts[0]
    return min(ends, key=lambda i: (pts[i]["invert"], pts[i]["surface"]))


def build(net, origin=(0.0, 0.0), local_origin=(0.0, 0.0), rotation_deg=0.0, scale=1.0,
          sizes=None, outlet_xy=None, undesigned_depth=None, material=0):
    """Illini network lines from a parsed .NET file.

    Returns {"lines": [{"coords": [(x, y)...] upstream -> downstream, "tile_z", "ground_m",
    "attrs": {...}}], "outlet": index, "notes": [...]}. Elevations and lengths are in
    feet; coordinates in map units (scale x feet from the origin).
    """
    sizes = sizes or LANDRAIN_SIZES
    pts, links = net["points"], net["links"]
    s = net["settings"]
    xy = [place(p, origin, local_origin, rotation_deg, scale) for p in pts]
    notes = []
    def tile_of(i):
        return pts[i]["invert"]

    # undirected adjacency, then orient everything from the outlet
    adj = {}
    for li, l in enumerate(links):
        a, b = l["from"] - 1, l["to"] - 1
        adj.setdefault(a, []).append((b, li))
        adj.setdefault(b, []).append((a, li))
    out = _outlet(net, outlet_xy, xy)
    parent = {out: None}
    plink = {}
    order = [out]
    roots = [out]
    while True:
        for v in order:                               # breadth first from each outlet
            for w, li in adj.get(v, []):
                if w not in parent:
                    parent[w] = v
                    plink[w] = li
                    order.append(w)
        rest = [v for v in adj if v not in parent]
        if not rest:
            break
        # a separate part (not joined to the outlet): its own outlet, its lowest end
        ends = [v for v in rest if len(adj[v]) == 1] or rest
        r = min(ends, key=lambda i: (tile_of(i), pts[i]["surface"]))
        parent[r] = None
        order.append(r)
        roots.append(r)
    if len(roots) > 1:
        notes.append("{} separate part(s) not joined to the outlet were imported as systems "
                     "of their own, each with its outlet at its lowest end.".format(
                         len(roots) - 1))
    if net["designed"]:
        against = sum(1 for w, li in plink.items() if links[li]["from"] - 1 != parent[w])
        if against:
            notes.append("{} link(s) ran away from the outlet in the file; they were "
                         "turned round.".format(against))
    children = {}
    for w in order[1:]:
        children.setdefault(parent[w], []).append(w)

    def seg(a, b):
        return math.hypot(pts[b]["x"] - pts[a]["x"], pts[b]["y"] - pts[a]["y"])

    up_len = {}
    for v in reversed(order):                        # tile length upstream of each point
        up_len[v] = sum(seg(v, c) + up_len[c] for c in children.get(v, []))
    # tile elevations, part by part: a part whose inverts all equal the ground has
    # not been designed, and its tile is put a set depth below the ground
    root_of = {}
    for v in order:
        root_of[v] = v if parent[v] is None else root_of[parent[v]]
    part_designed = {}
    for v in order:
        r = root_of[v]
        part_designed[r] = part_designed.get(r, False) or \
            abs(pts[v]["surface"] - pts[v]["invert"]) > 0.01
    d = undesigned_depth if undesigned_depth is not None else (s["depth_lower"] or 0.0)
    tile = [p["invert"] for p in pts]
    undesigned = [r for r, ok in part_designed.items() if not ok]
    for v in order:
        if not part_designed[root_of[v]]:
            tile[v] = pts[v]["surface"] - d
    if undesigned:
        notes.append("{} not designed in LANDRAIN (inverts at the ground): the tile is "
                     "placed {:g} ft below the ground at every point there.".format(
                         "The system is" if len(undesigned) == len(part_designed)
                         else "{} part(s) are".format(len(undesigned)), d))

    def size_of(w):
        c = links[plink[w]]["size_class"]
        return sizes[c - 1] if 1 <= c <= len(sizes) else None

    # pipes: from the outlet up, each pipe carries on along the branch with the
    # most tile upstream; the other branches start pipes of their own
    pipes = []
    stack = [(None, r, None) for r in reversed(roots)]   # (junction, first point, host pipe)
    while stack:
        junc, first, host = stack.pop()
        path = ([junc] if junc is not None else []) + [first]
        v = first
        me = len(pipes)                            # this pipe's index once added
        while children.get(v):
            kids = sorted(children[v], key=lambda c: -(up_len[c] + seg(v, c)))
            for c in kids[1:]:
                stack.append((v, c, me))
            path.append(kids[0])
            v = kids[0]
        if len(path) > 1:                          # downstream -> upstream
            pipes.append({"path": path, "host": host})
    # kinds and names
    has_trib = {i: False for i in range(len(pipes))}
    for p in pipes:
        if p["host"] is not None:
            has_trib[p["host"]] = True
    names, counts = [], {"main": 0, "submain": 0, "lateral": 0}
    for i, p in enumerate(pipes):
        kind = ("main" if p["host"] is None else "submain" if has_trib[i] else "lateral")
        counts[kind] += 1
        p["kind"] = kind
        names.append("main" if kind == "main" and counts["main"] == 1 else
                     "{} {}".format(kind, counts[kind]))
    # junction points (where pipes meet) and size changes cut pipes into lines
    junction = set()
    for p in pipes:
        if p["host"] is not None:
            junction.add(p["path"][0])
    lines = []
    line_of_link = {}                              # upstream point of a link -> line index
    for pi, p in enumerate(pipes):
        path = p["path"]                           # downstream -> upstream
        cuts = [0]
        for k in range(1, len(path) - 1):
            v = path[k]
            if v in junction or size_of(path[k]) != size_of(path[k + 1]):
                cuts.append(k)
        cuts.append(len(path) - 1)
        for a, b in zip(cuts[:-1], cuts[1:]):
            seq = path[a:b + 1][::-1]               # upstream -> downstream
            lines.append({"pipe": pi, "pts": seq})
            for w in path[a + 1:b + 1]:
                line_of_link[w] = len(lines) - 1
    # build attributes
    dc = s["drainage_coeff"] if s["drainage_coeff"] else K.DEFAULT_COEFF
    spacing = s["spacing"] if s["spacing"] else None
    nfirst = {}
    result = []
    for k, L in enumerate(lines):
        seq = L["pts"]
        p = pipes[L["pipe"]]
        us, ds = seq[0], seq[-1]
        length = sum(seg(a, b) for a, b in zip(seq[:-1], seq[1:]))
        ds_line = line_of_link.get(ds)             # the line the downstream end flows into
        tz = [tile[v] for v in seq]
        gm = [pts[v]["surface"] for v in seq]
        dep = [g - t for g, t in zip(gm, tz)]
        fall = tz[0] - tz[-1]
        slope = fall / length if length > 0 else 0.0
        nom = size_of(seq[0])                      # all links of a line share a size
        flags = {links[plink[v]]["flag"] for v in seq if v in plink and v != ds}
        bc = ["Imported from LANDRAIN"]
        steps = [(tz[i] - tz[i + 1]) / seg(seq[i], seq[i + 1])
                 for i in range(len(seq) - 1) if seg(seq[i], seq[i + 1]) > 0]
        worst = min(steps) if steps else None
        if worst is not None and worst < 0.00005:
            bc.append("Reverse or zero grade ({:.2f}%): tile cannot drain".format(worst * 100))
        if not part_designed[root_of[us]]:
            bc.append("Not designed in LANDRAIN: tile {:g} ft below ground".format(d))
        a = {"id": k, "pipe_id": L["pipe"] + 1, "kind": p["kind"], "pipe": names[L["pipe"]],
             "from_node": us + 1, "to_node": ds + 1,
             "ds_link": ds_line if ds_line is not None else -1,
             "length": round(length, 2), "TileLength": int(5 * round(length / 5.0)),
             "ground_us": round(gm[0], 3), "ground_ds": round(gm[-1], 3),
             "terrain_grade_pct": round((gm[0] - gm[-1]) / length * 100, 3) if length else 0,
             "invert_us": round(tz[0], 3), "invert_ds": round(tz[-1], 3),
             "design_grade_pct": round(slope * 100, 4),
             "max_depth": round(max(dep), 3), "min_depth": round(min(dep), 3),
             "depth_ds": round(dep[-1], 3),
             "upstream_length": round(up_len[us] + length, 1),
             "ElevDepth1": round(tz[0], 3), "BuryDepth1": round(dep[0], 3),
             "ElevDepth2": round(tz[-1], 3), "BuryDepth2": round(dep[-1], 3),
             "InSlope": round(slope, 6), "OutSlope": round(slope, 6),
             "BURY_CHECK": "; ".join(bc), "NOMINAL": nom,
             "PIPE_TYPE": K.MATERIALS[material], "D_COEFF": round(dc, 4),
             "DRAIN_SPACE": int(math.floor(spacing + 0.5)) if spacing else None,
             "SIZE_SLOPE": round(max(slope, 0.0), 6), "flag": "",
             "LR_FLAG": max(flags) if flags else 0}
        result.append({"coords": [xy[v] for v in seq], "tile_z": tz, "ground_m": gm,
                        "attrs": a, "_us": us, "_ds": ds})
    # Strahler order and the tile length draining through each line (Routine K)
    ups = {}
    for L in result:
        d = L["attrs"]["ds_link"]
        if d is not None and d >= 0:
            ups.setdefault(d, []).append(L)
    done = {}

    def visit(i):
        if i in done:
            return done[i]
        L = result[i]
        inc = ups.get(i, [])
        orders, flow = [], L["attrs"]["length"]
        for u in inc:
            o, f = visit(u["attrs"]["id"])
            orders.append(o)
            flow += f
        if not orders:
            o = 1
        else:
            m = max(orders)
            o = m + 1 if orders.count(m) >= 2 else m
        done[i] = (o, flow)
        return done[i]

    for L in result:
        o, f = visit(L["attrs"]["id"])
        a = L["attrs"]
        a["TILE_ORDER"] = o
        a["FLOW_LENGTH"] = round(f, 2)
        if spacing:
            r = K.size_segment(f, a["SIZE_SLOPE"], spacing, material, a["D_COEFF"])
            a["D_AREA"] = round(r["D_AREA"], 4)
            a["CUM_SEG_FLOW"] = round(r["CUM_SEG_FLOW"], 6)
            a["ACTUAL_SIZE"] = (round(r["ACTUAL_SIZE"], 3)
                                if r["ACTUAL_SIZE"] != float("inf") else 9999.0)
            if a["NOMINAL"] is None and part_designed[root_of[L["_us"]]]:
                a["NOMINAL"] = r["NOMINAL"]
                a["BURY_CHECK"] += "; size worked out (none in the file)"
        a["drained_area"] = round(f * spacing / 43560.0, 3) if spacing else None
    return {"lines": result, "outlet": out, "notes": notes, "pipes": len(pipes),
            "counts": counts}
