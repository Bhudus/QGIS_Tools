"""Installation runs, stations and checks for a buried tile network (numpy only).

A run is one continuous pipe as the machine installs it: a main from the
outlet to its top end, a submain from its junction up, a lateral from its
junction up. Runs follow the `pipe_id` of the layout plugins where there is
one; otherwise a pipe carries on, at each junction, into the upstream pipe
of the same kind with the most tile above it.

Arrays are (n, 4): x, y, tile elevation, ground elevation (NaN if unknown),
ordered from the run's downstream end (station 0 of an upstream run).
"""
import math

import numpy as np

KIND_RANK = {"main": 0, "submain": 1, "lateral": 2}
KIND_PREFIX = {"main": "M", "submain": "S", "lateral": "L"}


class Run:
    def __init__(self):
        self.edges = []          # edge indices, downstream -> upstream
        self.xyzg = None         # (n, 4) downstream -> upstream
        self.kind = "lateral"
        self.attrs = {}
        self.name = ""
        self.number = 0
        self.parent = -1         # run it discharges into (-1 at the outlet)
        self.ds_chain = 0.0      # distance of its downstream end from the outlet
        self.junctions = []      # (chainage along this run, child run index)
        self.parent_chain = None  # chainage along the parent where it joins


def _edge_kind(e):
    if e.kind in KIND_RANK:
        return e.kind
    return "main" if e.main else ("submain" if e.collector else "lateral")


def _pipe_id(e):
    for k in ("pipe_id", "PIPE_ID", "Pipe_ID"):
        if k in e.attrs and e.attrs[k] not in (None, ""):
            return str(e.attrs[k])
    return None


def _deflection(e_ds, e_us):
    """Angle in degrees between the upstream ends of e_ds and the start of e_us."""
    a = e_ds.xyz
    b = e_us.xyz
    k = len(a) - 2
    while k > 0 and np.hypot(*(a[-1, :2] - a[k, :2])) < 1e-6:
        k -= 1
    d1 = a[-1, :2] - a[k, :2]
    j = 1
    while j < len(b) - 1 and np.hypot(*(b[j, :2] - b[0, :2])) < 1e-6:
        j += 1
    d2 = b[j, :2] - b[0, :2]
    n = np.hypot(*d1) * np.hypot(*d2)
    if n <= 0:
        return 180.0
    c = float(np.clip(np.dot(d1, d2) / n, -1.0, 1.0))
    return math.degrees(math.acos(c))


def build_runs(net):
    """Split the network tree into runs. Returns the list of Run (named, ordered)."""
    E = net.edges
    run_of = [-1] * len(E)
    runs = []
    # distance of each edge's downstream node from the outlet
    dist_ds = [0.0] * len(E)
    for ei, e in enumerate(E):                    # parents come first
        if e.parent >= 0:
            dist_ds[ei] = dist_ds[e.parent] + E[e.parent].length

    def start(ei, parent_run):
        r = Run()
        r.parent = parent_run
        r.ds_chain = dist_ds[ei]
        r.kind = _edge_kind(E[ei])
        runs.append(r)
        cur = ei
        while cur >= 0:
            r.edges.append(cur)
            run_of[cur] = len(runs) - 1
            kids = E[cur].children
            if not kids:
                break
            pid = _pipe_id(E[cur])
            same = [c for c in kids if pid is not None and _pipe_id(E[c]) == pid]
            if not same and pid is None:
                k = _edge_kind(E[cur])
                same = [c for c in kids if _edge_kind(E[c]) == k]
                if not same and k == "lateral":
                    same = list(kids)
            if not same and pid is None:
                # nothing else identifies the pipe: carry on straight ahead
                straight = [c for c in kids if _deflection(E[cur], E[c]) <= 30.0]
                same = straight
            cur = max(same, key=lambda c: E[c].up_length) if same else -1
        return len(runs) - 1

    stack = [(r, -1) for r in net.root_edges]
    while stack:
        ei, pr = stack.pop()
        if run_of[ei] >= 0:
            continue
        ri = start(ei, pr)
        for e2 in runs[ri].edges:
            for c in E[e2].children:
                if run_of[c] < 0:
                    stack.append((c, ri))

    for ri, r in enumerate(runs):
        parts = []
        chain = 0.0
        breaks = {}
        for k, ei in enumerate(r.edges):
            xyz = E[ei].xyz
            parts.append(xyz if k == 0 else xyz[1:])
            breaks[ei] = chain
            chain += E[ei].length
        r.xyzg = np.vstack(parts)
        first = E[r.edges[0]]
        r.attrs = dict(first.attrs)
        # the run's kind is its most important edge kind
        r.kind = min((_edge_kind(E[ei]) for ei in r.edges), key=lambda k: KIND_RANK[k])
        r._edge_start = breaks
    # junctions: where each child run leaves its parent
    for ri, r in enumerate(runs):
        if r.parent >= 0:
            p = runs[r.parent]
            pe = E[r.edges[0]].parent
            c = p._edge_start.get(pe, 0.0) + E[pe].length
            r.parent_chain = c
            p.junctions.append((c, ri))
    # order and names: by kind, then by distance from the outlet
    order = sorted(range(len(runs)), key=lambda i: (KIND_RANK[runs[i].kind], runs[i].ds_chain,
                                                     -runs[i].length()))
    counters = {}
    for i in order:
        r = runs[i]
        counters[r.kind] = counters.get(r.kind, 0) + 1
        r.number = counters[r.kind]
        r.name = "{}{}".format(KIND_PREFIX[r.kind], r.number)
    new_of = {old: new for new, old in enumerate(order)}
    for r in runs:
        r.parent = new_of[r.parent] if r.parent >= 0 else -1
        r.junctions = sorted((c, new_of[ri]) for c, ri in r.junctions)
    return [runs[i] for i in order]


