# -*- coding: utf-8 -*-
"""
LiDAR Field DEM  -  Processing algorithm of the LiDAR Field DEM plugin (QGIS 3.22+)

Given a field boundary, builds a bare-earth DEM from LiDAR point clouds:

  * ISGS (Illinois Height Modernization Program)
      - finds the LiDAR collections covering the field from the ISGS
        ILHMP/Lidar_Collections ArcGIS service
      - reads the collection's tile index and picks the tiles under the field
      - pulls ONLY those tiles out of the county-wide LAS zip using HTTP range
        requests (the zips are hundreds of GB; nothing else is downloaded)
  * USGS 3DEP (any U.S. state)
      - finds 3DEP projects covering the field in the public AWS
        Entwine Point Tile (EPT) collection
      - streams only the points inside the field's bounding box

Then, with PDAL: keep ground points, crop to the (buffered) field, reproject,
triangulate (TIN) and rasterize, and finally clip the DEM to the exact field
boundary with GDAL.

Requires: the PDAL command-line program (pdal / pdal.exe), e.g. from OSGeo4W.
"""

import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

from qgis.PyQt.QtCore import QCoreApplication, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import (
    QgsApplication,
    QgsBlockingNetworkRequest,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterString,
    QgsProject,
    QgsRectangle,
    QgsUnitTypes,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant
import processing


ISGS_COLLECTIONS_URL = ("https://maps.isgs.illinois.edu/arcgis/rest/services/"
                        "ILHMP/Lidar_Collections/MapServer/0/query")
USGS_EPT_INDEX_URL = ("https://raw.githubusercontent.com/hobuinc/usgs-lidar/"
                      "master/boundaries/resources.geojson")
USGS_EPT_BASE = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public/"

FT_US = 1200.0 / 3937.0          # metres per US survey foot
FT_INTL = 0.3048
WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
WEB_MERCATOR = QgsCoordinateReferenceSystem("EPSG:3857")


# --------------------------------------------------------------------------
# HTTP helpers (Qt network stack, so QGIS proxy settings are honoured)
# --------------------------------------------------------------------------
def http_get(url, byte_range=None, feedback=None, retries=3):
    """GET a URL. Returns (status, headers{lowercase: str}, bytes)."""
    last_err = None
    for attempt in range(retries):
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"User-Agent", b"QGIS-LidarFieldDEM/1.0")
        if byte_range is not None:
            req.setRawHeader(b"Range", ("bytes=%d-%d" % byte_range).encode())
        br = QgsBlockingNetworkRequest()
        err = br.get(req, True, feedback)
        if feedback is not None and feedback.isCanceled():
            raise QgsProcessingException("Canceled.")
        reply = br.reply()
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if err == QgsBlockingNetworkRequest.NoError and status in (200, 206):
            headers = {}
            for k in reply.rawHeaderList():
                name = bytes(k).decode("latin-1")
                headers[name.lower()] = bytes(reply.rawHeader(k)).decode("latin-1")
            return status, headers, bytes(reply.content())
        last_err = "%s (HTTP %s)" % (br.errorMessage(), status)
        time.sleep(1.5 * (attempt + 1))
    raise QgsProcessingException("Download failed: %s\n%s" % (url, last_err))


def download_to(url, path, feedback=None):
    _, _, data = http_get(url, feedback=feedback)
    with open(path, "wb") as f:
        f.write(data)
    return path


class HttpRangeFile(io.RawIOBase):
    """Read-only, seekable file object over HTTP byte ranges, so Python's
    zipfile can read individual members of a huge remote zip."""

    def __init__(self, url, feedback=None, block_size=4 * 1024 * 1024, max_blocks=6):
        super().__init__()
        self.url = url
        self.feedback = feedback
        self.block_size = block_size
        self.max_blocks = max_blocks
        self._cache = {}
        self._order = []
        self._pos = 0
        _, headers, _ = http_get(url, (0, 0), feedback)
        cr = headers.get("content-range", "")
        m = re.search(r"/(\d+)\s*$", cr)
        if not m:
            raise QgsProcessingException(
                "Server does not support partial downloads for %s" % url)
        self.size = int(m.group(1))

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._pos

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        elif whence == io.SEEK_END:
            self._pos = self.size + offset
        self._pos = max(0, self._pos)
        return self._pos

    def _block(self, idx):
        if idx in self._cache:
            return self._cache[idx]
        start = idx * self.block_size
        end = min(self.size, start + self.block_size) - 1
        _, _, data = http_get(self.url, (start, end), self.feedback)
        self._cache[idx] = data
        self._order.append(idx)
        if len(self._order) > self.max_blocks:
            self._cache.pop(self._order.pop(0), None)
        return data

    def readinto(self, b):
        n = len(b)
        if self._pos >= self.size or n == 0:
            return 0
        n = min(n, self.size - self._pos)
        done = 0
        while done < n:
            idx = (self._pos + done) // self.block_size
            blk = self._block(idx)
            off = (self._pos + done) - idx * self.block_size
            take = min(n - done, len(blk) - off)
            if take <= 0:
                break
            b[done:done + take] = blk[off:off + take]
            done += take
        self._pos += done
        return done


def extract_member(zf, info, dest, feedback=None):
    """Stream one zip member to a local file (via a .part file)."""
    tmp = dest + ".part"
    with zf.open(info) as src, open(tmp, "wb") as out:
        while True:
            if feedback is not None and feedback.isCanceled():
                raise QgsProcessingException("Canceled.")
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    os.replace(tmp, dest)
    return dest


# --------------------------------------------------------------------------
# misc helpers
# --------------------------------------------------------------------------
def year_of(text):
    years = [int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d\d)(?!\d)", str(text or ""))]
    return max(years) if years else 0


