"""Client for the USDA NASS CropScape CDL web service, with a local cache.

GetCDLFile takes a year and a bounding box in CONUS Albers (EPSG:5070) and
answers with a small XML document holding the URL of a clipped GeoTIFF.
Requests go through QGIS's network stack so the proxy and SSL settings in
QGIS Options apply.
"""
import os
import re

CDL_SERVICE = "https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLFile"
FIRST_YEAR = 2008            # first year with all of the lower 48 states


class CdlError(Exception):
    pass


class CdlUnavailable(CdlError):
    """The service has no CDL for that year (or that place)."""


def _enum(owner, name, scope):
    value = getattr(owner, name, None)
    if value is None:
        value = getattr(getattr(owner, scope), name)
    return value


def http_get(url, feedback=None, retries=2):
    """GET a URL through QGIS; returns (status, bytes)."""
    from qgis.core import QgsBlockingNetworkRequest
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtNetwork import QNetworkRequest

    last = ""
    for _ in range(retries + 1):
        if feedback is not None and feedback.isCanceled():
            raise CdlError("Cancelled.")
        req = QNetworkRequest(QUrl(url))
        br = QgsBlockingNetworkRequest()
        err = br.get(req, True, feedback)
        reply = br.reply()
        data = bytes(reply.content().data())
        status = reply.attribute(_enum(QNetworkRequest, "HttpStatusCodeAttribute",
                                       "Attribute"))
        try:
            status = int(status) if status is not None else 0
        except (TypeError, ValueError):
            status = 0
        if err == _enum(QgsBlockingNetworkRequest, "NoError", "ErrorCode"):
            return status, data
        if status >= 400 and data:
            return status, data             # the server answered (e.g. a SOAP fault)
        last = br.errorMessage() or ""
    raise CdlError("Could not reach CropScape: {}".format(last or "no response"))


def tif_url(year, bbox, feedback=None):
    """URL of the CDL GeoTIFF for a year and an EPSG:5070 box (xmin, ymin, xmax, ymax)."""
    url = "{}?year={}&bbox={:.0f},{:.0f},{:.0f},{:.0f}".format(CDL_SERVICE, int(year), *bbox)
    status, data = http_get(url, feedback)
    text = data.decode("utf-8", "replace")
    m = re.search(r"<returnURL>\s*(\S+?)\s*</returnURL>", text) or \
        re.search(r"(https?://\S+?\.tif)", text)
    if m:
        return m.group(1)
    detail = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()[:300]
    # the service answered without a file: no CDL for that year or place
    raise CdlUnavailable("No CDL for {} (HTTP {}): {}".format(year, status,
                                                            detail or "empty answer"))


def cache_dir():
    from qgis.core import QgsApplication
    d = os.path.join(QgsApplication.qgisSettingsDirPath(), "cache", "crop_history")
    os.makedirs(d, exist_ok=True)
    return d


def get_cdl(year, bbox, feedback=None, use_cache=True):
    """Path of a local GeoTIFF of the CDL for a year and box (downloaded if needed)."""
    key = "CDL_{}_{:.0f}_{:.0f}_{:.0f}_{:.0f}.tif".format(int(year), *bbox)
    path = os.path.join(cache_dir(), key)
    if use_cache and os.path.exists(path) and os.path.getsize(path) > 0:
        return path, True
    url = tif_url(year, bbox, feedback)
    status, data = http_get(url, feedback)
    if status not in (0, 200) or not data[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00"):
        raise CdlError("Could not download the {} CDL GeoTIFF (HTTP {}).".format(year, status))
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return path, False
