"""Per-level results for the ditch-level explorer: the QGIS panel and the report.

Pure Python and numpy. Step 0 is the free outlet; steps 1.. are the ditch levels of
the rating curve, in the same order as its rows. In the QGIS outputs, step i is band
i + 1 of the drainage-rate raster and the fields q_<i> / rise_<i> of the pipes layer.
"""
import base64
import json
import struct
import zlib

import numpy as np

from .model import main_profile

PROP_KEY = "tile_outlet_submergence/explorer"
NODATA = 255                                         # drainage-rate raster, Byte
RISE_BREAKS = (0.0001, 0.25, 1.0)                    # HGL rise classes, design units
RISE_COLORS = ("#16a34a", "#a3e635", "#f59e0b", "#b91c1c")
RISE_LABELS = ("no rise", "up to 0.25 {u}", "0.25 to 1 {u}", "more than 1 {u}")
RATE_STOPS = ((0, "#b91c1c"), (50, "#f59e0b"), (90, "#fde68a"), (100, "#16a34a"))
MAX_PROFILE = 400


def q_field(i):
    return "q_{:02d}".format(i)


def rise_field(i):
    return "rise_{:02d}".format(i)


def rise_class(v):
    for k, b in enumerate(RISE_BREAKS):
        if v < b:
            return k
    return len(RISE_BREAKS)


class LevelStore:
    """Collects, for every step, the flow and HGL rise of each pipe, the drainage rate
    of each drained cell (% of the free outlet) and the HGL along the main."""

    def __init__(self, model, uf, qk, max_profile=MAX_PROFILE):
        self.model, self.uf, self.qk = model, uf, qk
        S = model.S
        self.spans = [(a, b + 1) for a, b in zip(S.edge_first, S.edge_last)]
        self.ef = np.asarray(S.edge_first, dtype=np.int64)
        idx, dist = main_profile(model)
        if len(idx) > max_profile:
            pick = np.unique(np.round(np.linspace(0, len(idx) - 1, max_profile)).astype(int))
            idx, dist = idx[pick], dist[pick]
        self.pidx, self.pdist = idx, dist
        self.edge_q, self.edge_rise, self.cell_pct, self.hgl = [], [], [], []

    def add(self, res):
        m = self.model
        rise = res["H"] - m.free["H"]
        self.edge_q.append(res["Q"][self.ef] * self.qk)
        self.edge_rise.append(np.array([max(float(rise[a:b].max()), 0.0) if b > a else 0.0
                                        for a, b in self.spans]) / self.uf)
        pct = np.clip(np.round(100.0 * m.cell_relative(res)), 0, 100).astype(np.uint8)
        self.cell_pct.append(pct)
        self.hgl.append(res["H"][self.pidx] / self.uf)

    def __len__(self):
        return len(self.edge_q)

    def band(self, i, nrows, ncols):
        """Drainage rate of step i as a Byte grid (NODATA outside the drained area)."""
        arr = np.full(nrows * ncols, NODATA, dtype=np.uint8)
        arr[self.model.cell_flat] = self.cell_pct[i]
        return arr.reshape(nrows, ncols)

    def profile(self, ground, wt):
        """The main's profile in design units; ground and wt (m) at the profile samples."""
        m, uf = self.model, self.uf
        S, idx = m.S, self.pidx
        return {"dist": _r(self.pdist / uf, 2), "invert": _r(S.z[idx] / uf, 3),
                "crown": _r((S.z[idx] + m.D[idx]) / uf, 3), "ground": _r(ground / uf, 3),
                "wt": _r(wt / uf, 3), "hgl": [_r(h, 3) for h in self.hgl]}


def _r(a, nd):
    return [None if not np.isfinite(v) else round(float(v), nd) for v in np.asarray(a)]


def steps_from_rating(rating):
    """Per-step numbers for the explorer, from the rating rows (free outlet first)."""
    return {"level": [r["level"] for r in rating], "elev": [r["elev"] for r in rating],
            "outflow": [r["outflow"] for r in rating], "pct": [r["pct_free"] for r in rating],
            "backflow": [r["backflow"] for r in rating]}


def panel_meta(run_id, name, rating, store, ground, wt, lu, qu, crown, map_index):
    """What the QGIS panel needs about a run, kept as JSON on the output layers."""
    return json.dumps({"version": 1, "run": run_id, "name": name,
                       "units": {"l": lu, "q": qu}, "crown": crown, "map_index": map_index,
                       "steps": steps_from_rating(rating),
                       "profile": store.profile(ground, wt)}, separators=(",", ":"))


