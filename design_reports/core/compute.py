"""Pure-Python/numpy helpers for the DWM plan (no QGIS), so they can be tested alone."""
import datetime
import math
import re

import numpy as np

M_TO_FT = 1.0 / 0.3048
NICE_SCALES = (600, 1200, 1800, 2400, 3000, 3600, 4800, 6000, 7200, 9600, 12000, 14400,
               18000, 24000, 36000, 48000, 63360, 100000, 125000, 250000, 500000)


# ---------------------------------------------------------------- formatting
def fmt_latlon(lat, lon, nd=5):
    return "{:.{n}f}° {} / {:.{n}f}° {}".format(
        abs(lat), "N" if lat >= 0 else "S", abs(lon), "W" if lon < 0 else "E", n=nd)


def fmt_date(d=None):
    d = d or datetime.date.today()
    return "{} {}, {}".format(d.strftime("%B"), d.day, d.year)


def fmt_num(v, nd=1):
    return "" if v is None else "{:,.{}f}".format(v, nd)


def scale_text(scale):
    """'1:3,600 (1 in = 300 ft)'"""
    ft = scale / 12.0
    ft_s = "{:,.0f}".format(ft) if ft >= 10 else "{:,.1f}".format(ft)
    return "1:{:,.0f} (1 in = {} ft)".format(scale, ft_s)


def nice_scale(needed):
    """Smallest standard engineering scale that is at least `needed`."""
    for s in NICE_SCALES:
        if s >= needed * 0.999:
            return s
    return int(math.ceil(needed / 100000.0) * 100000)


def scalebar_length(scale, width_in):
    """A round scale-bar length in feet, about a quarter of the map width."""
    target = scale * width_in / 12.0 / 4.0
    p = 10 ** math.floor(math.log10(max(target, 1e-9)))
    best = p
    for m in (1, 2, 2.5, 5, 10):
        if m * p <= target:
            best = m * p
    return best


# ---------------------------------------------------------------- rasters
def smooth(z, radius):
    """Box-filter mean of radius `radius` cells, ignoring NaN cells."""
    if radius <= 0:
        return z.copy()
    ok = np.isfinite(z)
    zz = np.where(ok, z, 0.0)
    w = ok.astype(float)

    def box(a, r):
        a = np.pad(a, r, mode="constant")
        c = np.cumsum(np.cumsum(a, 0), 1)
        c = np.pad(c, ((1, 0), (1, 0)), mode="constant")
        n = 2 * r + 1
        return c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]
    s = box(zz, radius)
    n = box(w, radius)
    out = np.full(z.shape, np.nan)
    good = ok & (n > 0)
    out[good] = s[good] / n[good]
    return out


def polygon_mask(rings, x0, y0, dx, dy, nrows, ncols):
    """Cells (centres) inside a polygon given as a list of rings [(x, y), ...]; holes and
    multipolygons handled with the even-odd rule. x0, y0 = upper-left corner, dy < 0."""
    xs = x0 + (np.arange(ncols) + 0.5) * dx
    ys = y0 + (np.arange(nrows) + 0.5) * dy
    inside = np.zeros((nrows, ncols), bool)
    for ring in rings:
        r = np.asarray(ring, float)
        if len(r) < 3:
            continue
        xa, ya = r[:, 0], r[:, 1]
        xb, yb = np.roll(xa, -1), np.roll(ya, -1)
        for i in range(nrows):
            y = ys[i]
            cross = (ya > y) != (yb > y)
            if not cross.any():
                continue
            xi = xa[cross] + (y - ya[cross]) * (xb[cross] - xa[cross]) / (yb[cross] - ya[cross])
            xi.sort()
            cnt = np.searchsorted(xi, xs, side="right")
            inside[i] ^= (cnt % 2 == 1)
    return inside


def lowest_cell(z, mask, x0, y0, dx, dy, smooth_radius=0):
    """(x, y, z) of the lowest cell inside mask; with smoothing, the location is found
    on the smoothed surface (to skip single noisy cells) and the elevation reported is
    the smoothed one."""
    zz = smooth(z, smooth_radius) if smooth_radius else z
    m = mask & np.isfinite(zz)
    if not m.any():
        return None
    k = np.nanargmin(np.where(m, zz, np.inf))
    i, j = divmod(int(k), z.shape[1])
    return x0 + (j + 0.5) * dx, y0 + (i + 0.5) * dy, float(zz[i, j])


# ---------------------------------------------------------------- network
def depth_stats(lines):
    """Length-weighted mean and range of depth (ground M - tile Z) of polylines given as
    arrays of (x, y, z, m). Returns (mean, min, max) or None."""
    tot = acc = 0.0
    lo, hi = math.inf, -math.inf
    for a in lines:
        a = np.asarray(a, float)
        if len(a) < 2:
            continue
        d = a[:, 3] - a[:, 2]
        seg = np.hypot(np.diff(a[:, 0]), np.diff(a[:, 1]))
        dm = 0.5 * (d[1:] + d[:-1])
        ok = np.isfinite(dm) & (seg > 0)
        if not ok.any():
            continue
        tot += seg[ok].sum()
        acc += (seg[ok] * dm[ok]).sum()
        fin = d[np.isfinite(d)]
        lo, hi = min(lo, fin.min()), max(hi, fin.max())
    if tot <= 0:
        return None
    return acc / tot, lo, hi


