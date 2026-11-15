"""Build a tile profile for one pipe of a drainage network (pure Python, no QGIS).

A pipe (a lateral, submain or main) is every line in the network that shares
its pipe_id; the lines are chained from_node -> to_node in the direction of
flow. Stations are measured from the pipe's downstream end (station 0 at the
junction or outlet) back up the pipe.

Elevations come from, in order of preference:
- tile: the Z value of each vertex (Illini Subsurface Drain Layout 0.13+),
  else the line's end fields (ElevDepth1/ElevDepth2, or invert_us/invert_ds),
  straight between the two ends;
- ground: a DEM sampled along the pipe, else the M value of each vertex,
  else the line's end fields (ground_us/ground_ds).
"""
import math

TILE_END_FIELDS = (("ElevDepth1", "ElevDepth2"), ("invert_us", "invert_ds"))
GROUND_END_FIELDS = (("ground_us", "ground_ds"),)


class ProfileError(Exception):
    pass


def _get(attrs, name):
    """Case-insensitive attribute lookup; None when missing or NULL."""
    if name in attrs:
        v = attrs[name]
    else:
        low = name.lower()
        v = None
        for k, val in attrs.items():
            if str(k).lower() == low:
                v = val
                break
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


def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def order_pipe(lines):
    """Order a pipe's lines from upstream to downstream by their nodes."""
    if len(lines) <= 1:
        return list(lines)
    have_nodes = all(_get(l["attrs"], "from_node") is not None
                     and _get(l["attrs"], "to_node") is not None for l in lines)
    if not have_nodes:
        return list(lines)
    by_from = {}
    to_nodes = set()
    for l in lines:
        by_from.setdefault(_get(l["attrs"], "from_node"), []).append(l)
        to_nodes.add(_get(l["attrs"], "to_node"))
    starts = [l for l in lines if _get(l["attrs"], "from_node") not in to_nodes] or lines[:1]
    ordered, used = [], set()
    for start in starts:
        cur = start
        while cur is not None and id(cur) not in used:
            ordered.append(cur)
            used.add(id(cur))
            nxt = [l for l in by_from.get(_get(cur["attrs"], "to_node"), []) if id(l) not in used]
            cur = nxt[0] if nxt else None
    ordered += [l for l in lines if id(l) not in used]
    return ordered


