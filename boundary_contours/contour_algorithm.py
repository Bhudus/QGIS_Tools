# -*- coding: utf-8 -*-
"""
Boundary Contours - Processing algorithm.

Generates contour lines from a DEM (like Raster > Extraction > Contour),
with two extras:
  * optional boundary polygon(s): only the part of the DEM covering the
    boundary is read, and contours are clipped to the boundary;
  * optional sink filling, so contours come from a hydrologically filled
    DEM (pits are filled flat to their spill level);
  * optional smoothing: Gaussian smoothing of the DEM and/or Chaikin
    smoothing of the contour lines;
  * index contours: every Nth contour is flagged (INDEX = 1, TYPE = 'Index')
    and the output can be auto-styled with thicker, labelled index lines.
"""

import math
import os
import uuid

from osgeo import gdal, ogr, osr

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsLineSymbol,
    QgsPalLayerSettings,
    QgsPolygon,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingFeatureSourceDefinition,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingParameterBand,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterDistance,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProcessingUtils,
    QgsRasterFileWriter,
    QgsRuleBasedLabeling,
    QgsRuleBasedRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsWkbTypes,
)

from .fill import FillCanceled, fill_depressions
from .smooth import gaussian_smooth
from . import settings_store

SAGA_FILL_IDS = ("sagang:fillsinkswangliu", "saga:fillsinkswangliu")

try:  # QGIS >= 3.30
    from qgis.PyQt.QtCore import QMetaType
    _INT, _DOUBLE, _STRING = QMetaType.Type.Int, QMetaType.Type.Double, QMetaType.Type.QString
except Exception:  # older QGIS
    from qgis.PyQt.QtCore import QVariant
    _INT, _DOUBLE, _STRING = QVariant.Int, QVariant.Double, QVariant.String


def _enum(new, old):
    """Return the scoped (QGIS >= 3.36 / QGIS 4) enum value, else the legacy one."""
    try:
        return new()
    except Exception:
        return old()


NUM_DOUBLE = _enum(lambda: Qgis.ProcessingNumberParameterType.Double,
                   lambda: QgsProcessingParameterNumber.Double)
NUM_INT = _enum(lambda: Qgis.ProcessingNumberParameterType.Integer,
                lambda: QgsProcessingParameterNumber.Integer)
FLAG_ADVANCED = _enum(lambda: Qgis.ProcessingParameterFlag.Advanced,
                      lambda: QgsProcessingParameterDefinition.FlagAdvanced)
SRC_POLYGON = _enum(lambda: Qgis.ProcessingSourceType.VectorPolygon,
                    lambda: QgsProcessing.TypeVectorPolygon)
SRC_LINE = _enum(lambda: Qgis.ProcessingSourceType.VectorLine,
                 lambda: QgsProcessing.TypeVectorLine)
WKB_MLINE = _enum(lambda: Qgis.WkbType.MultiLineString,
                  lambda: QgsWkbTypes.MultiLineString)
WKB_MLINE_Z = _enum(lambda: Qgis.WkbType.MultiLineStringZ,
                    lambda: QgsWkbTypes.MultiLineStringZ)
GEOM_LINE = _enum(lambda: Qgis.GeometryType.Line,
                  lambda: QgsWkbTypes.LineGeometry)
FAST_INSERT = _enum(lambda: QgsFeatureSink.Flag.FastInsert,
                    lambda: QgsFeatureSink.FastInsert)


def tr(text):
    return QCoreApplication.translate("BoundaryContours", text)


