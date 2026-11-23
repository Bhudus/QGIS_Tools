"""Steady backwater model of a tile network with a submerged outlet (numpy only).

The ditch water level sets the hydraulic grade line (HGL) at the outlet. The
HGL is stepped up the network: in each step the water surface rises by the
friction loss of the flow at the downstream depth (full-pipe loss once the
pipe is surcharged), and never falls below normal depth. Where the HGL in a
drain stands above its invert, the head that drives water from the soil into
the drain is smaller; Hooghoudt's steady-state equation gives the reduced
drainage rate,

    q / q_design = f(h_wt - (HGL - invert)) / f(h_wt),   f(h) = 8 de h + 4 h^2,

where h_wt is the height of the design water table (midway between drains)
above the drain. Less inflow means less friction loss, so the two are
iterated to a steady state. Each cell drains to its nearest pipe; the design
drainage rate (the drainage coefficient) applies with a free outlet.

All quantities are SI (m, m3/s) inside the model.
"""
import numpy as np

from .allocate import nearest_sample
from .hydraulics import (equivalent_depth, friction_slope, hooghoudt_shape, normal_depth,
                         roughness)
from .network import sample_network


class ModelParams:
    def __init__(self, dc_m_per_s, wt_depth, imp_depth, spacing, step, pipe_type=0,
                 default_d_in=4.0, max_dist=None, min_slope=1e-4):
        self.dc = dc_m_per_s            # design drainage rate, m/s
        self.wt_depth = wt_depth        # design midpoint water table depth below ground, m
        self.imp_depth = imp_depth      # impermeable layer depth below drains, m
        self.spacing = spacing          # lateral spacing, m
        self.step = step                # network sampling step, map units
        self.pipe_type = pipe_type
        self.default_d_in = default_d_in
        self.max_dist = max_dist        # cells farther from a pipe are not drained (map units)
        self.min_slope = min_slope


