import os

from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsDistanceArea,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsPalLayerSettings,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsRendererCategory,
    QgsSymbol,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsUnitTypes,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor

from .recalc import (
    METRIC, NEW_FIELDS, REPLACED, SOIL_NAME_FIELDS, US,
    add_footage, field_values, length_to_m, rate_to_mm, recalc,
)
from .summary import SUMMARY_FIELDS, describe, summarize


def _pick(new, old):
    try:
        return new()
    except AttributeError:
        return old()


SOURCE_POLYGON = _pick(lambda: Qgis.ProcessingSourceType.VectorPolygon, lambda: QgsProcessing.TypeVectorPolygon)
SOURCE_TABLE = _pick(lambda: Qgis.ProcessingSourceType.Vector, lambda: QgsProcessing.TypeVector)
WKB_NONE = _pick(lambda: Qgis.WkbType.NoGeometry, lambda: QgsWkbTypes.NoGeometry)
SQUARE_METERS = _pick(lambda: Qgis.AreaUnit.SquareMeters, lambda: QgsUnitTypes.AreaSquareMeters)
FAST_INSERT = _pick(lambda: QgsFeatureSink.Flag.FastInsert, lambda: QgsFeatureSink.FastInsert)
NUMBER_DOUBLE = _pick(lambda: Qgis.ProcessingNumberParameterType.Double,
                      lambda: QgsProcessingParameterNumber.Double)
FLAG_ADVANCED = _pick(lambda: Qgis.ProcessingParameterFlag.Advanced,
                      lambda: QgsProcessingParameterDefinition.FlagAdvanced)

UNIT_CHOICES = ["US customary (ft, in/day, in)", "Metric (m, mm/day, mm)"]
LABEL_CHOICES = ["Soil name and drain spacing", "Drain spacing", "Soil name and subirrigation spacing",
                 "Subirrigation spacing", "Soil name"]


def _make_field(name, kind, length, prec):
    if Qgis.QGIS_VERSION_INT >= 33800:
        from qgis.PyQt.QtCore import QMetaType
        qtype = {"str": QMetaType.Type.QString, "int": QMetaType.Type.Int, "dbl": QMetaType.Type.Double}[kind]
    else:
        from qgis.PyQt.QtCore import QVariant
        qtype = {"str": QVariant.String, "int": QVariant.Int, "dbl": QVariant.Double}[kind]
    return QgsField(name, qtype, len=length, prec=prec)


def label_expression(choice, units, name_field):
    spacing, subirr = ("spacing_ft", "subirr_ft") if units == US else ("spacing_m", "subirr_m")
    unit = " ft" if units == US else " m"
    sp = f"format_number(\"{spacing}\", 0) || '{unit}'"
    su = f"format_number(\"{subirr}\", 0) || '{unit}'"
    name = f"\"{name_field}\"" if name_field else None
    if choice == 0:
        return f"{name} || '\\n' || {sp}" if name else sp
    if choice == 1:
        return sp
    if choice == 2:
        return f"{name} || '\\n' || {su}" if name else su
    if choice == 3:
        return su
    return name or sp


def _n(count):
    return f"{count} soil polygon has" if count == 1 else f"{count} soil polygons have"


def _save_qml(layer):
    path = layer.source().split("|")[0]
    if path.lower().endswith(".shp") and os.path.exists(path):
        layer.saveNamedStyle(os.path.splitext(path)[0] + ".qml")