# ------------------------------------------------------------------ text
def level_text(meta, i):
    """(headline, elevation line, outlet-crown line) for step i."""
    st, lu = meta["steps"], meta["units"]["l"]
    lv = st["level"][i]
    if lv is None:
        return ("Free outlet", "Ditch below the outlet invert", "Outlet pipe flowing freely")
    crown = meta["crown"]
    if lv > crown + 1e-9:
        over = lv - crown
        c = "{:.2f} {} over the outlet crown ({:.1f} D)".format(over, lu, over / crown)
    elif lv <= 1e-9:
        c = "Ditch at the outlet invert"
    else:
        c = "Outlet pipe {:.0f}% submerged (crown at {:.2f} {})".format(
            100.0 * lv / crown, crown, lu)
    return ("{:.2f} {} above the outlet invert".format(lv, lu),
            "Ditch elevation {:.2f} {}".format(st["elev"][i], lu), c)


def flow_text(meta, i):
    """(outflow line, change line) for step i."""
    st, qu = meta["steps"], meta["units"]["q"]
    a = "{:.3f} {}, {:.1f}% of the free outlet".format(st["outflow"][i], qu, st["pct"][i])
    if i == 0:
        return a, "Change from the step below: -"
    dq = round(st["outflow"][i] - st["outflow"][i - 1], 3) + 0.0
    dp = round(st["pct"][i] - st["pct"][i - 1], 1) + 0.0
    return a, "Change from the step below: {:+.3f} {} ({:+.1f} points)".format(dq, qu, dp)


def flap_text(meta, i):
    """(warning?, text) for step i."""
    b = meta["steps"]["backflow"][i]
    if meta["steps"]["level"][i] is not None and b > 0.05:
        return True, ("The ditch stands above the design water table under {:.1f}% of the "
                      "drained area: without a flap gate, water could run back into the "
                      "field.".format(b))
    return False, "The ditch stays below the design water table across the drained area."


# ------------------------------------------------------------------ report payload
def png_gray(arr):
    """8-bit greyscale PNG of a 2-D uint8 array, as bytes."""
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    h, w = arr.shape
    raw = np.zeros((h, w + 1), dtype=np.uint8)
    raw[:, 1:] = arr

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw.tobytes(), 9)) + chunk(b"IEND", b""))


def report_payload(meta_json, store, grid, edges, kinds, d_in, max_w=700, max_h=520):
    """Data for the report's explorer: the panel meta plus a map of every step."""
    meta = json.loads(meta_json)
    # map frame: the drained cells and the pipes
    m = store.model
    rows, cols = np.divmod(m.cell_flat, grid.ncols)
    xs = [grid.x0 + (cols.min()) * grid.cell, grid.x0 + (cols.max() + 1) * grid.cell]
    ys = [grid.y0 - (rows.max() + 1) * grid.cell, grid.y0 - rows.min() * grid.cell]
    for e in edges:
        xs += [float(e.xyz[:, 0].min()), float(e.xyz[:, 0].max())]
        ys += [float(e.xyz[:, 1].min()), float(e.xyz[:, 1].max())]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    pad = 0.03 * max(x1 - x0, y1 - y0, grid.cell)
    x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
    s = min(max_w / (x1 - x0), max_h / (y1 - y0))
    W, H = int(round((x1 - x0) * s)), int(round((y1 - y0) * s))
    # image: at most one pixel per cell
    k = max(1.0, grid.cell * s)
    iw, ih = max(1, int(round(W / k))), max(1, int(round(H / k)))
    px = x0 + (np.arange(iw) + 0.5) * (x1 - x0) / iw
    py = y1 - (np.arange(ih) + 0.5) * (y1 - y0) / ih
    cj = np.floor((px - grid.x0) / grid.cell).astype(np.int64)
    ci = np.floor((grid.y0 - py) / grid.cell).astype(np.int64)
    pos = np.full(grid.nrows * grid.ncols, -1, dtype=np.int64)
    pos[m.cell_flat] = np.arange(len(m.cell_flat))
    okj = (cj >= 0) & (cj < grid.ncols)
    oki = (ci >= 0) & (ci < grid.nrows)
    look = np.full((ih, iw), -1, dtype=np.int64)
    I, J = np.meshgrid(np.nonzero(oki)[0], np.nonzero(okj)[0], indexing="ij")
    look[I, J] = pos[ci[I] * grid.ncols + cj[J]]
    has = look >= 0
    pngs = []
    for i in range(len(store)):
        img = np.full((ih, iw), NODATA, dtype=np.uint8)
        img[has] = store.cell_pct[i][look[has]]
        pngs.append(base64.b64encode(png_gray(img)).decode("ascii"))
    pipes = []
    for ei, e in enumerate(edges):
        pts = [(round((x - x0) * s, 1), round((y1 - y) * s, 1)) for x, y in e.xyz[:, :2]]
        pipes.append({"p": [c for xy in pts for c in xy], "k": kinds[ei],
                      "d": round(float(d_in[ei]), 1)})
    meta["map"] = {"w": W, "h": H, "iw": iw, "ih": ih, "png": pngs, "pipes": pipes,
                   "q": [[round(float(v), 4) for v in q] for q in store.edge_q],
                   "rise": [[round(float(v), 3) for v in r] for r in store.edge_rise]}
    return meta
