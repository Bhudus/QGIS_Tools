"""Exercise the Illini Main Tile Locator engine on synthetic terrain and plot the results.

    python test/run_synthetic.py [plane|swale|rolling|corner|all]

Writes one PNG per case to ./out (needs matplotlib).
"""
import os
import sys
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from illini_main_tile_locator.core import Grid, LocatorParams, run_locator  # noqa: E402

OUT = os.path.join(os.getcwd(), "out")
os.makedirs(OUT, exist_ok=True)


def make_dem(kind, cell=4.0, w=500.0, hgt=420.0):
    nc, nr = int(w / cell) + 20, int(hgt / cell) + 20
    g = Grid(-10 * cell, hgt + 10 * cell, cell, nr, nc)
    X, Y = g.centers()
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 0.03, X.shape)
    if kind == "plane":
        z = 220 + 0.008 * Y + 0.002 * X
    elif kind == "swale":
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
CMAP = ListedColormap(["#d1d5db", "#86efac", "#c084fc", "#fca5a5", "#fdba74"])


def plot(name, g, z, rings, outlet, res):
    fig, ax = plt.subplots(figsize=(8, 7), dpi=100)
    ext = [g.x0, g.x0 + g.ncols * g.cell, g.y0 - g.nrows * g.cell, g.y0]
    cov = np.ma.masked_equal(res["coverage"], 255)
    ax.imshow(cov, extent=ext, cmap=CMAP, vmin=-0.5, vmax=4.5, alpha=0.8,
              interpolation="nearest")
    X, Y = g.centers()
    lv = np.arange(np.floor(z.min() * 4) / 4, z.max(), 0.25)
    ax.contour(X, Y, z, levels=lv, colors="#8a6d3b", linewidths=0.5, alpha=0.7)
    for r in rings:
        r = np.vstack([r, r[:1]])
        ax.plot(r[:, 0], r[:, 1], color="black", lw=1.2)
    for c in res["candidates"][1:12]:
        for a in c["coords"]:
            ax.plot(a[:, 0], a[:, 1], color="#64748b", lw=0.6, alpha=0.6)
    for m in res["mains"]:
        c = m["coords"]
        ax.plot(c[:, 0], c[:, 1], color="#b91c1c", lw=2.8)
    for m in res.get("submains", []):
        c = m["coords"]
        ax.plot(c[:, 0], c[:, 1], color="#ea580c", lw=1.8)
    ax.plot(*outlet, marker="*", color="#b91c1c", ms=14, mec="white")
    s = res["summary"]
    ax.set_title("{}\n{}\nserved {:.1f}%  converge {:.1f}%  no-reach {:.1f}%  uphill {:.1f}%  "
                 "main {:.0f} m  problem {:.0f} m  submains {} ({:.0f} m)".format(
                     name, s["method"], s["served_pct"], s["converge_pct"], s["noreach_pct"],
                     s["uphill_pct"], s["main_length_m"], s["problem_length_m"], s["submains"],
                     s["submain_length_m"]), fontsize=8)
    ax.set_aspect("equal")
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, name + ".png"))
    plt.close(fig)


def case(terrain, field, outlet, orient, shape="contour", align="best", tag="", **kw):
    g, z = make_dem(terrain)
    rings = [np.array(r, dtype=float) for r in FIELDS[field]]
    params = LocatorParams(spacing=24.0, lateral_orientation=orient, lateral_shape=shape,
                           alignment=align, **kw)
    t0 = time.time()
    res = run_locator(z, np.ones_like(z, bool), g, rings, outlet, params)
    dt = time.time() - t0
    name = "{}_{}_{}_{}_{}{}".format(terrain, field, orient, shape, align, tag)
    plot(name, g, z, rings, outlet, res)
    s = res["summary"]
    print("{:52s} {:5.1f}s served={:5.1f}% (main {:5.1f}%) main={:5.0f}m subs={:2d}/{:5.0f}m "
          "prob={:4.0f}m  {}".format(
              name, dt, s["served_pct"], s["main_served_pct"], s["main_length_m"],
              s["submains"], s["submain_length_m"], s["problem_length_m"], s["method"]))
    for w in res["warnings"]:
        print("     ! " + w)
    return res


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("plane", "all"):
        for o in ("parallel", "perpendicular"):
            for sh in ("contour", "straight"):
                case("plane", "rect", (250, 0), o, sh)
    if which in ("corner", "all"):
        case("plane", "rect", (0, 0), "parallel", "contour")
        case("plane", "rect", (0, 0), "parallel", "contour", tag="_nolimit",
             max_lateral_length=None)
        case("plane", "rect", (0, 0), "perpendicular", "contour")
    if which in ("swale", "all"):
        for o in ("parallel", "perpendicular"):
            case("swale", "rect", (255, 0), o, "contour")
        case("swale", "rect", (255, 0), "perpendicular", "contour", "straight")
    if which in ("subs", "all"):
        for o in ("parallel", "perpendicular"):
            case("swale", "rect", (255, 0), o, "contour", tag="_subs", add_submains=True)
            case("rolling", "irregular", (230, 0), o, "contour", tag="_subs",
                 add_submains=True)
        case("plane", "rect", (0, 0), "perpendicular", "contour", tag="_subs",
             add_submains=True)
    if which in ("rolling", "all"):
        for o in ("parallel", "perpendicular"):
            case("rolling", "irregular", (230, 0), o, "contour")
