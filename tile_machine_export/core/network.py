"""Drainage network topology from plain line geometry (numpy only, no QGIS).

The network may come from any of the QGIS_Tools layout plugins or be digitized
by hand: lines need not be split at junctions, digitized in the flow direction
or carry node fields. Lines are cut where an end touches the middle of another
line (a tee), ends closer than the snap tolerance are joined into one node, and
the network is turned into a tree rooted at the outlet.
"""
import math
from collections import deque

import numpy as np

COLLECTOR_KINDS = ("main", "submain")


class NetworkError(Exception):
    pass


class Edge:
    """One pipe between two nodes, with its vertices ordered downstream -> upstream."""

    __slots__ = ("xyz", "kind", "src", "ds_node", "us_node", "parent", "children",
                 "length", "order", "up_length", "collector", "main", "attrs")

    def __init__(self, xyz, kind, src, attrs=None):
        self.xyz = xyz
        self.kind = kind
        self.src = src
        self.attrs = attrs or {}
        self.ds_node = self.us_node = -1
        self.parent = -1
        self.children = []
        d = np.hypot(np.diff(xyz[:, 0]), np.diff(xyz[:, 1]))
        self.length = float(d.sum())
        self.order = 1
        self.up_length = 0.0
        self.collector = False
        self.main = False


class Network:
    def __init__(self):
        self.nodes = None          # (n, 2) node coordinates
        self.edges = []            # reached edges, oriented from the outlet upstream
        self.outlet_node = -1
        self.root_edges = []
        self.unconnected = 0       # lines not connected to the outlet
        self.loops = 0             # edges closing a loop (dropped)
        self.kind_source = "kind"  # "kind" field or "topology"
        self.crossings = 0         # X crossings joined


def _cumlen(xy):
    d = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    return np.concatenate([[0.0], np.cumsum(d)])


def _cut(xyz, cuts):
    """Cut a polyline at chainages `cuts` (sorted, strictly inside). Returns parts."""
    s = _cumlen(xyz[:, :2])
    parts = []
    start_pt = xyz[0]
    k0 = 1
    prev = 0.0
    for c in cuts:
        if c - prev <= 1e-9:
            continue
        k = int(np.searchsorted(s, c))
        k = min(max(k, 1), len(s) - 1)
        t = (c - s[k - 1]) / max(s[k] - s[k - 1], 1e-12)
        p = xyz[k - 1] + t * (xyz[k] - xyz[k - 1])
        mid = xyz[k0:k]
        parts.append(np.vstack([start_pt[None, :], mid, p[None, :]]))
        start_pt = p
        k0 = k
        if t >= 1.0 - 1e-12:
            k0 = k + 1
        prev = c
    parts.append(np.vstack([start_pt[None, :], xyz[k0:]]))
    out = []
    for q in parts:
        keep = np.r_[True, np.hypot(np.diff(q[:, 0]), np.diff(q[:, 1])) > 1e-9]
        q = q[keep]
        if len(q) >= 2:
            out.append(q)
    return out


def _split_at_tees(lines, tol):
    """Cut each line where another line's end touches its interior."""
    segs_a, segs_b, seg_line, seg_s0 = [], [], [], []
    for li, (xyz, _, _, _) in enumerate(lines):
        s = _cumlen(xyz[:, :2])
        segs_a.append(xyz[:-1, :2])
        segs_b.append(xyz[1:, :2])
        seg_line.append(np.full(len(xyz) - 1, li))
        seg_s0.append(s[:-1])
    if not segs_a:
        return lines
    A = np.vstack(segs_a)
    B = np.vstack(segs_b)
    SL = np.concatenate(seg_line)
    S0 = np.concatenate(seg_s0)
    AB = B - A
    L2 = np.maximum((AB ** 2).sum(1), 1e-18)
    SLEN = np.sqrt(L2)
    lo = np.minimum(A, B) - tol
    hi = np.maximum(A, B) + tol
    lengths = [float(_cumlen(xyz[:, :2])[-1]) for xyz, _, _, _ in lines]
    cuts = {}
    for li, (xyz, _, _, _) in enumerate(lines):
        for end in (xyz[0, :2], xyz[-1, :2]):
            cand = np.nonzero((end[0] >= lo[:, 0]) & (end[0] <= hi[:, 0])
                              & (end[1] >= lo[:, 1]) & (end[1] <= hi[:, 1]) & (SL != li))[0]
            if len(cand) == 0:
                continue
            t = np.clip(((end - A[cand]) * AB[cand]).sum(1) / L2[cand], 0.0, 1.0)
            p = A[cand] + t[:, None] * AB[cand]
            d = np.hypot(p[:, 0] - end[0], p[:, 1] - end[1])
            k = int(np.argmin(d))
            if d[k] > tol:
                continue
            seg = cand[k]
            tl = int(SL[seg])
            c = float(S0[seg] + t[k] * SLEN[seg])
            if c <= tol or c >= lengths[tl] - tol:
                continue            # touches an end: joined by node snapping
            cuts.setdefault(tl, []).append(c)
    if not cuts:
        return lines
    out = []
    for li, (xyz, kind, src, attrs) in enumerate(lines):
        if li in cuts:
            for q in _cut(xyz, sorted(set(cuts[li]))):
                out.append((q, kind, src, attrs))
        else:
            out.append((xyz, kind, src, attrs))
    return out


