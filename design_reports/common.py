"""Shared Processing machinery for the drainage and subirrigation design reports:
remembered settings, common parameters, data gathering and the map pages."""
import os

import numpy as np
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsMapLayer,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingFeatureSourceDefinition,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProject,
    QgsRectangle,
    QgsUnitTypes,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon

from . import maps
from .core.compute import (component_rows, crop_text, fmt_latlon, lateral_spacing,
                           polygon_mask, scale_text, series_name, smooth, soil_rows)
from .core.design import (coarsen, depressions_and_runon, depth_range, pipe_schedule,
                          pipe_summary, sheet_centers, sheet_scale_for_pages)
from .core import terrain

PLUGIN_DIR = os.path.dirname(__file__)
M2_AC = 4046.8564224
FT_PER_M = 1.0 / 0.3048
Z_FACTORS = [1.0, 0.3048, 1200.0 / 3937.0]     # DEM elevation units -> metres
BASEMAPS = ["None", "OpenStreetMap", "Esri World Imagery"]
MAX_TERRAIN_CELLS = 3.0e6
SHEET_H_IN = 7.4

# Settings of the last run of each tool, kept while the project stays open; cleared by
# the plugin when a new project is started or another one is loaded.
_LAST_RUN = {}
LAYER_INPUTS = ("FIELD", "DEM", "NETWORK", "EXISTING", "SOILS", "CROPS", "WATERSHED",
                "OUTLET", "STREAMS", "DEPRESSIONS", "LAYLINES", "ZONES", "STRUCTURES")


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


def advanced(param):
    try:
        param.setFlags(param.flags() | Qgis.ProcessingParameterFlag.Advanced)
    except AttributeError:
        param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
    return param


def num(v):
    try:
        if v is None or (hasattr(v, "isNull") and v.isNull()):
            return None
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def sval(v):
    if v is None or (hasattr(v, "isNull") and v.isNull()):
        return ""
    s = str(v).strip()
    return "" if s == "NULL" else s


def rings(geom):
    out = []
    polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
    for poly in polys:
        for ring in poly:
            out.append([(p.x(), p.y()) for p in ring])
    return out


