"""Processing algorithm: import a LANDRAIN .NET drainage network as an Illini network."""
import json
import os

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsLineString,
    QgsPoint,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterString,
    QgsSettings,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon

from .algorithm import _POST, _Styler, _advanced, _meters_factor
from .core import landrain as LR
from .core import sizing as K

PLUGIN_DIR = os.path.dirname(__file__)
SETTINGS_GROUP = "illini_subsurface_drain_layout/landrain_import"
FT = 0.3048

# design-unit fields converted from feet when the output is in metres
LENGTH_FIELDS = ("length", "ground_us", "ground_ds", "invert_us", "invert_ds", "max_depth",
                 "min_depth", "depth_ds", "upstream_length", "ElevDepth1", "BuryDepth1",
                 "ElevDepth2", "BuryDepth2")


def network_fields():
    """The Illini network fields, plus LANDRAIN's link flag."""
    D, I, S = QVariant.Double, QVariant.Int, QVariant.String
    spec = [
        ("id", I), ("pipe_id", I), ("kind", S), ("pipe", S), ("from_node", I), ("to_node", I),
        ("ds_link", I), ("length", D), ("TileLength", I), ("ground_us", D), ("ground_ds", D),
        ("terrain_grade_pct", D), ("invert_us", D), ("invert_ds", D),
        ("design_grade_pct", D), ("max_depth", D), ("min_depth", D), ("depth_ds", D),
        ("upstream_length", D), ("drained_area", D),
        ("ElevDepth1", D), ("BuryDepth1", D), ("ElevDepth2", D), ("BuryDepth2", D),
        ("InSlope", D), ("OutSlope", D), ("BURY_CHECK", S),
        ("TILE_ORDER", I), ("FLOW_LENGTH", D), ("SIZE_SLOPE", D), ("D_COEFF", D),
        ("DRAIN_SPACE", I), ("D_AREA", D), ("CUM_SEG_FLOW", D), ("ACTUAL_SIZE", D),
        ("NOMINAL", I), ("PIPE_TYPE", S), ("flag", S), ("LR_FLAG", I)]
    fs = QgsFields()
    for name, typ in spec:
        fs.append(QgsField(name, typ))
    return fs


def parse_sizes(text):
    """'3,4,5,...' -> [3, 4, 5, ...] (inches, size class 1 first)."""
    try:
        sizes = [int(round(float(t))) for t in str(text).replace(";", ",").split(",")
                 if t.strip()]
    except ValueError:
        raise QgsProcessingException("Pipe sizes must be numbers separated by commas.")
    if not sizes or any(s <= 0 for s in sizes) or sizes != sorted(sizes):
        raise QgsProcessingException("Pipe sizes must be positive and in increasing order.")
    return sizes


def to_metres(attrs):
    a = dict(attrs)
    for k in LENGTH_FIELDS:
        if a.get(k) is not None:
            a[k] = round(a[k] * FT, 3)
    if a.get("length") is not None:
        a["TileLength"] = int(round(a["length"]))
    if a.get("drained_area") is not None:          # acres -> ha
        a["drained_area"] = round(a["drained_area"] / 2.4710538, 3)
    return a