def srs_string(crs):
    """A non-empty SRS string PDAL/PROJ understands: EPSG code, else WKT2, else PROJ."""
    if crs is None or not crs.isValid():
        return ""
    aid = crs.authid()
    if aid and aid.upper().startswith("EPSG:"):
        return aid
    variants = []
    try:
        from qgis.core import Qgis
        variants.append(Qgis.CrsWktVariant.Preferred)
    except Exception:
        pass
    for name in ("WKT_PREFERRED", "WKT2_2019", "WKT_PREFERRED_GDAL"):
        v = getattr(QgsCoordinateReferenceSystem, name, None)
        if v is not None:
            variants.append(v)
    for v in variants:
        try:
            w = crs.toWkt(v)
            if w:
                return w
        except Exception:
            pass
    for fn in (lambda: crs.toWkt(), lambda: crs.toProj()):
        try:
            w = fn()
            if w:
                return w
        except Exception:
            pass
    return ""


def metres_per_unit(crs):
    return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), QgsUnitTypes.DistanceMeters)


def vertical_unit_from_prj(prj_text, fallback_m):
    """Metres per vertical unit, read from an ESRI .prj VERTCS if present."""
    if prj_text and "VERTCS" in prj_text.upper():
        vert = prj_text[prj_text.upper().index("VERTCS"):]
        units = re.findall(r'UNIT\["([^"]+)"\s*,\s*([0-9.eE+-]+)', vert)
        if units:
            return float(units[-1][1])
    return fallback_m


IL_EAST_OUT, IL_WEST_OUT = "EPSG:3435", "EPSG:3436"   # NAD83 / Illinois East|West (ftUS)


def in_illinois(lon, lat):
    return -91.6 <= lon <= -87.4 and 36.9 <= lat <= 42.6


def il_zone_from_lon(lon):
    """Approximate East/West State Plane zone split (zones follow county lines,
    roughly -88.9 to -89.4 W). Used only when the LiDAR data does not say."""
    return "east" if lon >= -89.1 else "west"


def il_zone_from_prj(prj_text):
    up = (prj_text or "").upper().replace(" ", "_")
    if "ILLINOIS" in up and "EAST" in up:
        return "east"
    if "ILLINOIS" in up and "WEST" in up:
        return "west"
    return None


def il_source_epsg(prj_text, zone):
    """EPSG code matching the tile index horizontal CRS (fallback for LAS files
    that carry no CRS of their own)."""
    if "2011" in (prj_text or ""):
        return "EPSG:6455" if zone == "east" else "EPSG:6457"
    return IL_EAST_OUT if zone == "east" else IL_WEST_OUT


DEFAULT_CELL_FACTOR = 10       # blank cell size = 10 x the LiDAR's native cell size
USGS_3DEP_NATIVE_M = 1.0      # USGS 3DEP standard bare-earth DEM is 1 m


def _units_m_from_esri_sr(sr):
    """Metres per unit of an ArcGIS REST spatialReference dict."""
    sr = sr or {}
    for key in ("latestWkid", "wkid"):
        code = sr.get(key)
        if code:
            for auth in ("EPSG", "ESRI"):
                c = QgsCoordinateReferenceSystem("%s:%d" % (auth, code))
                if c.isValid():
                    return metres_per_unit(c)
    wkt = sr.get("wkt") or ""
    if wkt:
        c = QgsCoordinateReferenceSystem.fromWkt(wkt)
        if c.isValid():
            return metres_per_unit(c)
        up = wkt.upper()
        if "FOOT_US" in up or "FT_US" in up or "US_SURVEY" in up:
            return FT_US
        if "FOOT" in up or "FEET" in up:
            return FT_INTL
    return None


def las_point_spacing_m(paths, unit_m):
    """Nominal first-return point spacing (m) from LAS headers."""
    import struct
    area, n = 0.0, 0
    for p in paths:
        try:
            with open(p, "rb") as f:
                h = f.read(375)
            if h[:4] != b"LASF":
                continue
            minor = h[25]
            maxx, minx, maxy, miny = struct.unpack("<4d", h[179:211])
            first = struct.unpack("<I", h[111:115])[0]
            if minor >= 4 and len(h) >= 375:
                first = struct.unpack("<Q", h[255:263])[0] or first
            if first == 0:
                first = struct.unpack("<I", h[107:111])[0]
                if minor >= 4 and len(h) >= 255:
                    first = struct.unpack("<Q", h[247:255])[0] or first
            area += (maxx - minx) * (maxy - miny) * unit_m * unit_m
            n += first
        except Exception:
            continue
    if area <= 0 or n <= 0:
        return None
    return math.sqrt(area / n)


def utm_crs_for(lon, lat):
    zone = int(math.floor((lon + 180.0) / 6.0)) + 1
    if lat >= 0 and 10 <= zone <= 23 and -125 < lon < -66:
        return QgsCoordinateReferenceSystem("EPSG:269%02d" % zone)   # NAD83 / UTM (CONUS)
    return QgsCoordinateReferenceSystem("EPSG:%d" % ((32600 if lat >= 0 else 32700) + zone))


def cache_dir(*parts):
    d = os.path.join(tempfile.gettempdir(), "lidar_field_dem_cache", *parts)
    os.makedirs(d, exist_ok=True)
    return d


def safe(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(s))


def find_pdal(user_path):
    cands = []
    if user_path:
        cands.append(user_path)
    exe = "pdal.exe" if os.name == "nt" else "pdal"
    w = shutil.which("pdal")
    if w:
        cands.append(w)
    roots = [os.environ.get("OSGEO4W_ROOT", "")]
    try:
        prefix = QgsApplication.prefixPath()          # .../apps/qgis(-ltr)
        roots.append(os.path.normpath(os.path.join(prefix, "..", "..")))
    except Exception:
        pass
    for r in roots:
        if r:
            cands.append(os.path.join(r, "bin", exe))
    cands += [r"C:\OSGeo4W\bin\pdal.exe", r"C:\OSGeo4W64\bin\pdal.exe",
              "/usr/bin/pdal", "/usr/local/bin/pdal", "/opt/homebrew/bin/pdal"]
    for c in cands:
        if c and (os.path.isfile(c) or shutil.which(c)):
            return c
    raise QgsProcessingException(
        "Could not find the PDAL program. Enter its full path in 'PDAL executable' "
        "(e.g. C:\\OSGeo4W\\bin\\pdal.exe).")