class ReportAlgorithm(QgsProcessingAlgorithm):
    """Base class: subclasses set KEY, TEMPLATE_FILE, MAX_SCALE_DEFAULT and implement
    extra_parameters() and build(data, ctx)."""
    KEY = ""
    TEMPLATE_FILE = ""
    MAX_SCALE_DEFAULT = 0
    CONTOUR_DEFAULT = 0.5
    NOT_SAVED = ("OUTPUT",)

    def tr(self, s):
        return QCoreApplication.translate("DesignReports", s)

    def group(self):
        return ""

    def groupId(self):
        return ""

    def icon(self):
        return QIcon(os.path.join(PLUGIN_DIR, "icon.png"))

    # ------------------------------------------------------------ remembered settings
    def addParameter(self, definition, createOutput=True):
        nm = definition.name()
        saved = _LAST_RUN.setdefault(self.KEY, {})
        if nm not in self.NOT_SAVED:
            if not hasattr(self, "_saved_names"):
                self._saved_names = []
            self._saved_names.append(nm)
            if nm in saved:
                value = saved[nm]
                try:
                    ok = (definition.checkValueIsAcceptable(value)
                          if hasattr(definition, "checkValueIsAcceptable") else True)
                    if ok and (nm not in LAYER_INPUTS or _layer_still_there(value)):
                        definition.setDefaultValue(value)
                except (ValueError, TypeError):
                    pass
        return super().addParameter(definition, createOutput)

    def _save_settings(self, parameters):
        saved = _LAST_RUN.setdefault(self.KEY, {})
        for nm in getattr(self, "_saved_names", []):
            if nm not in parameters:
                continue
            value = parameters[nm]
            if isinstance(value, QgsMapLayer):
                value = value.id()
            elif isinstance(value, QgsProcessingFeatureSourceDefinition):
                value = QgsProcessingFeatureSourceDefinition(value)
            saved[nm] = value

    # ------------------------------------------------------------ parameters
    def initAlgorithm(self, config=None):
        dbl = QgsProcessingParameterNumber.Double
        src = QgsProcessingParameterFeatureSource
        poly, line, point = (QgsProcessing.TypeVectorPolygon, QgsProcessing.TypeVectorLine,
                             QgsProcessing.TypeVectorPoint)
        self.addParameter(src("FIELD", self.tr("Field boundary (polygons; selected features "
                                               "are honoured)"), [poly]))
        self.addParameter(QgsProcessingParameterRasterLayer(
            "DEM", self.tr("DEM (LiDAR, projected CRS; extended beyond the field to find "
                           "run-on)")))
        self.addParameter(QgsProcessingParameterEnum(
            "Z_UNITS", self.tr("DEM elevation units"),
            options=[self.tr("Metres"), self.tr("International feet"),
                     self.tr("US survey feet")], defaultValue=2))
        self.addParameter(src("NETWORK", self.tr("Drainage network (Illini Subsurface "
                                                 "Drainage: kind, size, tile Z, ground M)"),
                              [line], optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            "DESIGN_UNITS", self.tr("Design units of the network (elevations)"),
            options=[self.tr("Metres"), self.tr("Feet")], defaultValue=1))
        self.addParameter(src("EXISTING", self.tr("Existing tile (optional)"), [line],
                              optional=True))
        self.extra_layers()
        self.addParameter(src("SOILS", self.tr("Soils (SSURGO drainage soil map output: "
                                               "musym, muname, spacing_ft)"), [poly],
                              optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            "FETCH_SOILS", self.tr("No soils layer: download soils from Soil Data Access"),
            defaultValue=True))
        self.addParameter(src("CROPS", self.tr("Crop History fields (optional)"), [poly],
                              optional=True))
        self.addParameter(QgsProcessingParameterString(
            "CROPS_TEXT", self.tr("Crops grown and planned rotation (used when there is no "
                                  "Crop History layer, or added to it)"), defaultValue="",
            optional=True, multiLine=True))
        self.addParameter(src("WATERSHED", self.tr("Watershed (e.g. StreamStats "
                                                   "Delineator watershed)"), [poly],
                              optional=True))
        self.addParameter(src("OUTLET", self.tr("Watershed outlet point (e.g. StreamStats "
                                                "outlet)"), [point], optional=True))
        self.addParameter(src("STREAMS", self.tr("Streams (optional, for the watershed "
                                                 "map)"), [line], optional=True))
        self.addParameter(src("DEPRESSIONS", self.tr("Depressions (Depressions and Laylines "
                                                     "output; blank = found from the DEM)"),
                              [poly], optional=True))
        self.addParameter(src("LAYLINES", self.tr("Laylines (Depressions and Laylines output; "
                                                  "blank = found from the DEM)"), [line],
                              optional=True))
        self.addParameter(QgsProcessingParameterString(
            "COUNTY_TWP", self.tr("County / Township"), defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterString(
            "NARRATIVE", self.tr("Background: history and management of the field, related "
                                 "practices, objectives (blank line between paragraphs)"),
            defaultValue="", optional=True, multiLine=True))
        self.addParameter(QgsProcessingParameterString(
            "EXISTING_NOTE", self.tr("Existing tile (note for the background)"),
            defaultValue="", optional=True))
        self.extra_text()
        for nm, label in (("UPPER", "Upper drain depth limit (ft; blank = shallowest lateral)"),
                          ("LOWER", "Lower drain depth limit (ft; blank = deepest lateral)"),
                          ("ABS_UPPER", "Absolute upper drain depth (ft; blank = shallowest "
                                        "tile)"),
                          ("ABS_LOWER", "Absolute lower drain depth (ft; blank = deepest "
                                        "tile)"),
                          ("DC", "Design drainage coefficient (in/day; blank = from the "
                                 "network's D_COEFF)")):
            self.addParameter(QgsProcessingParameterString(nm, self.tr(label), defaultValue="",
                                                           optional=True))
        self.addParameter(QgsProcessingParameterString(
            "MATERIAL", self.tr("Pipe material(s)"),
            defaultValue="Corrugated polyethylene (HDPE)"))
        self.addParameter(QgsProcessingParameterString(
            "OTHER", self.tr("Other considerations"), defaultValue="", optional=True,
            multiLine=True))
        self.addParameter(QgsProcessingParameterNumber(
            "MAX_PAGES", self.tr("Maximum pages for the system (tile) and topographic maps "
                                 "(0 = no limit; fewer pages = smaller scale)"),
            QgsProcessingParameterNumber.Integer, defaultValue=0, minValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            "PIPE_TABLE", self.tr("Pipe table after the components"),
            options=[self.tr("Summary: total length by pipe size and material"),
                     self.tr("Full schedule: size, length, slope and depth of every pipe"),
                     self.tr("Both"), self.tr("None")], defaultValue=0))
        self.addParameter(advanced(QgsProcessingParameterNumber(
            "CONTOUR", self.tr("Contour interval (ft)"), dbl,
            defaultValue=self.CONTOUR_DEFAULT, minValue=0.1)))
        self.addParameter(advanced(QgsProcessingParameterNumber(
            "CONTOUR_SMOOTH", self.tr("Smooth the DEM before contouring (radius, ft; 0 = "
                                      "none)"), dbl, defaultValue=6.0, minValue=0.0)))
        self.addParameter(advanced(QgsProcessingParameterNumber(
            "MAX_SCALE", self.tr("Largest scale for the system and topographic maps "
                                 "(1:N; larger fields are split into sheets; 0 = fit one "
                                 "page)"), QgsProcessingParameterNumber.Integer,
            defaultValue=self.MAX_SCALE_DEFAULT, minValue=0)))
        self.addParameter(advanced(QgsProcessingParameterNumber(
            "DEP_DEPTH", self.tr("Depressions: depth below the filled surface (ft)"), dbl,
            defaultValue=0.75, minValue=0.0)))
        self.addParameter(advanced(QgsProcessingParameterNumber(
            "DEP_MIN", self.tr("Depressions: smallest area (acres)"), dbl, defaultValue=0.05,
            minValue=0.0)))
        self.addParameter(advanced(QgsProcessingParameterNumber(
            "RUNON_MIN", self.tr("Run-on areas: smallest area (acres)"), dbl,
            defaultValue=0.5, minValue=0.0)))
        self.addParameter(advanced(QgsProcessingParameterNumber(
            "RUNON_REACH", self.tr("Look for run-on up to this far outside the field (ft)"),
            dbl, defaultValue=1000.0, minValue=0.0)))
        self.addParameter(advanced(QgsProcessingParameterNumber(
            "LAYLINE_AC", self.tr("Laylines: contributing area (acres)"), dbl,
            defaultValue=2.0, minValue=0.1)))
        self.addParameter(advanced(QgsProcessingParameterEnum(
            "BASEMAP", self.tr("Location map background (needs internet)"),
            options=[self.tr(b) for b in BASEMAPS], defaultValue=1)))
        self.addParameter(advanced(QgsProcessingParameterBoolean(
            "IMAGERY", self.tr("Aerial imagery under the background (field) map (needs "
                               "internet)"), defaultValue=True)))
        self.addParameter(advanced(QgsProcessingParameterFile(
            "TEMPLATE", self.tr("Report template (.dotx/.docx; blank = the template in the "
                                "plugin)"), optional=True,
            fileFilter="Word templates (*.dotx *.docx)")))
        self.addParameter(QgsProcessingParameterFileDestination(
            "OUTPUT", self.tr("Report (Word)"), self.tr("Word document (*.docx)")))

    def extra_layers(self):
        pass

    def extra_text(self):
        pass

    # ------------------------------------------------------------ DEM
    def read_dem(self, layer, rect, zf_ft):
        from osgeo import gdal
        ds = gdal.Open(layer.source())
        if ds is None:
            raise QgsProcessingException(self.tr("GDAL could not open the DEM: ") +
                                         layer.source())
        gt = ds.GetGeoTransform()
        if gt[2] != 0 or gt[4] != 0:
            raise QgsProcessingException(self.tr("The DEM must be north-up (not rotated)."))
        c0 = max(0, int(np.floor((rect.xMinimum() - gt[0]) / gt[1])))
        c1 = min(ds.RasterXSize, int(np.ceil((rect.xMaximum() - gt[0]) / gt[1])))
        r0 = max(0, int(np.floor((rect.yMaximum() - gt[3]) / gt[5])))
        r1 = min(ds.RasterYSize, int(np.ceil((rect.yMinimum() - gt[3]) / gt[5])))
        if c1 <= c0 or r1 <= r0:
            raise QgsProcessingException(self.tr("The DEM does not cover the field."))
        ncell = (c1 - c0) * (r1 - r0)
        if ncell > 150e6:
            raise QgsProcessingException(self.tr(
                "The area covers {:,} DEM cells; resample the DEM first.").format(ncell))
        band = ds.GetRasterBand(1)
        z = band.ReadAsArray(c0, r0, c1 - c0, r1 - r0).astype(float)
        nd = band.GetNoDataValue()
        if nd is not None:
            z[z == nd] = np.nan
        z[z < -1e20] = np.nan
        z *= zf_ft
        return z, gt[0] + c0 * gt[1], gt[3] + r0 * gt[5], gt[1], gt[5]

    def contours(self, z, x0, y0, dx, dy, interval, crs):
        from osgeo import gdal, ogr
        ds = gdal.GetDriverByName("MEM").Create("", z.shape[1], z.shape[0], 1,
                                                gdal.GDT_Float32)
        ds.SetGeoTransform((x0, dx, 0, y0, 0, dy))
        b = ds.GetRasterBand(1)
        b.WriteArray(np.where(np.isfinite(z), z, -99999.0).astype(np.float32))
        b.SetNoDataValue(-99999.0)
        drv = ogr.GetDriverByName("Memory") or ogr.GetDriverByName("MEM")
        ods = drv.CreateDataSource("c")
        ol = ods.CreateLayer("c", geom_type=ogr.wkbLineString)
        ol.CreateField(ogr.FieldDefn("id", ogr.OFTInteger))
        ol.CreateField(ogr.FieldDefn("elev", ogr.OFTReal))
        gdal.ContourGenerate(b, float(interval), 0.0, [], 1, -99999.0, ol, 0, 1)
        major = interval * (4 if abs(interval - 0.5) < 1e-9 else 5)
        rows = []
        for f in ol:
            g = f.GetGeometryRef()
            if g is None:
                continue
            e = f.GetField(1)
            q = QgsGeometry()
            q.fromWkb(bytes(g.ExportToWkb()))
            rows.append((q, [round(e, 2), 1 if abs(e / major - round(e / major)) < 1e-6
                             else 0]))
        lyr = maps.memory_layer("LineString", crs, [("elev", QVariant.Double),
                                                    ("major", QVariant.Int)], rows, "Contours")
        maps.style_contours(lyr)
        return lyr

    def polygonize(self, labels, x0, y0, dx, dy):
        """{label: QgsGeometry} of an integer label grid (0 = none)."""
        from osgeo import gdal, ogr
        ds = gdal.GetDriverByName("MEM").Create("", labels.shape[1], labels.shape[0], 1,
                                                gdal.GDT_Int32)
        ds.SetGeoTransform((x0, dx, 0, y0, 0, dy))
        b = ds.GetRasterBand(1)
        b.WriteArray(labels.astype(np.int32))
        mds = gdal.GetDriverByName("MEM").Create("", labels.shape[1], labels.shape[0], 1,
                                                 gdal.GDT_Byte)
        mds.SetGeoTransform((x0, dx, 0, y0, 0, dy))
        mds.GetRasterBand(1).WriteArray((labels > 0).astype(np.uint8))
        drv = ogr.GetDriverByName("Memory") or ogr.GetDriverByName("MEM")
        ods = drv.CreateDataSource("p")
        ol = ods.CreateLayer("p", geom_type=ogr.wkbPolygon)
        ol.CreateField(ogr.FieldDefn("lab", ogr.OFTInteger))
        gdal.Polygonize(b, mds.GetRasterBand(1), ol, 0, [], callback=None)
        parts = {}
        for f in ol:
            q = QgsGeometry()
            q.fromWkb(bytes(f.GetGeometryRef().ExportToWkb()))
            parts.setdefault(f.GetField(0), []).append(q)
        return {k: QgsGeometry.unaryUnion(v) for k, v in parts.items() if k}

    # ------------------------------------------------------------ main
    def processAlgorithm(self, parameters, context, feedback):
        self._save_settings(parameters)
        P = parameters
        self.P, self.context, self.feedback = P, context, feedback
        ctx = self.gather(P, context, feedback)
        data = self.build(ctx)
        tpl = self.parameterAsFile(P, "TEMPLATE", context) or os.path.join(
            PLUGIN_DIR, "template", self.TEMPLATE_FILE)
        out = self.parameterAsFileOutput(P, "OUTPUT", context)
        if not out.lower().endswith(".docx"):
            out += ".docx"
        try:
            self.write(tpl, data, out)
        except ValueError as e:
            raise QgsProcessingException(str(e))
        feedback.setProgress(100)
        for w in ctx["warnings"]:
            feedback.reportError(w, fatalError=False)
        feedback.pushInfo("Report written: {}".format(out))
        return {"OUTPUT": out}

    def s(self, name):
        return self.parameterAsString(self.P, name, self.context).strip()

    def d(self, name):
        return self.parameterAsDouble(self.P, name, self.context)

    def gather(self, P, context, feedback):
        """Everything both reports need. Returns a dict (ctx)."""
        c = {"warnings": []}
        W = c["warnings"]
        tctx = context.transformContext()
        c["tctx"] = tctx
        wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        dem = self.parameterAsRasterLayer(P, "DEM", context)
        if dem is None:
            raise QgsProcessingException(self.tr("Could not load the DEM."))
        crs = dem.crs()
        if crs.isGeographic():
            raise QgsProcessingException(self.tr(
                "The DEM must be in a projected coordinate system (metres or feet)."))
        c["crs"] = crs
        ft = QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), Qgis.DistanceUnit.Feet)
        c["ft"] = ft
        ac = (ft * 0.3048) ** 2 / M2_AC
        c["ac"] = ac
        zf_ft = Z_FACTORS[self.parameterAsEnum(P, "Z_UNITS", context)] * FT_PER_M
        df_ft = 1.0 if self.parameterAsEnum(P, "DESIGN_UNITS", context) == 1 else FT_PER_M
        to_wgs = QgsCoordinateTransform(crs, wgs, tctx)
        c["to_wgs"] = to_wgs

        def latlon(x, y):
            p = to_wgs.transform(QgsPointXY(x, y))
            return fmt_latlon(p.y(), p.x())
        c["latlon"] = latlon

        def source(name):
            return self.parameterAsSource(P, name, context)

        def geoms_of(src, kind=None):
            out = []
            if src is None:
                return out
            t = QgsCoordinateTransform(src.sourceCrs(), crs, tctx)
            for f in src.getFeatures():
                g = QgsGeometry(f.geometry())
                if g.isNull() or g.isEmpty():
                    continue
                g.transform(t)
                out.append((g, f))
            return out

        # ---- field
        fl = geoms_of(source("FIELD"))
        if not fl:
            raise QgsProcessingException(self.tr("The field boundary has no polygons."))
        field = QgsGeometry.unaryUnion([g.makeValid() for g, _ in fl])
        c["field"] = field
        c["field_ac"] = field.area() * ac
        cen = field.centroid().asPoint()
        c["center"] = cen
        field_lyr = maps.memory_layer("Polygon", crs, [("n", QVariant.Int)],
                                      [(field, [1])], "Field")
        maps.style_outline(field_lyr)
        c["field_lyr"] = field_lyr
        feedback.pushInfo("Field: {:.1f} acres".format(c["field_ac"]))

        # ---- network
        pipes, lines, net_rows = [], [], []
        flow, dcs = [], set()
        nsrc = source("NETWORK")
        if nsrc is not None:
            low = {f.name().lower(): f.name() for f in nsrc.fields()}
            kind_f = low.get("kind")
            size_f = next((low[k] for k in ("nominal", "actual_size") if k in low), None)
            id_f = next((low[k] for k in ("pipe_id", "pipe", "name", "id") if k in low), None)
            dc_f = low.get("d_coeff")
            mat_f = next((low[k] for k in ("material", "pipe_material", "pipe_type",
                                           "pipe_mat") if k in low), None)
            for g, f in geoms_of(nsrc):
                kind = sval(f[kind_f]).lower() if kind_f else ""
                size = sval(f[size_f]) if size_f else ""
                size = "{} in".format(size) if size and not size.endswith("in") else size
                pid = sval(f[id_f]) if id_f else str(f.id())
                if dc_f and num(f[dc_f]):
                    dcs.add(round(num(f[dc_f]), 3))
                has_z = QgsWkbTypes.hasZ(g.wkbType())
                has_m = QgsWkbTypes.hasM(g.wkbType())
                parts = list(g.constGet().parts()) if g.isMultipart() else [g.constGet()]
                arrs = []
                for part in parts:
                    a = np.array([(v.x() * ft, v.y() * ft,
                                   v.z() * df_ft if has_z else np.nan,
                                   v.m() * df_ft if has_m else np.nan)
                                  for v in part.vertices()], float)
                    for col in (2, 3):
                        if len(a) and np.all(a[:, col] == 0):
                            a[:, col] = np.nan
                    if len(a) >= 2:
                        arrs.append(a)
                if not arrs:
                    continue
                a = np.vstack(arrs)
                pipes.append({"id": pid or str(f.id()), "kind": kind or "tile",
                              "size": size, "a": a, "geom": g,
                              "material": sval(f[mat_f]) if mat_f else ""})
            # flowline elevations where tile crosses the field boundary
            boundary = QgsGeometry(field.constGet().boundary())
            for p in pipes:
                g = p["geom"]
                if not QgsWkbTypes.hasZ(g.wkbType()) or not g.intersects(boundary):
                    continue
                inter = g.intersection(boundary)
                pts = inter.asMultiPoint() if inter.isMultipart() else (
                    [inter.asPoint()] if inter.type() == Qgis.GeometryType.Point else [])
                for pt in pts:
                    ip = g.interpolate(g.lineLocatePoint(QgsGeometry.fromPointXY(pt)))
                    try:
                        zz = ip.constGet().z() * df_ft
                        if np.isfinite(zz) and zz != 0:
                            flow.append(zz)
                    except (AttributeError, TypeError):
                        pass
            if not pipes:
                W.append("The drainage network has no lines.")
        c.update(pipes=pipes, flow=flow, dcs=sorted(dcs))

        # ---- DEM window
        rect = QgsRectangle(field.boundingBox())
        for p in pipes:
            rect.combineExtentWith(p["geom"].boundingBox())
        reach = self.d("RUNON_REACH") / ft
        rect.grow(max(reach, max(rect.width(), rect.height()) * 0.05 + 100.0 / ft))
        feedback.setProgressText(self.tr("Reading the DEM"))
        z, x0, y0, dx, dy = self.read_dem(dem, rect, zf_ft)
        c.update(z=z, x0=x0, y0=y0, dx=dx, dy=dy)
        cell_ft = abs(dx) * ft

        def dem_at(xs, ys):
            j = np.floor((np.asarray(xs) - x0) / dx).astype(int)
            i = np.floor((np.asarray(ys) - y0) / dy).astype(int)
            ok = (i >= 0) & (i < z.shape[0]) & (j >= 0) & (j < z.shape[1])
            out = np.full(np.shape(xs), np.nan)
            out[ok] = z[i[ok], j[ok]]
            return out
        c["dem_at"] = dem_at
        for p in pipes:
            a = p["a"]
            bad = ~np.isfinite(a[:, 3])
            if bad.any():
                a[bad, 3] = dem_at(a[bad, 0] / ft, a[bad, 1] / ft)
        feedback.setProgress(10)

        # ---- network numbers
        lat = [p["a"] for p in pipes if p["kind"] == "lateral"] or [p["a"] for p in pipes]
        c["depth_lat"] = depth_range(lat) if lat else None
        c["depth_all"] = depth_range([p["a"] for p in pipes]) if pipes else None
        c["spacing"] = lateral_spacing([a[:, :2] for a in lat]) if lat else None
        sched = pipe_schedule(pipes)
        c["schedule"] = sched
        by_id = {r["id"]: r for r in sched}
        material = self.s("MATERIAL") or "Corrugated polyethylene (HDPE)"
        c["material"] = material
        comps = component_rows([(p["kind"], p["size"],
                                 by_id.get(p["id"], {}).get("length_exact", 0),
                                 p["material"]) for p in pipes], material)
        c["pipe_summary"] = pipe_summary([
            (p["size"], p["material"] or material,
             by_id.get(p["id"], {}).get("length_exact", 0)) for p in pipes])
        c["components"] = comps
        c["tile_note"] = "; ".join("{} {:,.0f} ft".format(r[0], r[2]) for r in comps)
        if pipes:
            rows = []
            for p in pipes:
                r = by_id.get(p["id"], {})
                rows.append((p["geom"], [p["kind"], p["size"] or None, r.get("label")]))
            net = maps.memory_layer("LineString", crs, [("kind", QVariant.String),
                                                        ("size", QVariant.String),
                                                        ("label", QVariant.String)],
                                    rows, "Drainage network")
            maps.style_network(net)
            faint = maps.memory_layer("LineString", crs, [("kind", QVariant.String),
                                                          ("size", QVariant.String)],
                                      [(r[0], r[1][:2]) for r in rows], "Tile")
            maps.style_network(faint, faint=True)
            plain = maps.memory_layer("LineString", crs, [("kind", QVariant.String),
                                                          ("size", QVariant.String)],
                                      [(r[0], r[1][:2]) for r in rows], "Tile")
            maps.style_network(plain, labels=False)
            c.update(net_lyr=net, faint_lyr=faint, plain_lyr=plain)
        else:
            c.update(net_lyr=None, faint_lyr=None, plain_lyr=None)
        ex = geoms_of(source("EXISTING"))
        if ex:
            lyr = maps.memory_layer("LineString", crs, [("n", QVariant.Int)],
                                    [(g, [1]) for g, _ in ex], "Existing tile")
            maps.style_existing(lyr)
            c["existing_lyr"] = lyr
            c["existing_ft"] = sum(g.length() for g, _ in ex) * ft
        else:
            c["existing_lyr"] = None

        # ---- soils
        feedback.setProgressText(self.tr("Soils"))
        pieces = []
        ssrc = source("SOILS")
        if ssrc is not None:
            low = {f.name().lower(): f.name() for f in ssrc.fields()}
            for g, f in geoms_of(ssrc):
                gi = g.makeValid().intersection(field)
                if gi.isEmpty() or gi.area() <= 0:
                    continue
                rec = {"musym": sval(f[low["musym"]]) if "musym" in low else "",
                       "muname": sval(f[low["muname"]]) if "muname" in low else "",
                       "spacing": num(f[low["spacing_ft"]]) if "spacing_ft" in low else None,
                       "acres": gi.area() * ac, "geom": gi}
                if not rec["musym"] and "series" in low:
                    rec["musym"] = sval(f[low["series"]])
                pieces.append(rec)
        elif self.parameterAsBool(P, "FETCH_SOILS", context):
            try:
                from .sda import sda_soils
                pw = QgsGeometry(field)
                pw.transform(to_wgs)
                rows = sda_soils(pw.simplify(0.00001).asWkt(6), feedback)
                tw = QgsCoordinateTransform(wgs, crs, tctx)
                for r in rows:
                    g = QgsGeometry.fromWkt(r.get("wkt") or "")
                    if g.isNull() or g.isEmpty():
                        continue
                    g.transform(tw)
                    gi = g.makeValid().intersection(field)
                    if gi.isEmpty():
                        continue
                    pieces.append({"musym": sval(r.get("musym")),
                                   "muname": sval(r.get("muname")), "spacing": None,
                                   "acres": gi.area() * ac, "geom": gi})
            except Exception as e:  # noqa: BLE001 - reported, not fatal
                W.append("Soil Data Access: {}".format(e))
        c["soil_rows"] = soil_rows(pieces)
        c["soil_pieces"] = pieces
        soil_lyr = None
        if pieces:
            soil_lyr = maps.memory_layer(
                "MultiPolygon", crs, [("musym", QVariant.String), ("muname", QVariant.String)],
                [(p["geom"], [p["musym"], p["muname"]]) for p in pieces], "Soils")
            maps.style_soils(soil_lyr, [r[0] for r in c["soil_rows"]])
        c["soil_lyr"] = soil_lyr

        # dominant soil under the tile and its recommended spacing
        c["dominant"], c["rec_spacing"] = "", None
        if pipes and pieces:
            near = QgsGeometry.unaryUnion([p["geom"].buffer((c["spacing"] or 80.0) / 2.0 / ft,
                                                            8) for p in pipes])
            acc = {}
            for p in pieces:
                a = p["geom"].intersection(near).area()
                e = acc.setdefault(p["musym"], [0.0, p["muname"], 0.0, 0.0])
                e[0] += a
                if p["spacing"]:
                    e[2] += p["spacing"] * a
                    e[3] += a
            k, e = max(acc.items(), key=lambda kv: kv[1][0])
            c["dominant"] = series_name(e[1]) or k
            c["rec_spacing"] = e[2] / e[3] if e[3] > 0 else None
        feedback.setProgress(20)

        # ---- crops
        crops = ""
        csrc = source("CROPS")
        if csrc is not None:
            cl = {f.name().lower(): f.name() for f in csrc.fields()}
            rows = []
            for g, f in geoms_of(csrc):
                if not g.intersects(field) or g.intersection(field).area() < 0.1 * g.area():
                    continue
                rows.append({k: (sval(f[cl[k]]) if k in cl else "")
                             for k in ("field", "rotation", "sequence", "years")})
            crops = crop_text(rows)
        c["crops"] = "\n".join(x for x in (crops, self.s("CROPS_TEXT")) if x)

        # ---- contours
        feedback.setProgressText(self.tr("Contours"))
        interval = self.d("CONTOUR")
        rad = self.d("CONTOUR_SMOOTH")
        c["interval"], c["smooth_ft"] = interval, rad
        try:
            zc = smooth(z, int(round(rad / cell_ft))) if rad > 0 else z
            c["contour_lyr"] = self.contours(zc, x0, y0, dx, dy, interval, crs)
        except Exception as e:  # noqa: BLE001
            c["contour_lyr"] = None
            W.append("Contours could not be made: {}".format(e))
        feedback.setProgress(30)

        # ---- depressions, run-on, laylines
        feedback.setProgressText(self.tr("Depressions and run-on"))
        fmask = polygon_mask(rings(field), x0, y0, dx, dy, z.shape[0], z.shape[1])
        c["field_mask"] = fmask
        factor = int(np.ceil(np.sqrt(z.size / MAX_TERRAIN_CELLS))) if z.size > \
            MAX_TERRAIN_CELLS else 1
        zt = coarsen(z, factor)
        fm_t = fmask if factor == 1 else polygon_mask(rings(field), x0, y0, dx * factor,
                                                       dy * factor, zt.shape[0], zt.shape[1])
        if factor > 1:
            feedback.pushInfo("Terrain analysis on {:.1f} ft cells".format(cell_ft * factor))
        ter = depressions_and_runon(
            zt, fm_t, cell_ft * factor, self.d("DEP_DEPTH"), self.d("DEP_MIN"),
            self.d("RUNON_MIN"), progress=lambda f: feedback.setProgress(30 + int(25 * f)),
            is_canceled=feedback.isCanceled)
        tdx, tdy = dx * factor, dy * factor
        dep_rows, dep_feats, run_feats = [], [], []
        dsrc = source("DEPRESSIONS")
        if dsrc is not None:
            dl = {f.name().lower(): f.name() for f in dsrc.fields()}
            for k, (g, f) in enumerate(geoms_of(dsrc)):
                if not g.intersects(field):
                    continue
                area = num(f[dl["area_ac"]]) if "area_ac" in dl else g.area() * ac
                mx = num(f[dl["max_dep_ft"]]) if "max_dep_ft" in dl else None
                vol = num(f[dl["vol_cu_yd"]]) if "vol_cu_yd" in dl else None
                dep_feats.append((g, "D{}".format(len(dep_feats) + 1), area, mx, vol))
        else:
            polys = self.polygonize(ter["dep_labels"], x0, y0, tdx, tdy)
            for s in ter["deps"]:
                g = polys.get(s["id"])
                if g is not None:
                    dep_feats.append((g, "D{}".format(len(dep_feats) + 1), s["area_ac"],
                                      s["max_depth"], s["volume_cy"]))
        rpolys = self.polygonize(ter["run_labels"], x0, y0, tdx, tdy)
        for s in ter["runs"]:
            g = rpolys.get(s["id"])
            if g is not None:
                run_feats.append((g, "R{}".format(len(run_feats) + 1), s["area_ac"], s["edge"]))
        for g, lab, a, mx, vol in dep_feats:
            dep_rows.append([lab, "Depression", "{:.2f}".format(a or 0),
                             "" if mx is None else "{:.2f}".format(mx),
                             "" if vol is None else "{:,.0f}".format(vol)])
        for g, lab, a, edge in run_feats:
            dep_rows.append([lab, "Run-on area" + (" (extends beyond the DEM)" if edge else ""),
                             "{:.1f}".format(a), "", ""])
        c["dep_rows"] = dep_rows
        c["dep_lyr"] = None
        if dep_feats:
            c["dep_lyr"] = maps.memory_layer(
                "MultiPolygon", crs, [("lbl", QVariant.String)],
                [(g, ["{}\n{:.2f} ac".format(lab, a or 0)]) for g, lab, a, _, _ in dep_feats],
                "Depressions")
            maps.style_labeled_fill(c["dep_lyr"], "#1e88e5", "#0d47a1", 170, '"lbl"',
                                    "#0d47a1", 7)
        c["run_lyr"] = None
        if run_feats:
            c["run_lyr"] = maps.memory_layer(
                "MultiPolygon", crs, [("lbl", QVariant.String)],
                [(g, ["{}\n{:.1f} ac".format(lab, a)]) for g, lab, a, _ in run_feats],
                "Run-on areas")
            maps.style_labeled_fill(c["run_lyr"], "#fb8c00", "#e65100", 110, '"lbl"',
                                    "#bf360c", 8)
        if ter["edge"]:
            W.append("A run-on area reaches the edge of the DEM: it extends beyond the DEM, "
                     "so its area is a minimum. Extend the DEM (or Run-on reach) to map it "
                     "all.")
        c["dep_extent"] = QgsRectangle(field.boundingBox())
        for g, *_ in run_feats:
            c["dep_extent"].combineExtentWith(g.boundingBox())
        feedback.pushInfo("Depressions: {}; run-on areas: {} ({:.1f} acres)".format(
            len(dep_feats), len(run_feats), sum(r[2] for r in run_feats)))

        # laylines
        c["lay_lyr"] = None
        lsrc = source("LAYLINES")
        if lsrc is not None:
            rows = [(g.intersection(field), [1]) for g, _ in geoms_of(lsrc)
                    if g.intersects(field)]
        else:
            try:
                zr, rec, nod = ter["routing"], ter["rec"], ter["nodata"]
                cell_ac = (cell_ft * factor) ** 2 / 43560.0
                thr = max(1.0, self.d("LAYLINE_AC") / cell_ac)
                acc, strahler = terrain.accumulate(zr, nod, rec, thr,
                                                   is_canceled=feedback.isCanceled)
                segs = terrain.channel_segments(rec, acc, strahler, thr)
                cols = zt.shape[1]
                rows = []
                for sg in segs:
                    pts = [QgsPointXY(x0 + (q % cols + 0.5) * tdx, y0 + (q // cols + 0.5) * tdy)
                           for q in sg["cells"]]
                    g = QgsGeometry.fromPolylineXY(pts)
                    if len(pts) > 2:
                        g = g.smooth(2, 0.25)
                    if g.intersects(field):
                        rows.append((g.intersection(field), [sg["strahler"]]))
            except Exception as e:  # noqa: BLE001
                rows = []
                W.append("Laylines could not be found: {}".format(e))
        if rows:
            c["lay_lyr"] = maps.memory_layer("LineString", crs, [("order", QVariant.Int)],
                                             rows, "Laylines")
            maps.style_lines(c["lay_lyr"], "#0288d1", 0.6)
        feedback.setProgress(60)

        # ---- watershed
        c["ws_lyr"] = c["outlet_lyr"] = c["streams_lyr"] = None
        ws = geoms_of(source("WATERSHED"))
        ws = [(g, f) for g, f in ws if g.intersects(field.buffer(5000.0 / ft, 8))] or ws
        c["ws_geom"] = None
        if ws:
            g = QgsGeometry.unaryUnion([g for g, _ in ws])
            c["ws_geom"] = g
            c["ws_lyr"] = maps.memory_layer("MultiPolygon", crs, [("n", QVariant.Int)],
                                            [(g, [1])], "Watershed")
            maps.style_fill(c["ws_lyr"], "#90caf9", outline="#0d47a1", width=0.8, alpha=70)
            if not g.contains(field):
                W.append("The watershed does not contain the whole field.")
        out = geoms_of(source("OUTLET"))
        c["outlet_coords"] = ""
        if out:
            pt = out[0][0].asPoint() if not out[0][0].isMultipart() else \
                out[0][0].asMultiPoint()[0]
            c["outlet_coords"] = latlon(pt.x(), pt.y())
            c["outlet_lyr"] = maps.memory_layer("Point", crs, [("lbl", QVariant.String)],
                                                [(QgsGeometry.fromPointXY(pt), ["Outlet"])],
                                                "Outlet")
            maps.style_points(c["outlet_lyr"], "circle", "#d50000", 3.2, '"lbl"', "#b71c1c")
        st = geoms_of(source("STREAMS"))
        if st:
            c["streams_lyr"] = maps.memory_layer("LineString", crs, [("n", QVariant.Int)],
                                                 [(g, [1]) for g, _ in st], "Streams")
            maps.style_lines(c["streams_lyr"], "#1565c0", 0.5)
        return c

    # ------------------------------------------------------------ design values
    def design_values(self, c, unit=" ft"):
        def pick(param, auto, nd=1):
            v = self.s(param)
            if v:
                try:
                    return "{:.{}f}{}".format(float(v), nd, unit)
                except ValueError:
                    return v
            return "" if auto is None else "{:.{}f}{}".format(auto, nd, unit)
        dl, da = c["depth_lat"] or (None, None), c["depth_all"] or (None, None)
        dc = self.s("DC")
        if dc:
            try:
                dc = "{:g} in/day".format(float(dc))
            except ValueError:
                pass
        elif c["dcs"]:
            dc = ", ".join("{:g}".format(v) for v in c["dcs"]) + " in/day"
        other = [self.s("OTHER")] if self.s("OTHER") else []
        if c["flow"]:
            other.append("Flowline elevation(s) where tile crosses the field boundary: " +
                         ", ".join("{:.2f}".format(v) for v in sorted(set(
                             round(x, 2) for x in c["flow"]))[:8]) + " ft.")
        if c.get("existing_lyr") is not None:
            other.append("Existing tile shown dashed ({:,.0f} ft).".format(c["existing_ft"]))
        return {"upper": pick("UPPER", dl[0]), "lower": pick("LOWER", dl[1]),
                "abs_upper": pick("ABS_UPPER", da[0]), "abs_lower": pick("ABS_LOWER", da[1]),
                "spacing": "" if not c["spacing"] else "{:.0f}{}".format(c["spacing"], unit),
                "dc": dc, "materials": ", ".join(sorted({p["material"] or c["material"]
                                                         for p in c["pipes"]}))
                or c["material"], "other": " ".join(other),
                "_dc_value": (float(self.s("DC")) if _isnum(self.s("DC")) else
                              (min(c["dcs"]) if c["dcs"] else None))}

    # ------------------------------------------------------------ maps
    def render(self, c, layers, title, legend, rect=None, height=SHEET_H_IN, scale=None,
               center=None, max_scale=0):
        rect = rect or self.field_rect(c)
        if scale is None:
            scale = maps.page_scale(rect, c["crs"], height_in=height)
            if max_scale:
                scale = min(scale, max_scale)
        center = center or rect.center()
        return maps.render(layers, c["crs"], center, scale, c["tctx"], height_in=height,
                           title=title, legend=legend, feedback=self.feedback), scale

    def field_rect(self, c, grow=0.04):
        r = QgsRectangle(c["field"].boundingBox())
        for p in c["pipes"]:
            r.combineExtentWith(p["geom"].boundingBox())
        r.grow(max(r.width(), r.height()) * grow + 30.0 / c["ft"])
        return r

    def _sheet_centers(self, c, rect, scale):
        """Centres of the sheets at `scale` that show some of the field or tile."""
        w_mu = scale * maps.PAGE_W_IN / 12.0 / c["ft"]
        h_mu = scale * SHEET_H_IN / 12.0 / c["ft"]
        out = []
        for x, y in sheet_centers(rect.xMinimum(), rect.yMinimum(), rect.xMaximum(),
                                  rect.yMaximum(), w_mu, h_mu):
            sheet = QgsGeometry.fromRect(QgsRectangle(x - w_mu / 2, y - h_mu / 2,
                                                      x + w_mu / 2, y + h_mu / 2))
            if c["field"].intersects(sheet) or any(p["geom"].intersects(sheet)
                                                   for p in c["pipes"]):
                out.append((x, y))
        return out

    def sheet_plan(self, c, max_scale, max_pages):
        """(scale, centres or None for one page) shared by the system and topographic
        maps, so both use the same sheets."""
        if "sheet_plan" in c:
            return c["sheet_plan"]
        rect = self.field_rect(c)
        fit = maps.page_scale(rect, c["crs"], height_in=SHEET_H_IN)
        if not max_scale or fit <= max_scale:
            plan = (fit, None)
        else:
            cache = {}

            def count_at(s):
                cache[s] = self._sheet_centers(c, rect, s)
                return len(cache[s])
            scale, n = sheet_scale_for_pages(count_at, max_scale, fit, max_pages)
            plan = (scale, cache.get(scale) if n is not None else None)
            if scale != max_scale:
                if max_scale not in cache:
                    count_at(max_scale)
                c["warnings"].append(
                    "System and topographic maps at {} to fit {} page(s); the template asks "
                    "for {} or closer ({} sheets at that scale).".format(
                        scale_text(scale), max_pages, scale_text(max_scale),
                        len(cache.get(max_scale) or [])))
        c["sheet_plan"] = plan
        return plan

    def sheets(self, c, layers, title, legend, caption, max_scale, max_pages=0):
        """The map on one page, or on sheets at `max_scale`; with `max_pages`, at the
        largest standard scale that needs no more than that many pages."""
        scale, centers = self.sheet_plan(c, max_scale, max_pages)
        if centers is None:
            rect = self.field_rect(c)
            h = min(SHEET_H_IN, max(4.0, rect.height() * c["ft"] * 12.0 / scale + 0.6))
            png, sc = self.render(c, layers, title, legend, rect, h, scale)
            return [(png, "{}. Scale {}".format(caption, scale_text(sc)))], scale
        res = []
        for k, (x, y) in enumerate(centers):
            t = "{} - sheet {} of {}".format(title, k + 1, len(centers))
            png, sc = self.render(c, layers, t, legend, height=SHEET_H_IN, scale=scale,
                                  center=QgsPointXY(x, y))
            res.append((png, "{}. Sheet {} of {}. Scale {}".format(
                caption, k + 1, len(centers), scale_text(sc))))
        return res, scale

    def common_maps(self, c, system_title):
        """Location, background, soils, watershed, depressions, topographic and system maps."""
        img, cap = {}, {}
        crs, ft = c["crs"], c["ft"]
        LF = ("line", "#d00000", "Field boundary")
        net_leg = [("line", "#b91c1c", "Main"), ("line", "#ea580c", "Submain"),
                   ("line", "#1d4ed8", "Lateral")] if c["pipes"] else []
        ex_leg = [("line", "#6b7280", "Existing tile")] if c["existing_lyr"] else []
        bm_kind = self.parameterAsEnum(self.P, "BASEMAP", self.context)
        bm = maps.basemap(bm_kind)
        credit = {1: "background © OpenStreetMap contributors",
                  2: "background Esri World Imagery"}.get(bm_kind if bm else 0, "")
        # location
        r = QgsRectangle(c["field"].boundingBox())
        r.grow(max(r.width(), r.height()) * 3 + 2000.0 / ft)
        loc = maps.memory_layer("Polygon", crs, [("n", QVariant.Int)],
                                [(QgsGeometry(c["field"]), [1])], "Field")
        maps.style_outline(loc, width=1.2)
        png, sc = self.render(c, [loc, bm], "Location", [LF], r, 4.5,
                              center=c["center"])
        img["location"] = png
        cap["location"] = "Field {:.1f} acres, centre {}. Scale {}{}.".format(
            c["field_ac"], c["latlon"](c["center"].x(), c["center"].y()), scale_text(sc),
            "; " + credit if credit else "")
        self.feedback.setProgress(64)
        # background: the field on imagery with any existing tile
        imagery = maps.basemap(2) if self.parameterAsBool(self.P, "IMAGERY",
                                                          self.context) else None
        png, sc = self.render(c, [c["existing_lyr"], c["field_lyr"], imagery], "Field",
                              [LF] + ex_leg, height=5.5)
        img["background"] = png
        cap["background"] = "Scale {}{}.".format(
            scale_text(sc), "; imagery Esri World Imagery" if imagery else "")
        # soils
        png, sc = self.render(c, [c["field_lyr"], c["soil_lyr"]], "Soil map", [LF],
                              height=5.4)
        img["soils"] = png
        cap["soils"] = "Scale {}. Soils: USDA-NRCS SSURGO.".format(scale_text(sc))
        # watershed
        if c["ws_geom"] is not None:
            wr = QgsRectangle(c["ws_geom"].boundingBox())
            wr.combineExtentWith(c["field"].boundingBox())
            wr.grow(max(wr.width(), wr.height()) * 0.05)
            fl = maps.memory_layer("Polygon", crs, [("n", QVariant.Int)],
                                   [(QgsGeometry(c["field"]), [1])], "Field")
            maps.style_outline(fl, width=1.4)
            png, sc = self.render(
                c, [c["outlet_lyr"], fl, c["streams_lyr"], c["ws_lyr"], bm], "Watershed",
                [LF, ("fill", "#90caf9", "Watershed"), ("point", "#d50000", "Outlet")], wr,
                5.5)
            a_ac = c["ws_geom"].area() * c["ac"]
            img["watershed"] = png
            cap["watershed"] = "Watershed {:,.0f} acres ({:.2f} sq mi). Scale {}{}.".format(
                a_ac, a_ac / 640.0, scale_text(sc), "; " + credit if credit else "")
        # depressions and run-on
        dr = QgsRectangle(c["dep_extent"])
        dr.grow(max(dr.width(), dr.height()) * 0.04 + 30.0 / ft)
        png, sc = self.render(c, [c["field_lyr"], c["dep_lyr"], c["run_lyr"], c["faint_lyr"],
                                  c["contour_lyr"]], "Depressions and run-on areas",
                              [LF, ("fill", "#1e88e5", "Depression"),
                               ("fill", "#fb8c00", "Run-on area")], dr, 5.0)
        img["depressions"] = png
        cap["depressions"] = "Scale {}.".format(scale_text(sc))
        self.feedback.setProgress(72)
        max_scale = self.parameterAsInt(self.P, "MAX_SCALE", self.context)
        max_pages = self.parameterAsInt(self.P, "MAX_PAGES", self.context)
        # topographic
        lay_leg = [("line", "#0288d1", "Layline")] if c["lay_lyr"] else []
        topo, tsc = self.sheets(
            c, [c["field_lyr"], c["lay_lyr"], c["faint_lyr"], c["contour_lyr"]],
            "Topographic map", [LF, ("line", "#8a6d3b", "Contour, {:g} ft".format(
                c["interval"]))] + lay_leg,
            "Contours at {:g} ft from the LiDAR DEM{} (elevations in ft)".format(
                c["interval"], ", smoothed over {:g} ft".format(c["smooth_ft"])
                if c["smooth_ft"] else ""), max_scale, max_pages)
        img["topo"] = topo
        self.feedback.setProgress(80)
        system, ssc = self.sheets(
            c, [c["field_lyr"], c["net_lyr"], c["existing_lyr"]], system_title,
            [LF] + net_leg + ex_leg,
            "Labels: size, length (nearest 10 ft), slope (nearest 0.05 %)" +
            ("; " + c["tile_note"] if c["tile_note"] else ""), max_scale, max_pages)
        img["system"] = system if c["pipes"] else []
        self.feedback.setProgress(88)
        return img, cap

    def site(self, c):
        return {"county_township": self.s("COUNTY_TWP"),
                "latlon": c["latlon"](c["center"].x(), c["center"].y()),
                "crops": c["crops"], "total_acres": "{:.1f} acres".format(c["field_ac"])}

    def pipe_tables(self, c):
        """(summary rows, schedule rows) as chosen in Pipe table."""
        k = self.parameterAsEnum(self.P, "PIPE_TABLE", self.context)
        return (c["pipe_summary"] if k in (0, 2) else [],
                c["schedule"] if k in (1, 2) else [])


def _isnum(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False
