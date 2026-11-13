import math
import os

from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeature,
    QgsFeatureRequest,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPalLayerSettings,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsRendererCategory,
    QgsSymbol,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor

from .hooghoudt import spacing_for_component
from .sda import SdaError, clipped_mapunits_sql, horizons_sql, restrictions_sql, run_query
from .style import apply_boundary_style

SQ_M_PER_ACRE = 4046.8564224

# Enum names moved between QGIS 3.x releases and QGIS 4; resolve them once.
def _pick(new, old):
    try:
        return new()
    except AttributeError:
        return old()

POLYGON_TYPE = _pick(lambda: Qgis.GeometryType.Polygon, lambda: QgsWkbTypes.PolygonGeometry)
WKB_MULTIPOLYGON = _pick(lambda: Qgis.WkbType.MultiPolygon, lambda: QgsWkbTypes.MultiPolygon)
WKB_COLLECTION = _pick(lambda: Qgis.WkbType.GeometryCollection, lambda: QgsWkbTypes.GeometryCollection)
SOURCE_POLYGON = _pick(lambda: Qgis.ProcessingSourceType.VectorPolygon, lambda: QgsProcessing.TypeVectorPolygon)
FAST_INSERT = _pick(lambda: QgsFeatureSink.Flag.FastInsert, lambda: QgsFeatureSink.FastInsert)
NUMBER_DOUBLE = _pick(lambda: Qgis.ProcessingNumberParameterType.Double,
                      lambda: QgsProcessingParameterNumber.Double)
FLAG_ADVANCED = _pick(lambda: Qgis.ProcessingParameterFlag.Advanced,
                      lambda: QgsProcessingParameterDefinition.FlagAdvanced)

LABEL_CHOICES = [
    ("Soil name (map unit name)", '"muname"'),
    ("Map unit symbol", '"musym"'),
    ("Symbol and soil name", "\"musym\" || '\\n' || \"muname\""),
    ("Series name only (first word, e.g. Drummer)", '"series"'),
    ("Drain spacing (ft)", "format_number(\"spacing_ft\", 0) || ' ft'"),
    ("Soil name and drain spacing", "\"muname\" || '\\n' || format_number(\"spacing_ft\", 0) || ' ft'"),
    ("Subirrigation spacing (ft)", "format_number(\"subirr_ft\", 0) || ' ft'"),
    ("Soil name and subirrigation spacing", "\"muname\" || '\\n' || format_number(\"subirr_ft\", 0) || ' ft'"),
]

# Series names that are two words, so "Du Page silt loam" gives "Du Page", not "Du".
_TWO_WORD_PREFIXES = {"du", "la", "le", "de", "del", "el", "st.", "mt.", "port", "high"}


def series_name(muname):
    """First word of the soil name: "Drummer silty clay loam, 0 to 2 percent slopes" -> "Drummer"."""
    words = (muname or "").split(",")[0].split()
    if not words:
        return None
    if words[0].lower() in _TWO_WORD_PREFIXES and len(words) > 1:
        return " ".join(words[:2])
    return words[0]

# Shapefile field names are limited to 10 characters.
OUTPUT_FIELDS = [
    ("mukey", "str", 12, 0),
    ("musym", "str", 12, 0),
    ("muname", "str", 254, 0),
    ("series", "str", 60, 0),
    ("compname", "str", 60, 0),
    ("drainage", "str", 40, 0),
    ("hydgrp", "str", 6, 0),
    ("acres", "dbl", 12, 2),
    ("ka_ftday", "dbl", 12, 3),    # Ksat above drain depth
    ("kb_ftday", "dbl", 12, 3),    # Ksat from drain depth to the barrier
    ("barrier_ft", "dbl", 12, 2),  # depth to impermeable layer
    ("spacing_ft", "int", 6, 0),   # Hooghoudt drain spacing, nearest 5 ft
    ("ka_sub", "dbl", 12, 3),      # Ksat of the saturated zone above drains, subirrigation
    ("subirr_ft", "int", 6, 0),    # inverse Hooghoudt subirrigation spacing, nearest 5 ft
]


def _make_field(name, kind, length, prec):
    if Qgis.QGIS_VERSION_INT >= 33800:
        from qgis.PyQt.QtCore import QMetaType
        qtype = {"str": QMetaType.Type.QString, "int": QMetaType.Type.Int, "dbl": QMetaType.Type.Double}[kind]
    else:
        from qgis.PyQt.QtCore import QVariant
        qtype = {"str": QVariant.String, "int": QVariant.Int, "dbl": QVariant.Double}[kind]
    return QgsField(name, qtype, len=length, prec=prec)


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v):
    f = _to_float(v)
    return int(round(f)) if f is not None else None


