# -*- coding: utf-8 -*-
"""Output layers: watershed polygons, sub-basins, outlet points and streams.

They are scratch (memory) layers; right-click > Make Permanent to save them.
Layers are found again by a custom property, so renaming them is fine.

Geometry work (GeoJSON -> QgsGeometry, dissolving and clipping, measuring) is
done by ``prepare_geometries`` in the background task; ``add_result`` only
writes features, so the GUI stays responsive on large basins.
"""

from datetime import datetime, timezone

from qgis.core import (QgsCoordinateReferenceSystem, QgsDistanceArea, QgsFeature,
                       QgsFillSymbol, QgsGeometry, QgsLineSymbol, QgsMarkerSymbol,
                       QgsPalLayerSettings, QgsPointXY, QgsProject, QgsTextFormat,
                       QgsVectorLayer, QgsVectorLayerSimpleLabeling, QgsWkbTypes)
from qgis.PyQt.QtGui import QColor

from .compat import make_field

ROLE_KEY = "streamstats_delineator/role"
WGS84 = "EPSG:4326"
M2_PER_MI2 = 2589988.110336
GAP_EPS_DEG = 1e-5  # ~1 m: buffer used to find which sub-basin a gap touches

COMMON_FIELDS = [
    ("ws_id", "int"), ("region", "string"), ("huc", "string"),
    ("click_lat", "double"), ("click_lon", "double"),
    ("snap_lat", "double"), ("snap_lon", "double"), ("snap_dist_m", "double"),
    ("created_utc", "string"),
]
WATERSHED_FIELDS = COMMON_FIELDS + [("area_km2", "double"), ("area_mi2", "double"),
                                    ("stream_km", "double"), ("n_streams", "int"),
                                    ("n_subbasins", "int")]
STREAM_FIELDS = [("ws_id", "int"), ("comid", "string"), ("length_km", "double"),
                 ("clipped", "int"), ("sb_id", "int"), ("order", "int"),
                 ("name", "string")]
SUBBASIN_FIELDS = [("ws_id", "int"), ("sb_id", "int"), ("ds_sb_id", "int"),
                   ("name", "string"), ("max_order", "int"), ("split_order", "int"),
                   ("outlet_comid", "string"), ("n_reaches", "int"),
                   ("area_km2", "double"), ("area_mi2", "double"),
                   ("pct_area", "double"), ("stream_km", "double")]


# ------------------------------------------------------------------ layers
def _find_layer(role):
    for lyr in QgsProject.instance().mapLayers().values():
        if lyr.customProperty(ROLE_KEY) == role and lyr.isValid():
            return lyr
    return None


def _create_layer(role, uri, name, fields, symbol, above_watersheds=False):
    lyr = QgsVectorLayer(uri, name, "memory")
    lyr.dataProvider().addAttributes([make_field(n, k) for n, k in fields])
    lyr.updateFields()
    lyr.setCustomProperty(ROLE_KEY, role)
    lyr.renderer().setSymbol(symbol)
    proj = QgsProject.instance()
    if above_watersheds:
        try:  # insert directly above the watershed layer, wherever it sits
            ws = _find_layer("watersheds")
            node = proj.layerTreeRoot().findLayer(ws.id()) if ws else None
            if node is not None:
                parent = node.parent()
                idx = next(i for i, ch in enumerate(parent.children())
                           if hasattr(ch, "layerId") and ch.layerId() == ws.id())
                proj.addMapLayer(lyr, False)
                parent.insertLayer(idx, lyr)
                return lyr
        except Exception:
            pass
    proj.addMapLayer(lyr)
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
    lyr = _find_layer("streams")
    if lyr:
        return _ensure_fields(lyr, STREAM_FIELDS)
    return _create_layer(
        "streams", "MultiLineString?crs=" + WGS84, "StreamStats streams",
        STREAM_FIELDS,
        QgsLineSymbol.createSimple({"color": "8,81,156,255", "width": "0.5",
                                    "capstyle": "round", "joinstyle": "round"}))


def subbasin_layer():
    lyr = _find_layer("subbasins")
    if lyr:
        return _ensure_fields(lyr, SUBBASIN_FIELDS)
    lyr = _create_layer(
        "subbasins", "MultiPolygon?crs=" + WGS84, "StreamStats sub-basins",
        SUBBASIN_FIELDS,
        QgsFillSymbol.createSimple({"color": "0,0,0,0",
                                    "outline_color": "0,0,0,255",
                                    "outline_width": "0.45",
                                    "outline_style": "dash"}),
        above_watersheds=True)
    try:
        settings = QgsPalLayerSettings()
        settings.fieldName = "sb_id"
        fmt = QgsTextFormat()
        fmt.setSize(9)
        fmt.setColor(QColor(0, 0, 0))
        buf = fmt.buffer()
        buf.setEnabled(True)
        buf.setSize(0.8)
        fmt.setBuffer(buf)
        settings.setFormat(fmt)
        lyr.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        lyr.setLabelsEnabled(True)
    except Exception:
        pass  # labels are a nicety; never fail the result over them
    return lyr


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


