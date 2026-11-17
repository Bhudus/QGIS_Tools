"""Sample drainage and subirrigation reports for a synthetic 80-acre field.
Maps are matplotlib stand-ins (QGIS renders them in the plugin)."""
import io, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MPoly
from core.compute import *
from core.design import *
from core.reports import build_drainage, build_subirrigation

HERE = os.path.dirname(__file__)
TPL = os.path.join(HERE, "..", "template")
dx = 3.0
xs = np.arange(-300, 1620, dx) + dx / 2
ys = np.arange(2940, -300, -dx) - dx / 2
X, Y = np.meshgrid(xs, ys)
rng = np.random.default_rng(1)
Z = 700 + 0.0015 * X + 0.0012 * Y - 1.2 * np.exp(-((X - 700) ** 2) / 2e4) * (Y < 1800) \
    - 1.4 * np.exp(-((X - 1000) ** 2 + (Y - 2100) ** 2) / 8e3) + rng.normal(0, 0.03, X.shape)
x0, y0 = xs[0] - dx / 2, ys[0] + dx / 2
field = [(0, 0), (1320, 0), (1320, 2640), (0, 2640)]
fmask = polygon_mask([field], x0, y0, dx, -dx, *Z.shape)
ter = depressions_and_runon(Z, fmask, dx, 0.5, 0.02, 0.5)

def zat(x, y):
    j = ((np.asarray(x) - x0) / dx).astype(int); i = ((np.asarray(y) - y0) / -dx).astype(int)
    return Z[i, j]

pipes = []
main = np.array([[700, y] for y in np.linspace(20, 2600, 60)]); mg = zat(*main.T)
pipes.append(dict(id="M1", kind="main", size="8 in", a=np.column_stack([main, mg - 4.5, mg])))
k = 0
for y in np.arange(60, 2600, 60):
    for xa, xb in ((40, 690), (710, 1280)):
        k += 1
        xx = np.linspace(xa, xb, 30); yy = np.full(30, y); g = zat(xx, yy)
        z = np.linspace(g[0] - 3.5, g[0] - 3.5 - 0.001 * (xb - xa), 30) if xa > 700 else np.linspace(g[0] - 3.6 - 0.001 * (xb - xa), g[0] - 3.6, 30)
        pipes.append(dict(id="L%d" % k, kind="lateral", size="4 in", a=np.column_stack([xx, yy, z, g])))
sched = pipe_schedule(pipes)
lat = [p["a"] for p in pipes if p["kind"] == "lateral"]
dr_l = depth_range(lat); dr_a = depth_range([p["a"] for p in pipes])
sp = lateral_spacing([a[:, :2] for a in lat])
print("pipes", len(sched), "depth", dr_l, dr_a, "spacing", sp, "deps", len(ter["deps"]), "runon", [round(r["area_ac"], 2) for r in ter["runs"]])

def fig(title, h=7.0, draw=None):
    f, ax = plt.subplots(figsize=(6.5, h), dpi=100)
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    if draw: draw(ax)
    ax.add_patch(MPoly(field, fill=False, ec="#d00000", lw=1.5))
    ax.set_xlim(-300, 1620); ax.set_ylim(-300, 2940); ax.set_title(title, loc="left", fontsize=9, weight="bold")
    b = io.BytesIO(); f.savefig(b, format="png"); plt.close(f); return b.getvalue()