class BackwaterModel:
    def __init__(self, grid, ground, mask, net, p, xy_to_m=1.0, diam_in=None):
        """grid/ground/mask: DEM grid (ground in m); net: network (tile z in m);
        xy_to_m: map units -> m; diam_in: list of inside diameters (in) per edge."""
        self.p = p
        S = sample_network(net, p.step)
        self.S = S
        n = len(S.x)
        d_in = np.array([diam_in[e] if diam_in and diam_in[e] else p.default_d_in
                         for e in range(len(net.edges))], float)
        self.d_in_edge = d_in
        self.D = d_in[S.edge] * 0.0254
        self.n = roughness(d_in[S.edge], p.pipe_type)
        ds = S.ds
        has = ds >= 0
        L = np.zeros(n)
        L[has] = np.hypot(S.x[has] - S.x[ds[has]], S.y[has] - S.y[ds[has]]) * xy_to_m
        self.L = L
        # invert of each sample's own pipe at the downstream end of its step: at a
        # junction that is the branch's own invert, not the invert of the pipe it joins
        z_dn = np.where(has, S.z[np.maximum(ds, 0)], S.z)
        for ei, e in enumerate(net.edges):
            if e.parent >= 0:
                z_dn[S.edge_first[ei]] = e.xyz[0, 2]
        self.z_dn = z_dn
        slope = np.full(n, p.min_slope)
        ok = has & (L > 1e-6)
        slope[ok] = (S.z[ok] - z_dn[ok]) / L[ok]
        # the outlet sample takes the slope of the pipe just upstream of it
        root = np.nonzero(~has)[0]
        for r in root:
            kids = np.nonzero(ds == r)[0]
            if len(kids):
                slope[r] = max(slope[kids].min(), p.min_slope)
        self.slope = slope
        # depth levels for vectorised stepping (samples are indexed downstream first)
        depth = np.zeros(n, dtype=np.int64)
        for i in range(n):
            if ds[i] >= 0:
                depth[i] = depth[ds[i]] + 1
        self.levels = [np.nonzero(depth == k)[0] for k in range(int(depth.max()) + 1)]
        self.root = root
        # cells drained by each sample
        idx, dist = nearest_sample(grid, mask, S.x, S.y)
        drained = mask & (idx >= 0) & np.isfinite(ground)
        if p.max_dist:
            drained &= dist <= p.max_dist
        self.drained = drained
        self.cell_flat = np.nonzero(drained.ravel())[0]
        self.cell_s = idx.ravel()[self.cell_flat]
        self.cell_area = (grid.cell * xy_to_m) ** 2
        g = ground.ravel()[self.cell_flat]
        self.cell_ground = g
        z_wt = g - p.wt_depth                       # design water table midway
        self.cell_zwt = z_wt
        h0 = z_wt - S.z[self.cell_s]                # its height above the drain invert
        self.cell_h0 = h0
        r = float(np.median(self.D)) / 2.0 if len(self.D) else 0.05
        self.de = equivalent_depth(p.imp_depth, p.spacing, max(r, 0.02))
        self.f0 = hooghoudt_shape(np.maximum(h0, 0.0), self.de)
        self.shallow = h0 <= 0.0                    # drain at or above the design water table
        self.outlet = int(root[0])
        self.n_samples = n
        self.free = None

    # ------------------------------------------------------------------
    def _accumulate(self, q_s):
        Q = q_s.copy()
        ds = self.S.ds
        for lev in reversed(self.levels[1:]):
            np.add.at(Q, ds[lev], Q[lev])
        return Q

    def _hgl(self, Q, tail):
        """HGL at every sample for flows Q and an outlet water level `tail` (None = free)."""
        S, D, n = self.S, self.D, self.n
        yn = normal_depth(Q, self.slope, D, n)
        H = S.z + yn
        for r in self.root:
            if tail is not None:
                H[r] = max(H[r], tail)
        ds = S.ds
        for lev in self.levels[1:]:
            p = ds[lev]
            yp = H[p] - self.z_dn[lev]          # depth over this pipe's own invert
            y_use = np.clip(yp, 1e-4, D[lev])
            sf = friction_slope(Q[lev], y_use, D[lev], n[lev])
            H[lev] = np.maximum(H[p] + sf * self.L[lev], S.z[lev] + yn[lev])
        return H

    def _rates(self, H):
        """Cell drainage rate as a share of the design rate, for an HGL."""
        rise = np.maximum(H[self.cell_s] - self.S.z[self.cell_s], 0.0)
        f = hooghoudt_shape(self.cell_h0 - rise, self.de)
        ratio = np.where(self.f0 > 0, f / np.maximum(self.f0, 1e-15), 0.0)
        return np.clip(ratio, 0.0, 1.0)

    def solve(self, tail=None, iters=60, tol=1e-5):
        """Steady state for an outlet water level `tail` (m; None = free outlet)."""
        cs = self.cell_s
        n = self.n_samples
        ratio = np.ones(len(cs)) if self.free is None else self.free["ratio"].copy()
        Q = None
        for it in range(iters):
            q_s = np.bincount(cs, weights=self.p.dc * self.cell_area * ratio, minlength=n)
            Qn = self._accumulate(q_s)
            Q = Qn if Q is None else 0.5 * Q + 0.5 * Qn
            H = self._hgl(Q, tail)
            new = self._rates(H)
            delta = float(np.max(np.abs(new - ratio))) if len(new) else 0.0
            ratio = 0.5 * ratio + 0.5 * new
            if delta < tol:
                break
        q_s = np.bincount(cs, weights=self.p.dc * self.cell_area * ratio, minlength=n)
        Q = self._accumulate(q_s)
        H = self._hgl(Q, tail)
        res = {"tail": tail, "Q": Q, "H": H, "ratio": ratio, "outflow": float(Q[self.outlet]),
               "iterations": it + 1,
               "surcharged": H > self.S.z + self.D,
               "backflow": (tail is not None) & (H[cs] >= self.cell_zwt)}
        return res

    def run_free(self):
        self.free = self.solve(None)
        return self.free

    def summary(self, res):
        """Rating-curve row for a solution, relative to the free outlet."""
        free = self.free
        rel = np.where(free["ratio"] > 0, res["ratio"] / np.maximum(free["ratio"], 1e-12), 1.0)
        a = self.cell_area
        tot = max(len(rel), 1)
        rise = res["H"] - free["H"]
        return {
            "outflow": res["outflow"],
            "pct_free": 100.0 * res["outflow"] / max(free["outflow"], 1e-15),
            "pct_area_10": 100.0 * float((rel < 0.9).sum()) / tot,
            "pct_area_50": 100.0 * float((rel < 0.5).sum()) / tot,
            "pct_area_none": 100.0 * float((res["ratio"] <= 1e-3).sum()) / tot,
            "pct_backflow": 100.0 * float(np.asarray(res["backflow"]).sum()) / tot
            if res["tail"] is not None else 0.0,
            "max_rise": float(np.max(rise)) if len(rise) else 0.0,
            "surcharged_len": float(self.L[res["surcharged"]].sum()),
            "area": len(rel) * a,
        }

    def cell_relative(self, res):
        """Cell drainage rate relative to the free outlet, as a grid (NaN = not drained)."""
        free = self.free
        rel = np.where(free["ratio"] > 0, res["ratio"] / np.maximum(free["ratio"], 1e-12), 1.0)
        return rel


def main_profile(model, ground_at=None):
    """Samples of the main from the outlet up: distance (m) and their indices."""
    S = model.S
    idx = [model.outlet]
    # follow, from each main sample, the main child with the most samples upstream
    kids = {}
    for i in range(model.n_samples):
        if S.ds[i] >= 0:
            kids.setdefault(int(S.ds[i]), []).append(i)
    up = np.ones(model.n_samples)
    for lev in reversed(model.levels[1:]):
        np.add.at(up, S.ds[lev], up[lev])
    cur = model.outlet
    while True:
        c = [k for k in kids.get(cur, []) if S.main[k]]
        if not c:
            break
        cur = max(c, key=lambda k: up[k])
        idx.append(cur)
    idx = np.array(idx)
    dist = np.concatenate([[0.0], np.cumsum(model.L[idx[1:]])])
    return idx, dist
