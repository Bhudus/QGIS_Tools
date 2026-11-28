"""As-built check of a tile network (pure Python + numpy, no QGIS).

Logged points from the installation (RTK tile elevations) are matched to the
design network's links; along each link and each pipe they are compared with
the design:

* deviation of the as-built tile from the design tile at the same station;
* bellies (sags): where water is held in the tile because the tile further
  down the pipe is higher; the depth held is the highest tile downstream less
  the tile there;
* each check is made on the design tile too, so that faults of the
  installation (worse than the design) are told from those of the design;
* grade over a moving window below the minimum slope, or reversed;
* depth below the ground (from the DEM or the design ground) outside limits;
* stretches of a link with no logged points (not installed, or not logged).

The as-built tile elevation is then put at each design vertex (and, as an
option, the logged positions replace the line's inner vertices), giving an
as-built network.

All lengths and elevations in feet.
"""
import math

import numpy as np


def link_stations(xy):
    """Cumulative distance along a polyline's vertices."""
    d = np.hypot(*np.diff(xy, axis=0).T)
    return np.concatenate([[0.0], np.cumsum(d)])


def project(px, py, xy, st):
    """Nearest point on a polyline for many points: (distance, station)."""
    px = np.asarray(px, float)
    py = np.asarray(py, float)
    best_d = np.full(px.shape, np.inf)
    best_s = np.zeros(px.shape)
    for k in range(len(xy) - 1):
        ax, ay = xy[k]
        bx, by = xy[k + 1]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        if L2 <= 0:
            continue
        t = np.clip(((px - ax) * dx + (py - ay) * dy) / L2, 0.0, 1.0)
        qx, qy = ax + t * dx, ay + t * dy
        d = np.hypot(px - qx, py - qy)
        better = d < best_d
        best_d = np.where(better, d, best_d)
        best_s = np.where(better, st[k] + t * math.sqrt(L2), best_s)
    return best_d, best_s


def match(points, links, max_dist):
    """Nearest link for each logged point within max_dist.

    points: (n, 3) x, y, tile elevation. links: list of dicts with "xy" (m, 2),
    "z" (m,) design tile, "g" (m,) design ground (may be nan), "st" stations.
    Returns arrays: link index (-1 none), distance, station.
    """
    n = len(points)
    li = np.full(n, -1, dtype=int)
    dist = np.full(n, np.inf)
    sta = np.zeros(n)
    if n == 0:
        return li, dist, sta
    px, py = points[:, 0], points[:, 1]
    for k, L in enumerate(links):
        xy = L["xy"]
        lo = xy.min(axis=0) - max_dist
        hi = xy.max(axis=0) + max_dist
        cand = (px >= lo[0]) & (px <= hi[0]) & (py >= lo[1]) & (py <= hi[1])
        if not cand.any():
            continue
        d, s = project(px[cand], py[cand], xy, L["st"])
        idx = np.nonzero(cand)[0]
        better = (d < dist[idx]) & (d <= max_dist)
        li[idx[better]] = k
        dist[idx[better]] = d[better]
        sta[idx[better]] = s[better]
    return li, dist, sta


def pipes_of(links):
    """Chains of link indices in flow order (upstream first) for each pipe,
    following ds_link while the pipe name stays the same."""
    by_id = {}
    for k, L in enumerate(links):
        if L.get("id") is not None:
            by_id[L["id"]] = k
    nxt = {}
    has_up = set()
    for k, L in enumerate(links):
        ds = L.get("ds_link")
        j = by_id.get(ds) if ds else None
        if j is not None and links[j].get("pipe") == L.get("pipe") and L.get("pipe") is not None:
            nxt[k] = j
            has_up.add(j)
    chains, seen = [], set()
    for k in range(len(links)):
        if k in has_up or k in seen:
            continue
        c = [k]
        seen.add(k)
        while c[-1] in nxt and nxt[c[-1]] not in seen:
            c.append(nxt[c[-1]])
            seen.add(c[-1])
        chains.append(c)
    for k in range(len(links)):           # loops, if any
        if k not in seen:
            chains.append([k])
    return chains