def _len(self):
    a = self.xyzg
    return float(np.hypot(np.diff(a[:, 0]), np.diff(a[:, 1])).sum())


Run.length = _len


def chainage(a):
    return np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(a[:, 0]), np.diff(a[:, 1])))])


def grade_sections(a, tol_pct=0.005):
    """Constant-grade sections, downstream -> upstream: list of
    (start chain, end chain, grade %, tile elev start, tile elev end). Positive grade
    means the tile rises upstream (drains toward the outlet)."""
    s = chainage(a)
    z = a[:, 2]
    seg = []
    for k in range(len(a) - 1):
        L = s[k + 1] - s[k]
        if L <= 1e-9:
            continue
        g = 100.0 * (z[k + 1] - z[k]) / L
        if seg and abs(seg[-1][2] - g) <= tol_pct:
            s0, _, _, z0, _ = seg[-1]
            L2 = s[k + 1] - s0
            g2 = 100.0 * (z[k + 1] - z0) / L2
            seg[-1] = (s0, s[k + 1], g2, z0, z[k + 1])
        else:
            seg.append((s[k], s[k + 1], g, z[k], z[k + 1]))
    return seg


def run_stats(run, min_grade, min_cover, max_depth, control_offset=0.0):
    """Summary of one run (design units). Depths are ground - tile invert."""
    a = run.xyzg
    secs = grade_sections(a)
    g = a[:, 3]
    depth = g - a[:, 2]
    has_g = np.isfinite(depth).any()
    flags = []
    grades = [sc[2] for sc in secs]
    if any(gr < 0 for gr in grades):
        flags.append("reverse grade")
    elif any(gr < min_grade - 5e-4 for gr in grades):     # 0.0995% shows as 0.100
        flags.append("grade below {:g}%".format(min_grade))
    if has_g:
        if np.nanmin(depth) < min_cover - 1e-9:
            flags.append("shallower than {:g}".format(min_cover))
        if np.nanmax(depth) > max_depth + 1e-9:
            flags.append("deeper than {:g}".format(max_depth))
    return {
        "length": run.length(),
        "sections": secs,
        "inv_ds": float(a[0, 2]), "inv_us": float(a[-1, 2]),
        "ground_ds": float(a[0, 3]), "ground_us": float(a[-1, 3]),
        "min_grade": min(grades) if grades else float("nan"),
        "max_grade": max(grades) if grades else float("nan"),
        "min_depth": float(np.nanmin(depth)) if has_g else float("nan"),
        "max_depth": float(np.nanmax(depth)) if has_g else float("nan"),
        "mean_depth": float(np.nanmean(depth)) if has_g else float("nan"),
        "control_ds": float(a[0, 2] - control_offset),
        "control_us": float(a[-1, 2] - control_offset),
        "flags": flags,
    }


