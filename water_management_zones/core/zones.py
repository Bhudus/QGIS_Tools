"""Water management zones and control-structure placement (numpy only).

Two modes:

* Network mode: a drainage network is given. Every field cell is drained by its
  nearest pipe; its water reaches the collector system where that pipe joins it
  (its attach point). Walking up the collectors from the outlet, a structure is
  placed wherever the ground served since the last structure spans more than
  the zone elevation interval. Each structure's zone is the land it controls.
* Band mode: no network yet. The field is cut into elevation bands one interval
  high, the zones a layout should be built around.

Each zone gets a board (stop-log) elevation: the managed water table sits
`wt_depth` below the low ground of the zone. A cell counts as controlled when
the managed water table is above its tile and within `control_depth` of the
ground.

Elevations and depths are in design units; distances in map units.
"""
import numpy as np

from .allocate import nearest_sample
from .network import _cumlen, _cut, sample_network

# control-status codes
ST_NONE, ST_OK, ST_DEEP, ST_FREE, ST_WET, ST_UNDRAINED = 0, 1, 2, 3, 4, 5
STATUS_LABELS = {
    ST_OK: "Controlled",
    ST_DEEP: "Water table too deep (high ground)",
    ST_FREE: "Tile above managed water table (drains freely)",
    ST_WET: "Managed water table above ground (too wet)",
    ST_UNDRAINED: "Not near a tile",
}


class ZoneParams:
    def __init__(self, interval=1.0, wt_depth=1.0, control_depth=2.0, min_spacing=30.0,
                 structures_on="collectors", max_dist=None, step=2.0, p_low=5.0,
                 p_high=95.0, intervals=None, band_smooth=0, step_pct=25.0,
                 field_rings=None):
        self.interval = float(interval)          # zone elevation interval
        self.wt_depth = float(wt_depth)          # managed WT depth below zone low ground
        self.control_depth = float(control_depth)  # deepest WT that still counts as control
        self.min_spacing = float(min_spacing)    # shortest distance between structures (map)
        self.structures_on = structures_on       # "collectors" or "mains"
        self.max_dist = max_dist                 # cells farther from a tile are undrained
        self.step = float(step)                  # network sampling step (map units)
        self.p_low = float(p_low)                # percentiles for the zone ground range
        self.p_high = float(p_high)
        self.intervals = intervals or []         # intervals for the options table
        self.band_smooth = int(band_smooth)      # smoothing radius (cells) for band mode
        # ground of the land joining the collectors at each point, taken as this
        # percentile and its complement (50 = median), for placing structures
        self.step_pct = float(step_pct)
        # field boundary rings (outer rings and holes, (n, 2) arrays in map units):
        # structures are placed only inside them. None = inside the field raster.
        self.field_rings = field_rings


