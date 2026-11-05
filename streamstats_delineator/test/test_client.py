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