def _split_at_crossings(lines, which, tol):
    """Cut lines where a line in `which` crosses another line (an X junction)."""
    segs = []
    for li, (xyz, _, _, _) in enumerate(lines):
        s = _cumlen(xyz[:, :2])
        for k in range(len(xyz) - 1):
            segs.append((li, k, s[k]))
    if not segs:
        return lines, 0
    SL = np.array([a for a, _, _ in segs])
    A = np.vstack([lines[a][0][k, :2] for a, k, _ in segs])
    B = np.vstack([lines[a][0][k + 1, :2] for a, k, _ in segs])
    S0 = np.array([c for _, _, c in segs])
    D = B - A
    SLEN = np.hypot(D[:, 0], D[:, 1])
    lo = np.minimum(A, B)
    hi = np.maximum(A, B)
    which = set(which)
    cuts = {}
    n_x = 0
    for si in np.nonzero(np.isin(SL, list(which)))[0]:
        li = SL[si]
        cand = np.nonzero((lo[:, 0] <= hi[si, 0]) & (hi[:, 0] >= lo[si, 0])
                          & (lo[:, 1] <= hi[si, 1]) & (hi[:, 1] >= lo[si, 1]) & (SL != li))[0]
        if len(cand) == 0:
            continue
        p, r = A[si], D[si]
        q, sv = A[cand], D[cand]
        den = r[0] * sv[:, 1] - r[1] * sv[:, 0]
        ok = np.abs(den) > 1e-12
        qp = q - p
        t = np.where(ok, (qp[:, 0] * sv[:, 1] - qp[:, 1] * sv[:, 0]) / np.where(ok, den, 1), -1)
        u = np.where(ok, (qp[:, 0] * r[1] - qp[:, 1] * r[0]) / np.where(ok, den, 1), -1)
        hit = ok & (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1)
        for k in np.nonzero(hit)[0]:
            o = cand[k]
            lj = SL[o]
            ci = S0[si] + t[k] * SLEN[si]
            cj = S0[o] + u[k] * SLEN[o]
            cuts.setdefault(li, []).append(ci)
            cuts.setdefault(lj, []).append(cj)
            n_x += 1
    if not cuts:
        return lines, 0
    out = []
    for li, (xyz, kind, src, attrs) in enumerate(lines):
        if li in cuts:
            L = _cumlen(xyz[:, :2])[-1]
            cs = sorted(c for c in set(np.round(cuts[li], 6)) if tol * 0.5 < c < L - tol * 0.5)
            for q in (_cut(xyz, cs) if cs else [xyz]):
                out.append((q, kind, src, attrs))
        else:
            out.append((xyz, kind, src, attrs))
    return out, n_x


def _cluster(points, tol):
    """Join points closer than tol (single linkage, grid hashed). Returns labels."""
    n = len(points)
    parent = np.arange(n)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    cell = max(tol, 1e-9)
    buckets = {}
    keys = np.floor(points / cell).astype(np.int64)
    for i, (kx, ky) in enumerate(keys):
        buckets.setdefault((int(kx), int(ky)), []).append(i)
    for i, (kx, ky) in enumerate(keys):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((int(kx) + dx, int(ky) + dy), ()):
                    if j <= i:
                        continue
                    if math.hypot(*(points[i] - points[j])) <= tol:
                        ri, rj = find(i), find(j)
                        if ri != rj:
                            parent[max(ri, rj)] = min(ri, rj)
    roots = np.array([find(i) for i in range(n)])
    _, labels = np.unique(roots, return_inverse=True)
    return labels