def run_pdal(pdal_exe, pipeline, work_dir, feedback):
    ppath = os.path.join(work_dir, "pipeline.json")
    with open(ppath, "w") as f:
        json.dump(pipeline, f, indent=2)
    feedback.pushInfo("PDAL pipeline: %s" % ppath)
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000      # CREATE_NO_WINDOW
    proc = subprocess.Popen([pdal_exe, "pipeline", ppath],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            universal_newlines=True, **kw)
    out_lines = []
    while True:
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
            break
        if line:
            out_lines.append(line.rstrip())
            feedback.pushConsoleInfo(line.rstrip())
        if feedback.isCanceled():
            proc.kill()
            raise QgsProcessingException("Canceled.")
    if proc.returncode != 0:
        raise QgsProcessingException(
            "PDAL failed (exit %s):\n%s" % (proc.returncode, "\n".join(out_lines[-30:])))


# --------------------------------------------------------------------------
# the algorithm
# --------------------------------------------------------------------------
class LidarFieldDEM(QgsProcessingAlgorithm):

    INPUT = "INPUT"
    SOURCE = "SOURCE"
    YEAR = "YEAR"
    RESOLUTION = "RESOLUTION"
    BUFFER = "BUFFER"
    EXTEND = "EXTEND"
    CLASSES = "CLASSES"
    OUT_CRS = "OUT_CRS"
    Z_UNITS = "Z_UNITS"
    KEEP_LAZ = "KEEP_LAZ"
    FILL = "FILL"
    MINSLOPE = "MINSLOPE"
    FILLED = "FILLED"
    PDAL = "PDAL"
    OUTPUT = "OUTPUT"

    SOURCES = ["Automatic - ISGS in Illinois, USGS 3DEP elsewhere",
               "ISGS - Illinois Height Modernization (Illinois only)",
               "USGS 3DEP - AWS point clouds (any U.S. state)"]
    SRC_AUTO, SRC_ISGS, SRC_USGS = 0, 1, 2
    ZUNITS = ["Feet (US survey)", "Meters"]

    def tr(self, s):
        return QCoreApplication.translate("LidarFieldDEM", s)

    def createInstance(self):
        return LidarFieldDEM()

    def name(self):
        return "lidarfielddem"

    def displayName(self):
        return self.tr("Bare-earth DEM for a field (ISGS / USGS 3DEP LiDAR)")

    def group(self):
        return self.tr("LiDAR")

    def groupId(self):
        return "lidar"

    def shortHelpString(self):
        return self.tr(
            "Builds a bare-earth DEM clipped to a field boundary from LiDAR ground points.\n\n"
            "LiDAR source: Automatic (default) uses ISGS for fields in Illinois and USGS 3DEP "
            "elsewhere. Fields outside Illinois always use USGS 3DEP, even if ISGS is chosen.\n"
            "ISGS: finds the Illinois Height Modernization collection covering the field, "
            "pulls only the needed LAS tiles out of the county zip (HTTP range requests).\n"
            "USGS 3DEP: streams points for the field from the public AWS EPT archive "
            "(works in any state with 3DEP coverage).\n\n"
            "Collection year: leave blank for the most recent; the log lists the years found.\n"
            "Cell size is in feet (US survey). Leave it blank to use 10 times the LiDAR's native "
            "cell size: the ISGS DTM cell size for that collection (2 ft for Champaign 2020, so "
            "20 ft), or 1 m for USGS 3DEP (so 32.8 ft). If ISGS doesn't publish one, the native "
            "size is estimated from the LiDAR point spacing.\n"
            "Fill sinks (Wang & Liu): also writes a DEM in which every depression is filled so "
            "that each cell drains to the DEM's edge. Minimum slope (degrees) keeps a slight "
            "gradient across filled areas; 0 gives flat fills.\n"
            "Extend DEM beyond field (ft, default 100): the DEM covers the field plus this "
            "distance, so tile outlets and waterways can be placed outside the field.\n"
            "Interpolation buffer (ft): extra LiDAR data read beyond that extent so the DEM "
            "edges are interpolated properly; it is not part of the output.\n"
            "Ground classes: ASPRS codes kept, default 2 (ground).\n"
            "Downloaded tiles are cached in your temp folder (lidar_field_dem_cache).\n\n"
            "Requires the PDAL command-line program.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Field boundary"), [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterEnum(
            self.SOURCE, self.tr("LiDAR source"), options=self.SOURCES, defaultValue=0))
        self.addParameter(QgsProcessingParameterString(
            self.YEAR, self.tr("Collection year (blank = most recent)"),
            defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.RESOLUTION, self.tr("DEM cell size (feet, blank = 10 x the LiDAR's native cell size)"),
            QgsProcessingParameterNumber.Double, defaultValue=None, optional=True, minValue=0.1))
        self.addParameter(QgsProcessingParameterNumber(
            self.EXTEND, self.tr("Extend DEM beyond field boundary (feet)"),
            QgsProcessingParameterNumber.Double, defaultValue=100.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.BUFFER, self.tr("Extra interpolation buffer beyond DEM extent (feet)"),
            QgsProcessingParameterNumber.Double, defaultValue=50.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterString(
            self.CLASSES, self.tr("Ground classes to keep (comma separated)"),
            defaultValue="2"))
        self.addParameter(QgsProcessingParameterCrs(
            self.OUT_CRS, self.tr("Output CRS (blank = EPSG:3435 / 3436 in Illinois, UTM elsewhere)"),
            optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.Z_UNITS, self.tr("Elevation units"), options=self.ZUNITS, defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.KEEP_LAZ, self.tr("Also save clipped ground points (.laz)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FILL, self.tr("Also produce a DEM with sinks filled (Wang & Liu)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterNumber(
            self.MINSLOPE, self.tr("Minimum slope across filled sinks (degrees, 0 = flat)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.06, minValue=0.0, maxValue=5.0))
        self.addParameter(QgsProcessingParameterString(
            self.PDAL, self.tr("PDAL executable (blank = auto-detect)"),
            defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Bare-earth DEM")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.FILLED, self.tr("Filled DEM (Wang & Liu) [blank = next to the DEM]"),
            optional=True, createByDefault=False))

    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException("Invalid field boundary layer.")
        src_idx = self.parameterAsEnum(parameters, self.SOURCE, context)
        year_req = (self.parameterAsString(parameters, self.YEAR, context) or "").strip()
        raw_res = parameters.get(self.RESOLUTION)
        if raw_res is None or (isinstance(raw_res, str) and not raw_res.strip()):
            res_m = None                                   # use source native resolution
        else:
            res_m = self.parameterAsDouble(parameters, self.RESOLUTION, context) * FT_US
            if res_m <= 0:
                res_m = None
        buf_m = self.parameterAsDouble(parameters, self.BUFFER, context) * FT_US   # feet -> m
        ext_m = self.parameterAsDouble(parameters, self.EXTEND, context) * FT_US   # feet -> m
        classes = self._parse_classes(self.parameterAsString(parameters, self.CLASSES, context))
        out_crs = self.parameterAsCrs(parameters, self.OUT_CRS, context)
        z_target_m = FT_US if self.parameterAsEnum(parameters, self.Z_UNITS, context) == 0 else 1.0
        keep_laz = self.parameterAsBoolean(parameters, self.KEEP_LAZ, context)
        do_fill = self.parameterAsBoolean(parameters, self.FILL, context)
        min_slope = self.parameterAsDouble(parameters, self.MINSLOPE, context)
        pdal_exe = find_pdal(self.parameterAsString(parameters, self.PDAL, context).strip())
        out_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        feedback.pushInfo("PDAL: %s" % pdal_exe)

        # ---- field geometry (all / selected features dissolved) ----
        geoms = [f.geometry() for f in source.getFeatures() if f.hasGeometry()]
        if not geoms:
            raise QgsProcessingException("The field layer has no polygons.")
        field = QgsGeometry.unaryUnion(geoms)
        field_crs = source.sourceCrs()
        field_wgs = QgsGeometry(field)
        field_wgs.transform(QgsCoordinateTransform(field_crs, WGS84, context.transformContext()))
        c = field_wgs.centroid().asPoint()
        feedback.pushInfo("Field centroid: %.5f, %.5f" % (c.y(), c.x()))

        # ---- DEM extent = field + EXTEND (buffered in a metric UTM CRS) ----
        if ext_m > 0:
            utm_tmp = utm_crs_for(c.x(), c.y())
            g = QgsGeometry(field)
            g.transform(QgsCoordinateTransform(field_crs, utm_tmp, context.transformContext()))
            g = g.buffer(ext_m, 16)
            g.transform(QgsCoordinateTransform(utm_tmp, field_crs, context.transformContext()))
            field = g
            field_wgs = QgsGeometry(field)
            field_wgs.transform(QgsCoordinateTransform(field_crs, WGS84,
                                                       context.transformContext()))
            feedback.pushInfo("DEM extent: field + %.0f ft" % (ext_m / FT_US))

        work = tempfile.mkdtemp(prefix="fielddem_")

        # ---- is the field in Illinois? (ISGS coverage = the state) ----
        in_il = self._isgs_covers(field_wgs, feedback)
        if in_il is None:                       # ISGS service unreachable: rough box test
            in_il = in_illinois(c.x(), c.y())
        feedback.pushInfo("Field is %s Illinois" % ("in" if in_il else "outside"))

        if not in_il:
            if src_idx == self.SRC_ISGS:
                feedback.reportError(
                    "The field is outside Illinois, so ISGS data isn't available; "
                    "using USGS 3DEP instead.", False)
            src_idx = self.SRC_USGS
        elif src_idx == self.SRC_AUTO:
            src_idx = self.SRC_ISGS
        feedback.pushInfo("LiDAR source: %s" % ("ISGS" if src_idx == self.SRC_ISGS else "USGS 3DEP"))

        if src_idx == self.SRC_ISGS:
            pts = self._isgs(field, field_crs, field_wgs, year_req, buf_m, context, feedback)
        else:
            pts = self._usgs(field, field_crs, field_wgs, year_req, buf_m, context, feedback,
                             in_il=in_il)

        if not out_crs.isValid():
            out_crs = pts["default_out_crs"]
        feedback.pushInfo("Output CRS: %s" % (out_crs.authid() or out_crs.description()))

        # field and buffered field in output CRS
        field_out = QgsGeometry(field)
        field_out.transform(QgsCoordinateTransform(field_crs, out_crs, context.transformContext()))
        upm = metres_per_unit(out_crs)                    # metres per output CRS unit
        if out_crs.isGeographic():
            raise QgsProcessingException("Choose a projected output CRS (not lat/long).")
        if res_m is None:
            native = pts.get("native_res_m")
            if not native:
                raise QgsProcessingException(
                    "Could not determine the LiDAR's native cell size; enter a DEM cell size.")
            res_m = DEFAULT_CELL_FACTOR * native
            feedback.pushInfo("Cell size: %.2f ft (%.2f m) - %d x the native %.2f ft; %s" % (
                res_m / FT_US, res_m, DEFAULT_CELL_FACTOR, native / FT_US,
                pts.get("native_res_note", "native")))
        else:
            feedback.pushInfo("Cell size: %.2f ft (%.2f m) - user setting" % (res_m / FT_US, res_m))
        res = res_m / upm
        buf = field_out.buffer(buf_m / upm, 8)

        # ---- PDAL pipeline ----
        stages = list(pts["readers"])
        if len(pts["readers"]) > 1:
            stages.append({"type": "filters.merge",
                           "inputs": [r["tag"] for r in pts["readers"]]})
        stages.append({"type": "filters.range",
                       "limits": ",".join("Classification[%d:%d]" % (k, k) for k in classes)})
        if pts["src_crs"].authid() != out_crs.authid() or not out_crs.authid():
            out_s = srs_string(out_crs)
            if not out_s:
                raise QgsProcessingException(
                    "Could not describe the output CRS for PDAL; "
                    "set 'Output CRS' to an EPSG code (e.g. EPSG:3435).")
            # no in_srs: PDAL uses the CRS stored with the points (or default_srs)
            stages.append({"type": "filters.reprojection", "out_srs": out_s})
        stages.append({"type": "filters.crop", "polygon": buf.asWkt(3)})
        zf = pts["z_unit_m"] / z_target_m
        if abs(zf - 1.0) > 1e-9:
            stages.append({"type": "filters.assign", "value": "Z = Z * %.12f" % zf})
        stages[-1]["tag"] = "ground"

        if keep_laz:
            laz_path = os.path.splitext(out_path)[0] + "_ground.laz"
            wl = {"type": "writers.las", "inputs": ["ground"],
                  "filename": laz_path, "compression": "true",
                  "minor_version": 4, "forward": "all"}
            if srs_string(out_crs):
                wl["a_srs"] = srs_string(out_crs)
            stages.append(wl)
            feedback.pushInfo("Ground points will be saved to %s" % laz_path)

        bb = buf.boundingBox()
        ox = math.floor(bb.xMinimum() / res) * res
        oy = math.floor(bb.yMinimum() / res) * res
        w = int(math.ceil((bb.xMaximum() - ox) / res))
        h = int(math.ceil((bb.yMaximum() - oy) / res))
        if w * h > 60000000:
            raise QgsProcessingException(
                "DEM would be %d x %d cells; use a coarser cell size." % (w, h))
        raw_dem = os.path.join(work, "dem_buffered.tif")
        stages += [
            {"type": "filters.delaunay", "inputs": ["ground"]},
            {"type": "filters.faceraster", "resolution": res, "nodata": -9999,
             "origin_x": ox, "origin_y": oy, "width": w, "height": h},
            {"type": "writers.raster", "filename": raw_dem, "data_type": "float32",
             "nodata": -9999, "gdalopts": "COMPRESS=DEFLATE,TILED=YES"},
        ]
        feedback.pushInfo("Gridding %d x %d cells at %.3f %s ..."
                          % (w, h, res, QgsUnitTypes.toString(out_crs.mapUnits())))
        feedback.setProgressText("Running PDAL")
        run_pdal(pdal_exe, {"pipeline": stages}, work, feedback)
        if not os.path.exists(raw_dem):
            raise QgsProcessingException(
                "PDAL produced no DEM. Possibly no ground points (class %s) fell inside "
                "the field - check the classes or try another year." % classes)

        # ---- clip to exact field ----
        feedback.setProgressText("Clipping DEM to field boundary")
        mask = self._write_mask(field_out, out_crs, work, context)
        self._clip(raw_dem, mask, out_crs, out_path, context, feedback)
        results = {self.OUTPUT: out_path}

        # ---- optional: sinks filled (Wang & Liu) ----
        if do_fill:
            feedback.setProgressText("Filling sinks (Wang & Liu)")
            filled_path = self.parameterAsOutputLayer(parameters, self.FILLED, context)
            auto_path = not filled_path
            if auto_path:
                base, ext = os.path.splitext(out_path)
                filled_path = base + "_filled" + (ext or ".tif")
            filled_raw = os.path.join(work, "dem_buffered_filled.tif")
            z_per_xy = upm / z_target_m      # elevation units per horizontal unit
            self._fill_raster(raw_dem, filled_raw, res, min_slope, z_per_xy, feedback,
                              xy_unit_m=upm, z_unit_m=z_target_m)
            self._clip(filled_raw, mask, out_crs, filled_path, context, feedback)
            results[self.FILLED] = filled_path
            if auto_path:
                details = QgsProcessingContext.LayerDetails(
                    "Filled DEM (Wang & Liu)", context.project(), self.FILLED)
                context.addLayerToLoadOnCompletion(filled_path, details)
            feedback.pushInfo("Filled DEM: %s" % filled_path)

        feedback.pushInfo("Done. Source: %s" % pts["label"])
        return results

    @staticmethod
    def _clip(src, mask, crs, dest, context, feedback):
        processing.run("gdal:cliprasterbymasklayer", {
            "INPUT": src, "MASK": mask,
            "SOURCE_CRS": crs, "TARGET_CRS": crs,
            "NODATA": -9999, "ALPHA_BAND": False, "CROP_TO_CUTLINE": True,
            "KEEP_RESOLUTION": True, "OPTIONS": "COMPRESS=DEFLATE|TILED=YES",
            "DATA_TYPE": 6, "OUTPUT": dest,
        }, context=context, feedback=feedback, is_child_algorithm=True)

    @staticmethod
    def _fill_raster(src, dest, cell, min_slope, z_per_xy, feedback, xy_unit_m=FT_US,
                     z_unit_m=FT_US):
        """Fill sinks in the buffered DEM (its outer edge and NoData act as outlets)."""
        import numpy as np
        from osgeo import gdal
        from .sinkfill import fill_sinks_wang_liu

        ds = gdal.Open(src)
        band = ds.GetRasterBand(1)
        nd = band.GetNoDataValue()
        z = band.ReadAsArray().astype(np.float64)
        mask = ~np.isfinite(z)
        if nd is not None:
            mask |= (z == nd)
        z[mask] = -9999.0
        feedback.pushInfo("Filling sinks (Wang & Liu, minimum slope %g deg) over %d cells ..."
                          % (min_slope, int((~mask).sum())))
        filled = fill_sinks_wang_liu(
            z, mask, cell, min_slope_deg=min_slope, z_per_xy=z_per_xy,
            progress=lambda f: feedback.setProgress(80 + int(18 * f)),
            is_canceled=feedback.isCanceled)
        depth = np.where(mask, 0.0, filled - z)
        raised = depth > 1e-6
        if raised.any():
            vol_m3 = float(depth.sum()) * z_unit_m * (cell * xy_unit_m) ** 2
            zu = "ft" if abs(z_unit_m - FT_US) < 1e-6 else "m"
            feedback.pushInfo("  %d cells raised; deepest fill %.2f %s; filled volume "
                              "%.0f cu yd (%.0f m3)"
                              % (int(raised.sum()), float(depth.max()), zu,
                                 vol_m3 / 0.764554857984, vol_m3))
        else:
            feedback.pushInfo("  no sinks found")
        filled[mask] = -9999.0
        drv = gdal.GetDriverByName("GTiff")
        out = drv.Create(dest, ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Float32,
                         ["COMPRESS=DEFLATE", "TILED=YES"])
        out.SetGeoTransform(ds.GetGeoTransform())
        out.SetProjection(ds.GetProjection())
        ob = out.GetRasterBand(1)
        ob.SetNoDataValue(-9999.0)
        ob.WriteArray(filled.astype(np.float32))
        ob.FlushCache()
        out = None
        ds = None

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_classes(text):
        out = []
        for t in re.split(r"[,\s]+", text or ""):
            if t.strip().isdigit():
                out.append(int(t))
        return out or [2]

    @staticmethod
    def _write_mask(geom, crs, work, context):
        path = os.path.join(work, "field_mask.gpkg")
        lyr = QgsVectorLayer("Polygon?crs=%s" % crs.authid() if crs.authid() else "Polygon",
                             "mask", "memory")
        if not crs.authid():
            lyr.setCrs(crs)
        f = QgsFeature()
        f.setGeometry(geom)
        lyr.dataProvider().addFeatures([f])
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        res = QgsVectorFileWriter.writeAsVectorFormatV2(
            lyr, path, context.transformContext(), opts)
        if res[0] != QgsVectorFileWriter.NoError:
            raise QgsProcessingException("Could not write the field mask: %s" % (res,))
        return path

    # ------------------------------------------------------------------
    # ISGS
    # ------------------------------------------------------------------
    def _isgs(self, field, field_crs, field_wgs, year_req, buf_m, context, feedback):
        feedback.setProgressText("Querying ISGS LiDAR collections")
        lon_c = field_wgs.centroid().asPoint().x()
        bb = field_wgs.boundingBox()
        q = ("?geometry=%f,%f,%f,%f&geometryType=esriGeometryEnvelope&inSR=4326"
             "&spatialRel=esriSpatialRelIntersects"
             "&outFields=CollectionName,CollectionYear,Status,TileIndex,LAS,DTMImageService"
             "&returnGeometry=false&f=json"
             % (bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum()))
        _, _, data = http_get(ISGS_COLLECTIONS_URL + q, feedback=feedback)
        js = json.loads(data.decode("utf-8"))
        if "error" in js:
            raise QgsProcessingException("ISGS service error: %s" % js["error"])
        cols = []
        for ft in js.get("features", []):
            a = ft.get("attributes", {})
            if not a.get("LAS") or not a.get("TileIndex"):
                continue
            a["_year"] = year_of(a.get("CollectionYear"))
            cols.append(a)
            feedback.pushInfo("  found: %s %s  [%s]" % (
                a.get("CollectionName"), a.get("CollectionYear"), a.get("Status")))
        if not cols:
            raise QgsProcessingException(
                "No ISGS LiDAR collections with LAS downloads cover this field. "
                "Is it in Illinois? Try the USGS 3DEP source.")

        if year_req:
            cols = [c for c in cols if year_req in str(c.get("CollectionYear"))]
            if not cols:
                raise QgsProcessingException("No ISGS collection for year %s here." % year_req)
        years = sorted({c["_year"] for c in cols}, reverse=True)

        # buffered field in WGS84 (approximate, for tile selection)
        tile_dir = None
        for yr in years:
            group = [c for c in cols if c["_year"] == yr]
            readers, index_crs, z_m, used = [], None, None, []
            src_epsg, out_zone = None, None
            native_m, native_note, all_files, unit_for_files = None, None, [], None
            covered = None
            for col in group:
                feedback.pushInfo("Using %s %s" % (col["CollectionName"], col["CollectionYear"]))
                idx_zip = os.path.join(cache_dir("isgs_index"),
                                       safe(col["CollectionName"]) + "_" + safe(col["CollectionYear"])
                                       + "_" + safe(os.path.basename(col["TileIndex"])))
                if not os.path.exists(idx_zip):
                    feedback.setProgressText("Downloading tile index")
                    download_to(col["TileIndex"], idx_zip, feedback)
                tiles, crs, zunit, prj = self._tiles_from_index(
                    idx_zip, field, field_crs, buf_m, context, feedback)
                if not tiles:
                    feedback.pushInfo("  no tiles of this collection touch the field")
                    continue
                feedback.pushInfo("  %d tile(s) under the field" % len(tiles))
                tile_dir = cache_dir("isgs_tiles", safe(col["CollectionName"]),
                                     safe(col["CollectionYear"]))
                files = self._fetch_tiles(col["LAS"], tiles, tile_dir, feedback)
                all_files += files
                unit_for_files = unit_for_files or metres_per_unit(crs)
                if native_m is None:
                    native_m = self._isgs_native_res(col, feedback)
                    if native_m:
                        native_note = "native cell size of ISGS %s %s DTM" % (
                            col["CollectionName"], col["CollectionYear"])
                zone = il_zone_from_prj(prj) or il_zone_from_lon(lon_c)
                dsrs = il_source_epsg(prj, zone)
                for fpath in files:
                    rd = {"type": "readers.las", "filename": fpath,
                          "tag": "r%d" % len(readers)}
                    if dsrs:
                        rd["default_srs"] = dsrs     # only used if the LAS has no CRS
                    readers.append(rd)
                index_crs = index_crs or crs
                src_epsg = src_epsg or dsrs
                out_zone = out_zone or zone
                z_m = z_m or zunit
                used.append("%s %s" % (col["CollectionName"], col["CollectionYear"]))
                for t in tiles.values():
                    covered = t if covered is None else covered.combine(t)
            if readers:
                fld = QgsGeometry(field)
                fld.transform(QgsCoordinateTransform(field_crs, index_crs,
                                                     context.transformContext()))
                if covered is not None and not covered.buffer(1, 4).contains(fld):
                    feedback.reportError(
                        "Warning: the %d LiDAR tiles only partly cover the field (it may cross a "
                        "county line with a different collection year). Missing areas "
                        "will be blank or interpolated." % yr, False)
                other = [y for y in years if y != yr]
                if other:
                    feedback.pushInfo("Other years available here: %s" % ", ".join(map(str, other)))
                feedback.pushInfo("Illinois State Plane zone: %s" % out_zone.title())
                if native_m is None:
                    nps = las_point_spacing_m(all_files, unit_for_files or FT_US)
                    if nps:
                        # round up to a tidy value in feet (0.5 ft steps), min 1 ft
                        ft = max(1.0, math.ceil((nps / FT_US) * 2.0) / 2.0)
                        native_m = ft * FT_US
                        native_note = ("estimated from LiDAR point spacing (%.2f ft); "
                                       "no ISGS DTM cell size published" % (nps / FT_US))
                return {"readers": readers,
                        "src_crs": QgsCoordinateReferenceSystem(src_epsg),
                        "default_out_crs": QgsCoordinateReferenceSystem(
                            IL_EAST_OUT if out_zone == "east" else IL_WEST_OUT),
                        "z_unit_m": z_m,
                        "native_res_m": native_m, "native_res_note": native_note,
                        "label": "ISGS " + "; ".join(used)}
        raise QgsProcessingException("No ISGS LiDAR tiles intersect the field.")

    @staticmethod
    def _isgs_native_res(col, feedback):
        """Cell size (m) of the collection's published ISGS DTM image service."""
        svc = (col.get("DTMImageService") or "").strip()
        if not svc:
            return None
        try:
            _, _, data = http_get(svc.rstrip("/") + "?f=json", feedback=feedback, retries=2)
            js = json.loads(data.decode("utf-8"))
            px = float(js.get("pixelSizeX") or 0)
            um = _units_m_from_esri_sr(js.get("spatialReference"))
            if px > 0 and um:
                return px * um
        except QgsProcessingException:
            raise
        except Exception as e:  # service missing / unexpected JSON
            feedback.pushInfo("  (could not read ISGS DTM cell size: %s)" % e)
        return None

    @staticmethod
    def _unpack_index(idx_zip):
        """Extract the tile-index zip with Python's zipfile (handles LZMA/bzip2 zips
        that GDAL's /vsizip cannot read) and return the path of the .shp.
        Nested zips are unpacked too."""
        out_dir = os.path.splitext(idx_zip)[0] + "_unz"
        marker = os.path.join(out_dir, ".done")
        if not os.path.exists(marker):
            if os.path.isdir(out_dir):
                shutil.rmtree(out_dir, ignore_errors=True)
            os.makedirs(out_dir, exist_ok=True)
            pending = [idx_zip]
            while pending:
                zp = pending.pop()
                try:
                    with zipfile.ZipFile(zp) as z:
                        for info in z.infolist():
                            if info.is_dir():
                                continue
                            name = os.path.normpath(info.filename).lstrip("\\/")
                            if name.startswith(".."):
                                continue          # never write outside out_dir
                            dest = os.path.join(out_dir, name)
                            os.makedirs(os.path.dirname(dest), exist_ok=True)
                            with z.open(info) as src, open(dest, "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            if dest.lower().endswith(".zip"):
                                pending.append(dest)
                except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as e:
                    raise QgsProcessingException(
                        "Could not unpack tile index %s: %s" % (zp, e))
            open(marker, "w").close()
        shps = []
        for root, _, files in os.walk(out_dir):
            shps += [os.path.join(root, f) for f in files if f.lower().endswith(".shp")]
        if not shps:
            raise QgsProcessingException("No shapefile in tile index %s" % idx_zip)
        shps.sort(key=lambda p: ("tile" not in os.path.basename(p).lower(), len(p)))
        return shps[0]

    def _tiles_from_index(self, idx_zip, field, field_crs, buf_m, context, feedback):
        shp_path = self._unpack_index(idx_zip)
        prj_path = os.path.splitext(shp_path)[0] + ".prj"
        prj = ""
        if os.path.exists(prj_path):
            with open(prj_path, "rb") as f:
                prj = f.read().decode("latin-1")
        lyr = QgsVectorLayer(shp_path, "idx", "ogr")
        if not lyr.isValid():
            raise QgsProcessingException("Could not open tile index %s" % shp_path)
        crs = lyr.crs()
        if not crs.isValid() and prj:
            crs = QgsCoordinateReferenceSystem.fromWkt(prj)
        if not crs.isValid():
            raise QgsProcessingException("Tile index has no usable coordinate system.")
        g = QgsGeometry(field)
        g.transform(QgsCoordinateTransform(field_crs, crs, context.transformContext()))
        g = g.buffer(buf_m / metres_per_unit(crs), 8)
        zunit = vertical_unit_from_prj(prj, metres_per_unit(crs))
        str_fields = [f.name() for f in lyr.fields() if f.type() == QVariant.String]
        tiles = {}
        req = QgsFeatureRequest().setFilterRect(g.boundingBox())
        for ft in lyr.getFeatures(req):
            if not ft.geometry().intersects(g):
                continue
            keys = [str(ft[n]).strip() for n in str_fields if ft[n] not in (None, "")]
            tiles[tuple(keys)] = ft.geometry()
        return tiles, crs, zunit, prj

    def _fetch_tiles(self, las_zip_url, tiles, tile_dir, feedback):
        feedback.setProgressText("Reading LAS zip directory (remote)")
        rf = HttpRangeFile(las_zip_url, feedback)
        feedback.pushInfo("  LAS zip is %.1f GB; extracting only the needed tiles"
                          % (rf.size / 1e9))
        zf = zipfile.ZipFile(rf)
        members = {}
        for info in zf.infolist():
            base, ext = os.path.splitext(os.path.basename(info.filename))
            if ext.lower() in (".las", ".laz", ".zip") and base:
                members.setdefault(base.lower(), info)

        wanted = []
        for keys in tiles:
            hit = None
            for k in keys:
                kk = os.path.splitext(os.path.basename(k))[0].lower()
                if kk in members:
                    hit = members[kk]
                    break
            if hit is None:
                # looser: a member whose name contains the tile label
                for k in keys:
                    kk = k.lower()
                    if len(kk) >= 4:
                        cand = [i for b, i in members.items() if kk in b]
                        if len(cand) == 1:
                            hit = cand[0]
                            break
            if hit is None:
                feedback.pushInfo("  (no LAS file found for tile %s)" % "/".join(keys))
            elif hit not in wanted:
                wanted.append(hit)
        if not wanted:
            sample_t = ", ".join("/".join(k) for k in list(tiles)[:3])
            sample_m = ", ".join(list(members)[:3])
            raise QgsProcessingException(
                "Could not match tile-index names to files in the LAS zip.\n"
                "Index labels: %s\nZip files: %s" % (sample_t, sample_m))

        files = []
        total_mb = sum(i.compress_size for i in wanted) / 1e6
        feedback.pushInfo("  downloading %d tile(s), ~%.0f MB compressed" % (len(wanted), total_mb))
        for n, info in enumerate(wanted):
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled.")
            dest = os.path.join(tile_dir, os.path.basename(info.filename))
            if not os.path.exists(dest):
                feedback.setProgressText("Tile %d/%d: %s" % (n + 1, len(wanted),
                                                            os.path.basename(info.filename)))
                extract_member(zf, info, dest, feedback)
            else:
                feedback.pushInfo("  cached: %s" % dest)
            if dest.lower().endswith(".zip"):          # tile zipped inside the county zip
                with zipfile.ZipFile(dest) as inner:
                    for m in inner.infolist():
                        if m.filename.lower().endswith((".las", ".laz")):
                            p = os.path.join(tile_dir, os.path.basename(m.filename))
                            if not os.path.exists(p):
                                extract_member(inner, m, p, feedback)
                            files.append(p)
            else:
                files.append(dest)
            feedback.setProgress(int(80 * (n + 1) / len(wanted)))
        return files

    # ------------------------------------------------------------------
    # USGS 3DEP (AWS EPT)
    # ------------------------------------------------------------------
    @staticmethod
    def _isgs_covers(field_wgs, feedback):
        """True/False if any ISGS LiDAR collection intersects the field; None if the
        service can't be reached."""
        bb = field_wgs.boundingBox()
        if not (-92.0 <= bb.xMinimum() and bb.xMaximum() <= -87.0
                and 36.5 <= bb.yMinimum() and bb.yMaximum() <= 43.0):
            return False                       # nowhere near Illinois
        hull = field_wgs.convexHull().simplify(0.00005)   # short URL, ~5 m tolerance
        q = ("?geometry=%s&geometryType=esriGeometryPolygon&inSR=4326"
             "&spatialRel=esriSpatialRelIntersects&returnCountOnly=true&f=json"
             % QUrl.toPercentEncoding(json.dumps(
                 {"rings": [[[round(p.x(), 6), round(p.y(), 6)] for p in ring]
                            for ring in hull.asPolygon()],
                  "spatialReference": {"wkid": 4326}})).data().decode())
        try:
            _, _, data = http_get(ISGS_COLLECTIONS_URL + q, feedback=feedback, retries=2)
            js = json.loads(data.decode("utf-8"))
            if "count" in js:
                return int(js["count"]) > 0
        except Exception as e:
            if feedback.isCanceled():
                raise
            feedback.pushInfo("(ISGS coverage check failed: %s)" % e)
        return None

    def _usgs(self, field, field_crs, field_wgs, year_req, buf_m, context, feedback,
              in_il=None):
        feedback.setProgressText("Finding USGS 3DEP projects")
        idx = os.path.join(cache_dir("usgs"), "resources.geojson")
        if not os.path.exists(idx) or time.time() - os.path.getmtime(idx) > 30 * 86400:
            feedback.pushInfo("Downloading 3DEP project index (~9 MB) ...")
            download_to(USGS_EPT_INDEX_URL, idx, feedback)
        lyr = QgsVectorLayer(idx, "3dep", "ogr")
        if not lyr.isValid():
            raise QgsProcessingException("Could not read the 3DEP index %s" % idx)
        g = QgsGeometry(field_wgs)
        if lyr.crs().isValid() and lyr.crs() != WGS84:
            g.transform(QgsCoordinateTransform(WGS84, lyr.crs(), context.transformContext()))
        names = [f.name() for f in lyr.fields()]
        hits = []
        for ft in lyr.getFeatures(QgsFeatureRequest().setFilterRect(g.boundingBox())):
            if not ft.geometry().intersects(g):
                continue
            nm = ft["name"] if "name" in names else ""
            url = ft["url"] if "url" in names and ft["url"] else USGS_EPT_BASE + nm + "/ept.json"
            hits.append({"name": nm, "url": url, "year": year_of(nm)})
            feedback.pushInfo("  found: %s" % nm)
        if not hits:
            raise QgsProcessingException("No USGS 3DEP point clouds cover this field.")
        if year_req:
            hits = [h for h in hits if year_req in h["name"]]
            if not hits:
                raise QgsProcessingException("No 3DEP project for year %s here." % year_req)
        best = max(h["year"] for h in hits)
        use = [h for h in hits if h["year"] == best]
        others = sorted({h["year"] for h in hits if h["year"] != best}, reverse=True)
        if others:
            feedback.pushInfo("Other years available here: %s" % ", ".join(map(str, others)))

        # bounds in EPSG:3857 (EPT native), buffered
        utm = utm_crs_for(field_wgs.centroid().asPoint().x(), field_wgs.centroid().asPoint().y())
        gb = QgsGeometry(field)
        gb.transform(QgsCoordinateTransform(field_crs, utm, context.transformContext()))
        gb = gb.buffer(buf_m + 5.0, 8)
        gb.transform(QgsCoordinateTransform(utm, WEB_MERCATOR, context.transformContext()))
        r = gb.boundingBox()
        bounds = "([%.2f, %.2f], [%.2f, %.2f])" % (r.xMinimum(), r.xMaximum(),
                                                   r.yMinimum(), r.yMaximum())
        readers = []
        for h in use:
            feedback.pushInfo("Using 3DEP project %s" % h["name"])
            readers.append({"type": "readers.ept", "filename": h["url"],
                            "bounds": bounds, "tag": "r%d" % len(readers)})
        cpt = field_wgs.centroid().asPoint()
        if (in_il if in_il is not None else in_illinois(cpt.x(), cpt.y())):
            default_out = QgsCoordinateReferenceSystem(
                IL_EAST_OUT if il_zone_from_lon(cpt.x()) == "east" else IL_WEST_OUT)
        else:
            default_out = utm
        return {"readers": readers, "src_crs": WEB_MERCATOR, "default_out_crs": default_out,
                "z_unit_m": 1.0, "native_res_m": USGS_3DEP_NATIVE_M,
                "native_res_note": "USGS 3DEP standard 1 m DEM resolution",
                "label": "USGS 3DEP " + "; ".join(h["name"] for h in use)}
