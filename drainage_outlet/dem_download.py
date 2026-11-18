# -*- coding: utf-8 -*-
"""Optional DEM download from the USGS 3DEP elevation ImageServer."""
import math
import os

from osgeo import gdal, osr
from qgis.core import QgsBlockingNetworkRequest, QgsProcessingException
from qgis.PyQt.QtCore import QUrl, QUrlQuery
from qgis.PyQt.QtNetwork import QNetworkRequest

BASE_URL = ("https://elevation.nationalmap.gov/arcgis/rest/services/"
            "3DEPElevation/ImageServer/exportImage")
NODATA = -99999.0
TILE = 2000  # pixels per request side (well under the server limit)


def _no_error():
    ec = getattr(QgsBlockingNetworkRequest, "ErrorCode", None)
    return ec.NoError if ec is not None else QgsBlockingNetworkRequest.NoError


def fetch_3dep(xmin, ymin, xmax, ymax, epsg, res, out_dir, feedback=None):
    """
    Download a 3DEP DEM covering the bbox (in EPSG:``epsg`` units) at
    resolution ``res`` and return the path to a VRT mosaic.
    """
    width = int(math.ceil((xmax - xmin) / res))
    height = int(math.ceil((ymax - ymin) / res))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(int(epsg))
    wkt = srs.ExportToWkt()

    tiles = []
    n_tiles = math.ceil(width / TILE) * math.ceil(height / TILE)
    k = 0
    for ty in range(0, height, TILE):
        for tx in range(0, width, TILE):
            if feedback and feedback.isCanceled():
                raise QgsProcessingException("Cancelled")
            w, h = min(TILE, width - tx), min(TILE, height - ty)
            x0 = xmin + tx * res
            x1 = x0 + w * res
            y1 = ymax - ty * res
            y0 = y1 - h * res

            q = QUrlQuery()
            for key, val in (
                ("bbox", "{},{},{},{}".format(x0, y0, x1, y1)),
                ("bboxSR", str(epsg)), ("imageSR", str(epsg)),
                ("size", "{},{}".format(w, h)),
                ("format", "tiff"), ("pixelType", "F32"),
                ("noData", str(NODATA)),
                ("noDataInterpretation", "esriNoDataMatchAny"),
                ("interpolation", "RSP_BilinearInterpolation"),
                ("f", "image"),
            ):
                q.addQueryItem(key, val)
            url = QUrl(BASE_URL)
            url.setQuery(q)

            k += 1
            if feedback:
                feedback.pushInfo("Downloading 3DEP tile {}/{} ({}x{} px)".format(k, n_tiles, w, h))
            req = QgsBlockingNetworkRequest()
            err = req.get(QNetworkRequest(url), True)
            if err != _no_error():
                raise QgsProcessingException("3DEP download failed: " + req.errorMessage())
            data = bytes(req.reply().content())
            if data[:2] not in (b"II", b"MM"):
                raise QgsProcessingException(
                    "3DEP server did not return a GeoTIFF:\n" + data[:400].decode("utf-8", "replace"))

            path = os.path.join(out_dir, "dem_3dep_{}_{}.tif".format(ty, tx))
            with open(path, "wb") as f:
                f.write(data)
            ds = gdal.Open(path, gdal.GA_Update)
            # Force exact georeferencing of what we asked for
            ds.SetGeoTransform((x0, (x1 - x0) / ds.RasterXSize, 0.0, y1, 0.0, -(y1 - y0) / ds.RasterYSize))
            ds.SetProjection(wkt)
            ds.GetRasterBand(1).SetNoDataValue(NODATA)
            ds = None
            tiles.append(path)

    vrt_path = os.path.join(out_dir, "dem_3dep.vrt")
    vrt = gdal.BuildVRT(vrt_path, tiles, srcNodata=NODATA, VRTNodata=NODATA)
    vrt = None
    return vrt_path