def build_network(lines, tol, outlet_xy=None, ground_at=None, join_crossings=True):
    """Build the network tree.

    lines: list of (xyz array (n, 3), kind or None, source id, attrs dict); z is the
        tile elevation of each vertex (may be NaN where unknown).
    tol: snap tolerance in map units.
    outlet_xy: outlet location, or None to use the lowest free end.
    ground_at: callable(x, y arrays) -> ground elevation, used to pick the outlet
        when the tile elevations are unknown.
    join_crossings: lines that cross a pipe without meeting it at an end are
        joined to it where they cross (hand-drawn laterals drawn across a main).
    """
    clean = []
    for xyz, kind, src, attrs in lines:
        xyz = np.asarray(xyz, dtype=float)
        if xyz.ndim != 2 or len(xyz) < 2:
            continue
        keep = np.r_[True, np.hypot(np.diff(xyz[:, 0]), np.diff(xyz[:, 1])) > 1e-9]
        xyz = xyz[keep]
        if len(xyz) >= 2:
            k = (kind or "").strip().lower() if isinstance(kind, str) else None
            clean.append((xyz, k or None, src, attrs))
    if not clean:
        raise NetworkError("The drainage network has no usable lines.")
    lines = _split_at_tees(clean, tol)
    net, used = _graph(lines, tol, outlet_xy, ground_at)
    if join_crossings and net.unconnected:
        lines2, n_x = _split_at_crossings(lines, np.nonzero(~used)[0], tol)
        if n_x:
            lines2 = _split_at_tees(lines2, tol)
            net, used = _graph(lines2, tol, outlet_xy, ground_at)
            net.crossings = n_x
    if not net.edges:
        raise NetworkError("No pipe is connected to the outlet.")
    _classify(net)
    return net


def _graph(lines, tol, outlet_xy, ground_at):
    ends = np.array([[xyz[0, 0], xyz[0, 1]] for xyz, _, _, _ in lines]
                    + [[xyz[-1, 0], xyz[-1, 1]] for xyz, _, _, _ in lines])
    labels = _cluster(ends, tol)
    m = len(lines)
    n_nodes = int(labels.max()) + 1
    node_xy = np.zeros((n_nodes, 2))
    cnt = np.zeros(n_nodes)
    np.add.at(node_xy, labels, ends)
    np.add.at(cnt, labels, 1)
    node_xy /= cnt[:, None]

    adj = [[] for _ in range(n_nodes)]
    for li in range(m):
        a, b = int(labels[li]), int(labels[li + m])
        if a == b:
            continue                        # closed ring or a stub shorter than tol
        adj[a].append((li, b))
        adj[b].append((li, a))

    # outlet node
    degree = np.array([len(x) for x in adj])
    if outlet_xy is not None:
        d = np.hypot(node_xy[:, 0] - outlet_xy[0], node_xy[:, 1] - outlet_xy[1])
        d[degree == 0] = np.inf
        outlet = int(np.argmin(d))
    else:
        free = np.nonzero(degree == 1)[0]
        if len(free) == 0:
            free = np.nonzero(degree > 0)[0]
        zfree = np.full(len(free), np.nan)
        for k, nd in enumerate(free):
            li, _ = adj[nd][0]
            xyz = lines[li][0]
            first = int(labels[li]) == nd
            zfree[k] = xyz[0, 2] if first else xyz[-1, 2]
        if np.all(~np.isfinite(zfree)) and ground_at is not None:
            zfree = np.asarray(ground_at(node_xy[free, 0], node_xy[free, 1]), dtype=float)
        if np.any(np.isfinite(zfree)):
            outlet = int(free[int(np.nanargmin(zfree))])
        else:
            outlet = int(free[0])

    net = Network()
    net.nodes = node_xy
    net.outlet_node = outlet
    seen_node = np.zeros(n_nodes, dtype=bool)
    used_line = np.zeros(m, dtype=bool)
    seen_node[outlet] = True
    edge_into = {}                      # node -> edge index ending (upstream) at it
    q = deque([outlet])
    while q:
        nd = q.popleft()
        for li, other in adj[nd]:
            if used_line[li]:
                continue
            used_line[li] = True
            if seen_node[other]:
                net.loops += 1
                continue
            seen_node[other] = True
            xyz, kind, src, attrs = lines[li]
            if int(labels[li]) != nd:       # orient downstream -> upstream
                xyz = xyz[::-1].copy()
            xyz = xyz.copy()
            xyz[0, :2] = node_xy[nd]
            xyz[-1, :2] = node_xy[other]
            e = Edge(xyz, kind, src, attrs)
            e.ds_node, e.us_node = nd, other
            ei = len(net.edges)
            e.parent = edge_into.get(nd, -1)
            if e.parent >= 0:
                net.edges[e.parent].children.append(ei)
            else:
                net.root_edges.append(ei)
            net.edges.append(e)
            edge_into[other] = ei
            q.append(other)
    net.unconnected = int((~used_line).sum())
    return net, used_line


