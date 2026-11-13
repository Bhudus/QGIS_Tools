"""Lay out a tile-profile chart as simple drawing instructions (pure Python).

The Qt widget in chart.py paints these instructions with QPainter; tests can
paint the same instructions with any other library. Coordinates are pixels,
origin top left.

Instructions:
  ("poly", [(x, y), ...], color, width, dash)        dash: None or (on, off)
  ("fill", [(x, y), ...], color)                       color may carry alpha
  ("rect", x, y, w, h, fill, outline)
  ("dot", x, y, r, color)
  ("tri", x, y, size, color)                            triangle pointing down
  ("text", x, y, string, color, size, align, bold)     align: l, c, r (+ v: t, m, b)
"""
import math

from .profile_core import depth_lines, depth_problems, interp

COLORS = {
    "ground": (120, 82, 45),
    "band": (34, 139, 34),
    "band_fill": (34, 139, 34, 34),
    "abs": (217, 119, 6),
    "tile": (29, 78, 216),
    "tile_bad": (220, 38, 38),
    "grid": (226, 226, 226),
    "axis": (90, 90, 90),
    "text": (40, 40, 40),
    "muted": (110, 110, 110),
    "junction": (100, 100, 100),
    "bg": (255, 255, 255),
    "highlight": (255, 196, 0, 110),
}
FONT = 11
SOURCES = {"vertices": "tile at its grade breaks",
           "line ends": "tile straight between line ends (layer has no Z values)",
           "g_vertices": "ground at the line vertices",
           "g_line ends": "ground straight between line ends",
           "g_DEM": "ground from the DEM"}


def text_width(s, size=FONT):
    return 0.53 * size * len(s)


def nice_ticks(lo, hi, target=6):
    """Round tick values spanning [lo, hi]."""
    if hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / max(target, 1)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw:
            break
    start = math.ceil(lo / step - 1e-9) * step
    ticks = []
    v = start
    while v <= hi + 1e-9 * step:
        ticks.append(round(v, 10))
        v += step
    return ticks, step


def fmt(v, step):
    if step >= 1:
        return "{:,.0f}".format(v)
    d = max(0, -int(math.floor(math.log10(step))))
    return "{:,.{}f}".format(v, d)