def stations(run, interval, runs_by_index=None):
    """Stations along a run (downstream -> upstream chainage): every `interval`,
    at grade breaks, at junctions and at both ends. Returns a list of dicts
    with chain, x, y, tile, ground, grade (% of the section upstream) and note."""
    a = run.xyzg
    s = chainage(a)
    L = s[-1]
    secs = grade_sections(a)
    pts = {0.0: "outlet end" if run.parent < 0 else "junction with parent", L: "upstream end"}
    if interval and interval > 0:
        for c in np.arange(interval, L - 1e-6, interval):
            pts.setdefault(round(float(c), 6), "")
    for sc in secs[1:]:
        c = round(float(sc[0]), 6)
        pts[c] = (pts.get(c, "") + "; " if pts.get(c) else "") + "grade break"
    for c, ri in run.junctions:
        c = round(float(c), 6)
        nm = runs_by_index[ri].name if runs_by_index is not None else str(ri)
        pts[c] = (pts.get(c, "") + "; " if pts.get(c) else "") + "junction " + nm
    out = []
    sec_starts = np.array([sc[0] for sc in secs]) if secs else np.array([0.0])
    for c in sorted(pts):
        k = int(np.searchsorted(sec_starts, c + 1e-6) - 1) if secs else 0
        k = min(max(k, 0), max(len(secs) - 1, 0))
        out.append({
            "chain": float(c),
            "x": float(np.interp(c, s, a[:, 0])), "y": float(np.interp(c, s, a[:, 1])),
            "tile": float(np.interp(c, s, a[:, 2])),
            "ground": float(np.interp(c, s, a[:, 3])) if np.isfinite(a[:, 3]).all()
            else float("nan"),
            "grade": float(secs[k][2]) if secs else float("nan"),
            "note": pts[c],
        })
    return out


def station_label(chain, per=100.0):
    """Engineering station text, e.g. 12+50.0 (per = 100 ft) or 1+250.0 (per = 1000 m)."""
    whole = int(math.floor(chain / per + 1e-9))
    rest = chain - whole * per
    w = 2 if per == 100.0 else 3
    return "{}+{:0{w}.1f}".format(whole, rest, w=w + 2)


def prepare_records(runs, to_xy, to_lonlat, k_out, k_m, offset_out, control_offset,
                    interval, min_grade, min_cover, max_depth, upstream_first=False,
                    station_per=100.0, size_of=None):
    """Turn runs (design units) into writer records (output units).

    to_xy(x, y arrays) -> (easting, northing) in the output CRS;
    to_lonlat(x, y arrays) -> (lon, lat) WGS 84;
    k_out: design units -> output units; k_m: design units -> metres;
    offset_out: added to every elevation, in output units;
    control_offset: control point below the tile invert, design units.
    """
    off_d = offset_out / k_out              # offset in design units
    recs = []
    for r in runs:
        st = run_stats(r, min_grade, min_cover, max_depth, control_offset)
        sts = stations(r, interval, runs)
        a = r.xyzg
        ctrl = a[:, 2] - control_offset
        order = slice(None, None, -1) if upstream_first else slice(None)
        ex, ny = to_xy(a[:, 0], a[:, 1])
        lo, la = to_lonlat(a[:, 0], a[:, 1])
        xyz = [(float(x), float(y), float((c + off_d) * k_out))
               for x, y, c in zip(ex[order], ny[order], ctrl[order])]
        lonlat = [(float(x), float(y), float((c + off_d) * k_m))
                  for x, y, c in zip(lo[order], la[order], ctrl[order])]
        sx = np.array([s["x"] for s in sts])
        sy = np.array([s["y"] for s in sts])
        se, sn = to_xy(sx, sy)
        slo, sla = to_lonlat(sx, sy)
        out_st = []
        for k, s in enumerate(sts):
            out_st.append({
                "label": station_label(s["chain"] * k_out, station_per),
                "chain": s["chain"] * k_out,
                "lon": float(slo[k]), "lat": float(sla[k]),
                "e": float(se[k]), "n": float(sn[k]),
                "ground": (s["ground"] + off_d) * k_out,
                "tile": (s["tile"] + off_d) * k_out,
                "control": (s["tile"] - control_offset + off_d) * k_out,
                "depth": (s["ground"] - s["tile"]) * k_out,
                "grade": s["grade"], "note": s["note"]})
        stc = dict(st)
        for key in ("length", "min_depth", "max_depth", "mean_depth"):
            stc[key] = st[key] * k_out
        for key in ("inv_ds", "inv_us", "ground_ds", "ground_us", "control_ds", "control_us"):
            stc[key] = (st[key] + off_d) * k_out
        stc["sections"] = [(s0 * k_out, s1 * k_out, g, (z0 + off_d) * k_out,
                            (z1 + off_d) * k_out) for s0, s1, g, z0, z1 in st["sections"]]
        stc["flags"] = list(st["flags"])
        recs.append({"name": r.name, "kind": r.kind,
                     "size": size_of(r) if size_of else None,
                     "install_from": "upstream end" if upstream_first else
                     ("outlet end" if r.parent < 0 else "junction (downstream end)"),
                     "parent": runs[r.parent].name if r.parent >= 0 else "",
                     "xyz": xyz, "lonlat": lonlat, "stats": stc, "stations": out_st})
    return recs
