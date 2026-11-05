# -*- coding: utf-8 -*-
"""Depressions and Laylines plugin - processing algorithm for subsurface drainage design.

Depressions: depth = filled DEM - unfilled DEM; cells deeper than a threshold
(default 0.75 ft) inside the field are grouped (8-connected) into polygons with
area, depth, storage volume and pond level.

Laylines: the flow channels within the field. Flow is routed D8 (steepest
descent) over the filled DEM, so water crosses filled depressions toward their
spill point; cells whose contributing area reaches a threshold become channel
cells, which are joined into polylines between heads, junctions and outlets,
with Strahler order and contributing area, and clipped to the field boundary.
Routing uses the whole DEM, so water from the margin around the field counts.
"""

import math
import os

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsLineSymbol,
    QgsFillSymbol,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProperty,
    QgsSingleSymbolRenderer,
    QgsSymbolLayer,
    QgsUnitTypes,
    QgsWkbTypes,
)

FT_US = 1200.0 / 3937.0            # metres per US survey foot
SQ_FT_PER_ACRE = 43560.0
CU_FT_PER_CU_YD = 27.0
ROUTING_EPS_Z = 1e-4               # rise per cell added on flats of the routing surface

_POSTPROCESSORS = []               # keep stylers alive until QGIS calls them


class _Styler(QgsProcessingLayerPostProcessorInterface):
    """Gives the outputs a readable default style when they are loaded."""

    def __init__(self, kind, name=None, decimals=0):
        super().__init__()
        self.kind = kind
        self.name = name
        self.decimals = decimals

    def postProcessLayer(self, layer, context, feedback):  # noqa: N802
        try:
            if self.name:
                layer.setName(self.name)
            if self.kind == "depressions":
                sym = QgsFillSymbol.createSimple({
                    "color": "31,120,180,110", "outline_color": "8,69,148",
                    "outline_width": "0.3"})
                layer.setRenderer(QgsSingleSymbolRenderer(sym))
            elif self.kind == "laylines":
                sym = QgsLineSymbol.createSimple({
                    "color": "8,48,107", "width": "0.4", "capstyle": "round",
                    "joinstyle": "round"})
                prop = getattr(QgsSymbolLayer, "PropertyStrokeWidth", None)
                if prop is None:                      # newer QGIS
                    from qgis.core import Qgis
                    prop = Qgis.SymbolLayerProperty.StrokeWidth
                sym.symbolLayer(0).setDataDefinedProperty(
                    prop,
                    QgsProperty.fromExpression('0.25 + 0.35 * ("strahler" - 1)'))
                layer.setRenderer(QgsSingleSymbolRenderer(sym))
            elif self.kind == "contours":
                sym = QgsLineSymbol.createSimple({"color": "75,60,50", "width": "0.2"})
                prop = getattr(QgsSymbolLayer, "PropertyStrokeWidth", None)
                if prop is None:
                    from qgis.core import Qgis
                    prop = Qgis.SymbolLayerProperty.StrokeWidth
                sym.symbolLayer(0).setDataDefinedProperty(
                    prop, QgsProperty.fromExpression(
                        'CASE WHEN "is_index" = 1 THEN 0.45 ELSE 0.18 END'))
                layer.setRenderer(QgsSingleSymbolRenderer(sym))
                self._label_index_contours(layer)
            elif self.kind == "depth":
                from qgis.core import (QgsColorRampShader, QgsRasterShader,
                                       QgsSingleBandPseudoColorRenderer)
                stats = layer.dataProvider().bandStatistics(1)
                vmax = max(stats.maximumValue, 0.01)
                ramp = QgsColorRampShader(0.0, vmax)
                ramp.setColorRampType(QgsColorRampShader.Interpolated)
                ramp.setColorRampItemList([
                    QgsColorRampShader.ColorRampItem(0.0, QColor(255, 255, 255, 0), "0"),
                    QgsColorRampShader.ColorRampItem(0.05, QColor(222, 235, 247, 160), ""),
                    QgsColorRampShader.ColorRampItem(vmax, QColor(8, 48, 107, 230),
                                                     "%.2f" % vmax)])
                shader = QgsRasterShader()
                shader.setRasterShaderFunction(ramp)
                layer.setRenderer(QgsSingleBandPseudoColorRenderer(
                    layer.dataProvider(), 1, shader))
            layer.triggerRepaint()
        except Exception as e:                       # styling is cosmetic; never fail
            if feedback is not None:
                feedback.pushInfo("Could not style %s: %s" % (self.kind, e))

    def _label_index_contours(self, layer):
        from qgis.core import (QgsPalLayerSettings, QgsTextBufferSettings, QgsTextFormat,
                               QgsVectorLayerSimpleLabeling)
        pal = QgsPalLayerSettings()
        pal.isExpression = True
        pal.fieldName = ('CASE WHEN "is_index" = 1 THEN format_number("elev_ft", %d) END'
                         % self.decimals)
        try:
            from qgis.core import Qgis
            pal.placement = Qgis.LabelPlacement.Line
        except AttributeError:
            pal.placement = QgsPalLayerSettings.Line
        fmt = QgsTextFormat()
        fmt.setSize(7)
        fmt.setColor(QColor(60, 45, 35))
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(0.7)
        buf.setColor(QColor(255, 255, 255))
        fmt.setBuffer(buf)
        pal.setFormat(fmt)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        layer.setLabelsEnabled(True)


