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
    QgsRectangle,
    QgsRendererCategory,
    QgsSymbol,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor

from .hooghoudt import spacing_for_layers
from .rosetta import ksat_for_layers
from .soilgrids import IGH_PROJ, SoilGridsError, fetch_layers, post_json, snap_bbox
from .style import apply_boundary_style
from .zoning import mean_profile, pixel_profile, zone_key, zone_name

SQ_M_PER_HA = 10000.0


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
    ("Soil zone (texture above and below the drains)", '"soil"'),
    ("Drain spacing (m)", "format_number(\"spacing_m\", 0) || ' m'"),
    ("Soil zone and drain spacing", "\"soil\" || '\\n' || format_number(\"spacing_m\", 0) || ' m'"),
    ("Subirrigation spacing (m)", "format_number(\"subirr_m\", 0) || ' m'"),
    ("Soil zone and subirrigation spacing", "\"soil\" || '\\n' || format_number(\"subirr_m\", 0) || ' m'"),
]

# Shapefile field names are limited to 10 characters.
OUTPUT_FIELDS = [
    ("field_id", "int", 10, 0),
    ("zone", "int", 6, 0),
    ("soil", "str", 80, 0),         # e.g. "silt loam over clay loam"
    ("tex_above", "str", 20, 0),    # USDA texture, surface to drain depth
    ("tex_below", "str", 20, 0),    # USDA texture, drain depth to barrier
    ("hectares", "dbl", 12, 2),
    ("ka_mday", "dbl", 12, 3),      # Ksat above the drains
    ("kb_mday", "dbl", 12, 3),      # Ksat from the drains to the barrier
    ("barrier_m", "dbl", 8, 2),     # depth to impermeable layer
    ("spacing_m", "int", 6, 0),     # Hooghoudt drain spacing, nearest 1 m
    ("ka_sub", "dbl", 12, 3),       # Ksat of the saturated zone above drains, subirrigation
    ("subirr_m", "int", 6, 0),      # inverse Hooghoudt subirrigation spacing, nearest 1 m
    ("ksat_src", "str", 60, 0),
]


def _make_field(name, kind, length, prec):
    if Qgis.QGIS_VERSION_INT >= 33800:
        from qgis.PyQt.QtCore import QMetaType
        qtype = {"str": QMetaType.Type.QString, "int": QMetaType.Type.Int, "dbl": QMetaType.Type.Double}[kind]
    else:
        from qgis.PyQt.QtCore import QVariant
        qtype = {"str": QVariant.String, "int": QVariant.Int, "dbl": QVariant.Double}[kind]
    return QgsField(name, qtype, len=length, prec=prec)


def _round(v, n):
    return round(v, n) if v is not None else None


def _round_m(v):
    """Spacing to the nearest metre (halves round up), never below 1 m."""
    if v is None:
        return None
    return int(max(1, int(v + 0.5)))


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


def _save_qml(layer):
    path = layer.source().split("|")[0]
    if path.lower().endswith(".shp") and os.path.exists(path):
        layer.saveNamedStyle(os.path.splitext(path)[0] + ".qml")


class ZoneStyler(QgsProcessingLayerPostProcessorInterface):
    """Colors each soil zone differently, labels it, and saves a .qml style next to a shapefile."""

    instance = None  # Processing needs this kept alive until the layer loads.

    def __init__(self, label_expression):
        super().__init__()
        self.label_expression = label_expression

    @staticmethod
    def create(label_expression):
        ZoneStyler.instance = ZoneStyler(label_expression)
        return ZoneStyler.instance

    def postProcessLayer(self, layer, context, feedback):
        idx = layer.fields().indexOf("soil")
        if idx < 0:
            return
        names = sorted(v for v in layer.uniqueValues(idx) if v)
        categories = []
        for i, name in enumerate(names):
            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            hue = int((i * 137.508) % 360)  # golden-angle spacing keeps neighbors distinct
            symbol.setColor(QColor.fromHsv(hue, 120, 225))
            symbol.setOpacity(0.85)
            categories.append(QgsRendererCategory(name, symbol, name))
        layer.setRenderer(QgsCategorizedSymbolRenderer("soil", categories))

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
        _save_qml(layer)


class BoundaryStyler(QgsProcessingLayerPostProcessorInterface):
    """Draws the field boundary as a bold outline and moves it above the soil zones."""

    instance = None

    @staticmethod
    def create():
        BoundaryStyler.instance = BoundaryStyler()
        return BoundaryStyler.instance

    def postProcessLayer(self, layer, context, feedback):
        apply_boundary_style(layer)
        _save_qml(layer)
        layer_id = layer.id()
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


class SoilGridsDrainageAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    DISSOLVE = "DISSOLVE"
    LABEL = "LABEL"
    OUTLINE = "OUTLINE"
    DRAIN_DEPTH = "DRAIN_DEPTH"
    DRAIN_COEF = "DRAIN_COEF"
    DRAIN_DIAM = "DRAIN_DIAM"
    BARRIER = "BARRIER"
    SUB_RATE = "SUB_RATE"
    SUB_HW = "SUB_HW"
    SUB_HM = "SUB_HM"
    OUTPUT = "OUTPUT"
    BOUNDARY = "BOUNDARY"

    def tr(self, text):
        return QCoreApplication.translate("SoilGridsDrainageAlgorithm", text)

    def createInstance(self):
        return SoilGridsDrainageAlgorithm()

    def name(self):
        return "soilzones"

    def displayName(self):
        return self.tr("Get drainage soil zones for polygons (SoilGrids)")

    def group(self):
        return self.tr("Soils")

    def groupId(self):
        return "soils"

    def shortHelpString(self):
        return self.tr(
            "For fields anywhere in the world. Downloads ISRIC SoilGrids 2.0 (250 m) sand, clay and "
            "bulk density at six depths to 2 m for each input polygon, groups the 250 m cells into soil "
            "zones by USDA texture above and below the drains (e.g. \"silt loam over clay loam\"), and "
            "gives each zone a drain spacing and a subirrigation spacing, in metres.\n\n"
            "Ksat for each zone and depth comes from Rosetta 3 (sand, silt, clay and bulk density), or "
            "from Rosetta texture-class averages if the Rosetta service can't be reached (see ksat_src).\n\n"
            "Drain spacing (spacing_m) uses the Hooghoudt equation, steady state, with the water table "
            "midway between drains at the soil surface: q L² = 8 Kb d h + 4 Ka h², where q is the "
            "drainage coefficient, h the drain depth and d the equivalent depth (van der Molen and "
            "Wesseling, 1991). Ka (ka_mday) is the thickness-weighted Ksat from the surface to the "
            "drains; Kb (kb_mday) from the drains to the impermeable layer.\n\n"
            "Subirrigation spacing (subirr_m) uses the inverse Hooghoudt equation: "
            "q L² = 8 Kb d (Hw − Hm) + 4 Ka (Hw² − Hm²), where q is the irrigation coefficient, Hw the "
            "water level held at the drains and Hm the water table midway between drains, both in metres "
            "above the drains. Ka (ka_sub) is averaged over the saturated zone above the drains.\n\n"
            "SoilGrids has no restrictive-layer data, so enter the depth to the impermeable layer. Below "
            "2 m the 1-2 m layer is assumed to continue down to it. Spacings are rounded to the nearest "
            "metre.\n\n"
            "SoilGrids values are machine-learning predictions at 250 m, not mapped soils: treat the "
            "zones and spacings as a first estimate. Needs an internet connection."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Field boundary layer (shapefile or other polygons)"),
            [SOURCE_POLYGON]))
        self.addParameter(QgsProcessingParameterBoolean(
            self.DISSOLVE, self.tr("Merge all polygons into one area before querying"), defaultValue=False))
        self.addParameter(QgsProcessingParameterEnum(
            self.LABEL, self.tr("Label zones with"), options=[c[0] for c in LABEL_CHOICES], defaultValue=2))
        self.addParameter(QgsProcessingParameterBoolean(
            self.OUTLINE, self.tr("Outline the field boundary on the soil zone map"), defaultValue=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.DRAIN_DEPTH, self.tr("Drain depth (m)"), NUMBER_DOUBLE,
            defaultValue=1.0, minValue=0.2, maxValue=5.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.DRAIN_COEF, self.tr("Drainage coefficient (mm/day)"), NUMBER_DOUBLE,
            defaultValue=10.0, minValue=0.5, maxValue=100.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.BARRIER, self.tr("Depth to impermeable layer (m)"), NUMBER_DOUBLE,
            defaultValue=2.0, minValue=0.3, maxValue=20.0))
        diam = QgsProcessingParameterNumber(
            self.DRAIN_DIAM, self.tr("Drain diameter for equivalent depth (mm)"), NUMBER_DOUBLE,
            defaultValue=100.0, minValue=25.0, maxValue=1000.0)
        diam.setFlags(diam.flags() | FLAG_ADVANCED)
        self.addParameter(diam)
        self.addParameter(QgsProcessingParameterNumber(
            self.SUB_RATE, self.tr("Irrigation coefficient for subirrigation (mm/day)"), NUMBER_DOUBLE,
            defaultValue=6.0, minValue=0.5, maxValue=50.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SUB_HW, self.tr("Subirrigation water level at the drains, height above drain (m)"),
            NUMBER_DOUBLE, defaultValue=0.6, minValue=0.0, maxValue=5.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SUB_HM, self.tr("Subirrigation mid-drain water table, height above drain (m)"),
            NUMBER_DOUBLE, defaultValue=0.45, minValue=0.0, maxValue=5.0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Soil zones"), SOURCE_POLYGON))
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
        drain_depth = self.parameterAsDouble(parameters, self.DRAIN_DEPTH, context)
        coef = self.parameterAsDouble(parameters, self.DRAIN_COEF, context)
        barrier = self.parameterAsDouble(parameters, self.BARRIER, context)
        diam = self.parameterAsDouble(parameters, self.DRAIN_DIAM, context)
        sub_rate = self.parameterAsDouble(parameters, self.SUB_RATE, context)
        sub_hw = self.parameterAsDouble(parameters, self.SUB_HW, context)
        sub_hm = self.parameterAsDouble(parameters, self.SUB_HM, context)
        feedback.pushInfo(self.tr(
            f"Drain spacing: drains {drain_depth:g} m deep, drainage coefficient {coef:g} mm/day, "
            f"{diam:g} mm drains, impermeable layer at {barrier:g} m, water table at the surface "
            "midway between drains."))
        feedback.pushInfo(self.tr(
            f"Subirrigation spacing: irrigation coefficient {sub_rate:g} mm/day, water held "
            f"{sub_hw:g} m above the drains, mid-drain water table {sub_hm:g} m above the drains."))
        if barrier <= drain_depth:
            feedback.pushWarning(self.tr(
                "The impermeable layer is not below the drains; spacings use flow above the drains only."))

        src_crs = source.sourceCrs()
        if not src_crs.isValid():
            raise QgsProcessingException(self.tr(
                "The input layer has no coordinate reference system. Set its CRS (for a shapefile, "
                "the .prj file) and run again."))
        igh = QgsCoordinateReferenceSystem.fromProj(IGH_PROJ)
        if not igh.isValid():
            raise QgsProcessingException(self.tr("QGIS could not set up the SoilGrids (Homolosine) projection."))
        to_igh = QgsCoordinateTransform(src_crs, igh, context.transformContext())
        from_igh = QgsCoordinateTransform(igh, src_crs, context.transformContext())

        # Areas to query, in Homolosine (an equal-area projection, so planar area is true area).
        parts = []
        for f in source.getFeatures(QgsFeatureRequest()):
            if feedback.isCanceled():
                return {}
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            g = QgsGeometry(g)
            g.transform(to_igh)
            g = _polygon_only(g)
            if g.isEmpty():
                feedback.pushWarning(self.tr(f"Skipped feature {f.id()}: no polygon area."))
                continue
            parts.append((f.id(), g))
        if not parts:
            raise QgsProcessingException(self.tr("The input layer has no polygons to query."))
        if dissolve:
            parts = [(0, _polygon_only(QgsGeometry.unaryUnion([g for _, g in parts])))]

        # Build zones: (field id, texture key) -> pixel pieces, profiles and areas.
        zones = {}
        for n, (fid, geom) in enumerate(parts):
            if feedback.isCanceled():
                return {}
            ha = geom.area() / SQ_M_PER_HA
            feedback.pushInfo(self.tr(f"Downloading SoilGrids for polygon {fid} ({ha:,.2f} ha)…"))
            box = geom.boundingBox()
            bbox = snap_bbox(box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())
            try:
                raw, gt = fetch_layers(bbox, feedback)
            except SoilGridsError as e:
                raise QgsProcessingException(str(e))
            rows, cols = raw["sand"][0].shape
            no_data_ha = 0.0
            for i in range(rows):
                for j in range(cols):
                    x0 = gt[0] + j * gt[1]
                    y0 = gt[3] + i * gt[5]
                    cell = QgsGeometry.fromRect(QgsRectangle(
                        min(x0, x0 + gt[1]), min(y0, y0 + gt[5]), max(x0, x0 + gt[1]), max(y0, y0 + gt[5])))
                    if not cell.intersects(geom):
                        continue
                    piece = cell.intersection(geom)
                    area = piece.area()
                    if piece.isEmpty() or area <= 0:
                        continue
                    profile = pixel_profile(raw, i, j)
                    if profile is None:
                        no_data_ha += area / SQ_M_PER_HA
                        continue
                    key = zone_key(profile, drain_depth, barrier)
                    z = zones.setdefault((fid, key), {"pieces": [], "profiles": [], "areas": []})
                    z["pieces"].append(piece)
                    z["profiles"].append(profile)
                    z["areas"].append(area)
            if no_data_ha > 0:
                feedback.pushWarning(self.tr(
                    f"Polygon {fid}: {no_data_ha:,.2f} ha has no SoilGrids data (water, urban or "
                    "outside the soil mask) and is left out."))
            feedback.setProgress(70.0 + 15.0 * (n + 1) / len(parts))
        if not zones:
            raise QgsProcessingException(self.tr("SoilGrids returned no soil data for these polygons."))

        # Ksat for every zone and layer in one Rosetta request.
        keys = sorted(zones, key=lambda k: (k[0], zone_name(k[1])))
        rows = []
        for k in keys:
            zones[k]["mean"] = mean_profile(zones[k]["profiles"], zones[k]["areas"])
            rows.extend((s, si, c, bd) for _, _, s, si, c, bd in zones[k]["mean"])
        feedback.pushInfo(self.tr(f"Estimating Ksat with Rosetta 3 for {len(keys)} zones…"))
        ksat, ksat_src = ksat_for_layers(rows, post_json)
        if not ksat_src.startswith("Rosetta 3"):
            feedback.pushWarning(self.tr(f"Ksat: {ksat_src}"))

        fields = QgsFields()
        for spec in OUTPUT_FIELDS:
            fields.append(_make_field(*spec))
        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, WKB_MULTIPOLYGON, src_crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        zone_no = {}
        for idx, k in enumerate(keys):
            fid, key = k
            z = zones[k]
            ks = ksat[idx * 6:(idx + 1) * 6]
            layers = [(top, bot, kv) for (top, bot, *_), kv in zip(z["mean"], ks)]
            dr = spacing_for_layers(layers, barrier, drain_depth, coef, diam)
            sub = spacing_for_layers(layers, barrier, drain_depth, sub_rate, diam,
                                     mid_m=sub_hm, drain_level_m=sub_hw)
            geom = _polygon_only(QgsGeometry.unaryUnion(z["pieces"]))
            if geom.isEmpty():
                continue
            hectares = geom.area() / SQ_M_PER_HA
            geom.transform(from_igh)
            zone_no[fid] = zone_no.get(fid, 0) + 1
            feat = QgsFeature(fields)
            feat.setGeometry(geom)
            feat.setAttributes([
                int(fid),
                zone_no[fid],
                zone_name(key),
                key[0],
                key[1],
                round(hectares, 2),
                _round(dr["ka"], 3),
                _round(dr["kb"], 3),
                round(barrier, 2),
                _round_m(dr["spacing_m"]),
                _round(sub["ka"], 3),
                _round_m(sub["spacing_m"]),
                ksat_src[:60],
            ])
            sink.addFeature(feat, FAST_INSERT)
        feedback.setProgress(100.0)
        feedback.pushInfo(self.tr(f"Wrote {len(keys)} soil zones."))

        if context.willLoadLayerOnCompletion(dest_id):
            context.layerToLoadOnCompletionDetails(dest_id).setPostProcessor(ZoneStyler.create(label_expr))

        results = {self.OUTPUT: dest_id}
        if outline and parameters.get(self.BOUNDARY) is not None:
            results.update(self._write_boundary(parameters, context, parts, src_crs, from_igh))
        return results

    def _write_boundary(self, parameters, context, parts, src_crs, from_igh):
        fields = QgsFields()
        fields.append(_make_field("hectares", "dbl", 12, 2))
        (sink, dest_id) = self.parameterAsSink(
            parameters, self.BOUNDARY, context, fields, WKB_MULTIPOLYGON, src_crs)
        if sink is None:
            return {}
        for _, geom in parts:
            g = QgsGeometry(geom)
            hectares = g.area() / SQ_M_PER_HA
            g.transform(from_igh)
            feat = QgsFeature(fields)
            feat.setGeometry(g)
            feat.setAttributes([round(hectares, 2)])
            sink.addFeature(feat, FAST_INSERT)
        if context.willLoadLayerOnCompletion(dest_id):
            context.layerToLoadOnCompletionDetails(dest_id).setPostProcessor(BoundaryStyler.create())
        return {self.BOUNDARY: dest_id}