def _round(v, n):
    return round(v, n) if v is not None else None


def _round_to(v, step):
    """Round to the nearest multiple of step (5 ft for spacings), never below one step."""
    if v is None:
        return None
    return int(max(step, step * math.floor(v / step + 0.5)))


def _polygon_only(geom):
    """Strip Z/M and keep only polygon parts, returned as a multipolygon."""
    g = QgsGeometry(geom)
    if g.isEmpty():
        return g
    abstract = g.get()
    abstract.dropZValue()
    abstract.dropMValue()
    if not g.isGeosValid():
        g = g.makeValid()
    if QgsWkbTypes.geometryType(g.wkbType()) != POLYGON_TYPE or \
            QgsWkbTypes.flatType(g.wkbType()) == WKB_COLLECTION:
        converted = g.convertToType(POLYGON_TYPE, True)
        g = converted if converted is not None else QgsGeometry()
    if not g.isEmpty():
        g.convertToMultiType()
    return g


class SoilStyler(QgsProcessingLayerPostProcessorInterface):
    """Colors each soil differently, labels it, and saves a .qml style next to a shapefile."""

    instance = None  # Processing needs this kept alive until the layer loads.

    def __init__(self, label_expression):
        super().__init__()
        self.label_expression = label_expression

    @staticmethod
    def create(label_expression):
        SoilStyler.instance = SoilStyler(label_expression)
        return SoilStyler.instance

    def postProcessLayer(self, layer, context, feedback):
        idx = layer.fields().indexOf("muname")
        if idx < 0:
            return
        names = sorted(v for v in layer.uniqueValues(idx) if v)
        symbols = {}
        sym_idx = layer.fields().indexOf("musym")
        for f in layer.getFeatures():
            symbols.setdefault(f["muname"], f[sym_idx] if sym_idx >= 0 else "")

        categories = []
        for i, name in enumerate(names):
            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            hue = int((i * 137.508) % 360)  # golden-angle spacing keeps neighbors distinct
            symbol.setColor(QColor.fromHsv(hue, 120, 225))
            symbol.setOpacity(0.85)
            legend = f"{symbols.get(name, '')}  {name}".strip()
            categories.append(QgsRendererCategory(name, symbol, legend))
        layer.setRenderer(QgsCategorizedSymbolRenderer("muname", categories))

        settings = QgsPalLayerSettings()
        settings.fieldName = self.label_expression
        settings.isExpression = True
        settings.autoWrapLength = 22
        text_format = QgsTextFormat()
        text_format.setSize(9)
        buffer = QgsTextBufferSettings()
        buffer.setEnabled(True)
        buffer.setSize(0.8)
        buffer.setColor(QColor(255, 255, 255))
        text_format.setBuffer(buffer)
        settings.setFormat(text_format)
        try:
            settings.placement = Qgis.LabelPlacement.Horizontal
        except AttributeError:
            settings.placement = QgsPalLayerSettings.Horizontal
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()

        # Shapefiles can't hold styling, so save a .qml with the same name.
        # QGIS loads it automatically whenever the shapefile is opened.
        _save_qml(layer)


def _save_qml(layer):
    path = layer.source().split("|")[0]
    if path.lower().endswith(".shp") and os.path.exists(path):
        layer.saveNamedStyle(os.path.splitext(path)[0] + ".qml")


class BoundaryStyler(QgsProcessingLayerPostProcessorInterface):
    """Draws the field boundary as a bold outline and moves it above the soil map."""

    instance = None

    @staticmethod
    def create():
        BoundaryStyler.instance = BoundaryStyler()
        return BoundaryStyler.instance

    def postProcessLayer(self, layer, context, feedback):
        apply_boundary_style(layer)
        _save_qml(layer)
        layer_id = layer.id()
        # Wait until Processing has finished adding every output, then put the outline on top.
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(0, lambda: _move_to_top(layer_id))


def _move_to_top(layer_id):
    from qgis.core import QgsProject
    root = QgsProject.instance().layerTreeRoot()
    node = root.findLayer(layer_id)
    if node is None:
        return
    parent = node.parent()
    if parent is None or parent.children() and parent.children()[0] is node:
        return
    clone = node.clone()
    parent.insertChildNode(0, clone)
    parent.removeChildNode(node)


class SoilMapAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    DISSOLVE = "DISSOLVE"
    LABEL = "LABEL"
    OUTLINE = "OUTLINE"
    DRAIN_DEPTH = "DRAIN_DEPTH"
    DRAIN_COEF = "DRAIN_COEF"
    DRAIN_DIAM = "DRAIN_DIAM"
    SUB_RATE = "SUB_RATE"
    SUB_HW = "SUB_HW"
    SUB_HM = "SUB_HM"
    OUTPUT = "OUTPUT"
    BOUNDARY = "BOUNDARY"

    def tr(self, text):
        return QCoreApplication.translate("SoilMapAlgorithm", text)

    def createInstance(self):
        return SoilMapAlgorithm()

    def name(self):
        return "soilmap"

    def displayName(self):
        return self.tr("Get soil map for polygons (SSURGO)")

    def group(self):
        return self.tr("Soils")

    def groupId(self):
        return "soils"

    def shortHelpString(self):
        return self.tr(
            "Clips USDA-NRCS SSURGO soil map units (the data shown in Web Soil Survey) "
            "to each input polygon and returns them as a polygon layer labeled with the soil name.\n\n"
            "Fields: map unit key, symbol and soil name; series (first word of the soil name, "
            "e.g. Drummer); dominant component; drainage class; "
            "hydrologic group; acres; and drain spacing.\n\n"
            "Drain spacing (spacing_ft) comes from the Hooghoudt equation, steady state, with the water "
            "table midway between drains at the soil surface: q L² = 8 Kb d h + 4 Ka h², where q is the "
            "drainage coefficient, h the drain depth, d the equivalent depth (van der Molen and Wesseling, "
            "1991) and L the spacing. Ka (ka_ftday) is the thickness-weighted SSURGO Ksat from the surface "
            "to the drains; Kb (kb_ftday) is the same from the drains down to the impermeable layer "
            "(barrier_ft), taken as the shallowest restrictive layer or, if there is none, the bottom of "
            "the soil description. Ksat and depths are for the dominant component.\n\n"
            "Subirrigation spacing (subirr_ft) uses the inverse Hooghoudt equation: "
            "q L² = 8 Kb d (Hw − Hm) + 4 Ka (Hw² − Hm²), where q is the irrigation coefficient (design upward flux, e.g. peak ET), "
            "Hw the water level held at the drains and Hm the water table midway between drains, both "
            "in feet above the drains. Hw must be higher than Hm and no higher than the drain depth. "
            "Ka here (ka_sub) is averaged over the saturated zone above the drains, Hw deep.\n\n"
            "Save the output as a .shp file to get a shapefile. A .qml style file with the same name is "
            "written next to it so the colors and labels come back whenever the shapefile is opened.\n\n"
            "Check \"Outline the field boundary on the soil map\" to add the field boundary as a bold "
            "outline on top of the soils. Save that output as a .shp too if you want to keep the boundary, "
            "for example one you traced on the map.\n\n"
            "Needs an internet connection. Very large polygons (tens of thousands of acres) may exceed "
            "Soil Data Access limits; split them first."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Field boundary layer (shapefile or other polygons)"),
            [SOURCE_POLYGON]))
        self.addParameter(QgsProcessingParameterBoolean(
            self.DISSOLVE, self.tr("Merge all polygons into one area before querying"), defaultValue=False))
        self.addParameter(QgsProcessingParameterEnum(
            self.LABEL, self.tr("Label soils with"), options=[c[0] for c in LABEL_CHOICES], defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.OUTLINE, self.tr("Outline the field boundary on the soil map"), defaultValue=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.DRAIN_DEPTH, self.tr("Drain depth (ft)"), NUMBER_DOUBLE,
            defaultValue=3.5, minValue=0.5, maxValue=15.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.DRAIN_COEF, self.tr("Drainage coefficient (in/day)"), NUMBER_DOUBLE,
            defaultValue=0.375, minValue=0.01, maxValue=5.0))
        diam = QgsProcessingParameterNumber(
            self.DRAIN_DIAM, self.tr("Drain diameter for equivalent depth (in)"), NUMBER_DOUBLE,
            defaultValue=4.0, minValue=1.0, maxValue=36.0)
        diam.setFlags(diam.flags() | FLAG_ADVANCED)
        self.addParameter(diam)
        self.addParameter(QgsProcessingParameterNumber(
            self.SUB_RATE, self.tr("Irrigation coefficient for subirrigation (in/day)"), NUMBER_DOUBLE,
            defaultValue=0.25, minValue=0.01, maxValue=2.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SUB_HW, self.tr("Subirrigation water level at the drains, height above drain (ft)"),
            NUMBER_DOUBLE, defaultValue=2.0, minValue=0.0, maxValue=15.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SUB_HM, self.tr("Subirrigation mid-drain water table, height above drain (ft)"),
            NUMBER_DOUBLE, defaultValue=1.5, minValue=0.0, maxValue=15.0))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Soil map"), SOURCE_POLYGON))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.BOUNDARY, self.tr("Field boundary outline"), SOURCE_POLYGON,
            optional=True, createByDefault=True))

    def checkParameterValues(self, parameters, context):
        depth = self.parameterAsDouble(parameters, self.DRAIN_DEPTH, context)
        hw = self.parameterAsDouble(parameters, self.SUB_HW, context)
        hm = self.parameterAsDouble(parameters, self.SUB_HM, context)
        if hw <= hm:
            return False, self.tr(
                "For subirrigation the water level at the drains must be higher than the "
                "mid-drain water table.")
        if hw > depth:
            return False, self.tr(
                "The subirrigation water level at the drains can't be above the soil surface "
                "(higher than the drain depth).")
        return super().checkParameterValues(parameters, context)

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))
        dissolve = self.parameterAsBool(parameters, self.DISSOLVE, context)
        label_expr = LABEL_CHOICES[self.parameterAsEnum(parameters, self.LABEL, context)][1]
        outline = self.parameterAsBool(parameters, self.OUTLINE, context)
        drain = (
            self.parameterAsDouble(parameters, self.DRAIN_DEPTH, context),
            self.parameterAsDouble(parameters, self.DRAIN_COEF, context),
            self.parameterAsDouble(parameters, self.DRAIN_DIAM, context),
        )
        feedback.pushInfo(self.tr(
            f"Drain spacing: drains {drain[0]:g} ft deep, drainage coefficient {drain[1]:g} in/day, "
            f"{drain[2]:g} in drains, water table at the surface midway between drains."))
        subirr = (
            self.parameterAsDouble(parameters, self.SUB_RATE, context),
            self.parameterAsDouble(parameters, self.SUB_HW, context),
            self.parameterAsDouble(parameters, self.SUB_HM, context),
        )
        feedback.pushInfo(self.tr(
            f"Subirrigation spacing: irrigation coefficient {subirr[0]:g} in/day, water held {subirr[1]:g} ft above the drains, "
            f"mid-drain water table {subirr[2]:g} ft above the drains."))

        src_crs = source.sourceCrs()
        if not src_crs.isValid():
            raise QgsProcessingException(self.tr(
                "The input layer has no coordinate reference system. Set its CRS (for a shapefile, "
                "the .prj file) and run again."))
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        to_wgs = QgsCoordinateTransform(src_crs, wgs84, context.transformContext())
        from_wgs = QgsCoordinateTransform(wgs84, src_crs, context.transformContext())

        area_calc = QgsDistanceArea()
        area_calc.setSourceCrs(wgs84, context.transformContext())
        area_calc.setEllipsoid("EPSG:7030")  # WGS 84

        # Collect the areas to query, in WGS84.
        parts = []
        for f in source.getFeatures(QgsFeatureRequest()):
            if feedback.isCanceled():
                return {}
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            g = QgsGeometry(g)
            g.transform(to_wgs)
            g = _polygon_only(g)
            if g.isEmpty():
                feedback.pushWarning(self.tr(f"Skipped feature {f.id()}: no polygon area."))
                continue
            parts.append((f.id(), g))
        if not parts:
            raise QgsProcessingException(self.tr("The input layer has no polygons to query."))
        if dissolve:
            merged = _polygon_only(QgsGeometry.unaryUnion([g for _, g in parts]))
            parts = [(0, merged)]

        fields = QgsFields()
        for spec in OUTPUT_FIELDS:
            fields.append(_make_field(*spec))
        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, WKB_MULTIPOLYGON, src_crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        all_rows = []
        for n, (src_id, geom) in enumerate(parts):
            if feedback.isCanceled():
                break
            field_acres = area_calc.measureArea(geom) / SQ_M_PER_ACRE
            feedback.pushInfo(self.tr(f"Querying Soil Data Access for polygon {src_id} ({field_acres:,.1f} acres)…"))
            try:
                rows = run_query(clipped_mapunits_sql(geom.asWkt(8)), feedback)
            except SdaError as e:
                raise QgsProcessingException(str(e))
            if not rows:
                feedback.pushWarning(self.tr(
                    f"No SSURGO soil data for polygon {src_id}. It may be outside the survey area."))

            all_rows.extend(rows)
            feedback.setProgress(90.0 * (n + 1) / len(parts))

        spacing = self._drain_spacing(all_rows, drain, subirr, feedback)

        written = 0
        for r in all_rows:
            if feedback.isCanceled():
                break
            g = _polygon_only(QgsGeometry.fromWkt(r.get("wkt") or ""))
            if g.isEmpty():
                continue
            acres = area_calc.measureArea(g) / SQ_M_PER_ACRE
            g.transform(from_wgs)
            sp = spacing.get(_to_int(r.get("cokey")), {})
            feat = QgsFeature(fields)
            feat.setGeometry(g)
            feat.setAttributes([
                r.get("mukey"),
                r.get("musym"),
                (r.get("muname") or "").split(",")[0].strip() or None,  # drop slope/flooding phase
                series_name(r.get("muname")),
                r.get("compname"),
                r.get("drainagecl"),
                r.get("hydgrp"),
                round(acres, 2),
                _round(sp.get("ka_ftday"), 3),
                _round(sp.get("kb_ftday"), 3),
                _round(sp.get("barrier_ft"), 2),
                _round_to(sp.get("spacing_ft"), 5),
                _round(sp.get("ka_sub"), 3),
                _round_to(sp.get("subirr_ft"), 5),
            ])
            sink.addFeature(feat, FAST_INSERT)
            written += 1
        feedback.setProgress(100.0)

        feedback.pushInfo(self.tr(f"Wrote {written} soil polygons."))

        if context.willLoadLayerOnCompletion(dest_id):
            context.layerToLoadOnCompletionDetails(dest_id).setPostProcessor(SoilStyler.create(label_expr))

        results = {self.OUTPUT: dest_id}
        if outline and parameters.get(self.BOUNDARY) is not None:
            results.update(self._write_boundary(parameters, context, parts, src_crs, from_wgs, area_calc))
        return results

    def _drain_spacing(self, rows, drain, subirr, feedback):
        """Hooghoudt drain spacing for each dominant component, keyed by cokey.

        A failed lookup only warns, so the soil map is still written.
        """
        drain_depth_ft, coef_in_day, diam_in = drain
        cokeys = sorted({k for k in (_to_int(r.get("cokey")) for r in rows) if k is not None})
        if not cokeys:
            return {}
        horizons, barriers = {}, {}
        feedback.pushInfo(self.tr(f"Getting horizon Ksat and restrictive layers for {len(cokeys)} soils…"))
        try:
            for i in range(0, len(cokeys), 200):
                chunk = cokeys[i:i + 200]
                for h in run_query(horizons_sql(chunk), feedback):
                    horizons.setdefault(_to_int(h.get("cokey")), []).append(
                        (_to_float(h.get("hzdept_r")), _to_float(h.get("hzdepb_r")), _to_float(h.get("ksat_r"))))
                for b in run_query(restrictions_sql(chunk), feedback):
                    barriers[_to_int(b.get("cokey"))] = _to_float(b.get("resdept_r"))
        except SdaError as e:
            feedback.pushWarning(self.tr(f"Drain spacing skipped; horizon data could not be read: {e}"))
            return {}

        result = {}
        for k in cokeys:
            sp = spacing_for_component(horizons.get(k, []), barriers.get(k), drain_depth_ft, coef_in_day, diam_in)
            rate, hw, hm = subirr
            sub = spacing_for_component(horizons.get(k, []), barriers.get(k), drain_depth_ft, rate, diam_in,
                                        mid_ft=hm, drain_level_ft=hw)
            sp["ka_sub"] = sub.get("ka_ftday")
            sp["subirr_ft"] = sub.get("spacing_ft")
            result[k] = sp
            b = sp.get("barrier_ft")
            if sp.get("spacing_ft") is not None and b is not None and b <= drain_depth_ft:
                feedback.pushWarning(self.tr(
                    f"Component {k}: the impermeable layer ({b:.1f} ft) is not below the drains; "
                    "spacing uses flow above the drains only."))
        return result

    def _write_boundary(self, parameters, context, parts, src_crs, from_wgs, area_calc):
        fields = QgsFields()
        fields.append(_make_field("acres", "dbl", 12, 2))
        (sink, dest_id) = self.parameterAsSink(
            parameters, self.BOUNDARY, context, fields, WKB_MULTIPOLYGON, src_crs)
        if sink is None:
            return {}
        for _, geom in parts:
            acres = area_calc.measureArea(geom) / SQ_M_PER_ACRE
            g = QgsGeometry(geom)
            g.transform(from_wgs)
            feat = QgsFeature(fields)
            feat.setGeometry(g)
            feat.setAttributes([round(acres, 2)])
            sink.addFeature(feat, FAST_INSERT)
        if context.willLoadLayerOnCompletion(dest_id):
            context.layerToLoadOnCompletionDetails(dest_id).setPostProcessor(BoundaryStyler.create())
        return {self.BOUNDARY: dest_id}
