# -*- coding: utf-8 -*-
"""
Drainage Outlet - Processing algorithm.

Given drainage-system outlet point(s) and the StreamStats watershed that
contains them, trace the D8
steepest-descent flow path from that outlet to the first (nearest
downstream) stream.
"""
import math
import os
import tempfile

import numpy as np
from osgeo import gdal, ogr, osr

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsLineString,
    QgsLineSymbol,
    QgsMarkerLineSymbolLayer,
    QgsMarkerSymbol,
    QgsPoint,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon

from . import hydro

# --------------------------------------------------------------------------
# Small compatibility layer (QGIS 3.16 ... 3.40+)
# --------------------------------------------------------------------------
_PST = getattr(Qgis, "ProcessingSourceType", None)
T_POLY = _PST.VectorPolygon if _PST else QgsProcessing.TypeVectorPolygon
T_LINE = _PST.VectorLine if _PST else QgsProcessing.TypeVectorLine
T_POINT = _PST.VectorPoint if _PST else QgsProcessing.TypeVectorPoint

_PNT = getattr(Qgis, "ProcessingNumberParameterType", None)
NUM_DOUBLE = _PNT.Double if _PNT else QgsProcessingParameterNumber.Double
NUM_INT = _PNT.Integer if _PNT else QgsProcessingParameterNumber.Integer

_WKB = getattr(Qgis, "WkbType", None)
if _WKB is not None:
    WKB_LINE, WKB_POINT = _WKB.LineString, _WKB.Point
else:
    from qgis.core import QgsWkbTypes
    WKB_LINE, WKB_POINT = QgsWkbTypes.LineString, QgsWkbTypes.Point

if Qgis.QGIS_VERSION_INT >= 33800:
    from qgis.PyQt.QtCore import QMetaType
    F_INT, F_DBL, F_STR = QMetaType.Type.Int, QMetaType.Type.Double, QMetaType.Type.QString
else:
    from qgis.PyQt.QtCore import QVariant
    F_INT, F_DBL, F_STR = QVariant.Int, QVariant.Double, QVariant.String


def _to_metres_factor(crs):
    """Factor converting the CRS map unit to metres (1.0 for metric CRS)."""
    try:
        meters = Qgis.DistanceUnit.Meters
    except AttributeError:
        meters = QgsUnitTypes.DistanceMeters
    try:
        return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), meters)
    except Exception:  # pragma: no cover
        return 1.0


def _ogr_mem_datasource():
    drv = ogr.GetDriverByName("Memory") or ogr.GetDriverByName("MEM")
    return drv.CreateDataSource("mem")


def _rasterize(geoms, values, shape, gt, wkt, all_touched=False):
    """Burn QgsGeometry list into an int32 numpy array (0 = background)."""
    nrows, ncols = shape
    ds = gdal.GetDriverByName("MEM").Create("", ncols, nrows, 1, gdal.GDT_Int32)
    ds.SetGeoTransform(gt)
    ds.SetProjection(wkt)
    ds.GetRasterBand(1).Fill(0)
    src = _ogr_mem_datasource()
    srs = osr.SpatialReference()
    srs.ImportFromWkt(wkt)
    lyr = src.CreateLayer("l", srs=srs)
    lyr.CreateField(ogr.FieldDefn("v", ogr.OFTInteger))
    for g, v in zip(geoms, values):
        if g is None or g.isEmpty():
            continue
        og = ogr.CreateGeometryFromWkb(bytes(g.asWkb()))
        if og is None:
            continue
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetGeometry(og)
        f.SetField("v", int(v))
        lyr.CreateFeature(f)
    opts = ["ATTRIBUTE=v"]
    if all_touched:
        opts.append("ALL_TOUCHED=TRUE")
    gdal.RasterizeLayer(ds, [1], lyr, options=opts)
    return ds.GetRasterBand(1).ReadAsArray()


def _utm_crs_for(geom, crs, context):
    """Pick the WGS84 UTM zone CRS for the centroid of ``geom``."""
    wgs = QgsCoordinateReferenceSystem("EPSG:4326")
    ct = QgsCoordinateTransform(crs, wgs, context.transformContext())
    c = ct.transform(geom.centroid().asPoint())
    zone = int((c.x() + 180.0) // 6.0) + 1
    epsg = (32600 if c.y() >= 0 else 32700) + zone
    return QgsCoordinateReferenceSystem("EPSG:{}".format(epsg)), epsg


class _PathStyler(QgsProcessingLayerPostProcessorInterface):
    """Give a flow-path output a coloured line with direction arrows."""
    instances = []   # keep post-processors alive until QGIS has used them

    def __init__(self, color="31,120,180"):
        super().__init__()
        self.color = color

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsVectorLayer):
            return
        sym = QgsLineSymbol.createSimple({"color": self.color, "width": "0.9"})
        arrow = QgsMarkerSymbol.createSimple(
            {"name": "filled_arrowhead", "color": self.color, "size": "3",
             "outline_style": "no"})
        ml = QgsMarkerLineSymbolLayer()
        ml.setInterval(15)
        ml.setSubSymbol(arrow)
        sym.appendSymbolLayer(ml)
        layer.setRenderer(QgsSingleSymbolRenderer(sym))
        layer.triggerRepaint()

    @staticmethod
    def create(color="31,120,180"):
        inst = _PathStyler(color)
        _PathStyler.instances = _PathStyler.instances[-7:] + [inst]
        return inst


def _name_field(fields, candidates):
    """First field name from ``candidates`` present in ``fields`` (or None)."""
    for cand in candidates:
        if fields.indexFromName(cand) >= 0:
            return cand
    return None


STREAM_NAME_FIELDS = ("GNIS_NAME", "gnis_name", "gnisname", "NAME", "name", "Name")
DRAIN_NAME_FIELDS = ("DRAIN_NAME", "drain_name", "DrainName", "Drain_Name", "DRAIN_NM",
                     "drain_nm", "DRAIN", "Drain", "drain", "NAME", "name", "Name",
                     "GNIS_NAME", "gnis_name")


