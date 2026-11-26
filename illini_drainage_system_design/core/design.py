"""Drawn tile network -> Illini network (pure Python + numpy).

Illini Drainage System Design works on a network of mains and submains the
user has drawn. This module turns its cleaned segments (routine E) into the
pipes of the Illini Subsurface Drain Layout engine, and buries and sizes them
with that engine, so the design writes the same network as the layout tool:
lines from node to node with the tile (Z) and ground (M) elevation at every
vertex, Routine J and K fields, BURY_CHECK and flag.

Every drawn line is drained tile, so a line's drained area is the tile length
upstream of it times the drain spacing (Routine K's FLOW_LENGTH x spacing). A
line that no other line drains into is a lateral; the rest are the main and
its submains.
"""
import math
from collections import defaultdict

import numpy as np

from . import sizing as K
from .geometry import polyline_length, resample
from .layout import DrainageLayout, LayoutError


def _key(x, y, tol):
    return (int(round(x / tol)), int(round(y / tol)))


def build_pipes(segments, outlet_xy, step, tol):
    """Pipes from drawn segments, flowing to the segment nearest `outlet_xy`.

    segments: list of (x, y) vertex lists (metres). Segments meet where their
    ends are within `tol`. From the outlet, every segment gets the direction of
    flow; each pipe carries on up the branch with the most tile upstream, and
    the other branches start pipes of their own (submains) joining it there.
    Returns (cols, notes): cols in the engine's form ({"pts" upstream ->
    downstream, "kind", "to": (host, vertex) or None}).
    """
    segs = []
    for pts in segments:
        a = np.asarray(pts, dtype=float)
        if len(a) >= 2 and polyline_length(a) > 1e-6:
            segs.append(a)
    if not segs:
        raise LayoutError("The tile lines have no segments.")
    notes = []

    # nodes: segment ends that meet within tol
    node_of = {}
    node_xy = []

    def node(x, y):
        k = _key(x, y, tol)
        hit = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                kk = (k[0] + dx, k[1] + dy)
                if kk in node_of:
                    n = node_of[kk]
                    if math.hypot(node_xy[n][0] - x, node_xy[n][1] - y) <= tol:
                        hit = n
                        break
            if hit is not None:
                break
        if hit is None:
            hit = len(node_xy)
            node_xy.append((float(x), float(y)))
            node_of[k] = hit
        return hit

    ends = [(node(*a[0]), node(*a[-1])) for a in segs]
    at_node = defaultdict(list)
    for i, (n0, n1) in enumerate(ends):
        at_node[n0].append(i)
        at_node[n1].append(i)

    # outlet: the segment nearest the point, flowing out at its free end
    ox, oy = outlet_xy

    def dist_to(a):
        best = float("inf")
        for p, q in zip(a[:-1], a[1:]):
            d = q - p
            L2 = float(d @ d)
            t = 0.0 if L2 <= 0 else max(0.0, min(1.0, float(((ox, oy) - p) @ d) / L2))
            c = p + t * d
            best = min(best, math.hypot(c[0] - ox, c[1] - oy))
        return best

    out_seg = int(np.argmin([dist_to(a) for a in segs]))
    n0, n1 = ends[out_seg]
    free0, free1 = len(at_node[n0]) == 1, len(at_node[n1]) == 1
    if free0 != free1:
        out_node = n0 if free0 else n1
    else:
        d0 = math.hypot(node_xy[n0][0] - ox, node_xy[n0][1] - oy)
        d1 = math.hypot(node_xy[n1][0] - ox, node_xy[n1][1] - oy)
        out_node = n0 if d0 <= d1 else n1
        if not free0:
            notes.append("The outlet segment is joined at both ends; the end nearest the "
                         "outlet point is taken as the outlet.")

    # direction of flow: breadth first from the outlet
    down = {}                                   # segment -> its downstream node
    seen_nodes = {out_node}
    frontier = [out_node]
    loops = 0
    while frontier:
        nxt = []
        for n in frontier:
            for i in at_node[n]:
                if i in down:
                    continue
                a, b = ends[i]
                other = b if a == n else a
                if other in seen_nodes and i != out_seg and other != n:
                    loops += 1                  # closes a loop: left out
                    down[i] = None
                    continue
                down[i] = n
                if other not in seen_nodes:
                    seen_nodes.add(other)
                    nxt.append(other)
        frontier = nxt
    if loops:
        notes.append("{} segment(s) close a loop in the network and are left out.".format(loops))
    unconnected = [i for i in range(len(segs)) if i not in down]
    if unconnected:
        notes.append("{} segment(s) are not connected to the outlet and are left out."
                     .format(len(unconnected)))

    def up_node(i):
        a, b = ends[i]
        return b if a == down[i] else a

    children = defaultdict(list)                # node -> segments draining into it
    for i, n in down.items():
        if n is not None:
            children[n].append(i)

    # tile length upstream of (and including) every segment
    seg_len = [polyline_length(a) for a in segs]
    total = {}
    order = []
    stack = [out_seg]
    while stack:
        i = stack.pop()
        order.append(i)
        stack.extend(children[up_node(i)])
    for i in reversed(order):
        total[i] = seg_len[i] + sum(total[c] for c in children[up_node(i)])

    def oriented(i):
        """Segment i's vertices, upstream -> downstream."""
        a = segs[i]
        return a if ends[i][1] == down[i] else a[::-1]

    # pipes: carry on up the branch with the most tile upstream
    pipes = []                                  # [segments downstream -> upstream], host
    starts = [(out_seg, None)]
    while starts:
        first, host = starts.pop(0)
        chain = [first]
        while True:
            kids = children[up_node(chain[-1])]
            if not kids:
                break
            kids = sorted(kids, key=lambda c: -total[c])
            for c in kids[1:]:
                starts.append((c, (len(pipes), up_node(chain[-1]))))
            chain.append(kids[0])
        pipes.append((chain, host))

    cols, vertex_of = [], []
    for P, (chain, host) in enumerate(pipes):
        parts, where, count = [], {}, 0
        for i in reversed(chain):               # upstream first
            q = resample(oriented(i), step)
            if parts:
                q = q[1:]                       # its upstream end is the last vertex so far
                where[up_node(i)] = count - 1
            else:
                where[up_node(i)] = 0
            parts.append(q)
            count += len(q)
            where[down[i]] = count - 1
        pts = np.vstack(parts)
        cols.append({"pts": pts, "kind": "main" if P == 0 else "submain", "to": host,
                     "user": True})
        vertex_of.append(where)
    for P, c in enumerate(cols):
        if c["to"] is not None:
            H, n = c["to"]
            c["to"] = (H, vertex_of[H][n])
            c["pts"][-1] = cols[H]["pts"][vertex_of[H][n]]
    return cols, notes


