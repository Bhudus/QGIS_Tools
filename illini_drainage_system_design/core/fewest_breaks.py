"""Bury a pipe as a chain of straight runs (pure Python + numpy).

The tile along a pipe is a chain of straight runs joined at grade breaks. At
every vertex it must lie between `lo` (the maximum depth) and `hi` (the
minimum cover), every run must rise upstream at a grade between `gmin` and
`gmax`, and with `max_change` the grade may change by at most that much at a
break. Two ways of choosing among the profiles that fit:

- mode "fewest": the fewest runs; among those, the profile closest to `mid`
  (the middle of the target depth band), by length-weighted squared distance.
- mode "cost": the least total cost, where every run costs `break_cost`
  (a length: the cost of a grade break, as tile run that far 1 `unit` off
  the target), every metre of tile costs (its distance from `mid` / unit)^2,
  and, with `excavation` > 0, every metre also costs excavation x depth / unit.

Breaks are allowed at the candidate vertices `cand`, with tile elevations on
a grid of `dz` between lo and hi there. The search over those states is exact:
runs only go upstream, so the states are settled in order from the downstream
end, and with a grade-change limit each state is also split by the grade of
the run below it (in bins `max_change` wide, keeping that run's exact grade).
Each run is checked at every vertex it passes, not only at the candidates.

Vertices run upstream (index 0) to downstream (index n - 1), with stations
`s` increasing downstream. Elevations and stations in metres.
"""
import math

import numpy as np

EPS = 1e-7


def _weights(s):
    """Length each vertex stands for (half of each neighbouring interval)."""
    d = np.diff(s)
    w = np.zeros(len(s))
    w[:-1] += 0.5 * d
    w[1:] += 0.5 * d
    return np.maximum(w, 1e-9)


def _grid(a, b, dz):
    if b < a:
        a = b = 0.5 * (a + b)
    k = int(np.floor((b - a) / dz + 1e-9)) + 1
    lev = a + dz * np.arange(k)
    if (b - a) - (k - 1) * dz > 0.25 * dz:
        lev = np.append(lev, b)
    return lev


