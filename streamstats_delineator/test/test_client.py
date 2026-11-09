# -*- coding: utf-8 -*-
"""Offline tests for client.py. Run with:  python -m unittest discover -s test

Fixtures mirror the live response shapes documented for ss-delineate 1.2 /
pourpoint (verified by others against the live service in Sept 2026).
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import client as c  # noqa: E402

# A square watershed ~1.1 km on a side; outlet sits just inside the SE corner
SQUARE = [[[-90.74, 46.42], [-90.73, 46.42], [-90.73, 46.43],
           [-90.74, 46.43], [-90.74, 46.42]]]
OUTLET = (46.4201, -90.7302)  # (lat, lon)


def sshydro_response(polygon=SQUARE, warning="", gtype="Polygon"):
    return {
        "stateAbbreviation": "wi",
        "bcrequest": {
            "bcLabels": "*",
            "wsresp": {
                "workspace_id": "",
                "featurecollection": [[
                    {"name": "globalwatershedpoint",
                     "feature": {"type": "FeatureCollection", "features": [
                         {"type": "Feature",
                          "geometry": {"type": "Point",
                                       "coordinates": [OUTLET[1], OUTLET[0]]},
                          "properties": {"HUCID": "0401", "ExclusionType": 0,
                                         "WarningMsg": warning}}]}},
                    {"name": "globalwatershed",
                     "feature": {"type": "FeatureCollection", "features": [
                         {"type": "Feature",
                          "geometry": {"type": gtype, "coordinates": polygon},
                          "properties": {"GlobalWshd": 1, "Shape_Area": 1.2e6,
                                         "WarningMsg": warning, "Edited": 0}}]}},
                ]],
            },
        },
    }


class FakeFetch:
    """Route URLs to canned (status, body) answers and record calls."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, params, timeout):
        self.calls.append((url, dict(params)))
        for key, answer in self.routes.items():
            if key in url:
                if isinstance(answer, list):
                    answer = answer.pop(0)
                elif callable(answer) and not isinstance(answer, Exception):
                    answer = answer(params)
                if isinstance(answer, Exception):
                    raise answer
                status, body = answer
                return status, body if isinstance(body, str) else json.dumps(body)
        raise AssertionError("unexpected URL " + url)


SNAP_OK = (200, {"couldSnap": True,
                 "output": {"type": "Point", "coordinates": [OUTLET[1], OUTLET[0]]}})
CENSUS_WI = (200, {"result": {"geographies": {"States": [{"STUSAB": "WI"}]}}})


def make(routes, **kw):
    f = FakeFetch(routes)
    kw.setdefault("retries", 1)
    cl = c.StreamStatsClient(fetch=f, **kw)
    return cl, f


