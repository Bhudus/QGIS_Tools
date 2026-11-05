# -*- coding: utf-8 -*-
"""Output layers: watershed polygons, outlet points and stream lines.

They are scratch (memory) layers; right-click > Make Permanent to save them.
Layers are found again by a custom property, so renaming them is fine.

Geometry work (GeoJSON -> QgsGeometry, clipping streams, measuring) is done by
``prepare_geometries`` in the background task; ``add_result`` only writes
features, so the GUI stays responsive on large basins.
"""

from datetime import datetime, timezone

from qgis.core import (QgsCoordinateReferenceSystem, QgsDistanceArea, QgsFeature,
                       QgsFillSymbol, QgsGeometry, QgsLineSymbol, QgsMarkerSymbol,
                       QgsPointXY, QgsProject, QgsVectorLayer, QgsWkbTypes)

from .compat import make_field

ROLE_KEY = "streamstats_delineator/role"
WGS84 = "EPSG:4326"
M2_PER_MI2 = 2589988.110336

COMMON_FIELDS = [
    ("ws_id", "int"), ("region", "string"), ("huc", "string"),
    ("click_lat", "double"), ("click_lon", "double"),
    ("snap_lat", "double"), ("snap_lon", "double"), ("snap_dist_m", "double"),
    ("created_utc", "string"),
]
WATERSHED_FIELDS = COMMON_FIELDS + [("area_km2", "double"), ("area_mi2", "double"),
                                    ("stream_km", "double"), ("n_streams", "int")]
STREAM_FIELDS = [("ws_id", "int"), ("comid", "string"), ("length_km", "double"),
                 ("clipped", "int")]


# ------------------------------------------------------------------ layers
def _find_layer(role):
    for lyr in QgsProject.instance().mapLayers().values():
        if lyr.customProperty(ROLE_KEY) == role and lyr.isValid():
            return lyr
    return None


def _create_layer(role, uri, name, fields, symbol):
    lyr = QgsVectorLayer(uri, name, "memory")
    lyr.dataProvider().addAttributes([make_field(n, k) for n, k in fields])
    lyr.updateFields()
    lyr.setCustomProperty(ROLE_KEY, role)
    lyr.renderer().setSymbol(symbol)
    QgsProject.instance().addMapLayer(lyr)
    return lyr


def _ensure_fields(lyr, fields):
    """Add any fields missing from a layer created by an older plugin version."""
    have = set(lyr.fields().names())
    missing = [make_field(n, k) for n, k in fields if n not in have]
    if missing:
        lyr.dataProvider().addAttributes(missing)
        lyr.updateFields()
    return lyr


def watershed_layer():
    lyr = _find_layer("watersheds")
    if lyr:
        return _ensure_fields(lyr, WATERSHED_FIELDS)
    return _create_layer(
        "watersheds", "MultiPolygon?crs=" + WGS84, "StreamStats watersheds",
        WATERSHED_FIELDS,
        QgsFillSymbol.createSimple({"color": "31,120,180,60",
                                    "outline_color": "31,120,180,255",
                                    "outline_width": "0.6"}))


def outlet_layer():
    return _find_layer("outlets") or _create_layer(
        "outlets", "Point?crs=" + WGS84, "StreamStats outlets", COMMON_FIELDS,
        QgsMarkerSymbol.createSimple({"name": "circle", "color": "227,26,28,255",
                                      "outline_color": "255,255,255,255",
                                      "size": "2.6"}))


def stream_layer():
    return _find_layer("streams") or _create_layer(
        "streams", "MultiLineString?crs=" + WGS84, "StreamStats streams",
        STREAM_FIELDS,
        QgsLineSymbol.createSimple({"color": "8,81,156,255", "width": "0.5",
                                    "capstyle": "round", "joinstyle": "round"}))


# ------------------------------------------------------------------ geometry
def _pts(coords):
    return [QgsPointXY(float(c[0]), float(c[1])) for c in coords]


def geojson_to_geometry(geom):
    """GeoJSON Polygon/MultiPolygon dict -> valid multipart QgsGeometry."""
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    g = QgsGeometry.fromMultiPolygonXY([[_pts(r) for r in poly] for poly in polys])
    if not g.isGeosValid():
        fixed = g.makeValid()
        if fixed and not fixed.isEmpty():
            g = fixed
    return g


