"""Exercise the zonal subirrigation layout on synthetic terrain and plot it."""
import os
import sys
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from illini_subirrigation_layout.core import (  # noqa: E402
    Grid, LayoutParams, ZoneParams, run_zonal_layout)
from illini_subirrigation_layout.core.zonal import points_in_rings  # noqa: E402

OUT = os.path.join(os.getcwd(), "out")
os.makedirs(OUT, exist_ok=True)
FT = 0.3048


def make_dem(kind, cell=2.0, w=500.0, hgt=420.0):
    nc, nr = int(w / cell) + 40, int(hgt / cell) + 40
    g = Grid(-20 * cell, hgt + 20 * cell, cell, nr, nc)
    X, Y = g.centers()
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 0.03, X.shape)
    if kind == "plane":
        z = 220 + 0.004 * Y + 0.001 * X
    elif kind == "swale":
        z = 220 + 0.003 * Y + 1.5 * np.tanh(np.abs(X - 260 - 30 * np.sin(Y / 90.0)) / 160.0)
    elif kind == "rolling":
        z = (220 + 0.003 * Y
             + 0.8 * np.exp(-((X - 120) ** 2 + (Y - 300) ** 2) / 9000.0)
             + 0.6 * np.exp(-((X - 400) ** 2 + (Y - 330) ** 2) / 12000.0)
             - 0.5 * np.exp(-((X - 270) ** 2) / 4000.0) * (Y / 420.0))
    else:
        raise ValueError(kind)
    return g, z + noise


FIELDS = {
    "rect": [[(0, 0), (500, 0), (500, 420), (0, 420)]],
    "irregular": [[(0, 40), (230, 0), (500, 30), (480, 260), (360, 420), (60, 400),
                   (90, 230)]],
}

COLORS = plt.get_cmap("tab20").colors


def plot(name, g, z, rings, outlet, res):
    fig, ax = plt.subplots(figsize=(8, 7), dpi=110)
    ext = [g.x0, g.x0 + g.ncols * g.cell, g.y0 - g.nrows * g.cell, g.y0]
    zg = res["zone_grid"].astype(float)
    zg[zg == 0] = np.nan
    ax.imshow(np.mod(zg, 20), extent=ext, cmap="tab20", vmin=0, vmax=20, alpha=0.35,
              interpolation="nearest")
    X, Y = g.centers()
    lv = np.arange(np.floor(z.min() / FT) * FT, z.max(), FT)
    ax.contour(X, Y, z, levels=lv, colors="#8a6d3b", linewidths=0.4, alpha=0.6)
    for r in rings:
        r = np.vstack([r, r[:1]])
        ax.plot(r[:, 0], r[:, 1], color="black", lw=1.2)
    for L in res["network"]:
        a = L["attrs"]
        c = L["coords"]
        if a["kind"] == "lateral":
            col = "#d97706" if a["flag"] else "#2563eb"
            ax.plot(c[:, 0], c[:, 1], color=col, lw=0.8)
        elif a["kind"] == "connector":
            ax.plot(c[:, 0], c[:, 1], color="#555", lw=1.8, ls="--")
        else:
            ax.plot(c[:, 0], c[:, 1], color="#b91c1c",
                    lw=2.4 if a["kind"] == "main" else 1.6)
    for s in res["structures"]:
        ax.plot(*s["xy"], marker="s", ms=7, color="#16a34a" if not s["flag"] else "#dc2626",
                mec="black")
        ax.annotate(str(s["zone"]), s["xy"], xytext=(4, 4), textcoords="offset points",
                    fontsize=7)
    ax.plot(*outlet, marker="*", color="#b91c1c", ms=14, mec="white")
    s = res["summary"]
    ax.set_title("{}\n{} zones, {} structures, {} laterals, controlled {:.0f}%, "
                 "cross-zone {}, unconnected {}".format(
                     name, s["zones"], s["structures"], s["laterals"], s["pct_ok"],
                     s["cross_zone_laterals"], s["unconnected_laterals"]), fontsize=8)
    ax.set_aspect("equal")
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, name + ".png"))
    plt.close(fig)