class PipelineTests(unittest.TestCase):

    def setUp(self):
        c.time.sleep = lambda s: None  # no real backoff waits in tests

    def test_happy_path_auto_region(self):
        cl, f = make({"geocoder": CENSUS_WI, "pourpoint": SNAP_OK,
                      "ss-delineate": (200, sshydro_response())})
        r = cl.delineate_from_click(46.4210, -90.7310)
        self.assertEqual(r["region"], "WI")
        self.assertEqual(r["polygon"]["type"], "Polygon")
        self.assertEqual(r["huc"], "0401")
        self.assertAlmostEqual(r["snapped"][0], OUTLET[0])
        self.assertGreater(r["snap_distance_m"], 50)
        # delineation must be requested at the *snapped* point, in the right region
        url, params = f.calls[-1]
        self.assertTrue(url.endswith("/sshydro/WI"))
        self.assertEqual(params["lat"], "{:.6f}".format(OUTLET[0]))

    def test_manual_region_skips_census(self):
        cl, f = make({"pourpoint": SNAP_OK, "ss-delineate": (200, sshydro_response())})
        cl.delineate_from_click(46.4210, -90.7310, region="wi")
        self.assertFalse(any("geocoder" in u for u, _ in f.calls))

    def test_unsnappable_point_raises(self):
        cl, f = make({"pourpoint": (200, {"couldSnap": False, "output": None})})
        with self.assertRaises(c.NotSnappableError):
            cl.delineate_from_click(46.42, -90.73, region="WI")
        self.assertFalse(any("ss-delineate" in u for u, _ in f.calls))

    def test_warning_msg_with_http_200_raises(self):
        w = ", Point not snappable using ss-pourpoint API service; results may be inaccurate."
        cl, _ = make({"pourpoint": SNAP_OK,
                      "ss-delineate": (200, sshydro_response(warning=w))})
        with self.assertRaises(c.DegenerateDelineationError) as cm:
            cl.delineate_from_click(46.42, -90.73, region="WI")
        self.assertIn("not snappable", str(cm.exception))

    def test_unsupported_region_404(self):
        cl, _ = make({"pourpoint": (404, '{"detail":"No such region"}')})
        with self.assertRaises(c.RegionNotSupportedError):
            cl.delineate_from_click(44.0, -85.0, region="MI")

    def test_no_region_found(self):
        cl, _ = make({"geocoder": (200, {"result": {"geographies": {"States": []}}})})
        with self.assertRaises(c.RegionNotSupportedError):
            cl.delineate_from_click(10.0, -40.0)

    def test_5xx_retried_then_succeeds(self):
        cl, f = make({"pourpoint": SNAP_OK,
                      "ss-delineate": [(502, "bad gateway"), (200, sshydro_response())]})
        r = cl.delineate_from_click(46.42, -90.73, region="WI")
        self.assertEqual(r["region"], "WI")
        self.assertEqual(sum("ss-delineate" in u for u, _ in f.calls), 2)

    def test_5xx_exhausted_raises_transport(self):
        cl, _ = make({"pourpoint": SNAP_OK,
                      "ss-delineate": [(503, "x"), (503, "x")]})
        with self.assertRaises(c.TransportError):
            cl.delineate_from_click(46.42, -90.73, region="WI")

    def test_timeout_exception_is_transport(self):
        cl, _ = make({"pourpoint": [TimeoutError("timed out"), TimeoutError("timed out")]})
        with self.assertRaises(c.TransportError):
            cl.delineate_from_click(46.42, -90.73, region="WI")

    def test_cancel(self):
        cl, _ = make({"pourpoint": SNAP_OK}, is_canceled=lambda: True)
        with self.assertRaises(c.StreamStatsError):
            cl.delineate_from_click(46.42, -90.73, region="WI")


NLDI_POS = (200, {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "LineString",
                                     "coordinates": [[-90.735, 46.425], [-90.7302, 46.4201]]},
     "properties": {"identifier": "13293750", "comid": "13293750", "source": "comid"}}]})
NLDI_UT = (200, {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "LineString",
                                     "coordinates": [[-90.735, 46.425], [-90.7302, 46.4201]]},
     "properties": {"nhdplus_comid": "13293750"}},
    {"type": "Feature", "geometry": {"type": "MultiLineString",
                                     "coordinates": [[[-90.738, 46.428], [-90.735, 46.425]]]},
     "properties": {"nhdplus_comid": "13293752"}},
    {"type": "Feature", "geometry": None, "properties": {"nhdplus_comid": "x"}}]})


