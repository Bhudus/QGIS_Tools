# -*- coding: utf-8 -*-
"""Find the LiDAR collections (ISGS and USGS 3DEP) that cover a field."""

import json
import os
import time

from qgis.PyQt.QtCore import QUrl
from qgis.core import (
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingException,
    QgsVectorLayer,
)

from .field_dem import (
    FT_US,
    ISGS_COLLECTIONS_URL,
    USGS_3DEP_NATIVE_M,
    USGS_EPT_INDEX_URL,
    WGS84,
    LidarFieldDEM,
    cache_dir,
    download_to,
    http_get,
    year_of,
)


def field_to_wgs(geoms, crs, transform_context=None):
    """Union of the field polygons, in WGS84."""
    if not geoms:
        raise QgsProcessingException("The field layer has no polygons.")
    g = QgsGeometry.unaryUnion(geoms)
    tc = transform_context or QgsCoordinateTransformContext()
    g.transform(QgsCoordinateTransform(crs, WGS84, tc))
    return g


def _coverage_pct(field_wgs, footprint):
    """Share of the field (by area) inside a footprint, 0-100."""
    try:
        a = field_wgs.area()
        if a <= 0 or footprint is None or footprint.isEmpty():
            return None
        inter = field_wgs.intersection(footprint)
        return max(0.0, min(100.0, 100.0 * inter.area() / a))
    except Exception:
        return None


def _esri_rings_to_geom(rings):
    parts = []
    for ring in rings or []:
        pts = [QgsPointXY(x, y) for x, y in ring[:]]
        if len(pts) >= 4:
            parts.append(QgsGeometry.fromPolygonXY([pts]))
    if not parts:
        return None
    return QgsGeometry.unaryUnion(parts)


def list_isgs(field_wgs, feedback):
    """ISGS collections intersecting the field (any status)."""
    hull = field_wgs.convexHull().simplify(0.00005)
    geom = {"rings": [[[round(p.x(), 6), round(p.y(), 6)] for p in ring]
                      for ring in hull.asPolygon()],
            "spatialReference": {"wkid": 4326}}
    q = ("?geometry=%s&geometryType=esriGeometryPolygon&inSR=4326"
         "&spatialRel=esriSpatialRelIntersects"
         "&outFields=CollectionName,CollectionYear,Status,QualityLevel,TileIndex,LAS,"
         "DTMImageService&returnGeometry=true&outSR=4326&maxAllowableOffset=0.0002&f=json"
         % QUrl.toPercentEncoding(json.dumps(geom)).data().decode())
    _, _, data = http_get(ISGS_COLLECTIONS_URL + q, feedback=feedback, retries=2)
    js = json.loads(data.decode("utf-8"))
    if "error" in js:
        raise QgsProcessingException("ISGS service error: %s" % js["error"])
    out = []
    for ft in js.get("features", []):
        a = ft.get("attributes", {})
        usable = bool(a.get("LAS") and a.get("TileIndex"))
        cell_m = LidarFieldDEM._isgs_native_res(a, feedback) if usable else None
        fp = _esri_rings_to_geom((ft.get("geometry") or {}).get("rings"))
        out.append({
            "source": "ISGS",
            "collection": "%s %s" % (a.get("CollectionName") or "", a.get("CollectionYear") or ""),
            "year": str(a.get("CollectionYear") or ""),
            "year_num": year_of(a.get("CollectionYear")),
            "status": a.get("Status") or "",
            "quality": a.get("QualityLevel"),
            "cell_ft": round(cell_m / FT_US, 2) if cell_m else None,
            "coverage": _coverage_pct(field_wgs, fp),
            "usable": usable,
        })
    return out


def _usgs_index(feedback):
    idx = os.path.join(cache_dir("usgs"), "resources.geojson")
    if not os.path.exists(idx) or time.time() - os.path.getmtime(idx) > 30 * 86400:
        feedback.pushInfo("Downloading 3DEP project index (~9 MB, once a month) ...")
        download_to(USGS_EPT_INDEX_URL, idx, feedback)
    lyr = QgsVectorLayer(idx, "3dep", "ogr")
    if not lyr.isValid():
        raise QgsProcessingException("Could not read the 3DEP index %s" % idx)
    return lyr


def list_usgs(field_wgs, feedback):
    """USGS 3DEP EPT projects intersecting the field."""
    lyr = _usgs_index(feedback)
    names = [f.name() for f in lyr.fields()]
    out = []
    for ft in lyr.getFeatures(QgsFeatureRequest().setFilterRect(field_wgs.boundingBox())):
        g = ft.geometry()
        if not g.intersects(field_wgs):
            continue
        nm = ft["name"] if "name" in names else ""
        y = year_of(nm)
        out.append({
            "source": "USGS 3DEP",
            "collection": nm,
            "year": str(y) if y else "",
            "year_num": y,
            "status": "Available",
            "quality": None,
            "cell_ft": round(USGS_3DEP_NATIVE_M / FT_US, 2),
            "coverage": _coverage_pct(field_wgs, g),
            "usable": True,
        })
    return out


def list_collections(field_wgs, feedback, include_usgs=True):
    """All collections covering the field, newest first, ISGS before 3DEP."""
    rows = []
    try:
        rows += list_isgs(field_wgs, feedback)
    except Exception as e:
        if feedback.isCanceled():
            raise
        feedback.reportError("ISGS lookup failed: %s" % e, False)
    if include_usgs:
        try:
            rows += list_usgs(field_wgs, feedback)
        except Exception as e:
            if feedback.isCanceled():
                raise
            feedback.reportError("USGS 3DEP lookup failed: %s" % e, False)
    rows.sort(key=lambda r: (-(r["year_num"] or 0), r["source"] != "ISGS", r["collection"]))
    return rows


def describe(r):
    cov = "-" if r["coverage"] is None else "%.0f%%" % r["coverage"]
    cell = "-" if not r["cell_ft"] else "%g ft" % r["cell_ft"]
    ql = "" if r["quality"] in (None, "") else "  QL%s" % r["quality"]
    flag = "" if r["usable"] else "  (no LAS download)"
    return "%-9s %-6s %-34s cell %-8s covers %s%s  [%s]%s" % (
        r["source"], r["year"] or "?", r["collection"], cell, cov, ql, r["status"], flag)