def bellies(station, z, tol):
    """Water held in a profile (stations rising downstream): for each point,
    the highest tile further down less its own tile (0 where none is held).
    Returns (held, runs): runs of held > tol as (start, end, max depth)."""
    n = len(z)
    held = np.zeros(n)
    if n < 2:
        return held, []
    hold = np.maximum.accumulate(z[::-1])[::-1]      # max of z[i:] ...
    down = np.concatenate([hold[1:], [-np.inf]])     # ... of z[i+1:]
    held = np.clip(down - z, 0.0, None)
    runs = []
    k = 0
    while k < n:
        if held[k] > tol:
            j = k
            while j + 1 < n and held[j + 1] > tol:
                j += 1
            runs.append((float(station[k]), float(station[j]), float(held[k:j + 1].max())))
            k = j + 1
        else:
            k += 1
    return held, runs


def smooth(station, z, length):
    """Moving average of z over `length` (stations ascending)."""
    if length <= 0 or len(z) < 3:
        return np.asarray(z, float)
    c = np.concatenate([[0.0], np.cumsum(z)])
    lo = np.searchsorted(station, station - 0.5 * length, side="left")
    hi = np.searchsorted(station, station + 0.5 * length, side="right")
    return (c[hi] - c[lo]) / np.maximum(hi - lo, 1)