class StreamTests(unittest.TestCase):

    def setUp(self):
        c.time.sleep = lambda s: None

    def test_streams_fetched_from_outlet(self):
        cl, f = make({"pourpoint": SNAP_OK, "ss-delineate": (200, sshydro_response()),
                      "comid/position": NLDI_POS, "navigation/UT": NLDI_UT})
        r = cl.delineate_from_click(46.42, -90.73, region="WI", streams=True)
        self.assertIsNone(r["streams_error"])
        self.assertEqual(r["stream_outlet_comid"], "13293750")
        self.assertEqual(len(r["streams"]), 2)  # geometry-less feature dropped
        pos_url, pos_params = [x for x in f.calls if "position" in x[0]][0]
        self.assertEqual(pos_params["coords"],
                         "POINT({:.6f} {:.6f})".format(OUTLET[1], OUTLET[0]))
        ut_url, ut_params = [x for x in f.calls if "navigation" in x[0]][0]
        self.assertIn("/comid/13293750/navigation/UT/flowlines", ut_url)
        self.assertEqual(ut_params["distance"], "10")  # tiny basin -> floor

    def test_streams_off_by_default(self):
        cl, f = make({"pourpoint": SNAP_OK, "ss-delineate": (200, sshydro_response())})
        r = cl.delineate_from_click(46.42, -90.73, region="WI")
        self.assertEqual(r["streams"], [])
        self.assertFalse(any("nldi" in u for u, _ in f.calls))

    def test_stream_failure_keeps_watershed(self):
        cl, _ = make({"pourpoint": SNAP_OK, "ss-delineate": (200, sshydro_response()),
                      "comid/position": [(503, "down"), (503, "down")]})
        r = cl.delineate_from_click(46.42, -90.73, region="WI", streams=True)
        self.assertEqual(r["polygon"]["type"], "Polygon")
        self.assertEqual(r["streams"], [])
        self.assertIn("503", r["streams_error"])

    def test_no_catchment(self):
        cl, _ = make({"pourpoint": SNAP_OK, "ss-delineate": (200, sshydro_response()),
                      "comid/position": (200, {"type": "FeatureCollection", "features": []})})
        r = cl.delineate_from_click(46.42, -90.73, region="WI", streams=True)
        self.assertIn("no NHDPlus catchment", r["streams_error"])

    def test_area_and_distance(self):
        # 0.01° x 0.01° at 46.425°N ≈ 0.7676 km × 1.1054 km ≈ 0.849 km²
        a = c.geojson_area_km2({"type": "Polygon", "coordinates": SQUARE})
        self.assertAlmostEqual(a, 0.849, delta=0.01)
        self.assertEqual(c.upstream_distance_km(0.5), 10.0)
        # Methow-sized basin (4644 km² ≈ 1793 mi²): main stem ~ 200 km -> ~600 km
        self.assertTrue(400 < c.upstream_distance_km(4644) < 900)
        self.assertEqual(c.upstream_distance_km(5e6), 3000.0)


# Toy network (hydroseq increases upstream, as in NHDPlus):
#
#   A1  A2    B1  B2
#    \  /      \  /
#     A (2)    B (2)    T (1)
#        \     |      /
#         `---- O (3) -'      <- watershed outlet
NET = {
    "100": {"hydroseq": 1, "dnhydroseq": 0, "order": 3, "name": "Big Creek"},
    "200": {"hydroseq": 2, "dnhydroseq": 1, "order": 2, "name": "Alder Creek"},
    "201": {"hydroseq": 3, "dnhydroseq": 2, "order": 1, "name": ""},
    "202": {"hydroseq": 4, "dnhydroseq": 2, "order": 1, "name": ""},
    "300": {"hydroseq": 5, "dnhydroseq": 1, "order": 2, "name": ""},
    "301": {"hydroseq": 6, "dnhydroseq": 5, "order": 1, "name": "Birch Run"},
    "302": {"hydroseq": 7, "dnhydroseq": 5, "order": 1, "name": ""},
    "400": {"hydroseq": 8, "dnhydroseq": 1, "order": 1, "name": ""},
}


def _by(groups):
    return {g["outlet_comid"]: g for g in groups}