# --------------------------------------------------------------------------
class DrainagePathAlgorithm(QgsProcessingAlgorithm):
    FIELD = "FIELD"
    SECONDARY = "SECONDARY"
    POINTS = "POINTS"
    OUTLET_POINT = "OUTLET_POINT"
    WATERSHED = "WATERSHED"
    DEM = "DEM"
    DOWNLOAD_DEM = "DOWNLOAD_DEM"
    RESOLUTION = "RESOLUTION"
    MAX_CELLS = "MAX_CELLS"
    STREAMS = "STREAMS"
    SNAP = "SNAP"
    USE_DERIVED = "USE_DERIVED"
    THRESHOLD = "THRESHOLD"
    UNITS = "UNITS"
    DEM_Z_UNITS = "DEM_Z_UNITS"
    POINT_SNAP = "POINT_SNAP"
    DRAINS = "DRAINS"
    DRAIN_SNAP = "DRAIN_SNAP"
    DRAIN_ROUTE = "DRAIN_ROUTE"
    OUTPUT_DRAIN_PATHS = "OUTPUT_DRAIN_PATHS"
    OUTPUT_PATHS = "OUTPUT_PATHS"
    OUTPUT_STREAMS = "OUTPUT_STREAMS"
    OUTPUT_DEM = "OUTPUT_DEM"

    def tr(self, s):
        return QCoreApplication.translate("DrainageOutlet", s)

    def createInstance(self):
        return DrainagePathAlgorithm()

    def name(self):
        return "drainagepath"

    def displayName(self):
        return self.tr("Drainage outlet: outlet point to nearest stream")

    def group(self):
        return ""

    def groupId(self):
        return ""

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def shortHelpString(self):
        return self.tr(
            "<p>Traces the natural downhill path water takes from a <b>drainage system "
            "outlet point</b> (tile-drain outlet, culvert, ditch end...) to the first stream "
            "it reaches, inside the <b>StreamStats watershed</b> that contains the point.</p>"
            "<p><b>Method</b></p><ol>"
            "<li>The DEM is clipped to the watershed and reprojected to a projected CRS.</li>"
            "<li>Depressions are filled (priority-flood with a gradient across flats) so "
            "water can always move downhill.</li>"
            "<li>D8 steepest-descent flow directions are computed.</li>"
            "<li>From each outlet point the path is followed downhill until it touches a "
            "stream cell (mapped streams within the snap distance, and/or channels derived "
            "from flow accumulation).</li></ol>"
            "<p><b>Field boundary mode</b>: give a field polygon instead of a point. The "
            "plugin routes every cell of the field, finds the spot on the boundary where most "
            "of the field's runoff leaves for good (the natural outlet) and traces from there. "
            "Water that leaves a concave field and flows back in is followed through, and "
            "streams inside the field (or stream buffers overlapping its edge) are ignored "
            "until the water has left the field. "
            "Secondary outlets can be traced too; <i>share_pct</i> is the share of the "
            "field draining through each.</p>"
            "<p><b>Outlet snap</b>: a GPS point on a ditch bank or road shoulder may sit just "
            "off the real flow line. A snap distance moves the start to the cell with the "
            "largest drainage area within that radius.</p>"
            "<p><b>DEM</b>: supply one (e.g. 3DEP 1/3 arc-second or lidar), or tick "
            "<i>Download DEM from USGS 3DEP</i> (US only, needs internet).</p>"
            "<p><b>Streams</b>: optional line layer such as NHD / NHDPlus HR flowlines. "
            "If omitted, channels are derived from the DEM using the area threshold.</p>"
            "<p><b>Start from</b> any mix of: field boundary polygon(s), a point layer, or a "
            "point picked on the map. At least one is required.</p>"
            "<p><b>Outlets</b>: use a point layer (tick 'Selected features only' to run on "
            "chosen points), and/or click the '...' button next to <i>Outlet point picked on "
            "the map</i> and click the outlet on the map canvas. For fields, select them on "
            "the map and tick 'Selected features only'. "
            "Output keeps each layer point's attributes.</p>"
            "<p><b>Units</b>: choose US customary (feet, acres, mi² - matching StreamStats) "
            "or metric. Cell size, snap distance and channel area are entered in the chosen "
            "units, and all lengths, elevations and areas in the outputs use them. Set the "
            "DEM's elevation unit separately (many state lidar DEMs are in US survey feet). "
            "Any horizontal CRS works, including State Plane in feet.</p>"
            "<p><b>District drain</b> (optional): give the district / legal drain as a line "
            "layer and a second output, <i>Paths to district drain</i>, is made for every "
            "start point. The path follows natural downhill flow (streams do not stop it) "
            "until it touches the drain. If the natural flow never reaches the drain (it "
            "leaves the watershed first) and <i>route over higher ground</i> is ticked, the "
            "path is instead routed along the lowest ground to the nearest point on the drain, "
            "avoiding climbs where it can; <i>climb</i> and <i>max_cut</i> show how much high "
            "ground the route crosses. <i>end_type</i> says which happened. The stream "
            "paths are made as before.</p>")

    UNIT_OPTIONS = ["US customary (feet, acres, mi²)", "Metric (metres, hectares, km²)"]
    Z_OPTIONS = ["Feet (international)", "US survey feet", "Metres"]

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterEnum(
            self.UNITS, self.tr("Units for inputs and results"),
            options=self.UNIT_OPTIONS, defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.FIELD, self.tr("Field boundary polygon(s) - find the outlet automatically [optional]"),
            [T_POLY], optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.SECONDARY,
            self.tr("Field mode: also trace secondary outlets draining at least this % of the field (0 = main outlet only)"),
            type=NUM_DOUBLE, defaultValue=0.0, minValue=0.0, maxValue=100.0))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.POINTS, self.tr("Outlet point layer [optional]"),
            [T_POINT], optional=True))
        self.addParameter(QgsProcessingParameterPoint(
            self.OUTLET_POINT, self.tr("Outlet point picked on the map [optional]"),
            optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.WATERSHED, self.tr("StreamStats watershed (polygon)"), [T_POLY]))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, self.tr("DEM (elevation raster)"), optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.DEM_Z_UNITS, self.tr("Elevation units of the DEM (3DEP downloads are always metres)"),
            options=self.Z_OPTIONS, defaultValue=2))
        self.addParameter(QgsProcessingParameterBoolean(
            self.DOWNLOAD_DEM, self.tr("Download DEM from USGS 3DEP (ignore DEM above)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterNumber(
            self.RESOLUTION, self.tr("Cell size, ft or m (0 = DEM native; ~33 ft / 10 m for 3DEP download)"),
            type=NUM_DOUBLE, defaultValue=0.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_CELLS, self.tr("Maximum grid cells (DEM is coarsened above this)"),
            type=NUM_INT, defaultValue=6000000, minValue=10000))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.STREAMS, self.tr("Stream lines (e.g. NHD flowlines) [optional]"),
            [T_LINE], optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.SNAP, self.tr("Stream snap distance, ft or m"),
            type=NUM_DOUBLE, defaultValue=50.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.USE_DERIVED,
            self.tr("Also treat DEM-derived channels as streams (always on if no stream layer)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterNumber(
            self.THRESHOLD, self.tr("Channel initiation drainage area, mi² or km²"),
            type=NUM_DOUBLE, defaultValue=0.2, minValue=0.0001))
        self.addParameter(QgsProcessingParameterNumber(
            self.POINT_SNAP,
            self.tr("Snap outlet to strongest flow line within, ft or m (0 = use point as-is)"),
            type=NUM_DOUBLE, defaultValue=0.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.DRAINS, self.tr("District drain lines - also trace a path to the drain [optional]"),
            [T_LINE], optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.DRAIN_SNAP, self.tr("District drain snap distance, ft or m"),
            type=NUM_DOUBLE, defaultValue=50.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.DRAIN_ROUTE,
            self.tr("If natural flow misses the district drain, route over the lowest ground to it"),
            defaultValue=True))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_PATHS, self.tr("Drainage paths"), T_LINE))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_DRAIN_PATHS,
            self.tr("Paths to district drain (made only when a district drain is given)"),
            T_LINE, optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_DEM,
            self.tr("Downloaded 3DEP DEM (saved only when 'Download DEM' is ticked)"),
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_STREAMS, self.tr("Stream cells used (raster)"),
            optional=True, createByDefault=False))

    # ------------------------------------------------------------------
    @staticmethod
    def _save_dem(src_path, out_path, us, feedback):
        """Save the downloaded 3DEP DEM as GeoTIFF, in feet when US units are chosen."""
        src = gdal.Open(src_path)
        band = src.GetRasterBand(1)
        nodata = band.GetNoDataValue()
        arr = band.ReadAsArray().astype(np.float32)
        ok = np.isfinite(arr) & (arr > -9999)
        if nodata is not None:
            ok &= arr != nodata
        out_nodata = -99999.0
        if us:
            arr = np.where(ok, arr / 0.3048, out_nodata).astype(np.float32)
        else:
            arr = np.where(ok, arr, out_nodata).astype(np.float32)
        drv = gdal.GetDriverByName("GTiff")
        ds = drv.Create(out_path, src.RasterXSize, src.RasterYSize, 1, gdal.GDT_Float32,
                        ["COMPRESS=DEFLATE", "PREDICTOR=3", "TILED=YES", "BIGTIFF=IF_SAFER"])
        ds.SetGeoTransform(src.GetGeoTransform())
        ds.SetProjection(src.GetProjection())
        ob = ds.GetRasterBand(1)
        ob.WriteArray(arr)
        ob.SetNoDataValue(out_nodata)
        ob.SetUnitType("ft" if us else "m")
        ob.SetDescription("USGS 3DEP elevation ({})".format("feet" if us else "metres"))
        ds.SetMetadataItem("SOURCE", "USGS 3DEP Elevation ImageServer")
        ds.SetMetadataItem("ELEVATION_UNITS", "feet" if us else "metres")
        ds = None
        feedback.pushInfo("Downloaded DEM saved to {} (elevations in {}).".format(
            out_path, "feet" if us else "metres"))

    def processAlgorithm(self, parameters, context, feedback):
        pts_src = self.parameterAsSource(parameters, self.POINTS, context)
        fld_src = self.parameterAsSource(parameters, self.FIELD, context)
        secondary = self.parameterAsDouble(parameters, self.SECONDARY, context)
        ws_src = self.parameterAsSource(parameters, self.WATERSHED, context)
        map_pt = None
        if parameters.get(self.OUTLET_POINT) not in (None, ""):
            map_pt = self.parameterAsPoint(parameters, self.OUTLET_POINT, context)
            map_pt_crs = self.parameterAsPointCrs(parameters, self.OUTLET_POINT, context)
            if not map_pt_crs.isValid():
                map_pt_crs = context.project().crs() if context.project() else ws_src.sourceCrs() if ws_src else None
        if ws_src is None:
            raise QgsProcessingException(self.tr("A StreamStats watershed layer is required."))
        if pts_src is None and map_pt is None and fld_src is None:
            raise QgsProcessingException(self.tr(
                "Give a field boundary layer, an outlet point layer, or pick an outlet point on the map."))
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        download = self.parameterAsBool(parameters, self.DOWNLOAD_DEM, context)
        res_m = self.parameterAsDouble(parameters, self.RESOLUTION, context)
        max_cells = self.parameterAsInt(parameters, self.MAX_CELLS, context)
        streams_src = self.parameterAsSource(parameters, self.STREAMS, context)
        snap_m = self.parameterAsDouble(parameters, self.SNAP, context)
        use_derived = self.parameterAsBool(parameters, self.USE_DERIVED, context)
        thr_km2 = self.parameterAsDouble(parameters, self.THRESHOLD, context)
        point_snap = self.parameterAsDouble(parameters, self.POINT_SNAP, context)
        drains_src = self.parameterAsSource(parameters, self.DRAINS, context)
        drain_snap_m = self.parameterAsDouble(parameters, self.DRAIN_SNAP, context)
        drain_route = self.parameterAsBool(parameters, self.DRAIN_ROUTE, context)
        us = self.parameterAsEnum(parameters, self.UNITS, context) == 0
        z_choice = self.parameterAsEnum(parameters, self.DEM_Z_UNITS, context)

        # u = metres per output length unit
        u = 0.3048 if us else 1.0
        len_sfx = "ft" if us else "m"
        res_m = res_m * u
        snap_m = snap_m * u
        point_snap = point_snap * u
        drain_snap_m = drain_snap_m * u
        thr_m2 = thr_km2 * (2589988.110336 if us else 1e6)
        # DEM vertical unit -> metres
        z_to_m = {0: 0.3048, 1: 1200.0 / 3937.0, 2: 1.0}[z_choice]
        if download and z_to_m != 1.0:
            feedback.pushInfo(self.tr("3DEP elevations are in metres; DEM elevation unit setting ignored."))
            z_to_m = 1.0

        if dem_layer is None and not download:
            raise QgsProcessingException(
                self.tr("Provide a DEM or tick 'Download DEM from USGS 3DEP'."))
        if streams_src is None:
            use_derived = True

        # ---- watershed geometry --------------------------------------
        ws_geoms = [f.geometry() for f in ws_src.getFeatures()
                    if f.hasGeometry() and not f.geometry().isEmpty()]
        if not ws_geoms:
            raise QgsProcessingException(self.tr("Watershed layer has no polygons."))
        ws_geom = QgsGeometry.unaryUnion(ws_geoms)
        ws_crs = ws_src.sourceCrs()

        # ---- working CRS ----------------------------------------------
        if dem_layer is not None and not download and not dem_layer.crs().isGeographic():
            work_crs = dem_layer.crs()
        else:
            work_crs, _ = _utm_crs_for(ws_geom, ws_crs, context)
        m_factor = _to_metres_factor(work_crs)        # map unit -> metres
        work_wkt = work_crs.toWkt()
        feedback.pushInfo(self.tr("Working CRS: {}").format(work_crs.authid() or work_crs.description()))

        def to_work(geom, crs):
            g = QgsGeometry(geom)
            if crs != work_crs:
                g.transform(QgsCoordinateTransform(crs, work_crs, context.transformContext()))
            return g

        ws_work = to_work(ws_geom, ws_crs)

        # ---- resolution ----------------------------------------------
        if res_m <= 0:
            if download or dem_layer is None:
                res_m = 10.0 if not us else 10.0584  # 33 ft
            else:
                rx = dem_layer.rasterUnitsPerPixelX()
                if dem_layer.crs().isGeographic():
                    wgs = QgsCoordinateTransform(work_crs, QgsCoordinateReferenceSystem("EPSG:4326"),
                                                 context.transformContext())
                    lat = wgs.transform(ws_work.centroid().asPoint()).y()
                    res_m = rx * 111320.0 * math.cos(math.radians(lat))
                else:
                    res_m = rx * _to_metres_factor(dem_layer.crs())
        res = res_m / m_factor                          # in map units

        bb = ws_work.boundingBox()
        pad = 3 * res
        xmin = math.floor((bb.xMinimum() - pad) / res) * res
        ymin = math.floor((bb.yMinimum() - pad) / res) * res
        xmax = math.ceil((bb.xMaximum() + pad) / res) * res
        ymax = math.ceil((bb.yMaximum() + pad) / res) * res
        ncells = ((xmax - xmin) / res) * ((ymax - ymin) / res)
        if ncells > max_cells:
            res *= math.sqrt(ncells / max_cells)
            feedback.pushWarning(self.tr(
                "Watershed is large: cell size coarsened to {:.1f} {} to stay under {} cells. "
                "Raise 'Maximum grid cells' for more detail (slower).").format(res * m_factor / u, len_sfx, max_cells))
            xmin = math.floor((bb.xMinimum() - pad) / res) * res
            ymin = math.floor((bb.yMinimum() - pad) / res) * res
            xmax = math.ceil((bb.xMaximum() + pad) / res) * res
            ymax = math.ceil((bb.yMaximum() + pad) / res) * res
        feedback.pushInfo(self.tr("Cell size: {:.2f} {}").format(res * m_factor / u, len_sfx))

        # ---- obtain & warp DEM ---------------------------------------
        tmpdir = tempfile.mkdtemp(prefix="drainage_outlet_")
        saved_dem = None
        if download:
            from .dem_download import fetch_3dep
            epsg = int(work_crs.authid().split(":")[1])
            feedback.pushInfo(self.tr("Requesting DEM from USGS 3DEP..."))
            dem_path = fetch_3dep(xmin, ymin, xmax, ymax, epsg, res, tmpdir, feedback)
            saved_dem = None
            if parameters.get(self.OUTPUT_DEM):
                saved_dem = self.parameterAsOutputLayer(parameters, self.OUTPUT_DEM, context)
            if saved_dem:
                self._save_dem(dem_path, saved_dem, us, feedback)
        else:
            if dem_layer.providerType() != "gdal":
                raise QgsProcessingException(self.tr(
                    "DEM must be a file-based raster (GeoTIFF, VRT, IMG...). "
                    "Save the layer to a file first."))
            dem_path = dem_layer.source()

        feedback.setProgress(5)
        warped = gdal.Warp(
            "", dem_path, format="MEM", dstSRS=work_wkt,
            outputBounds=(xmin, ymin, xmax, ymax), xRes=res, yRes=res,
            resampleAlg="bilinear", dstNodata=-99999.0, outputType=gdal.GDT_Float32,
            multithread=True)
        if warped is None:
            raise QgsProcessingException(self.tr("Could not read / reproject the DEM."))
        gt = warped.GetGeoTransform()
        dem = warped.GetRasterBand(1).ReadAsArray().astype(np.float64)
        shape = dem.shape
        nrows, ncols = shape
        cell_area_m2 = abs(gt[1] * gt[5]) * m_factor ** 2

        ws_mask = _rasterize([ws_work], [1], shape, gt, work_wkt, all_touched=True) > 0
        valid = ws_mask & np.isfinite(dem) & (dem > -9999)
        dem = np.where(valid, dem * z_to_m, dem)   # work internally in metres
        if valid.sum() < 10:
            raise QgsProcessingException(self.tr(
                "The DEM does not cover the watershed (no valid elevation cells)."))
        if (ws_mask & ~valid).sum() > 0.02 * ws_mask.sum():
            feedback.pushWarning(self.tr("The DEM leaves gaps inside the watershed; results may be incomplete."))

        # ---- hydrology ------------------------------------------------
        feedback.pushInfo(self.tr("Filling depressions ({:,} cells)...").format(int(valid.sum())))

        def fill_cb(frac):
            feedback.setProgress(10 + 50 * frac)
            return feedback.isCanceled()

        try:
            zfill = hydro.fill_depressions(dem, valid, epsilon=1e-4, feedback=fill_cb)
        except RuntimeError:
            return {}
        feedback.pushInfo(self.tr("Computing flow directions..."))
        rcv = hydro.flow_directions(zfill, valid, cellsize=res * m_factor)
        feedback.setProgress(65)

        # ---- stream cells --------------------------------------------
        stream = np.zeros(shape, dtype=bool)
        stream_id = np.zeros(shape, dtype=np.int32)
        stream_info = {}  # burn value -> (fid, name)
        if streams_src is not None:
            s_crs = streams_src.sourceCrs()
            grid_rect = QgsRectangle(xmin, ymin, xmax, ymax)
            back = QgsCoordinateTransform(work_crs, s_crs, context.transformContext())
            req = QgsFeatureRequest().setFilterRect(back.transformBoundingBox(grid_rect))
            name_field = _name_field(streams_src.fields(), STREAM_NAME_FIELDS)
            geoms, vals = [], []
            for k, f in enumerate(streams_src.getFeatures(req), start=1):
                if not f.hasGeometry():
                    continue
                g = to_work(f.geometry(), s_crs)
                if snap_m > 0:
                    g = g.buffer(snap_m / m_factor, 4)
                geoms.append(g)
                vals.append(k)
                stream_info[k] = (f.id(), str(f[name_field]) if name_field and f[name_field] is not None else "")
            if geoms:
                stream_id = _rasterize(geoms, vals, shape, gt, work_wkt, all_touched=True)
                stream_id[~valid] = 0
                stream = stream_id > 0
            feedback.pushInfo(self.tr("Stream lines in watershed area: {}").format(len(geoms)))
            if not stream.any() and not use_derived:
                feedback.pushWarning(self.tr(
                    "No stream lines fall inside the watershed; switching to DEM-derived channels."))
                use_derived = True

        # ---- district drain cells -------------------------------------
        drain = None
        drain_id = None
        drain_info = {}  # burn value -> (fid, name)
        if drains_src is not None:
            d_crs = drains_src.sourceCrs()
            back = QgsCoordinateTransform(work_crs, d_crs, context.transformContext())
            req = QgsFeatureRequest().setFilterRect(
                back.transformBoundingBox(QgsRectangle(xmin, ymin, xmax, ymax)))
            name_field = _name_field(drains_src.fields(), DRAIN_NAME_FIELDS)
            geoms, vals = [], []
            for k, f in enumerate(drains_src.getFeatures(req), start=1):
                if not f.hasGeometry() or f.geometry().isEmpty():
                    continue
                g = to_work(f.geometry(), d_crs)
                if drain_snap_m > 0:
                    g = g.buffer(drain_snap_m / m_factor, 4)
                geoms.append(g)
                vals.append(k)
                drain_info[k] = (f.id(), str(f[name_field]) if name_field and f[name_field] is not None else "")
            drain_id = np.zeros(shape, dtype=np.int32)
            if geoms:
                drain_id = _rasterize(geoms, vals, shape, gt, work_wkt, all_touched=True)
                drain_id[~valid] = 0
            drain = drain_id > 0
            if drain.any():
                feedback.pushInfo(self.tr("District drain lines in watershed area: {}").format(len(geoms)))
            else:
                feedback.pushWarning(self.tr(
                    "No district drain lines fall inside the watershed; paths to the district drain "
                    "will not reach it. Check the drain layer and the watershed."))

        acc = None
        if use_derived or (point_snap > 0 and (pts_src is not None or map_pt is not None)):
            feedback.pushInfo(self.tr("Computing flow accumulation..."))
            acc = hydro.flow_accumulation(zfill, valid, rcv)
        if use_derived:
            thr_cells = max(1.0, thr_m2 / cell_area_m2)
            stream |= (acc >= thr_cells) & valid
        feedback.setProgress(75)

        if self.OUTPUT_STREAMS in parameters and parameters[self.OUTPUT_STREAMS]:
            out_r = self.parameterAsOutputLayer(parameters, self.OUTPUT_STREAMS, context)
            if out_r:
                drv = gdal.GetDriverByName("GTiff")
                rds = drv.Create(out_r, ncols, nrows, 1, gdal.GDT_Byte, ["COMPRESS=DEFLATE"])
                rds.SetGeoTransform(gt)
                rds.SetProjection(work_wkt)
                band = rds.GetRasterBand(1)
                band.WriteArray(stream.astype(np.uint8))
                band.SetNoDataValue(0)
                rds = None

        # ---- output layer: source attributes + path stats ------------------
        pt_fields = pts_src.fields() if pts_src is not None else QgsFields()
        fl_fields = fld_src.fields() if fld_src is not None else QgsFields()
        p_fields = QgsFields()
        taken = set()

        def add_field(fld, name):
            nf = QgsField(fld)
            nf.setName(name)
            p_fields.append(nf)
            taken.add(name.lower())

        for fld in pt_fields:
            add_field(fld, fld.name())
        for fld in fl_fields:
            name = fld.name()
            if name.lower() in taken:
                name = "fld_" + name
            add_field(fld, name)
        for n, t in (("source", F_STR), ("pt_fid", F_INT), ("field_fid", F_INT),
                     ("outlet_rank", F_INT), ("share_pct", F_DBL),
                     ("length_" + len_sfx, F_DBL),
                     ("start_z_" + len_sfx, F_DBL), ("end_z_" + len_sfx, F_DBL),
                     ("drop_" + len_sfx, F_DBL), ("slope_pct", F_DBL), ("end_type", F_STR),
                     ("stream_fid", F_INT), ("stream_nm", F_STR),
                     ("snapped_" + len_sfx, F_DBL), ("start_x", F_DBL), ("start_y", F_DBL),
                     ("end_x", F_DBL), ("end_y", F_DBL), ("cell_" + len_sfx, F_DBL)):
            name = n if n.lower() not in taken else "dp_" + n
            p_fields.append(QgsField(name, t))
            taken.add(name.lower())

        (p_sink, p_id) = self.parameterAsSink(parameters, self.OUTPUT_PATHS, context,
                                              p_fields, WKB_LINE, work_crs)
        if p_sink is None:
            raise QgsProcessingException(self.tr("Could not create output."))

        def center(i):
            r, c = divmod(int(i), ncols)
            return gt[0] + (c + 0.5) * gt[1], gt[3] + (r + 0.5) * gt[5]

        def field_start(oi, cells, boundary):
            """Start the line exactly where the flow crosses the field boundary
            (outlet cell centre is inside the polygon, the next cell's is outside).
            Returns (first_point, cells_without_the_outlet_cell)."""
            first = center(oi)
            if len(cells) < 2:
                return first, cells
            a, b2 = center(oi), center(cells[1])
            seg = QgsGeometry.fromPolylineXY([QgsPointXY(*a), QgsPointXY(*b2)])
            hit = seg.intersection(boundary)
            verts = [] if (hit is None or hit.isEmpty()) else \
                [QgsPointXY(v.x(), v.y()) for v in hit.vertices()]
            if verts:
                # last crossing along the segment = where water leaves the field
                o = QgsPointXY(*b2)
                bp = min(verts, key=lambda v: v.sqrDist(o))
                first = (bp.x(), bp.y())
            else:
                first = ((a[0] + b2[0]) / 2.0, (a[1] + b2[1]) / 2.0)
            return first, cells[1:]

        sidf = stream_id.ravel()
        validf = valid.ravel()
        snap_cells = int(math.ceil((point_snap / m_factor) / res)) if point_snap > 0 else 0
        n_pt_attr, n_fl_attr = pt_fields.count(), fl_fields.count()

        # Each job: where the path starts and what to write for it.
        jobs = []

        # -- outlet points (layer and/or map click) ---------------------
        pt_items = []
        if pts_src is not None:
            p_crs = pts_src.sourceCrs()
            for f in pts_src.getFeatures():
                if f.hasGeometry():
                    pt_items.append((list(f.attributes()), f.id(), to_work(f.geometry(), p_crs), "layer"))
        if map_pt is not None:
            pt_items.append(([None] * n_pt_attr, None,
                             to_work(QgsGeometry.fromPointXY(map_pt), map_pt_crs), "map"))

        for attrs, fid, g, src_label in pt_items:
            label = "map point" if fid is None else "point {}".format(fid)
            if g.isMultipart():
                parts = g.asMultiPoint()
                if not parts:
                    continue
                px, py = parts[0].x(), parts[0].y()
            else:
                pt = g.asPoint()
                px, py = pt.x(), pt.y()
            c = int(math.floor((px - gt[0]) / gt[1]))
            r = int(math.floor((py - gt[3]) / gt[5]))
            if not (0 <= r < nrows and 0 <= c < ncols) or not validf[r * ncols + c]:
                feedback.pushWarning(self.tr(
                    "Outlet {} is outside the watershed / DEM; skipped.").format(label))
                continue
            start = r * ncols + c
            # optional snap to the strongest flow line nearby (e.g. GPS point on a ditch bank)
            if snap_cells > 0:
                r0, r1 = max(0, r - snap_cells), min(nrows, r + snap_cells + 1)
                c0, c1 = max(0, c - snap_cells), min(ncols, c + snap_cells + 1)
                rr, cc = np.mgrid[r0:r1, c0:c1]
                inside = ((rr - r) ** 2 + (cc - c) ** 2) <= snap_cells ** 2
                win = np.where(inside & valid[r0:r1, c0:c1], acc[r0:r1, c0:c1], -1)
                k = int(np.argmax(win))
                start = (r0 + k // (c1 - c0)) * ncols + (c0 + k % (c1 - c0))
            sx, sy = center(start)
            snapped = math.hypot(sx - px, sy - py) * m_factor if start != r * ncols + c else 0.0
            cells, why = hydro.trace_path(start, rcv, stream)
            jobs.append(dict(
                label=label, attrs=attrs + [None] * n_fl_attr, source=src_label,
                pt_fid=fid, field_fid=None, rank=None, share=None,
                first=(px, py), cells=cells, why=why, z_cell=r * ncols + c,
                snapped=round(snapped / u, 2), start=start, boundary=None, fcells=None))

        # -- field boundaries: find where the field naturally drains ----
        if fld_src is not None:
            f_crs = fld_src.sourceCrs()
            for feat in fld_src.getFeatures():
                if feedback.isCanceled():
                    break
                if not feat.hasGeometry():
                    continue
                label = "field {}".format(feat.id())
                fg = to_work(feat.geometry(), f_crs)
                if not fg.intersects(ws_work):
                    feedback.pushWarning(self.tr("{} is outside the watershed; skipped.").format(label))
                    continue
                if not ws_work.buffer(res, 2).contains(fg):
                    feedback.pushWarning(self.tr(
                        "{} is only partly inside the watershed; only the inside part is routed.").format(label))
                fmask = (_rasterize([fg], [1], shape, gt, work_wkt) > 0) & valid
                if not fmask.any():
                    fmask = (_rasterize([fg], [1], shape, gt, work_wkt, all_touched=True) > 0) & valid
                if not fmask.any():
                    feedback.pushWarning(self.tr("{} has no DEM cells; skipped.").format(label))
                    continue
                n_src = int(fmask.sum())
                if (fmask & stream).any():
                    feedback.pushInfo(self.tr(
                        "{}: stream cells (or the stream snap buffer) overlap the field; they are "
                        "ignored inside the field, and the path starts where water leaves the boundary.").format(label))
                outs = hydro.field_final_outlets(fmask, stream, rcv)
                if not outs:
                    feedback.pushWarning(self.tr("{}: no outlet found.").format(label))
                    continue
                stream_out = stream & ~fmask
                chosen = [outs[0]]
                if secondary > 0:
                    chosen += [o for o in outs[1:] if 100.0 * o[1] / n_src >= secondary]

                if drain is not None and (fmask & drain).any():
                    feedback.pushInfo(self.tr(
                        "{}: the district drain (or its snap buffer) crosses the field; the path "
                        "to the drain still starts at the field's natural outlet.").format(label))
                boundary = QgsGeometry(fg.constGet().boundary())
                fcells = set(np.flatnonzero(fmask.ravel()).tolist()) if drain is not None else None
                for rank, (oi, ncell) in enumerate(chosen, start=1):
                    cells, why = hydro.trace_path(oi, rcv, stream_out)
                    first, cells = field_start(oi, cells, boundary)
                    jobs.append(dict(
                        label="{} outlet {}".format(label, rank),
                        attrs=[None] * n_pt_attr + list(feat.attributes()),
                        source="field boundary", pt_fid=None, field_fid=feat.id(),
                        rank=rank, share=round(100.0 * ncell / n_src, 2),
                        first=first, cells=cells, why=why, z_cell=oi, snapped=None,
                        start=oi, boundary=boundary, fcells=fcells))

        # -- write paths ------------------------------------------------
        demf = dem.ravel()
        njobs = len(jobs) or 1
        n_paths = 0
        for k, jb in enumerate(jobs):
            if feedback.isCanceled():
                break
            feedback.setProgress(80 + (10 if drain is not None else 20) * k / njobs)
            cells, why = jb["cells"], jb["why"]
            pts = [QgsPoint(*jb["first"])] + [QgsPoint(*center(i)) for i in cells]
            if len(pts) == 1:
                pts.append(QgsPoint(*jb["first"]))
            line = QgsGeometry(QgsLineString(pts))
            length = line.length() * m_factor
            z0 = float(demf[jb["z_cell"]])
            end_cell = cells[-1] if cells else jb["z_cell"]
            z1 = float(demf[end_cell])
            drop = z0 - z1
            ex, ey = center(end_cell)
            if why == "stream":
                sid = int(sidf[end_cell])
                sfid, sname = stream_info.get(sid, (None, "derived channel"))
                end_type = "already at stream" if length < res * m_factor else "stream"
            else:
                sfid, sname = None, ""
                end_type = "left watershed (no stream reached)"
                feedback.pushWarning(self.tr(
                    "{}: path left the watershed before reaching a stream. "
                    "Try a larger stream snap distance or enable DEM-derived channels.").format(jb["label"]))

            out = QgsFeature(p_fields)
            out.setGeometry(line)
            out.setAttributes(jb["attrs"] + [
                jb["source"], jb["pt_fid"], jb["field_fid"], jb["rank"], jb["share"],
                round(length / u, 2), round(z0 / u, 3), round(z1 / u, 3), round(drop / u, 3),
                round(100.0 * drop / length, 3) if length > 0 else None,
                end_type, sfid, sname, jb["snapped"], jb["first"][0], jb["first"][1], ex, ey,
                round(res * m_factor / u, 3)])
            p_sink.addFeature(out, QgsFeatureSink.FastInsert)
            n_paths += 1

        feedback.pushInfo(self.tr("Traced {} drainage path(s).").format(n_paths))

        # -- paths to the district drain --------------------------------
        d_id = None
        if drain is not None and jobs and not feedback.isCanceled():
            d_id = self._write_drain_paths(
                parameters, context, feedback, jobs, p_fields, n_pt_attr + n_fl_attr,
                drain, drain_id, drain_info, drain_route, stream, rcv, zfill, valid, dem,
                center, field_start, res, m_factor, u, len_sfx, work_crs)

        if context.willLoadLayerOnCompletion(p_id):
            context.layerToLoadOnCompletionDetails(p_id).setPostProcessor(_PathStyler.create())
        if d_id and context.willLoadLayerOnCompletion(d_id):
            context.layerToLoadOnCompletionDetails(d_id).setPostProcessor(
                _PathStyler.create("227,26,28"))

        result = {self.OUTPUT_PATHS: p_id}
        if d_id:
            result[self.OUTPUT_DRAIN_PATHS] = d_id
        if download and saved_dem:
            result[self.OUTPUT_DEM] = saved_dem
        if self.OUTPUT_STREAMS in parameters and parameters[self.OUTPUT_STREAMS]:
            result[self.OUTPUT_STREAMS] = self.parameterAsOutputLayer(parameters, self.OUTPUT_STREAMS, context)
        return result

    # ------------------------------------------------------------------
    def _write_drain_paths(self, parameters, context, feedback, jobs, p_fields, n_attr,
                           drain, drain_id, drain_info, drain_route, stream, rcv, zfill,
                           valid, dem, center, field_start, res, m_factor, u, len_sfx,
                           work_crs):
        """Second output: a path from every start to the district drain.

        Natural D8 flow is followed (streams do not stop it) until it touches
        the drain. Starts whose natural flow never reaches the drain are, if
        allowed, routed along the lowest ground to the nearest drain cell."""
        d_fields = QgsFields()
        taken = set()
        for k in range(n_attr):
            d_fields.append(QgsField(p_fields.at(k)))
            taken.add(p_fields.at(k).name().lower())
        for n, t in (("source", F_STR), ("pt_fid", F_INT), ("field_fid", F_INT),
                     ("outlet_rank", F_INT), ("share_pct", F_DBL),
                     ("length_" + len_sfx, F_DBL),
                     ("start_z_" + len_sfx, F_DBL), ("end_z_" + len_sfx, F_DBL),
                     ("drop_" + len_sfx, F_DBL), ("slope_pct", F_DBL), ("end_type", F_STR),
                     ("drain_fid", F_INT), ("drain_nm", F_STR), ("via_stream", F_INT),
                     ("climb_" + len_sfx, F_DBL), ("max_cut_" + len_sfx, F_DBL),
                     ("start_x", F_DBL), ("start_y", F_DBL),
                     ("end_x", F_DBL), ("end_y", F_DBL), ("cell_" + len_sfx, F_DBL)):
            name = n if n.lower() not in taken else "dp_" + n
            d_fields.append(QgsField(name, t))
            taken.add(name.lower())

        d_sink, d_id = self.parameterAsSink(parameters, self.OUTPUT_DRAIN_PATHS, context,
                                            d_fields, WKB_LINE, work_crs)
        if d_sink is None:
            feedback.pushInfo(self.tr("'Paths to district drain' output is skipped; not traced."))
            return None

        feedback.pushInfo(self.tr("Tracing paths to the district drain..."))
        # 1) natural downhill flow, stopping only at the drain
        natural = [hydro.trace_path(jb["start"], rcv, drain) for jb in jobs]
        need = [jb["start"] for jb, (_, why) in zip(jobs, natural) if why != "stream"]
        routes = {}
        if need and drain_route and drain.any():
            feedback.pushInfo(self.tr(
                "{} start(s) do not drain naturally to the district drain; routing over the "
                "lowest ground...").format(len(set(need))))

            def route_cb(frac):
                feedback.setProgress(90 + 8 * frac)
                return feedback.isCanceled()

            try:
                routes = hydro.route_to_targets(zfill, valid, drain, need,
                                                cellsize=res * m_factor, feedback=route_cb)
            except RuntimeError:
                return d_id

        demf = dem.ravel()
        drf = drain_id.ravel()
        side = (stream & ~drain).ravel()   # stream cells that are not the drain itself
        cell_m = res * m_factor
        n_out = 0
        for jb, (cells, why) in zip(jobs, natural):
            if feedback.isCanceled():
                break
            start = jb["start"]
            if why == "stream":
                end_type = "already at district drain" if len(cells) == 1 else "district drain"
            elif start in routes:
                cells = routes[start]
                end_type = "routed to district drain (crosses higher ground)"
            else:
                end_type = "district drain not reached (left watershed)"
                feedback.pushWarning(self.tr(
                    "{}: no path to the district drain{}.").format(
                        jb["label"], "" if drain_route else
                        " by natural flow (tick 'route over the lowest ground' to force one)"))

            # path begins at the point, or where the flow leaves the field
            first = jb["first"]
            fc = jb["fcells"]
            if jb["boundary"] is not None:
                if len(cells) > 1 and cells[1] not in fc:
                    first, cells = field_start(start, cells, jb["boundary"])
                else:
                    first = center(start)
            via = 0
            for i in cells[:-1]:
                if side[i] and (fc is None or i not in fc):
                    via = 1
                    break

            pts = [QgsPoint(*first)] + [QgsPoint(*center(i)) for i in cells]
            if len(pts) == 1:
                pts.append(QgsPoint(*first))
            line = QgsGeometry(QgsLineString(pts))
            length = line.length() * m_factor
            z0 = float(demf[jb["z_cell"]])
            end_cell = cells[-1] if cells else start
            z1 = float(demf[end_cell])
            drop = z0 - z1
            ex, ey = center(end_cell)
            prof_z = [z0] + [float(demf[i]) for i in cells]
            prof_d = [0.0]
            for a, b in zip(pts[:-1], pts[1:len(prof_z)]):
                prof_d.append(prof_d[-1] + math.hypot(b.x() - a.x(), b.y() - a.y()) * m_factor)
            climb, max_cut = hydro.path_profile_stats(prof_z, prof_d)
            if end_type.startswith("district drain not"):
                dfid, dname = None, ""
            else:
                dfid, dname = drain_info.get(int(drf[end_cell]), (None, ""))
                if end_type == "district drain" and length < cell_m:
                    end_type = "already at district drain"

            out = QgsFeature(d_fields)
            out.setGeometry(line)
            out.setAttributes(list(jb["attrs"]) + [
                jb["source"], jb["pt_fid"], jb["field_fid"], jb["rank"], jb["share"],
                round(length / u, 2), round(z0 / u, 3), round(z1 / u, 3), round(drop / u, 3),
                round(100.0 * drop / length, 3) if length > 0 else None,
                end_type, dfid, dname, via, round(climb / u, 3), round(max_cut / u, 3),
                first[0], first[1], ex, ey, round(cell_m / u, 3)])
            d_sink.addFeature(out, QgsFeatureSink.FastInsert)
            n_out += 1
        feedback.pushInfo(self.tr("Traced {} path(s) to the district drain.").format(n_out))
        return d_id