def points_in_rings(x, y, rings):
    """Even-odd point-in-polygon test of points (x, y) against a set of rings
    (outer rings and holes alike). Points on an edge may fall either way."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    inside = np.zeros(x.shape, dtype=bool)
    for r in rings:
        r = np.asarray(r, dtype=float)
        if len(r) < 3:
            continue
        x1, y1 = r[:, 0], r[:, 1]
        x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
        for k in range(len(r)):
            ya, yb = y1[k], y2[k]
            if ya == yb:
                continue
            cross = (ya > y) != (yb > y)
            if not cross.any():
                continue
            xc = x1[k] + (y - ya) * (x2[k] - x1[k]) / (yb - ya)
            inside ^= cross & (x < xc)
    return inside


def bilinear(grid, z, x, y):
    """Bilinear sample of grid array z at points (NaN outside or on NaN cells)."""
    x = np.atleast_1d(np.asarray(x, dtype=float))
    y = np.atleast_1d(np.asarray(y, dtype=float))
    fj = (x - grid.x0) / grid.cell - 0.5
    fi = (grid.y0 - y) / grid.cell - 0.5
    j0 = np.clip(np.floor(fj).astype(int), 0, grid.ncols - 1)
    i0 = np.clip(np.floor(fi).astype(int), 0, grid.nrows - 1)
    j1 = np.clip(j0 + 1, 0, grid.ncols - 1)
    i1 = np.clip(i0 + 1, 0, grid.nrows - 1)
    tj = np.clip(fj - j0, 0, 1)
    ti = np.clip(fi - i0, 0, 1)
    a, b, c, d = z[i0, j0], z[i0, j1], z[i1, j0], z[i1, j1]
    vals = np.stack([a, b, c, d])
    w = np.stack([(1 - ti) * (1 - tj), (1 - ti) * tj, ti * (1 - tj), ti * tj])
    ok = np.isfinite(vals)
    w = np.where(ok, w, 0.0)
    ws = w.sum(0)
    out = np.where(ws > 0, (np.where(ok, vals, 0.0) * w).sum(0) / np.maximum(ws, 1e-12),
                   np.nan)
    outside = (fj < -0.5) | (fj > grid.ncols - 0.5) | (fi < -0.5) | (fi > grid.nrows - 0.5)
    out[outside] = np.nan
    return out


def box_smooth(z, mask, r):
    """Mean of the valid cells within a (2r+1) square, for cells in mask."""
    if r <= 0:
        return np.where(mask, z, np.nan)
    v = np.where(mask, z, 0.0)
    m = mask.astype(float)

    def boxsum(a):
        p = np.pad(a, r + 1, mode="constant")
        c = p.cumsum(0).cumsum(1)
        k = 2 * r + 1
        return c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]

    s = boxsum(v)[:z.shape[0], :z.shape[1]]
    n = boxsum(m)[:z.shape[0], :z.shape[1]]
    out = np.where(mask & (n > 0), s / np.maximum(n, 1), np.nan)
    return out


def _group_percentiles(keys, values, n_keys, pcts):
    """Percentiles of values grouped by integer keys 0..n_keys-1 (NaN for empty)."""
    out = np.full((len(pcts), n_keys), np.nan)
    if len(keys) == 0:
        return out, np.zeros(n_keys, dtype=int)
    order = np.lexsort((values, keys))
    k = keys[order]
    v = values[order]
    counts = np.bincount(k, minlength=n_keys)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    has = counts > 0
    for pi, p in enumerate(pcts):
        pos = starts + np.clip(np.round((counts - 1) * p / 100.0).astype(int), 0, None)
        out[pi, has] = v[pos[has]]
    return out, counts


# ---------------------------------------------------------------- network mode
class NetworkZoning:
    """Everything that does not depend on the interval, computed once."""

    def __init__(self, grid, dem, mask, net, params, cell_area, groups=None):
        """groups: optional label grid (0 = none). Land in a labelled area drains
        only to pipe in the same area (e.g. one zone polygon per drainage system);
        an area with no pipe of its own drains to the nearest pipe anywhere."""
        self.g = grid
        self.dem = dem
        self.mask = mask
        self.net = net
        self.p = params
        self.cell_area = cell_area
        S = sample_network(net, params.step)
        self.S = S
        S.ground = bilinear(grid, np.where(mask | np.isfinite(dem), dem, np.nan), S.x, S.y)
        bad = ~np.isfinite(S.ground)
        if bad.any():                     # off the DEM: carry the tile depth of neighbours
            S.ground[bad] = S.z[bad] + np.nanmedian(S.ground[~bad] - S.z[~bad]) \
                if (~bad).any() else S.z[bad]
        # pipe inside the field: only it drains field land and holds structures
        if params.field_rings:
            ins = points_in_rings(S.x, S.y, params.field_rings)
        else:
            i_ = np.floor((grid.y0 - S.y) / grid.cell).astype(np.int64)
            j_ = np.floor((S.x - grid.x0) / grid.cell).astype(np.int64)
            ok = (i_ >= 0) & (i_ < grid.nrows) & (j_ >= 0) & (j_ < grid.ncols)
            ins = np.zeros(len(S.x), dtype=bool)
            ins[ok] = mask[i_[ok], j_[ok]]
        if not ins.any():
            raise ValueError("No part of the drainage network lies inside the field.")
        self.inside = ins
        ins_idx = np.nonzero(ins)[0]
        idx, dist = nearest_sample(grid, mask, S.x[ins_idx], S.y[ins_idx])
        idx = np.where(idx >= 0, ins_idx[np.maximum(idx, 0)], -1)
        self.groups_without_pipe = []
        if groups is not None:
            groups = np.asarray(groups)
            i_ = np.floor((grid.y0 - S.y) / grid.cell).astype(np.int64)
            j_ = np.floor((S.x - grid.x0) / grid.cell).astype(np.int64)
            ok = (i_ >= 0) & (i_ < grid.nrows) & (j_ >= 0) & (j_ < grid.ncols)
            sg = np.zeros(len(S.x), dtype=np.int64)
            sg[ok] = groups[i_[ok], j_[ok]]
            for gid in np.unique(groups[mask & (groups > 0)]):
                gm = mask & (groups == gid)
                sel = ins_idx[sg[ins_idx] == gid]
                if len(sel) == 0:
                    self.groups_without_pipe.append(int(gid))
                    continue
                gi, gd = nearest_sample(grid, gm, S.x[sel], S.y[sel])
                idx = np.where(gm, np.where(gi >= 0, sel[np.maximum(gi, 0)], -1), idx)
                dist = np.where(gm, gd, dist)
        drained = mask & (idx >= 0)
        if params.max_dist is not None and params.max_dist > 0:
            drained &= dist <= params.max_dist
        self.idx = idx
        self.dist = dist
        self.drained = drained
        # eligible structure points
        elig = S.main.copy() if params.structures_on == "mains" else S.collector.copy()
        elig &= ins
        # where the network enters the field (the outlet, if it is inside) always
        # gets a structure: the first point inside the field on the way up
        ds = S.ds
        # (an outlet inside the field, or a main/submain coming in from outside;
        # a lateral that leaves the field drains out uncontrolled)
        entry = ins & ((ds < 0) | (elig & ~ins[np.maximum(ds, 0)]))
        elig[entry] = True
        self.elig = elig
        self.entry = entry
        nxt = np.where(elig, np.arange(len(elig)), ds)
        nxt = np.where(nxt < 0, np.arange(len(elig)), nxt)   # an outlet outside: a sink
        for _ in range(64):                         # pointer doubling to the attach point
            nn = nxt[nxt]
            if np.array_equal(nn, nxt):
                break
            nxt = nn
        self.att = nxt
        flat = np.nonzero(drained.ravel())[0]
        reach = elig[self.att[idx.ravel()[flat]]]
        # land whose pipe leaves the field before reaching a structure point
        self.escape_flat = flat[~reach]
        self.escape_area = len(self.escape_flat) * cell_area
        self.cell_flat = flat[reach]
        self.cell_samp = idx.ravel()[self.cell_flat]
        self.cell_att = self.att[self.cell_samp]
        self.cell_z = dem.ravel()[self.cell_flat]
        self.cell_tile = S.z[self.cell_samp]
        n = len(S.x)
        q = min(max(params.step_pct, 0.0), 50.0)
        (slo, shi), cnt = _group_percentiles(self.cell_att, self.cell_z, n, [q, 100.0 - q])
        self.step_lo = np.where(cnt > 0, slo, S.ground)
        self.step_hi = np.where(cnt > 0, shi, S.ground)
        self.elig_idx = np.nonzero(elig)[0]          # ascending = upstream order
        par = np.where(ds[self.elig_idx] >= 0, self.att[np.maximum(ds[self.elig_idx], 0)], -1)
        # an entry whose downstream pipe leaves the field and never reaches a
        # structure point is a root of its own
        par = np.where((par >= 0) & elig[np.maximum(par, 0)], par, -1)
        self.elig_parent = par

    # ------------------------------------------------------------ placement
    def place(self, interval):
        """Greedy structure placement. Returns zone id (1..n) per sample and the
        list of structure sample indices (structure k is zone k+1)."""
        S = self.S
        n = len(S.x)
        sid = np.zeros(n, dtype=np.int64)
        zmin = np.zeros(n)
        zmax = np.zeros(n)
        wmax = np.zeros(n)      # widest ground spread of the land joining at one point
        run = np.zeros(n)
        structs = []
        min_sp = self.p.min_spacing
        for c, p in zip(self.elig_idx, self.elig_parent):
            zl, zh = self.step_lo[c], self.step_hi[c]
            if p >= 0:
                lo = min(zmin[p], zl)
                hi = max(zmax[p], zh)
                w = max(wmax[p], zh - zl)
                d = run[p] + float(np.hypot(S.x[c] - S.x[p], S.y[c] - S.y[p]))
                # a new structure when the zone spans more than the interval -- but a
                # structure only helps with rise along the collector, not with the
                # spread of the land joining at one point (a lateral up the slope), so
                # that rise must also be at least half the interval
                if not (hi - lo > interval + 1e-9 and hi - lo - w > 0.5 * interval
                        and d >= min_sp):
                    sid[c] = sid[p]
                    zmin[c], zmax[c], wmax[c], run[c] = lo, hi, w, d
                    continue
            structs.append(c)
            sid[c] = len(structs)
            zmin[c], zmax[c], wmax[c], run[c] = zl, zh, zh - zl, 0.0
        zone = sid[self.att]
        return zone, structs

    def merge_small(self, zone, structs, min_cells):
        """Merge zones smaller than min_cells (of drained land) into a neighbour.

        A small zone loses its structure, so its land drains to the structure
        downstream and joins that zone. A root zone (at the outlet, or where the
        network enters the field) keeps its structure; if it is small, the
        smallest zone draining into it loses its structure instead. Smallest zones first, until none is small or one zone is left.
        Returns (zone, structs, number merged).
        """
        if min_cells <= 0 or len(structs) <= 1:
            return zone, structs, 0
        S = self.S
        zone = zone.copy()
        structs = list(structs)
        alive = list(range(1, len(structs) + 1))      # original zone ids still standing
        merged = 0
        stuck = set()
        while len(alive) > 1:
            cnt = np.bincount(zone[self.cell_att], minlength=len(structs) + 1)
            ds_zone = {}
            for z in alive:
                d = S.ds[structs[z - 1]]
                ds_zone[z] = int(zone[d]) if d >= 0 else 0
            small = [z for z in alive if cnt[z] < min_cells and z not in stuck]
            if not small:
                break
            z = min(small, key=lambda q: cnt[q])
            if ds_zone[z] == 0:
                ups = [q for q in alive if ds_zone[q] == z]
                if not ups:
                    stuck.add(z)          # a root with nothing draining into it
                    continue
                victim = min(ups, key=lambda q: cnt[q])
                into = z
            else:
                victim, into = z, ds_zone[z]
            zone[zone == victim] = into
            alive.remove(victim)
            merged += 1
        # renumber the survivors 1..n in the original (upstream) order
        remap = np.zeros(len(structs) + 1, dtype=np.int64)
        for k, z in enumerate(alive, start=1):
            remap[z] = k
        return remap[zone], [structs[z - 1] for z in alive], merged

    # ------------------------------------------------------------ evaluation
    def evaluate(self, zone, structs):
        S = self.S
        p = self.p
        nz = len(structs)
        cz = zone[self.cell_att] - 1                 # 0-based zone per drained cell
        (plo, pmed, phi, zmn, zmx), cnt = _group_percentiles(
            cz, self.cell_z, nz, [p.p_low, 50.0, p.p_high, 0.0, 100.0])
        s_idx = np.asarray(structs, dtype=np.int64)
        g_struct = S.ground[s_idx]
        inv = S.z[s_idx]
        low = np.where(np.isfinite(plo), np.minimum(plo, g_struct), g_struct)
        board = low - p.wt_depth
        cell_board = board[cz]
        status = np.full(len(cz), ST_OK, dtype=np.int8)
        wt_below = self.cell_z - cell_board
        status[wt_below > p.control_depth] = ST_DEEP
        status[self.cell_tile >= cell_board] = ST_FREE
        status[wt_below < 0] = ST_WET
        return {"zone_cell": cz, "status_cell": status, "p_low": plo, "median": pmed,
                "p_high": phi, "zmin": zmn, "zmax": zmx, "cells": cnt, "board": board,
                "invert": inv, "ground": g_struct, "structs": s_idx, "zone_sample": zone}

    def summary(self, ev):
        st = ev["status_cell"]
        ne = len(self.escape_flat)            # drains out of the field uncontrolled
        n = max(len(st) + ne, 1)
        rng = ev["p_high"] - ev["p_low"]
        return {
            "structures": len(ev["structs"]),
            "pct_controlled": 100.0 * float((st == ST_OK).sum()) / n,
            "pct_deep": 100.0 * float((st == ST_DEEP).sum()) / n,
            "pct_free": 100.0 * (float((st == ST_FREE).sum()) + ne) / n,
            "pct_wet": 100.0 * float((st == ST_WET).sum()) / n,
            "mean_zone_area": float(np.mean(ev["cells"])) * self.cell_area if len(st) else 0.0,
            "max_range": float(np.nanmax(rng)) if np.isfinite(rng).any() else 0.0,
        }

    def rasters(self, ev):
        """Zone id (0 = none) and control status rasters."""
        shape = self.mask.shape
        zr = np.zeros(shape[0] * shape[1], dtype=np.int32)
        sr = np.zeros(shape[0] * shape[1], dtype=np.int8)
        zr[self.cell_flat] = ev["zone_cell"] + 1
        sr[self.cell_flat] = ev["status_cell"]
        sr[self.escape_flat] = ST_FREE        # drains out of the field past no structure
        und = (self.mask & ~self.drained).ravel()
        sr[und] = ST_UNDRAINED
        return zr.reshape(shape), sr.reshape(shape)

    def zone_table(self, ev):
        """One dict per zone (also describes its structure)."""
        S = self.S
        net = self.net
        p = self.p
        st = ev["status_cell"]
        cz = ev["zone_cell"]
        nz = len(ev["structs"])
        counts = np.zeros((6, nz))
        for code in (ST_OK, ST_DEEP, ST_FREE, ST_WET):
            counts[code] = np.bincount(cz[st == code], minlength=nz)
        tot = np.maximum(ev["cells"], 1)
        # downstream structure and upstream controlled area
        ds_zone = np.zeros(nz, dtype=np.int64)
        for k, c in enumerate(ev["structs"]):
            d = S.ds[c]
            ds_zone[k] = ev["zone_sample"][d] if d >= 0 else 0
        up_cells = ev["cells"].astype(float).copy()
        for k in range(nz - 1, -1, -1):          # structures are in upstream order
            if ds_zone[k] > 0:
                up_cells[ds_zone[k] - 1] += up_cells[k]
        # tile length per zone
        seg_zone = ev["zone_sample"]
        seg_len = np.zeros(nz)
        has_ds = S.ds >= 0
        dlen = np.hypot(S.x[has_ds] - S.x[S.ds[has_ds]], S.y[has_ds] - S.y[S.ds[has_ds]])
        np.add.at(seg_len, seg_zone[has_ds] - 1, dlen)
        rows = []
        for k, c in enumerate(ev["structs"]):
            e = net.edges[int(S.edge[c])]
            rng = ev["p_high"][k] - ev["p_low"][k]
            board_h = ev["board"][k] - ev["invert"][k]
            flags = []
            if np.isfinite(rng) and rng > 1.5 * p.interval + 1e-6:
                flags.append("ground range {:.2f} is over 1.5 x the interval".format(rng))
            if board_h <= 0:
                flags.append("no control: tile at the structure is shallower than the "
                             "managed water-table depth")
            if ev["cells"][k] == 0:
                flags.append("controls no land")
            pct_ok = 100.0 * counts[ST_OK][k] / tot[k] if ev["cells"][k] else 0.0
            if ev["cells"][k] and pct_ok < 50.0:
                flags.append("less than half the zone is controlled")
            rows.append({
                "zone": k + 1,
                "x": float(S.x[c]), "y": float(S.y[c]),
                "kind": e.kind or ("main" if e.main else ("submain" if e.collector
                                                         else "lateral")),
                "edge_src": e.src,
                "edge_attrs": e.attrs,
                "system": int(getattr(e, "system", 1)),
                "ds_zone": int(ds_zone[k]),
                "cells": int(ev["cells"][k]),
                "area": float(ev["cells"][k]) * self.cell_area,
                "up_area": float(up_cells[k]) * self.cell_area,
                "tile_len": float(seg_len[k]),
                "ground": float(ev["ground"][k]),
                "invert": float(ev["invert"][k]),
                "tile_depth": float(ev["ground"][k] - ev["invert"][k]),
                "zmin": float(ev["zmin"][k]), "p_low": float(ev["p_low"][k]),
                "median": float(ev["median"][k]), "p_high": float(ev["p_high"][k]),
                "zmax": float(ev["zmax"][k]), "range": float(rng),
                "board": float(ev["board"][k]),
                "board_height": float(board_h),
                "board_below_ground": float(ev["ground"][k] - ev["board"][k]),
                "pct_ok": pct_ok,
                "pct_deep": 100.0 * counts[ST_DEEP][k] / tot[k] if ev["cells"][k] else 0.0,
                "pct_free": 100.0 * counts[ST_FREE][k] / tot[k] if ev["cells"][k] else 0.0,
                "pct_wet": 100.0 * counts[ST_WET][k] / tot[k] if ev["cells"][k] else 0.0,
                "flag": "; ".join(flags),
            })
        return rows

    def network_pieces(self, ev):
        """Network edges cut at the structures, each piece with its zone."""
        S = self.S
        zone = ev["zone_sample"]
        is_struct = np.zeros(len(S.x), dtype=bool)
        is_struct[ev["structs"]] = True
        pieces = []
        for ei, e in enumerate(self.net.edges):
            a, b = S.edge_first[ei], S.edge_last[ei]
            own = np.arange(a, b + 1)
            node = S.ds[a] if e.parent >= 0 else a
            z0 = zone[node]
            cuts, zones = [], [z0]
            for k in own:
                if is_struct[k] and 1e-6 < S.chain[k] < e.length - 1e-6:
                    cuts.append(float(S.chain[k]))
                    zones.append(zone[k])
            parts = _cut(e.xyz, cuts) if cuts else [e.xyz]
            if len(parts) != len(zones):            # degenerate cut: keep one zone each
                zones = (zones + [zones[-1]] * len(parts))[:len(parts)]
            for q, zq in zip(parts, zones):
                pieces.append({"xyz": q[::-1].copy(),     # downstream direction
                               "zone": int(zq), "edge": ei, "kind": e.kind or (
                                   "main" if e.main else ("submain" if e.collector
                                                          else "lateral")),
                               "order": e.order, "src": e.src,
                               "system": int(getattr(e, "system", 1)),
                               "length": float(_cumlen(q[:, :2])[-1])})
        return pieces


def options_table(zoning, intervals, min_cells=0):
    rows = []
    for iv in intervals:
        zone, structs = zoning.place(iv)
        zone, structs, _ = zoning.merge_small(zone, structs, min_cells)
        ev = zoning.evaluate(zone, structs)
        s = zoning.summary(ev)
        s["interval"] = iv
        rows.append(s)
    return rows


# ---------------------------------------------------------------- band mode
def _shift(a, di, dj, fill):
    out = np.full_like(a, fill)
    nr, nc = a.shape
    r0, r1 = max(0, di), nr + min(0, di)
    c0, c1 = max(0, dj), nc + min(0, dj)
    if r1 <= r0 or c1 <= c0:
        return out
    out[r0 - di:r1 - di, c0 - dj:c1 - dj] = a[r0:r1, c0:c1]
    return out


def label_patches(zr):
    """Label 4-connected patches of equal non-zero value: 1..n (0 stays 0)."""
    nr, nc = zr.shape
    n = nr * nc
    lab = np.arange(n, dtype=np.int64).reshape(nr, nc)
    big = n + 1
    lab = np.where(zr > 0, lab, big)
    for _ in range(n):
        new = lab
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = _shift(lab, di, dj, big)
            same = _shift(zr, di, dj, 0) == zr
            new = np.where(same & (zr > 0), np.minimum(new, nb), new)
        flat = new.ravel()
        ok = flat < big
        flat[ok] = np.minimum(flat[ok], flat[flat[ok]])   # pointer jumping
        new = flat.reshape(nr, nc)
        if np.array_equal(new, lab):
            break
        lab = new
    out = np.zeros((nr, nc), dtype=np.int32)
    m = lab < big
    _, inv = np.unique(lab[m], return_inverse=True)
    out[m] = inv.astype(np.int32) + 1
    return out


def fill_from_neighbours(zr, mask):
    """Fill zero cells inside mask from their non-zero 4-neighbours, repeatedly."""
    zr = zr.copy()
    for _ in range(max(zr.shape)):
        hole = mask & (zr == 0)
        if not hole.any():
            break
        best = np.zeros_like(zr)
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = _shift(zr, di, dj, 0)
            best = np.where((best == 0) & (nb > 0), nb, best)
        upd = hole & (best > 0)
        if not upd.any():
            break
        zr[upd] = best[upd]
    return zr


class BandZoning:
    def __init__(self, grid, dem, mask, params, cell_area):
        self.g = grid
        self.dem = dem
        self.mask = mask
        self.p = params
        self.cell_area = cell_area
        self.zs = box_smooth(dem, mask, params.band_smooth)
        self.base = float(np.nanmin(self.zs[mask])) if mask.any() else 0.0

    def bands(self, interval, min_cells=0):
        """Elevation bands, each connected patch its own zone (1..n, 0 outside).
        Patches smaller than min_cells are absorbed by their neighbours."""
        zr = np.zeros(self.mask.shape, dtype=np.int32)
        m = self.mask & np.isfinite(self.zs)
        zr[m] = np.floor((self.zs[m] - self.base) / interval).astype(np.int32) + 1
        lab = label_patches(zr)
        if min_cells > 0 and lab.max() > 0:
            cnt = np.bincount(lab.ravel())
            small = (cnt < min_cells)
            small[0] = False
            if small.any() and not small[1:].all():
                zr[small[lab]] = 0
                zr = fill_from_neighbours(zr, m)
                lab = label_patches(zr)
        return lab

    def evaluate(self, zr):
        p = self.p
        m = zr > 0
        ids = np.unique(zr[m])
        remap = np.zeros(int(zr.max()) + 1, dtype=np.int64)
        remap[ids] = np.arange(len(ids))
        cz = remap[zr[m]]
        cell_z = self.dem[m]
        nz = len(ids)
        (plo, pmed, phi, zmn, zmx), cnt = _group_percentiles(
            cz, cell_z, nz, [p.p_low, 50.0, p.p_high, 0.0, 100.0])
        board = plo - p.wt_depth
        wt_below = cell_z - board[cz]
        status = np.full(len(cz), ST_OK, dtype=np.int8)
        status[wt_below > p.control_depth] = ST_DEEP
        status[wt_below < 0] = ST_WET
        return {"ids": ids, "zone_cell": cz, "status_cell": status, "p_low": plo,
                "median": pmed, "p_high": phi, "zmin": zmn, "zmax": zmx, "cells": cnt,
                "board": board, "mask": m}

    def summary(self, ev):
        st = ev["status_cell"]
        n = max(len(st), 1)
        rng = ev["p_high"] - ev["p_low"]
        return {"structures": len(ev["ids"]),
                "pct_controlled": 100.0 * float((st == ST_OK).sum()) / n,
                "pct_deep": 100.0 * float((st == ST_DEEP).sum()) / n,
                "pct_free": 0.0,
                "pct_wet": 100.0 * float((st == ST_WET).sum()) / n,
                "mean_zone_area": float(np.mean(ev["cells"])) * self.cell_area
                if len(st) else 0.0,
                "max_range": float(np.nanmax(rng)) if np.isfinite(rng).any() else 0.0}

    def rasters(self, ev, zr):
        sr = np.zeros(zr.shape, dtype=np.int8)
        sr[ev["mask"]] = ev["status_cell"]
        out = np.zeros(zr.shape, dtype=np.int32)
        out[ev["mask"]] = ev["zone_cell"] + 1
        return out, sr

    def zone_table(self, ev):
        rows = []
        st, cz = ev["status_cell"], ev["zone_cell"]
        nz = len(ev["ids"])
        cnts = {c: np.bincount(cz[st == c], minlength=nz) for c in (ST_OK, ST_DEEP, ST_WET)}
        for k in range(nz):
            n = max(int(ev["cells"][k]), 1)
            rng = float(ev["p_high"][k] - ev["p_low"][k])
            rows.append({
                "zone": k + 1, "cells": int(ev["cells"][k]),
                "area": float(ev["cells"][k]) * self.cell_area,
                "zmin": float(ev["zmin"][k]), "p_low": float(ev["p_low"][k]),
                "median": float(ev["median"][k]), "p_high": float(ev["p_high"][k]),
                "zmax": float(ev["zmax"][k]), "range": rng,
                "board": float(ev["board"][k]),
                "pct_ok": 100.0 * cnts[ST_OK][k] / n, "pct_deep": 100.0 * cnts[ST_DEEP][k] / n,
                "pct_free": 0.0, "pct_wet": 100.0 * cnts[ST_WET][k] / n,
                "flag": "",
            })
        return rows


def band_options(bz, intervals, min_cells=0):
    rows = []
    for iv in intervals:
        s = bz.summary(bz.evaluate(bz.bands(iv, min_cells)))
        s["interval"] = iv
        rows.append(s)
    return rows


def boundary_crossings(net, rings, tol=1e-6):
    """Points where the network's pipes cross the field boundary.

    Returns a list of dicts: x, y, tile z (interpolated from the pipe), edge index,
    chainage along the edge from its downstream end, and direction: "leaves the
    field" when the water flows out across the boundary there, "enters the field"
    when it flows in. Crossings at a node shared by two pipes are reported once.
    """
    segs = []
    for r in rings or []:
        r = np.asarray(r, dtype=float)
        if len(r) < 2:
            continue
        a = r
        b = np.roll(r, -1, axis=0)
        if np.allclose(r[0], r[-1]):
            a, b = r[:-1], r[1:]
        segs.append(np.hstack([a, b]))
    if not segs:
        return []
    R = np.vstack(segs)
    rx1, ry1, rx2, ry2 = R[:, 0], R[:, 1], R[:, 2], R[:, 3]
    out = []
    for ei, e in enumerate(net.edges):
        xyz = np.asarray(e.xyz, dtype=float)       # downstream -> upstream
        ch = np.r_[0.0, np.cumsum(np.hypot(np.diff(xyz[:, 0]), np.diff(xyz[:, 1])))]
        for k in range(len(xyz) - 1):
            px, py = xyz[k, 0], xyz[k, 1]
            qx, qy = xyz[k + 1, 0], xyz[k + 1, 1]
            dx, dy = qx - px, qy - py
            ex, ey = rx2 - rx1, ry2 - ry1
            den = dx * ey - dy * ex
            ok = np.abs(den) > 1e-12
            if not ok.any():
                continue
            with np.errstate(divide="ignore", invalid="ignore"):
                t = ((rx1 - px) * ey - (ry1 - py) * ex) / den      # along the pipe
                u = ((rx1 - px) * dy - (ry1 - py) * dx) / den      # along the ring
            hit = ok & (t >= -tol) & (t <= 1 + tol) & (u >= -tol) & (u <= 1 + tol)
            for tt in np.unique(np.clip(t[hit], 0.0, 1.0)):
                x, y = px + tt * dx, py + tt * dy
                z = xyz[k, 2] + tt * (xyz[k + 1, 2] - xyz[k, 2])
                L = np.hypot(dx, dy)
                eps = min(max(1e-3 * L, 1e-4), 0.5 * L)
                # flow runs from upstream (k + 1) to downstream (k)
                f = eps / L if L > 0 else 0.0
                up_in = points_in_rings([x + f * dx], [y + f * dy], rings)[0]
                dn_in = points_in_rings([x - f * dx], [y - f * dy], rings)[0]
                if up_in == dn_in:
                    continue                                       # grazes the boundary
                out.append({"x": float(x), "y": float(y), "z": float(z), "edge": ei,
                            "chain": float(ch[k] + tt * (ch[k + 1] - ch[k])),
                            "direction": "leaves the field" if up_in else "enters the field"})
    # one point per place (a crossing at a node shared by two pipes)
    uniq = []
    for c in out:
        if not any(abs(c["x"] - u["x"]) < 1e-3 and abs(c["y"] - u["y"]) < 1e-3
                   and c["direction"] == u["direction"] for u in uniq):
            uniq.append(c)
    return uniq


def lowest_points(zr, dem, grid):
    """Lowest ground cell of every zone: {zone: (x, y, ground, i, j)}."""
    out = {}
    ok = (zr > 0) & np.isfinite(dem)
    if not ok.any():
        return out
    ii, jj = np.nonzero(ok)
    zz = zr[ii, jj]
    gz = dem[ii, jj]
    order = np.lexsort((gz, zz))            # by zone, then ground
    first = np.r_[True, zz[order][1:] != zz[order][:-1]]
    for q in order[first]:
        i, j = int(ii[q]), int(jj[q])
        out[int(zz[q])] = (grid.x0 + (j + 0.5) * grid.cell, grid.y0 - (i + 0.5) * grid.cell,
                           float(gz[q]), i, j)
    return out