def case(name, terrain, field, outlet, pattern="parallel", orient="across", contour_grade=0.0,
         shape="contour", S=40 * FT, interval=1.0 * FT, zones=None, **kw):
    g, z = make_dem(terrain)
    rings = [np.array(r, dtype=float) for r in FIELDS[field]]
    params = LayoutParams(spacing=S, pattern=pattern, orientation=orient, shape=shape,
                          contour_grade=contour_grade, units="ft", **kw)
    zp = ZoneParams(interval=interval)
    t0 = time.time()
    res = run_zonal_layout(z, np.ones_like(z, bool), g, rings, outlet, params, zp,
                           zones=zones)
    dt = time.time() - t0
    plot(name, g, z, rings, outlet, res)
    s = res["summary"]
    print("{:34s} {:5.1f}s zones={:2d} structs={:2d} zsub={} lat={:4d} cov={:5.1f}% "
          "ok={:4.1f}% xzone={} unc={}".format(
              name, dt, s["zones"], s["structures"], s["zone_submains"], s["laterals"],
              s["coverage_pct"], s["pct_ok"], s["cross_zone_laterals"],
              s["unconnected_laterals"]))
    for zr in res["zones"]:
        print("    zone {:2d} {:5.2f} ac range {:.2f} ft board {:.2f} ft  structs {} lat {:4.0f} ft "
              "below board {:3.0f}%  ok {:3.0f}%  supply {:4.1f} gpm {}".format(
                  zr["zone"], zr["area_m2"] / 4046.86, zr["range"] / FT, zr["board"] / FT,
                  zr["structures"], zr["lateral_m"] / FT, zr["tile_below_board_pct"],
                  zr["pct_ok"], zr["supply_gpm"], zr["flag"]))
    for st in res["structures"]:
        if st["flag"]:
            print("    structure {} zone {}: {}".format(st["id"], st["zone"], st["flag"]))
    for w in res["warnings"]:
        print("     ! " + w)
    res["rings"] = rings
    return res, g, z


def containment(res, g):
    """Every lateral link's points lie in its own zone (apart from its junction end)."""
    bad = 0
    zl = res["zone_grid"]
    for L in res["network"]:
        a = L["attrs"]
        if a["kind"] != "lateral":
            continue
        for x, y in L["coords"][:-1]:
            ij = g.cell_of(x, y)
            if ij and zl[ij] > 0 and zl[ij] != a["zone"]:
                bad += 1
                break
    return bad


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    runs = []
    if which in ("plane", "all"):
        runs.append(case("plane_parallel_across", "plane", "rect", (250, 0)))
    if which in ("rolling", "all"):
        runs.append(case("rolling_parallel_across", "rolling", "irregular", (230, 0)))
        runs.append(case("rolling_herringbone_across", "rolling", "irregular", (230, 0),
                         pattern="herringbone"))
    if which in ("swale", "all"):
        runs.append(case("swale_herringbone_across", "swale", "rect", (255, 0),
                         pattern="herringbone"))
    if which in ("outside", "all"):
        runs.append(case("plane_outlet_outside", "plane", "rect", (250, -30)))
        runs.append(case("swale_herringbone_outlet_outside", "swale", "irregular",
                         (230, -30), pattern="herringbone"))
    if which in ("user", "all"):
        g, z = make_dem("plane")
        X, Y = g.centers()
        zones = np.where(X < 250, 1, 2) + 2 * (Y > 210)
        runs.append(case("plane_user_zones", "plane", "rect", (250, 0), zones=zones))
    for res, g, z in runs:
        xy = np.array([st["xy"] for st in res["structures"]]).reshape(-1, 2)
        out = int((~points_in_rings(xy[:, 0], xy[:, 1], res["rings"])).sum()) if len(xy) else 0
        print("lateral points outside their zone:", containment(res, g),
              "| structures outside the field:", out)
        assert out == 0