def geojson_line_to_geometry(geom):
    if geom["type"] == "LineString":
        return QgsGeometry.fromMultiPolylineXY([_pts(geom["coordinates"])])
    return QgsGeometry.fromMultiPolylineXY([_pts(p) for p in geom["coordinates"]])


def _lines_only(g):
    """Keep only line parts of a clip result (drops touching points)."""
    if g is None or g.isEmpty():
        return None
    parts = g.asGeometryCollection() if g.isMultipart() or \
        "Collection" in QgsWkbTypes.displayString(g.wkbType()) else [g]
    lines = [p for p in parts
             if "LineString" in QgsWkbTypes.displayString(p.wkbType()) and p.length() > 0]
    if not lines:
        return None
    out = QgsGeometry.collectGeometry(lines)
    out.convertToMultiType()
    return out


def make_distance_area(transform_context):
    da = QgsDistanceArea()
    da.setSourceCrs(QgsCoordinateReferenceSystem(WGS84), transform_context)
    da.setEllipsoid("EPSG:7030")  # WGS 84 ellipsoid
    return da


def prepare_geometries(result, transform_context):
    """Build QGIS geometries for a client result. Safe to call off the GUI thread.

    Adds to ``result``: ws_geometry, area_m2, stream_parts
    (list of (comid, geometry, length_km, clipped)).
    """
    da = make_distance_area(transform_context)
    ws = geojson_to_geometry(result["polygon"])
    result["ws_geometry"] = ws
    result["area_m2"] = da.measureArea(ws)

    parts = []
    feats = result.get("streams") or []
    if feats:
        engine = QgsGeometry.createGeometryEngine(ws.constGet())
        engine.prepareGeometry()
        for f in feats:
            try:
                line = geojson_line_to_geometry(f["geometry"])
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            if line.isEmpty() or not engine.intersects(line.constGet()):
                continue
            if engine.contains(line.constGet()):
                clipped, was_clipped = line, 0
            else:
                clipped, was_clipped = _lines_only(line.intersection(ws)), 1
            if clipped is None:
                continue
            props = f.get("properties") or {}
            comid = str(props.get("nhdplus_comid") or props.get("comid") or "")
            parts.append((comid, clipped, da.measureLength(clipped) / 1000.0, was_clipped))
    result["stream_parts"] = parts
    return result


# ------------------------------------------------------------------ write
def add_result(result, add_streams=True):
    """Write one prepared result to the layers. Returns (ws_layer, feature)."""
    parts = result.get("stream_parts") or []
    # creation order sets draw order: outlets on top, then streams, then basins
    ws_lyr = watershed_layer()
    st_lyr = stream_layer() if (add_streams and parts) else None
    pt_lyr = outlet_layer()

    ids = [f["ws_id"] for f in ws_lyr.getFeatures() if f["ws_id"] is not None]
    ws_id = (max(ids) + 1) if ids else 1

    clat, clon = result["clicked"]
    slat, slon = result["snapped"]
    common = {
        "ws_id": ws_id, "region": result["region"], "huc": result["huc"] or "",
        "click_lat": clat, "click_lon": clon, "snap_lat": slat, "snap_lon": slon,
        "snap_dist_m": round(result["snap_distance_m"], 1),
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    wf = QgsFeature(ws_lyr.fields())
    wf.setGeometry(result["ws_geometry"])
    for k, v in common.items():
        wf[k] = v
    wf["area_km2"] = round(result["area_m2"] / 1e6, 4)
    wf["area_mi2"] = round(result["area_m2"] / M2_PER_MI2, 4)
    wf["n_streams"] = len(parts)
    wf["stream_km"] = round(sum(p[2] for p in parts), 3)
    ws_lyr.dataProvider().addFeatures([wf])

    olat, olon = result["pour_point"]
    of = QgsFeature(pt_lyr.fields())
    of.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(olon, olat)))
    for k, v in common.items():
        of[k] = v
    pt_lyr.dataProvider().addFeatures([of])

    touched = [ws_lyr, pt_lyr]
    if st_lyr is not None:
        feats = []
        for comid, geom, length_km, clipped in parts:
            sf = QgsFeature(st_lyr.fields())
            sf.setGeometry(geom)
            sf["ws_id"] = ws_id
            sf["comid"] = comid
            sf["length_km"] = round(length_km, 4)
            sf["clipped"] = clipped
            feats.append(sf)
        st_lyr.dataProvider().addFeatures(feats)
        touched.append(st_lyr)

    for lyr in touched:
        lyr.updateExtents()
        lyr.triggerRepaint()
    return ws_lyr, wf