def window_grades(station, z, window, with_se=False):
    """Fall per unit length (positive = falling downstream) by a least-squares
    line through the points of the window of `window` ft ending at each point;
    nan where fewer than 3 points or less than 90% of the window is covered.
    With with_se, also the standard error of each window's grade."""
    s = np.asarray(station, float)
    z = np.asarray(z, float)
    n = len(z)
    g = np.full(n, np.nan)
    se = np.full(n, np.nan)
    if n < 3:
        return (g, se) if with_se else g
    s0 = s - s[0]                                    # better conditioned sums
    z0 = z - z[0]
    c1 = np.concatenate([[0.0], np.cumsum(s0)])
    c2 = np.concatenate([[0.0], np.cumsum(s0 * s0)])
    cz = np.concatenate([[0.0], np.cumsum(z0)])
    czz = np.concatenate([[0.0], np.cumsum(z0 * z0)])
    csz = np.concatenate([[0.0], np.cumsum(s0 * z0)])
    lo = np.searchsorted(s, s - window, side="left")
    hi = np.arange(1, n + 1)
    k = hi - lo
    span = s - s[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        ms = (c1[hi] - c1[lo]) / k
        mz = (cz[hi] - cz[lo]) / k
        sxx = (c2[hi] - c2[lo]) - k * ms * ms
        sxz = (csz[hi] - csz[lo]) - k * ms * mz
        szz = (czz[hi] - czz[lo]) - k * mz * mz
        slope = sxz / sxx
        sse = np.clip(szz - slope * sxz, 0.0, None)
        err = np.sqrt(sse / np.maximum(k - 2, 1) / sxx)
    ok = (k >= 3) & (span >= 0.9 * window) & (sxx > 0)
    g[ok] = -slope[ok]
    se[ok] = err[ok]
    return (g, se) if with_se else g


def coverage(station, length, gap):
    """Share of a link's length within gap/2 of a logged point, and the
    uncovered stretches longer than `gap` as (start, end)."""
    if length <= 0:
        return 1.0, []
    if len(station) == 0:
        if length <= gap:                    # shorter than the longest allowed gap
            return 1.0, []
        return 0.0, [(0.0, float(length))]
    s = np.sort(np.asarray(station, float))
    edges = np.concatenate([[0.0], s, [length]])
    holes = []
    covered = length
    for a, b in zip(edges[:-1], edges[1:]):
        if b - a > gap:
            covered -= (b - a) - gap
            holes.append((float(a), float(b)))
    return max(0.0, covered / length), holes


def analyse(points, links, max_dist, tol_elev, tol_belly, min_slope, window, min_depth,
            max_depth, gap, ground=None, smoothing=20.0, grade_tol=0.0,
            share_reverse=0.2, share_low=0.25):
    """Compare the logged tile with the design.

    points: (n, 3) x, y, tile. ground: optional callable(xs, ys) -> ground,
    else the design ground of the link at the station. The checks use the
    logged tile smoothed over `smoothing` ft along each pipe, so that RTK noise
    is not taken for a fault; the raw deviation is kept too. A window's grade
    is "below the minimum" when it is less than min_slope - grade_tol, and
    "reversed" when less than -grade_tol; a line is flagged for it when that
    holds over share_low (share_reverse) of its windows. Short humps and sags
    are bellies.
    Returns (pts, per_link, per_pipe): pts a dict of per-point arrays; per_link
    a list of dicts of figures; per_pipe the bellies of each pipe.
    """
    li, dist, sta = match(points, links, max_dist)
    n = len(points)
    dz = np.full(n, np.nan)
    gz = np.full(n, np.nan)
    gr = np.full(n, np.nan)
    for k, L in enumerate(links):
        m = li == k
        if m.any():
            dz[m] = np.interp(sta[m], L["st"], L["z"])
            if np.isfinite(L["g"]).all():
                gz[m] = np.interp(sta[m], L["st"], L["g"])
    if ground is not None and n:
        g2 = ground(points[:, 0], points[:, 1])
        gz = np.where(np.isfinite(g2), g2, gz)
    dev_raw = points[:, 2] - dz if n else np.zeros(0)
    tile_s = points[:, 2].astype(float).copy() if n else np.zeros(0)
    held = np.zeros(n)
    grade = np.full(n, np.nan)
    grade_se = np.zeros(n)
    held_d = np.zeros(n)                     # the same checks on the design tile
    grade_d = np.full(n, np.nan)
    pipe_runs = []
    for chain in pipes_of(links):
        off = 0.0
        idx, stc = [], []
        for k in chain:
            m = np.nonzero(li == k)[0]
            idx.append(m)
            stc.append(sta[m] + off)
            off += links[k]["st"][-1]
        idx = np.concatenate(idx) if idx else np.zeros(0, int)
        if idx.size < 2:
            continue
        stc = np.concatenate(stc)
        order = np.argsort(stc, kind="stable")
        idx, stc = idx[order], stc[order]
        # RTK noise out: the deviation from the design is smoothed, so the
        # design's own grade breaks are kept sharp
        zs = dz[idx] + smooth(stc, points[idx, 2] - dz[idx], smoothing)
        tile_s[idx] = zs
        h, runs = bellies(stc, zs, tol_belly)
        held[idx] = h
        # grade by least squares over the raw points (the fit is the smoothing)
        grade[idx], grade_se[idx] = window_grades(stc, points[idx, 2], window, with_se=True)
        held_d[idx], _ = bellies(stc, dz[idx], tol_belly)
        grade_d[idx] = window_grades(stc, dz[idx], window)
        pipe_runs.append({"links": chain, "runs": runs})
    dev = tile_s - dz
    depth = gz - tile_s
    depth_d = gz - dz
    per_link = []
    for k, L in enumerate(links):
        m = li == k
        length = float(L["st"][-1])
        cov, holes = coverage(sta[m], length, gap)
        d = dev[m]
        dd = depth[m]
        h = held[m]
        g = grade[m]
        hd, gd, ddd = held_d[m], grade_d[m], depth_d[m]
        rec = {"n": int(m.sum()), "coverage": cov, "holes": holes, "length": length}
        flags, design = [], []
        if m.any():
            order = np.argsort(sta[m])
            s_o, z_o = sta[m][order], points[m, 2][order]
            fall = fall_d = fall_se = float("nan")
            if m.sum() >= 5 and s_o[-1] - s_o[0] >= 20.0:
                coef, res_ss, *_ = np.polyfit(s_o, z_o, 1, full=True)
                fall = -float(coef[0])                       # least-squares grade
                sxx = float(((s_o - s_o.mean()) ** 2).sum())
                rss = float(res_ss[0]) if len(res_ss) else 0.0
                fall_se = math.sqrt(rss / max(len(s_o) - 2, 1) / sxx) if sxx > 0 else 0.0
                fall_d = -float(np.polyfit(s_o, dz[m][order], 1)[0])
            # short of the grade by more than the tolerance and twice the fit's
            # standard error (the log's own scatter)
            e2 = 2.0 * np.nan_to_num(grade_se[m])
            low = np.isfinite(g) & (g < min_slope - grade_tol - e2 - 1e-6)
            rev = np.isfinite(g) & (g < -grade_tol - e2 - 1e-6)
            eps = 1e-6                                       # rounding (0.0001%)
            low_d = np.isfinite(gd) & (gd < min_slope - grade_tol - eps)
            rev_d = np.isfinite(gd) & (gd < -grade_tol - eps)
            n_win = max(int(np.isfinite(g).sum()), 1)
            extra_belly = h - hd
            rec.update({
                "dev_mean": float(np.nanmean(d)), "dev_max": float(np.nanmax(np.abs(d))),
                "dev_rms": float(np.sqrt(np.nanmean(dev_raw[m] ** 2))),
                "within": float(np.mean(np.abs(d) <= tol_elev)),
                "belly": float(h.max()) if h.size else 0.0,
                "belly_design": float(hd.max()) if hd.size else 0.0,
                "flat_pct": 100.0 * float(low.sum()) / n_win,
                "reverse_pct": 100.0 * float(rev.sum()) / n_win,
                "depth_min": float(np.nanmin(dd)) if np.isfinite(dd).any() else float("nan"),
                "depth_max": float(np.nanmax(dd)) if np.isfinite(dd).any() else float("nan"),
                "grade": fall, "grade_design": fall_d,
                "offset_mean": float(dist[m].mean()), "offset_max": float(dist[m].max())})
            # installation: the tile worse than the design
            if rec["dev_max"] > tol_elev:
                flags.append("off grade {:+.2f} ft max".format(
                    float(d[np.nanargmax(np.abs(d))])))
            if extra_belly.size and extra_belly.max() > tol_belly:
                flags.append("belly {:.2f} ft".format(float(h[np.argmax(extra_belly)])))
            if (rev & ~rev_d).sum() >= max(2, share_reverse * n_win):
                flags.append("reverse grade")
            elif ((low & ~low_d).sum() >= max(2, share_low * n_win) or (
                    math.isfinite(fall) and fall < min_slope - grade_tol - 2.0 * fall_se
                    and not fall_d < min_slope - grade_tol - 1e-6)):
                flags.append("below minimum grade")
            sh = np.isfinite(dd) & (dd < min_depth - 0.5 * tol_elev) & ~(ddd < min_depth)
            dp = np.isfinite(dd) & (dd > max_depth + 0.5 * tol_elev) & ~(ddd > max_depth)
            if sh.any():
                flags.append("shallow {:.2f} ft".format(float(dd[sh].min())))
            if dp.any():
                flags.append("deep {:.2f} ft".format(float(dd[dp].max())))
            # the design itself
            if hd.size and hd.max() > tol_belly:
                design.append("belly {:.2f} ft".format(float(hd.max())))
            if rev_d.sum() >= max(2, share_reverse * n_win):
                design.append("reverse grade")
            elif low_d.sum() >= max(2, share_low * n_win):
                design.append("below minimum grade")
            if np.isfinite(ddd).any() and np.nanmin(ddd) < min_depth - 1e-9:
                design.append("shallow {:.2f} ft".format(float(np.nanmin(ddd))))
            if np.isfinite(ddd).any() and np.nanmax(ddd) > max_depth + 1e-9:
                design.append("deep {:.2f} ft".format(float(np.nanmax(ddd))))
        if cov < 0.8:
            flags.insert(0, "{:.0f}% logged".format(100 * cov))
        rec["design_flags"] = design
        rec["flags"] = flags
        per_link.append(rec)
    pts = {"link": li, "dist": dist, "station": sta, "design": dz, "dev": dev,
           "dev_raw": dev_raw, "tile_s": tile_s, "ground": gz, "depth": depth, "held": held,
           "grade": grade, "grade_se": grade_se, "held_design": held_d,
           "grade_design": grade_d,
           "depth_design": depth_d}
    return pts, per_link, pipe_runs


def as_built_line(L, sta, z, xy_pts=None, gap=30.0, move=False):
    """The link's vertices with the as-built tile elevation.

    sta, z: logged stations and tile elevations on this link. Each design
    vertex takes the tile interpolated from the logged points, where one lies
    within `gap` of it; elsewhere it keeps the design tile. With move, the
    logged positions (xy_pts) become the inner vertices (the ends stay, so
    the network stays joined). Returns (xy, z, ground or None, n_updated).
    """
    st = L["st"]
    if len(sta) == 0:
        return L["xy"], L["z"].copy(), L["g"], 0
    order = np.argsort(sta)
    s_o, z_o = np.asarray(sta)[order], np.asarray(z)[order]
    if move and xy_pts is not None and len(s_o) >= 2:
        xy_o = np.asarray(xy_pts)[order]
        inner = (s_o > 0.5) & (s_o < st[-1] - 0.5)
        s_new = np.concatenate([[0.0], s_o[inner], [st[-1]]])
        xy_new = np.vstack([L["xy"][:1], xy_o[inner], L["xy"][-1:]])
        g_new = np.interp(s_new, st, L["g"]) if np.isfinite(L["g"]).all() else None
        z_design = np.interp(s_new, st, L["z"])
    else:
        s_new, xy_new = st, L["xy"]
        g_new = L["g"]
        z_design = L["z"]
    near = np.array([np.min(np.abs(s_o - s)) <= gap for s in s_new])
    z_new = np.where(near, np.interp(s_new, s_o, z_o), z_design)
    return xy_new, z_new, g_new, int(near.sum())


# ------------------------------------------------- the as-built on its own
def order_points(xy):
    """Order scattered points of one run along it: from the end of the run's
    main axis, always on to the nearest point not yet taken (which follows a
    curved run). Returns the index order."""
    xy = np.asarray(xy, float)
    n = len(xy)
    if n < 3:
        return np.arange(n)
    c = xy - xy.mean(axis=0)
    _, _, vt = np.linalg.svd(c, full_matrices=False)
    start = int(np.argmin(c @ vt[0]))
    left = np.ones(n, dtype=bool)
    order = [start]
    left[start] = False
    cur = start
    for _ in range(n - 1):
        d = np.hypot(xy[:, 0] - xy[cur, 0], xy[:, 1] - xy[cur, 1])
        d[~left] = np.inf
        cur = int(np.argmin(d))
        order.append(cur)
        left[cur] = False
    return np.array(order)


def downhill(st, z):
    """True when the run already falls along its stations (least squares)."""
    if len(z) < 2 or st[-1] <= st[0]:
        return True
    return float(np.polyfit(st, z, 1)[0]) <= 0.0


def profile_arrays(st, z, ground, tol_belly, window, smoothing=20.0):
    """Per-point checks of one pipe's tile on its own (stations rising
    downstream): smoothed tile, water held, window grade and its standard
    error, depth."""
    st = np.asarray(st, float)
    z = np.asarray(z, float)
    zs = smooth(st, z, smoothing)
    held, runs = bellies(st, zs, tol_belly)
    g, se = window_grades(st, z, window, with_se=True)
    gr = np.asarray(ground, float) if ground is not None else np.full(len(z), np.nan)
    return {"tile_s": zs, "held": held, "grade": g, "grade_se": se, "depth": gr - zs,
            "runs": runs}


def summarise(st, z, a, tol_elev, tol_belly, min_slope, min_depth, max_depth, gap,
              grade_tol=0.0, share_reverse=0.2, share_low=0.25, check_gaps=True):
    """Figures and flags of a stretch of pipe from its per-point arrays `a`
    (slices of profile_arrays for the same points)."""
    st = np.asarray(st, float)
    z = np.asarray(z, float)
    n = len(z)
    held, g, se, depth = a["held"], a["grade"], a["grade_se"], a["depth"]
    rec = {"n": n, "length": float(st[-1] - st[0]) if n else 0.0}
    flags = []
    fall = fall_se = float("nan")
    if n >= 5 and st[-1] - st[0] >= 20.0:
        coef, res_ss, *_ = np.polyfit(st, z, 1, full=True)
        fall = -float(coef[0])
        sxx = float(((st - st.mean()) ** 2).sum())
        rss = float(res_ss[0]) if len(res_ss) else 0.0
        fall_se = math.sqrt(rss / max(n - 2, 1) / sxx) if sxx > 0 else 0.0
    e2 = 2.0 * np.nan_to_num(se)
    low = np.isfinite(g) & (g < min_slope - grade_tol - e2 - 1e-6)
    rev = np.isfinite(g) & (g < -grade_tol - e2 - 1e-6)
    n_win = max(int(np.isfinite(g).sum()), 1)
    gaps = [(float(p), float(q)) for p, q in zip(st[:-1], st[1:]) if q - p > gap] \
        if check_gaps else []
    rec.update({"belly": float(held.max()) if n else 0.0,
                "flat_pct": 100.0 * float(low.sum()) / n_win,
                "reverse_pct": 100.0 * float(rev.sum()) / n_win,
                "grade": fall, "grade_se": fall_se,
                "depth_min": float(np.nanmin(depth)) if np.isfinite(depth).any() else float("nan"),
                "depth_max": float(np.nanmax(depth)) if np.isfinite(depth).any() else float("nan"),
                "gaps": gaps})
    if rec["belly"] > tol_belly:
        flags.append("belly {:.2f} ft".format(rec["belly"]))
    if rev.sum() >= max(2, share_reverse * n_win):
        flags.append("reverse grade")
    elif low.sum() >= max(2, share_low * n_win) or (
            math.isfinite(fall) and fall < min_slope - grade_tol - 2.0 * fall_se - 1e-6):
        flags.append("below minimum grade")
    half = 0.5 * tol_elev
    if np.isfinite(rec["depth_min"]) and rec["depth_min"] < min_depth - half:
        flags.append("shallow {:.2f} ft".format(rec["depth_min"]))
    if np.isfinite(rec["depth_max"]) and rec["depth_max"] > max_depth + half:
        flags.append("deep {:.2f} ft".format(rec["depth_max"]))
    if gaps:
        flags.append("{} gap(s) in the log over {:g} ft".format(len(gaps), gap))
    rec["flags"] = flags
    return rec


def check_profile(st, z, ground, tol_elev, tol_belly, min_slope, window, min_depth,
                  max_depth, gap, smoothing=20.0, grade_tol=0.0, share_reverse=0.2,
                  share_low=0.25):
    """Check one pipe's tile on its own (stations rising downstream)."""
    a = profile_arrays(st, z, ground, tol_belly, window, smoothing)
    rec = summarise(st, z, a, tol_elev, tol_belly, min_slope, min_depth, max_depth, gap,
                    grade_tol, share_reverse, share_low)
    rec["runs"] = a["runs"]
    return a, rec


def densify(st, values, step):
    """Stations every `step` (keeping the originals) and values interpolated."""
    st = np.asarray(st, float)
    extra = []
    for a, b in zip(st[:-1], st[1:]):
        k = int(math.ceil((b - a) / step))
        if k > 1:
            extra.append(a + (b - a) * np.arange(1, k) / k)
    s2 = np.unique(np.concatenate([st] + extra)) if extra else st.copy()
    return s2, [np.interp(s2, st, np.asarray(v, float)) for v in values]
