"""Processing algorithm: locate a tile main for laterals parallel or perpendicular to the contours."""
import math
import os

import numpy as np
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsLineSymbol,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingOutputNumber,
    QgsProcessingOutputString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterRasterLayer,
    QgsRectangle,
    QgsRuleBasedRenderer,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon

from .core import (
    ALIGN_BEST,
    ALIGN_STRAIGHT,
    ALIGN_TERRAIN,
    LAT_PARALLEL,
    LAT_PERPENDICULAR,
    SHAPE_CONTOUR,
    SHAPE_STRAIGHT,
    STATUS_LABELS,
    Grid,
    LocatorCancelled,
    LocatorError,
    LocatorParams,
    run_locator,
)

PLUGIN_DIR = os.path.dirname(__file__)

ORIENTS = [LAT_PARALLEL, LAT_PERPENDICULAR]
SHAPES = [SHAPE_CONTOUR, SHAPE_STRAIGHT]
ALIGNS = [ALIGN_BEST, ALIGN_TERRAIN, ALIGN_STRAIGHT]
Z_FACTORS = [1.0, 0.3048, 1200.0 / 3937.0]
UNIT_FACTORS = [1.0, 0.3048]
# keep post-processors alive until QGIS has used them
_POST = {}


def _meters_factor(crs):
    """Map units -> metres."""
    try:
        from qgis.core import Qgis
        return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), Qgis.DistanceUnit.Meters)
    except (ImportError, AttributeError):
        return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), QgsUnitTypes.DistanceMeters)


def _union_box(a, b):
    return QgsRectangle(min(a.xMinimum(), b.xMinimum()), min(a.yMinimum(), b.yMinimum()),
                        max(a.xMaximum(), b.xMaximum()), max(a.yMaximum(), b.yMaximum()))


def _advanced(param):
    try:
        from qgis.core import Qgis
        param.setFlags(param.flags() | Qgis.ProcessingParameterFlag.Advanced)
    except (ImportError, AttributeError):
        param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
    return param


class _CandidateStyler(QgsProcessingLayerPostProcessorInterface):
    """Chosen main red; runners-up blue, dashed."""

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsVectorLayer):
            return
        root = QgsRuleBasedRenderer.Rule(None)
        ch = QgsLineSymbol.createSimple({"color": "185,28,28", "width": "1.2"})
        root.appendChild(QgsRuleBasedRenderer.Rule(ch, 0, 0, "\"chosen\"", "Chosen main"))
        run = QgsLineSymbol.createSimple({"color": "37,99,235", "width": "0.6",
                                          "line_style": "dash"})
        root.appendChild(QgsRuleBasedRenderer.Rule(run, 0, 0, "NOT \"chosen\"",
                                                   "Runner-up (see rank)"))
        layer.setRenderer(QgsRuleBasedRenderer(root))
        try:
            self._label(layer)
        except Exception as e:  # noqa: BLE001  labels are a convenience only
            feedback.pushWarning("Could not label the candidates: {}".format(e))
        layer.triggerRepaint()

    @staticmethod
    def _label(layer):
        """Label each candidate with its rank, along the line, in a white halo."""
        from qgis.core import (QgsPalLayerSettings, QgsTextBufferSettings, QgsTextFormat,
                               QgsVectorLayerSimpleLabeling)
        from qgis.PyQt.QtGui import QColor, QFont
        fmt = QgsTextFormat()
        font = QFont()
        font.setBold(True)
        fmt.setFont(font)
        fmt.setSize(10)
        fmt.setColor(QColor(30, 41, 59))
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(1.0)
        buf.setColor(QColor(255, 255, 255))
        fmt.setBuffer(buf)
        pal = QgsPalLayerSettings()
        pal.fieldName = "rank"
        pal.isExpression = False
        pal.setFormat(fmt)
        try:
            from qgis.core import Qgis
            pal.placement = Qgis.LabelPlacement.Line
        except (ImportError, AttributeError):
            pal.placement = QgsPalLayerSettings.Line
        pal.enabled = True
        layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        layer.setLabelsEnabled(True)