def _classify(net):
    """Strahler order, upstream length, collectors and the main."""
    E = net.edges
    for ei in range(len(E) - 1, -1, -1):        # children come after parents
        e = E[ei]
        if e.children:
            orders = sorted((E[c].order for c in e.children), reverse=True)
            e.order = orders[0] + (1 if len(orders) > 1 and orders[1] == orders[0] else 0)
        e.up_length = e.length + sum(E[c].up_length for c in e.children)
    kinds = {e.kind for e in E}
    have_kind = bool(kinds & {"main", "submain", "lateral"})
    net.kind_source = "kind" if have_kind else "topology"
    if have_kind:
        for e in E:
            e.collector = e.kind in COLLECTOR_KINDS
            e.main = e.kind == "main"
        if not any(e.main for e in E):
            _trace_main(net)
    else:
        for e in E:
            e.collector = e.order >= 2 or (e.parent < 0 and len(E) == 1)
        _trace_main(net)


def _trace_main(net):
    """The main: from the outlet, follow the collector with the most pipe upstream."""
    E = net.edges
    cand = [r for r in net.root_edges if E[r].collector] or list(net.root_edges)
    if not cand:
        return
    cur = max(cand, key=lambda i: E[i].up_length)
    while cur >= 0:
        E[cur].main = True
        E[cur].collector = True
        kids = [c for c in E[cur].children if E[c].collector]
        cur = max(kids, key=lambda i: E[i].up_length) if kids else -1


def fill_tile_z(net, ground_at, default_depth):
    """Fill unknown tile elevations: interpolate along each pipe, else ground - depth.

    Returns the number of vertices that took the default depth."""
    n_default = 0
    for e in net.edges:
        z = e.xyz[:, 2]
        ok = np.isfinite(z)
        if ok.all():
            continue
        s = _cumlen(e.xyz[:, :2])
        if ok.sum() >= 2:
            z[~ok] = np.interp(s[~ok], s[ok], z[ok])
        else:
            g = np.asarray(ground_at(e.xyz[:, 0], e.xyz[:, 1]), dtype=float)
            fill = g - default_depth
            if ok.sum() == 1:
                k = int(np.nonzero(ok)[0][0])
                fill = fill + (z[k] - fill[k])
            n_default += int((~ok).sum())
            z[~ok] = fill[~ok]
    return n_default


class Samples:
    """Points every `step` along the network, each linked to its downstream neighbour."""

    def __init__(self):
        self.x = self.y = self.z = None
        self.edge = None         # edge index
        self.chain = None        # distance from the edge's downstream node
        self.ds = None           # index of the downstream sample (-1 at the outlet)
        self.collector = None    # bool: on a pipe that can take a structure
        self.main = None
        self.edge_first = []     # index of each edge's first own sample
        self.edge_last = []      # index of each edge's sample at its upstream node


def sample_network(net, step):
    xs, ys, zs, es, cs, ds = [], [], [], [], [], []
    first, last = [0] * len(net.edges), [0] * len(net.edges)
    n = 0
    for ei, e in enumerate(net.edges):
        s = _cumlen(e.xyz[:, :2])
        L = s[-1]
        k = max(1, int(math.ceil(L / step)))
        st = np.linspace(0.0, L, k + 1)
        if e.parent >= 0:
            st = st[1:]                     # the node itself belongs to the parent
        x = np.interp(st, s, e.xyz[:, 0])
        y = np.interp(st, s, e.xyz[:, 1])
        z = np.interp(st, s, e.xyz[:, 2])
        cnt = len(st)
        xs.append(x)
        ys.append(y)
        zs.append(z)
        es.append(np.full(cnt, ei))
        cs.append(st)
        d = np.arange(n - 1, n + cnt - 1)
        d[0] = last[e.parent] if e.parent >= 0 else -1
        ds.append(d)
        first[ei] = n
        last[ei] = n + cnt - 1
        n += cnt
    S = Samples()
    S.x, S.y, S.z = np.concatenate(xs), np.concatenate(ys), np.concatenate(zs)
    S.edge, S.chain, S.ds = np.concatenate(es), np.concatenate(cs), np.concatenate(ds)
    coll = np.array([e.collector for e in net.edges])
    main = np.array([e.main for e in net.edges])
    S.collector = coll[S.edge]
    S.main = main[S.edge]
    # the sample at a node shared with a parent belongs to the parent edge; a node
    # where a collector leaves a lateral-free point is still a collector point
    S.edge_first, S.edge_last = first, last
    return S
