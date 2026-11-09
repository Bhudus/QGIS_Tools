# -*- coding: utf-8 -*-
"""Tests for the sub-basin assembly in layers.py without QGIS.

QgsGeometry is replaced by a toy geometry made of unit grid cells, so union,
intersection, difference, buffer and area are exact set operations. That is
enough to check the logic around them: clipping to the watershed, filling
gaps, dropping empty sub-basins, renumbering and routing links.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))


class Box:
    def __init__(self, cells):
        xs = [c[0] for c in cells] or [0]
        ys = [c[1] for c in cells] or [0]
        self.b = (min(xs), min(ys), max(xs), max(ys))

    def intersects(self, o):
        a, b = self.b, o.b
        return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


class G:
    """Toy geometry: a frozenset of (x, y) unit cells."""

    def __init__(self, cells=()):
        self.cells = frozenset(cells)

    # QgsGeometry API used by layers.py
    def constGet(self): return self
    def isEmpty(self): return not self.cells
    def area(self): return float(len(self.cells))
    def length(self): return 0.0
    def wkbType(self): return "MultiPolygon" if self.isMultipart() else "Polygon"
    def convertToMultiType(self): return True
    def boundingBox(self): return Box(self.cells)
    def intersection(self, o): return G(self.cells & o.cells)
    def difference(self, o): return G(self.cells - o.cells)

    def buffer(self, _d, _segs):
        grown = set(self.cells)
        for x, y in self.cells:
            grown |= {(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)}
        return G(grown)

    def distance(self, o):
        return min(max(abs(a[0] - b[0]), abs(a[1] - b[1]))
                   for a in self.cells for b in o.cells)

    def _components(self):
        left, comps = set(self.cells), []
        while left:
            stack, comp = [left.pop()], set()
            while stack:
                x, y = stack.pop()
                comp.add((x, y))
                for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if n in left:
                        left.remove(n)
                        stack.append(n)
            comps.append(G(comp))
        return comps

    def isMultipart(self): return len(self._components()) > 1
    def asGeometryCollection(self): return self._components()

    @staticmethod
    def unaryUnion(gs):
        out = set()
        for g in gs:
            out |= g.cells
        return G(out)

    collectGeometry = unaryUnion

    @staticmethod
    def createGeometryEngine(g):
        eng = MagicMock()
        eng.contains = lambda other: other.cells <= g.cells
        return eng


def rect(x0, x1, y0, y1):
    return G((x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))


def load_layers():
    for m in ["qgis", "qgis.core", "qgis.gui", "qgis.PyQt", "qgis.PyQt.QtCore",
              "qgis.PyQt.QtGui", "qgis.PyQt.QtWidgets", "qgis.PyQt.QtNetwork"]:
        sys.modules.setdefault(m, MagicMock())
    core = sys.modules["qgis.core"]
    core.QgsGeometry = G
    core.QgsWkbTypes.displayString = staticmethod(lambda t: t)
    sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
    pkg = os.path.basename(os.path.dirname(HERE))
    mod = __import__(pkg + ".layers", fromlist=["layers"])
    mod.QgsGeometry = G
    mod.QgsWkbTypes = core.QgsWkbTypes
    # catchment "GeoJSON" in these tests is {"cells": G}
    mod.geojson_to_geometry = lambda gj: gj["cells"]
    return mod


layers = load_layers()


class AreaDA:
    def measureArea(self, g): return g.area()
    def measureLength(self, g): return 0.0


# groups as build_subbasins returns them for split order 2 (see test_client.NET)
GROUPS = [
    {"sb_id": 1, "ds_sb_id": None, "outlet_comid": "100", "comids": ["100", "400"],
     "max_order": 3, "name": "Big Creek"},
    {"sb_id": 2, "ds_sb_id": 1, "outlet_comid": "200", "comids": ["200", "201", "202"],
     "max_order": 2, "name": "Alder Creek"},
    {"sb_id": 3, "ds_sb_id": 1, "outlet_comid": "300", "comids": ["300", "301", "302"],
     "max_order": 2, "name": ""},
]
NETW = {c: {"order": 1, "name": ""} for g in GROUPS for c in g["comids"]}
WS = rect(0, 5, 0, 3)  # 24 cells


def stream(comid):
    return {"comid": comid, "length_km": 1.0, "sb_id": None, "order": None, "name": ""}


class SubbasinGeometryTests(unittest.TestCase):

    def run_case(self, cats, streams=()):
        sb = {"split_order": 2, "subbasins": GROUPS, "network": NETW,
              "catchments": {k: {"cells": v} for k, v in cats.items()}}
        parts = [stream(c) for c in streams]
        out = layers._prepare_subbasins(sb, WS, WS.area(), AreaDA(), parts,
                                        lambda: False)
        return out, parts

    def test_clip_gapfill_drop_and_renumber(self):
        cats = {
            "100": G(rect(0, 1, 0, 3).cells | {(-1, 0)}),  # sticks out of the watershed
            "200": rect(2, 3, 0, 1),
            "201": rect(4, 4, 0, 1),
            # 400 and 202 missing -> gap; 30x lie entirely outside the watershed
            "300": rect(10, 11, 0, 1), "301": rect(12, 12, 0, 0), "302": rect(13, 13, 0, 0),
        }
        out, parts = self.run_case(cats, streams=["201", "301", "100"])
        self.assertEqual([s["sb_id"] for s in out], [1, 2])          # sb 3 dropped
        self.assertEqual([s["ds_sb_id"] for s in out], [None, 1])
        by = {s["outlet_comid"]: s for s in out}
        self.assertEqual(by["100"]["area_m2"], 8)                   # clipped, (-1,0) gone
        self.assertEqual(by["200"]["area_m2"], 16)                  # took the gap
        # the sub-basins tile the watershed exactly
        union = G.unaryUnion([s["geometry"] for s in out])
        self.assertEqual(union.cells, WS.cells)
        self.assertEqual(sum(s["area_m2"] for s in out), WS.area())
        self.assertAlmostEqual(sum(s["pct_area"] for s in out), 100.0)
        # streams tagged; a stream in the dropped sub-basin goes to its downstream one
        self.assertEqual({p["comid"]: p["sb_id"] for p in parts},
                         {"201": 2, "301": 1, "100": 1})
        self.assertEqual(by["100"]["stream_km"], 2.0)
        self.assertEqual(by["200"]["n_reaches"], 3)

    def test_no_catchment_overlaps_watershed(self):
        out, _ = self.run_case({"100": rect(20, 21, 0, 0)})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["sb_id"], 1)
        self.assertEqual(out[0]["geometry"].cells, WS.cells)

    def test_island_gap_goes_to_nearest(self):
        # sb1 covers the left half; sb2 one cell at the far right; nothing else
        cats = {"100": rect(0, 2, 0, 3), "200": rect(5, 5, 3, 3)}
        out, _ = self.run_case(cats)
        union = G.unaryUnion([s["geometry"] for s in out])
        self.assertEqual(union.cells, WS.cells)
        by = {s["outlet_comid"]: s for s in out}
        # the remaining block x3..5 touches both; shares more edge with sb1 (4 vs 2)
        self.assertEqual(by["100"]["area_m2"], 23)
        self.assertEqual(by["200"]["area_m2"], 1)

    def test_prepare_geometries_isolates_errors(self):
        res = {"polygon": None, "subbasins": {"bad": True}}
        layers.geojson_to_geometry = lambda gj: WS if gj is None else gj["cells"]
        layers.make_distance_area = lambda ctx: AreaDA()
        layers._clip_streams = lambda feats, ws, da: []
        out = layers.prepare_geometries(res, None)
        self.assertIsNone(out["subbasin_parts"])
        self.assertIn("Could not build sub-basin polygons", out["subbasins_error"])
        self.assertEqual(out["area_m2"], 24)


if __name__ == "__main__":
    unittest.main()