class MainTileLocatorAlgorithm(QgsProcessingAlgorithm):
    DEM = "DEM"
    FIELD = "FIELD"
    OUTLET = "OUTLET"
    ORIENTATION = "ORIENTATION"
    LATERAL_SHAPE = "LATERAL_SHAPE"
    ALIGNMENT = "ALIGNMENT"
    UNITS = "UNITS"
    SPACING = "SPACING"
    MAX_LAT_LEN = "MAX_LAT_LEN"
    UPPER_DEPTH = "UPPER_DEPTH"
    LOWER_DEPTH = "LOWER_DEPTH"
    MIN_COVER = "MIN_COVER"
    MAX_DEPTH = "MAX_DEPTH"
    MAX_GRADE = "MAX_GRADE"
    MIN_MAIN_GRADE = "MIN_MAIN_GRADE"
    MIN_LAT_GRADE = "MIN_LAT_GRADE"
    OFFSET = "OFFSET"
    LOWER_OUTLET = "LOWER_OUTLET"
    OUTLET_INVERT = "OUTLET_INVERT"
    Z_UNITS = "Z_UNITS"
    CONTOUR_GRADE = "CONTOUR_GRADE"
    GRADE_BREAK = "GRADE_BREAK"
    BEARING_STEP = "BEARING_STEP"
    MAIN_STOP = "MAIN_STOP"
    MAIN_WEIGHT = "MAIN_WEIGHT"
    SETBACK = "SETBACK"
    SMOOTH = "SMOOTH"
    CELL_SIZE = "CELL_SIZE"
    CANDIDATES = "CANDIDATES"
    N_CANDIDATES = 5          # the chosen main and four runners-up

    # ------------------------------------------------------------ metadata
    def tr(self, s):
        return QCoreApplication.translate("MainTileLocator", s)

    def createInstance(self):
        return MainTileLocatorAlgorithm()

    def name(self):
        return "maintilelocator"

    def displayName(self):
        return self.tr("Main tile locator")

    def group(self):
        return ""

    def groupId(self):
        return ""

    def icon(self):
        return QIcon(os.path.join(PLUGIN_DIR, "icon.png"))

    def shortHelpString(self):
        return self.tr(
            "<p>Locates a tile main from an <b>outlet</b>, a <b>DEM</b> and the <b>field "
            "boundary</b>, for laterals to be installed either <b>parallel</b> or "
            "<b>perpendicular</b> to the contour lines.</p>"
            "<p><b>How it works.</b> From every part of the field the tool traces the path a "
            "lateral would take: along the contours (either way, turned just enough downhill "
            "to hold the target lateral grade) or straight down the fall line, bending with "
            "the terrain or on one straight bearing. It then builds candidate mains from the "
            "outlet: the low-ground (thalweg) line, a fall-line trace, headers along the "
            "contours (with or without following the field edge), straight mains on every "
            "bearing, and dog-legs that run along the low side before turning up the slope. "
            "Each candidate is scored by the drainable area whose laterals reach it with "
            "enough fall, within the maximum lateral length and without converging into a "
            "draw, less a cost for its length and for any length that cannot be buried within "
            "the depth and slope limits. The best main is trimmed at its upper end while that "
            "helps, then buried with Routine J (Tile Burying System) of the Illini Drainage "
            "Tools.</p>"
            "<p><b>Laterals parallel to the contours</b> (across the slope) need a main that "
            "crosses the contours: up the slope, usually in the low ground. <b>Laterals "
            "perpendicular to the contours</b> (down the slope) need a main that runs along "
            "the contours across the low side of the field (a header), or up a draw they "
            "drain into.</p>"
            "<p><b>Outputs</b><br>"
            "<i>Candidate mains</i>: the chosen main (rank 1, <i>chosen</i> = true, drawn red) "
            "and the four runners-up (blue, dashed), each drawn from its upper end to the "
            "outlet, with the share of the drainable area its laterals reach, its length, "
            "design grade, depths and Routine J notes. Select the chosen one to use it as "
            "the <i>Main line(s)</i> input of the Illini Subsurface Drain Layout plugin "
            "(tick <i>Selected features only</i>).</p>"
            "<p>All lengths, depths and elevations on this form and in the outputs are in the "
            "chosen design units; grades are in percent. The DEM must be in a projected "
            "CRS.</p>"
            "<p>Terrain handling and the Routine J burying scheme come from the Illini "
            "Subsurface Drain Layout plugin and the Illini Drainage Tools by Anamelechi "
            "Falasy and Richard Cooke, University of Illinois Urbana-Champaign (GPL v3).</p>")

    # ------------------------------------------------------------ parameters
    def initAlgorithm(self, config=None):
        D = QgsProcessingParameterNumber.Double
        self.addParameter(QgsProcessingParameterPoint(
            self.OUTLET, self.tr("Outlet location (click on the map)")))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, self.tr("Elevation (DEM)")))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.FIELD, self.tr("Field boundary"), [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterEnum(
            self.ORIENTATION, self.tr("Laterals to be installed"),
            options=[self.tr("Parallel to the contour lines (across the slope)"),
                     self.tr("Perpendicular to the contour lines (down the slope)")],
            defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.LATERAL_SHAPE, self.tr("Lateral shape"),
            options=[self.tr("Bend with the terrain (follow the contours or fall line)"),
                     self.tr("Straight (dominant contour or fall bearing of the field)")],
            defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.ALIGNMENT, self.tr("Main alignment"),
            options=[self.tr("Best of all candidates"),
                     self.tr("Bend with the terrain only"),
                     self.tr("Straight lines only")],
            defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.UNITS, self.tr("Design units (spacing, lengths, depths, outputs)"),
            options=[self.tr("Metres"), self.tr("Feet")], defaultValue=1))
        self.addParameter(QgsProcessingParameterNumber(
            self.SPACING, self.tr("Lateral spacing"), D, defaultValue=80.0, minValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_LAT_LEN, self.tr("Maximum lateral length (0 = no limit)"), D,
            defaultValue=0.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.UPPER_DEPTH, self.tr("Burying: upper tile depth (target band)"), D,
            defaultValue=3.25, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.LOWER_DEPTH, self.tr("Burying: lower tile depth (target band)"), D,
            defaultValue=4.25, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_COVER, self.tr("Burying: absolute upper tile depth (minimum cover)"), D,
            defaultValue=2.5, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_DEPTH, self.tr("Burying: absolute lower tile depth (maximum depth)"), D,
            defaultValue=6.0, minValue=0.1))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_GRADE, self.tr("Burying: maximum slope (%)"), D, defaultValue=5.0,
            minValue=0.01, maxValue=100.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_MAIN_GRADE, self.tr("Burying: minimum slope of the main (%)"), D,
            defaultValue=0.1, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_LAT_GRADE, self.tr("Minimum slope of the laterals (%)"), D,
            defaultValue=0.1, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.OFFSET, self.tr("Burying: offset depth at the outlet (+ lowers the outlet)"),
            D, defaultValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.LOWER_OUTLET,
            self.tr("Burying: lower the outlet for low ground (down to the lower tile depth)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterNumber(
            self.OUTLET_INVERT,
            self.tr("Outlet invert elevation, in DEM elevation units [optional]"), D,
            optional=True))

        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.Z_UNITS, self.tr("DEM elevation units"),
            options=[self.tr("Metres"), self.tr("International feet"),
                     self.tr("US survey feet")], defaultValue=2)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.CONTOUR_GRADE,
            self.tr("Target grade of laterals parallel to the contours (%)"), D,
            defaultValue=0.2, minValue=0.01)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.GRADE_BREAK, self.tr("Burying: longest segment between grade breaks"), D,
            defaultValue=50.0, minValue=0.1)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.BEARING_STEP, self.tr("Straight mains: bearing step (degrees)"), D,
            defaultValue=5.0, minValue=1.0, maxValue=45.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MAIN_STOP,
            self.tr("Low-ground main ends where its drainage area falls below (% of field)"),
            D, defaultValue=2.0, minValue=0.1, maxValue=50.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MAIN_WEIGHT,
            self.tr("Scoring: cost of main length (drained strips of one spacing per unit "
                    "length)"), D, defaultValue=0.5, minValue=0.0, maxValue=10.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SETBACK, self.tr("Boundary setback [optional, default half the spacing]"), D,
            optional=True, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SMOOTH, self.tr("Terrain smoothing radius [optional, default the spacing]"),
            D, optional=True, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.CELL_SIZE, self.tr("Analysis cell size [optional, automatic]"), D,
            optional=True, minValue=0.0)))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.CANDIDATES, self.tr("Candidate mains (chosen and four runners-up)"),
            QgsProcessing.TypeVectorLine))

        for key, desc in (("MAIN_LENGTH", "Main length"),
                          ("SERVED_PCT", "Drainable area whose laterals reach the main (%)"),
                          ("OUTLET_DEPTH", "Outlet tile depth")):
            self.addOutput(QgsProcessingOutputNumber(key, self.tr(desc)))
        self.addOutput(QgsProcessingOutputString("METHOD", self.tr("Chosen candidate")))
        self.addOutput(QgsProcessingOutputString("WARNINGS", self.tr("Warnings")))

    # ------------------------------------------------------------ inputs
    @staticmethod
    def _rings(geom, xf):
        rings = []
        polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
        for poly in polys:
            for ring in poly:
                if len(ring) >= 3:
                    rings.append(np.array([[p.x() * xf, p.y() * xf] for p in ring]))
        return rings

    @staticmethod
    def _read_dem(layer, bounds, nrows, ncols, feedback):
        """Resample the DEM onto the analysis grid (bilinear). Returns (array, valid)."""
        xmin, ymin, xmax, ymax = bounds
        if layer.providerType() == "gdal":
            try:
                from osgeo import gdal
                src = layer.dataProvider().dataSourceUri()
                tmp = "/vsimem/main_tile_locator_dem.tif"
                ds = gdal.Warp(tmp, src, format="GTiff",
                               outputBounds=(xmin, ymin, xmax, ymax),
                               width=ncols, height=nrows, resampleAlg="bilinear",
                               dstNodata=-99999.0, outputType=gdal.GDT_Float32)
                arr = None
                if ds is not None:
                    arr = ds.GetRasterBand(1).ReadAsArray().astype(float)
                    ds = None
                gdal.Unlink(tmp)
                if arr is not None:
                    return arr, np.isfinite(arr) & (arr > -99998.0)
            except Exception as e:  # noqa: BLE001
                feedback.pushWarning("GDAL resampling failed ({}); falling back to "
                                     "nearest-neighbour reading.".format(e))
        block = layer.dataProvider().block(1, QgsRectangle(xmin, ymin, xmax, ymax),
                                           ncols, nrows)
        arr = np.empty((nrows, ncols))
        valid = np.ones((nrows, ncols), dtype=bool)
        for i in range(nrows):
            for j in range(ncols):
                arr[i, j] = block.value(i, j)
                if block.isNoData(i, j):
                    valid[i, j] = False
        return arr, valid & np.isfinite(arr)

    # ------------------------------------------------------------ run
    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        if dem_layer is None:
            raise QgsProcessingException(self.tr("Could not load the DEM."))
        crs = dem_layer.crs()
        if not crs.isValid() or crs.isGeographic():
            raise QgsProcessingException(self.tr(
                "The DEM must be in a projected coordinate system (metres or feet). "
                "Reproject it with Raster > Projections > Warp first."))
        xf = _meters_factor(crs)
        uf = UNIT_FACTORS[self.parameterAsEnum(parameters, self.UNITS, context)]
        zf = Z_FACTORS[self.parameterAsEnum(parameters, self.Z_UNITS, context)]
        u_lbl = "m" if uf == 1.0 else "ft"

        def num(key, optional=False):
            if optional and parameters.get(key) in (None, ""):
                return None
            v = self.parameterAsDouble(parameters, key, context)
            if optional and (v is None or v != v):
                return None
            return v

        spacing = num(self.SPACING) * uf
        setback = num(self.SETBACK, True)
        smooth_r = num(self.SMOOTH, True)
        cell_in = num(self.CELL_SIZE, True)
        outlet_inv = num(self.OUTLET_INVERT, True)
        max_lat = num(self.MAX_LAT_LEN)

        source = self.parameterAsSource(parameters, self.FIELD, context)
        if source is None:
            raise QgsProcessingException(self.tr("Could not load the field boundary."))
        tr = QgsCoordinateTransform(source.sourceCrs(), crs, context.transformContext())
        geoms = []
        for f in source.getFeatures():
            g = QgsGeometry(f.geometry())
            if g.isEmpty():
                continue
            g.transform(tr)
            geoms.append(g)
        if not geoms:
            raise QgsProcessingException(self.tr("The field layer has no polygons."))
        field = QgsGeometry.unaryUnion(geoms) if len(geoms) > 1 else geoms[0]
        if len(geoms) > 1:
            feedback.pushInfo(self.tr("{} field polygons merged into one field.")
                              .format(len(geoms)))
        if parameters.get(self.OUTLET) in (None, ""):
            raise QgsProcessingException(self.tr("Pick the outlet location on the map."))
        outlet = self.parameterAsPoint(parameters, self.OUTLET, context, crs)

        # analysis grid
        bb = _union_box(field.boundingBox(),
                        QgsRectangle(outlet.x(), outlet.y(), outlet.x(), outlet.y()))
        area_m2 = field.area() * xf * xf
        dem_res_m = abs(dem_layer.rasterUnitsPerPixelX()) * xf
        if cell_in:
            cell_m = cell_in * uf
        else:
            cell_m = max(dem_res_m, spacing / 6.0, 0.5)
            cell_m = min(cell_m, spacing / 3.0)
            cell_m = max(cell_m, math.sqrt(area_m2 / 2.5e5))
            cell_m = round(cell_m, 2)
        smooth_m = smooth_r * uf if smooth_r is not None else spacing
        margin = (1.5 * smooth_m + 4 * cell_m) / xf
        cell_map = cell_m / xf
        xmin = math.floor((bb.xMinimum() - margin) / cell_map) * cell_map
        ymin = math.floor((bb.yMinimum() - margin) / cell_map) * cell_map
        xmax = math.ceil((bb.xMaximum() + margin) / cell_map) * cell_map
        ymax = math.ceil((bb.yMaximum() + margin) / cell_map) * cell_map
        ncols = int(round((xmax - xmin) / cell_map))
        nrows = int(round((ymax - ymin) / cell_map))
        if nrows * ncols > 6_000_000:
            raise QgsProcessingException(self.tr(
                "The analysis grid would have {:,} cells; set a larger analysis cell size."
            ).format(nrows * ncols))
        feedback.pushInfo(self.tr("Analysis grid: {} x {} cells of {:.2f} {}.")
                          .format(ncols, nrows, cell_m / uf, u_lbl))
        dem, valid = self._read_dem(dem_layer, (xmin, ymin, xmax, ymax), nrows, ncols,
                                    feedback)
        if not valid.any():
            raise QgsProcessingException(self.tr("The DEM has no data over the field."))
        dem = dem * zf
        grid = Grid(xmin * xf, ymax * xf, cell_m, nrows, ncols)

        params = LocatorParams(
            lateral_orientation=ORIENTS[self.parameterAsEnum(parameters, self.ORIENTATION,
                                                             context)],
            lateral_shape=SHAPES[self.parameterAsEnum(parameters, self.LATERAL_SHAPE, context)],
            alignment=ALIGNS[self.parameterAsEnum(parameters, self.ALIGNMENT, context)],
            spacing=spacing,
            max_lateral_length=max_lat * uf if max_lat and max_lat > 0 else None,
            contour_grade=num(self.CONTOUR_GRADE) / 100.0,
            min_lateral_grade=num(self.MIN_LAT_GRADE) / 100.0,
            min_main_grade=num(self.MIN_MAIN_GRADE) / 100.0,
            max_grade=num(self.MAX_GRADE) / 100.0,
            target_upper=num(self.UPPER_DEPTH) * uf,
            target_lower=num(self.LOWER_DEPTH) * uf,
            min_cover=num(self.MIN_COVER) * uf,
            max_depth=num(self.MAX_DEPTH) * uf,
            outlet_offset=num(self.OFFSET) * uf,
            lower_outlet=self.parameterAsBool(parameters, self.LOWER_OUTLET, context),
            grade_break=num(self.GRADE_BREAK) * uf,
            outlet_invert=outlet_inv * zf if outlet_inv is not None else None,
            setback=setback * uf if setback is not None else None,
            smooth_radius=smooth_m,
            bearing_step=num(self.BEARING_STEP),
            main_weight=num(self.MAIN_WEIGHT),
            main_stop_frac=num(self.MAIN_STOP) / 100.0,
            units=u_lbl,
        )
        if params.target_lower < params.target_upper:
            raise QgsProcessingException(self.tr(
                "The lower tile depth must not be less than the upper tile depth."))
        if params.max_depth <= params.min_cover:
            raise QgsProcessingException(self.tr(
                "The absolute lower tile depth must be greater than the absolute upper "
                "tile depth."))

        def progress(pct, msg):
            feedback.setProgress(pct)
            feedback.setProgressText(msg)

        try:
            res = run_locator(dem, valid, grid, self._rings(field, xf),
                              (outlet.x() * xf, outlet.y() * xf), params,
                              progress=progress, cancel=feedback.isCanceled)
        except LocatorCancelled:
            return {}
        except LocatorError as e:
            raise QgsProcessingException(str(e))

        def pts_geom(coords):
            return [QgsPointXY(x / xf, y / xf) for x, y in coords]

        def fields(spec):
            fs = QgsFields()
            for name, typ in spec:
                fs.append(QgsField(name, typ))
            return fs

        Dv, Iv, Sv, Bv = QVariant.Double, QVariant.Int, QVariant.String, QVariant.Bool
        L = lambda v: round(v / uf, 3)          # noqa: E731  metres -> design units
        out = {}

        # ---------------------------------------------------- candidates
        cand_fields = fields([
            ("rank", Iv), ("chosen", Bv), ("name", Sv), ("method", Sv), ("arms", Iv),
            ("served_pct", Dv), ("length", Dv), ("problem_length", Dv),
            ("design_grade_pct", Dv), ("max_depth", Dv), ("min_depth", Dv),
            ("outlet_depth", Dv), ("BURY_CHECK", Sv), ("score", Dv)])
        csink, cand_id = self.parameterAsSink(parameters, self.CANDIDATES, context,
                                              cand_fields, QgsWkbTypes.MultiLineString, crs)
        if csink is None:
            raise QgsProcessingException(self.tr("Could not create the candidate layer."))
        for c in res["candidates"][:self.N_CANDIDATES]:
            a = c["attrs"]
            vals = {"rank": a["rank"], "chosen": bool(a["chosen"]), "name": a["name"],
                    "method": a["method"], "arms": a["arms"],
                    "served_pct": a["served_pct"], "length": L(a["length_m"]),
                    "problem_length": L(a["problem_m"]),
                    "design_grade_pct": a["design_grade_pct"],
                    "max_depth": L(a["max_depth_m"]), "min_depth": L(a["min_depth_m"]),
                    "outlet_depth": (L(a["outlet_depth_m"])
                                     if a["outlet_depth_m"] is not None else None),
                    "BURY_CHECK": a["bury_check"], "score": a["score"]}
            f = QgsFeature(cand_fields)
            f.setGeometry(QgsGeometry.fromMultiPolylineXY(
                [pts_geom(arm) for arm in c["coords"]]))
            f.setAttributes([vals[cand_fields.at(i).name()]
                             for i in range(cand_fields.count())])
            csink.addFeature(f, QgsFeatureSink.FastInsert)
        out[self.CANDIDATES] = cand_id
        if context.willLoadLayerOnCompletion(cand_id):
            _POST[cand_id] = _CandidateStyler()
            context.layerToLoadOnCompletionDetails(cand_id).setPostProcessor(_POST[cand_id])


        # ---------------------------------------------------- log
        s = res["summary"]
        feedback.pushInfo("")
        feedback.pushInfo(self.tr("Chosen main: {}").format(s["method"]))
        feedback.pushInfo(self.tr("Main length: {:,.0f} {}").format(
            s["main_length_m"] / uf, u_lbl))
        feedback.pushInfo(self.tr("Drainable area whose laterals reach the main: {:.1f}% "
                                  "(converge first: {:.1f}%, do not reach: {:.1f}%, "
                                  "uphill: {:.1f}%)").format(
            s["served_pct"], s["converge_pct"], s["noreach_pct"], s["uphill_pct"]))
        feedback.pushInfo(self.tr("Lateral run to the main: mean {:,.0f} {}, longest "
                                  "{:,.0f} {}").format(s["mean_lateral_run_m"] / uf, u_lbl,
                                                       s["longest_lateral_run_m"] / uf,
                                                       u_lbl))
        feedback.pushInfo(self.tr("Mean field slope {:.2f}%; contour bearing {:.0f} deg; "
                                  "downhill bearing {:.0f} deg; lateral bearing {:.0f} deg")
                          .format(s["mean_slope_pct"], s["contour_bearing_deg"],
                                  s["downhill_bearing_deg"], s["lateral_bearing_deg"]))
        if s.get("outlet_depth_m") is not None:
            feedback.pushInfo(self.tr("Outlet tile depth: {:.2f} {}").format(
                s["outlet_depth_m"] / uf, u_lbl))
        feedback.pushInfo(self.tr("{} candidate mains scored.").format(s["candidates"]))
        for w in res["warnings"]:
            feedback.pushWarning(w)

        out.update({
            "MAIN_LENGTH": s["main_length_m"] / uf,
            "SERVED_PCT": s["served_pct"],
            "OUTLET_DEPTH": (s["outlet_depth_m"] / uf
                             if s.get("outlet_depth_m") is not None else None),
            "METHOD": s["method"],
            "WARNINGS": "\n".join(res["warnings"]),
        })
        return out

    # ------------------------------------------------------------ outputs
