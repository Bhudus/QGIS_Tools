"""Processing algorithm: automated subsurface drainage layout."""
import html
import math
import os

import numpy as np
from qgis.core import (
    QgsCategorizedSymbolRenderer,
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
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsRectangle,
    QgsRendererCategory,
    QgsRuleBasedRenderer,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon

from .core import (
    ORIENT_ACROSS,
    ORIENT_ALONG,
    PATTERN_HERRINGBONE,
    PATTERN_PARALLEL,
    SHAPE_CONTOUR,
    SHAPE_STRAIGHT,
    Grid,
    LayoutCancelled,
    LayoutError,
    LayoutParams,
    run_layout,
)

PLUGIN_DIR = os.path.dirname(__file__)

PATTERNS = [PATTERN_PARALLEL, PATTERN_HERRINGBONE]
ORIENTS = [ORIENT_ACROSS, ORIENT_ALONG]
SHAPES = [SHAPE_STRAIGHT, SHAPE_CONTOUR]
Z_FACTORS = [1.0, 0.3048, 1200.0 / 3937.0]
UNIT_FACTORS = [1.0, 0.3048]
INTENSITIES = [0.375, 0.500, 0.750, 1.000]   # inch/day, Routine K options A-D

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


class _Styler(QgsProcessingLayerPostProcessorInterface):
    """Style the network: mains and submains red, laterals blue, flagged pipes orange."""

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsVectorLayer):
            return
        root = QgsRuleBasedRenderer.Rule(None)
        styles = [
            ("main", "185,28,28", "1.0", "Main"),
            ("submain", "185,28,28", "0.6", "Submain"),
            ("lateral", "37,99,235", "0.35", "Lateral"),
        ]
        for kind, color, width, label in styles:
            sym = QgsLineSymbol.createSimple({"color": color, "width": width})
            root.appendChild(QgsRuleBasedRenderer.Rule(
                sym, 0, 0, "\"kind\" = '{}' AND (\"flag\" IS NULL OR \"flag\" = '') AND NOT "
                "(\"BURY_CHECK\" LIKE '%grade%' OR \"BURY_CHECK\" LIKE '%Not connected%')"
                .format(kind), label))
        flagged = QgsLineSymbol.createSimple({"color": "217,119,6", "width": "0.5"})
        root.appendChild(QgsRuleBasedRenderer.Rule(
            flagged, 0, 0, "\"flag\" <> ''", "Pipe to check (see flag)"))
        grade = QgsLineSymbol.createSimple({"color": "147,51,234", "width": "0.6"})
        root.appendChild(QgsRuleBasedRenderer.Rule(
            grade, 0, 0, "(\"flag\" IS NULL OR \"flag\" = '') AND "
            "(\"BURY_CHECK\" LIKE '%grade%' OR \"BURY_CHECK\" LIKE '%Not connected%')",
            "Grade problem (see BURY_CHECK)"))
        layer.setRenderer(QgsRuleBasedRenderer(root))
        layer.triggerRepaint()


