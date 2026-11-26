# -*- coding: utf-8 -*-
"""Generate the synthetic sample fields bundled with Yield Compare.

    python tools/make_sample_data.py        (from the yield_compare folder)

Writes shapefiles to sample_data/.  Needs numpy only.  The fields are
synthetic: they are not, and are not derived from, any real field, and they
sit on a made-up local grid in feet (a LOCAL_CS .prj), so they have no
real-world location.

What is built in (the "true" answers the plugin should find):

Sample Field A  (~29 ac)  laterals E-W, 100 ft apart: too wide for the soil.
    Yield loss grows with the square of the distance from the tile, reaching
    22 bu/ac midway, so the outer quarter (midway) averages about 17 bu/ac
    less than the inner quarter (over the tile).
Sample Field B  (~24 ac)  laterals N-S, 60 ft apart: well drained.
    Loss only 3 bu/ac at midway (about 2 bu/ac midway vs over the tile),
    but the north-east corner is untiled and wet: about 30 bu/ac lower.

Both: harvest passes 30 ft wide run parallel to the laterals (a degree or so
off), with a random offset per pass (sd 5 bu/ac), smoothing along the pass
(flow delay), low yields at the start of each pass (fill delay), a few
spikes, soil patches at two scales and a gentle trend across the field.
"""

import datetime
import math
import os
import struct

import numpy as np

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sample_data")
SEED = 20261126
FT = 1.0

PRJ = ('LOCAL_CS["Yield Compare sample grid (location masked)",'
       'LOCAL_DATUM["Local datum",0],UNIT["foot",0.3048],'
       'AXIS["Easting",EAST],AXIS["Northing",NORTH]]')


# ---------------------------------------------------------------------------
# minimal shapefile writer (points, polylines, polygons)
# ---------------------------------------------------------------------------

def _dbf(path, fields, records):
    """fields: list of (name, type 'N'/'C', length, decimals)."""
    n = len(records)
    hlen = 32 + 32 * len(fields) + 1
    rlen = 1 + sum(f[2] for f in fields)
    d = datetime.date(2000, 1, 1)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<BBBBIHH20x", 3, d.year - 1900, d.month, d.day, n, hlen, rlen))
        for name, typ, ln, dec in fields:
            fh.write(struct.pack("<11sc4xBB14x", name.encode("ascii"), typ.encode("ascii"),
                                 ln, dec))
        fh.write(b"\r")
        for rec in records:
            fh.write(b" ")
            for (name, typ, ln, dec), v in zip(fields, rec):
                if typ == "N":
                    s = f"{v:.{dec}f}" if dec else f"{int(round(v))}"
                    s = s.rjust(ln)[:ln]
                else:
                    s = str(v).ljust(ln)[:ln]
                fh.write(s.encode("ascii"))
        fh.write(b"\x1a")