class SubbasinGroupingTests(unittest.TestCase):

    def test_split_at_order_2(self):
        g = c.build_subbasins(NET, "100", 2)
        self.assertEqual(len(g), 3)
        b = _by(g)
        self.assertEqual(sorted(b["100"]["comids"]), ["100", "400"])  # T stays with O
        self.assertEqual(sorted(b["200"]["comids"]), ["200", "201", "202"])
        self.assertEqual(sorted(b["300"]["comids"]), ["300", "301", "302"])
        self.assertEqual(b["100"]["sb_id"], 1)
        self.assertIsNone(b["100"]["ds_sb_id"])
        self.assertEqual(b["200"]["ds_sb_id"], 1)
        self.assertEqual(b["300"]["ds_sb_id"], 1)
        self.assertEqual(b["200"]["name"], "Alder Creek")
        self.assertEqual(b["300"]["name"], "")  # name only taken from max-order reaches
        self.assertEqual(b["300"]["max_order"], 2)

    def test_every_reach_assigned_once(self):
        for n in (1, 2, 3, 4):
            g = c.build_subbasins(NET, "100", n)
            all_c = [x for s in g for x in s["comids"]]
            self.assertEqual(sorted(all_c), sorted(NET))

    def test_order_1_and_3(self):
        self.assertEqual(len(c.build_subbasins(NET, "100", 1)), 8)
        self.assertEqual(len(c.build_subbasins(NET, "100", 3)), 1)
        g1 = _by(c.build_subbasins(NET, "100", 1))
        ids = {k: v["sb_id"] for k, v in g1.items()}
        self.assertEqual(g1["201"]["ds_sb_id"], ids["200"])  # routing chain

    def test_choose_split_order(self):
        self.assertEqual(c.choose_split_order(NET, "100", 8), 1)
        self.assertEqual(c.choose_split_order(NET, "100", 3), 2)
        self.assertEqual(c.choose_split_order(NET, "100", 2), 3)
        self.assertEqual(c.choose_split_order(NET, "100", 1), 3)

    def test_disconnected_piece_becomes_own_subbasin(self):
        net = dict(NET)
        net["999"] = {"hydroseq": 50, "dnhydroseq": 49, "order": 1, "name": ""}
        g = _by(c.build_subbasins(net, "100", 3))
        self.assertEqual(len(g), 2)
        self.assertIsNone(g["999"]["ds_sb_id"])
        self.assertEqual(g["999"]["sb_id"], 2)  # numbered after the connected tree

    def test_loop_detected(self):
        net = {"1": {"hydroseq": 1, "dnhydroseq": 0, "order": 1, "name": ""},
               "2": {"hydroseq": 2, "dnhydroseq": 3, "order": 1, "name": ""},
               "3": {"hydroseq": 3, "dnhydroseq": 2, "order": 1, "name": ""}}
        with self.assertRaises(c.UnexpectedResponseError):
            c.build_subbasins(net, "1", 1)

    def test_ensure_lonlat(self):
        good = {"type": "Polygon", "coordinates": SQUARE}
        self.assertIs(c._ensure_lonlat(good), good)
        flipped = {"type": "Polygon",
                   "coordinates": [[[p[1], p[0]] for p in SQUARE[0]]]}
        self.assertEqual(c._ensure_lonlat(flipped)["coordinates"], SQUARE)


def _square(x, y, d=0.002):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + d, y], [x + d, y + d],
                                                [x, y + d], [x, y]]]}


def fake_wfs(params):
    """Answer flowline / catchment WFS requests from NET."""
    import re
    ids = re.search(r"IN \(([^)]*)\)", params["CQL_FILTER"]).group(1).split(",")
    feats = []
    for i in ids:
        if i not in NET:
            continue
        a = NET[i]
        if params["typeName"] == c.FLOWLINE_TYPE:
            feats.append({"type": "Feature", "geometry": None, "properties": {
                "comid": int(i), "hydroseq": a["hydroseq"], "dnhydroseq": a["dnhydroseq"],
                "streamorde": a["order"], "gnis_name": a["name"] or None}})
        else:
            feats.append({"type": "Feature", "properties": {"featureid": int(i)},
                          "geometry": _square(-90.74 + a["hydroseq"] * 0.001, 46.42)})
    return 200, {"type": "FeatureCollection", "features": feats}


UT_ALL = (200, {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"nhdplus_comid": k},
     "geometry": {"type": "LineString", "coordinates": [[-90.735, 46.425], [-90.734, 46.424]]}}
    for k in NET]})