def build_profile(lines, all_lines=None, ground_sampler=None, sample_step=None, xy_scale=1.0):
    """Profile of one pipe.

    lines: the pipe's lines, each {"attrs": {...}, "xy": [(x, y), ...],
           "z": [..] or None, "m": [..] or None}, each drawn upstream -> downstream.
    all_lines: every line of the network, to find the pipes joining this one.
    ground_sampler: optional f(x, y) -> elevation or None (a DEM), x, y in map units.
    sample_step: spacing of DEM samples along the pipe (map units).
    xy_scale: profile length units per map unit (e.g. 3.2808 for feet on a metre CRS).
    """
    lines = order_pipe([l for l in lines if len(l["xy"]) >= 2])
    if not lines:
        raise ProfileError("The selected line has no geometry.")

    # walk the pipe upstream -> downstream
    tile_pts, ground_pts, verts, joins, tile_xy = [], [], [], [], []
    path = []                     # (s, x, y) along the pipe, for DEM sampling
    tile_source = ground_source = None
    s0 = 0.0
    for k, l in enumerate(lines):
        a, xy = l["attrs"], l["xy"]
        seg = [0.0]
        for i in range(1, len(xy)):
            seg.append(seg[-1] + _dist(xy[i - 1], xy[i]))
        L = seg[-1]
        for i, p in enumerate(xy):
            if i == 0 and path:
                continue
            path.append((s0 + seg[i], p[0], p[1]))

        z = l.get("z")
        if z is not None and len(z) == len(xy) and all(v is not None and v == v for v in z):
            tile_source = tile_source or "vertices"
            pts = [(s0 + seg[i], float(z[i])) for i in range(len(xy))]
            pxy = list(xy)
        else:
            ends = None
            for f1, f2 in TILE_END_FIELDS:
                e1, e2 = _num(a, f1), _num(a, f2)
                if e1 is not None and e2 is not None:
                    ends = (e1, e2)
                    break
            if ends is None:
                raise ProfileError("The layer has no tile elevations: it needs Z values or "
                                   "ElevDepth1/ElevDepth2 (or invert_us/invert_ds) fields.")
            tile_source = tile_source or "line ends"
            pts = [(s0, ends[0]), (s0 + L, ends[1])]
            pxy = [xy[0], xy[-1]]
        tile_pts += pts if not tile_pts else pts[1:]
        tile_xy += pxy if not tile_xy else pxy[1:]
        verts += [p[0] for p in pts]

        m = l.get("m")
        if m is not None and len(m) == len(xy) and all(v is not None and v == v for v in m):
            gp = [(s0 + seg[i], float(m[i])) for i in range(len(xy))]
            ground_source = ground_source or "vertices"
        else:
            g1, g2 = _num(a, "ground_us"), _num(a, "ground_ds")
            gp = [(s0, g1), (s0 + L, g2)] if g1 is not None and g2 is not None else []
            if gp:
                ground_source = ground_source or "line ends"
        ground_pts += gp if not ground_pts else gp[1:]

        if k > 0:
            joins.append((s0, _get(a, "from_node")))
        s0 += L
    total = s0
    end_node = _get(lines[-1]["attrs"], "to_node")

    if ground_sampler is not None:
        step = sample_step or max(total / 400.0, 1e-6)
        samples = []
        for (sa, xa, ya), (sb, xb, yb) in zip(path[:-1], path[1:]):
            n = max(1, int(math.ceil((sb - sa) / step)))
            for j in range(n + (1 if (sb == path[-1][0]) else 0)):
                t = j / n
                v = ground_sampler(xa + t * (xb - xa), ya + t * (yb - ya))
                if v is not None and v == v:
                    samples.append((sa + t * (sb - sa), float(v)))
        if len(samples) >= 2:
            ground_pts = samples
            ground_source = "DEM"
    if len(ground_pts) < 2:
        raise ProfileError("No ground elevations: the layer has no M values or "
                           "ground_us/ground_ds fields, and no DEM was chosen.")

    # stations from the downstream end, in profile units
    k_ = float(xy_scale)

    def flip(pts):
        return sorted((((total - s) * k_, v) for s, v in pts), key=lambda p: p[0])

    # pipes that join this one, and where
    tributaries = []
    if all_lines is not None:
        pipe_ids = {_get(l["attrs"], "pipe_id") for l in lines}
        nodes = {node: s for s, node in joins}
        for l in all_lines:
            a = l["attrs"]
            if _get(a, "pipe_id") in pipe_ids:
                continue
            to = _get(a, "to_node")
            if to in nodes:
                tributaries.append(((total - nodes[to]) * k_,
                                    str(_get(a, "pipe") or _get(a, "id"))))
    first = lines[0]["attrs"]
    sizes = sorted({int(_num(l["attrs"], "NOMINAL")) for l in lines
                    if _num(l["attrs"], "NOMINAL") is not None})
    name = _get(first, "pipe") or ("line {}".format(_get(first, "id"))
                                   if _get(first, "id") is not None else "selected line")
    return {
        "name": str(name),
        "kind": str(_get(first, "kind") or ""),
        "length": total * k_,
        "tile": flip(tile_pts),
        # map position of each tile point, in the order of "tile"
        "tile_xy": [xy for _, xy in sorted((((total - s) * k_, xy)
                                            for (s, _), xy in zip(tile_pts, tile_xy)),
                                           key=lambda q: q[0])],
        "ground": flip(ground_pts),
        "grade_breaks": sorted({round((total - s) * k_, 6) for s in verts}),
        "junctions": sorted({round((total - s) * k_, 6) for s, _ in joins}),
        "tributaries": sorted(tributaries),
        "nominal_sizes": sizes,
        "flows_to": end_node,
        "tile_source": tile_source,
        "ground_source": ground_source,
        "line_ids": [_get(l["attrs"], "id") for l in lines],
        # map position along the pipe: (station from the downstream end, x, y)
        "path": sorted(((total - s) * k_, x, y) for s, x, y in path),
    }


def interp(pts, s):
    """Linear interpolation in a sorted [(station, value)] list."""
    if not pts:
        return None
    if s <= pts[0][0]:
        return pts[0][1]
    if s >= pts[-1][0]:
        return pts[-1][1]
    lo, hi = 0, len(pts) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if pts[mid][0] <= s:
            lo = mid
        else:
            hi = mid
    (s1, v1), (s2, v2) = pts[lo], pts[hi]
    if s2 == s1:
        return v2
    return v1 + (v2 - v1) * (s - s1) / (s2 - s1)


def depth_lines(profile, depths):
    """Ground minus each depth: {label: [(station, elevation)]}."""
    out = {}
    for key, d in depths.items():
        out[key] = [(s, g - d) for s, g in profile["ground"]]
    return out