def _shp(path, shape_type, contents, bbox):
    """contents: list of bytes (record content without the 8-byte header)."""
    shp_len = 100 + sum(8 + len(c) for c in contents)
    shx_len = 100 + 8 * len(contents)

    def header(length):
        return (struct.pack(">i5ii", 9994, 0, 0, 0, 0, 0, length // 2)
                + struct.pack("<ii4d4d", 1000, shape_type, *bbox, 0, 0, 0, 0))

    with open(path + ".shp", "wb") as shp, open(path + ".shx", "wb") as shx:
        shp.write(header(shp_len))
        shx.write(header(shx_len))
        off = 100
        for i, c in enumerate(contents):
            shp.write(struct.pack(">ii", i + 1, len(c) // 2) + c)
            shx.write(struct.pack(">ii", off // 2, len(c) // 2))
            off += 8 + len(c)
    with open(path + ".prj", "w") as fh:
        fh.write(PRJ)
    with open(path + ".cpg", "w") as fh:
        fh.write("UTF-8")


def write_points(path, xy, fields, records):
    contents = [struct.pack("<i2d", 1, x, y) for x, y in xy]
    bbox = (xy[:, 0].min(), xy[:, 1].min(), xy[:, 0].max(), xy[:, 1].max())
    _shp(path, 1, contents, bbox)
    _dbf(path + ".dbf", fields, records)


def _multi(shape_type, parts):
    pts = np.vstack(parts)
    bb = (pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max())
    starts, k = [], 0
    for p in parts:
        starts.append(k)
        k += len(p)
    c = struct.pack("<i4dii", shape_type, *bb, len(parts), len(pts))
    c += struct.pack(f"<{len(parts)}i", *starts)
    c += pts.astype("<f8").tobytes()
    return c, bb


def write_multi(path, shape_type, geoms, fields, records):
    contents, bbs = [], []
    for parts in geoms:
        c, bb = _multi(shape_type, parts)
        contents.append(c)
        bbs.append(bb)
    bbs = np.array(bbs)
    bbox = (bbs[:, 0].min(), bbs[:, 1].min(), bbs[:, 2].max(), bbs[:, 3].max())
    _shp(path, shape_type, contents, bbox)
    _dbf(path + ".dbf", fields, records)


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------

def inside(poly, xy):
    """Even-odd point-in-polygon test (vectorised)."""
    x, y = xy[:, 0], xy[:, 1]
    res = np.zeros(len(xy), bool)
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cross = ((y1 > y) != (y2 > y)) & (x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1)
        res ^= cross
    return res


def seg_dist(xy, a, b):
    ab = b - a
    t = np.clip(((xy - a) @ ab) / (ab @ ab), 0, 1)
    p = a + t[:, None] * ab
    return np.hypot(*(xy - p).T)


def rotate(pts, ang_deg, centre):
    a = math.radians(ang_deg)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return (np.asarray(pts, float) - centre) @ R.T + centre


def smooth_field(xy, rng, scale, sd, n=400):
    """Stationary Gaussian random field by random Fourier features."""
    k = rng.normal(0, 1.0 / scale, (n, 2))
    ph = rng.uniform(0, 2 * np.pi, n)
    f = np.sqrt(2.0 / n) * np.cos(xy @ k.T + ph).sum(1)
    return sd * f / f.std()


# ---------------------------------------------------------------------------
# a field
# ---------------------------------------------------------------------------

def make_field(rng, name, origin, boundary, passes_ew, laterals, main, angle,
               spacing, base, loss_fn, untiled_fn=None):
    boundary = np.asarray(boundary, float) + origin
    xmin, ymin = boundary.min(0)
    xmax, ymax = boundary.max(0)
    centre = boundary.mean(0)

    # tile network (lateral segments and main), rotated a little off the passes
    lat = [rotate(np.asarray(l, float) + origin, angle, centre) for l in laterals]
    mn = rotate(np.asarray(main, float) + origin, angle, centre)

    # harvest passes, 30 ft header, a point every ~6.6 ft (4.5 mph, 1 s)
    header, step = 30.0, 6.6
    pts, pnum, track = [], [], []
    if passes_ew:
        centres = np.arange(ymin + header / 2, ymax, header)
    else:
        centres = np.arange(xmin + header / 2, xmax, header)
    for i, c in enumerate(centres):
        forward = i % 2 == 0
        if passes_ew:
            along = np.arange(xmin + 3, xmax - 3, step)
            p = np.c_[along, np.full(len(along), c)]
            hdg = 90 if forward else 270
        else:
            along = np.arange(ymin + 3, ymax - 3, step)
            p = np.c_[np.full(len(along), c), along]
            hdg = 0 if forward else 180
        if not forward:
            p = p[::-1]
        p = p + rng.normal(0, 1.0, p.shape)          # GPS wander
        keep = inside(boundary, p)
        p = p[keep]
        if len(p) < 5:
            continue
        pts.append(p)
        pnum.append(np.full(len(p), len(pts)))
        track.append(np.full(len(p), hdg))
    pass_len = [len(p) for p in pts]
    xy = np.vstack(pts)
    pnum = np.concatenate(pnum)
    track = np.concatenate(track)
    n = len(xy)

    # distance to the nearest tile line
    d = np.full(n, np.inf)
    for l in lat:
        for a, b in zip(l[:-1], l[1:]):
            d = np.minimum(d, seg_dist(xy, a, b))
    for a, b in zip(mn[:-1], mn[1:]):
        d = np.minimum(d, seg_dist(xy, a, b))

    local = xy - boundary.min(0)
    soil = smooth_field(xy, rng, 220.0, 9.0) + smooth_field(xy, rng, 50.0, 4.0)
    trend = 8.0 * (local[:, 1] / (ymax - ymin)) - 4.0
    drain = loss_fn(d)
    untiled = untiled_fn(d) if untiled_fn else np.zeros(n)
    pass_off = rng.normal(0, 5.0, pnum.max() + 1)[pnum]
    white = rng.normal(0, 11.0, n)
    noise = np.empty(n)
    k = 0
    for m in pass_len:                               # flow-delay smoothing along each pass
        w = white[k:k + m]
        noise[k:k + m] = np.convolve(np.r_[w[0], w, w[-1]], [0.25, 0.5, 0.25], "valid")
        k += m
    yld = base + soil + trend - drain - untiled + pass_off + noise
    k = 0
    for m in pass_len:                               # fill delay at the start of each pass
        yld[k:k + 3] *= (0.35, 0.65, 0.9)
        k += m
    spikes = rng.random(n) < 0.003
    yld[spikes] *= rng.uniform(1.4, 1.8, spikes.sum())
    yld = np.maximum(yld, 0.0)

    wet = (drain + untiled) / 30.0
    moist = 17.0 + 1.5 * wet - 0.04 * soil + rng.normal(0, 0.35, n)
    speed = 4.5 + rng.normal(0, 0.15, n)
    k = 0
    for m in pass_len:
        speed[k:k + 3] *= (0.6, 0.8, 0.95)
        k += m
    elev = 100.0 + 0.004 * local[:, 1] + 0.002 * local[:, 0] \
        + smooth_field(xy, rng, 300.0, 0.6) - 0.5 * wet
    return dict(name=name, boundary=boundary, xy=xy, yld=yld, moist=moist, speed=speed,
                pnum=pnum, track=track, elev=elev, d=d, lat=lat, main=mn,
                spacing=spacing)


def write_field(f, fname):
    n = len(f["xy"])
    fields = [("Product", "C", 8, 0), ("Pass_Num", "N", 6, 0), ("Track_deg_", "N", 9, 1),
              ("Swth_Wdth_", "N", 8, 2), ("Distance_f", "N", 8, 2), ("Speed_mph_", "N", 8, 3),
              ("Elevation_", "N", 10, 3), ("Moisture__", "N", 8, 2),
              ("Yld_Vol_Dr", "N", 10, 3)]
    recs = [("CORN", f["pnum"][i], f["track"][i], 30.0, 6.6, f["speed"][i], f["elev"][i],
             f["moist"][i], f["yld"][i]) for i in range(n)]
    write_points(os.path.join(OUT, fname), f["xy"], fields, recs)


def network_records(fields_):
    geoms, recs = [], []
    k = 0
    for f in fields_:
        sp = f["spacing"]
        for line in f["lat"]:
            k += 1
            ln = float(np.hypot(*np.diff(line, axis=0).T).sum())
            geoms.append([line])
            recs.append((k, k, "lateral", ln, sp, 4, 1, f["name"]))
        m = f["main"]
        segs = list(zip(m[:-1], m[1:]))
        for j, (a, b) in enumerate(segs):
            k += 1
            size = (6, 8, 10)[min(2, 3 * j // max(len(segs), 1))]
            geoms.append([np.vstack([a, b])])
            recs.append((k, k, "main", float(np.hypot(*(b - a))), sp, size, 2, f["name"]))
    fields = [("id", "N", 6, 0), ("pipe_id", "N", 6, 0), ("kind", "C", 10, 0),
              ("length", "N", 10, 2), ("DRAIN_SPAC", "N", 5, 0), ("NOMINAL", "N", 4, 0),
              ("TILE_ORDER", "N", 3, 0), ("field", "C", 20, 0)]
    return geoms, fields, recs


def main():
    os.makedirs(OUT, exist_ok=True)
    rng = np.random.default_rng(SEED)

    # ---- Field A: laterals E-W, 100 ft spacing, poorly drained soil
    bA = [(0, 0), (1300, 0), (1300, 780), (1080, 1000), (0, 1000)]
    latA, mainA = [], [(20, -40)]
    for y in np.arange(50, 1000, 100):
        x_end = 1275 if y <= 780 else 1080 + (1000 - y) * 220 / 220 - 25
        latA.append([(20, y), (x_end, y)])
        mainA.append((20, y))
    fA = make_field(rng, "Sample Field A", np.array([0.0, 0.0]), bA, True, latA, mainA,
                    angle=1.0, spacing=100, base=200.0,
                    loss_fn=lambda d: 22.0 * (np.minimum(d, 50.0) / 50.0) ** 2)

    # ---- Field B: laterals N-S, 60 ft spacing, well drained, untiled NE corner
    bB = [(0, 0), (1200, 0), (1200, 900), (320, 900), (0, 640)]
    latB, mainB = [], [(-40, 20)]
    for x in np.arange(30, 1200, 60):
        y_top = 875 if x >= 320 else 640 + x * 260 / 320 - 25
        if x > 860:
            y_top = 520                       # untiled wet corner north-east of here
        latB.append([(x, 20), (x, y_top)])
        mainB.append((x, 20))
    fB = make_field(rng, "Sample Field B", np.array([2600.0, 0.0]), bB, False, latB, mainB,
                    angle=-1.5, spacing=60, base=205.0,
                    loss_fn=lambda d: 3.0 * (np.minimum(d, 30.0) / 30.0) ** 2,
                    untiled_fn=lambda d: 32.0 * np.clip((d - 30.0) / 90.0, 0, 1) ** 0.7)

    write_field(fA, "sample_field_A_yield")
    write_field(fB, "sample_field_B_yield")
    geoms, fields, recs = network_records([fA, fB])
    write_multi(os.path.join(OUT, "sample_tile_networks"), 3, geoms, fields, recs)

    def ring(b):                     # shapefile outer rings run clockwise
        r = np.vstack([b, b[:1]])
        area = 0.5 * np.sum(r[:-1, 0] * r[1:, 1] - r[1:, 0] * r[:-1, 1])
        return r[::-1] if area > 0 else r
    bfields = [("name", "C", 20, 0), ("acres", "N", 8, 2), ("DRAIN_SPAC", "N", 5, 0)]
    brecs = []
    for f in (fA, fB):
        r = ring(f["boundary"])
        acres = abs(0.5 * np.sum(r[:-1, 0] * r[1:, 1] - r[1:, 0] * r[:-1, 1])) / 43560.0
        brecs.append((f["name"], acres, f["spacing"]))
    write_multi(os.path.join(OUT, "sample_field_boundaries"), 5,
                [[ring(fA["boundary"])], [ring(fB["boundary"])]], bfields, brecs)

    for f in (fA, fB):
        print(f"{f['name']}: {len(f['xy'])} points, {f['pnum'].max()} passes, "
              f"{len(f['lat'])} laterals at {f['spacing']} ft")
    return fA, fB


if __name__ == "__main__":
    main()