def tile(ax, labels=False):
    for p in pipes:
        a = p["a"]; ax.plot(a[:, 0], a[:, 1], color="#b91c1c" if p["kind"] == "main" else "#1d4ed8", lw=1.4 if p["kind"] == "main" else .5)
    if labels:
        for r, p in zip(sched[:12], pipes[:12]):
            a = p["a"]; ax.text(a[len(a)//2, 0], a[len(a)//2, 1] + 8, r["label"], fontsize=4)

def contours(ax):
    cs = ax.contour(xs, ys, smooth(Z, 2), levels=np.arange(697, 707, 0.5), colors="#c4a77d", linewidths=.4)
    ax.clabel(cs, cs.levels[::4], fontsize=5, fmt="%.0f")

def deps(ax):
    ax.imshow(np.ma.masked_equal(ter["dep_labels"], 0) > 0, extent=(xs[0], xs[-1], ys[-1], ys[0]), cmap="Blues", vmin=0, vmax=1.2, alpha=.9)
    ax.imshow(np.ma.masked_equal(ter["run_labels"], 0) > 0, extent=(xs[0], xs[-1], ys[-1], ys[0]), cmap="Oranges", vmin=0, vmax=2, alpha=.6)

cap = "Scale 1:4,800 (1 in = 400 ft)"
dep_rows = [["D%d" % (k + 1), "Depression", "{:.2f}".format(s["area_ac"]), "{:.2f}".format(s["max_depth"]), "{:,.0f}".format(s["volume_cy"])] for k, s in enumerate(ter["deps"][:15])] + \
           [["R%d" % (k + 1), "Run-on area" + (" (beyond DEM)" if s["edge"] else ""), "{:.1f}".format(s["area_ac"]), "", ""] for k, s in enumerate(ter["runs"])]
soils = [["152A", "Drummer silty clay loam, 0 to 2 percent slopes", 43.1], ["154A", "Flanagan silt loam, 0 to 2 percent slopes", 36.9]]
common = dict(
    site=dict(county_township="Champaign / Sidney Township", latlon=fmt_latlon(40.0236, -88.0676),
              crops="F3: Corn-soybean (2019-2025: C S C S C S C)", total_acres="80.0 acres"),
    narrative="The field has been farmed in a corn-soybean rotation. Surface drainage is poor in the swale through the middle of the field.\n\nNutrient Management (590) is followed on the field.",
    existing_tile_note="Existing tile: none recorded.",
    soils=soils, dep_table=dep_rows,
    dep_note="Depressions deeper than 0.5 ft below the filled surface; run-on = land outside the field draining into it (LiDAR DEM).",
    components=[["Main (8 in)", "Corrugated polyethylene (HDPE)"], ["Laterals (4 in)", "Corrugated polyethylene (HDPE)"]],
    schedule=sched[:25],
)
imgs = dict(location=fig("Location", 5), background=fig("Field", 5, lambda ax: tile(ax)), soils=fig("Soils", 6),
            watershed=fig("Watershed", 5), depressions=fig("Depressions and run-on", 6.5, deps),
            topo=[(fig("Topographic map", 7.4, contours), cap + ". Contours 0.5 ft.")],
            system=[(fig("Drainage system map - sheet 1 of 2", 7.4, lambda ax: tile(ax, True)), cap + ". Sheet 1 of 2"),
                    (fig("Drainage system map - sheet 2 of 2", 7.4, lambda ax: tile(ax, True)), cap + ". Sheet 2 of 2")])

dd = dict(common, images=dict(imgs), captions=dict(soils=cap, watershed="Watershed from USGS StreamStats"),
          benchmark_elev="712.45 ft (NAVD88)", benchmark_desc="",
          design=dict(upper="3.0 ft", lower="4.0 ft", abs_upper="{:.1f} ft".format(dr_a[0]), abs_lower="{:.1f} ft".format(dr_a[1]),
                      spacing="{:.0f} ft".format(sp), dc="3/8 in/day (0.375 in/day)", materials="Corrugated polyethylene (HDPE)",
                      other="Surface inlet in depression D1."),
          sim_note="Illinois Drainage Guide county simulations, Champaign County, Drummer soil.")
build_drainage(os.path.join(TPL, "Drainage_Design_Template.dotx"), dd, "/tmp/dwmt/drainage.docx")

systems = [dict(id=1, struct_latlon=fmt_latlon(40.0201, -88.0745), low_latlon=fmt_latlon(40.0202, -88.0739), control_elev=699.82, area=38.0,
                map=fig("Overlay - System 1", 6.0, contours), caption=cap, note="Impacted area: zone within the irrigated area, ground up to 1 ft above the control elevation (structures set 1.6 ft apart)."),
           dict(id=2, struct_latlon=fmt_latlon(40.0236, -88.0745), low_latlon=fmt_latlon(40.0237, -88.0712), control_elev=701.38, area=39.5,
                map=fig("Overlay - System 2", 6.0, contours), caption=cap)]
sd = dict(common, images=dict(imgs, zones=fig("Zones", 6.5), irrigated=fig("Irrigated area", 6.5, tile)), captions=dict(soils=cap),
          outlet_coords=fmt_latlon(40.0102, -88.0810),
          zones_table=[["1", "38.0", "699.82", "1"], ["2", "39.5", "701.38", "2"]],
          design=dict(upper="3.0", lower="4.0", abs_upper="{:.1f}".format(dr_a[0]), abs_lower="{:.1f}".format(dr_a[1]), spacing="{:.0f}".format(sp),
                      ic="0.25", dc="0.375", materials="Corrugated polyethylene (HDPE)", other="Water supply: well, 600 gpm.",
                      note="Drainage coefficient 0.375 in/day >= irrigation coefficient 0.25 in/day."),
          irrigated=dict(dominant_soil="Drummer", spacing=95, area=78.6, note="Irrigated area: " + irrigated_buffer(sp, 95)[1] + "."),
          systems=systems)
build_subirrigation(os.path.join(TPL, "Subirrigation_Design_Template.dotx"), sd, "/tmp/dwmt/subirrigation.docx")
print("ok")