def lateral_spacing(lines, step=None):
    """Typical spacing of laterals: median distance from sample points on each lateral to
    the nearest other lateral (2D polylines, list of (n, 2+) arrays). None if < 2."""
    segs, owner = [], []
    for k, a in enumerate(lines):
        a = np.asarray(a, float)[:, :2]
        for p, q in zip(a[:-1], a[1:]):
            if np.hypot(*(q - p)) > 0:
                segs.append((p, q))
                owner.append(k)
    if len(set(owner)) < 2:
        return None
    P = np.array([s[0] for s in segs])
    Q = np.array([s[1] for s in segs])
    owner = np.array(owner)
    total = sum(np.hypot(*(q - p)) for p, q in segs)
    step = step or max(total / 2000.0, 1e-6)
    dists = []
    for k, a in enumerate(lines):
        a = np.asarray(a, float)[:, :2]
        s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(a, axis=0).T))])
        if s[-1] <= 0:
            continue
        # skip the ends (tees and stubs) - middle 80 %
        ts = np.arange(0.1 * s[-1], 0.9 * s[-1] + 1e-9, step)
        if len(ts) == 0:
            continue
        pts = np.column_stack([np.interp(ts, s, a[:, 0]), np.interp(ts, s, a[:, 1])])
        other = owner != k
        if not other.any():
            continue
        p, q = P[other], Q[other]
        d = q - p
        L2 = (d ** 2).sum(1)
        for pt in pts:
            t = np.clip(((pt - p) * d).sum(1) / L2, 0, 1)
            c = p + t[:, None] * d
            dists.append(np.sqrt(((c - pt) ** 2).sum(1)).min())
    if not dists:
        return None
    return float(np.median(dists))


def component_rows(items, material):
    """items: (kind, size_text, length[, material]). Rows [component, material, length]
    sorted main -> submain -> lateral, then by size; `material` where an item has none."""
    order = {"main": 0, "submain": 1, "lateral": 2}
    names = {"main": "Main", "submain": "Submain", "lateral": "Laterals"}
    agg = {}
    for it in items:
        kind, size, length = it[:3]
        mat = (it[3] if len(it) > 3 else "") or material
        k = (kind or "tile").lower()
        key = (k, size or "", mat)
        agg[key] = agg.get(key, 0.0) + (length or 0.0)

    def size_val(s):
        m = re.search(r"[\d.]+", s or "")
        return float(m.group(0)) if m else 0.0
    rows = []
    for (k, size, mat), length in sorted(agg.items(), key=lambda kv: (
            order.get(kv[0][0], 3), -size_val(kv[0][1]), kv[0][2])):
        label = names.get(k, k.capitalize())
        if size:
            label += " ({})".format(size)
        rows.append([label, mat, length])
    return rows


# ---------------------------------------------------------------- soils
def soil_rows(pieces):
    """pieces: dicts musym, muname, acres, spacing (ft or None). Grouped by symbol:
    [musym, muname, acres, area-weighted spacing], largest first."""
    agg = {}
    for p in pieces:
        key = p.get("musym") or p.get("muname") or "?"
        a = agg.setdefault(key, {"name": p.get("muname") or "", "ac": 0.0, "sw": 0.0,
                                 "sa": 0.0})
        ac = p.get("acres") or 0.0
        a["ac"] += ac
        if p.get("spacing"):
            a["sw"] += p["spacing"] * ac
            a["sa"] += ac
    rows = [[k, v["name"], v["ac"], (v["sw"] / v["sa"]) if v["sa"] > 0 else None]
            for k, v in agg.items() if v["ac"] >= 0.05]
    rows.sort(key=lambda r: -r[2])
    return rows


def series_name(muname):
    """'Drummer silty clay loam, 0 to 2 percent slopes' -> 'Drummer'"""
    if not muname:
        return ""
    first = re.split(r"[ ,\-]", muname.strip())[0]
    return first


# ---------------------------------------------------------------- management
CROP_RELEASE = {"corn": "March 1", "soybeans": "April 1"}


def release_date(crop):
    c = (crop or "").lower()
    if "corn" in c and "soy" in c:
        return "March 1 (corn) / April 1 (soybeans)"
    if "soy" in c:
        return CROP_RELEASE["soybeans"]
    if "corn" in c:
        return CROP_RELEASE["corn"]
    return "March 1 (corn) / April 1 (soybeans)"


def parse_schedule(text):
    """'Jun 15, 1, 703.1; Aug 15, 1, 704.0' -> [[date, system, setting], ...]."""
    rows = []
    for item in re.split(r"[;\n]+", text or ""):
        item = item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split(",")]
        if len(parts) < 3:
            raise ValueError("Growing season schedule: '{}' needs date, system, "
                             "setting".format(item))
        date = ", ".join(parts[:-2])
        system, setting = parts[-2], parts[-1]
        try:
            setting = "{:.2f} ft".format(float(setting))
        except ValueError:
            pass
        rows.append([date, system, setting])
    return rows


ROTATION_WORDS = {"C": "corn", "S": "soybeans", "W": "wheat", "G": "small grain",
                  "H": "hay", "A": "alfalfa", "P": "pasture", "F": "fallow",
                  "O": "other"}


def crop_text(rows):
    """rows: dicts field, rotation, sequence, years. -> one line per field."""
    out = []
    for r in rows:
        name = str(r.get("field") or "").strip()
        rot = (r.get("rotation") or "").strip()
        seq = (r.get("sequence") or "").strip()
        yrs = (r.get("years") or "")
        bits = []
        if rot:
            bits.append(rot)
        if seq:
            bits.append("({}{})".format("{}: ".format(yrs) if yrs else "", seq))
        if bits:
            out.append(("{}: ".format(name) if name else "") + " ".join(bits))
    return "\n".join(out)
