"""Processing algorithm: water management zones and control-structure placement."""
import math
import os

import numpy as np
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsFillSymbol,
    QgsGeometry,
    QgsLineString,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsMapLayer,
    QgsPalettedRasterRenderer,
    QgsPoint,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingFeatureSourceDefinition,
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
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProject,
    QgsRasterLayer,
    QgsRendererCategory,
    QgsTextFormat,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QColor, QFont, QIcon

from .core.zones import boundary_crossings, lowest_points
from .core import (
    STATUS_LABELS,
    ST_DEEP,
    ST_FREE,
    ST_OK,
    ST_UNDRAINED,
    ST_WET,
    BandZoning,
    Grid,
    NetworkError,
    NetworkZoning,
    ZoneParams,
    band_options,
    bilinear,
    build_network,
    fill_tile_z,
    options_table,
    smooth_zones,
)

PLUGIN_DIR = os.path.dirname(__file__)
Z_FACTORS = [1.0, 0.3048, 1200.0 / 3937.0]     # DEM elevation units -> metres
UNIT_FACTORS = [1.0, 0.3048]                    # design units -> metres
MAX_CELLS = 3_000_000

STATUS_COLORS = {
    ST_OK: "#1a9850",
    ST_DEEP: "#d8b365",
    ST_FREE: "#4575b4",
    ST_WET: "#d73027",
    ST_UNDRAINED: "#bdbdbd",
}
ZONE_PALETTE = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b",
                "#e377c2", "#7f7f7f", "#bcbd22", "#17becf", "#aec7e8", "#ffbb78",
                "#98df8a", "#ff9896", "#c5b0d5", "#c49c94", "#f7b6d2", "#dbdb8d",
                "#9edae5", "#393b79"]

_POST = {}   # keep post-processors alive until QGIS has used them


# Settings of the last run, kept for as long as the project stays open: the
# plugin clears them when a new project is started or another one is loaded
# (QgsProject.cleared). Nothing is written to the QGIS settings or the project.
_LAST_RUN = {}
LAYER_INPUTS = ("DEM", "FIELD", "NETWORK")


def reset_saved_settings():
    """Forget the last run's settings (back to the plugin defaults)."""
    _LAST_RUN.clear()


def _layer_still_there(value):
    """True when a remembered layer input still names a layer or file."""
    if isinstance(value, QgsProcessingFeatureSourceDefinition):
        value = value.source.staticValue()
    if isinstance(value, QgsMapLayer):
        return True
    if not isinstance(value, str) or not value:
        return False
    if QgsProject.instance().mapLayer(value) is not None:
        return True
    if QgsProject.instance().mapLayersByName(value):
        return True
    return os.path.exists(value.split("|")[0])


def _meters_factor(crs):
    try:
        from qgis.core import Qgis
        return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), Qgis.DistanceUnit.Meters)
    except (ImportError, AttributeError):
        return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), QgsUnitTypes.DistanceMeters)


def _advanced(param):
    try:
        from qgis.core import Qgis
        param.setFlags(param.flags() | Qgis.ProcessingParameterFlag.Advanced)
    except (ImportError, AttributeError):
        param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
    return param


def _zone_color(z):
    return QColor(ZONE_PALETTE[(int(z) - 1) % len(ZONE_PALETTE)])


def _labels(layer, expr, size=8.0, color="#111111", buffer=True, placement=None):
    s = QgsPalLayerSettings()
    s.fieldName = expr
    s.isExpression = True
    fmt = QgsTextFormat()
    f = QFont()
    f.setBold(True)
    fmt.setFont(f)
    fmt.setSize(size)
    fmt.setColor(QColor(color))
    if buffer:
        b = fmt.buffer()
        b.setEnabled(True)
        b.setSize(0.8)
        b.setColor(QColor("white"))
        fmt.setBuffer(b)
    s.setFormat(fmt)
    if placement is not None:
        s.placement = placement
    layer.setLabeling(QgsVectorLayerSimpleLabeling(s))
    layer.setLabelsEnabled(True)


class _ZoneStyler(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, zones):
        super().__init__()
        self.zones = zones

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsVectorLayer):
            return
        cats = []
        for z in self.zones:
            c = _zone_color(z)
            sym = QgsFillSymbol.createSimple({
                "color": "{},{},{},110".format(c.red(), c.green(), c.blue()),
                "outline_color": "{},{},{},255".format(c.red(), c.green(), c.blue()),
                "outline_width": "0.4"})
            cats.append(QgsRendererCategory(int(z), sym, "Zone {}".format(z)))
        layer.setRenderer(QgsCategorizedSymbolRenderer("zone", cats))
        _labels(layer, "'Zone ' || \"zone\" || '\\nboards ' || "
                       "format_number(\"board_elev\", 2)", size=8.0)
        layer.triggerRepaint()


class _StructureStyler(QgsProcessingLayerPostProcessorInterface):
    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsVectorLayer):
            return
        sym = QgsMarkerSymbol.createSimple({"name": "square", "color": "249,115,22",
                                            "outline_color": "0,0,0", "size": "3.2"})
        from qgis.core import QgsRuleBasedRenderer
        root = QgsRuleBasedRenderer.Rule(None)
        root.appendChild(QgsRuleBasedRenderer.Rule(
            sym, 0, 0, "\"flag\" IS NULL OR \"flag\" = ''", "Control structure"))
        bad = QgsMarkerSymbol.createSimple({"name": "square", "color": "220,38,38",
                                            "outline_color": "0,0,0", "size": "3.6"})
        root.appendChild(QgsRuleBasedRenderer.Rule(
            bad, 0, 0, "\"flag\" <> ''", "Structure to check (see flag)"))
        layer.setRenderer(QgsRuleBasedRenderer(root))
        _labels(layer, "'S' || \"struct_id\"", size=9.0)
        layer.triggerRepaint()


class _PointStyler(QgsProcessingLayerPostProcessorInterface):
    """Lowest points: blue downward triangles; boundary crossings: diamonds, red
    where the water leaves the field, green where it enters."""

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsVectorLayer):
            return
        from qgis.core import QgsRuleBasedRenderer
        root = QgsRuleBasedRenderer.Rule(None)
        for expr, shape, color, label in (
                ("\"type\" = 'lowest point'", "triangle", "37,99,235",
                 "Lowest point of a zone"),
                ("\"type\" = 'boundary crossing' AND \"direction\" = 'leaves the field'",
                 "diamond", "220,38,38", "Pipe leaves the field"),
                ("\"type\" = 'boundary crossing' AND \"direction\" = 'enters the field'",
                 "diamond", "22,163,74", "Pipe enters the field")):
            sym = QgsMarkerSymbol.createSimple({"name": shape, "color": color,
                                                "outline_color": "0,0,0", "size": "3.4",
                                                "angle": "180" if shape == "triangle" else "0"})
            root.appendChild(QgsRuleBasedRenderer.Rule(sym, 0, 0, expr, label))
        layer.setRenderer(QgsRuleBasedRenderer(root))
        _labels(layer, "CASE WHEN \"type\" = 'lowest point' THEN 'low ' || "
                       "format_number(\"ground\", 2) ELSE format_number(\"ground\", 2) || "
                       "' / tile ' || format_number(\"tile_depth\", 2) || ' deep' END",
                size=8.0)
        layer.triggerRepaint()