def solve(s, lo, hi, mid, gmin, gmax, start, cand, dz=0.02, min_run=0.0, mode="fewest",
          break_cost=0.0, unit=1.0, excavation=0.0, z=None, max_change=None,
          start_grade=None, _all_top=False):
    """Best profile of one pipe, or None when none fits.

    start: ("fixed", t) for a tile elevation already set at the downstream end
    (the pipe it joins, or a fixed outlet), or ("window", t_low, t_high).
    start_grade: grade of the tile below the downstream end, for the grade-change
    limit across it (None: no limit there).
    Returns {"breaks": [vertex...] upstream -> downstream, "tile": [elevation...],
    "runs": n, "cost": total}.
    """
    s = np.asarray(s, float)
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    mid = np.asarray(mid, float)
    zz = np.asarray(z if z is not None else mid, float)
    n = len(s)
    w = _weights(s)
    cand = sorted({int(c) for c in cand if 0 <= int(c) < n} | {0, n - 1})
    nC = len(cand)
    cv = np.array(cand)
    last = nC - 1
    fewest = mode == "fewest"
    inv_u2 = 1.0 / (unit * unit)
    exc = float(excavation) / unit if excavation else 0.0
    limit = max_change is not None and max_change > 0
    if limit:
        width = float(max_change)
        NB = max(1, int(math.ceil((gmax - gmin) / width - 1e-9)) + 1)
    else:
        width, NB = 1.0, 1

    def bins(g):
        if not limit:
            return np.zeros(np.shape(g), dtype=np.int64)
        g = np.nan_to_num(g, nan=gmin, posinf=gmax, neginf=gmin)
        return np.clip(np.floor((g - gmin) / width + 1e-9), 0, NB - 1).astype(np.int64)

    levels = [_grid(lo[v], hi[v], dz) for v in cand[:-1]]
    if start[0] == "fixed":
        levels.append(np.array([float(start[1])]))
    else:
        levels.append(_grid(float(start[1]), float(start[2]), dz))
    K = max(len(x) for x in levels)
    LEV = np.full((nC, K), np.nan)
    for c, lev in enumerate(levels):
        LEV[c, :len(lev)] = lev

    INF = np.inf
    PRI = np.full((nC, K, NB), INF)          # runs (fewest) or 0 (cost)
    SEC = np.full((nC, K, NB), INF)          # deviation (fewest) or total cost
    GIN = np.full((nC, K, NB), np.nan)       # grade of the run below this break
    PRED = np.full((nC, K, NB, 3), -1, dtype=np.int64)
    ok_end = ~np.isnan(LEV[last])
    tl = LEV[last, ok_end]
    PRI[last, ok_end, 0] = 0.0
    if start[0] == "fixed":
        SEC[last, ok_end, 0] = 0.0
    else:
        e0 = w[-1] * (tl - mid[-1]) ** 2 * inv_u2
        if exc:
            e0 = e0 + w[-1] * exc * (zz[-1] - tl)
        SEC[last, ok_end, 0] = e0
    if start_grade is not None:
        GIN[last, ok_end, 0] = float(start_grade)

    for cb in range(last, 0, -1):
        act = np.isfinite(PRI[cb])
        # states that cannot beat the best profile already reaching the top
        top = np.isfinite(PRI[0])
        if top.any() and not _all_top:
            if fewest:
                act &= PRI[cb] + 1.0 <= np.min(PRI[0]) + 1e-9
            else:
                act &= SEC[cb] + break_cost < np.min(SEC[0]) - 1e-12
        if not act.any():
            continue
        ks_all = np.nonzero(act.any(axis=1))[0]
        vb = cand[cb]
        tb = LEV[cb, ks_all]
        idx = np.arange(vb - 1, -1, -1)
        d = s[vb] - s[idx]
        good = d > 1e-9
        dd = np.where(good, d, 1e-9)
        Lg = (lo[idx][None, :] - tb[:, None]) / dd[None, :]
        Hg = (hi[idx][None, :] - tb[:, None]) / dd[None, :]
        Lg[:, ~good] = gmin
        Hg[:, ~good] = gmax
        Glo = np.maximum.accumulate(np.maximum(Lg, gmin), axis=1)
        Ghi = np.minimum.accumulate(np.minimum(Hg, gmax), axis=1)
        alive = (Glo <= Ghi + EPS).any(axis=0)
        if not alive[0]:
            continue
        reach = len(idx) if alive.all() else int(np.argmin(alive))
        cs = np.nonzero((cv < vb) & (cv >= vb - reach))[0]
        if not len(cs):
            continue
        pos = vb - 1 - cv[cs]
        dj = s[vb] - s[cv[cs]]
        keep = (dj >= min_run - 1e-9) | (cv[cs] == 0)
        cs, pos, dj = cs[keep], pos[keep], dj[keep]
        if not len(cs):
            continue
        wi, mi, zi = w[idx], mid[idx], zz[idx]
        Sw, Sm, Sm2 = (np.cumsum(wi)[pos], np.cumsum(wi * mi)[pos],
                       np.cumsum(wi * mi * mi)[pos])
        Sd, Sd2, Smd = (np.cumsum(wi * d)[pos], np.cumsum(wi * d * d)[pos],
                        np.cumsum(wi * mi * d)[pos])
        TJ = LEV[cs]                                          # (nc, K)
        g = (TJ[None, :, :] - tb[:, None, None]) / dj[None, :, None]  # (Kb, nc, K)
        feas = ((g >= Glo[:, pos][:, :, None] - EPS) & (g <= Ghi[:, pos][:, :, None] + EPS)
                & ~np.isnan(g))
        if not feas.any():
            continue
        t0 = tb[:, None, None]
        run = (Sw[None, :, None] * t0 * t0 + Sm2[None, :, None]
               + g * g * Sd2[None, :, None] - 2 * t0 * Sm[None, :, None]
               + 2 * g * t0 * Sd[None, :, None] - 2 * g * Smd[None, :, None]) * inv_u2
        if exc:
            Szw = np.cumsum(wi * zi)[pos]
            run = run + exc * (Szw[None, :, None] - t0 * Sw[None, :, None]
                               - g * Sd[None, :, None])
        if not fewest:
            run = run + break_cost
        m_idx = np.arange(K)[None, :]
        if not limit:
            # every state in bin 0: reduce over the source levels at once
            pri_e = np.where(act[ks_all, 0], PRI[cb, ks_all, 0], INF) + (
                1.0 if fewest else 0.0)                                 # (Kb,)
            P3 = np.where(feas, pri_e[:, None, None], INF)
            pmin = P3.min(axis=0)
            S3 = np.where(feas & (P3 <= pmin[None] + 1e-9),
                          SEC[cb, ks_all, 0][:, None, None] + run, INF)
            best = np.argmin(S3, axis=0)                                # (nc, K)
            smin = np.take_along_axis(S3, best[None], axis=0)[0]
            cur_p = PRI[cs, :, 0]
            cur_s = SEC[cs, :, 0]
            with np.errstate(invalid="ignore"):
                better = np.isfinite(smin) & ((pmin < cur_p - 1e-9) | (
                    (np.abs(pmin - cur_p) <= 1e-9) & (smin < cur_s - 1e-12)))
            if better.any():
                aa, mm = np.nonzero(better)
                cj = cs[aa]
                PRI[cj, mm, 0] = pmin[aa, mm]
                SEC[cj, mm, 0] = smin[aa, mm]
                PRED[cj, mm, 0, 0] = cb
                PRED[cj, mm, 0, 1] = ks_all[best[aa, mm]]
                PRED[cj, mm, 0, 2] = 0
            continue
        gb = bins(g)
        add = 1.0 if fewest else 0.0
        for e_k, k in enumerate(ks_all):
            bs = np.nonzero(act[k])[0]
            if not len(bs):
                continue
            fk = feas[e_k]
            if not fk.any():
                continue
            gins = GIN[cb, k, bs]                                   # (nb,)
            F = np.broadcast_to(fk, (len(bs),) + fk.shape).copy()
            lim_b = ~np.isnan(gins)
            if lim_b.any():
                F[lim_b] &= (np.abs(g[e_k][None] - gins[lim_b][:, None, None])
                             <= max_change + EPS)
            if not F.any():
                continue
            # best incoming-grade state of this level for every target cell
            P_ = np.where(F, (PRI[cb, k, bs] + add)[:, None, None], INF)
            pmin = P_.min(axis=0)
            S_ = np.where(F & (P_ <= pmin[None] + 1e-9),
                          (SEC[cb, k, bs])[:, None, None] + run[e_k][None], INF)
            jb = np.argmin(S_, axis=0)
            smin = np.take_along_axis(S_, jb[None], axis=0)[0]
            tb_ = gb[e_k]
            cur_p = PRI[cs[:, None], m_idx, tb_]
            cur_s = SEC[cs[:, None], m_idx, tb_]
            with np.errstate(invalid="ignore"):
                better = np.isfinite(smin) & ((pmin < cur_p - 1e-9) | (
                    (np.abs(pmin - cur_p) <= 1e-9) & (smin < cur_s - 1e-12)))
            if not better.any():
                continue
            aa, mm = np.nonzero(better)
            cj = cs[aa]
            bb = tb_[aa, mm]
            PRI[cj, mm, bb] = pmin[aa, mm]
            SEC[cj, mm, bb] = smin[aa, mm]
            GIN[cj, mm, bb] = g[e_k][aa, mm]
            PRED[cj, mm, bb, 0] = cb
            PRED[cj, mm, bb, 1] = k
            PRED[cj, mm, bb, 2] = bs[jb[aa, mm]]

    top_p, top_s = PRI[0], SEC[0]
    if _all_top:
        ok = np.isfinite(top_p).any(axis=1) & ~np.isnan(LEV[0])
        return LEV[0][ok]
    if not np.isfinite(top_p).any():
        return None
    pm = np.min(top_p)
    sel = np.where(np.abs(top_p - pm) <= 1e-9, top_s, INF)
    k, b = np.unravel_index(int(np.argmin(sel)), sel.shape)
    total = float(sel[k, b])
    c = 0
    brk, tile = [], []
    guard = 0
    while guard <= nC + 2:
        brk.append(cand[c])
        tile.append(float(LEV[c, k]))
        if c == last:
            break
        c, k, b = (int(PRED[c, k, b, 0]), int(PRED[c, k, b, 1]), int(PRED[c, k, b, 2]))
        if c < 0:
            return None
        guard += 1
    if brk[-1] != n - 1:
        return None
    return {"breaks": brk, "tile": tile, "runs": len(brk) - 1, "cost": total}