def depth_problems(profile, abs_upper, abs_lower, tol=0.005):
    """Station ranges where the tile is shallower than abs_upper or deeper than abs_lower."""
    stations = sorted({s for s, _ in profile["tile"]} | {s for s, _ in profile["ground"]})
    bad = []
    for s in stations:
        d = interp(profile["ground"], s) - interp(profile["tile"], s)
        if d < abs_upper - tol or d > abs_lower + tol:
            bad.append(s)
    return bad


def table_rows(profile, step=None):
    """Rows for CSV export: station, ground, tile, depth."""
    stations = sorted({s for s, _ in profile["tile"]} | {s for s, _ in profile["ground"]})
    if step:
        n = int(profile["length"] // step)
        stations = sorted(set(stations) | {i * step for i in range(n + 1)})
    rows = []
    for s in stations:
        g = interp(profile["ground"], s)
        t = interp(profile["tile"], s)
        rows.append((s, g, t, g - t))
    return rows


# fields the network offset changes: tile elevations go down, depths go up
TILE_ELEV_FIELDS = ("invert_us", "invert_ds", "ElevDepth1", "ElevDepth2")
DEPTH_FIELDS = ("min_depth", "max_depth", "depth_ds", "BuryDepth1", "BuryDepth2")


def node_key(x, y):
    """Key of a tile point by its map position (shared by the lines that meet there)."""
    return (round(float(x), 3), round(float(y), 3))


def offset_profile(profile, offset, node_offsets=None):
    """The profile with every tile elevation lowered by offset (negative raises),
    and each tile point in node_offsets {node_key: amount} lowered by its amount too."""
    node_offsets = node_offsets or {}
    pts = profile["tile"]
    xy = profile.get("tile_xy") or [None] * len(pts)
    each = [node_offsets.get(node_key(*q), 0.0) if q is not None else 0.0 for q in xy]
    if not offset and not any(each):
        return profile
    p = dict(profile)
    p["tile"] = [(s, v - offset - e) for (s, v), e in zip(pts, each)]
    p["offset"] = offset
    p["node_offset"] = each
    return p


FLAT_SLOPE = 0.00005      # |slope| below this (0.005%) is flat


def adverse_sections(profile, flat=FLAT_SLOPE):
    """Tile sections that are flat or slope the wrong way (rising toward the outlet):
    [(start station, end station, slope)], slope positive when falling to station 0."""
    out = []
    pts = profile["tile"]
    for (s1, t1), (s2, t2) in zip(pts[:-1], pts[1:]):
        if s2 - s1 <= 1e-9:
            continue
        slope = (t2 - t1) / (s2 - s1)
        if slope < flat:
            out.append((s1, s2, slope))
    return out


def offset_changes(attrs, offset, ndigits=4):
    """{field: new value} for one line of the network lowered by offset.

    Tile elevation fields drop by offset and depth fields grow by it; missing
    or NULL fields are left alone. Field names match case-insensitively; the
    returned keys are the layer's own names."""
    out = {}
    if not offset:
        return out
    low = {str(k).lower(): k for k in attrs}
    for names, sign in ((TILE_ELEV_FIELDS, -1.0), (DEPTH_FIELDS, 1.0)):
        for n in names:
            k = low.get(n.lower())
            if k is None:
                continue
            v = _num(attrs, k)
            if v is not None:
                out[k] = round(v + sign * offset, ndigits)
    return out



# ---------------------------------------------------------------- network check
DEPTH_OK, DEPTH_SHALLOW, DEPTH_DEEP = 1, 2, 3


def group_pipes(records):
    """{pipe key: [records]}; lines without a pipe_id are each their own pipe."""
    out = {}
    for k, r in enumerate(records):
        pid = _get(r["attrs"], "pipe_id")
        out.setdefault(("p", pid) if pid is not None else ("l", k), []).append(r)
    return out


def check_pipe(profile, abs_upper, abs_lower, step, tol=0.005):
    """Sample a pipe's profile every `step` (profile units) and at every tile and
    ground point, and classify the tile depth against the absolute limits.

    Returns {"samples": [(x, y, tile, depth, code)], "shallow": length shallower
    than abs_upper, "deep": length deeper than abs_lower, "min_depth",
    "max_depth", "worst": (station, depth) farthest outside the limits or None}.
    Lengths are in profile units; x, y in the layer's map units.
    """
    L = profile["length"]
    st = set(s for s, _ in profile["tile"]) | set(s for s, _ in profile["ground"])
    if step and step > 0:
        n = int(L // step)
        st |= {i * step for i in range(n + 1)}
    st = sorted(s for s in st if 0.0 <= s <= L) or [0.0, L]
    path = profile["path"]
    pxs = [(p[0], p[1]) for p in path]
    pys = [(p[0], p[2]) for p in path]
    samples, codes, depths = [], [], []
    for s in st:
        t = interp(profile["tile"], s)
        g = interp(profile["ground"], s)
        d = g - t
        c = (DEPTH_SHALLOW if d < abs_upper - tol else
             DEPTH_DEEP if d > abs_lower + tol else DEPTH_OK)
        x = interp(pxs, s)
        y = interp(pys, s)
        samples.append((x, y, t, d, c))
        codes.append(c)
        depths.append(d)
    shallow = deep = 0.0
    for i in range(len(st) - 1):
        ds = st[i + 1] - st[i]
        for c, attr in ((DEPTH_SHALLOW, "s"), (DEPTH_DEEP, "d")):
            # a stretch counts by the share of its two ends that are outside
            w = 0.5 * ((codes[i] == c) + (codes[i + 1] == c)) * ds
            if attr == "s":
                shallow += w
            else:
                deep += w
    worst, excess = None, 0.0
    for s, d in zip(st, depths):
        e = max(abs_upper - d, d - abs_lower)
        if e > excess + 1e-12 and e > tol:
            worst, excess = (s, d), e
    adv = adverse_sections(profile)
    return {"samples": samples, "shallow": shallow, "deep": deep,
            "min_depth": min(depths), "max_depth": max(depths), "worst": worst,
            "adverse": len(adv), "adverse_length": sum(b - a for a, b, _ in adv),
            "worst_slope": min((sl for _, _, sl in adv), default=None)}


def rasterize_samples(samples, x0, y0, cell, ncols, nrows, cell_y=None):
    """Burn check samples onto a north-up grid (x0, y0 = top-left corner).

    A cell crossed by the tile gets the tile elevation, the depth and the depth
    check of its worst sample (shallow before deep); a cell within the limits
    keeps its shallowest sample. Returns (tile, depth, code) numpy arrays, NaN
    and 0 off the tile, and the numbers of cells (on tile, shallow, deep).
    """
    import numpy as np
    tile = np.full((nrows, ncols), np.nan)
    depth = np.full((nrows, ncols), np.nan)
    code = np.zeros((nrows, ncols), dtype=np.int8)
    if not samples:
        return tile, depth, code, (0, 0, 0)
    a = np.asarray([s[:5] for s in samples], dtype=float)
    j = np.floor((a[:, 0] - x0) / cell).astype(int)
    i = np.floor((y0 - a[:, 1]) / (cell_y or cell)).astype(int)
    ok = (i >= 0) & (i < nrows) & (j >= 0) & (j < ncols) & np.isfinite(a[:, 3])
    a, i, j = a[ok], i[ok], j[ok]
    if not len(a):
        return tile, depth, code, (0, 0, 0)
    cid = i * ncols + j
    # rank: shallow first, then deep, then ok; within a class the worst first
    cls = a[:, 4]
    key = np.where(cls == DEPTH_SHALLOW, a[:, 3],
                   np.where(cls == DEPTH_DEEP, -a[:, 3], a[:, 3]))
    rank = np.where(cls == DEPTH_SHALLOW, 0, np.where(cls == DEPTH_DEEP, 1, 2))
    order = np.lexsort((key, rank, cid))
    cid, a = cid[order], a[order]
    first = np.r_[True, cid[1:] != cid[:-1]]
    ii, jj = np.divmod(cid[first], ncols)
    tile[ii, jj] = a[first, 2]
    depth[ii, jj] = a[first, 3]
    code[ii, jj] = a[first, 4].astype(np.int8)
    return tile, depth, code, (int(first.sum()), int((code == DEPTH_SHALLOW).sum()),
                               int((code == DEPTH_DEEP).sum()))



def line_changes(attrs, xy, z, m, offset, node_offsets, length=None, ndigits=4):
    """New tile elevations for one network line, with the network lowered by
    offset and each point in node_offsets {node_key: amount} lowered by its amount.

    xy, z, m: the line's vertices (z, m may be None). Returns (attribute changes
    {field: value}, new z list or None). The end fields follow the new end
    elevations: invert_us/ElevDepth1 (first vertex), invert_ds/ElevDepth2 (last),
    BuryDepth1/2 and depth_ds from the ground at the ends; min_depth/max_depth
    from the vertices when they carry ground (M) values; design_grade_pct and
    InSlope from the end-to-end fall over `length` (the line's length field).
    Without Z, only the two ends can move."""
    node_offsets = node_offsets or {}
    low = {str(k).lower(): k for k in attrs}

    def key(name):
        return low.get(name.lower())

    def num(name):
        k = key(name)
        return None if k is None else _num(attrs, k)

    each = [offset + node_offsets.get(node_key(*q), 0.0) for q in xy]
    if not any(each):
        return {}, None
    out = {}
    new_z = None
    if z is not None and len(z) == len(xy) and all(v is not None for v in z):
        new_z = [float(v) - d for v, d in zip(z, each)]
        t_us, t_ds = new_z[0], new_z[-1]
    else:
        e1 = num("ElevDepth1") if num("ElevDepth1") is not None else num("invert_us")
        e2 = num("ElevDepth2") if num("ElevDepth2") is not None else num("invert_ds")
        if e1 is None or e2 is None:
            return offset_changes(attrs, offset, ndigits), None
        t_us, t_ds = e1 - each[0], e2 - each[-1]
    for names, val in ((("invert_us", "ElevDepth1"), t_us), (("invert_ds", "ElevDepth2"), t_ds)):
        for n in names:
            if key(n) is not None and num(n) is not None:
                out[key(n)] = round(val, ndigits)
    g_us = num("ground_us")
    g_ds = num("ground_ds")
    if m is not None and len(m) == len(xy) and all(v is not None for v in m):
        g_us = float(m[0]) if g_us is None else g_us
        g_ds = float(m[-1]) if g_ds is None else g_ds
        if new_z is not None and max(each) != min(each):
            # points moved by different amounts: the envelope of the shifted stored
            # range and the depths at the vertices
            dep = [float(gv) - tv for gv, tv in zip(m, new_z)]
            for n, v, pick, d in (("min_depth", min(dep), min, min(each)),
                                  ("max_depth", max(dep), max, max(each))):
                old = num(n)
                if key(n) is not None:
                    out[key(n)] = round(v if old is None else pick(v, old + d), ndigits)
    if g_us is not None:
        for n in ("BuryDepth1",):
            if key(n) is not None:
                out[key(n)] = round(g_us - t_us, ndigits)
    if g_ds is not None:
        for n in ("BuryDepth2", "depth_ds"):
            if key(n) is not None:
                out[key(n)] = round(g_ds - t_ds, ndigits)
    if key("min_depth") not in out and key("max_depth") not in out:
        # the whole line moved by one amount, or no vertex ground: shift the range
        for n, d in (("min_depth", min(each)), ("max_depth", max(each))):
            v = num(n)
            if v is not None:
                out[key(n)] = round(v + d, ndigits)
    L = length if length is not None else num("length")
    if L:
        if key("design_grade_pct") is not None:
            out[key("design_grade_pct")] = round((t_us - t_ds) / L * 100.0, ndigits)
        if key("InSlope") is not None and num("InSlope") is not None:
            out[key("InSlope")] = round((t_us - t_ds) / L, 6)
    return out, new_z


def slope_offsets(profile, index, slope, rest=False):
    """Point offsets that give the section above tile point `index` the slope
    `slope` (a fraction, positive falling toward the outlet), keeping that point
    where it is.

    profile: the profile as shown (with any offsets already applied).
    rest=False moves only the next point upstream; rest=True moves every point
    further up the pipe by the same amount, keeping the other sections' slopes.
    Returns {node_key: amount to lower}, to add to the existing point offsets.
    """
    pts, xy = profile["tile"], profile.get("tile_xy")
    if xy is None or not 0 <= index < len(pts) - 1:
        return {}
    (s1, t1), (s2, t2) = pts[index], pts[index + 1]
    delta = t2 - (t1 + slope * (s2 - s1))           # how far the next point goes down
    if abs(delta) < 1e-12:
        return {}
    upper = range(index + 1, len(pts)) if rest else (index + 1,)
    out = {}
    for k in upper:
        out[node_key(*xy[k])] = delta
    return out


def section_slope(profile, index):
    """Slope of the section above tile point `index` (fraction), or None at the top."""
    pts = profile["tile"]
    if not 0 <= index < len(pts) - 1 or pts[index + 1][0] - pts[index][0] <= 1e-9:
        return None
    return (pts[index + 1][1] - pts[index][1]) / (pts[index + 1][0] - pts[index][0])
