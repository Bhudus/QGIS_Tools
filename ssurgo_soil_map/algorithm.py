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
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsRendererCategory,
    QgsSymbol,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor

from .sda import SdaError, clipped_mapunits_sql, run_query
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

LABEL_CHOICES = [
    ("Soil name (map unit name)", '"muname"'),
    ("Map unit symbol", '"musym"'),
    ("Symbol and soil name", "\"musym\" || '\\n' || \"muname\""),
]

# Shapefile field names are limited to 10 characters.
OUTPUT_FIELDS = [
    ("mukey", "str", 12, 0),
    ("musym", "str", 12, 0),
    ("muname", "str", 254, 0),
    ("compname", "str", 60, 0),
    ("drainage", "str", 40, 0),
    ("hydgrp", "str", 6, 0),
    ("acres", "dbl", 12, 2),
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
            "Fields: map unit key, symbol and soil name; dominant component; drainage class; "
            "hydrologic group; and acres.\n\n"
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
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Soil map"), SOURCE_POLYGON))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.BOUNDARY, self.tr("Field boundary outline"), SOURCE_POLYGON,
            optional=True, createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))
        dissolve = self.parameterAsBool(parameters, self.DISSOLVE, context)
        label_expr = LABEL_CHOICES[self.parameterAsEnum(parameters, self.LABEL, context)][1]
        outline = self.parameterAsBool(parameters, self.OUTLINE, context)

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

        written = 0
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

            for r in rows:
                g = _polygon_only(QgsGeometry.fromWkt(r.get("wkt") or ""))
                if g.isEmpty():
                    continue
                acres = area_calc.measureArea(g) / SQ_M_PER_ACRE
                g.transform(from_wgs)
                feat = QgsFeature(fields)
                feat.setGeometry(g)
                feat.setAttributes([
                    r.get("mukey"),
                    r.get("musym"),
                    (r.get("muname") or "").split(",")[0].strip() or None,  # drop slope/flooding phase
                    r.get("compname"),
                    r.get("drainagecl"),
                    r.get("hydgrp"),
                    round(acres, 2),
                ])
                sink.addFeature(feat, FAST_INSERT)
                written += 1
            feedback.setProgress(100.0 * (n + 1) / len(parts))

        feedback.pushInfo(self.tr(f"Wrote {written} soil polygons."))

        if context.willLoadLayerOnCompletion(dest_id):
            context.layerToLoadOnCompletionDetails(dest_id).setPostProcessor(SoilStyler.create(label_expr))

        results = {self.OUTPUT: dest_id}
        if outline and parameters.get(self.BOUNDARY) is not None:
            results.update(self._write_boundary(parameters, context, parts, src_crs, from_wgs, area_calc))
        return results

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