class DepressionsLaylines(QgsProcessingAlgorithm):

    DEM = "DEM"
    FILLED = "FILLED"
    FIELD = "FIELD"
    Z_UNITS = "Z_UNITS"
    DEPTH_FT = "DEPTH_FT"
    MIN_AREA = "MIN_AREA"
    CHANNEL_AC = "CHANNEL_AC"
    MINSLOPE = "MINSLOPE"
    SMOOTH = "SMOOTH"
    COLOR_DEM = "COLOR_DEM"
    HILLSHADE = "HILLSHADE"
    DEPTH = "DEPTH"
    DEPRESSIONS = "DEPRESSIONS"
    LAYLINES = "LAYLINES"
    FLOWACC = "FLOWACC"
    CONTOUR_FT = "CONTOUR_FT"
    INDEX_EVERY = "INDEX_EVERY"
    CONTOURS = "CONTOURS"

    ZUNITS = ["Feet (US survey)", "Meters"]
    SCHEMES = ["Hypsometric (green - yellow - brown)", "Greens", "Tan / brown",
               "Don't color the DEM"]
    HILLSHADE_EXAGGERATION = 3.0     # flat fields need vertical exaggeration to show relief

    def tr(self, s):
        return QCoreApplication.translate("DepressionsLaylines", s)

    def createInstance(self):
        return DepressionsLaylines()

    def name(self):
        return "depressionslaylines"

    def displayName(self):
        return self.tr("Depressions and laylines for a field")

    def group(self):
        return self.tr("Subsurface drainage")

    def groupId(self):
        return "subsurfacedrainage"

    def shortHelpString(self):
        return self.tr(
            "For subsurface (tile) drainage design: finds the depressions in a field, where surface inlets or extra laterals may be needed, and plots its laylines (flow channels), the natural routes for mains and submains.\n\n"
            "Depressions: depth = filled DEM - unfilled DEM. Cells deeper than the depth "
            "threshold (default 0.75 ft) inside the field boundary are grouped into polygons "
            "(cells touching at edges or corners are one depression). Each polygon gets its "
            "area (acres), maximum and mean depth (ft), storage volume above the ground "
            "within the polygon (cu yd) and pond level (the filled-DEM elevation).\n\n"
            "Laylines: water is routed downhill (D8, steepest descent) over the filled DEM, "
            "so it crosses filled depressions toward their spill point. Cells draining at "
            "least the contributing-area threshold become channels, joined into lines between "
            "channel heads, junctions and outlets, and clipped to the field boundary. Each "
            "line has its Strahler order and the contributing area (acres) at its top and "
            "bottom. Flow is routed over the whole DEM, so water from the margin around the "
            "field is counted; build the DEM with 'Extend DEM beyond field' for this.\n\n"
            "Inputs: a bare-earth DEM, e.g. from the LiDAR Field DEM plugin. Filled DEM: LiDAR Field DEM's "
            "'Filled DEM (Wang & Liu)' on the same grid; leave blank to fill the DEM here "
            "with the Wang & Liu method and the minimum slope given. A minimum slope above "
            "0 adds a small ramp across filled areas, which adds slightly to the depth.\n"
            "Contours (optional output, on by default): contour lines of the filled DEM at "
            "the contour interval (default 1 ft), clipped to the field; every 5th is an "
            "index contour, drawn heavier and labeled. Untick the output to skip them.\n"
            "DEM color scheme: when the tool finishes, the DEM layer in the map is colored. "
            "Hypsometric (default) runs green on low ground through yellow and tan to brown "
            "on high ground, leaving blue for the depressions and laylines. The colors are "
            "stretched over the field's elevations (2nd to 98th percentile), so the margin "
            "and odd edge cells don't wash out the field. Hillshade (on by default) adds a "
            "shaded-relief layer just above the DEM, multiplied at 45% with 3x vertical "
            "exaggeration, to show micro-relief such as swales and old furrows.\n"
            "Field boundary: optional; blank uses the whole DEM.\n"
            "Elevation units: the DEM's vertical units (LiDAR Field DEM's default is feet). "
            "Thresholds and results are always in feet and acres.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, self.tr("DEM (unfilled bare-earth)")))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.FILLED, self.tr("Filled DEM (Wang & Liu) [blank = fill here]"),
            optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.FIELD, self.tr("Field boundary [blank = whole DEM]"),
            [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.Z_UNITS, self.tr("DEM elevation units"), options=self.ZUNITS,
            defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.DEPTH_FT, self.tr("Depression depth threshold (ft)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.75, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_AREA, self.tr("Minimum depression area (acres, 0 = keep all)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.CHANNEL_AC, self.tr("Layline contributing area threshold (acres)"),
            QgsProcessingParameterNumber.Double, defaultValue=2.0, minValue=0.001))
        self.addParameter(QgsProcessingParameterNumber(
            self.MINSLOPE, self.tr("Minimum slope across filled sinks, if filled here "
                                   "(degrees, 0 = flat)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.06, minValue=0.0,
            maxValue=5.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SMOOTH, self.tr("Smooth laylines (removes the cell-to-cell stair steps)"),
            defaultValue=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.COLOR_DEM, self.tr("DEM color scheme"), options=self.SCHEMES,
            defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.HILLSHADE, self.tr("Add a hillshade over the DEM colors"),
            defaultValue=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.CONTOUR_FT, self.tr("Contour interval (ft)"),
            QgsProcessingParameterNumber.Double, defaultValue=1.0, minValue=0.01))
        self.addParameter(QgsProcessingParameterNumber(
            self.INDEX_EVERY, self.tr("Index contour every (contours, 0 = none)"),
            QgsProcessingParameterNumber.Integer, defaultValue=5, minValue=0))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.DEPTH, self.tr("Depression depth (filled - unfilled)"),
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.DEPRESSIONS, self.tr("Depressions"), QgsProcessing.TypeVectorPolygon))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.LAYLINES, self.tr("Laylines"), QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.CONTOURS, self.tr("Contours"), QgsProcessing.TypeVectorLine,
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.FLOWACC, self.tr("Contributing area (acres)"),
            optional=True, createByDefault=False))

    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        import numpy as np
        from osgeo import gdal, ogr
        from .sinkfill import fill_sinks_wang_liu
        from . import terrain

        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        if dem_layer is None:
            raise QgsProcessingException("Invalid DEM.")
        self._dem_style = None
        scheme = self.parameterAsEnum(parameters, self.COLOR_DEM, context)
        hillshade = self.parameterAsBoolean(parameters, self.HILLSHADE, context)
        filled_layer = self.parameterAsRasterLayer(parameters, self.FILLED, context)
        field_src = self.parameterAsSource(parameters, self.FIELD, context)
        z_unit_ft = 1.0 if self.parameterAsEnum(parameters, self.Z_UNITS, context) == 0 \
            else 1.0 / FT_US                                  # feet per elevation unit
        depth_thr_ft = self.parameterAsDouble(parameters, self.DEPTH_FT, context)
        min_area_ac = self.parameterAsDouble(parameters, self.MIN_AREA, context)
        channel_ac = self.parameterAsDouble(parameters, self.CHANNEL_AC, context)
        min_slope = self.parameterAsDouble(parameters, self.MINSLOPE, context)
        smooth = self.parameterAsBoolean(parameters, self.SMOOTH, context)

        crs = dem_layer.crs()
        if not crs.isValid() or crs.isGeographic():
            raise QgsProcessingException("The DEM must be in a projected CRS (not lat/long).")
        xy_m = QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(),
                                                 QgsUnitTypes.DistanceMeters)
        if not xy_m or xy_m <= 0:
            raise QgsProcessingException("Unknown horizontal units for the DEM's CRS.")
        xy_ft = xy_m / FT_US                                    # feet per CRS unit

        # ---- read the DEM ----
        z, nodata, gt, proj = self._read(dem_layer.source(), gdal)
        if abs(gt[2]) > 1e-12 or abs(gt[4]) > 1e-12:
            raise QgsProcessingException("Rotated rasters are not supported.")
        cw, ch = abs(gt[1]), abs(gt[5])
        if abs(cw - ch) > 1e-6 * max(cw, ch):
            feedback.pushWarning("Cells are not square (%g x %g); using their mean size."
                                 % (cw, ch))
        cell = 0.5 * (cw + ch)                                  # CRS units
        cell_ft = cell * xy_ft
        cell_area_sqft = cw * ch * xy_ft * xy_ft
        cell_ac = cell_area_sqft / SQ_FT_PER_ACRE
        z_per_xy = 1.0 / (z_unit_ft / xy_ft)                    # elevation units per CRS unit
        rows, cols = z.shape
        feedback.pushInfo("DEM: %d x %d cells of %.2f ft (%.4f ac); %d with data"
                          % (cols, rows, cell_ft, cell_ac, int((~nodata).sum())))

        # ---- filled DEM ----
        if filled_layer is not None:
            zf, nd_f, gt_f, _ = self._read(filled_layer.source(), gdal)
            if zf.shape != z.shape or any(abs(a - b) > 1e-6 * max(1.0, abs(a))
                                          for a, b in zip(gt, gt_f)):
                raise QgsProcessingException(
                    "The filled DEM is not on the same grid as the DEM. Use the DEM and the "
                    "filled DEM from the same DEM build, or leave the filled DEM "
                    "blank to fill here.")
            nodata = nodata | nd_f
            feedback.pushInfo("Filled DEM: %s" % filled_layer.source())
        else:
            feedback.setProgressText("Filling sinks (Wang & Liu)")
            feedback.pushInfo("Filling sinks here (Wang & Liu, minimum slope %g deg)"
                              % min_slope)
            zf = fill_sinks_wang_liu(
                z, nodata, cell, min_slope_deg=min_slope, z_per_xy=z_per_xy,
                progress=lambda f: feedback.setProgress(int(25 * f)),
                is_canceled=feedback.isCanceled)
        if feedback.isCanceled():
            return {}

        # ---- field mask (cell centres inside the field) ----
        field_geom = None
        in_field = ~nodata
        if field_src is not None:
            geoms = [f.geometry() for f in field_src.getFeatures() if f.hasGeometry()]
            if geoms:
                field_geom = QgsGeometry.unaryUnion(geoms)
                if field_src.sourceCrs() != crs:
                    field_geom.transform(QgsCoordinateTransform(
                        field_src.sourceCrs(), crs, context.transformContext()))
                in_field = self._rasterize(field_geom, gt, proj, rows, cols, gdal, ogr) \
                    & ~nodata
                feedback.pushInfo("Field: %.1f acres of DEM inside the boundary"
                                  % (in_field.sum() * cell_ac))
                if not in_field.any():
                    raise QgsProcessingException("The field boundary does not overlap the DEM.")
            else:
                feedback.pushWarning("The field layer has no polygons; using the whole DEM.")

        # ---- DEM colors: stretch to the field, 2nd - 98th percentile ----
        if scheme < 3:
            vals = z[in_field]
            lo, hi = (np.percentile(vals, [2.0, 98.0]) if vals.size else (0.0, 1.0))
            self._dem_style = {
                "layer_id": dem_layer.id(), "scheme": scheme, "hillshade": hillshade,
                "vmin": float(lo), "vmax": float(hi),
                "z_factor": self.HILLSHADE_EXAGGERATION * z_unit_ft / xy_ft}

        # ---- depression depth ----
        depth = terrain.depression_depth(z, zf, nodata)        # elevation units
        depth_ft = depth * z_unit_ft
        results = {}
        depth_path = self.parameterAsOutputLayer(parameters, self.DEPTH, context)
        if depth_path:
            out = np.where(nodata, -9999.0, depth_ft)
            if field_geom is not None:
                out = np.where(in_field, out, -9999.0)
            depth_path = self._write(depth_path, out, gt, proj, gdal)
            results[self.DEPTH] = depth_path
            self._style(context, depth_path, "depth", "Depression depth (ft)")

        # ---- depressions ----
        feedback.setProgressText("Finding depressions")
        feedback.setProgress(30)
        deep = (depth_ft > depth_thr_ft) & in_field
        labels, n = terrain.label_regions(deep)
        stats = terrain.region_stats(labels, n, depth_ft, zf, cell_area_sqft)
        keep = [s for s in stats if s["cells"] * cell_ac >= min_area_ac]
        dropped = len(stats) - len(keep)
        polys = self._polygonize(labels, gt, proj, {s["id"] for s in keep}, gdal, ogr)

        dep_fields = QgsFields()
        for name, typ in (("dep_id", QVariant.Int), ("area_ac", QVariant.Double),
                          ("max_dep_ft", QVariant.Double), ("mean_dep_ft", QVariant.Double),
                          ("vol_cu_yd", QVariant.Double), ("pond_elev", QVariant.Double),
                          ("cells", QVariant.Int)):
            dep_fields.append(QgsField(name, typ))
        (dep_sink, dep_id) = self.parameterAsSink(
            parameters, self.DEPRESSIONS, context, dep_fields,
            QgsWkbTypes.MultiPolygon, crs)
        if dep_sink is None:
            raise QgsProcessingException("Could not create the depressions output.")
        keep.sort(key=lambda s: -s["volume"])
        tot_area = tot_vol = 0.0
        for k, s in enumerate(keep, 1):
            g = polys.get(s["id"])
            if g is None or g.isEmpty():
                continue
            g.convertToMultiType()
            f = QgsFeature(dep_fields)
            f.setGeometry(g)
            area = s["cells"] * cell_ac
            vol = s["volume"] / CU_FT_PER_CU_YD
            f.setAttributes([k, round(area, 3), round(s["max_depth"], 2),
                             round(s["mean_depth"], 2), round(vol, 1),
                             round(s["pond_elev"], 2), s["cells"]])
            dep_sink.addFeature(f, QgsFeatureSink.FastInsert)
            tot_area += area
            tot_vol += vol
        results[self.DEPRESSIONS] = dep_id
        self._style(context, dep_id, "depressions",
                    "Depressions > %g ft" % depth_thr_ft)
        feedback.pushInfo("Depressions deeper than %.2f ft: %d (%.2f acres, %.0f cu yd)%s"
                          % (depth_thr_ft, len(keep), tot_area, tot_vol,
                             (", %d smaller than %g ac left out" % (dropped, min_area_ac))
                             if dropped else ""))
        if feedback.isCanceled():
            return results

        # ---- laylines ----
        feedback.setProgressText("Routing flow")
        feedback.setProgress(40)
        eps_deg = math.degrees(math.atan(ROUTING_EPS_Z / (cell * z_per_xy)))
        zr = fill_sinks_wang_liu(
            zf.astype(np.float64), nodata, cell, min_slope_deg=eps_deg, z_per_xy=z_per_xy,
            progress=lambda f: feedback.setProgress(40 + int(20 * f)),
            is_canceled=feedback.isCanceled)
        rec = terrain.d8_receivers(zr, nodata)
        thr_cells = max(1.0, channel_ac / cell_ac)
        feedback.setProgress(65)
        acc, strahler = terrain.accumulate(zr, nodata, rec, thr_cells,
                                           is_canceled=feedback.isCanceled)
        feedback.setProgress(80)
        segs = terrain.channel_segments(rec, acc, strahler, thr_cells)

        fa_path = self.parameterAsOutputLayer(parameters, self.FLOWACC, context)
        if fa_path:
            fa = np.where(nodata, -9999.0, acc.reshape(rows, cols) * cell_ac)
            fa_path = self._write(fa_path, fa, gt, proj, gdal)
            results[self.FLOWACC] = fa_path

        ll_fields = QgsFields()
        for name, typ in (("line_id", QVariant.Int), ("strahler", QVariant.Int),
                          ("ac_top", QVariant.Double), ("ac_bottom", QVariant.Double),
                          ("length_ft", QVariant.Double)):
            ll_fields.append(QgsField(name, typ))
        (ll_sink, ll_id) = self.parameterAsSink(
            parameters, self.LAYLINES, context, ll_fields,
            QgsWkbTypes.MultiLineString, crs)
        if ll_sink is None:
            raise QgsProcessingException("Could not create the laylines output.")

        x0, y0, dx, dy = gt[0], gt[3], gt[1], gt[5]
        clip = None
        if field_geom is not None:
            clip = QgsGeometry(field_geom)
            clip.convertToMultiType()
        n_lines = 0
        tot_len = 0.0
        max_order = 0
        for s in segs:
            if feedback.isCanceled():
                break
            pts = [QgsPointXY(x0 + (c % cols + 0.5) * dx, y0 + (c // cols + 0.5) * dy)
                   for c in s["cells"]]
            g = QgsGeometry.fromPolylineXY(pts)
            if smooth and len(pts) > 2:
                g = g.smooth(2, 0.25)
            if clip is not None:
                if not g.intersects(clip):
                    continue
                g = self._lines_only(g.intersection(clip))
                if g is None:
                    continue
            g.convertToMultiType()
            length_ft = g.length() * xy_ft
            n_lines += 1
            f = QgsFeature(ll_fields)
            f.setGeometry(g)
            f.setAttributes([n_lines, s["strahler"], round(s["acc_top"] * cell_ac, 2),
                             round(s["acc_bottom"] * cell_ac, 2), round(length_ft, 1)])
            ll_sink.addFeature(f, QgsFeatureSink.FastInsert)
            tot_len += length_ft
            max_order = max(max_order, s["strahler"])
        results[self.LAYLINES] = ll_id
        self._style(context, ll_id, "laylines", "Laylines >= %g ac" % channel_ac)
        feedback.pushInfo("Laylines (contributing area >= %g ac, %d cells): %d lines, "
                          "%.0f ft in total, highest Strahler order %d%s"
                          % (channel_ac, int(math.ceil(thr_cells)), n_lines, tot_len,
                             max_order, " inside the field" if clip is not None else ""))
        # ---- contours (optional) ----
        if not feedback.isCanceled():
            feedback.setProgressText("Contours")
            feedback.setProgress(95)
            self._contours(parameters, context, feedback, results, zf, nodata, gt, proj,
                           crs, z_unit_ft, clip, gdal, ogr)
        feedback.setProgress(100)
        return results

    # ------------------------------------------------------------------
    def _contours(self, parameters, context, feedback, results, z, nodata, gt, proj, crs,
                  z_unit_ft, clip, gdal, ogr):
        """Contour lines of the filled DEM (``z`` here is the filled DEM), clipped to the field."""
        import numpy as np
        interval_ft = self.parameterAsDouble(parameters, self.CONTOUR_FT, context)
        every = self.parameterAsInt(parameters, self.INDEX_EVERY, context)
        fields = QgsFields()
        for name, typ in (("elev_ft", QVariant.Double), ("elev", QVariant.Double),
                          ("is_index", QVariant.Int)):
            fields.append(QgsField(name, typ))
        (sink, dest) = self.parameterAsSink(parameters, self.CONTOURS, context, fields,
                                            QgsWkbTypes.MultiLineString, crs)
        if sink is None or interval_ft <= 0:
            return                                         # contours not wanted
        zr = z[~nodata]
        if zr.size == 0:
            return
        n_levels = (zr.max() - zr.min()) * z_unit_ft / interval_ft
        if n_levels > 5000:
            raise QgsProcessingException(
                "A %g ft contour interval gives %d contours over this DEM; use a larger "
                "interval." % (interval_ft, n_levels))
        rows, cols = z.shape
        ds = gdal.GetDriverByName("MEM").Create("", cols, rows, 1, gdal.GDT_Float64)
        ds.SetGeoTransform(gt)
        ds.SetProjection(proj)
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(-9999.0)
        band.WriteArray(np.where(nodata, -9999.0, z))
        vds = self._ogr_mem(ogr, "contours")
        lyr = vds.CreateLayer("contours", geom_type=ogr.wkbLineString)
        lyr.CreateField(ogr.FieldDefn("id", ogr.OFTInteger))
        lyr.CreateField(ogr.FieldDefn("elev", ogr.OFTReal))
        gdal.ContourGenerate(band, interval_ft / z_unit_ft, 0.0, [], 1, -9999.0, lyr, 0, 1)
        n = 0
        for of in lyr:
            elev = of.GetField(1)
            g = QgsGeometry.fromWkt(of.GetGeometryRef().ExportToWkt())
            if clip is not None:
                if not g.intersects(clip):
                    continue
                g = self._lines_only(g.intersection(clip))
                if g is None:
                    continue
            g.convertToMultiType()
            elev_ft = elev * z_unit_ft
            k = int(round(elev_ft / interval_ft))
            is_index = 1 if every > 0 and k % every == 0 else 0
            f = QgsFeature(fields)
            f.setGeometry(g)
            f.setAttributes([round(elev_ft, 3), round(elev, 3), is_index])
            sink.addFeature(f, QgsFeatureSink.FastInsert)
            n += 1
        ds = vds = None
        results[self.CONTOURS] = dest
        decimals = 0 if abs(interval_ft * every - round(interval_ft * every)) < 1e-9 else 2
        self._style(context, dest, "contours", "Contours (%g ft)" % interval_ft, decimals)
        feedback.pushInfo("Contours: %d lines at %g ft%s" % (
            n, interval_ft, (", index every %d" % every) if every else ""))

    def postProcessAlgorithm(self, context, feedback):  # noqa: N802
        """Runs on the main thread after the tool: colors the input DEM layer."""
        st = getattr(self, "_dem_style", None)
        if st:
            from qgis.core import QgsProject
            layer = QgsProject.instance().mapLayer(st["layer_id"])
            if layer is None:
                feedback.pushInfo("The DEM is not a layer in the map, so it was not colored.")
            else:
                try:
                    self._color_dem(layer, st)
                    feedback.pushInfo("DEM colored: %s, %.2f to %.2f%s" % (
                        self.SCHEMES[st["scheme"]], st["vmin"], st["vmax"],
                        ", with hillshade" if st["hillshade"] else ""))
                except Exception as e:                   # cosmetic; never fail the run
                    feedback.pushInfo("Could not color the DEM: %s" % e)
        return {}

    @staticmethod
    def _ramp(scheme):
        """Color ramp for a DEM color scheme (index into SCHEMES)."""
        from qgis.core import QgsGradientColorRamp, QgsGradientStop, QgsStyle

        def grad(stops):
            c = [QColor(h) for _, h in stops]
            mid = [QgsGradientStop(t, QColor(h)) for t, h in stops[1:-1]]
            return QgsGradientColorRamp(c[0], c[-1], False, mid)

        if scheme == 0:          # hypsometric: green (low, wet) - yellow - tan - brown (high)
            return grad([(0.0, "#2f7a3e"), (0.25, "#8fbf5a"), (0.5, "#ece39b"),
                         (0.75, "#d6a35c"), (1.0, "#8a5629")])
        if scheme == 1:          # Greens (QGIS built-in)
            ramp = QgsStyle.defaultStyle().colorRamp("Greens")
            return ramp if ramp is not None else grad([(0.0, "#f7fcf5"), (1.0, "#00441b")])
        return grad([(0.0, "#f6eedf"), (0.35, "#dcc196"), (0.7, "#a8743f"),   # tan / brown
                     (1.0, "#5e3b1c")])

    def _color_dem(self, layer, st):
        """Pseudocolor the DEM, stretched to the field, with an optional hillshade above it."""
        from qgis.core import (QgsColorRampShader, QgsProject, QgsRasterShader,
                               QgsSingleBandPseudoColorRenderer)
        vmin, vmax = st["vmin"], st["vmax"]
        if not vmax > vmin:
            vmax = vmin + 1.0
        ramp = self._ramp(st["scheme"])
        shader = QgsColorRampShader(vmin, vmax)
        shader.setSourceColorRamp(ramp.clone())
        shader.setColorRampType(QgsColorRampShader.Interpolated)
        n = 9
        shader.setColorRampItemList([
            QgsColorRampShader.ColorRampItem(
                vmin + (vmax - vmin) * i / (n - 1), ramp.color(i / (n - 1)),
                "%.1f" % (vmin + (vmax - vmin) * i / (n - 1)))
            for i in range(n)])
        rs = QgsRasterShader()
        rs.setRasterShaderFunction(shader)
        renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, rs)
        renderer.setClassificationMin(vmin)
        renderer.setClassificationMax(vmax)
        layer.setRenderer(renderer)
        layer.triggerRepaint()
        self._refresh_legend(layer)

        # hillshade: a second layer on the same file, just above the DEM, multiplied
        project = QgsProject.instance()
        key = "depressions_laylines/hillshade_of"
        existing = [l for l in project.mapLayers().values()
                    if l.customProperty(key) == layer.id()]
        if not st["hillshade"]:
            return
        from qgis.PyQt.QtGui import QPainter
        from qgis.core import QgsHillshadeRenderer, QgsRasterLayer
        if existing:
            hs = existing[0]
        else:
            hs = QgsRasterLayer(layer.source(), "Hillshade - %s" % layer.name(),
                               layer.providerType())
            if not hs.isValid():
                raise RuntimeError("could not open the DEM again for the hillshade")
            hs.setCustomProperty(key, layer.id())
            project.addMapLayer(hs, False)
            root = project.layerTreeRoot()
            node = root.findLayer(layer.id())
            parent = node.parent() if node is not None else root
            idx = parent.children().index(node) if node is not None else 0
            parent.insertLayer(idx, hs)                 # directly above the DEM
        hr = QgsHillshadeRenderer(hs.dataProvider(), 1, 315.0, 45.0)
        hr.setZFactor(st["z_factor"])
        try:
            hr.setMultiDirectional(True)
        except Exception:
            pass
        hr.setOpacity(0.45)
        hs.setRenderer(hr)
        hs.setBlendMode(QPainter.CompositionMode_Multiply)
        hs.triggerRepaint()

    @staticmethod
    def _refresh_legend(layer):
        try:
            from qgis.utils import iface
            if iface is not None:
                iface.layerTreeView().refreshLayerSymbology(layer.id())
        except Exception:
            pass

    @staticmethod
    def _style(context, layer_id, kind, name, decimals=0):
        if not context.willLoadLayerOnCompletion(layer_id):
            return
        styler = _Styler(kind, name, decimals)
        _POSTPROCESSORS.append(styler)
        del _POSTPROCESSORS[:-20]
        context.layerToLoadOnCompletionDetails(layer_id).setPostProcessor(styler)

    @staticmethod
    def _lines_only(g):
        """The line parts of a clip result (drops points where a line touches the edge)."""
        if g is None or g.isEmpty():
            return None
        if g.type() == QgsWkbTypes.LineGeometry:
            return g
        lines = [p for p in g.asGeometryCollection()
                 if p.type() == QgsWkbTypes.LineGeometry and not p.isEmpty()]
        return QgsGeometry.collectGeometry(lines) if lines else None

    @staticmethod
    def _read(path, gdal):
        import numpy as np
        ds = gdal.Open(path)
        if ds is None:
            raise QgsProcessingException("Could not open raster %s" % path)
        band = ds.GetRasterBand(1)
        nd = band.GetNoDataValue()
        z = band.ReadAsArray().astype(np.float64)
        mask = ~np.isfinite(z)
        if nd is not None:
            mask |= (z == nd)
        z[mask] = -9999.0
        gt, proj = ds.GetGeoTransform(), ds.GetProjection()
        ds = None
        return z, mask, gt, proj

    @staticmethod
    def _write(path, arr, gt, proj, gdal):
        import numpy as np
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".tif", ".tiff"):
            path = os.path.splitext(path)[0] + ".tif"
        drv = gdal.GetDriverByName("GTiff")
        ds = drv.Create(path, arr.shape[1], arr.shape[0], 1, gdal.GDT_Float32,
                        ["COMPRESS=DEFLATE", "TILED=YES"])
        ds.SetGeoTransform(gt)
        ds.SetProjection(proj)
        b = ds.GetRasterBand(1)
        b.SetNoDataValue(-9999.0)
        b.WriteArray(arr.astype(np.float32))
        b.FlushCache()
        ds = None
        return path

    @staticmethod
    def _ogr_mem(ogr, name):
        """In-memory OGR data source ('Memory' before GDAL 3.11, 'MEM' after)."""
        for drv_name in ("Memory", "MEM"):
            drv = ogr.GetDriverByName(drv_name)
            if drv is not None:
                ds = drv.CreateDataSource(name)
                if ds is not None:
                    return ds
        raise QgsProcessingException("GDAL has no in-memory vector driver.")

    @staticmethod
    def _rasterize(geom, gt, proj, rows, cols, gdal, ogr):
        """Boolean grid, True where the cell centre is inside ``geom``."""
        drv = gdal.GetDriverByName("MEM")
        ds = drv.Create("", cols, rows, 1, gdal.GDT_Byte)
        ds.SetGeoTransform(gt)
        ds.SetProjection(proj)
        vds = DepressionsLaylines._ogr_mem(ogr, "field")
        lyr = vds.CreateLayer("field", geom_type=ogr.wkbMultiPolygon)
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetGeometry(ogr.CreateGeometryFromWkt(geom.asWkt()))
        lyr.CreateFeature(f)
        gdal.RasterizeLayer(ds, [1], lyr, burn_values=[1])
        arr = ds.GetRasterBand(1).ReadAsArray().astype(bool)
        ds = vds = None
        return arr

    @staticmethod
    def _polygonize(labels, gt, proj, wanted, gdal, ogr):
        """{label: QgsGeometry} for the wanted labels (cell-edge outlines)."""
        import numpy as np
        out = {}
        if not wanted:
            return out
        lab = np.where(np.isin(labels, list(wanted)), labels, 0).astype(np.int32)
        rows, cols = lab.shape
        drv = gdal.GetDriverByName("MEM")
        ds = drv.Create("", cols, rows, 1, gdal.GDT_Int32)
        ds.SetGeoTransform(gt)
        ds.SetProjection(proj)
        band = ds.GetRasterBand(1)
        band.WriteArray(lab)
        mds = drv.Create("", cols, rows, 1, gdal.GDT_Byte)
        mds.SetGeoTransform(gt)
        mds.GetRasterBand(1).WriteArray((lab > 0).astype(np.uint8))
        vds = DepressionsLaylines._ogr_mem(ogr, "dep")
        lyr = vds.CreateLayer("dep", geom_type=ogr.wkbPolygon)
        lyr.CreateField(ogr.FieldDefn("lab", ogr.OFTInteger))
        gdal.Polygonize(band, mds.GetRasterBand(1), lyr, 0, ["8CONNECTED=8"])
        parts = {}
        for f in lyr:
            k = f.GetField(0)
            parts.setdefault(k, []).append(QgsGeometry.fromWkt(f.GetGeometryRef().ExportToWkt()))
        for k, gs in parts.items():
            g = gs[0] if len(gs) == 1 else QgsGeometry.unaryUnion(gs)
            if not g.isGeosValid():
                g = g.makeValid()
            out[k] = g
        ds = mds = vds = None
        return out