def _parts_of(g, kind):
    """Non-empty parts of ``g`` whose WKB type name contains ``kind``."""
    if g is None or g.isEmpty():
        return []
    multi = g.isMultipart() or "Collection" in QgsWkbTypes.displayString(g.wkbType())
    parts = g.asGeometryCollection() if multi else [g]
    out = []
    for p in parts:
        name = QgsWkbTypes.displayString(p.wkbType())
        if kind in name and "Collection" not in name and not p.isEmpty():
            if (kind == "Polygon" and p.area() > 0) or (kind == "LineString" and p.length() > 0):
                out.append(p)
    return out


def _collect(parts):
    if not parts:
        return None
    out = QgsGeometry.collectGeometry(parts)
    out.convertToMultiType()
    return out


def _lines_only(g):
    """Keep only line parts of a clip result (drops touching points)."""
    return _collect(_parts_of(g, "LineString"))


def _polygons_only(g):
    return _collect(_parts_of(g, "Polygon"))


def make_distance_area(transform_context):
    da = QgsDistanceArea()
    da.setSourceCrs(QgsCoordinateReferenceSystem(WGS84), transform_context)
    da.setEllipsoid("EPSG:7030")  # WGS 84 ellipsoid
    return da


def _clip_streams(feats, ws, da):
    parts = []
    if not feats:
        return parts
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
        parts.append({"comid": comid, "geometry": clipped, "clipped": was_clipped,
                      "length_km": da.measureLength(clipped) / 1000.0,
                      "sb_id": None, "order": None, "name": ""})
    return parts


def _assign_gaps(ws, geoms, is_canceled):
    """Give every piece of ``ws`` not covered by ``geoms`` to the sub-basin it
    shares the most boundary with, so the sub-basins tile the watershed.

    NHDPlus catchments (30 m) and the StreamStats boundary (10 m) disagree
    slightly, leaving slivers; missing catchments leave larger holes.
    """
    keys = [k for k, g in geoms.items() if g is not None]
    if not keys:
        return geoms
    covered = QgsGeometry.unaryUnion([geoms[k] for k in keys])
    gaps = _parts_of(ws.difference(covered), "Polygon")
    extra = {k: [] for k in keys}
    boxes = {k: geoms[k].boundingBox() for k in keys}
    for gap in gaps:
        if is_canceled():
            return geoms
        grown = gap.buffer(GAP_EPS_DEG, 2)
        gbox = grown.boundingBox()
        best, best_score = None, 0.0
        for k in keys:
            if not boxes[k].intersects(gbox):
                continue
            score = grown.intersection(geoms[k]).area()
            if score > best_score:
                best, best_score = k, score
        if best is None:  # an island: give it to the nearest sub-basin
            best = min(keys, key=lambda k: geoms[k].distance(gap))
        extra[best].append(gap)
    for k, pieces in extra.items():
        if pieces:
            merged = _polygons_only(QgsGeometry.unaryUnion([geoms[k]] + pieces))
            if merged is not None:
                geoms[k] = merged
    return geoms


def _prepare_subbasins(sb, ws, ws_area_m2, da, stream_parts, is_canceled):
    """Dissolve catchments per sub-basin, clip to the watershed, fill gaps,
    renumber, and tag stream parts with their sub-basin."""
    cats = sb["catchments"]
    groups = sb["subbasins"]
    net = sb["network"]
    engine = QgsGeometry.createGeometryEngine(ws.constGet())
    engine.prepareGeometry()

    geoms = {}
    for g in groups:
        if is_canceled():
            return None
        pieces = []
        for c in g["comids"]:
            if c in cats:
                try:
                    pieces.append(geojson_to_geometry(cats[c]))
                except (KeyError, TypeError, ValueError, IndexError):
                    pass
        if not pieces:
            geoms[g["sb_id"]] = None
            continue
        u = QgsGeometry.unaryUnion(pieces)
        if engine.contains(u.constGet()):
            geoms[g["sb_id"]] = _polygons_only(u)
        else:
            geoms[g["sb_id"]] = _polygons_only(u.intersection(ws))

    if all(v is None for v in geoms.values()):
        # no catchment overlaps the StreamStats boundary: one sub-basin
        geoms[groups[0]["sb_id"]] = ws
    geoms = _assign_gaps(ws, geoms, is_canceled)

    # drop sub-basins left with no area, re-pointing their upstream neighbours
    by_id = {g["sb_id"]: g for g in groups}
    kept = [g for g in groups if geoms.get(g["sb_id"]) is not None]
    new_id = {g["sb_id"]: i + 1 for i, g in enumerate(kept)}

    def resolve(old):
        seen = set()
        while old is not None and old not in new_id and old not in seen:
            seen.add(old)
            old = by_id[old]["ds_sb_id"]
        return new_id.get(old)

    comid_sb = {}
    for g in groups:
        target = new_id.get(g["sb_id"]) or resolve(g["ds_sb_id"])
        for c in g["comids"]:
            comid_sb[c] = target

    km = {}
    for p in stream_parts:
        p["sb_id"] = comid_sb.get(p["comid"])
        a = net.get(p["comid"])
        if a:
            p["order"], p["name"] = a["order"], a["name"]
        if p["sb_id"] is not None:
            km[p["sb_id"]] = km.get(p["sb_id"], 0.0) + p["length_km"]

    out = []
    for g in kept:
        geom = geoms[g["sb_id"]]
        area = da.measureArea(geom)
        sid = new_id[g["sb_id"]]
        out.append({"sb_id": sid, "ds_sb_id": resolve(g["ds_sb_id"]),
                    "name": g["name"], "max_order": g["max_order"],
                    "split_order": sb["split_order"],
                    "outlet_comid": g["outlet_comid"], "n_reaches": len(g["comids"]),
                    "geometry": geom, "area_m2": area,
                    "pct_area": 100.0 * area / ws_area_m2 if ws_area_m2 else None,
                    "stream_km": km.get(sid, 0.0)})
    return out


