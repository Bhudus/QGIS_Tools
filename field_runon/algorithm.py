"""Processing algorithm: run-on areas of a field (land outside it that drains into it)."""
import os

import numpy as np
from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsMapLayer,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingFeatureSourceDefinition,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProject,
    QgsRectangle,
    QgsUnitTypes,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon

from .core.runon import (accumulation, area_raster, coarsen, field_runon, polygon_mask,
                         routing)

PLUGIN_DIR = os.path.dirname(__file__)
FT_PER_M = 1.0 / 0.3048
Z_FACTORS = [1.0, 0.3048, 1200.0 / 3937.0]     # DEM elevation units -> metres
AC_PER_HA = 2.4710538147

# Settings of the last run, kept while the project stays open; cleared by the plugin
# when a new project is started or another one is loaded.
_LAST_RUN = {}
LAYER_INPUTS = ("FIELD", "DEM", "WATERSHED")


def reset_saved_settings():
    _LAST_RUN.clear()


def _layer_still_there(value):
    if isinstance(value, QgsProcessingFeatureSourceDefinition):
        value = value.source.staticValue()
    if isinstance(value, QgsMapLayer):
        return True
    if not isinstance(value, str) or not value:
        return False
    prj = QgsProject.instance()
    if prj.mapLayer(value) is not None or prj.mapLayersByName(value):
        return True
    return os.path.exists(value.split("|")[0])


def _advanced(param):
    try:
        param.setFlags(param.flags() | Qgis.ProcessingParameterFlag.Advanced)
    except AttributeError:
        param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
    return param


def _rings(geom):
    out = []
    polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
    for poly in polys:
        for ring in poly:
            out.append([(p.x(), p.y()) for p in ring])
    return out


class _Styler(QgsProcessingLayerPostProcessorInterface):
    """Styles the outputs when they are loaded."""
    instances = []

    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def postProcessLayer(self, layer, context, feedback):  # noqa: N802 (QGIS API)
        try:
            if self.kind == "areas":
                from qgis.core import (QgsFillSymbol, QgsPalLayerSettings,
                                       QgsSingleSymbolRenderer, QgsTextBufferSettings,
                                       QgsTextFormat, QgsVectorLayerSimpleLabeling)
                sym = QgsFillSymbol.createSimple({"color": "251,140,0,110",
                                                  "outline_color": "#e65100",
                                                  "outline_width": "0.5"})
                layer.setRenderer(QgsSingleSymbolRenderer(sym))
                s = QgsPalLayerSettings()
                s.fieldName = ("'R' || \"runon_id\" || CASE WHEN \"kind\" = 'sheet' THEN ' sheet' "
                               "ELSE '' END || '\\n' || format_number(\"area_ac\", 1) || ' ac'")
                s.isExpression = True
                tf = QgsTextFormat()
                tf.setSize(9)
                buf = QgsTextBufferSettings()
                buf.setEnabled(True)
                buf.setSize(0.8)
                tf.setBuffer(buf)
                s.setFormat(tf)
                layer.setLabeling(QgsVectorLayerSimpleLabeling(s))
                layer.setLabelsEnabled(True)
            elif self.kind == "inlets":
                from qgis.core import (QgsCategorizedSymbolRenderer, QgsMarkerSymbol,
                                       QgsRendererCategory)
                cats = []
                for kind, shape, col, size, label in (
                        ("inlet", "triangle", "#d50000", "3.6", "Concentrated inlet"),
                        ("sheet", "circle", "#ff8f00", "2.2", "Sheet flow (main entry)"),
                        ("connected", "triangle", "#d50000", "3.2", "Main entry")):
                    sym = QgsMarkerSymbol.createSimple({"name": shape, "color": col,
                                                        "outline_color": "white", "size": size,
                                                        "angle": "180"})
                    cats.append(QgsRendererCategory(kind, sym, label))
                layer.setRenderer(QgsCategorizedSymbolRenderer("kind", cats))
            elif self.kind == "raster":
                from qgis.core import (QgsColorRampShader, QgsRasterShader,
                                       QgsSingleBandPseudoColorRenderer, QgsStyle)
                prov = layer.dataProvider()
                stats = prov.bandStatistics(1)
                ramp = QgsStyle().defaultStyle().colorRamp("YlOrRd")
                shader = QgsColorRampShader(stats.minimumValue, stats.maximumValue, ramp)
                shader.classifyColorRamp(5, -1)
                rs = QgsRasterShader()
                rs.setRasterShaderFunction(shader)
                r = QgsSingleBandPseudoColorRenderer(prov, 1, rs)
                r.setOpacity(0.75)
                layer.setRenderer(r)
            layer.triggerRepaint()
        except Exception as e:  # noqa: BLE001 - styling must never fail the run
            feedback.pushInfo("Styling skipped: {}".format(e))