class ChartModel:
    def __init__(self, profile, depths, units="ft"):
        """depths: dict with keys upper, lower, abs_upper, abs_lower (positive, units)."""
        self.p = profile
        self.d = depths
        self.u = units
        self.lines = depth_lines(profile, {k: depths[k] for k in
                                           ("upper", "lower", "abs_upper", "abs_lower")})
        self.bad = set(depth_problems(profile, depths["abs_upper"], depths["abs_lower"]))
        ys = [v for _, v in profile["tile"]] + [v for _, v in profile["ground"]]
        ys += [v for pts in self.lines.values() for _, v in pts]
        lo, hi = min(ys), max(ys)
        pad = max((hi - lo) * 0.06, 0.1)
        self.ylo, self.yhi = lo - pad, hi + pad
        self.xlo, self.xhi = 0.0, max(profile["length"], 1e-6)

    # ---------------------------------------------------------------- geometry
    def layout(self, w, h):
        self.w, self.h = w, h
        self.ml, self.mr = 64, 18
        self.mt, self.mb = 62, 50
        self.pw = max(w - self.ml - self.mr, 10)
        self.ph = max(h - self.mt - self.mb, 10)

    def X(self, s):
        return self.ml + (s - self.xlo) / (self.xhi - self.xlo) * self.pw

    def Y(self, v):
        return self.mt + (self.yhi - v) / (self.yhi - self.ylo) * self.ph

    def station_at(self, px):
        s = self.xlo + (px - self.ml) / self.pw * (self.xhi - self.xlo)
        return min(max(s, self.xlo), self.xhi)

    def inside(self, px, py):
        return self.ml <= px <= self.ml + self.pw and self.mt <= py <= self.mt + self.ph

    # ---------------------------------------------------------------- drawing
    def scene(self, w, h):
        self.layout(w, h)
        p, u, out = self.p, self.u, []
        C = COLORS
        out.append(("rect", 0, 0, w, h, C["bg"], None))

        # title
        sizes = ", ".join("{} in".format(s) for s in p["nominal_sizes"])
        title = "{}{}".format(p["name"], " ({})".format(p["kind"]) if p["kind"]
                              and p["kind"] not in p["name"] else "")
        sub = "{:,.1f} {} long".format(p["length"], u)
        if sizes:
            sub += "  ·  pipe {}".format(sizes)
        sub += "  ·  {}; {}".format(SOURCES.get(p["tile_source"], ""),
                                      SOURCES.get("g_" + str(p["ground_source"]), ""))
        out.append(("text", self.ml, 8, title, C["text"], 13, "lt", True))
        out.append(("text", self.ml, 27, sub, C["muted"], FONT, "lt", False))

        # grid and axes
        yt, ys = nice_ticks(self.ylo, self.yhi, max(3, int(self.ph / 45)))
        xt, xs = nice_ticks(self.xlo, self.xhi, max(3, int(self.pw / 90)))
        for v in yt:
            y = self.Y(v)
            out.append(("poly", [(self.ml, y), (self.ml + self.pw, y)], C["grid"], 1, None))
            out.append(("text", self.ml - 6, y, fmt(v, ys), C["muted"], FONT - 1, "rm", False))
        for v in xt:
            x = self.X(v)
            out.append(("poly", [(x, self.mt), (x, self.mt + self.ph)], C["grid"], 1, None))
            out.append(("text", x, self.mt + self.ph + 5, fmt(v, xs), C["muted"], FONT - 1,
                        "ct", False))
        out.append(("poly", [(self.ml, self.mt), (self.ml, self.mt + self.ph),
                             (self.ml + self.pw, self.mt + self.ph)], C["axis"], 1, None))
        out.append(("text", self.ml + self.pw / 2, self.mt + self.ph + 22,
                    "Station from the downstream end ({})".format(u), C["text"], FONT, "ct",
                    False))
        out.append(("text", self.ml, self.mt + self.ph + 22, "← downstream", C["muted"],
                    FONT - 1, "lt", False))
        out.append(("text", self.ml + self.pw, self.mt + self.ph + 22, "upstream →",
                    C["muted"], FONT - 1, "rt", False))
        out.append(("text", 6, self.mt - 14, "Elevation ({})".format(u), C["text"], FONT,
                    "lt", False))

        # target band
        up, lo = self.lines["upper"], self.lines["lower"]
        out.append(("fill", [(self.X(s), self.Y(v)) for s, v in up]
                    + [(self.X(s), self.Y(v)) for s, v in reversed(lo)], C["band_fill"]))
        for key, color, dash in (("upper", C["band"], (6, 4)), ("lower", C["band"], (6, 4)),
                                 ("abs_upper", C["abs"], (2, 3)), ("abs_lower", C["abs"], (2, 3))):
            out.append(("poly", [(self.X(s), self.Y(v)) for s, v in self.lines[key]],
                        color, 1.4, dash))

        # junctions
        for s in p["junctions"]:
            x = self.X(s)
            out.append(("poly", [(x, self.mt), (x, self.mt + self.ph)], C["junction"], 1,
                        (1, 3)))
        for s, _ in p["tributaries"]:
            out.append(("tri", self.X(s), self.Y(interp(p["tile"], s)) - 9, 7, C["junction"]))

        # ground and tile
        out.append(("poly", [(self.X(s), self.Y(v)) for s, v in p["ground"]], C["ground"], 2.2,
                    None))
        out.append(("poly", [(self.X(s), self.Y(v)) for s, v in p["tile"]], C["tile"], 2.6, None))
        if self.bad:
            for (s1, v1), (s2, v2) in zip(p["tile"][:-1], p["tile"][1:]):
                if s1 in self.bad or s2 in self.bad:
                    out.append(("poly", [(self.X(s1), self.Y(v1)), (self.X(s2), self.Y(v2))],
                                C["tile_bad"], 3.0, None))
        for s, v in p["tile"]:
            out.append(("dot", self.X(s), self.Y(v), 2.6, C["tile"]))

        out += self._legend()
        return out

    def _legend(self):
        C, u, d = COLORS, self.u, self.d
        items = [("Ground", C["ground"], None, 2.2),
                 ("Tile", C["tile"], None, 2.6),
                 ("Target band {:g}–{:g} {}".format(d["upper"], d["lower"], u), C["band"],
                  (6, 4), 1.4),
                 ("Absolute limits {:g} and {:g} {}".format(d["abs_upper"], d["abs_lower"], u),
                  C["abs"], (2, 3), 1.4)]
        if self.bad:
            items.append(("Outside absolute limits", C["tile_bad"], None, 3.0))
        if self.p["tributaries"]:
            items.append(("Junction", C["junction"], "tri", 0))
        out, x, y = [], self.ml, 46
        for label, color, dash, width in items:
            if dash == "tri":
                out.append(("tri", x + 9, y + 1, 7, color))
            else:
                out.append(("poly", [(x, y), (x + 18, y)], color, width, dash))
            out.append(("text", x + 23, y, label, COLORS["text"], FONT - 1, "lm", False))
            x += 23 + text_width(label, FONT - 1) + 16
        return out

    def section_at(self, s):
        """The tile section (between two tile points) containing station s:
        (start station, end station, slope as a fraction, falling toward station 0)."""
        pts = self.p["tile"]
        for (s1, t1), (s2, t2) in zip(pts[:-1], pts[1:]):
            if s1 <= s <= s2 and s2 > s1:
                return s1, s2, (t2 - t1) / (s2 - s1)
        return None

    def hover(self, px):
        """Crosshair, section highlight and readout for the mouse at pixel x."""
        s = self.station_at(px)
        g = interp(self.p["ground"], s)
        t = interp(self.p["tile"], s)
        x = self.X(s)
        u = self.u
        lines = ["Station {:,.1f} {}".format(s, u), "Ground {:,.2f}".format(g),
                 "Tile {:,.2f}".format(t), "Depth {:,.2f} {}".format(g - t, u)]
        out = [("poly", [(x, self.mt), (x, self.mt + self.ph)], COLORS["axis"], 1, (3, 3))]
        sec = self.section_at(s)
        if sec is not None:
            s1, s2, slope = sec
            label = "Tile slope {:.2f}%".format(slope * 100)
            if slope <= 0:
                label += " (reverse)" if slope < 0 else " (flat)"
            lines.append(label)
            lines.append("  section {:,.0f}\u2013{:,.0f} {}".format(s1, s2, u))
            t1, t2 = interp(self.p["tile"], s1), interp(self.p["tile"], s2)
            out.append(("poly", [(self.X(s1), self.Y(t1)), (self.X(s2), self.Y(t2))],
                        COLORS["highlight"], 9, None))
            for ss, tt in ((s1, t1), (s2, t2)):
                out.append(("poly", [(self.X(ss), self.Y(tt) - 8), (self.X(ss), self.Y(tt) + 8)],
                            COLORS["tile"], 1.5, None))
        bw = max(text_width(l, FONT) for l in lines) + 14
        bh = 16 * len(lines) + 8
        bx = x + 10 if x + 10 + bw < self.ml + self.pw else x - 10 - bw
        by = self.mt + 6
        out += [("dot", x, self.Y(t), 4, COLORS["tile"]), ("dot", x, self.Y(g), 4, COLORS["ground"]),
                ("rect", bx, by, bw, bh, (255, 255, 255, 235), (160, 160, 160))]
        for i, l in enumerate(lines):
            color = COLORS["muted"] if l.startswith("  section") else COLORS["text"]
            out.append(("text", bx + 7, by + 5 + 16 * i, l.strip(), color, FONT, "lt",
                        i in (3, 4)))
        return out
