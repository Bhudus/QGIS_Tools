# -*- coding: utf-8 -*-
"""
Natural neighbor (Sibson) interpolation on a regular grid - numpy only.

For a query point x, the Sibson weight of data point p_i is the area that the
Voronoi cell of x "steals" from the cell of p_i when x is inserted into the
Voronoi diagram, divided by the total area of the cell of x.  The interpolant
is  z(x) = sum_i w_i z_i.  It reproduces linear functions exactly, is smooth
(C1) everywhere except at the data points, never over- or undershoots the data
and is only defined inside the convex hull of the data (cells outside get NaN).

Algorithm (vectorised over all grid nodes, no per-node Python loop)
-------------------------------------------------------------------
1. From a Delaunay triangulation of the data, find for every grid node the set
   of triangles whose circumcircle contains the node (the Bowyer-Watson
   "cavity").  Edges used by exactly one cavity triangle bound the cavity and
   their end points are the natural neighbours.
2. The stolen region of neighbour p_j is the polygon
        c(j-1) -> T_1 -> ... -> T_m -> c(j)
   where c(.) are the circumcentres of (x, p, p') for consecutive boundary
   edges (the new Voronoi vertices) and T_k are circumcentres of the cavity
   triangles incident to p_j (old Voronoi vertices inside the new cell).
   Its area is accumulated segment by segment (shoelace formula about p_j);
   each segment is oriented so that its outward normal points to the site on
   the far side of it.

Nodes that are degenerate (on a data point, cocircular configurations, ...)
fall back to linear (TIN) interpolation inside the triangle that contains them.
"""
import numpy as np


class Canceled(Exception):
    """Raised when the caller requests cancellation."""


def dedupe_points(px, py, pz):
    """Drop non-finite points and average z over coincident (x, y)."""
    px = np.asarray(px, dtype=np.float64)
    py = np.asarray(py, dtype=np.float64)
    pz = np.asarray(pz, dtype=np.float64)
    ok = np.isfinite(px) & np.isfinite(py) & np.isfinite(pz)
    px, py, pz = px[ok], py[ok], pz[ok]
    if px.size == 0:
        return px, py, pz
    uniq, inv = np.unique(np.column_stack((px, py)), axis=0, return_inverse=True)
    inv = inv.ravel()
    z = np.bincount(inv, weights=pz) / np.bincount(inv)
    return uniq[:, 0].copy(), uniq[:, 1].copy(), z


def triangulate_scipy(px, py):
    """Delaunay triangulation with scipy (raises ImportError if unavailable)."""
    from scipy.spatial import Delaunay
    return Delaunay(np.column_stack((px, py))).simplices


def _seg(apx, apy, ux, uy, vx, vy, rx, ry):
    """Shoelace term of the segment u-v about the apex, oriented so that the
    outward normal of the region points along (rx, ry)."""
    cr = (ux - apx) * (vy - apy) - (uy - apy) * (vx - apx)
    s = np.sign(rx * (vy - uy) - ry * (vx - ux))
    return 0.5 * s * cr