class _NetworkStyler(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, zones):
        super().__init__()
        self.zones = zones

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsVectorLayer):
            return
        cats = []
        for z in self.zones:
            c = _zone_color(z)
            sym = QgsLineSymbol.createSimple({"color": "{},{},{}".format(
                c.red(), c.green(), c.blue()), "width": "0.5"})
            cats.append(QgsRendererCategory(int(z), sym, "Zone {}".format(z)))
        r = QgsCategorizedSymbolRenderer("zone", cats)
        layer.setRenderer(r)
        layer.triggerRepaint()


class _StatusStyler(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, codes):
        super().__init__()
        self.codes = codes

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsRasterLayer):
            return
        classes = [QgsPalettedRasterRenderer.Class(c, QColor(STATUS_COLORS[c]),
                                                   STATUS_LABELS[c]) for c in self.codes]
        layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes))
        layer.renderer().setOpacity(0.75)
        layer.triggerRepaint()


class WaterManagementZonesAlgorithm(QgsProcessingAlgorithm):
    DEM = "DEM"
    FIELD = "FIELD"
    NETWORK = "NETWORK"
    OUTLET = "OUTLET"
    UNITS = "UNITS"
    INTERVAL = "INTERVAL"
    WT_DEPTH = "WT_DEPTH"
    CONTROL_DEPTH = "CONTROL_DEPTH"
    STRUCTURES_ON = "STRUCTURES_ON"
    MIN_SPACING = "MIN_SPACING"
    LAT_SPACING = "LAT_SPACING"
    Z_UNITS = "Z_UNITS"
    NET_Z = "NET_Z"
    DEFAULT_DEPTH = "DEFAULT_DEPTH"
    INTERVALS = "INTERVALS"
    P_LOW = "P_LOW"
    P_HIGH = "P_HIGH"
    STEP_PCT = "STEP_PCT"
    CELL_SIZE = "CELL_SIZE"
    SNAP = "SNAP"
    JOIN_CROSSINGS = "JOIN_CROSSINGS"
    BAND_SMOOTH = "BAND_SMOOTH"
    MIN_ZONE_AREA = "MIN_ZONE_AREA"
    SMOOTH_ZONES = "SMOOTH_ZONES"
    SMOOTH_RADIUS = "SMOOTH_RADIUS"
    ZONES = "ZONES"
    STRUCTURES = "STRUCTURES"
    NETWORK_ZONES = "NETWORK_ZONES"
    STATUS = "STATUS"
    OPTIONS = "OPTIONS"
    POINTS = "POINTS"
    OUTLETS = "OUTLETS"
    ALL_SYSTEMS = "ALL_SYSTEMS"
    ZONE_POLYGONS = "ZONE_POLYGONS"

    # output destinations are not remembered; every input and setting is
    NOT_SAVED = ("ZONES", "STRUCTURES", "NETWORK_ZONES", "STATUS", "OPTIONS", "POINTS")
    # (layer inputs are kept for the session only if the layer is still loaded)

    def tr(self, s):
        return QCoreApplication.translate("WaterManagementZones", s)

    def createInstance(self):
        return WaterManagementZonesAlgorithm()

    def name(self):
        return "watermanagementzones"

    def displayName(self):
        return self.tr("Water management zones and structures")

    def group(self):
        return ""

    def groupId(self):
        return ""

    def icon(self):
        return QIcon(os.path.join(PLUGIN_DIR, "icon.png"))

    # ------------------------------------------------------------ saved settings
    # Each run's inputs and settings become the defaults the next time the dialog
    # opens (QGIS builds a fresh algorithm, and so reads them again, for every
    # dialog), until a new project is started or another project is loaded.
    def addParameter(self, definition, createOutput=True):
        name = definition.name()
        if name not in self.NOT_SAVED:
            if not hasattr(self, "_saved_names"):
                self._saved_names = []
            self._saved_names.append(name)
            if name in _LAST_RUN:
                value = _LAST_RUN[name]
                try:
                    ok = (definition.checkValueIsAcceptable(value)
                          if hasattr(definition, "checkValueIsAcceptable") else True)
                    if ok and (name not in LAYER_INPUTS or _layer_still_there(value)):
                        definition.setDefaultValue(value)
                except (ValueError, TypeError):
                    pass
        return super().addParameter(definition, createOutput)

    def _save_settings(self, parameters):
        for name in getattr(self, "_saved_names", []):
            if name not in parameters:
                continue
            value = parameters[name]
            if isinstance(value, QgsMapLayer):
                value = value.id()              # don't hold on to the layer itself
            elif isinstance(value, QgsProcessingFeatureSourceDefinition):
                value = QgsProcessingFeatureSourceDefinition(value)
            _LAST_RUN[name] = value

    def shortHelpString(self):
        return self.tr(
            "<p>Splits a field into water management zones, for controlled drainage and "
            "subirrigation alike, and places a water-control structure for each zone.</p>"
            "<p><b>With a drainage network</b> (from Illini Subsurface Drain Layout, "
            "Subsurface Drainage Layout, Illini Drainage System Design, Tile Drainage "
            "Layout or drawn by hand): every cell is drained by its nearest pipe, and its "
            "water joins the mains and submains where that pipe does. Walking up the "
            "mains and submains from the outlet, a structure is placed wherever the "
            "ground served since the last structure spans more than the <i>zone "
            "elevation interval</i>. A structure goes at the outlet too.</p>"
            "<p>Structures are placed only inside the field boundary, and only pipe inside "
            "the field drains field land. If the outlet lies outside the field, its "
            "structure goes where the network enters the field (each place it enters, if "
            "more than one).</p>"
            "<p><b>Without a network</b>: the field is cut into elevation bands one "
            "interval high, each connected patch its own zone, to lay the system out "
            "around.</p>"
            "<p>Each zone's boards are set so the managed water table sits the <i>managed "
            "water-table depth</i> below the zone's low ground (its 5th percentile, or "
            "the ground at the structure if lower). A cell is <i>controlled</i> when the "
            "managed water table is above its tile and no deeper than the <i>deepest "
            "controlled water table</i>. The control status raster shows where it is "
            "too deep (high ground), where the tile is above the managed water table "
            "(that land drains freely), where the water table would reach the surface, "
            "and land not near a tile.</p>"
            "<p><b>Merge zones smaller than</b>: zones with less land are merged, "
            "smallest first. With a network, a small zone loses its structure and joins "
            "the zone downstream (the outlet structure is kept: a small outlet zone takes "
            "in the smallest zone draining into it). Without a network, small patches of "
            "a band join their neighbours. 0 keeps every zone.</p>"
            "<p><b>Several drainage systems</b> (e.g. one per zone, each discharging to a "
            "stream): give them all in the network layer with an <i>Outlets</i> point layer "
            "(one point per system), or tick <i>Keep every separate drainage system</i> to "
            "root each at its lowest free end. Each system gets its own structures; outputs "
            "carry a <i>system</i> number. <i>Zone polygons</i> keep each zone's land on the "
            "pipes inside it.</p>"
            "<p><b>Lowest points and boundary crossings</b>: the lowest ground point of "
            "each zone, and every place a pipe crosses the field boundary, with its "
            "surface elevation, tile elevation and tile depth, and whether the water "
            "leaves or enters the field there. Structures and these points carry their "
            "latitude and longitude (WGS 84).</p>"
            "<p><b>Interval options</b> compares several intervals: structures needed "
            "against controlled area.</p>"
            "<p>Tile elevations are read from the vertex Z values (Illini Subsurface "
            "Drain Layout 0.13+), else ElevDepth1/ElevDepth2 or invert_us/invert_ds, "
            "else the tile is taken at the default depth below the DEM. Mains and "
            "submains come from a <i>kind</i> field, else from the network itself "
            "(pipes with tributaries).</p>")

    # ------------------------------------------------------------ parameters
    def initAlgorithm(self, config=None):
        num = QgsProcessingParameterNumber.Double
        self.addParameter(QgsProcessingParameterRasterLayer(self.DEM, self.tr("Elevation (DEM)")))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.FIELD, self.tr("Field boundary"), [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.NETWORK, self.tr("Drainage network [optional; leave empty for elevation "
                                  "bands only]"),
            [QgsProcessing.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterPoint(
            self.OUTLET, self.tr("Outlet location [optional; default: lowest free end of "
                                 "the network]"), optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.OUTLETS, self.tr("Outlets, one per separate drainage system [optional]"),
            [QgsProcessing.TypeVectorPoint], optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.ALL_SYSTEMS, self.tr("Keep every separate drainage system (each with its own "
                                      "outlet: an outlet point, else its lowest free end)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.ZONE_POLYGONS, self.tr("Zone polygons: land in each drains only to pipe "
                                        "inside it [optional]"),
            [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.UNITS, self.tr("Design units (elevations, depths, distances, outputs)"),
            options=[self.tr("Metres"), self.tr("Feet")], defaultValue=1))
        self.addParameter(QgsProcessingParameterNumber(
            self.INTERVAL, self.tr("Zone elevation interval"), num, defaultValue=1.0,
            minValue=0.01))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_ZONE_AREA, self.tr("Merge zones smaller than (acres, or hectares in "
                                        "metres; 0 = keep all)"), num, defaultValue=0.5,
            minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.WT_DEPTH, self.tr("Managed water-table depth below the zone's low ground"),
            num, defaultValue=1.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.CONTROL_DEPTH, self.tr("Deepest water table that still counts as "
                                        "controlled"), num, defaultValue=2.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.STRUCTURES_ON, self.tr("Place structures on"),
            options=[self.tr("Mains and submains"), self.tr("Mains only")], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_SPACING, self.tr("Shortest distance between structures"), num,
            defaultValue=100.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SMOOTH_ZONES, self.tr("Smooth zone boundaries"), defaultValue=False))
        self.addParameter(QgsProcessingParameterNumber(
            self.LAT_SPACING, self.tr("Lateral spacing [optional; land farther than 3/4 of "
                                      "a spacing from a tile counts as not drained]"),
            num, optional=True, minValue=0.0))

        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.Z_UNITS, self.tr("DEM elevation units"),
            options=[self.tr("Metres"), self.tr("International feet"),
                     self.tr("US survey feet")], defaultValue=2)))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.NET_Z, self.tr("Network tile elevations (Z values and fields) are in"),
            options=[self.tr("Design units (as written by the layout plugins)"),
                     self.tr("DEM elevation units"),
                     self.tr("Ignore them: use the default tile depth")], defaultValue=0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.DEFAULT_DEPTH, self.tr("Tile depth where the network has no elevations"),
            num, defaultValue=4.0, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterString(
            self.INTERVALS, self.tr("Intervals to compare (comma-separated; empty to skip)"),
            defaultValue="0.5, 1, 1.5, 2, 3", optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.P_LOW, self.tr("Zone low ground: percentile of the zone's ground"), num,
            defaultValue=5.0, minValue=0.0, maxValue=50.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.P_HIGH, self.tr("Zone high ground: percentile of the zone's ground"), num,
            defaultValue=95.0, minValue=50.0, maxValue=100.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.STEP_PCT, self.tr("Structure placement: ground of the land joining each "
                                   "point, as this percentile and its complement "
                                   "(50 = median)"),
            num, defaultValue=25.0, minValue=0.0, maxValue=50.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.CELL_SIZE, self.tr("Analysis cell size (0 = automatic)"), num,
            defaultValue=0.0, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SNAP, self.tr("Snap tolerance for joining pipe ends (0 = automatic)"), num,
            defaultValue=0.0, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterBoolean(
            self.JOIN_CROSSINGS, self.tr("Join pipes where they cross"), defaultValue=True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SMOOTH_RADIUS, self.tr("Zone boundary smoothing radius"), num,
            defaultValue=20.0, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.BAND_SMOOTH, self.tr("Elevation bands: terrain smoothing radius"), num,
            defaultValue=15.0, minValue=0.0)))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.ZONES, self.tr("Control zones"), QgsProcessing.TypeVectorPolygon))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.STRUCTURES, self.tr("Control structures"), QgsProcessing.TypeVectorPoint,
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.POINTS, self.tr("Lowest points and boundary crossings"),
            QgsProcessing.TypeVectorPoint, optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.NETWORK_ZONES, self.tr("Network by zone"), QgsProcessing.TypeVectorLine,
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.STATUS, self.tr("Control status"), optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OPTIONS, self.tr("Interval options"), QgsProcessing.TypeVector,
            optional=True, createByDefault=True))
        for key, desc in (("N_STRUCTURES", "Number of control structures (zones)"),
                          ("PCT_CONTROLLED", "Drained area under control (%)")):
            self.addOutput(QgsProcessingOutputNumber(key, self.tr(desc)))
        self.addOutput(QgsProcessingOutputString("WARNINGS", self.tr("Warnings")))

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _read_dem(layer, x0, y0, cell, nrows, ncols):
        from osgeo import gdal
        src = layer.dataProvider().dataSourceUri()
        tmp = "/vsimem/wmz_dem.tif"
        ds = gdal.Warp(tmp, src, format="GTiff",
                       outputBounds=(x0, y0 - nrows * cell, x0 + ncols * cell, y0),
                       width=ncols, height=nrows, resampleAlg="bilinear",
                       dstNodata=-99999.0, outputType=gdal.GDT_Float32)
        if ds is None:
            raise QgsProcessingException("Could not read the DEM with GDAL.")
        arr = ds.GetRasterBand(1).ReadAsArray().astype(float)
        ds = None
        gdal.Unlink(tmp)
        arr[~np.isfinite(arr) | (arr <= -99998.0)] = np.nan
        return arr

    @staticmethod
    def _rings(geom):
        """All rings (outer and holes) of a polygon geometry, as (n, 2) arrays."""
        g = QgsGeometry(geom)
        g.convertToStraightSegment()
        polys = g.asMultiPolygon() if g.isMultipart() else [g.asPolygon()]
        rings = []
        for poly in polys:
            for ring in poly:
                if len(ring) >= 3:
                    rings.append(np.array([(p.x(), p.y()) for p in ring], dtype=float))
        return rings or None

    @staticmethod
    def _rasterize_labels(geoms, x0, y0, cell, nrows, ncols):
        """Label grid (1..n by feature, 0 outside) from polygons."""
        from osgeo import gdal, ogr
        ds = gdal.GetDriverByName("MEM").Create("", ncols, nrows, 1, gdal.GDT_Int32)
        ds.SetGeoTransform((x0, cell, 0.0, y0, 0.0, -cell))
        ds.GetRasterBand(1).Fill(0)
        drv = ogr.GetDriverByName("Memory") or ogr.GetDriverByName("MEM")
        src = drv.CreateDataSource("wmz_zone_polys")
        lyr = src.CreateLayer("zones", geom_type=ogr.wkbMultiPolygon)
        lyr.CreateField(ogr.FieldDefn("zid", ogr.OFTInteger))
        for k, g in enumerate(geoms, start=1):
            f = ogr.Feature(lyr.GetLayerDefn())
            f.SetField("zid", k)
            f.SetGeometry(ogr.CreateGeometryFromWkb(bytes(g.asWkb())))
            lyr.CreateFeature(f)
        gdal.RasterizeLayer(ds, [1], lyr, options=["ATTRIBUTE=zid"])
        return ds.GetRasterBand(1).ReadAsArray().astype(np.int64)

    @staticmethod
    def _rasterize(geoms, x0, y0, cell, nrows, ncols):
        from osgeo import gdal, ogr
        ds = gdal.GetDriverByName("MEM").Create("", ncols, nrows, 1, gdal.GDT_Byte)
        ds.SetGeoTransform((x0, cell, 0.0, y0, 0.0, -cell))
        drv = ogr.GetDriverByName("Memory") or ogr.GetDriverByName("MEM")
        src = drv.CreateDataSource("wmz_field")
        lyr = src.CreateLayer("field", geom_type=ogr.wkbMultiPolygon)
        for g in geoms:
            f = ogr.Feature(lyr.GetLayerDefn())
            f.SetGeometry(ogr.CreateGeometryFromWkb(bytes(g.asWkb())))
            lyr.CreateFeature(f)
        gdal.RasterizeLayer(ds, [1], lyr, burn_values=[1])
        return ds.GetRasterBand(1).ReadAsArray().astype(bool)

    @staticmethod
    def _polygonize(zr, x0, y0, cell):
        """{zone id: QgsGeometry} from a zone raster (0 = none)."""
        from osgeo import gdal, ogr
        nrows, ncols = zr.shape
        ds = gdal.GetDriverByName("MEM").Create("", ncols, nrows, 1, gdal.GDT_Int32)
        ds.SetGeoTransform((x0, cell, 0.0, y0, 0.0, -cell))
        band = ds.GetRasterBand(1)
        band.WriteArray(zr.astype(np.int32))
        mds = gdal.GetDriverByName("MEM").Create("", ncols, nrows, 1, gdal.GDT_Byte)
        mds.SetGeoTransform((x0, cell, 0.0, y0, 0.0, -cell))
        mds.GetRasterBand(1).WriteArray((zr > 0).astype(np.uint8))
        drv = ogr.GetDriverByName("Memory") or ogr.GetDriverByName("MEM")
        src = drv.CreateDataSource("wmz_zones")
        lyr = src.CreateLayer("zones", geom_type=ogr.wkbPolygon)
        lyr.CreateField(ogr.FieldDefn("zone", ogr.OFTInteger))
        gdal.Polygonize(band, mds.GetRasterBand(1), lyr, 0, [])
        parts = {}
        for f in lyr:
            z = f.GetField(0)
            g = QgsGeometry()
            g.fromWkb(bytes(f.GetGeometryRef().ExportToWkb()))
            parts.setdefault(int(z), []).append(g)
        return {z: QgsGeometry.unaryUnion(gs) for z, gs in parts.items()}

    @staticmethod
    def _write_status(path, sr, x0, y0, cell, crs):
        from osgeo import gdal
        drv = gdal.GetDriverByName("GTiff")
        ds = drv.Create(path, sr.shape[1], sr.shape[0], 1, gdal.GDT_Byte,
                        ["COMPRESS=DEFLATE"])
        ds.SetGeoTransform((x0, cell, 0.0, y0, 0.0, -cell))
        ds.SetProjection(crs.toWkt())
        b = ds.GetRasterBand(1)
        b.WriteArray(sr.astype(np.uint8))
        b.SetNoDataValue(0)
        ds.FlushCache()
        ds = None

    # ------------------------------------------------------------ run
    def processAlgorithm(self, parameters, context, feedback):
        self._save_settings(parameters)
        status_path = self.parameterAsOutputLayer(parameters, self.STATUS, context)
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        if dem_layer is None:
            raise QgsProcessingException(self.tr("Could not load the DEM."))
        crs = dem_layer.crs()
        if not crs.isValid() or crs.isGeographic():
            raise QgsProcessingException(self.tr(
                "The DEM must be in a projected coordinate system (metres or feet). "
                "Reproject it with Raster > Projections > Warp first."))
        xf = _meters_factor(crs)                                   # map units -> m
        units = self.parameterAsEnum(parameters, self.UNITS, context)
        uf = UNIT_FACTORS[units]                                   # design -> m
        zf = Z_FACTORS[self.parameterAsEnum(parameters, self.Z_UNITS, context)]
        u_lbl = "ft" if units == 1 else "m"
        a_lbl = "ac" if units == 1 else "ha"
        d2m = uf / xf                                              # design -> map units
        warnings = []

        def num(key):
            v = parameters.get(key)
            if v is None or v == "":
                return None
            v = self.parameterAsDouble(parameters, key, context)
            return None if v is None or v != v else v

        interval = num(self.INTERVAL)
        intervals = []
        txt = self.parameterAsString(parameters, self.INTERVALS, context) or ""
        for t in txt.replace(";", ",").split(","):
            t = t.strip()
            if t:
                try:
                    v = float(t)
                    if v > 0:
                        intervals.append(v)
                except ValueError:
                    warnings.append("Ignored interval '{}' (not a number).".format(t))
        intervals = sorted(set(intervals))

        # ---- field boundary
        field_src = self.parameterAsSource(parameters, self.FIELD, context)
        if field_src is None:
            raise QgsProcessingException(self.tr("Could not load the field boundary."))
        to_dem = QgsCoordinateTransform(field_src.sourceCrs(), crs, context.transformContext())
        fgeoms = []
        for f in field_src.getFeatures():
            g = QgsGeometry(f.geometry())
            if g.isNull() or g.isEmpty():
                continue
            g.transform(to_dem)
            fgeoms.append(g)
        if not fgeoms:
            raise QgsProcessingException(self.tr("The field boundary has no polygons."))
        field_union = QgsGeometry.unaryUnion(fgeoms)
        bbox = field_union.boundingBox()

        # ---- network
        net_src = self.parameterAsSource(parameters, self.NETWORK, context)
        net_mode = net_src is not None and net_src.featureCount() != 0
        net_lines = []
        net_z_mode = self.parameterAsEnum(parameters, self.NET_Z, context)
        if net_mode:
            to_dem_n = QgsCoordinateTransform(net_src.sourceCrs(), crs,
                                              context.transformContext())
            names = [fd.name() for fd in net_src.fields()]
            low = {n.lower(): n for n in names}
            kind_f = low.get("kind")
            end_fields = None
            for a, b in (("elevdepth1", "elevdepth2"), ("invert_us", "invert_ds")):
                if a in low and b in low:
                    end_fields = (low[a], low[b])
                    break
            keep = [low[k] for k in ("nominal", "actual_size", "pipe", "pipe_id", "id")
                    if k in low]
            zconv = 1.0 if net_z_mode == 0 else zf / uf          # -> design units
            n_z = 0
            for f in net_src.getFeatures():
                g = QgsGeometry(f.geometry())
                if g.isNull() or g.isEmpty():
                    continue
                has_z = QgsWkbTypes.hasZ(g.wkbType())
                g.transform(to_dem_n)
                kind = f[kind_f] if kind_f else None
                kind = kind if isinstance(kind, str) else None
                attrs = {k: f[k] for k in keep}
                parts = list(g.constGet().parts()) if g.isMultipart() else [g.constGet()]
                for part in parts:
                    pts = [(v.x(), v.y(), v.z() if has_z else float("nan"))
                           for v in part.vertices()]
                    if len(pts) < 2:
                        continue
                    xyz = np.array(pts, dtype=float)
                    z = xyz[:, 2]
                    if net_z_mode == 2 or not np.isfinite(z).any() or np.all(z == 0):
                        z[:] = np.nan
                        if net_z_mode != 2 and end_fields and len(parts) == 1:
                            try:
                                z[0] = float(f[end_fields[0]])
                                z[-1] = float(f[end_fields[1]])
                            except (TypeError, ValueError):
                                pass
                    z *= zconv
                    if np.isfinite(z).any():
                        n_z += 1
                    xyz[:, 2] = z
                    net_lines.append((xyz, kind, int(f.id()), attrs))
            if not net_lines:
                raise QgsProcessingException(self.tr("The drainage network has no lines."))
            for xyz, _, _, _ in net_lines:
                bbox.combineExtentWith(float(xyz[:, 0].min()), float(xyz[:, 1].min()))
                bbox.combineExtentWith(float(xyz[:, 0].max()), float(xyz[:, 1].max()))
            if n_z == 0 and net_z_mode != 2:
                warnings.append("The network carries no tile elevations: the tile is taken "
                                "{:.2f} {} below the ground everywhere.".format(
                                    num(self.DEFAULT_DEPTH), u_lbl))

        outlet_xy = None
        if parameters.get(self.OUTLET) not in (None, ""):
            pt = self.parameterAsPoint(parameters, self.OUTLET, context, crs)
            if pt is not None and not (pt.x() == 0 and pt.y() == 0):
                outlet_xy = (pt.x(), pt.y())
                bbox.combineExtentWith(pt.x(), pt.y())
        outlets = []
        if parameters.get(self.OUTLETS) not in (None, ""):
            osrc = self.parameterAsSource(parameters, self.OUTLETS, context)
            if osrc is not None:
                otr = QgsCoordinateTransform(osrc.sourceCrs(), crs, context.transformContext())
                for f in osrc.getFeatures():
                    g = QgsGeometry(f.geometry())
                    if g.isNull() or g.isEmpty():
                        continue
                    g.transform(otr)
                    for v in g.vertices():
                        outlets.append((v.x(), v.y()))
                        bbox.combineExtentWith(v.x(), v.y())
        all_systems = self.parameterAsBool(parameters, self.ALL_SYSTEMS, context)
        zpoly_geoms = []
        if parameters.get(self.ZONE_POLYGONS) not in (None, ""):
            zsrc = self.parameterAsSource(parameters, self.ZONE_POLYGONS, context)
            if zsrc is not None:
                ztr = QgsCoordinateTransform(zsrc.sourceCrs(), crs, context.transformContext())
                for f in zsrc.getFeatures():
                    g = QgsGeometry(f.geometry())
                    if g.isNull() or g.isEmpty():
                        continue
                    g.transform(ztr)
                    zpoly_geoms.append(g)

        # ---- analysis grid
        native = max(abs(dem_layer.rasterUnitsPerPixelX()), abs(dem_layer.rasterUnitsPerPixelY()))
        cell = (num(self.CELL_SIZE) or 0) * d2m
        if cell <= 0:
            cell = native
        pad = 3 * cell
        w = bbox.width() + 2 * pad
        h = bbox.height() + 2 * pad
        if (w / cell) * (h / cell) > MAX_CELLS:
            cell = math.sqrt(w * h / MAX_CELLS)
            warnings.append("Analysis cell coarsened to {:.2f} {} to keep the grid under {:,} "
                            "cells.".format(cell / d2m, u_lbl, MAX_CELLS))
        ncols = int(math.ceil(w / cell))
        nrows = int(math.ceil(h / cell))
        x0 = bbox.xMinimum() - pad
        y0 = bbox.yMaximum() + pad
        grid = Grid(x0, y0, cell, nrows, ncols)
        feedback.pushInfo(self.tr("Analysis grid: {} x {} cells of {:.2f} {}").format(
            ncols, nrows, cell / d2m, u_lbl))
        feedback.setProgress(5)

        dem = self._read_dem(dem_layer, x0, y0, cell, nrows, ncols) * (zf / uf)
        mask = self._rasterize(fgeoms, x0, y0, cell, nrows, ncols) & np.isfinite(dem)
        if not mask.any():
            raise QgsProcessingException(self.tr(
                "The field does not overlap the DEM (check both layers' locations)."))
        cell_area = (cell * xf) ** 2 / (4046.8564224 if units == 1 else 1e4)
        feedback.setProgress(15)

        lat = num(self.LAT_SPACING)
        params = ZoneParams(
            interval=interval, wt_depth=num(self.WT_DEPTH), control_depth=num(self.CONTROL_DEPTH),
            min_spacing=num(self.MIN_SPACING) * d2m,
            structures_on="mains" if self.parameterAsEnum(
                parameters, self.STRUCTURES_ON, context) == 1 else "collectors",
            max_dist=0.75 * lat * d2m if lat else None, step=cell,
            p_low=num(self.P_LOW), p_high=num(self.P_HIGH), intervals=intervals,
            band_smooth=int(round((num(self.BAND_SMOOTH) or 0) * d2m / cell)),
            step_pct=num(self.STEP_PCT),
            field_rings=self._rings(field_union))
        if params.wt_depth > params.control_depth:
            warnings.append("The managed water-table depth is deeper than the deepest "
                            "controlled water table: no land can count as controlled.")

        def ground_at(x, y):
            return bilinear(grid, dem, x, y)

        # ---- zoning
        rows, pieces, opts = [], None, []
        # zones smaller than this are merged into a neighbour (0 = keep all)
        min_cells = int(round((num(self.MIN_ZONE_AREA) or 0) / cell_area))
        if net_mode:
            tol = (num(self.SNAP) or 0) * d2m
            if tol <= 0:
                tol = max(0.5 * cell, 0.3 / xf)
            try:
                net = build_network(net_lines, tol, outlet_xy, ground_at,
                                    self.parameterAsBool(parameters, self.JOIN_CROSSINGS,
                                                         context),
                                    outlets=outlets, all_systems=all_systems)
            except NetworkError as e:
                raise QgsProcessingException(str(e))
            n_def = fill_tile_z(net, ground_at, num(self.DEFAULT_DEPTH))
            if n_def and not any("no tile elevations" in w_ for w_ in warnings):
                warnings.append("{} network vertices had no tile elevation: taken {:.2f} {} "
                                "below the ground.".format(n_def, num(self.DEFAULT_DEPTH),
                                                           u_lbl))
            if net.unconnected:
                warnings.append("{} line(s) are not connected to {} and were left out{}."
                                .format(net.unconnected,
                                        "any outlet" if net.systems > 1 else "the outlet",
                                        "" if all_systems else
                                        " (tick 'Keep every separate drainage system' to "
                                        "keep separate systems)"))
            if net.dup_outlets:
                warnings.append("{} outlet point(s) fell on a system that already had an "
                                "outlet and were ignored.".format(net.dup_outlets))
            if net.systems > 1:
                feedback.pushInfo(self.tr("{} separate drainage systems, each with its own "
                                          "outlet.").format(net.systems))
            if net.loops:
                warnings.append("{} pipe(s) closed a loop and were left out.".format(net.loops))
            if net.crossings:
                feedback.pushInfo(self.tr("Joined {} crossing(s) between pipes.").format(
                    net.crossings))
            feedback.pushInfo(self.tr("Network: {} pipes between junctions; mains and "
                                      "submains from the {}.").format(
                len(net.edges), "kind field" if net.kind_source == "kind" else
                "network shape (pipes with tributaries)"))
            feedback.setProgress(25)
            try:
                groups = None
                if zpoly_geoms:
                    groups = self._rasterize_labels(zpoly_geoms, x0, y0, cell, nrows, ncols)
                zo = NetworkZoning(grid, dem, mask, net, params, cell_area, groups=groups)
                if zo.groups_without_pipe:
                    warnings.append("{} zone polygon(s) hold no pipe: their land drains to "
                                    "the nearest pipe anywhere.".format(
                                        len(zo.groups_without_pipe)))
            except ValueError as e:
                raise QgsProcessingException(str(e))
            if feedback.isCanceled():
                return {}
            feedback.setProgress(50)
            zone, structs = zo.place(interval)
            zone, structs, n_merged = zo.merge_small(zone, structs, min_cells)
            if n_merged:
                feedback.pushInfo(self.tr("{} zone(s) smaller than {:g} {} merged into a "
                                          "neighbour (their structures dropped).").format(
                    n_merged, num(self.MIN_ZONE_AREA), a_lbl))
            ev = zo.evaluate(zone, structs)
            rows = zo.zone_table(ev)
            pieces = zo.network_pieces(ev)
            zr, sr = zo.rasters(ev)
            summ = zo.summary(ev)
            if intervals:
                opts = options_table(zo, intervals, min_cells)
            if zo.escape_area > 0:
                warnings.append("{:.2f} {} of the field drains through pipe that leaves the "
                                "field before reaching a main or submain inside it, so no "
                                "structure can control it; it is shown as draining "
                                "freely.".format(zo.escape_area, a_lbl))
            n_entry = int(zo.entry.sum())
            if not zo.inside[zo.S.ds < 0].all():
                feedback.pushInfo(self.tr("The outlet lies outside the field: the network "
                                          "enters the field at {} place(s), each with a "
                                          "structure.").format(n_entry))
            undrained = int((mask & ~zo.drained).sum())
            if undrained and lat:
                feedback.pushInfo(self.tr("{:.1f} {} of the field is not near a tile.").format(
                    undrained * cell_area, a_lbl))
        else:
            bz = BandZoning(grid, dem, mask, params, cell_area)
            zr0 = bz.bands(interval, min_cells)
            ev = bz.evaluate(zr0)
            rows = bz.zone_table(ev)
            zr, sr = bz.rasters(ev, zr0)
            summ = bz.summary(ev)
            if intervals:
                opts = band_options(bz, intervals, min_cells)
        feedback.setProgress(75)

        # ---- outputs: zones
        def r3(v):
            return None if v is None or (isinstance(v, float) and not math.isfinite(v)) \
                else round(float(v), 3)

        if self.parameterAsBool(parameters, self.SMOOTH_ZONES, context) and zr.any():
            radius = (num(self.SMOOTH_RADIUS) or 0) * d2m / cell
            factor = 2 if nrows * ncols * 4 <= 4 * MAX_CELLS else 1
            # cover one cell beyond the zones so the smoothed zones reach the field
            # edge, then clip them to the field boundary
            cov = np.pad(zr > 0, 1)
            cov = (cov[1:-1, 1:-1] | cov[:-2, 1:-1] | cov[2:, 1:-1] | cov[1:-1, :-2]
                   | cov[1:-1, 2:] | cov[:-2, :-2] | cov[:-2, 2:] | cov[2:, :-2] | cov[2:, 2:])
            cov = np.repeat(np.repeat(cov, factor, 0), factor, 1)
            fz, lost = smooth_zones(zr, int(round(radius)), factor, cov)
            polys = self._polygonize(fz, x0, y0, cell / factor)
            for z in list(polys):
                clipped = polys[z].intersection(field_union)
                if clipped.type() != QgsWkbTypes.PolygonGeometry:   # drop stray lines/points
                    parts = [g_ for g_ in clipped.asGeometryCollection()
                             if g_.type() == QgsWkbTypes.PolygonGeometry]
                    clipped = QgsGeometry.collectGeometry(parts) if parts else QgsGeometry()
                if not clipped.isEmpty():
                    polys[z] = clipped
            feedback.pushInfo(self.tr("Zone boundaries smoothed over {:.1f} {}.").format(
                radius * cell / d2m, u_lbl))
            if lost:
                warnings.append("Zone(s) {} are too narrow to smooth and are drawn "
                                "unsmoothed.".format(", ".join(str(z) for z in lost)))
        else:
            polys = self._polygonize(zr, x0, y0, cell)
        zf_ = QgsFields()
        for name, typ in (("zone", QVariant.Int), ("struct_id", QVariant.Int),
                          ("system", QVariant.Int),
                          ("area", QVariant.Double), ("tile_len", QVariant.Double),
                          ("ground_min", QVariant.Double), ("ground_low", QVariant.Double),
                          ("ground_med", QVariant.Double), ("ground_high", QVariant.Double),
                          ("ground_max", QVariant.Double), ("range", QVariant.Double),
                          ("board_elev", QVariant.Double), ("wt_depth_med", QVariant.Double),
                          ("pct_ctrl", QVariant.Double), ("pct_deep", QVariant.Double),
                          ("pct_free", QVariant.Double), ("pct_wet", QVariant.Double),
                          ("flag", QVariant.String)):
            zf_.append(QgsField(name, typ))
        sink, zones_id = self.parameterAsSink(parameters, self.ZONES, context, zf_,
                                              QgsWkbTypes.MultiPolygon, crs)
        if sink is None:
            raise QgsProcessingException(self.tr("Could not create the Control zones layer."))
        for r in rows:
            g = polys.get(r["zone"])
            if g is None or g.isEmpty():
                continue
            g.convertToMultiType()
            f = QgsFeature(zf_)
            f.setGeometry(g)
            f.setAttributes([
                r["zone"], r["zone"] if net_mode else None,
                r.get("system") if net_mode else None, r3(r["area"]),
                r3(r["tile_len"] * xf / uf) if net_mode else None,
                r3(r["zmin"]), r3(r["p_low"]), r3(r["median"]), r3(r["p_high"]), r3(r["zmax"]),
                r3(r["range"]), r3(r["board"]), r3(r["median"] - r["board"]),
                round(r["pct_ok"], 1), round(r["pct_deep"], 1), round(r["pct_free"], 1),
                round(r["pct_wet"], 1), r["flag"] or None])
            sink.addFeature(f, QgsFeatureSink.FastInsert)
        zone_ids = [r["zone"] for r in rows]
        if context.willLoadLayerOnCompletion(zones_id):
            _POST[zones_id] = _ZoneStyler(zone_ids)
            context.layerToLoadOnCompletionDetails(zones_id).setPostProcessor(_POST[zones_id])
        out = {self.ZONES: zones_id}

        # ---- latitude / longitude (WGS 84)
        to_wgs = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"),
                                        context.transformContext())

        def latlon(x, y):
            try:
                p_ = to_wgs.transform(QgsPointXY(float(x), float(y)))
                return round(p_.y(), 7), round(p_.x(), 7)
            except Exception:  # noqa: BLE001 (a failed transform leaves the fields empty)
                return None, None

        # ---- lowest point of each zone, and where the pipes cross the field boundary
        pf = QgsFields()
        for name, typ in (("type", QVariant.String), ("zone", QVariant.Int),
                          ("system", QVariant.Int),
                          ("lat", QVariant.Double), ("lon", QVariant.Double),
                          ("ground", QVariant.Double), ("tile_elev", QVariant.Double),
                          ("tile_depth", QVariant.Double), ("direction", QVariant.String),
                          ("on_pipe", QVariant.String)):
            pf.append(QgsField(name, typ))
        psink, p_id = self.parameterAsSink(parameters, self.POINTS, context, pf,
                                           QgsWkbTypes.Point, crs)
        lows = lowest_points(zr, dem, grid)
        crossings = []
        if net_mode:
            crossings = boundary_crossings(net, self._rings(field_union) or [])
        if psink is not None:
            for zid in sorted(lows):
                x, y, gz, i, j = lows[zid]
                tz = td = None
                if net_mode and zo.idx[i, j] >= 0:
                    tz = float(zo.S.z[zo.idx[i, j]])
                    td = gz - tz
                lat, lon = latlon(x, y)
                f = QgsFeature(pf)
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
                sysno = None
                if net_mode and zo.idx[i, j] >= 0:
                    sysno = int(net.edges[int(zo.S.edge[zo.idx[i, j]])].system)
                f.setAttributes(["lowest point", zid, sysno, lat, lon, r3(gz), r3(tz),
                                 r3(td), None, None])
                psink.addFeature(f, QgsFeatureSink.FastInsert)
            zs_ = ev["zone_sample"] if net_mode else None
            for c in crossings:
                e = net.edges[c["edge"]]
                kind = e.kind or ("main" if e.main else ("submain" if e.collector
                                                         else "lateral"))
                gz = float(ground_at(np.array([c["x"]]), np.array([c["y"]]))[0])
                zid = None
                on = np.nonzero((zo.S.edge == c["edge"]) & (zs_ > 0))[0]
                if len(on):
                    zid = int(zs_[on[np.argmin(np.abs(zo.S.chain[on] - c["chain"]))]])
                lat, lon = latlon(c["x"], c["y"])
                f = QgsFeature(pf)
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(c["x"], c["y"])))
                f.setAttributes(["boundary crossing", zid, int(e.system), lat, lon,
                                 r3(gz) if math.isfinite(gz) else None, r3(c["z"]),
                                 r3(gz - c["z"]) if math.isfinite(gz) else None,
                                 c["direction"], kind])
                psink.addFeature(f, QgsFeatureSink.FastInsert)
            out[self.POINTS] = p_id
            if context.willLoadLayerOnCompletion(p_id):
                _POST[p_id] = _PointStyler()
                context.layerToLoadOnCompletionDetails(p_id).setPostProcessor(_POST[p_id])

        # ---- structures and network
        if net_mode:
            sf = QgsFields()
            for name, typ in (("struct_id", QVariant.Int), ("zone", QVariant.Int),
                              ("system", QVariant.Int),
                              ("lat", QVariant.Double), ("lon", QVariant.Double),
                              ("ds_struct", QVariant.Int), ("on_pipe", QVariant.String),
                              ("pipe_size", QVariant.String), ("ground", QVariant.Double),
                              ("invert", QVariant.Double), ("tile_depth", QVariant.Double),
                              ("board_elev", QVariant.Double),
                              ("board_height", QVariant.Double),
                              ("board_depth", QVariant.Double),
                              ("zone_area", QVariant.Double), ("up_area", QVariant.Double),
                              ("zone_low", QVariant.Double), ("zone_high", QVariant.Double),
                              ("pct_ctrl", QVariant.Double), ("flag", QVariant.String)):
                sf.append(QgsField(name, typ))
            ssink, s_id = self.parameterAsSink(parameters, self.STRUCTURES, context, sf,
                                               QgsWkbTypes.Point, crs)
            if ssink is not None:
                for r in rows:
                    a = r["edge_attrs"] or {}
                    size = None
                    for k in sorted(a, key=lambda k_: k_.lower() != "nominal"):
                        v = a[k]
                        if k.lower() in ("nominal", "actual_size") and v is not None \
                                and str(v) not in ("", "NULL"):
                            size = str(v)
                            break
                    f = QgsFeature(sf)
                    f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r["x"], r["y"])))
                    lat, lon = latlon(r["x"], r["y"])
                    f.setAttributes([
                        r["zone"], r["zone"], r["system"], lat, lon, r["ds_zone"] or None,
                        r["kind"], size,
                        r3(r["ground"]), r3(r["invert"]), r3(r["tile_depth"]), r3(r["board"]),
                        r3(r["board_height"]), r3(r["board_below_ground"]), r3(r["area"]),
                        r3(r["up_area"]), r3(r["p_low"]), r3(r["p_high"]),
                        round(r["pct_ok"], 1), r["flag"] or None])
                    ssink.addFeature(f, QgsFeatureSink.FastInsert)
                out[self.STRUCTURES] = s_id
                if context.willLoadLayerOnCompletion(s_id):
                    _POST[s_id] = _StructureStyler()
                    context.layerToLoadOnCompletionDetails(s_id).setPostProcessor(_POST[s_id])
            nf = QgsFields()
            for name, typ in (("zone", QVariant.Int), ("system", QVariant.Int),
                              ("kind", QVariant.String),
                              ("order", QVariant.Int), ("src_fid", QVariant.LongLong),
                              ("length", QVariant.Double), ("invert_us", QVariant.Double),
                              ("invert_ds", QVariant.Double)):
                nf.append(QgsField(name, typ))
            nsink, n_id = self.parameterAsSink(parameters, self.NETWORK_ZONES, context, nf,
                                               QgsWkbTypes.LineStringZ, crs)
            if nsink is not None:
                for p in pieces:
                    q = p["xyz"]
                    f = QgsFeature(nf)
                    f.setGeometry(QgsGeometry(QgsLineString(
                        [QgsPoint(float(x), float(y), float(z)) for x, y, z in q])))
                    f.setAttributes([p["zone"], p["system"], p["kind"], int(p["order"]),
                                     int(p["src"]),
                                     r3(p["length"] * xf / uf), r3(q[0, 2]), r3(q[-1, 2])])
                    nsink.addFeature(f, QgsFeatureSink.FastInsert)
                out[self.NETWORK_ZONES] = n_id
                if context.willLoadLayerOnCompletion(n_id):
                    _POST[n_id] = _NetworkStyler(zone_ids)
                    context.layerToLoadOnCompletionDetails(n_id).setPostProcessor(_POST[n_id])

        # ---- status raster
        if status_path:
            self._write_status(status_path, sr, x0, y0, cell, crs)
            out[self.STATUS] = status_path
            codes = [c for c in (ST_OK, ST_DEEP, ST_FREE, ST_WET, ST_UNDRAINED)
                     if (sr == c).any()]
            if context.willLoadLayerOnCompletion(status_path):
                _POST[status_path] = _StatusStyler(codes)
                context.layerToLoadOnCompletionDetails(status_path).setPostProcessor(
                    _POST[status_path])

        # ---- options table
        if opts:
            of = QgsFields()
            for name, typ in (("interval", QVariant.Double),
                              ("structures" if net_mode else "zones", QVariant.Int),
                              ("pct_ctrl", QVariant.Double), ("pct_deep", QVariant.Double),
                              ("pct_free", QVariant.Double), ("pct_wet", QVariant.Double),
                              ("mean_zone_area", QVariant.Double),
                              ("max_range", QVariant.Double)):
                of.append(QgsField(name, typ))
            osink, o_id = self.parameterAsSink(parameters, self.OPTIONS, context, of,
                                               QgsWkbTypes.NoGeometry, crs)
            if osink is not None:
                for o in opts:
                    f = QgsFeature(of)
                    f.setAttributes([o["interval"], o["structures"], round(o["pct_controlled"], 1),
                                     round(o["pct_deep"], 1), round(o["pct_free"], 1),
                                     round(o["pct_wet"], 1), r3(o["mean_zone_area"]),
                                     r3(o["max_range"])])
                    osink.addFeature(f, QgsFeatureSink.FastInsert)
                out[self.OPTIONS] = o_id

        # ---- log
        feedback.pushInfo("")
        if net_mode:
            feedback.pushInfo(self.tr("{} control structures for a {:.2f} {} interval.").format(
                summ["structures"], interval, u_lbl))
        else:
            feedback.pushInfo(self.tr("{} elevation-band zones for a {:.2f} {} interval.").format(
                summ["structures"], interval, u_lbl))
        feedback.pushInfo(self.tr(
            "Of the drained area: {:.1f}% controlled, {:.1f}% water table too deep, "
            "{:.1f}% tile above the managed water table, {:.1f}% water table above "
            "ground.").format(summ["pct_controlled"], summ["pct_deep"], summ["pct_free"],
                              summ["pct_wet"]))
        if opts:
            feedback.pushInfo(self.tr("Interval options:"))
            for o in opts:
                feedback.pushInfo("  {:.2f} {}: {} {}, {:.1f}% controlled".format(
                    o["interval"], u_lbl, o["structures"],
                    "structures" if net_mode else "zones", o["pct_controlled"]))
        if lows:
            zl_ = min(lows, key=lambda k_: lows[k_][2])
            feedback.pushInfo(self.tr("Lowest ground: {:.2f} {} (zone {}).").format(
                lows[zl_][2], u_lbl, zl_))
        if crossings:
            n_out = sum(1 for c in crossings if c["direction"] == "leaves the field")
            feedback.pushInfo(self.tr("The network crosses the field boundary at {} "
                                      "point(s): {} leaving the field, {} entering.").format(
                len(crossings), n_out, len(crossings) - n_out))
        flagged = [r for r in rows if r.get("flag")]
        if flagged:
            feedback.pushInfo(self.tr("{} zone(s) to check: see the flag field.").format(
                len(flagged)))
        for w_ in warnings:
            feedback.pushWarning(w_)
        feedback.setProgress(100)
        out.update({"N_STRUCTURES": summ["structures"],
                    "PCT_CONTROLLED": summ["pct_controlled"],
                    "WARNINGS": "\n".join(warnings)})
        return out
