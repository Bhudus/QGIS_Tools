"""Exercise the layout engine on synthetic terrain and plot the results."""
import itertools
import math
import os
import sys
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from illini_subsurface_drain_layout.core import Grid, LayoutParams, run_layout  # noqa: E402

OUT = os.path.join(os.getcwd(), "out")
os.makedirs(OUT, exist_ok=True)


def make_dem(kind, cell=2.0, w=500.0, hgt=420.0):
    nc, nr = int(w / cell) + 40, int(hgt / cell) + 40
    g = Grid(-20 * cell, hgt + 20 * cell, cell, nr, nc)
    X, Y = g.centers()
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 0.03, X.shape)   # LiDAR-like roughness
    if kind == "plane":
        z = 220 + 0.008 * Y + 0.002 * X
    elif kind == "swale":
        # valley running north-south through x=260, falling to the south
        z = 220 + 0.006 * Y + 4.0 * np.tanh(np.abs(X - 260 - 30 * np.sin(Y / 90.0)) / 160.0)
    elif kind == "rolling":
        z = (220 + 0.004 * Y
             + 1.6 * np.exp(-((X - 120) ** 2 + (Y - 300) ** 2) / 9000.0)
             + 1.2 * np.exp(-((X - 400) ** 2 + (Y - 330) ** 2) / 12000.0)
             - 1.0 * np.exp(-((X - 270) ** 2) / 4000.0) * (Y / 420.0)
             + 0.6 * np.sin(X / 70.0) * np.cos(Y / 110.0))
    else:
        raise ValueError(kind)
    return g, z + noise


FIELDS = {
    "rect": [[(0, 0), (500, 0), (500, 420), (0, 420)]],
    "irregular": [[(0, 40), (230, 0), (500, 30), (480, 260), (360, 420), (60, 400),
                   (90, 230)], [(330, 190), (380, 190), (380, 240), (330, 240)]],
}


def plot(name, g, z, rings, outlet, res, params):
    fig, ax = plt.subplots(figsize=(8, 7), dpi=110)
    ext = [g.x0, g.x0 + g.ncols * g.cell, g.y0 - g.nrows * g.cell, g.y0]
    gy, gx = np.gradient(z)
    hs = np.clip(0.5 + 6 * (gx - gy), 0, 1)
    ax.imshow(hs, extent=ext, cmap="gray", alpha=0.35)
    X, Y = g.centers()
    lv = np.arange(np.floor(z.min() * 4) / 4, z.max(), 0.25)
    ax.contour(X, Y, z, levels=lv, colors="#8a6d3b", linewidths=0.5, alpha=0.7)
    for r in rings:
        r = np.vstack([r, r[:1]])
        ax.plot(r[:, 0], r[:, 1], color="black", lw=1.2)
    for L in res["laterals"]:
        c = L["coords"]
        col = "#d97706" if L["attrs"]["flag"] else "#2563eb"
        ax.plot(c[:, 0], c[:, 1], color=col, lw=0.9)
    for C in res["collectors"]:
        c = C["coords"]
        ax.plot(c[:, 0], c[:, 1], color="#b91c1c",
                lw=2.6 if C["attrs"]["kind"] == "main" else 1.8)
    ax.plot(*outlet, marker="*", color="#b91c1c", ms=14, mec="white")
    s = res["summary"]
    ax.set_title("{}\n{} laterals, {:.0f} m/ha, coverage {:.1f}%, unconnected {}, deep {}"
                 .format(name, s["laterals"], s["length_per_ha_m"], s["coverage_pct"],
                         s["unconnected_laterals"], s["deep_laterals"]), fontsize=9)
    ax.set_aspect("equal")
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, name + ".png"))
    plt.close(fig)


def case(terrain, field, outlet, pattern, orient, shape, S=15.0, **kw):
    g, z = make_dem(terrain)
    rings = [np.array(r, dtype=float) for r in FIELDS[field]]
    params = LayoutParams(spacing=S, pattern=pattern, orientation=orient, shape=shape, **kw)
    t0 = time.time()
    res = run_layout(z, np.ones_like(z, bool), g, rings, outlet, params)
    dt = time.time() - t0
    name = "{}_{}_{}_{}_{}".format(terrain, field, pattern, orient, shape)
    plot(name, g, z, rings, outlet, res, params)
    s = res["summary"]
    print("{:55s} {:5.1f}s lat={:4d} cov={:5.1f}% unc={} deep={} m/ha={}".format(
        name, dt, s["laterals"], s["coverage_pct"], s["unconnected_laterals"],
        s["deep_laterals"], s["length_per_ha_m"]))
    for w in res["warnings"]:
        print("     ! " + w)
    return res


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("plane", "all"):
        for o, sh in itertools.product(("across", "along"), ("straight", "contour")):
            case("plane", "rect", (250, 0), "parallel", o, sh)
    if which in ("swale", "all"):
        for o, sh in itertools.product(("across", "along"), ("straight", "contour")):
            case("swale", "rect", (255, 0), "herringbone", o, sh)
    if which in ("rolling", "all"):
        for pat, o, sh in [("herringbone", "across", "contour"),
                           ("herringbone", "along", "contour"),
                           ("parallel", "across", "contour"),
                           ("herringbone", "along", "straight")]:
            case("rolling", "irregular", (230, 0), pat, o, sh)