def natural_neighbor_grid(px, py, pz, tris, xmin, ymax, cell, nrows, ncols,
                          progress=None, log=None, cancelled=None,
                          max_pairs=4000000):
    """
    Parameters
    ----------
    px, py, pz : 1-d arrays of UNIQUE points (see dedupe_points)
    tris       : (M, 3) integer array, Delaunay triangulation of the points
    xmin, ymax, cell, nrows, ncols : grid definition; cell (row, col) has its
                 centre at (xmin + (col+.5)*cell, ymax - (row+.5)*cell)
    max_pairs  : memory control - rows are processed in bands so that at most
                 about this many (node, triangle) candidates are alive at once

    Returns an (nrows, ncols) float64 array; NaN outside the convex hull.
    """
    progress = progress or (lambda f: None)
    log = log or (lambda s: None)
    cancelled = cancelled or (lambda: False)

    px = np.asarray(px, dtype=np.float64)
    py = np.asarray(py, dtype=np.float64)
    pz = np.asarray(pz, dtype=np.float64)
    npts = px.size
    if npts < 3:
        raise ValueError("At least 3 points are required.")

    W2, H2 = 0.5 * ncols * cell, 0.5 * nrows * cell
    sx = px - (xmin + W2)               # work in coordinates centred on the grid
    sy = py - (ymax - H2)
    scale = max(float(np.abs(sx).max()), float(np.abs(sy).max()), W2, H2, 1e-300)

    # ---- triangles: orient CCW, drop degenerate ones, circumcircles ------------
    tv = np.asarray(tris, dtype=np.intp).reshape(-1, 3).copy()
    ax, ay = sx[tv[:, 1]] - sx[tv[:, 0]], sy[tv[:, 1]] - sy[tv[:, 0]]
    bx, by = sx[tv[:, 2]] - sx[tv[:, 0]], sy[tv[:, 2]] - sy[tv[:, 0]]
    cross = ax * by - ay * bx
    flip = cross < 0
    tv[flip, 1], tv[flip, 2] = tv[flip, 2].copy(), tv[flip, 1].copy()
    ax[flip], bx[flip] = bx[flip].copy(), ax[flip].copy()
    ay[flip], by[flip] = by[flip].copy(), ay[flip].copy()
    cross = np.abs(cross)
    good = cross > 1e-14 * scale * scale
    tv, ax, ay, bx, by, cross = tv[good], ax[good], ay[good], bx[good], by[good], cross[good]
    if tv.shape[0] == 0:
        raise ValueError("The points are collinear - no triangulation is possible.")
    d = 2.0 * cross
    a2, b2 = ax * ax + ay * ay, bx * bx + by * by
    ucx = (by * a2 - ay * b2) / d
    ucy = (ax * b2 - bx * a2) / d
    tcx = sx[tv[:, 0]] + ucx
    tcy = sy[tv[:, 0]] + ucy
    tr2 = ucx * ucx + ucy * ucy
    tr = np.sqrt(tr2)

    # grid index window covered by each circumcircle's bounding box
    big = 4.0 * (nrows + ncols) + 8.0
    f = np.clip(np.ceil((H2 - (tcy + tr)) / cell - 0.5), -big, big)
    ci0 = np.clip(f, 0, nrows).astype(np.int64)
    f = np.clip(np.floor((H2 - (tcy - tr)) / cell - 0.5), -big, big)
    ci1 = np.clip(f, -1, nrows - 1).astype(np.int64)
    f = np.clip(np.ceil((tcx - tr + W2) / cell - 0.5), -big, big)
    cj0 = np.clip(f, 0, ncols).astype(np.int64)
    f = np.clip(np.floor((tcx + tr + W2) / cell - 0.5), -big, big)
    cj1 = np.clip(f, -1, ncols - 1).astype(np.int64)
    ncl = np.maximum(cj1 - cj0 + 1, 0)

    Xc = (np.arange(ncols) + 0.5) * cell - W2          # node x by column
    Yr = H2 - (np.arange(nrows) + 0.5) * cell          # node y by row
    eps_c2 = (1e-9 * cell) ** 2

    def process_band(i0, i1):
        nb = (i1 - i0) * ncols
        out = np.full(nb, np.nan)

        ri0 = np.maximum(ci0, i0)
        ri1 = np.minimum(ci1, i1 - 1)
        cnt = np.maximum(ri1 - ri0 + 1, 0) * ncl
        sel = np.nonzero(cnt > 0)[0]
        if sel.size == 0:
            return out
        cnt = cnt[sel]
        total = int(cnt.sum())

        # --- (node, triangle) pairs whose circumcircle holds the node ------------
        trr = np.repeat(sel, cnt)
        k = np.arange(total, dtype=np.int64) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        w = np.repeat(ncl[sel], cnt)
        ii = np.repeat(ri0[sel], cnt) + k // w
        jj = np.repeat(cj0[sel], cnt) + k % w
        del k, w
        xn, yn = Xc[jj], Yr[ii]
        # (relative tolerance: nodes lying exactly on a data point or on a
        # circumcircle must be treated consistently, not by rounding luck)
        inside = (xn - tcx[trr]) ** 2 + (yn - tcy[trr]) ** 2 <= tr2[trr] * (1.0 + 1e-10)
        trr, ii, jj, xn, yn = trr[inside], ii[inside], jj[inside], xn[inside], yn[inside]
        nd = (ii - i0) * ncols + jj
        del ii, jj, inside
        if nd.size == 0:
            return out

        # --- hull membership, linear fallback value, coincidence test ------------
        v = tv[trr]
        Ax, Ay, Bx, By, Cx, Cy = (sx[v[:, 0]], sy[v[:, 0]], sx[v[:, 1]], sy[v[:, 1]],
                                  sx[v[:, 2]], sy[v[:, 2]])
        wa = (Bx - xn) * (Cy - yn) - (By - yn) * (Cx - xn)
        wb = (Cx - xn) * (Ay - yn) - (Cy - yn) * (Ax - xn)
        wc = (Ax - xn) * (By - yn) - (Ay - yn) * (Bx - xn)
        den = wa + wb + wc
        tol = -1e-12 * den
        ins = (wa >= tol) & (wb >= tol) & (wc >= tol)
        in_hull = np.zeros(nb, dtype=bool)
        lin = np.full(nb, np.nan)
        in_hull[nd[ins]] = True
        lin[nd[ins]] = ((wa * pz[v[:, 0]] + wb * pz[v[:, 1]] + wc * pz[v[:, 2]]) / den)[ins]
        bad = np.zeros(nb, dtype=bool)
        near = (((Ax - xn) ** 2 + (Ay - yn) ** 2 < eps_c2) |
                ((Bx - xn) ** 2 + (By - yn) ** 2 < eps_c2) |
                ((Cx - xn) ** 2 + (Cy - yn) ** 2 < eps_c2))
        bad[nd[near]] = True
        del Ax, Ay, Bx, By, Cx, Cy, wa, wb, wc, den, tol, ins, near

        keep = in_hull[nd] & ~bad[nd]
        nd, trr, v = nd[keep], trr[keep], v[keep]
        out[in_hull] = lin[in_hull]             # linear value is the fallback
        if nd.size == 0:
            return out

        # --- cavity edges ----------------------------------------------------------
        ea = np.concatenate((v[:, 0], v[:, 1], v[:, 2]))
        eb = np.concatenate((v[:, 1], v[:, 2], v[:, 0]))
        en = np.tile(nd, 3)
        et = np.tile(trr, 3)
        lo, hi = np.minimum(ea, eb), np.maximum(ea, eb)
        o = np.lexsort((hi, lo, en))
        en, et, lo, hi = en[o], et[o], lo[o], hi[o]
        same = (en[1:] == en[:-1]) & (lo[1:] == lo[:-1]) & (hi[1:] == hi[:-1])
        first = np.zeros(en.size, dtype=bool)
        second = np.zeros(en.size, dtype=bool)
        first[:-1] = same
        second[1:] = same
        single = np.nonzero(~(first | second))[0]       # cavity boundary edges
        pair1 = np.nonzero(first)[0]                    # edges shared by 2 cavity triangles
        pair2 = pair1 + 1

        # boundary edges: circumcentre of the new triangle (x, a, b)
        bn, ba, bb, bt = en[single], lo[single], hi[single], et[single]
        bxn, byn = Xc[bn % ncols], Yr[i0 + bn // ncols]
        pax, pay = sx[ba] - bxn, sy[ba] - byn
        pbx, pby = sx[bb] - bxn, sy[bb] - byn
        d2 = 2.0 * (pax * pby - pay * pbx)
        qa2, qb2 = pax * pax + pay * pay, pbx * pbx + pby * pby
        degenerate = np.abs(d2) <= 1e-9 * (qa2 + qb2)
        d2 = np.where(degenerate, 1.0, d2)
        cex = bxn + (pby * qa2 - pay * qb2) / d2
        cey = byn + (pax * qb2 - pbx * qa2) / d2
        badnode = np.zeros(nb, dtype=bool)
        badnode[bn[degenerate]] = True
        Tcx, Tcy = tcx[bt], tcy[bt]

        # natural neighbours = (node, vertex) incidences of boundary edges; a
        # proper cavity polygon has exactly two boundary edges at each vertex
        inc_key = np.concatenate((bn.astype(np.int64) * npts + ba,
                                  bn.astype(np.int64) * npts + bb))
        inc_edge = np.concatenate((np.arange(bn.size), np.arange(bn.size)))
        o2 = np.argsort(inc_key, kind='stable')
        ks, es = inc_key[o2], inc_edge[o2]
        uk, cntk = np.unique(ks, return_counts=True)
        badnode[(uk // npts)[cntk != 2]] = True
        startk = np.cumsum(cntk) - cntk
        ok2 = cntk == 2
        e1 = es[startk[ok2]]
        e2 = es[startk[ok2] + 1]
        nid_ok = np.nonzero(ok2)[0]
        nv = uk % npts
        nnode = uk // npts

        area = np.zeros(uk.size)
        # (a) the new Voronoi edge between x and the neighbour
        vv = nv[nid_ok]
        nx_, ny_ = Xc[nnode[nid_ok] % ncols], Yr[i0 + nnode[nid_ok] // ncols]
        area[nid_ok] += _seg(sx[vv], sy[vv], cex[e1], cey[e1], cex[e2], cey[e2],
                             sx[vv] - nx_, sy[vv] - ny_)
        # (b) old Voronoi edge pieces cut by the new cell: new vertex -> cavity circumcentre
        for own, other in ((ba, bb), (bb, ba)):
            nid = np.searchsorted(uk, bn.astype(np.int64) * npts + own)
            val = _seg(sx[own], sy[own], cex, cey, Tcx, Tcy,
                       sx[other] - sx[own], sy[other] - sy[own])
            area += np.bincount(nid, weights=val, minlength=uk.size)
        # (c) old Voronoi edges lying completely inside the new cell
        if pair1.size:
            i_n, i_a, i_b = en[pair1], lo[pair1], hi[pair1]
            t1, t2 = et[pair1], et[pair2]
            for own, other in ((i_a, i_b), (i_b, i_a)):
                key = i_n.astype(np.int64) * npts + own
                pos = np.minimum(np.searchsorted(uk, key), uk.size - 1)
                member = uk[pos] == key
                val = _seg(sx[own], sy[own], tcx[t1], tcy[t1], tcx[t2], tcy[t2],
                           sx[other] - sx[own], sy[other] - sy[own])
                area += np.bincount(pos[member], weights=val[member], minlength=uk.size)

        den_n = np.bincount(nnode, weights=area, minlength=nb)
        num_n = np.bincount(nnode, weights=area * pz[nv], minlength=nb)
        goodnode = ~badnode & (den_n > 0) & np.isfinite(den_n) & np.isfinite(num_n)
        out[goodnode] = num_n[goodnode] / den_n[goodnode]
        return out

    # ---- bands of rows keep memory bounded -------------------------------------------
    result = np.full((nrows, ncols), np.nan)
    pending = [(0, nrows)]
    done_rows = 0
    while pending:
        if cancelled():
            raise Canceled()
        i0, i1 = pending.pop(0)
        ri0 = np.maximum(ci0, i0)
        ri1 = np.minimum(ci1, i1 - 1)
        est = int((np.maximum(ri1 - ri0 + 1, 0) * ncl).sum())
        if est > max_pairs and i1 - i0 > 1:
            mid = (i0 + i1) // 2
            pending[0:0] = [(i0, mid), (mid, i1)]
            continue
        result[i0:i1] = process_band(i0, i1).reshape(i1 - i0, ncols)
        done_rows += i1 - i0
        progress(done_rows / float(nrows))

    n_in = int(np.isfinite(result).sum())
    log("Natural neighbor: %d triangle(s), %d of %d cells inside the convex hull"
        % (tv.shape[0], n_in, nrows * ncols))
    return result