class _IndexStylePostProcessor(QgsProcessingLayerPostProcessorInterface):
    """Applies an index/intermediate style + index labels to the output layer."""

    instance = None  # keep a Python reference alive (QGIS requirement)

    def __init__(self, elev_field):
        super().__init__()
        self.elev_field = elev_field

    def postProcessLayer(self, layer, context, feedback):
        if layer is None or not layer.isValid():
            return
        brown = QColor(150, 90, 40)

        # --- Renderer: thick index lines, thin intermediate lines
        root = QgsRuleBasedRenderer.Rule(None)
        for label, expr, width in (
            (tr("Index contour"), '"INDEX" = 1', 0.6),
            (tr("Intermediate contour"), '"INDEX" = 0', 0.25),
        ):
            sym = QgsLineSymbol.createSimple(
                {"line_color": brown.name(), "line_width": str(width)}
            )
            rule = QgsRuleBasedRenderer.Rule(sym, 0, 0, expr, label)
            root.appendChild(rule)
        layer.setRenderer(QgsRuleBasedRenderer(root))

        # --- Labels on index contours only
        settings = QgsPalLayerSettings()
        settings.fieldName = 'round("{}", 3)'.format(self.elev_field)
        settings.isExpression = True
        try:
            settings.placement = Qgis.LabelPlacement.Line
        except AttributeError:
            settings.placement = QgsPalLayerSettings.Line
        fmt = QgsTextFormat()
        fmt.setColor(brown)
        fmt.setSize(8)
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(0.8)
        buf.setColor(QColor(255, 255, 255))
        fmt.setBuffer(buf)
        settings.setFormat(fmt)
        lrule = QgsRuleBasedLabeling.Rule(settings)
        lrule.setFilterExpression('"INDEX" = 1')
        lroot = QgsRuleBasedLabeling.Rule(None)
        lroot.appendChild(lrule)
        layer.setLabeling(QgsRuleBasedLabeling(lroot))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()


class BoundaryContoursAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    BAND = "BAND"
    INTERVAL = "INTERVAL"
    OFFSET = "OFFSET"
    INDEX_EVERY = "INDEX_EVERY"
    FIELD_NAME = "FIELD_NAME"
    BOUNDARY = "BOUNDARY"
    BUFFER = "BUFFER"
    CLIP = "CLIP"
    MIN_LENGTH = "MIN_LENGTH"
    CREATE_3D = "CREATE_3D"
    IGNORE_NODATA = "IGNORE_NODATA"
    NODATA = "NODATA"
    APPLY_STYLE = "APPLY_STYLE"
    FILL_METHOD = "FILL_METHOD"
    FILL_MARGIN = "FILL_MARGIN"
    FILLED_DEM = "FILLED_DEM"
    SMOOTH_DEM = "SMOOTH_DEM"
    SMOOTH_LINES = "SMOOTH_LINES"
    SMOOTH_OFFSET = "SMOOTH_OFFSET"

    FILL_OPTIONS = [
        "No, use the DEM as is",
        "Yes, built-in Priority-Flood (no extra software)",
        "Yes, SAGA Fill Sinks (Wang & Liu) (needs SAGA NextGen provider)",
    ]
    OUTPUT = "OUTPUT"

    # ------------------------------------------------------------------ meta
    def name(self):
        return "contours"

    def displayName(self):
        return tr("Contours (boundary + index)")

    def group(self):
        return tr("Contours")

    def groupId(self):
        return "contours"

    def shortHelpString(self):
        return tr(
            "Generates contour lines from a DEM, like the standard Contour tool, "
            "with two additions:<br><br>"
            "<b>Boundary</b> (optional): a polygon layer. Only the part of the DEM "
            "covering the boundary is processed, and contours are clipped to it. "
            "Tick 'Selected features only' to use just the selected polygons. "
            "A buffer can grow (or, if negative, shrink) the boundary.<br><br>"
            "<b>Index contours</b>: every Nth contour (counted from the offset) gets "
            "INDEX = 1 and TYPE = 'Index'; others get INDEX = 0 and TYPE = "
            "'Intermediate'. E.g. interval 2 m and N = 5 gives index lines every 10 m. "
            "Set N to 0 to disable.<br><br>"
            "With 'Apply index contour style', the output is drawn with thicker, "
            "labelled index contours.<br><br>"
            "'Minimum contour length' drops tiny fragments, which are common "
            "along a clipped boundary edge.<br><br>"
            "<b>Fill sinks</b>: builds the contours from a depression-filled DEM. "
            "Every pit is filled flat up to its spill (pour) elevation, so closed "
            "'bullseye' contours in hollows disappear and lines follow the way water "
            "actually drains. Cells on the DEM edge and next to NoData act as outlets. "
            "With a boundary, only the area around it is filled, so set "
            "'Extra DEM margin for filling' (advanced) wide enough for pits that "
            "straddle the boundary to fill using the real terrain beyond it. "
            "The filled DEM can be saved as an optional output.<br><br>"
            "<b>Smoothing</b>, in two independent forms:<br>"
            "&bull; <i>DEM smoothing</i> (Gaussian, strength in pixels): blurs the DEM "
            "before contouring. This removes noise and stair-steps at the source, "
            "and the contours can never cross. 1 to 3 pixels is usually enough for "
            "lidar. When combined with filling, the DEM is smoothed first, then filled.<br>"
            "&bull; <i>Line smoothing</i> (Chaikin, number of passes): rounds the "
            "corners of the finished lines. 1 to 3 passes are typical. Strong line "
            "smoothing on closely spaced contours can make neighbouring lines touch, "
            "so use DEM smoothing for heavy cleanup and line smoothing for the final "
            "polish. Lines are smoothed before clipping, so they still meet the "
            "boundary exactly.<br><br>"
            "The optional 'Filled / smoothed DEM' output saves the surface the "
            "contours were built from.<br><br>"
            "<b>Remembered settings</b>: the settings from the last run in this "
            "project are restored when the tool is opened from Raster &gt; Tile "
            "Drainage or the toolbar. A different or new project starts from the "
            "defaults. Output file names are not remembered, so an earlier result "
            "is never overwritten by accident."
        )

    def createInstance(self):
        return BoundaryContoursAlgorithm()

    # ------------------------------------------------------------ parameters
    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.INPUT, tr("Input DEM")))
        self.addParameter(
            QgsProcessingParameterBand(self.BAND, tr("Band number"), 1, self.INPUT)
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.INTERVAL, tr("Interval between contour lines"),
                NUM_DOUBLE, 10.0, minValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.INDEX_EVERY, tr("Index contour every Nth line (0 = none)"),
                NUM_INT, 5, minValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.BOUNDARY, tr("Boundary polygon layer [optional]"),
                [SRC_POLYGON], optional=True,
            )
        )
        buf = QgsProcessingParameterDistance(
            self.BUFFER, tr("Buffer boundary by (negative shrinks)"), 0.0, self.INPUT
        )
        self.addParameter(buf)
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.CLIP, tr("Clip contours exactly to boundary"), True
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.FIELD_NAME, tr("Attribute name for elevation"), "ELEV"
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.FILL_METHOD, tr("Contour from a filled DEM (fill sinks first)"),
                [tr(o) for o in self.FILL_OPTIONS], defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SMOOTH_DEM, tr("DEM smoothing strength, Gaussian sigma in pixels (0 = off)"),
                NUM_DOUBLE, 0.0, minValue=0.0, maxValue=50.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SMOOTH_LINES, tr("Contour line smoothing passes (0 = off)"),
                NUM_INT, 0, minValue=0, maxValue=10,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.APPLY_STYLE, tr("Apply index contour style and labels"), True
            )
        )

        # Advanced
        adv = [
            QgsProcessingParameterNumber(
                self.OFFSET, tr("Offset from zero relative to which to interpret intervals"),
                NUM_DOUBLE, 0.0,
            ),
            QgsProcessingParameterDistance(
                self.MIN_LENGTH, tr("Minimum contour length (0 = keep all)"), 0.0,
                self.INPUT, minValue=0.0,
            ),
            QgsProcessingParameterDistance(
                self.FILL_MARGIN, tr("Extra DEM margin around boundary for filling"),
                0.0, self.INPUT, minValue=0.0,
            ),
            QgsProcessingParameterNumber(
                self.SMOOTH_OFFSET, tr("Line smoothing corner offset (0.05 to 0.5)"),
                NUM_DOUBLE, 0.25, minValue=0.05, maxValue=0.5,
            ),
            QgsProcessingParameterBoolean(self.CREATE_3D, tr("Produce 3D vector"), False),
            QgsProcessingParameterBoolean(
                self.IGNORE_NODATA, tr("Treat all raster values as valid"), False
            ),
            QgsProcessingParameterNumber(
                self.NODATA, tr("Input pixel value to treat as NoData"),
                NUM_DOUBLE, optional=True,
            ),
        ]
        for p in adv:
            p.setFlags(p.flags() | FLAG_ADVANCED)
            self.addParameter(p)

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT, tr("Contours"), SRC_LINE
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.FILLED_DEM, tr("Filled / smoothed DEM"), optional=True,
                createByDefault=False,
            )
        )

    # --------------------------------------------------------------- helpers
    def _boundary_geometry(self, parameters, context, feedback, raster_crs):
        source = self.parameterAsSource(parameters, self.BOUNDARY, context)
        if source is None:
            return None
        xform = None
        if source.sourceCrs().isValid() and source.sourceCrs() != raster_crs:
            xform = QgsCoordinateTransform(
                source.sourceCrs(), raster_crs, context.transformContext()
            )
        geoms = []
        for f in source.getFeatures():
            if feedback.isCanceled():
                return None
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            g = QgsGeometry(g)
            if xform:
                g.transform(xform)
            if not g.isGeosValid():
                g = g.makeValid()
            geoms.append(g)
        if not geoms:
            raise QgsProcessingException(tr("The boundary layer has no usable polygons."))
        geom = QgsGeometry.unaryUnion(geoms)

        dist = self.parameterAsDouble(parameters, self.BUFFER, context)
        if dist:
            geom = geom.buffer(dist, 16)
        if geom is None or geom.isEmpty():
            raise QgsProcessingException(tr("The boundary is empty after buffering."))
        return geom

    @staticmethod
    def _src_window(ds, rect, pad=2):
        """Pixel window (xoff, yoff, xsize, ysize) covering rect, padded."""
        gt = ds.GetGeoTransform()
        if gt[2] != 0 or gt[4] != 0:
            return None  # rotated raster: just read the whole thing
        x0 = (rect.xMinimum() - gt[0]) / gt[1]
        x1 = (rect.xMaximum() - gt[0]) / gt[1]
        y0 = (rect.yMaximum() - gt[3]) / gt[5]
        y1 = (rect.yMinimum() - gt[3]) / gt[5]
        xmin, xmax = sorted((x0, x1))
        ymin, ymax = sorted((y0, y1))
        xoff = max(0, int(math.floor(xmin)) - pad)
        yoff = max(0, int(math.floor(ymin)) - pad)
        xend = min(ds.RasterXSize, int(math.ceil(xmax)) + pad)
        yend = min(ds.RasterYSize, int(math.ceil(ymax)) + pad)
        if xend <= xoff or yend <= yoff:
            return False  # no overlap
        return [xoff, yoff, xend - xoff, yend - yoff]

    @staticmethod
    def _smooth_line(geom, passes, offset):
        """Chaikin smoothing. Closed contours are smoothed as rings, so they
        stay closed and don't keep a sharp corner at the start/end vertex."""
        line = geom.constGet()
        try:
            closed = line.isClosed() and line.numPoints() >= 4
        except AttributeError:
            closed = False
        if closed:
            poly = QgsPolygon()
            poly.setExteriorRing(line.clone())
            sm = QgsGeometry(poly).smooth(passes, offset)
            if sm is not None and not sm.isEmpty():
                return QgsGeometry(sm.constGet().exteriorRing().clone())
            return geom
        sm = geom.smooth(passes, offset)
        return sm if sm is not None and not sm.isEmpty() else geom

    @staticmethod
    def _write_array(path, drv_name, ref_ds, arr, nodata):
        drv = gdal.GetDriverByName(drv_name)
        out = drv.Create(path, ref_ds.RasterXSize, ref_ds.RasterYSize, 1, gdal.GDT_Float32,
                         ["COMPRESS=DEFLATE"] if drv_name == "GTiff" else [])
        if out is None:
            raise QgsProcessingException(tr("Could not create: {}").format(path))
        out.SetGeoTransform(ref_ds.GetGeoTransform())
        out.SetProjection(ref_ds.GetProjection())
        ob = out.GetRasterBand(1)
        if nodata is not None:
            ob.SetNoDataValue(float(nodata))
        ob.WriteArray(arr)
        ob.FlushCache()
        return out

    def _prepare_surface(self, fill_method, sigma, ds, band, nodata,
                         parameters, context, feedback):
        """Smooth and/or fill `band`; return a new single-band GDAL dataset."""
        arr = band.ReadAsArray().astype("float64")
        mask = None
        if nodata is not None:
            mask = (arr == nodata) | ~(arr == arr)

        # ---- 1. Gaussian DEM smoothing
        if sigma > 0:
            feedback.pushInfo(tr("Smoothing DEM (Gaussian sigma = {} px)...").format(sigma))
            arr = gaussian_smooth(arr, sigma, mask)
            feedback.setProgress(5)
            if feedback.isCanceled():
                return None

        # ---- 2. Sink filling
        progress = lambda f: feedback.setProgress(5 + min(f, 1.0) * 25.0)
        if fill_method == 1:
            feedback.pushInfo(tr("Filling sinks (built-in Priority-Flood)..."))
            try:
                arr = fill_depressions(arr, mask, progress, feedback.isCanceled)
            except FillCanceled:
                return None
        elif fill_method == 2:
            import processing
            reg = QgsApplication.processingRegistry()
            alg_id = next((a for a in SAGA_FILL_IDS if reg.algorithmById(a)), None)
            if alg_id is None:
                raise QgsProcessingException(tr(
                    "SAGA 'Fill sinks (Wang & Liu)' is not available. Install the "
                    "'Processing Saga NextGen Provider' plugin, or choose the built-in "
                    "fill method."))
            feedback.pushInfo(tr("Filling sinks with {}...").format(alg_id))
            tmp_in = QgsProcessingUtils.generateTempFilename("dem_window.tif")
            self._write_array(tmp_in, "GTiff", ds, arr, nodata)
            res = processing.run(alg_id, {
                "ELEV": tmp_in, "MINSLOPE": 0.0, "FILLED": "TEMPORARY_OUTPUT",
            }, context=context, feedback=feedback, is_child_algorithm=True)
            out_ds = gdal.Open(res["FILLED"])
            if out_ds is None:
                raise QgsProcessingException(tr("Could not read the SAGA filled DEM."))
            filled = out_ds.GetRasterBand(1).ReadAsArray().astype("float64")
            out_ds = None
            if filled.shape != arr.shape:
                raise QgsProcessingException(tr("SAGA output size does not match the DEM."))
            if mask is not None:
                filled[mask] = nodata
            arr = filled

        # ---- write the surface (to the user's file, or to memory)
        out_path = None
        if parameters.get(self.FILLED_DEM) not in (None, ""):
            out_path = self.parameterAsOutputLayer(parameters, self.FILLED_DEM, context)
        if out_path:
            ext = os.path.splitext(out_path)[1]
            drv_name = QgsRasterFileWriter.driverForExtension(ext) or "GTiff"
        else:
            out_path = "/vsimem/boundary_contours_surface_{}.tif".format(uuid.uuid4().hex)
            drv_name = "GTiff"
        return self._write_array(out_path, drv_name, ds, arr, nodata)

    # ------------------------------------------------------------- remember
    def prepareAlgorithm(self, parameters, context, feedback):
        """Runs on the main thread before processing: store this run's
        settings for the current project so the dialog can restore them."""
        try:
            self._remember_settings(parameters, context)
        except Exception as e:  # never block a run because of this
            feedback.pushDebugInfo("Could not store settings: {}".format(e))
        return True

    def _remember_settings(self, parameters, context):
        project = context.project()
        if project is None:
            return
        v = {
            "BAND": self.parameterAsInt(parameters, self.BAND, context),
            "INTERVAL": self.parameterAsDouble(parameters, self.INTERVAL, context),
            "INDEX_EVERY": self.parameterAsInt(parameters, self.INDEX_EVERY, context),
            "BUFFER": self.parameterAsDouble(parameters, self.BUFFER, context),
            "CLIP": self.parameterAsBool(parameters, self.CLIP, context),
            "FIELD_NAME": self.parameterAsString(parameters, self.FIELD_NAME, context),
            "FILL_METHOD": self.parameterAsEnum(parameters, self.FILL_METHOD, context),
            "SMOOTH_DEM": self.parameterAsDouble(parameters, self.SMOOTH_DEM, context),
            "SMOOTH_LINES": self.parameterAsInt(parameters, self.SMOOTH_LINES, context),
            "APPLY_STYLE": self.parameterAsBool(parameters, self.APPLY_STYLE, context),
            "OFFSET": self.parameterAsDouble(parameters, self.OFFSET, context),
            "FILL_MARGIN": self.parameterAsDouble(parameters, self.FILL_MARGIN, context),
            "SMOOTH_OFFSET": self.parameterAsDouble(parameters, self.SMOOTH_OFFSET, context),
            "MIN_LENGTH": self.parameterAsDouble(parameters, self.MIN_LENGTH, context),
            "CREATE_3D": self.parameterAsBool(parameters, self.CREATE_3D, context),
            "IGNORE_NODATA": self.parameterAsBool(parameters, self.IGNORE_NODATA, context),
            "NODATA": None,
        }
        if parameters.get(self.NODATA) not in (None, ""):
            v["NODATA"] = self.parameterAsDouble(parameters, self.NODATA, context)

        dem = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        if dem is not None and project.mapLayer(dem.id()):
            v["INPUT"] = dem.id()

        bval = parameters.get(self.BOUNDARY)
        if bval not in (None, ""):
            blyr = self.parameterAsVectorLayer(parameters, self.BOUNDARY, context)
            if blyr is not None and project.mapLayer(blyr.id()):
                v["BOUNDARY"] = blyr.id()
                v["BOUNDARY_SELECTED"] = bool(
                    isinstance(bval, QgsProcessingFeatureSourceDefinition)
                    and bval.selectedFeaturesOnly
                )
        settings_store.save(v, project)

    # ------------------------------------------------------------------ run
    def processAlgorithm(self, parameters, context, feedback):
        raster = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        if raster is None:
            raise QgsProcessingException(tr("Invalid input DEM."))
        band_no = self.parameterAsInt(parameters, self.BAND, context)
        interval = self.parameterAsDouble(parameters, self.INTERVAL, context)
        offset = self.parameterAsDouble(parameters, self.OFFSET, context)
        index_every = self.parameterAsInt(parameters, self.INDEX_EVERY, context)
        elev_name = self.parameterAsString(parameters, self.FIELD_NAME, context) or "ELEV"
        clip = self.parameterAsBool(parameters, self.CLIP, context)
        min_len = self.parameterAsDouble(parameters, self.MIN_LENGTH, context)
        make_3d = self.parameterAsBool(parameters, self.CREATE_3D, context)
        ignore_nodata = self.parameterAsBool(parameters, self.IGNORE_NODATA, context)
        apply_style = self.parameterAsBool(parameters, self.APPLY_STYLE, context)
        fill_method = self.parameterAsEnum(parameters, self.FILL_METHOD, context)
        fill_margin = self.parameterAsDouble(parameters, self.FILL_MARGIN, context)
        sigma = self.parameterAsDouble(parameters, self.SMOOTH_DEM, context)
        smooth_passes = self.parameterAsInt(parameters, self.SMOOTH_LINES, context)
        smooth_offset = self.parameterAsDouble(parameters, self.SMOOTH_OFFSET, context)
        user_nodata = None
        if parameters.get(self.NODATA) not in (None, ""):
            user_nodata = self.parameterAsDouble(parameters, self.NODATA, context)

        if interval <= 0:
            raise QgsProcessingException(tr("Interval must be greater than zero."))
        if elev_name.upper() in ("ID", "INDEX", "TYPE"):
            raise QgsProcessingException(
                tr("Elevation field name clashes with a built-in field (ID, INDEX, TYPE).")
            )

        crs = raster.crs()
        boundary = self._boundary_geometry(parameters, context, feedback, crs)

        # ---- open DEM with GDAL
        src_path = raster.source()
        if raster.providerType() != "gdal":
            raise QgsProcessingException(
                tr("The input DEM must be a GDAL-readable raster (not a web service).")
            )
        ds = gdal.Open(src_path)
        if ds is None:
            raise QgsProcessingException(tr("GDAL could not open: {}").format(src_path))

        vrt_path = None
        if boundary is not None:
            pad = 2
            if fill_method and fill_margin > 0:
                pad += int(math.ceil(fill_margin / abs(ds.GetGeoTransform()[1])))
            if sigma > 0:
                pad += int(math.ceil(3.0 * sigma))  # so the blur sees real terrain
            win = self._src_window(ds, boundary.boundingBox(), pad)
            if win is False:
                raise QgsProcessingException(tr("The boundary does not overlap the DEM."))
            if win:
                vrt_path = "/vsimem/boundary_contours_{}.vrt".format(uuid.uuid4().hex)
                ds = gdal.Translate(vrt_path, ds, format="VRT", srcWin=win)
                if ds is None:
                    raise QgsProcessingException(tr("Could not crop the DEM to the boundary."))
                feedback.pushInfo(
                    tr("Processing DEM window {} x {} pixels.").format(win[2], win[3])
                )

        band = ds.GetRasterBand(band_no)
        if band is None:
            raise QgsProcessingException(tr("Band {} not found.").format(band_no))

        # ---- in-memory OGR layer for GDAL output
        drv = ogr.GetDriverByName("MEM") or ogr.GetDriverByName("Memory")
        mem_ds = drv.CreateDataSource("contours")
        srs = osr.SpatialReference()
        wkt = ds.GetProjection()
        if wkt:
            srs.ImportFromWkt(wkt)
        gtype = ogr.wkbLineString25D if make_3d else ogr.wkbLineString
        lyr = mem_ds.CreateLayer("contours", srs if wkt else None, gtype)
        lyr.CreateField(ogr.FieldDefn("ID", ogr.OFTInteger))
        lyr.CreateField(ogr.FieldDefn("ELEV", ogr.OFTReal))

        opts = [
            "LEVEL_INTERVAL={!r}".format(interval),
            "LEVEL_BASE={!r}".format(offset),
            "ID_FIELD=0",
            "ELEV_FIELD=1",
        ]
        nd = None
        if not ignore_nodata:
            nd = user_nodata
            if nd is None:
                nd = band.GetNoDataValue()
            if nd is not None:
                opts.append("NODATA={!r}".format(nd))

        # ---- optional sink filling
        filled_ds = None
        p0, p1 = 0.0, 60.0
        if fill_method or sigma > 0:
            filled_ds = self._prepare_surface(fill_method, sigma, ds, band, nd,
                                              parameters, context, feedback)
            if filled_ds is None or feedback.isCanceled():
                return {}
            band = filled_ds.GetRasterBand(1)
            p0, p1 = 30.0, 65.0

        def _progress(pct, _msg, _data):
            feedback.setProgress(p0 + pct * (p1 - p0))
            return 0 if feedback.isCanceled() else 1

        feedback.pushInfo(tr("Generating contours..."))
        err = gdal.ContourGenerateEx(band, lyr, options=opts, callback=_progress)
        if feedback.isCanceled():
            return {}
        if err != 0:
            raise QgsProcessingException(tr("GDAL contour generation failed."))

        # ---- output sink
        fields = QgsFields()
        fields.append(QgsField("ID", _INT))
        fields.append(QgsField(elev_name, _DOUBLE))
        fields.append(QgsField("INDEX", _INT))
        fields.append(QgsField("TYPE", _STRING, len=12))
        wkb = WKB_MLINE_Z if make_3d else WKB_MLINE
        sink, dest_id = self.parameterAsSink(parameters, self.OUTPUT, context, fields, wkb, crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        engine = None
        if boundary is not None and clip:
            engine = QgsGeometry.createGeometryEngine(boundary.constGet())
            engine.prepareGeometry()

        total = max(lyr.GetFeatureCount(), 1)
        written = 0
        lyr.ResetReading()
        for i, of in enumerate(lyr):
            if feedback.isCanceled():
                break
            if i % 200 == 0:
                feedback.setProgress(p1 + (100.0 - p1) * i / total)
            og = of.GetGeometryRef()
            if og is None:
                continue
            geom = QgsGeometry()
            geom.fromWkb(bytes(og.ExportToIsoWkb()))
            if geom.isEmpty():
                continue

            if smooth_passes > 0:
                geom = self._smooth_line(geom, smooth_passes, smooth_offset)

            if engine is not None:
                if not engine.intersects(geom.constGet()):
                    continue
                if not engine.contains(geom.constGet()):
                    geom = geom.intersection(boundary)
                    if geom.isEmpty():
                        continue
                    if geom.type() != GEOM_LINE:
                        geom = geom.convertToType(GEOM_LINE, True)
                        if geom is None or geom.isEmpty():
                            continue

            if min_len > 0:
                parts = [p for p in geom.asGeometryCollection() if p.length() >= min_len]
                if not parts:
                    continue
                geom = QgsGeometry.collectGeometry(parts)

            geom.convertToMultiType()
            elev = of.GetField(1)
            is_index = 0
            if index_every > 0:
                n = int(round((elev - offset) / interval))
                is_index = 1 if n % index_every == 0 else 0

            feat = QgsFeature(fields)
            feat.setGeometry(geom)
            written += 1
            feat.setAttributes([
                written, elev, is_index, "Index" if is_index else "Intermediate"
            ])
            sink.addFeature(feat, FAST_INSERT)

        mem_ds = None
        ds = None
        band = None
        filled_path = filled_ds.GetDescription() if filled_ds is not None else None
        filled_ds = None
        if vrt_path:
            gdal.Unlink(vrt_path)
        results = {self.OUTPUT: dest_id}
        if filled_path:
            if filled_path.startswith("/vsimem/"):
                gdal.Unlink(filled_path)
            else:
                results[self.FILLED_DEM] = filled_path

        feedback.pushInfo(tr("{} contour features written.").format(written))

        if apply_style and context.willLoadLayerOnCompletion(dest_id):
            _IndexStylePostProcessor.instance = _IndexStylePostProcessor(elev_name)
            context.layerToLoadOnCompletionDetails(dest_id).setPostProcessor(
                _IndexStylePostProcessor.instance
            )

        return results
