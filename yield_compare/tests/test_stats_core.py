# -*- coding: utf-8 -*-
"""Checks of the Yield Compare statistics on synthetic fields.

Run from the plugin folder:  python tests/test_stats_core.py
Needs numpy only (no QGIS).  Takes about two minutes.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import stats_core as sc  # noqa: E402


def field(seed, corr_len=25.0, nx=80, ny=50, step=6.0, sd=20.0, mean=180.0, extra=None):
    """A yield-monitor-like grid of points with spatially correlated noise."""
    rng = np.random.default_rng(seed)
    gx, gy = np.meshgrid(np.arange(nx) * step, np.arange(ny) * step)
    xy = np.c_[gx.ravel(), gy.ravel()]
    k = rng.normal(0, 1 / corr_len, (300, 2))
    ph = rng.uniform(0, 2 * np.pi, 300)
    smooth = np.sqrt(2 / 300) * np.cos(xy @ k.T + ph).sum(1)
    y = mean + sd * (0.8 * smooth + 0.6 * rng.normal(size=len(xy)))
    if extra is not None:
        y = y + extra(xy)
    return y, xy


def close(a, b, tol=1e-6):
    assert abs(a - b) < tol, (a, b)


def test_distributions():
    close(sc.t_cdf(2.1, 40), 0.97895899)
    close(sc.t_cdf(-1.7, 12.5), 0.05692949)
    close(sc.chi2_sf(40, 29), 0.08393690)
    close(sc.f_cdf(0.5, 3, 7), 0.30596361)
    close(sc.t_ppf(0.975, 13), 2.16036866)


def test_independent_noise_keeps_all_points():
    kept = 0
    for s in range(10):
        rng = np.random.default_rng(s)
        xy = rng.uniform(0, 500, (4000, 2))
        if sc.spatial_summary(xy, rng.normal(size=4000))["n_eff"] == 4000:
            kept += 1
    assert kept >= 9, kept


def test_false_alarms_between_fields():
    naive = adj = 0
    for i in range(30):
        y1, xy1 = field(100 + 2 * i)
        y2, xy2 = field(101 + 2 * i)
        t = sc.analyse({"y": y1, "xy": xy1}, {"y": y2, "xy": xy2})["tests"][0]
        naive += t["p_naive"] < 0.05
        adj += t["p"] < 0.05
    assert naive >= 15, naive          # the ordinary test is badly fooled
    assert adj <= 4, adj               # about 5 % with the correction


def test_real_difference_detected():
    y1, xy1 = field(300, mean=195)
    y2, xy2 = field(301)
    r = sc.analyse({"y": y1, "xy": xy1}, {"y": y2, "xy": xy2})
    assert r["tests"][0]["p"] < 0.05
    assert r["difference"]["lo"] < 15 < r["difference"]["hi"]


def test_distance_to_tile():
    spacing = 60.0
    reach = spacing / 2

    def dist(xy):
        return np.abs(((xy[:, 1] + reach) % spacing) - reach)

    def loss(xy):
        return -12.0 * (np.minimum(dist(xy), reach) / reach) ** 2

    for extra, expect_drop in ((loss, True), (None, False)):
        y, xy = field(7, extra=extra)
        model = sc.fit_model(xy, y - y.mean())
        p = sc.distance_profile(y, dist(xy), xy, reach, 6, y.mean(), model, True)
        m = p["mid_vs_near"]
        if expect_drop:
            assert m["diff"] < 0 and m["p"] < 0.01, m
        else:
            assert m["p"] > 0.05, m


def test_pass_effects():
    """Laterals parallel to harvest passes: pass offsets must not look like a
    drainage effect once the pass column is given."""
    flagged = 0
    for i in range(20):
        rng = np.random.default_rng(700 + i)
        y, xy = field(700 + i, nx=100, ny=60, step=3.0)
        g = (xy[:, 1] // 6).astype(int)                  # passes 6 units wide
        y = y + rng.normal(0, 8, g.max() + 1)[g]         # pass offsets
        spacing = 18.0 + rng.uniform(0, 12)
        d = np.abs(((xy[:, 1] + rng.uniform(0, spacing)) % spacing) - spacing / 2)
        model = sc.fit_model(xy, y - y.mean(), g)
        m = sc.distance_profile(y, d, xy, spacing / 2, 6, y.mean(), model, True, g)
        flagged += m["mid_vs_near"]["p"] < 0.05
    assert flagged <= 3, flagged


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok ", name)