class ImportLandrainAlgorithm(QgsProcessingAlgorithm):
    NET_FILE = "NET_FILE"
    CRS = "CRS"
    ORIGIN_X = "ORIGIN_X"
    ORIGIN_Y = "ORIGIN_Y"
    LOCAL_X = "LOCAL_X"
    LOCAL_Y = "LOCAL_Y"
    ROTATION = "ROTATION"
    SIZES = "SIZES"
    OUTLET = "OUTLET"
    UNDESIGNED_DEPTH = "UNDESIGNED_DEPTH"
    MATERIAL = "MATERIAL"
    UNITS = "UNITS"
    NETWORK = "NETWORK"

    NOT_SAVED = ("NET_FILE", "OUTLET", "NETWORK")

    def tr(self, s):
        return QCoreApplication.translate("ImportLandrain", s)

    def createInstance(self):
        return ImportLandrainAlgorithm()

    def name(self):
        return "importlandrainnetwork"

    def displayName(self):
        return self.tr("Import LANDRAIN network")

    def group(self):
        return ""

    def groupId(self):
        return ""

    def icon(self):
        return QIcon(os.path.join(PLUGIN_DIR, "icon.png"))

    # settings are remembered, as in Illini subsurface drain layout
    def addParameter(self, definition, createOutput=True):
        name = definition.name()
        if name not in self.NOT_SAVED:
            if not hasattr(self, "_saved_names"):
                self._saved_names = []
            self._saved_names.append(name)
            raw = QgsSettings().value("{}/{}".format(SETTINGS_GROUP, name), None)
            if raw is not None:
                try:
                    definition.setDefaultValue(json.loads(str(raw)))
                except (ValueError, TypeError):
                    pass
        return super().addParameter(definition, createOutput)

    def _save_settings(self, parameters):
        settings = QgsSettings()
        for name in getattr(self, "_saved_names", []):
            if name in parameters:
                try:
                    v = parameters[name]
                    if hasattr(v, "authid"):
                        v = v.authid()
                    settings.setValue("{}/{}".format(SETTINGS_GROUP, name), json.dumps(v))
                except (TypeError, ValueError):
                    pass

    def shortHelpString(self):
        return self.tr(
            "<p>Reads a drainage network saved by LANDRAIN (a .NET file) and writes it as an "
            "Illini subsurface drain layout network, so it can be checked with Tile Profile "
            "and sized and priced with the Tile Size Calculator.</p>"
            "<p>LANDRAIN works in feet from its own origin. Give the map position of that "
            "origin (<b>Origin easting / northing</b>, in the <b>Coordinate system</b> chosen; "
            "for example the zero point used when the field was surveyed) and, if the "
            "survey grid was not aligned with the map, the LANDRAIN coordinates at that "
            "point (<b>Local origin</b>) and the <b>Rotation</b> of the survey grid "
            "(degrees counterclockwise).</p>"
            "<p>Each point's surface and tile (invert) elevations, and each link's pipe size, "
            "come from the file. <b>Pipe sizes</b> lists the inches of LANDRAIN's size "
            "classes 1, 2, 3 ... (3 in is the smallest in LANDRAIN). Pipes are joined "
            "into mains, submains and laterals from the outlet: the lowest pipe end unless "
            "an <b>Outlet</b> point is given. A system LANDRAIN had not yet designed has no "
            "tile elevations; its tile is put at the <b>Depth for undesigned systems</b> "
            "(default: the file's lower depth limit) and sized here. The drainage "
            "coefficient and drain spacing are the file's, and the sizing fields (TILE_ORDER, "
            "FLOW_LENGTH, ACTUAL_SIZE ...) are worked out as in Illini subsurface drain "
            "layout.</p>"
            "<p>BURY_CHECK says 'Imported from LANDRAIN', with any reverse grades; LR_FLAG "
            "keeps LANDRAIN's link flag.</p>")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile(
            self.NET_FILE, self.tr("LANDRAIN network file (.NET)"),
            extension="NET"))
        self.addParameter(QgsProcessingParameterCrs(
            self.CRS, self.tr("Coordinate system of the origin and output"),
            defaultValue="EPSG:3435"))
        self.addParameter(QgsProcessingParameterNumber(
            self.ORIGIN_X, self.tr("Origin easting (map units)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.ORIGIN_Y, self.tr("Origin northing (map units)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.LOCAL_X, self.tr("Local origin: LANDRAIN x at that point (ft)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.LOCAL_Y, self.tr("Local origin: LANDRAIN y at that point (ft)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ROTATION, self.tr("Rotation of the survey grid (degrees counterclockwise)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0,
            minValue=-360.0, maxValue=360.0)))
        self.addParameter(QgsProcessingParameterString(
            self.SIZES, self.tr("Pipe sizes of LANDRAIN size classes 1, 2, ... (in)"),
            defaultValue=",".join(str(s) for s in LR.LANDRAIN_SIZES)))
        self.addParameter(QgsProcessingParameterPoint(
            self.OUTLET, self.tr("Outlet (optional; default the lowest pipe end)"),
            optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.UNDESIGNED_DEPTH,
            self.tr("Depth for undesigned systems (ft; 0 = the file's lower depth limit)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.MATERIAL, self.tr("Pipe type"), options=list(K.MATERIALS), defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.UNITS, self.tr("Design units of the output fields"),
            options=[self.tr("Metres"), self.tr("Feet")], defaultValue=1))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.NETWORK, self.tr("Drainage network")))

    def processAlgorithm(self, parameters, context, feedback):
        path = self.parameterAsFile(parameters, self.NET_FILE, context)
        if not path or not os.path.exists(path):
            raise QgsProcessingException(self.tr("Choose a LANDRAIN .NET file."))
        with open(path, encoding="latin-1") as fh:
            try:
                net = LR.parse(fh.read())
            except (LR.LandrainError, ValueError, IndexError) as e:
                raise QgsProcessingException(
                    self.tr("Could not read {} as a LANDRAIN network: {}").format(
                        os.path.basename(path), e))
        crs = self.parameterAsCrs(parameters, self.CRS, context)
        if crs is None or not crs.isValid():
            raise QgsProcessingException(self.tr("Choose a coordinate system."))
        if crs.isGeographic():
            raise QgsProcessingException(self.tr(
                "Choose a projected coordinate system (in feet or metres), such as the "
                "State Plane zone of the field: LANDRAIN works in feet on a flat grid."))
        scale = FT / _meters_factor(crs)                 # LANDRAIN feet -> map units
        sizes = parse_sizes(self.parameterAsString(parameters, self.SIZES, context))
        outlet_xy = None
        if parameters.get(self.OUTLET) not in (None, ""):
            pt = self.parameterAsPoint(parameters, self.OUTLET, context, crs)
            outlet_xy = (pt.x(), pt.y())
        depth = self.parameterAsDouble(parameters, self.UNDESIGNED_DEPTH, context)
        material = self.parameterAsEnum(parameters, self.MATERIAL, context)
        metric = self.parameterAsEnum(parameters, self.UNITS, context) == 0
        uz = FT if metric else 1.0

        try:
            res = LR.build(
                net,
                origin=(self.parameterAsDouble(parameters, self.ORIGIN_X, context),
                        self.parameterAsDouble(parameters, self.ORIGIN_Y, context)),
                local_origin=(self.parameterAsDouble(parameters, self.LOCAL_X, context),
                              self.parameterAsDouble(parameters, self.LOCAL_Y, context)),
                rotation_deg=self.parameterAsDouble(parameters, self.ROTATION, context),
                scale=scale, sizes=sizes, outlet_xy=outlet_xy,
                undesigned_depth=depth if depth > 0 else None, material=material)
        except (LR.LandrainError, ValueError, KeyError, IndexError) as e:
            raise QgsProcessingException(self.tr("Could not build the network: {}").format(e))
        if not res["lines"]:
            raise QgsProcessingException(self.tr("The file has no pipes."))

        fields = network_fields()
        sink, net_id = self.parameterAsSink(parameters, self.NETWORK, context, fields,
                                            QgsWkbTypes.LineStringZM, crs)
        if sink is None:
            raise QgsProcessingException(self.tr("Could not create the output layer."))
        for L in res["lines"]:
            a = to_metres(L["attrs"]) if metric else L["attrs"]
            f = QgsFeature(fields)
            f.setGeometry(QgsGeometry(QgsLineString([
                QgsPoint(x, y, float(z) * uz, float(g) * uz)
                for (x, y), z, g in zip(L["coords"], L["tile_z"], L["ground_m"])])))
            f.setAttributes([a.get(fields.at(i).name()) for i in range(fields.count())])
            sink.addFeature(f, QgsFeatureSink.FastInsert)

        if context.willLoadLayerOnCompletion(net_id):
            _POST[net_id] = _Styler()
            context.layerToLoadOnCompletionDetails(net_id).setPostProcessor(_POST[net_id])

        s = net["settings"]
        u = "m" if metric else "ft"
        total = sum(L["attrs"]["length"] for L in res["lines"]) * uz
        c = res["counts"]
        feedback.pushInfo("")
        feedback.pushInfo(self.tr("LANDRAIN network '{}': {} points, {} links").format(
            net["name"], len(net["points"]), len(net["links"])))
        feedback.pushInfo(self.tr("{} lines in {} pipes ({} main, {} submains, {} laterals), "
                                  "{:,.0f} {} of tile").format(
            len(res["lines"]), res["pipes"], c.get("main", 0), c.get("submain", 0),
            c.get("lateral", 0), total, u))
        feedback.pushInfo(self.tr("Designed (tile elevations): {}; sized: {}").format(
            self.tr("yes") if net["designed"] else self.tr("no"),
            self.tr("yes") if net["sized"] else self.tr("no")))
        feedback.pushInfo(self.tr(
            "File settings: drainage coefficient {} in/day, drain spacing {} ft, grade "
            "{:.2f}-{:.2f}%, depth {}-{} ft").format(
            s["drainage_coeff"], s["spacing"], 100 * (s["min_grade"] or 0),
            100 * (s["max_grade"] or 0), s["depth_lower"], s["depth_upper"]))
        small = sorted({L["attrs"]["NOMINAL"] for L in res["lines"]
                        if L["attrs"].get("NOMINAL") and L["attrs"]["NOMINAL"] < 4})
        if small:
            feedback.pushInfo(self.tr(
                "The network has {} in pipe. The Tile Size Calculator sizes from 4 in up, "
                "so lines it resizes become at least 4 in.").format(
                    ", ".join(str(v) for v in small)))
        for n in res["notes"]:
            feedback.pushWarning(n) if hasattr(feedback, "pushWarning") else \
                feedback.reportError(n, False)
        self._save_settings(parameters)          # once the import has worked
        return {self.NETWORK: net_id}