def prepare_geometries(result, transform_context, is_canceled=lambda: False):
    """Build QGIS geometries for a client result. Safe to call off the GUI thread.

    Adds to ``result``: ws_geometry, area_m2, stream_parts (list of dicts),
    subbasin_parts (list of dicts, or None).
    """
    da = make_distance_area(transform_context)
    ws = geojson_to_geometry(result["polygon"])
    result["ws_geometry"] = ws
    result["area_m2"] = da.measureArea(ws)
    result["stream_parts"] = _clip_streams(result.get("streams") or [], ws, da)

    result["subbasin_parts"] = None
    if result.get("subbasins"):
        try:
            result["subbasin_parts"] = _prepare_subbasins(
                result["subbasins"], ws, result["area_m2"], da,
                result["stream_parts"], is_canceled)
        except Exception as e:  # geometry trouble must not lose the watershed
            result["subbasins_error"] = "Could not build sub-basin polygons: {}: {}".format(
                type(e).__name__, e)
    return result


# ------------------------------------------------------------------ write
def add_result(result, add_streams=True):
    """Write one prepared result to the layers. Returns (ws_layer, feature)."""
    parts = result.get("stream_parts") or []
    sb_parts = result.get("subbasin_parts") or []
    # draw order: outlets on top, then streams, sub-basin outlines, watersheds
    ws_lyr = watershed_layer()
    sb_lyr = subbasin_layer() if sb_parts else None
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
    wf["stream_km"] = round(sum(p["length_km"] for p in parts), 3)
    wf["n_subbasins"] = len(sb_parts)
    ws_lyr.dataProvider().addFeatures([wf])

    olat, olon = result["pour_point"]
    of = QgsFeature(pt_lyr.fields())
    of.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(olon, olat)))
    for k, v in common.items():
        of[k] = v
    pt_lyr.dataProvider().addFeatures([of])

    touched = [ws_lyr, pt_lyr]
    if sb_lyr is not None:
        feats = []
        for s in sb_parts:
            f = QgsFeature(sb_lyr.fields())
            f.setGeometry(s["geometry"])
            f["ws_id"] = ws_id
            for k in ("sb_id", "ds_sb_id", "name", "max_order", "split_order",
                      "outlet_comid", "n_reaches"):
                f[k] = s[k]
            f["area_km2"] = round(s["area_m2"] / 1e6, 4)
            f["area_mi2"] = round(s["area_m2"] / M2_PER_MI2, 4)
            f["pct_area"] = round(s["pct_area"], 2) if s["pct_area"] is not None else None
            f["stream_km"] = round(s["stream_km"], 3) if add_streams else None
            feats.append(f)
        sb_lyr.dataProvider().addFeatures(feats)
        touched.append(sb_lyr)

    if st_lyr is not None:
        feats = []
        for p in parts:
            sf = QgsFeature(st_lyr.fields())
            sf.setGeometry(p["geometry"])
            sf["ws_id"] = ws_id
            sf["comid"] = p["comid"]
            sf["length_km"] = round(p["length_km"], 4)
            sf["clipped"] = p["clipped"]
            sf["sb_id"] = p["sb_id"]
            sf["order"] = p["order"]
            sf["name"] = p["name"]
            feats.append(sf)
        st_lyr.dataProvider().addFeatures(feats)
        touched.append(st_lyr)

    for lyr in touched:
        lyr.updateExtents()
        lyr.triggerRepaint()
    return ws_lyr, wf