def split_laterals(cols):
    """Mains and submains, and laterals: a drawn line that no other line drains
    into is a lateral (the main stays the main). Returns (cols, lats) in the
    engine's form, the laterals joining their collector at its vertex."""
    fed = {c["to"][0] for c in cols if c["to"] is not None}
    keep = [P for P in range(len(cols)) if P == 0 or P in fed]
    new_of = {P: i for i, P in enumerate(keep)}
    out = []
    for P in keep:
        c = dict(cols[P])
        if c["to"] is not None:
            c["to"] = (new_of[c["to"][0]], c["to"][1])
        out.append(c)
    lats = []
    for P, c in enumerate(cols):
        if P in new_of:
            continue
        H, v = c["to"]
        lats.append({"pts": np.asarray(c["pts"], float), "region": 0, "origin": "drawn",
                     "conn": ("collector", new_of[H], v), "flags": [], "bearing": None,
                     "user": True})
    return out, lats


class DesignNetwork(DrainageLayout):
    """The layout engine on a drawn network: its mains and submains as given,
    and the lines nothing drains into as laterals."""

    def __init__(self, dem, valid, grid, rings, cols, params, progress=None, cancel=None,
                 order_coeffs=None):
        super().__init__(dem, valid, grid, rings, tuple(cols[0]["pts"][-1]), params,
                         progress=progress, cancel=cancel)
        self._given = cols
        self.order_coeffs = [float(c) for c in (order_coeffs or [])]

    def run(self):
        self._progress(5, "Preparing terrain")
        self._prepare()
        self.cols, self.lats = split_laterals(self._given)
        for c in self.cols:
            c.setdefault("nlat", 0)
        self._progress(40, "Burying the tile")
        self._design()
        self._progress(90, "Sizing and writing the network")
        res = self._results()
        if self.p.tile_raster:
            res["tile_raster"] = self.tile_raster()
        return res

    def _size(self, net):
        """Routine K, every drawn line draining its spacing; with a drainage
        coefficient for each tile order when those are given."""
        for L in net:
            L["attrs"]["drained_ha"] = round(L["attrs"]["upstream_m"] * self.S / 1e4, 3)
        super()._size(net)
        if not self.order_coeffs:
            return
        p = self.p
        totals = {}
        for L in net:
            a = L["attrs"]
            ex = a["_exact"]
            order = int(a["TILE_ORDER"])
            dc = self.order_coeffs[min(order, len(self.order_coeffs)) - 1]
            r = K.size_segment(ex["flow_len_ft"], ex["slope"], self.S / 0.3048,
                               p.pipe_material, dc)
            a.update({"D_COEFF": round(r["D_COEFF"], 4), "D_AREA": round(r["D_AREA"], 4),
                      "CUM_SEG_FLOW": round(r["CUM_SEG_FLOW"], 6),
                      "ACTUAL_SIZE": round(r["ACTUAL_SIZE"], 3), "NOMINAL": r["NOMINAL"],
                      "_exact": dict(r, flow_len_ft=ex["flow_len_ft"], slope=ex["slope"])})
            totals[r["NOMINAL"]] = totals.get(r["NOMINAL"], 0.0) + a["length_m"]
        self.size_totals = dict(sorted(totals.items()))


def run_design(dem, valid, grid, segments, outlet_xy, params, progress=None, cancel=None,
               order_coeffs=None):
    """Bury and size a drawn network. segments in metres, on the grid's DEM."""
    cols, notes = build_pipes(segments, outlet_xy, grid.cell / 2.0,
                              tol=max(0.05, 0.01 * grid.cell))
    allp = np.vstack([c["pts"] for c in cols])
    m = 2.0 * float(params.spacing) + 2 * grid.cell
    x0, y0 = allp.min(axis=0) - m
    x1, y1 = allp.max(axis=0) + m
    rings = [np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])]
    eng = DesignNetwork(dem, valid, grid, rings, cols, params, progress, cancel, order_coeffs)
    res = eng.run()
    res["warnings"] = notes + res["warnings"]
    res["pipes"] = len(cols)
    res["n_laterals"] = len(eng.lats)
    res["n_submains"] = len(eng.cols) - 1
    return res