class FieldRunonAlgorithm(QgsProcessingAlgorithm):
    FIELD = "FIELD"
    NAME_FIELD = "NAME_FIELD"
    DEM = "DEM"
    Z_UNITS = "Z_UNITS"
    WATERSHED = "WATERSHED"
    SPLIT = "SPLIT"
    MIN_AREA = "MIN_AREA"
    INLET_AC = "INLET_AC"
    SMOOTH = "SMOOTH"
    SMOOTH_ITER = "SMOOTH_ITER"
    MAX_CELLS = "MAX_CELLS"
    AREAS = "AREAS"
    RASTER = "RASTER"
    INLETS = "INLETS"

    NOT_SAVED = ("AREAS", "RASTER", "INLETS")

    def tr(self, s):
        return QCoreApplication.translate("FieldRunon", s)

    def createInstance(self):
        return FieldRunonAlgorithm()

    def name(self):
        return "fieldrunon"

    def displayName(self):
        return self.tr("Field run-on areas")

    def group(self):
        return ""

    def groupId(self):
        return ""

    def icon(self):
        return QIcon(os.path.join(PLUGIN_DIR, "icon.png"))

    # ------------------------------------------------------------ remembered settings
    def addParameter(self, definition, createOutput=True):
        nm = definition.name()
        if nm not in self.NOT_SAVED:
            if not hasattr(self, "_saved_names"):
                self._saved_names = []
            self._saved_names.append(nm)
            if nm in _LAST_RUN:
                value = _LAST_RUN[nm]
                try:
                    ok = (definition.checkValueIsAcceptable(value)
                          if hasattr(definition, "checkValueIsAcceptable") else True)
                    if ok and (nm not in LAYER_INPUTS or _layer_still_there(value)):
                        definition.setDefaultValue(value)
                except (ValueError, TypeError):
                    pass
        return super().addParameter(definition, createOutput)

    def _save_settings(self, parameters):
        for nm in getattr(self, "_saved_names", []):
            if nm in parameters:
                value = parameters[nm]
                if isinstance(value, QgsMapLayer):
                    value = value.id()
                elif isinstance(value, QgsProcessingFeatureSourceDefinition):
                    value = QgsProcessingFeatureSourceDefinition(value)
                _LAST_RUN[nm] = value

    def shortHelpString(self):
        return self.tr(
            "<p>Maps the <b>run-on areas</b> of a field: the land outside it whose surface "
            "water flows into it, from the field boundary and a DEM of its watershed.</p>"
            "<p>Water is routed by D8 steepest descent over the Wang &amp; Liu filled DEM "
            "(re-filled with a tiny gradient so flats and filled depressions drain to their "
            "spill point). A cell outside the field is run-on when its flow path enters the "
            "field.</p>"
            "<p><b>Split</b>: <i>by inlet</i> gives one area for each place water enters the "
            "field. Boundary cells carrying at least the inlet acres are concentrated "
            "inlets (kind = inlet), where a waterway or surface inlet would go; the stretches "
            "of boundary between them take sheet flow (kind = sheet). <i>Connected areas</i> "
            "gives 8-connected groups of run-on land.</p>"
            "<p><b>Outputs</b>: run-on areas (polygons, optionally smoothed) with acres and "
            "hectares, share of the field area and an <i>open</i> flag; a raster of each "
            "run-on cell's area (acres); and the inlet points with the acres they carry.</p>"
            "<p><b>Open</b> areas reach the edge of the DEM (or its no-data), so they may go "
            "on beyond it and their area is a minimum: use a DEM covering the whole "
            "watershed, for example the StreamStats watershed with a LiDAR or 3DEP DEM.</p>"
            "<p>Several fields are done one at a time; each gets its own run-on (land "
            "draining to two fields appears in both).</p>")

    def initAlgorithm(self, config=None):
        dbl = QgsProcessingParameterNumber.Double
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.FIELD, self.tr("Field boundary (polygons; selected features are honoured)"),
            [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterField(
            self.NAME_FIELD, self.tr("Field name attribute (optional)"),
            parentLayerParameterName=self.FIELD, optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, self.tr("DEM of the field's watershed (projected CRS)")))
        self.addParameter(QgsProcessingParameterEnum(
            self.Z_UNITS, self.tr("DEM elevation units"),
            options=[self.tr("Metres"), self.tr("International feet"),
                     self.tr("US survey feet")], defaultValue=2))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.WATERSHED, self.tr("Watershed (optional; e.g. StreamStats). Limits the "
                                    "analysis to it"),
            [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.SPLIT, self.tr("Split the run-on"),
            options=[self.tr("By inlet: one area per place water enters the field"),
                     self.tr("Connected areas")], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.INLET_AC, self.tr("By inlet: an inlet carries at least (acres); the edge "
                                   "between inlets takes sheet flow"), dbl,
            defaultValue=2.0, minValue=0.01))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_AREA, self.tr("Smallest run-on area to keep (acres)"), dbl,
            defaultValue=0.5, minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SMOOTH, self.tr("Smooth the boundaries of the run-on polygons"),
            defaultValue=True))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SMOOTH_ITER, self.tr("Smoothing passes (Chaikin)"),
            QgsProcessingParameterNumber.Integer, defaultValue=3, minValue=1, maxValue=10)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MAX_CELLS, self.tr("Most DEM cells to route (larger DEMs are resampled "
                                    "coarser)"), QgsProcessingParameterNumber.Integer,
            defaultValue=6000000, minValue=100000)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.AREAS, self.tr("Run-on areas"), QgsProcessing.TypeVectorPolygon))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.RASTER, self.tr("Run-on area raster (acres)")))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.INLETS, self.tr("Run-on inlets"), QgsProcessing.TypeVectorPoint,
            optional=True, createByDefault=True))

    # ------------------------------------------------------------ helpers
    def _read_dem(self, layer, rect, zf_ft):
        from osgeo import gdal
        ds = gdal.Open(layer.source())
        if ds is None:
            raise QgsProcessingException(self.tr("GDAL could not open the DEM: ") +
                                         layer.source())
        gt = ds.GetGeoTransform()
        if gt[2] != 0 or gt[4] != 0:
            raise QgsProcessingException(self.tr("The DEM must be north-up (not rotated)."))
        if rect is None:
            c0, r0, c1, r1 = 0, 0, ds.RasterXSize, ds.RasterYSize
        else:
            c0 = max(0, int(np.floor((rect.xMinimum() - gt[0]) / gt[1])))
            c1 = min(ds.RasterXSize, int(np.ceil((rect.xMaximum() - gt[0]) / gt[1])))
            r0 = max(0, int(np.floor((rect.yMaximum() - gt[3]) / gt[5])))
            r1 = min(ds.RasterYSize, int(np.ceil((rect.yMinimum() - gt[3]) / gt[5])))
        if c1 <= c0 or r1 <= r0:
            raise QgsProcessingException(self.tr("The DEM does not cover the area."))
        if (c1 - c0) * (r1 - r0) > 400e6:
            raise QgsProcessingException(self.tr(
                "The DEM is too large to read ({:,} cells): clip or resample it first.")
                .format((c1 - c0) * (r1 - r0)))
        band = ds.GetRasterBand(1)
        z = band.ReadAsArray(c0, r0, c1 - c0, r1 - r0).astype(float)
        nd = band.GetNoDataValue()
        if nd is not None:
            z[z == nd] = np.nan
        z[z < -1e20] = np.nan
        z *= zf_ft
        return z, gt[0] + c0 * gt[1], gt[3] + r0 * gt[5], gt[1], gt[5]

    def _polygonize(self, labels, x0, y0, dx, dy):
        from osgeo import gdal, ogr
        mem = gdal.GetDriverByName("MEM")
        ds = mem.Create("", labels.shape[1], labels.shape[0], 1, gdal.GDT_Int32)
        ds.SetGeoTransform((x0, dx, 0, y0, 0, dy))
        ds.GetRasterBand(1).WriteArray(labels.astype(np.int32))
        ms = mem.Create("", labels.shape[1], labels.shape[0], 1, gdal.GDT_Byte)
        ms.SetGeoTransform((x0, dx, 0, y0, 0, dy))
        ms.GetRasterBand(1).WriteArray((labels > 0).astype(np.uint8))
        drv = ogr.GetDriverByName("Memory") or ogr.GetDriverByName("MEM")
        ods = drv.CreateDataSource("p")
        ol = ods.CreateLayer("p", geom_type=ogr.wkbPolygon)
        ol.CreateField(ogr.FieldDefn("lab", ogr.OFTInteger))
        gdal.Polygonize(ds.GetRasterBand(1), ms.GetRasterBand(1), ol, 0, [], callback=None)
        parts = {}
        for f in ol:
            q = QgsGeometry()
            q.fromWkb(bytes(f.GetGeometryRef().ExportToWkb()))
            parts.setdefault(f.GetField(0), []).append(q)
        return {k: QgsGeometry.unaryUnion(v) for k, v in parts.items() if k}

    def _write_raster(self, path, arr, x0, y0, dx, dy, crs):
        from osgeo import gdal
        drv = gdal.GetDriverByName("GTiff")
        ds = drv.Create(path, arr.shape[1], arr.shape[0], 1, gdal.GDT_Float32,
                        ["COMPRESS=DEFLATE", "TILED=YES"])
        ds.SetGeoTransform((x0, dx, 0, y0, 0, dy))
        ds.SetProjection(crs.toWkt())
        b = ds.GetRasterBand(1)
        b.SetNoDataValue(-9999.0)
        b.WriteArray(np.where(np.isfinite(arr), arr, -9999.0).astype(np.float32))
        b.SetDescription("Run-on area (acres)")
        ds.FlushCache()
        ds = None

    # ------------------------------------------------------------ main
    def processAlgorithm(self, parameters, context, feedback):
        self._save_settings(parameters)
        P = parameters
        dem = self.parameterAsRasterLayer(P, self.DEM, context)
        if dem is None:
            raise QgsProcessingException(self.tr("Could not load the DEM."))
        crs = dem.crs()
        if crs.isGeographic():
            raise QgsProcessingException(self.tr(
                "The DEM must be in a projected coordinate system (metres or feet)."))
        ft = QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), Qgis.DistanceUnit.Feet)
        zf_ft = Z_FACTORS[self.parameterAsEnum(P, self.Z_UNITS, context)] * FT_PER_M
        tctx = context.transformContext()

        # fields
        src = self.parameterAsSource(P, self.FIELD, context)
        if src is None:
            raise QgsProcessingException(self.tr("Could not load the field boundary."))
        name_f = self.parameterAsString(P, self.NAME_FIELD, context)
        tf = QgsCoordinateTransform(src.sourceCrs(), crs, tctx)
        fields = []
        for f in src.getFeatures():
            g = QgsGeometry(f.geometry())
            if g.isNull() or g.isEmpty():
                continue
            g.transform(tf)
            g = g.makeValid()
            nm = f[name_f] if name_f else None
            nm = str(f.id()) if nm in (None, "") or (hasattr(nm, "isNull") and nm.isNull()) \
                else str(nm)
            fields.append((nm, g))
        if not fields:
            raise QgsProcessingException(self.tr("The field boundary has no polygons."))

        # watershed (optional)
        ws = None
        wsrc = self.parameterAsSource(P, self.WATERSHED, context)
        if wsrc is not None:
            tw = QgsCoordinateTransform(wsrc.sourceCrs(), crs, tctx)
            gs = []
            for f in wsrc.getFeatures():
                g = QgsGeometry(f.geometry())
                if not g.isNull():
                    g.transform(tw)
                    gs.append(g.makeValid())
            if gs:
                ws = QgsGeometry.unaryUnion(gs)
                for nm, g in fields:
                    if not ws.contains(g):
                        feedback.reportError(self.tr(
                            "Field {}: not wholly inside the watershed; only the part "
                            "inside is analysed.").format(nm), fatalError=False)

        # DEM
        feedback.setProgressText(self.tr("Reading the DEM"))
        rect = None
        if ws is not None:
            rect = QgsRectangle(ws.boundingBox())
            for _, g in fields:
                rect.combineExtentWith(g.boundingBox())
        z, x0, y0, dx, dy = self._read_dem(dem, rect, zf_ft)
        cells = z.size
        max_cells = self.parameterAsInt(P, self.MAX_CELLS, context)
        factor = int(np.ceil(np.sqrt(cells / float(max_cells)))) if cells > max_cells else 1
        if factor > 1:
            z = coarsen(z, factor)
            dx, dy = dx * factor, dy * factor
            feedback.pushInfo(self.tr("DEM resampled to {:.1f} ft cells ({:,} cells) for "
                                      "routing.").format(abs(dx) * ft, z.size))
        cell_ft = abs(dx) * ft
        if ws is not None:
            wm = polygon_mask(_rings(ws), x0, y0, dx, dy, *z.shape)
            z = np.where(wm, z, np.nan)
        if not np.isfinite(z).any():
            raise QgsProcessingException(self.tr("The DEM has no data over the area."))
        feedback.pushInfo(self.tr("Routing {:,} cells of {:.1f} ft").format(
            int(np.isfinite(z).sum()), cell_ft))

        # routing
        feedback.setProgressText(self.tr("Filling and routing"))
        try:
            route = routing(z, cell_ft, progress=lambda f: feedback.setProgress(5 + 55 * f),
                            is_canceled=feedback.isCanceled)
            acc = accumulation(route, feedback.isCanceled)
        except RuntimeError:
            raise QgsProcessingException(self.tr("Canceled."))
        feedback.setProgress(65)

        # outputs
        out_f = QgsFields()
        for nm, t in (("field", QVariant.String), ("runon_id", QVariant.Int),
                      ("kind", QVariant.String),
                      ("area_ac", QVariant.Double), ("area_ha", QVariant.Double),
                      ("pct_field", QVariant.Double), ("open", QVariant.Int),
                      ("inlet_ac", QVariant.Double), ("n_inlet", QVariant.Int),
                      ("field_ac", QVariant.Double)):
            out_f.append(QgsField(nm, t))
        sink, sink_id = self.parameterAsSink(P, self.AREAS, context, out_f,
                                             QgsWkbTypes.MultiPolygon, crs)
        in_f = QgsFields()
        for nm, t in (("field", QVariant.String), ("runon_id", QVariant.Int),
                      ("kind", QVariant.String),
                      ("inlet_ac", QVariant.Double), ("area_ac", QVariant.Double),
                      ("x", QVariant.Double), ("y", QVariant.Double)):
            in_f.append(QgsField(nm, t))
        isink, isink_id = self.parameterAsSink(P, self.INLETS, context, in_f,
                                               QgsWkbTypes.Point, crs)
        split = "inlet" if self.parameterAsEnum(P, self.SPLIT, context) == 0 else "connected"
        min_ac = self.parameterAsDouble(P, self.MIN_AREA, context)
        inlet_ac = self.parameterAsDouble(P, self.INLET_AC, context)
        smooth = self.parameterAsBool(P, self.SMOOTH, context)
        iters = self.parameterAsInt(P, self.SMOOTH_ITER, context)
        araster = np.full(z.shape, np.nan)
        any_open = False
        for k, (nm, g) in enumerate(fields):
            if feedback.isCanceled():
                break
            feedback.setProgressText(self.tr("Run-on of field {}").format(nm))
            fm = polygon_mask(_rings(g), x0, y0, dx, dy, *z.shape)
            if not fm.any():
                feedback.reportError(self.tr("Field {}: smaller than one DEM cell or outside "
                                             "the DEM; skipped.").format(nm), fatalError=False)
                continue
            try:
                res = field_runon(route, fm, cell_ft, split=split, min_ac=min_ac, acc=acc,
                                  inlet_ac=inlet_ac, is_canceled=feedback.isCanceled)
            except RuntimeError:
                raise QgsProcessingException(self.tr("Canceled."))
            fac = res["field_ac"]
            polys = self._polygonize(res["labels"], x0, y0, dx, dy) if res["areas"] else {}
            ar = area_raster(res["labels"], res["areas"])
            araster = np.where(np.isfinite(ar) & ~(araster >= ar), ar, araster)
            for s in res["areas"]:
                pg = polys.get(s["id"])
                if pg is None:
                    continue
                if smooth:
                    pg = pg.simplify(abs(dx) * 0.6).smooth(iters, 0.25)
                    pg = pg.difference(g).makeValid()
                    if pg.isEmpty():
                        continue
                pg.convertToMultiType()
                feat = QgsFeature(out_f)
                feat.setGeometry(pg)
                feat.setAttributes([nm, s["id"], s["kind"], round(s["area_ac"], 3),
                                    round(s["area_ac"] / AC_PER_HA, 3),
                                    round(100.0 * s["area_ac"] / fac, 1) if fac else None,
                                    1 if s["open"] else 0, round(s["inlet_ac"], 3),
                                    s["inlet_cells"], round(fac, 2)])
                sink.addFeature(feat, QgsFeatureSink.FastInsert)
                if isink is not None and s["inlet"] is not None:
                    r, c = s["inlet"]
                    px, py = x0 + (c + 0.5) * dx, y0 + (r + 0.5) * dy
                    pf = QgsFeature(in_f)
                    pf.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(px, py)))
                    pf.setAttributes([nm, s["id"], s["kind"], round(s["inlet_ac"], 3),
                                      round(s["area_ac"], 3), px, py])
                    isink.addFeature(pf, QgsFeatureSink.FastInsert)
            n_open = sum(1 for s in res["areas"] if s["open"])
            if split == "inlet":
                ina = [s for s in res["areas"] if s["kind"] == "inlet"]
                feedback.pushInfo(self.tr("Field {}: {} concentrated inlet(s) carrying {:.1f} "
                                          "ac; {:.1f} ac comes in as sheet flow.").format(
                    nm, len(ina), sum(s["area_ac"] for s in ina),
                    sum(s["area_ac"] for s in res["areas"] if s["kind"] == "sheet")))
            any_open = any_open or n_open > 0
            feedback.pushInfo(self.tr(
                "Field {}: {:.1f} ac; run-on {:.1f} ac in {} area(s) of at least {:g} ac "
                "({:.1f} ac in smaller areas not mapped); {} open.").format(
                    nm, fac, res["total_ac"], len(res["areas"]), min_ac, res["dropped_ac"],
                    n_open))
            feedback.setProgress(65 + int(30 * (k + 1) / len(fields)))
        if any_open:
            feedback.reportError(self.tr(
                "Some run-on areas reach the edge of the DEM (open = 1): they may extend "
                "beyond it, so their areas are minimums. Use a DEM covering the whole "
                "watershed."), fatalError=False)

        rpath = self.parameterAsOutputLayer(P, self.RASTER, context)
        self._write_raster(rpath, araster, x0, y0, dx, dy, crs)
        if context.willLoadLayerOnCompletion(sink_id):
            st = _Styler("areas")
            _Styler.instances.append(st)
            context.layerToLoadOnCompletionDetails(sink_id).setPostProcessor(st)
        if isink_id and context.willLoadLayerOnCompletion(isink_id):
            st = _Styler("inlets")
            _Styler.instances.append(st)
            context.layerToLoadOnCompletionDetails(isink_id).setPostProcessor(st)
        if context.willLoadLayerOnCompletion(rpath):
            st = _Styler("raster")
            _Styler.instances.append(st)
            context.layerToLoadOnCompletionDetails(rpath).setPostProcessor(st)
        feedback.setProgress(100)
        out = {self.AREAS: sink_id, self.RASTER: rpath}
        if isink_id:
            out[self.INLETS] = isink_id
        return out
