"""Download SoilGrids 2.0 (ISRIC) layers for a small area.

Uses the SoilGrids Web Coverage Service in its native Homolosine projection,
through QGIS's network stack so proxy settings from QGIS Options apply. If
the WCS fails, falls back to reading the same layers from the SoilGrids
cloud-optimised VRTs with GDAL.
"""
import json
import os
import tempfile

from .zoning import DEPTH_LABELS, PROPERTIES

IGH_PROJ = "+proj=igh +lat_0=0 +lon_0=0 +datum=WGS84 +units=m +no_defs"
WCS_URL = (
    "https://maps.isric.org/mapserv?map=/map/{prop}.map&SERVICE=WCS&VERSION=2.0.1"
    "&REQUEST=GetCoverage&COVERAGEID={prop}_{depth}_mean&FORMAT=GEOTIFF_INT16"
    "&SUBSET=X({xmin},{xmax})&SUBSET=Y({ymin},{ymax})"
    "&SUBSETTINGCRS=http://www.opengis.net/def/crs/EPSG/0/152160"
    "&OUTPUTCRS=http://www.opengis.net/def/crs/EPSG/0/152160"
)
VRT_URL = ("/vsicurl?max_retry=3&retry_delay=1&list_dir=no&url="
           "https://files.isric.org/soilgrids/latest/data/{prop}/{prop}_{depth}_mean.vrt")
CELL = 250.0


class SoilGridsError(Exception):
    pass


def _enum(owner, name, scope):
    value = getattr(owner, name, None)
    if value is None:
        value = getattr(getattr(owner, scope), name)
    return value


def snap_bbox(xmin, ymin, xmax, ymax, pad=CELL):
    """Expand a Homolosine bounding box by pad and snap it to the 250 m grid."""
    import math
    return (math.floor((xmin - pad) / CELL) * CELL, math.floor((ymin - pad) / CELL) * CELL,
            math.ceil((xmax + pad) / CELL) * CELL, math.ceil((ymax + pad) / CELL) * CELL)


def _get(url, feedback):
    from qgis.core import QgsBlockingNetworkRequest
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtNetwork import QNetworkRequest

    blocking = QgsBlockingNetworkRequest()
    err = blocking.get(QNetworkRequest(QUrl(url)), True, feedback)
    content = bytes(blocking.reply().content().data())
    if err != _enum(QgsBlockingNetworkRequest, "NoError", "ErrorCode"):
        raise SoilGridsError(f"SoilGrids request failed: {blocking.errorMessage()}")
    return content


def post_json(url, payload):
    """POST JSON through QGIS's network stack and return the parsed reply."""
    from qgis.core import QgsBlockingNetworkRequest
    from qgis.PyQt.QtCore import QByteArray, QUrl
    from qgis.PyQt.QtNetwork import QNetworkRequest

    req = QNetworkRequest(QUrl(url))
    req.setHeader(_enum(QNetworkRequest, "ContentTypeHeader", "KnownHeaders"), "application/json")
    blocking = QgsBlockingNetworkRequest()
    err = blocking.post(req, QByteArray(json.dumps(payload).encode("utf-8")))
    body = bytes(blocking.reply().content().data()).decode("utf-8", "replace")
    if err != _enum(QgsBlockingNetworkRequest, "NoError", "ErrorCode"):
        raise RuntimeError(f"{blocking.errorMessage()} {body[:300]}")
    return json.loads(body) if body.strip() else {}


def _read_tif(path):
    from osgeo import gdal
    ds = gdal.Open(path)
    if ds is None:
        raise SoilGridsError("SoilGrids returned a file GDAL can't read.")
    arr = ds.GetRasterBand(1).ReadAsArray()
    gt = ds.GetGeoTransform()
    ds = None
    return arr, gt


def _fetch_wcs(prop, depth, bbox, folder, feedback):
    xmin, ymin, xmax, ymax = bbox
    url = WCS_URL.format(prop=prop, depth=depth, xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax)
    content = _get(url, feedback)
    if content[:4] not in (b"II*\x00", b"MM\x00*"):
        raise SoilGridsError("SoilGrids WCS did not return a GeoTIFF: "
                             + content[:300].decode("utf-8", "replace"))
    path = os.path.join(folder, f"{prop}_{depth}.tif")
    with open(path, "wb") as f:
        f.write(content)
    return _read_tif(path)


def _fetch_vrt(prop, depth, bbox, folder, feedback):
    from osgeo import gdal
    xmin, ymin, xmax, ymax = bbox
    path = os.path.join(folder, f"{prop}_{depth}_vrt.tif")
    ds = gdal.Translate(path, VRT_URL.format(prop=prop, depth=depth),
                        projWin=[xmin, ymax, xmax, ymin], xRes=CELL, yRes=CELL)
    if ds is None:
        raise SoilGridsError(f"Could not read {prop} {depth} from the SoilGrids VRT: "
                             + gdal.GetLastErrorMsg())
    ds = None
    return _read_tif(path)


def fetch_layers(bbox, feedback):
    """Sand, clay and bulk density at the six SoilGrids depths for a Homolosine bbox.

    Returns (raw, geotransform) where raw[prop] is a list of six 2-D arrays
    on the same grid, in SoilGrids' integer units.
    """
    folder = tempfile.mkdtemp(prefix="soilgrids_")
    fetchers = [_fetch_wcs, _fetch_vrt]
    raw, gt, shape = {}, None, None
    n, total = 0, len(PROPERTIES) * len(DEPTH_LABELS)
    for prop in PROPERTIES:
        raw[prop] = []
        for depth in DEPTH_LABELS:
            if feedback is not None and feedback.isCanceled():
                raise SoilGridsError("Canceled.")
            errors = []
            for fetch in list(fetchers):
                try:
                    arr, this_gt = fetch(prop, depth, bbox, folder, feedback)
                    break
                except Exception as e:
                    errors.append(f"{fetch.__name__[7:].upper()}: {e}")
                    if fetch is fetchers[0] and len(fetchers) > 1:
                        fetchers.remove(fetch)  # stop retrying a method that failed
                        if feedback is not None:
                            feedback.pushWarning(f"SoilGrids WCS failed ({e}); using the SoilGrids VRT files.")
            else:
                raise SoilGridsError(f"Could not download SoilGrids {prop} {depth}. " + " ".join(errors))
            if shape is None:
                gt, shape = this_gt, arr.shape
            elif arr.shape != shape:
                raise SoilGridsError("SoilGrids layers came back on different grids.")
            raw[prop].append(arr)
            n += 1
            if feedback is not None:
                feedback.setProgress(70.0 * n / total)
    return raw, gt