class SpacingStyler(QgsProcessingLayerPostProcessorInterface):
    """Colors each soil differently, labels with the spacing, names the layer after the scenario,
    and saves a .qml style next to a shapefile."""

    instance = None  # Processing needs this kept alive until the layer loads.

    def __init__(self, name_field, label_expr, layer_name):
        super().__init__()
        self.name_field = name_field
        self.label_expr = label_expr
        self.layer_name = layer_name

    @staticmethod
    def create(name_field, label_expr, layer_name):
        SpacingStyler.instance = SpacingStyler(name_field, label_expr, layer_name)
        return SpacingStyler.instance

    def postProcessLayer(self, layer, context, feedback):
        layer.setName(self.layer_name)
        idx = layer.fields().indexOf(self.name_field) if self.name_field else -1
        if idx >= 0:
            names = sorted(str(v) for v in layer.uniqueValues(idx) if v not in (None, ""))
            categories = []
            for i, name in enumerate(names):
                symbol = QgsSymbol.defaultSymbol(layer.geometryType())
                hue = int((i * 137.508) % 360)  # golden-angle spacing keeps neighbors distinct
                symbol.setColor(QColor.fromHsv(hue, 120, 225))
                symbol.setOpacity(0.85)
                categories.append(QgsRendererCategory(name, symbol, name))
            layer.setRenderer(QgsCategorizedSymbolRenderer(self.name_field, categories))

        settings = QgsPalLayerSettings()
        settings.fieldName = self.label_expr
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
        _save_qml(layer)


class RecalcAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    UNITS = "UNITS"
    DRAIN_DEPTH = "DRAIN_DEPTH"
    DRAIN_COEF = "DRAIN_COEF"
    MID_HEIGHT = "MID_HEIGHT"
    BARRIER = "BARRIER"
    DRAIN_DIAM = "DRAIN_DIAM"
    SUB_RATE = "SUB_RATE"
    SUB_HW = "SUB_HW"
    SUB_HM = "SUB_HM"
    COST = "COST"
    THRESHOLD = "THRESHOLD"
    LABEL = "LABEL"
    OUTPUT = "OUTPUT"
    SUMMARY = "SUMMARY"

    def tr(self, text):
        return QCoreApplication.translate("RecalcAlgorithm", text)

    def createInstance(self):
        return RecalcAlgorithm()

    def name(self):
        return "recalculate"

    def displayName(self):
        return self.tr("Recalculate drain spacing for a soil map")

    def group(self):
        return self.tr("Drain spacing")

    def groupId(self):
        return "spacing"

    def shortHelpString(self):
        return self.tr(
            "Recalculates drain and subirrigation spacings for a soil map made by SSURGO drainage soil map "
            "or SoilGrids drainage soil map (or by this tool), for a new drain depth, drainage coefficient, "
            "mid-drain water table, impermeable layer depth or subirrigation water levels. The soil layer "
            "is copied with new spacing fields; run again for another scenario.\n\n"
            "Lengths, coefficients and the drain diameter are in the units you choose: feet, in/day and "
            "inches, or metres, mm/day and mm. The output fields use the same units.\n\n"
            "Drain spacing uses the Hooghoudt equation: q L² = 8 Kb d (Hm − Hw) + 4 Ka (Hm² − Hw²) with "
            "Hw = 0, where Hm is the mid-drain water table height above the drains. Leave it blank for the "
            "water table at the soil surface. Subirrigation spacing uses the inverse Hooghoudt equation "
            "with the water level held at the drains (Hw) and the mid-drain water table (Hm), both above "
            "the drains. Ka is the Ksat of the saturated zone above the drains, Kb from the drains to the "
            "impermeable layer, and d the equivalent depth (van der Molen and Wesseling, 1991).\n\n"
            "Ksat comes from each soil's ksat_prof field (SSURGO drainage soil map 1.10.0 and SoilGrids "
            "drainage soil map 0.2.0 or later). Older soil maps only have Ka and Kb for the drain depth "
            "they were made with; those are used instead, and the log says so. Re-run the soil plugin for "
            "the most accurate results at a new drain depth.\n\n"
            "Leave the impermeable layer depth blank to use each soil's own (barrier_ft or barrier_m). "
            "Spacings are rounded to the nearest 5 ft or 1 m.\n\n"
            "Spacing range: spc_lo/spc_hi and sub_lo/sub_hi use each soil's low and high Ksat (SSURGO "
            "low and high values; for SoilGrids, Rosetta's estimate ± one standard deviation). Lower Ksat "
            "gives the narrower spacing.\n\n"
            "Drain footage and cost: each soil polygon gets its area, the drain length needed at its "
            "spacing (area ÷ spacing) and, if you enter a cost per foot or metre, the cost.\n\n"
            "Field summary table: one row per soil, the total for all soils at their own spacings, and two "
            "design spacings for the whole field: the spacing that needs the same total drain length "
            "(area-weighted harmonic mean), and the narrowest spacing among soils covering at least the "
            "share of the field you set, so small inclusions don't control the design. SoilGrids maps are "
            "summarized per field (field_id); other soil maps as one field, so run them one field at a "
            "time or use Selected features only."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Soil map from SSURGO or SoilGrids drainage soil map"), [SOURCE_POLYGON]))
        self.addParameter(QgsProcessingParameterEnum(
            self.UNITS, self.tr("Units of the values below and of the output"),
            options=UNIT_CHOICES, defaultValue=US))
        self.addParameter(QgsProcessingParameterNumber(
            self.DRAIN_DEPTH, self.tr("Drain depth (ft or m)"), NUMBER_DOUBLE,
            defaultValue=3.5, minValue=0.1, maxValue=20.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.DRAIN_COEF, self.tr("Drainage coefficient (in/day or mm/day)"), NUMBER_DOUBLE,
            defaultValue=0.375, minValue=0.001, maxValue=200.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MID_HEIGHT,
            self.tr("Drainage mid-drain water table, height above drain (ft or m; blank = soil surface)"),
            NUMBER_DOUBLE, optional=True, minValue=0.0, maxValue=20.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.BARRIER,
            self.tr("Depth to impermeable layer (ft or m; blank = each soil's own)"),
            NUMBER_DOUBLE, optional=True, minValue=0.1, maxValue=100.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SUB_RATE, self.tr("Irrigation coefficient for subirrigation (in/day or mm/day)"),
            NUMBER_DOUBLE, defaultValue=0.25, minValue=0.001, maxValue=100.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SUB_HW, self.tr("Subirrigation water level at the drains, height above drain (ft or m)"),
            NUMBER_DOUBLE, defaultValue=2.0, minValue=0.0, maxValue=20.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SUB_HM, self.tr("Subirrigation mid-drain water table, height above drain (ft or m)"),
            NUMBER_DOUBLE, defaultValue=1.5, minValue=0.0, maxValue=20.0))
        diam = QgsProcessingParameterNumber(
            self.DRAIN_DIAM, self.tr("Drain diameter (in or mm; blank = 4 in or 100 mm)"),
            NUMBER_DOUBLE, optional=True, minValue=0.5, maxValue=1000.0)
        diam.setFlags(diam.flags() | FLAG_ADVANCED)
        self.addParameter(diam)
        self.addParameter(QgsProcessingParameterNumber(
            self.COST, self.tr("Installed drain cost per ft or per m (blank = no cost)"),
            NUMBER_DOUBLE, optional=True, minValue=0.0, maxValue=10000.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.THRESHOLD,
            self.tr("Narrowest-soil design: only soils covering at least this share of the field (%)"),
            NUMBER_DOUBLE, defaultValue=10.0, minValue=0.0, maxValue=100.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.LABEL, self.tr("Label soils with"), options=LABEL_CHOICES, defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Recalculated spacings"), SOURCE_POLYGON))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.SUMMARY, self.tr("Field summary"), SOURCE_TABLE, optional=True, createByDefault=True))

    def _optional(self, parameters, name, context):
        value = parameters.get(name)
        if value is None or value == "":
            return None
        return self.parameterAsDouble(parameters, name, context)

    def checkParameterValues(self, parameters, context):
        units = self.parameterAsEnum(parameters, self.UNITS, context)
        depth = self.parameterAsDouble(parameters, self.DRAIN_DEPTH, context)
        hw = self.parameterAsDouble(parameters, self.SUB_HW, context)
        hm = self.parameterAsDouble(parameters, self.SUB_HM, context)
        mid = self._optional(parameters, self.MID_HEIGHT, context)
        if units == METRIC and depth > 5.0:
            return False, self.tr(
                f"A drain depth of {depth:g} m is very deep. If you meant feet, choose US customary units.")
        if hw <= hm:
            return False, self.tr(
                "For subirrigation the water level at the drains must be higher than the "
                "mid-drain water table.")
        if hw > depth:
            return False, self.tr(
                "The subirrigation water level at the drains can't be above the soil surface "
                "(higher than the drain depth).")
        if mid is not None and (mid <= 0 or mid > depth):
            return False, self.tr(
                "The drainage mid-drain water table must be above the drains and no higher than the "
                "soil surface (more than 0 and no more than the drain depth).")
        return super().checkParameterValues(parameters, context)

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))
        units = self.parameterAsEnum(parameters, self.UNITS, context)
        diam = self._optional(parameters, self.DRAIN_DIAM, context)
        L = lambda v: length_to_m(units, v)  # noqa: E731
        scen = {
            "drain": L(self.parameterAsDouble(parameters, self.DRAIN_DEPTH, context)),
            "coef": rate_to_mm(units, self.parameterAsDouble(parameters, self.DRAIN_COEF, context)),
            "mid": L(self._optional(parameters, self.MID_HEIGHT, context)),
            "barrier": L(self._optional(parameters, self.BARRIER, context)),
            "diam_mm": (100.0 if units == METRIC else 4 * 25.4) if diam is None
                       else (diam if units == METRIC else diam * 25.4),
            "rate": rate_to_mm(units, self.parameterAsDouble(parameters, self.SUB_RATE, context)),
            "hw": L(self.parameterAsDouble(parameters, self.SUB_HW, context)),
            "hm": L(self.parameterAsDouble(parameters, self.SUB_HM, context)),
        }

        names = source.fields().names()
        if not ({"ksat_prof", "ka_ftday", "ka_mday"} & set(names)):
            raise QgsProcessingException(self.tr(
                "This layer has no soil Ksat fields. Use a soil map made by SSURGO drainage soil map or "
                "SoilGrids drainage soil map."))
        name_field = next((n for n in SOIL_NAME_FIELDS if n in names), None)

        fields = QgsFields()
        kept = []
        for f in source.fields():
            if f.name() not in REPLACED:
                fields.append(QgsField(f))
                kept.append(f.name())
        for name, kind, length, prec, _ in NEW_FIELDS[units]:
            fields.append(_make_field(name, kind, length, prec))

        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, source.wkbType(), source.sourceCrs())
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        u = "ft" if units == US else "m"
        r = "in/day" if units == US else "mm/day"
        p = parameters
        depth_v = self.parameterAsDouble(p, self.DRAIN_DEPTH, context)
        coef_v = self.parameterAsDouble(p, self.DRAIN_COEF, context)
        mid_v = self._optional(p, self.MID_HEIGHT, context)
        feedback.pushInfo(self.tr(
            f"Drainage: drains {depth_v:g} {u} deep, {coef_v:g} {r}, mid-drain water table "
            + (f"{mid_v:g} {u} above the drains." if mid_v is not None else "at the soil surface.")))
        feedback.pushInfo(self.tr(
            f"Subirrigation: {self.parameterAsDouble(p, self.SUB_RATE, context):g} {r}, water held "
            f"{self.parameterAsDouble(p, self.SUB_HW, context):g} {u} above the drains, mid-drain water "
            f"table {self.parameterAsDouble(p, self.SUB_HM, context):g} {u} above the drains."))

        cost = self._optional(parameters, self.COST, context)
        threshold = self.parameterAsDouble(parameters, self.THRESHOLD, context)
        area_calc = QgsDistanceArea()
        area_calc.setSourceCrs(source.sourceCrs(), context.transformContext())
        area_calc.setEllipsoid(context.ellipsoid() or "WGS84")
        group_field = "field_id" if "field_id" in names else None
        new_names = [f[0] for f in NEW_FIELDS[units]]
        polygons = []

        total = source.featureCount() or 1
        counts = {"profile": 0, "ka_kb": 0, None: 0}
        for n, feat in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break
            attrs = {name: (None if v is None or (hasattr(v, "isNull") and v.isNull()) else v)
                     for name, v in zip(names, feat.attributes())}
            values, src = recalc(attrs, scen)
            counts[src] += 1
            geom = feat.geometry()
            sq_m = None
            if geom is not None and not geom.isEmpty():
                sq_m = area_calc.convertAreaMeasurement(area_calc.measureArea(geom), SQUARE_METERS)
            add_footage(units, values, sq_m, cost)
            new_values = field_values(units, values)
            out = QgsFeature(fields)
            out.setGeometry(geom)
            out.setAttributes([feat[name] for name in kept] + new_values)
            sink.addFeature(out, FAST_INSERT)
            d = dict(zip(new_names, new_values))
            u_ = "ft" if units == US else "m"
            polygons.append({
                "group": attrs.get(group_field) if group_field else "All",
                "soil": attrs.get(name_field) if name_field else None,
                "area": d["area_ac" if units == US else "area_ha"] or 0.0,
                "spacing": d[f"spacing_{u_}"], "spacing_lo": d[f"spc_lo_{u_}"], "spacing_hi": d[f"spc_hi_{u_}"],
                "subirr": d[f"subirr_{u_}"], "subirr_lo": d[f"sub_lo_{u_}"], "subirr_hi": d[f"sub_hi_{u_}"],
            })
            feedback.setProgress(100.0 * (n + 1) / total)

        if counts["ka_kb"]:
            feedback.pushWarning(self.tr(
                f"{_n(counts['ka_kb'])} no ksat_prof value, so the stored Ka and Kb were used. Re-run the "
                "soil plugin for layer-by-layer Ksat."))
        if counts[None]:
            feedback.pushWarning(self.tr(
                f"{_n(counts[None])} no Ksat or no impermeable layer depth, so no spacing was calculated."))
        done = counts["profile"] + counts["ka_kb"]
        feedback.pushInfo(self.tr(f"Recalculated {done} soil polygon{'' if done == 1 else 's'}."))

        rows = summarize(units, polygons, threshold, cost)
        feedback.pushInfo(self.tr("Field summary:"))
        for line in describe(units, rows):
            feedback.pushInfo(line)
        results = {self.OUTPUT: dest_id}
        if parameters.get(self.SUMMARY) is not None:
            s_fields = QgsFields()
            for us_name, m_name, kind, length, prec, _ in SUMMARY_FIELDS:
                s_fields.append(_make_field(us_name if units == US else m_name, kind, length, prec))
            (s_sink, s_id) = self.parameterAsSink(
                parameters, self.SUMMARY, context, s_fields, WKB_NONE, source.sourceCrs())
            if s_sink is not None:
                for r in rows:
                    f = QgsFeature(s_fields)
                    f.setAttributes([
                        round(r[key], prec) if kind == "dbl" and r.get(key) is not None else r.get(key)
                        for _, _, kind, _, prec, key in SUMMARY_FIELDS])
                    s_sink.addFeature(f, FAST_INSERT)
                results[self.SUMMARY] = s_id

        if context.willLoadLayerOnCompletion(dest_id):
            layer_name = f"Spacing {depth_v:g} {u}, {coef_v:g} {r}"
            label = label_expression(self.parameterAsEnum(parameters, self.LABEL, context), units, name_field)
            context.layerToLoadOnCompletionDetails(dest_id).setPostProcessor(
                SpacingStyler.create(name_field, label, layer_name))
        return results