class IlliniSubsurfaceDrainLayoutAlgorithm(QgsProcessingAlgorithm):
    DEM = "DEM"
    FIELD = "FIELD"
    OUTLET = "OUTLET"
    MAIN = "MAIN"
    SPACING = "SPACING"
    PATTERN = "PATTERN"
    ORIENTATION = "ORIENTATION"
    SHAPE = "SHAPE"
    PARALLELISM = "PARALLELISM"
    UNITS = "UNITS"
    Z_UNITS = "Z_UNITS"
    HERRINGBONE_ANGLE = "HERRINGBONE_ANGLE"
    CONTOUR_GRADE = "CONTOUR_GRADE"
    MIN_LAT_GRADE = "MIN_LAT_GRADE"
    MIN_MAIN_GRADE = "MIN_MAIN_GRADE"
    MIN_COVER = "MIN_COVER"
    MAX_DEPTH = "MAX_DEPTH"
    UPPER_DEPTH = "UPPER_DEPTH"
    LOWER_DEPTH = "LOWER_DEPTH"
    MAX_GRADE = "MAX_GRADE"
    OFFSET = "OFFSET"
    USE_CONST = "USE_CONST"
    CONST_GRADE = "CONST_GRADE"
    LOWER_OUTLET = "LOWER_OUTLET"
    GRADE_BREAK = "GRADE_BREAK"
    SIZE_PIPES = "SIZE_PIPES"
    PIPE_MATERIAL = "PIPE_MATERIAL"
    INTENSITY = "INTENSITY"
    ASSIGNED_INTENSITY = "ASSIGNED_INTENSITY"
    USE_ASSIGNED = "USE_ASSIGNED"
    ORDER_COEFFS = "ORDER_COEFFS"
    SIZING_SLOPE = "SIZING_SLOPE"
    OUTLET_INVERT = "OUTLET_INVERT"
    SETBACK = "SETBACK"
    CELL_SIZE = "CELL_SIZE"
    SMOOTH = "SMOOTH"
    MAIN_STOP = "MAIN_STOP"
    SUBMAIN = "SUBMAIN"
    NETWORK = "NETWORK"
    SURFACE_LAYOUT = "SURFACE_LAYOUT"
    REPORT = "REPORT"

    # ------------------------------------------------------------ metadata
    def tr(self, s):
        return QCoreApplication.translate("IlliniSubsurfaceDrainLayout", s)

    def createInstance(self):
        return IlliniSubsurfaceDrainLayoutAlgorithm()

    def name(self):
        return "illinisubsurfacedrainlayout"

    def displayName(self):
        return self.tr("Illini subsurface drain layout")

    def group(self):
        return ""

    def groupId(self):
        return ""

    def icon(self):
        return QIcon(os.path.join(PLUGIN_DIR, "icon.png"))

    def shortHelpString(self):
        return self.tr(
            "<p>Lays out a subsurface (tile) drainage network for a field from a DEM, "
            "the field boundary, an outlet location and the lateral spacing.</p>"
            "<p><b>Pattern</b><br>"
            "<i>Parallel / gridiron</i>: one main runs from the outlet perpendicular to "
            "the laterals — up the slope when laterals run across it, or as a header "
            "across the low side when laterals run down it.<br>"
            "<i>Herringbone</i>: the main and any submains follow the low ground "
            "(drainage-tree thalwegs) from the outlet; laterals enter from both sides.</p>"
            "<p><b>Main line</b> (optional): draw your own main, and any submains, in a "
            "line layer. The line ending nearest the outlet becomes the main and flows to "
            "that end; each other line becomes a submain flowing to its end nearest the "
            "main (or another submain), which it joins. Lines can run outside the field, "
            "for example out to a ditch. With a main line the outlet is optional: the "
            "lowest end of the lines is used. The pattern then only sets how laterals "
            "leave the main.</p>"
            "<p><b>Lateral orientation</b><br>"
            "<i>Across the slope</i>: laterals run nearly parallel to the contours. With "
            "contour-following shape they hold the target lateral grade along the "
            "contour.<br>"
            "<i>Down the slope</i>: laterals run nearly perpendicular to the contours "
            "(along the fall line).</p>"
            "<p><b>Lateral shape</b><br>"
            "<i>Straight</i>: each lateral is a straight line (herringbone laterals leave "
            "the main at the herringbone angle).<br>"
            "<i>Contour-following</i>: laterals bend with the smoothed terrain; spacing "
            "is held by evenly spaced streamline placement. <i>Parallelism</i> steers each "
            "lateral to run parallel to its neighbour at exactly one spacing: 0 follows "
            "the terrain freely, 1 makes each lateral an offset of its neighbour; 0.9 keeps "
            "laterals near parallel while still bending with the land.</p>"
            "<p><b>Tile burying</b> follows Routine J (Tile Burying System) of the Illini "
            "Drainage Tools. Every pipe is cut at its junctions and at grade breaks, and "
            "the segments are buried from the outlet upstream. The outlet tile sits at the "
            "middle of the target band (upper and lower tile depth) plus the offset; each "
            "segment starts from the tile already set at its downstream junction, aims for "
            "the middle of the band at its upstream end, keeps its slope between the "
            "minimum and maximum (or at the constant slope), and stays between the "
            "absolute upper and lower tile depths. <i>Lower the outlet</i> deepens the "
            "outlet, down to the lower tile depth, when low ground upstream cannot keep "
            "the minimum slope. Results are in the Routine J fields ElevDepth1/2, "
            "BuryDepth1/2, InSlope, OutSlope and BURY_CHECK (1 = upstream end of the line, "
            "2 = downstream end).</p>"
            "<p><b>Pipe sizing</b> follows Routine K (Network Pipe Sizing) of the Illini "
            "Drainage Tools, with the Strahler tile order of Routine G. Each line is sized "
            "for the flow from all tile upstream (FLOW_LENGTH x drain spacing x drainage "
            "coefficient), at the flattest burying slope among its segments (InSlope or "
            "OutSlope), with Manning's equation for a full pipe. The drainage coefficient "
            "is the intensity for order-1 lines and 1.25 x intensity above, or one value per "
            "tile order if you list them. Single-wall pipe uses n = 0.015 up to 8 in, 0.017 "
            "to 12 in and 0.020 above; the drain spacing is rounded to the nearest foot; and "
            "TILE_ORDER is the standard Strahler order. Results are in the Routine K fields D_COEFF "
            "(in/day), D_AREA (acres), CUM_SEG_FLOW (cfs), ACTUAL_SIZE and NOMINAL (inches), "
            "with TILE_ORDER, FLOW_LENGTH (ft) and SIZE_SLOPE. The drain spacing is the "
            "lateral spacing.</p>"
            "<p><b>TileLength</b> is each line's length rounded to the nearest 5 ft, or the "
            "nearest metre.</p>"
            "<p>Long connectors, laterals with no collector downstream and cover outside "
            "the absolute depths between grade breaks are flagged in the <i>flag</i> "
            "field.</p>"
            "<p><b>Output</b>: one connected network. Mains, submains and laterals are "
            "split at every junction into links that run node to node, with "
            "<i>from_node</i>, <i>to_node</i> and the downstream link (<i>ds_link</i>, 0 at "
            "the outlet). <i>upstream_length</i> is the pipe length draining through each "
            "link and <i>drained_area</i> the area served by the laterals upstream "
            "(lateral length x spacing; hectares, or acres in feet mode), for pipe "
            "sizing.</p>"
            "<p>All lengths, depths and elevations on this form and in the outputs are in "
            "the chosen design units. The DEM must be in a projected CRS. Grades are in "
            "percent. Depth defaults are in feet; enter metre values if you design in "
            "metres.</p>"
            "<p>The burying and sizing schemes are ported from the Illini Drainage Tools by Anamelechi "
            "Falasy and Richard Cooke, University of Illinois Urbana-Champaign (GPL v3).</p>")

    # ------------------------------------------------------------ parameters
    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, self.tr("Elevation (DEM)")))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.FIELD, self.tr("Field boundary"), [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterPoint(
            self.OUTLET, self.tr("Outlet location [optional if a main line is given]"),
            optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.MAIN, self.tr("Main line(s) [optional; leave empty to route the main "
                               "automatically]"),
            [QgsProcessing.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.UNITS, self.tr("Design units (spacing, depths, outputs)"),
            options=[self.tr("Metres"), self.tr("Feet")], defaultValue=1))
        self.addParameter(QgsProcessingParameterNumber(
            self.SPACING, self.tr("Lateral spacing"),
            QgsProcessingParameterNumber.Double, defaultValue=80.0, minValue=1.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.PATTERN, self.tr("Layout pattern"),
            options=[self.tr("Parallel / gridiron"), self.tr("Herringbone")],
            defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.ORIENTATION, self.tr("Lateral orientation"),
            options=[self.tr("Across the slope (near parallel to contours)"),
                     self.tr("Down the slope (near perpendicular to contours)")],
            defaultValue=1))
        self.addParameter(QgsProcessingParameterEnum(
            self.SHAPE, self.tr("Lateral shape"),
            options=[self.tr("Straight"),
                     self.tr("Contour-following (bend with the terrain)")],
            defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.PARALLELISM,
            self.tr("Contour-following laterals: parallelism "
                    "(0 = follow terrain, 1 = strictly parallel)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.9,
            minValue=0.0, maxValue=1.0))
        # tile burying, after Routine J (Tile Burying System) of the Illini Drainage Tools
        self.addParameter(QgsProcessingParameterNumber(
            self.UPPER_DEPTH, self.tr("Burying: upper tile depth (target band)"),
            QgsProcessingParameterNumber.Double, defaultValue=3.25, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.LOWER_DEPTH, self.tr("Burying: lower tile depth (target band)"),
            QgsProcessingParameterNumber.Double, defaultValue=4.25, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_COVER, self.tr("Burying: absolute upper tile depth (minimum cover)"),
            QgsProcessingParameterNumber.Double, defaultValue=2.5, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_DEPTH, self.tr("Burying: absolute lower tile depth (maximum depth)"),
            QgsProcessingParameterNumber.Double, defaultValue=6.0, minValue=0.1))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_GRADE, self.tr("Burying: maximum slope (%)"),
            QgsProcessingParameterNumber.Double, defaultValue=5.0, minValue=0.01,
            maxValue=100.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_LAT_GRADE, self.tr("Burying: minimum slope, laterals (%)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.1, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_MAIN_GRADE, self.tr("Burying: minimum slope, mains and submains (%)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.1, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.OFFSET, self.tr("Burying: offset depth at the outlet (+ lowers the outlet)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.USE_CONST, self.tr("Burying: use constant slope (flat terrain only)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterNumber(
            self.CONST_GRADE, self.tr("Burying: constant slope (%)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.5, minValue=0.0,
            maxValue=100.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.LOWER_OUTLET,
            self.tr("Burying: lower the outlet for low ground (down to the lower tile depth)"),
            defaultValue=False))
        # pipe sizing, after Routine K (Network Pipe Sizing) of the Illini Drainage Tools
        self.addParameter(QgsProcessingParameterBoolean(
            self.SIZE_PIPES, self.tr("Sizing: size the pipes"), defaultValue=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.PIPE_MATERIAL, self.tr("Sizing: pipe material"),
            options=[self.tr("Single Wall"), self.tr("Smooth Wall"),
                     self.tr("Clay or Concrete")], defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.INTENSITY, self.tr("Sizing: drainage intensity [inch/day]"),
            options=["A: 0.375", "B: 0.500", "C: 0.750", "D: 1.000"], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.ASSIGNED_INTENSITY, self.tr("Sizing: E: others = assign intensity [inch/day]"),
            QgsProcessingParameterNumber.Double, defaultValue=2.5, minValue=0.0,
            maxValue=100.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.USE_ASSIGNED, self.tr("Sizing: use assigned intensity"), defaultValue=False))
        self.addParameter(QgsProcessingParameterNumber(
            self.OUTLET_INVERT,
            self.tr("Outlet invert elevation, in DEM elevation units [optional]"),
            QgsProcessingParameterNumber.Double, optional=True))

        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.Z_UNITS, self.tr("DEM elevation units"),
            options=[self.tr("Metres"), self.tr("International feet"),
                     self.tr("US survey feet")], defaultValue=2)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.CONTOUR_GRADE, self.tr("Target grade for contour-following laterals (%)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.2, minValue=0.01)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.HERRINGBONE_ANGLE,
            self.tr("Herringbone angle between lateral and main (degrees)"),
            QgsProcessingParameterNumber.Double, defaultValue=60.0,
            minValue=15.0, maxValue=90.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.GRADE_BREAK, self.tr("Burying: longest segment between grade breaks"),
            QgsProcessingParameterNumber.Double, defaultValue=50.0, minValue=0.1)))
        self.addParameter(_advanced(QgsProcessingParameterString(
            self.ORDER_COEFFS,
            self.tr("Sizing: drainage coefficient per tile order, separated by ',' "
                    "[inch/day; optional, default by system]"),
            optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.SIZING_SLOPE, self.tr("Sizing: burying slope used"),
            options=[self.tr("InSlope"), self.tr("OutSlope")], defaultValue=0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SETBACK, self.tr("Boundary setback [optional, default half the spacing]"),
            QgsProcessingParameterNumber.Double, optional=True, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SMOOTH, self.tr("Terrain smoothing radius [optional, default the spacing]"),
            QgsProcessingParameterNumber.Double, optional=True, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.CELL_SIZE, self.tr("Analysis cell size [optional, automatic]"),
            QgsProcessingParameterNumber.Double, optional=True, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MAIN_STOP,
            self.tr("Main/submain ends where its drainage area falls below (% of field)"),
            QgsProcessingParameterNumber.Double, defaultValue=2.0, minValue=0.1,
            maxValue=50.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SUBMAIN,
            self.tr("Herringbone: branches draining more than this become submains "
                    "(% of field, 0 = none)"),
            QgsProcessingParameterNumber.Double, defaultValue=10.0, minValue=0.0,
            maxValue=100.0)))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.NETWORK, self.tr("Drainage network"), QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.SURFACE_LAYOUT, self.tr("Surface Layout"),
            QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.REPORT, self.tr("Layout report"), self.tr("HTML files (*.html)"),
            optional=True, createByDefault=False))

        for key, desc in (("TOTAL_LENGTH", "Total pipe length"),
                          ("LATERAL_LENGTH", "Lateral length"),
                          ("COLLECTOR_LENGTH", "Main and submain length"),
                          ("COVERAGE_PCT", "Drainable area within half a spacing of a pipe (%)"),
                          ("N_LATERALS", "Number of laterals"),
                          ("OUTLET_DEPTH", "Outlet tile depth")):
            self.addOutput(QgsProcessingOutputNumber(key, self.tr(desc)))
        self.addOutput(QgsProcessingOutputString("WARNINGS", self.tr("Warnings")))

    # ------------------------------------------------------------ inputs
    @staticmethod
    def _rings(geom, xf):
        """Polygon rings (outer and holes) in metre coordinates."""
        rings = []
        if geom.isMultipart():
            polys = geom.asMultiPolygon()
        else:
            polys = [geom.asPolygon()]
        for poly in polys:
            for ring in poly:
                if len(ring) >= 3:
                    rings.append(np.array([[p.x() * xf, p.y() * xf] for p in ring]))
        return rings

    @staticmethod
    def _read_dem(layer, bounds, cell_map, nrows, ncols, feedback):
        """Resample the DEM onto the analysis grid (bilinear). Returns (array, valid)."""
        xmin, ymin, xmax, ymax = bounds
        arr = None
        if layer.providerType() == "gdal":
            try:
                from osgeo import gdal
                src = layer.dataProvider().dataSourceUri()
                tmp = "/vsimem/subsurface_drain_layout_dem.tif"
                ds = gdal.Warp(tmp, src, format="GTiff",
                               outputBounds=(xmin, ymin, xmax, ymax),
                               width=ncols, height=nrows, resampleAlg="bilinear",
                               dstNodata=-99999.0, outputType=gdal.GDT_Float32)
                if ds is not None:
                    arr = ds.GetRasterBand(1).ReadAsArray().astype(float)
                    ds = None
                gdal.Unlink(tmp)
            except Exception as e:  # noqa: BLE001
                feedback.pushWarning("GDAL resampling failed ({}); falling back to "
                                     "nearest-neighbour reading.".format(e))
                arr = None
            if arr is not None:
                valid = np.isfinite(arr) & (arr > -99998.0)
                return arr, valid
        prov = layer.dataProvider()
        block = prov.block(1, QgsRectangle(xmin, ymin, xmax, ymax), ncols, nrows)
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
        xf = _meters_factor(crs)                       # map units -> m
        uf = UNIT_FACTORS[self.parameterAsEnum(parameters, self.UNITS, context)]
        zf = Z_FACTORS[self.parameterAsEnum(parameters, self.Z_UNITS, context)]

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

        # field geometry, in the DEM CRS
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
        if source.featureCount() > 1:
            feedback.pushInfo(self.tr("{} field polygons merged into one field.")
                              .format(source.featureCount()))
        outlet = None
        if parameters.get(self.OUTLET) not in (None, ""):
            outlet = self.parameterAsPoint(parameters, self.OUTLET, context, crs)

        # user-supplied main and submains, in the DEM CRS
        mains = []
        main_bb = None
        main_src = None
        if parameters.get(self.MAIN) not in (None, ""):
            main_src = self.parameterAsSource(parameters, self.MAIN, context)
        if main_src is not None:
            mtr = QgsCoordinateTransform(main_src.sourceCrs(), crs, context.transformContext())
            for f in main_src.getFeatures():
                g = QgsGeometry(f.geometry())
                if g.isEmpty():
                    continue
                g.transform(mtr)
                parts = g.asMultiPolyline() if g.isMultipart() else [g.asPolyline()]
                for part in parts:
                    if len(part) >= 2:
                        mains.append([(p.x(), p.y()) for p in part])
                        b = QgsGeometry.fromPolylineXY(part).boundingBox()
                        main_bb = b if main_bb is None else _union_box(main_bb, b)
            if not mains:
                raise QgsProcessingException(self.tr("The main line layer has no lines."))
            feedback.pushInfo(self.tr("Using {} supplied line(s): the one ending nearest the "
                                      "outlet is the main, the rest are submains.")
                              .format(len(mains)))
        if outlet is None and not mains:
            raise QgsProcessingException(self.tr(
                "Give an outlet location, a main line, or both."))

        # analysis grid
        bb = field.boundingBox()
        if main_bb is not None:
            bb = _union_box(bb, main_bb)
        if outlet is not None:
            bb = _union_box(bb, QgsRectangle(outlet.x(), outlet.y(), outlet.x(), outlet.y()))
        area_m2 = field.area() * xf * xf
        dem_res_m = abs(dem_layer.rasterUnitsPerPixelX()) * xf
        if cell_in:
            cell_m = cell_in * uf
        else:
            cell_m = max(dem_res_m, spacing / 12.0, 0.5)
            cell_m = min(cell_m, spacing / 5.0)
            cell_m = max(cell_m, math.sqrt(area_m2 / 2.0e6))
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
        if nrows * ncols > 12_000_000:
            raise QgsProcessingException(self.tr(
                "The analysis grid would have {:,} cells; set a larger analysis cell size."
            ).format(nrows * ncols))
        feedback.pushInfo(self.tr("Analysis grid: {} x {} cells of {:.2f} {}.")
                          .format(ncols, nrows, cell_m / uf, "m" if uf == 1.0 else "ft"))

        dem, valid = self._read_dem(dem_layer, (xmin, ymin, xmax, ymax), cell_map,
                                    nrows, ncols, feedback)
        if not valid.any():
            raise QgsProcessingException(self.tr("The DEM has no data over the field."))
        dem = dem * zf
        grid = Grid(xmin * xf, ymax * xf, cell_m, nrows, ncols)

        order_coeffs = None
        oc_text = (parameters.get(self.ORDER_COEFFS) or "").strip()
        if oc_text:
            try:
                order_coeffs = [float(x.strip()) for x in oc_text.split(",")]
            except ValueError:
                raise QgsProcessingException(self.tr(
                    "Drainage coefficients per tile order must be numbers separated by "
                    "commas, e.g. 0.375, 0.47, 0.5."))

        params = LayoutParams(
            spacing=spacing,
            units="m" if uf == 1.0 else "ft",
            pattern=PATTERNS[self.parameterAsEnum(parameters, self.PATTERN, context)],
            orientation=ORIENTS[self.parameterAsEnum(parameters, self.ORIENTATION, context)],
            shape=SHAPES[self.parameterAsEnum(parameters, self.SHAPE, context)],
            parallelism=num(self.PARALLELISM),
            herringbone_angle=num(self.HERRINGBONE_ANGLE),
            contour_grade=num(self.CONTOUR_GRADE) / 100.0,
            min_lateral_grade=num(self.MIN_LAT_GRADE) / 100.0,
            min_main_grade=num(self.MIN_MAIN_GRADE) / 100.0,
            min_cover=num(self.MIN_COVER) * uf,
            max_depth=num(self.MAX_DEPTH) * uf,
            target_upper=num(self.UPPER_DEPTH) * uf,
            target_lower=num(self.LOWER_DEPTH) * uf,
            max_grade=num(self.MAX_GRADE) / 100.0,
            outlet_offset=num(self.OFFSET) * uf,
            use_constant=self.parameterAsBool(parameters, self.USE_CONST, context),
            constant_grade=num(self.CONST_GRADE) / 100.0,
            lower_outlet=self.parameterAsBool(parameters, self.LOWER_OUTLET, context),
            grade_break=num(self.GRADE_BREAK) * uf,
            size_pipes=self.parameterAsBool(parameters, self.SIZE_PIPES, context),
            pipe_material=self.parameterAsEnum(parameters, self.PIPE_MATERIAL, context),
            intensity=(num(self.ASSIGNED_INTENSITY)
                       if self.parameterAsBool(parameters, self.USE_ASSIGNED, context)
                       else INTENSITIES[self.parameterAsEnum(parameters, self.INTENSITY,
                                                             context)]),
            order_coeffs=order_coeffs,
            sizing_slope="in" if self.parameterAsEnum(
                parameters, self.SIZING_SLOPE, context) == 0 else "out",
            outlet_invert=outlet_inv * zf if outlet_inv is not None else None,
            setback=setback * uf if setback is not None else None,
            smooth_radius=smooth_m,
            main_stop_frac=num(self.MAIN_STOP) / 100.0,
            submain_frac=num(self.SUBMAIN) / 100.0,
        )

        def progress(pct, msg):
            feedback.setProgress(pct)
            feedback.setProgressText(msg)

        try:
            res = run_layout(dem, valid, grid, self._rings(field, xf),
                             None if outlet is None else (outlet.x() * xf, outlet.y() * xf),
                             params, progress=progress, cancel=feedback.isCanceled,
                             mains=[[(x * xf, y * xf) for x, y in m] for m in mains])
        except LayoutCancelled:
            return {}
        except LayoutError as e:
            raise QgsProcessingException(str(e))

        # ---------------------------------------------------- outputs
        lengths = ("length_m", "ground_us", "ground_ds", "invert_us", "invert_ds",
                   "max_depth_m", "min_depth_m", "depth_ds_m")
        u_lbl = "m" if uf == 1.0 else "ft"

        def conv(attrs):
            out = dict(attrs)
            for k in lengths:
                out[k] = round(attrs[k] / uf, 3)
            return out

        def fields(first):
            fs = QgsFields()
            for name, typ in first:
                fs.append(QgsField(name, typ))
            return fs

        D, I, S = QVariant.Double, QVariant.Int, QVariant.String
        net_fields = fields([
            ("id", I), ("pipe_id", I), ("kind", S), ("pipe", S), ("from_node", I), ("to_node", I),
            ("ds_link", I), ("length", D), ("TileLength", I), ("ground_us", D), ("ground_ds", D),
            ("terrain_grade_pct", D), ("invert_us", D), ("invert_ds", D),
            ("design_grade_pct", D), ("max_depth", D), ("min_depth", D), ("depth_ds", D),
            ("upstream_length", D), ("drained_area", D),
            # Routine J (Illini Drainage Tools) fields: 1 = first vertex (upstream end)
            ("ElevDepth1", D), ("BuryDepth1", D), ("ElevDepth2", D), ("BuryDepth2", D),
            ("InSlope", D), ("OutSlope", D), ("BURY_CHECK", S),
            # Routine K (Illini Drainage Tools) fields, in Routine K's US units
            ("TILE_ORDER", I), ("FLOW_LENGTH", D), ("SIZE_SLOPE", D), ("D_COEFF", D),
            ("D_AREA", D), ("CUM_SEG_FLOW", D), ("ACTUAL_SIZE", D), ("NOMINAL", I),
            ("flag", S)])
        area_f = 1.0 if uf == 1.0 else 2.4710538     # ha, or acres in feet mode
        rename = {"length_m": "length", "max_depth_m": "max_depth", "min_depth_m": "min_depth",
                  "depth_ds_m": "depth_ds"}

        sink, net_id = self.parameterAsSink(parameters, self.NETWORK, context,
                                            net_fields, QgsWkbTypes.LineString, crs)
        id_fields = fields([("id", I)])
        id_sink, ids_id = self.parameterAsSink(parameters, self.SURFACE_LAYOUT, context,
                                               id_fields, QgsWkbTypes.LineString, crs)
        if sink is None or id_sink is None:
            raise QgsProcessingException(self.tr("Could not create the output layers."))
        for it in res["network"]:
            a = conv(it["attrs"])
            a = {rename.get(k, k): v for k, v in a.items()}
            a["upstream_length"] = round(it["attrs"]["upstream_m"] / uf, 1)
            a["drained_area"] = round(it["attrs"]["drained_ha"] * area_f, 3)
            # length rounded to the nearest 5 ft, or the nearest metre
            step = 5.0 if uf != 1.0 else 1.0
            a["TileLength"] = int(math.floor(a["length"] / step + 0.5) * step)
            a["ElevDepth1"] = a["invert_us"]
            a["BuryDepth1"] = round(a["ground_us"] - a["invert_us"], 3)
            a["ElevDepth2"] = a["invert_ds"]
            a["BuryDepth2"] = round(a["ground_ds"] - a["invert_ds"], 3)
            a["InSlope"] = it["attrs"]["in_slope"]
            a["OutSlope"] = it["attrs"]["out_slope"]
            a["BURY_CHECK"] = it["attrs"]["bury_check"]
            f = QgsFeature(net_fields)
            f.setGeometry(QgsGeometry.fromPolylineXY(
                [QgsPointXY(x / xf, y / xf) for x, y in it["coords"]]))
            f.setAttributes([a.get(net_fields.at(i).name()) for i in range(net_fields.count())])
            sink.addFeature(f, QgsFeatureSink.FastInsert)
            g = QgsFeature(id_fields)          # same line, id only
            g.setGeometry(f.geometry())
            g.setAttributes([a["id"]])
            id_sink.addFeature(g, QgsFeatureSink.FastInsert)

        if context.willLoadLayerOnCompletion(net_id):
            _POST[net_id] = _Styler()
            context.layerToLoadOnCompletionDetails(net_id).setPostProcessor(_POST[net_id])

        s = res["summary"]
        feedback.pushInfo("")
        feedback.pushInfo(self.tr("Field area: {:.2f} ha").format(s["field_area_ha"]))
        feedback.pushInfo(self.tr("Laterals: {} totalling {:,.0f} {}").format(
            s["laterals"], s["lateral_length_m"] / uf, u_lbl))
        feedback.pushInfo(self.tr("Mains/submains: {:,.0f} {}").format(
            s["collector_length_m"] / uf, u_lbl))
        feedback.pushInfo(self.tr("Drainable area within half a spacing of a pipe: {:.1f}%")
                          .format(s["coverage_pct"]))
        feedback.pushInfo(self.tr("Mean field slope: {:.2f}%; dominant contour bearing "
                                  "{:.0f} degrees").format(s["mean_slope_pct"],
                                                           s["contour_azimuth_deg"]))
        if s.get("outlet_depth_m") is not None:
            od, sd = s["outlet_depth_m"] / uf, s["outlet_start_depth_m"] / uf
            if od > sd + 1e-6:
                feedback.pushInfo(self.tr("Outlet lowered from {:.2f} {} to {:.2f} {} deep to "
                                          "serve low ground.").format(sd, u_lbl, od, u_lbl))
            feedback.pushInfo(self.tr("Outlet tile depth: {:.2f} {}.").format(od, u_lbl))
        feedback.pushInfo(self.tr("Buried {} segments between junctions and grade breaks.")
                          .format(s["bury_segments"]))
        if s.get("length_by_size_m"):
            feedback.pushInfo(self.tr("Pipe by nominal size: {}").format("; ".join(
                "{} in: {:,.0f} {}".format(k, v / uf, u_lbl)
                for k, v in s["length_by_size_m"].items())))
        for w in res["warnings"]:
            feedback.pushWarning(w)

        report = self.parameterAsFileOutput(parameters, self.REPORT, context)
        out = {self.NETWORK: net_id, self.SURFACE_LAYOUT: ids_id,
               "TOTAL_LENGTH": s["total_length_m"] / uf,
               "LATERAL_LENGTH": s["lateral_length_m"] / uf,
               "COLLECTOR_LENGTH": s["collector_length_m"] / uf,
               "COVERAGE_PCT": s["coverage_pct"],
               "N_LATERALS": s["laterals"],
               "OUTLET_DEPTH": (s["outlet_depth_m"] / uf
                                if s.get("outlet_depth_m") is not None else None),
               "WARNINGS": "\n".join(res["warnings"])}
        if report:
            self._write_report(report, res, params, uf, u_lbl)
            out[self.REPORT] = report
        return out

    def _write_report(self, path, res, params, uf, u):
        s = res["summary"]
        rows = [
            ("Field area", "{:.2f} ha".format(s["field_area_ha"])),
            ("Pattern / orientation / shape", "{} / {} / {}".format(
                params.pattern, params.orientation, params.shape)),
            ("Lateral spacing", "{:.1f} {}".format(params.spacing / uf, u)),
            ("Laterals", "{} ({:,.0f} {})".format(s["laterals"], s["lateral_length_m"] / uf, u)),
            ("Mains and submains", "{:,.0f} {}".format(s["collector_length_m"] / uf, u)),
            ("Total pipe", "{:,.0f} {}".format(s["total_length_m"] / uf, u)),
            ("Pipe per hectare", "{:,.0f} {}".format(s["length_per_ha_m"] / uf, u)),
            ("Drainable area within half a spacing of a pipe",
             "{:.1f}%".format(s["coverage_pct"])),
            ("Whole field within half a spacing of a pipe",
             "{:.1f}%".format(s["field_coverage_pct"])),
            ("Mean field slope", "{:.2f}%".format(s["mean_slope_pct"])),
            ("Outlet tile depth", "{:.2f} {}".format(s["outlet_depth_m"] / uf, u)
             if s.get("outlet_depth_m") is not None else "–"),
            ("Burying segments / flagged in BURY_CHECK",
             "{} / {}".format(s["bury_segments"], s["bury_flagged"])),
            ("Dominant contour bearing", "{:.0f}°".format(s["contour_azimuth_deg"])),
            ("Laterals deeper than the maximum", str(s["deep_laterals"])),
            ("Laterals without a collector", str(s["unconnected_laterals"])),
        ]
        rows += [("Pipe, {} in nominal".format(k), "{:,.0f} {}".format(v / uf, u))
                 for k, v in s.get("length_by_size_m", {}).items()]
        cols = "".join(
            "<tr><td>{}</td><td>{}</td><td>{:,.0f} {}</td><td>{:.2f}</td><td>{:.2f} {}</td>"
            "<td>{}</td></tr>".format(
                html.escape(c["attrs"]["name"]), html.escape(c["attrs"]["flows_to"]),
                c["attrs"]["length_m"] / uf, u, c["attrs"]["design_grade_pct"],
                c["attrs"]["max_depth_m"] / uf, u, html.escape(c["attrs"]["flag"]))
            for c in res["collectors"])
        warn = "".join("<li>{}</li>".format(html.escape(w)) for w in res["warnings"]) \
            or "<li>None</li>"
        css = ("body{font-family:sans-serif;margin:24px;color:#222}"
               "table{border-collapse:collapse;margin:8px 0 20px}"
               "td,th{border:1px solid #ccc;padding:4px 10px;text-align:left}"
               "th{background:#f2f2f2}")
        summary = "".join("<tr><th>{}</th><td>{}</td></tr>".format(
            html.escape(a), html.escape(b)) for a, b in rows)
        doc = (
            "<html><head><meta charset='utf-8'><title>Illini subsurface drain layout report</title>"
            "<style>" + css + "</style></head><body>"
            "<h2>Illini subsurface drain layout</h2><table>" + summary + "</table>"
            "<h3>Mains and submains</h3><table><tr><th>Name</th><th>Flows to</th>"
            "<th>Length</th><th>Design grade %</th><th>Max depth</th><th>Flag</th></tr>"
            + cols + "</table><h3>Warnings</h3><ul>" + warn + "</ul></body></html>")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(doc)