def highest_end(s, lo, hi, gmin, gmax, cand, dz=0.02, min_run=0.0, max_change=None):
    """Highest tile elevation at the downstream end (vertex n - 1) from which the
    pipe can be buried within the limits, or None if it cannot be at all.

    The same search, run down the pipe from its upper end (anywhere between lo
    and hi there), with every downstream-end level it can reach.
    """
    s = np.asarray(s, float)
    n = len(s)
    sf = s[-1] - s[::-1]
    lof, hif = np.asarray(lo, float)[::-1], np.asarray(hi, float)[::-1]
    cf = sorted({n - 1 - int(c) for c in cand})
    top = solve(sf, lof, hif, 0.5 * (lof + hif), -gmax, -gmin, ("window", lof[-1], hif[-1]),
                cf, dz=dz, min_run=min_run, max_change=max_change, _all_top=True)
    if top is None or not len(top):
        return None
    return float(np.max(top))


def candidates(s, step, keep=()):
    """Vertices about `step` apart along the pipe, plus the ends and `keep`."""
    s = np.asarray(s, float)
    n = len(s)
    out = {0, n - 1} | {int(k) for k in keep if 0 <= int(k) < n}
    if step <= 0 or n <= 2:
        out |= set(range(n))
        return sorted(out)
    nxt = s[0]
    for i in range(n):
        if s[i] >= nxt - 1e-9:
            out.add(i)
            nxt = s[i] + step
    return sorted(out)