POS_100 = (200, {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": None, "properties": {"comid": "100"}}]})


class SubbasinPipelineTests(unittest.TestCase):

    def setUp(self):
        c.time.sleep = lambda s: None
        self._chunk = c.WFS_CHUNK
        c.WFS_CHUNK = 3

    def tearDown(self):
        c.WFS_CHUNK = self._chunk

    def routes(self, **extra):
        r = {"pourpoint": SNAP_OK, "ss-delineate": (200, sshydro_response()),
             "comid/position": POS_100, "navigation/UT": UT_ALL, "geoserver": fake_wfs}
        r.update(extra)
        return r

    def test_auto_subbasins(self):
        cl, f = make(self.routes())
        r = cl.delineate_from_click(46.42, -90.73, region="WI", subbasins=True,
                                    max_subbasins=3)
        self.assertIsNone(r["subbasins_error"])
        sb = r["subbasins"]
        self.assertEqual(sb["split_order"], 2)
        self.assertEqual(len(sb["subbasins"]), 3)
        self.assertEqual(len(sb["catchments"]), 8)
        self.assertEqual(r["streams"], [])  # streams not requested
        wfs = [p for u, p in f.calls if "geoserver" in u]
        # 8 reaches in chunks of 3 -> 3 flowline + 3 catchment requests
        self.assertEqual(len(wfs), 6)
        self.assertTrue(all(p["version"] == "1.0.0" for p in wfs))
        self.assertIn("comid IN (", wfs[0]["CQL_FILTER"])
        self.assertTrue(any(p["CQL_FILTER"].startswith("featureid IN (") for p in wfs))

    def test_fixed_split_order(self):
        cl, _ = make(self.routes())
        r = cl.delineate_from_click(46.42, -90.73, region="WI", subbasins=True,
                                    split_order=1, streams=True)
        self.assertEqual(r["subbasins"]["split_order"], 1)
        self.assertEqual(len(r["subbasins"]["subbasins"]), 8)
        self.assertEqual(len(r["streams"]), 8)

    def test_wfs_failure_keeps_watershed_and_streams(self):
        cl, _ = make(self.routes(geoserver=(200, "<ServiceExceptionReport/>")))
        r = cl.delineate_from_click(46.42, -90.73, region="WI", subbasins=True,
                                    streams=True)
        self.assertEqual(r["polygon"]["type"], "Polygon")
        self.assertEqual(len(r["streams"]), 8)
        self.assertIsNone(r["subbasins"])
        self.assertIn("Non-JSON", r["subbasins_error"])

    def test_nldi_failure_reported_for_both(self):
        cl, _ = make(self.routes(**{"comid/position": [(503, "x"), (503, "x")]}))
        r = cl.delineate_from_click(46.42, -90.73, region="WI", subbasins=True)
        self.assertIn("503", r["subbasins_error"])
        self.assertIsNone(r["streams_error"])  # streams were not requested

    def test_too_large(self):
        old = c.MAX_SUBBASIN_COMIDS
        c.MAX_SUBBASIN_COMIDS = 5
        try:
            cl, _ = make(self.routes())
            r = cl.delineate_from_click(46.42, -90.73, region="WI", subbasins=True)
        finally:
            c.MAX_SUBBASIN_COMIDS = old
        self.assertIn("too large", r["subbasins_error"])


class ValidationTests(unittest.TestCase):

    def test_outlet_far_outside_raises(self):
        far = [[[-91.0, 47.0], [-90.99, 47.0], [-90.99, 47.01],
                [-91.0, 47.01], [-91.0, 47.0]]]
        ws = c.parse_delineation(sshydro_response(polygon=far))
        with self.assertRaises(c.DegenerateDelineationError):
            c.validate_watershed(ws, OUTLET)

    def test_outlet_just_outside_ok(self):
        # outlet ~10 m east of the square's east edge
        ws = c.parse_delineation(sshydro_response())
        c.validate_watershed(ws, (46.425, -90.73 + 0.00013))

    def test_unclosed_ring_raises(self):
        bad = [[[-90.74, 46.42], [-90.73, 46.42], [-90.73, 46.43], [-90.74, 46.43]]]
        ws = c.parse_delineation(sshydro_response(polygon=bad))
        with self.assertRaises(c.DegenerateDelineationError):
            c.validate_watershed(ws, OUTLET)

    def test_multipolygon_and_hole(self):
        hole = [[-90.738, 46.422], [-90.732, 46.422], [-90.732, 46.428],
                [-90.738, 46.428], [-90.738, 46.422]]
        mp = [[SQUARE[0], hole]]
        ws = c.parse_delineation(sshydro_response(polygon=mp, gtype="MultiPolygon"))
        c.validate_watershed(ws, OUTLET)  # outlet is in the shell, not the hole
        self.assertFalse(c._point_in_multipolygon(-90.735, 46.425, mp))

    def test_missing_featurecollection(self):
        with self.assertRaises(c.UnexpectedResponseError):
            c.parse_delineation({"bcrequest": {"wsresp": {}}})

    def test_legacy_layout_tolerated(self):
        new = sshydro_response()
        legacy = {"featurecollection": new["bcrequest"]["wsresp"]["featurecollection"][0]}
        ws = c.parse_delineation(legacy)
        self.assertEqual(ws["polygon"]["type"], "Polygon")

    def test_haversine(self):
        # 1 degree of latitude ~ 111.2 km
        self.assertAlmostEqual(c.haversine_m(46, -90, 47, -90) / 1000, 111.2, delta=0.3)


if __name__ == "__main__":
    unittest.main()
