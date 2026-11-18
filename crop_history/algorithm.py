"""Processing algorithm: crop history of fields from the USDA NASS Cropland Data Layer."""
import datetime
import math
import os
import tempfile

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
    QgsMapLayer,
    QgsPalettedRasterRenderer,
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
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProject,
    QgsRasterLayer,
    QgsRendererCategory,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QColor, QIcon

from .cdl import FIRST_YEAR, CdlError, CdlUnavailable, get_cdl
from .core import LETTER_KEY, ROTATIONS, cell_rotation, color, field_history, name

PLUGIN_DIR = os.path.dirname(__file__)
ALBERS = "EPSG:5070"
CLUSTER_SIZE = 10000.0        # m: fields within one 10 km box share a download
BUFFER = 60.0                 # m around each field
_POST = {}

# Settings of the last run, kept while the project stays open; cleared by the
# plugin when a new project is started or another one is loaded.
_LAST_RUN = {}
LAYER_INPUTS = ("FIELD",)


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
        from qgis.core import Qgis
        param.setFlags(param.flags() | Qgis.ProcessingParameterFlag.Advanced)
    except (ImportError, AttributeError):
        param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
    return param


class _PalettedStyler(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, classes, opacity=1.0):
        super().__init__()
        self.classes = classes            # [(value, (r, g, b), label)]
        self.opacity = opacity

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsRasterLayer):
            return
        cl = [QgsPalettedRasterRenderer.Class(v, QColor(*rgb), lbl)
              for v, rgb, lbl in self.classes]
        r = QgsPalettedRasterRenderer(layer.dataProvider(), 1, cl)
        r.setOpacity(self.opacity)
        layer.setRenderer(r)
        layer.triggerRepaint()


class _FieldStyler(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, rotations):
        super().__init__()
        self.rotations = rotations

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsVectorLayer):
            return
        cats = []
        for code in self.rotations:
            nm, rgb = ROTATIONS.get(code, ("No data", (200, 200, 200)))
            sym = QgsFillSymbol.createSimple({
                "color": "{},{},{},90".format(*rgb), "outline_color": "{},{},{}".format(*rgb),
                "outline_width": "0.6"})
            cats.append(QgsRendererCategory(nm, sym, nm))
        layer.setRenderer(QgsCategorizedSymbolRenderer("rotation", cats))
        from qgis.core import QgsPalLayerSettings, QgsVectorLayerSimpleLabeling
        s = QgsPalLayerSettings()
        s.fieldName = "\"field\" || '\\n' || \"sequence\""
        s.isExpression = True
        layer.setLabeling(QgsVectorLayerSimpleLabeling(s))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()


class CropHistoryAlgorithm(QgsProcessingAlgorithm):
    FIELD = "FIELD"
    NAME_FIELD = "NAME_FIELD"
    START_YEAR = "START_YEAR"
    END_YEAR = "END_YEAR"
    AREA_UNITS = "AREA_UNITS"
    USE_CACHE = "USE_CACHE"
    FIELDS_OUT = "FIELDS_OUT"
    HISTORY = "HISTORY"
    ROTATION = "ROTATION"
    CDL_LATEST = "CDL_LATEST"

    NOT_SAVED = ("FIELDS_OUT", "HISTORY", "ROTATION", "CDL_LATEST")

    def tr(self, s):
        return QCoreApplication.translate("CropHistory", s)

    def createInstance(self):
        return CropHistoryAlgorithm()

    def name(self):
        return "crophistory"

    def displayName(self):
        return self.tr("Crop history (Cropland Data Layer)")

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
            if nm not in parameters:
                continue
            value = parameters[nm]
            if isinstance(value, QgsMapLayer):
                value = value.id()
            elif isinstance(value, QgsProcessingFeatureSourceDefinition):
                value = QgsProcessingFeatureSourceDefinition(value)
            _LAST_RUN[nm] = value

    def shortHelpString(self):
        return self.tr(
            "<p>Crop history of one or more fields from the USDA NASS Cropland Data Layer "
            "(CDL), downloaded year by year from the CropScape web service.</p>"
            "<p>For each field and year: the main crop and its share of the field, the "
            "second crop, and every crop over 1%. For each field: the sequence of main "
            "crops (e.g. <i>C S C S C S</i>), the rotation (corn-soybean, continuous corn, "
            "corn-soybean-small grain, row crops with hay, hay, other, mostly "
            "non-cropland), the most common crop, the years in corn, soybeans, small "
            "grain and hay, and flags where the field is split between crops or "
            "rotations or a year has no data.</p>"
            "<p>The <b>rotation map</b> shows the rotation of every CDL cell, to see "
            "fields farmed in parts. The <b>latest CDL</b> is loaded with the official "
            "colours.</p>"
            "<p>CDL covers the lower 48 states from 2008 (some states earlier). It is 30 m "
            "before 2024 and 10 m from 2024; years are resampled (nearest) to the finest "
            "grid. CDL is a satellite classification: field edges and small fields are "
            "less reliable. Downloads are cached, so a second run is fast.</p>"
            "<p>Sequence letters: " + LETTER_KEY + ".</p>")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.FIELD, self.tr("Field boundaries"), [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterField(
            self.NAME_FIELD, self.tr("Field name attribute [optional]"),
            parentLayerParameterName=self.FIELD, optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.START_YEAR, self.tr("First year"), QgsProcessingParameterNumber.Integer,
            defaultValue=2015, minValue=1997, maxValue=2100))
        self.addParameter(QgsProcessingParameterNumber(
            self.END_YEAR, self.tr("Last year (0 = latest available)"),
            QgsProcessingParameterNumber.Integer, defaultValue=0, minValue=0, maxValue=2100))
        self.addParameter(QgsProcessingParameterEnum(
            self.AREA_UNITS, self.tr("Area units"),
            options=[self.tr("Acres"), self.tr("Hectares")], defaultValue=0))
        self.addParameter(_advanced(QgsProcessingParameterBoolean(
            self.USE_CACHE, self.tr("Reuse downloaded CDL files"), defaultValue=True)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.FIELDS_OUT, self.tr("Field crop history"), QgsProcessing.TypeVectorPolygon))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.HISTORY, self.tr("Crops by year"), QgsProcessing.TypeVector,
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.ROTATION, self.tr("Rotation map"), optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.CDL_LATEST, self.tr("Latest Cropland Data Layer"), optional=True,
            createByDefault=True))
        self.addOutput(QgsProcessingOutputNumber("N_FIELDS", self.tr("Fields")))
        self.addOutput(QgsProcessingOutputString("YEARS", self.tr("Years used")))
        self.addOutput(QgsProcessingOutputString("WARNINGS", self.tr("Warnings")))

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _clusters(boxes):
        """Group field boxes so that each group fits in CLUSTER_SIZE square."""
        groups = []
        for i in sorted(range(len(boxes)), key=lambda k: (boxes[k][0], boxes[k][1])):
            b = boxes[i]
            for g in groups:
                u = (min(g["box"][0], b[0]), min(g["box"][1], b[1]),
                     max(g["box"][2], b[2]), max(g["box"][3], b[3]))
                if u[2] - u[0] <= CLUSTER_SIZE and u[3] - u[1] <= CLUSTER_SIZE:
                    g["box"] = u
                    g["fields"].append(i)
                    break
            else:
                groups.append({"box": tuple(b), "fields": [i]})
        for g in groups:                          # snap to the 30 m CDL grid
            x0, y0, x1, y1 = g["box"]
            g["box"] = (math.floor(x0 / 30.0) * 30.0, math.floor(y0 / 30.0) * 30.0,
                        math.ceil(x1 / 30.0) * 30.0, math.ceil(y1 / 30.0) * 30.0)
        return groups

    @staticmethod
    def _warp(path, box, res):
        from osgeo import gdal
        tmp = "/vsimem/crop_history_{}.tif".format(abs(hash((path, box, res))))
        ds = gdal.Warp(tmp, path, format="GTiff", outputBounds=box, xRes=res, yRes=res,
                       dstSRS=ALBERS, resampleAlg="near", outputType=gdal.GDT_Byte,
                       srcNodata=None, dstNodata=0)
        if ds is None:
            raise QgsProcessingException("Could not read " + path)
        arr = ds.GetRasterBand(1).ReadAsArray().astype(np.uint8)
        ds = None
        gdal.Unlink(tmp)
        return arr

    @staticmethod
    def _palette(path):
        from osgeo import gdal
        ds = gdal.Open(path)
        if ds is None:
            return {}
        ct = ds.GetRasterBand(1).GetColorTable()
        out = {}
        if ct is not None:
            for k in range(min(ct.GetCount(), 256)):
                c = ct.GetColorEntry(k)
                out[k] = (int(c[0]), int(c[1]), int(c[2]))
        return out

    @staticmethod
    def _rasterize(geom, box, res, nrows, ncols):
        from osgeo import gdal, ogr
        ds = gdal.GetDriverByName("MEM").Create("", ncols, nrows, 1, gdal.GDT_Byte)
        ds.SetGeoTransform((box[0], res, 0.0, box[3], 0.0, -res))
        drv = ogr.GetDriverByName("Memory") or ogr.GetDriverByName("MEM")
        src = drv.CreateDataSource("crop_history_field")
        lyr = src.CreateLayer("f", geom_type=ogr.wkbMultiPolygon)
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetGeometry(ogr.CreateGeometryFromWkb(bytes(geom.asWkb())))
        lyr.CreateFeature(f)
        gdal.RasterizeLayer(ds, [1], lyr, burn_values=[1])
        return ds.GetRasterBand(1).ReadAsArray().astype(bool)

    @staticmethod
    def _write_mosaic(dest, pieces, palette):
        """Write [(array uint8, box, res)] pieces into one GeoTIFF with a colour table."""
        from osgeo import gdal, osr
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(5070)
        tmpdir = tempfile.mkdtemp(prefix="crop_history_")
        files = []
        for k, (arr, box, res) in enumerate(pieces):
            p = os.path.join(tmpdir, "piece_{}.tif".format(k))
            ds = gdal.GetDriverByName("GTiff").Create(p, arr.shape[1], arr.shape[0], 1,
                                                      gdal.GDT_Byte, ["COMPRESS=DEFLATE"])
            ds.SetGeoTransform((box[0], res, 0.0, box[3], 0.0, -res))
            ds.SetProjection(srs.ExportToWkt())
            ds.GetRasterBand(1).WriteArray(arr)
            ds.GetRasterBand(1).SetNoDataValue(0)
            ds = None
            files.append(p)
        if len(files) == 1:
            src = files[0]
        else:
            src = os.path.join(tmpdir, "mosaic.vrt")
            gdal.BuildVRT(src, files, resolution="highest", srcNodata=0, VRTNodata=0)
        gdal.Translate(dest, src, format="GTiff", creationOptions=["COMPRESS=DEFLATE"],
                       noData=0)
        ds = gdal.Open(dest, gdal.GA_Update)
        if ds is not None and palette:
            ct = gdal.ColorTable()
            for v, rgb in palette.items():
                ct.SetColorEntry(int(v), (rgb[0], rgb[1], rgb[2], 255))
            ds.GetRasterBand(1).SetColorTable(ct)
        ds = None

    # ------------------------------------------------------------ run
    def processAlgorithm(self, parameters, context, feedback):
        self._save_settings(parameters)
        src = self.parameterAsSource(parameters, self.FIELD, context)
        if src is None:
            raise QgsProcessingException(self.tr("Could not load the field boundaries."))
        name_field = self.parameterAsString(parameters, self.NAME_FIELD, context)
        acres = self.parameterAsEnum(parameters, self.AREA_UNITS, context) == 0
        a_lbl = "ac" if acres else "ha"
        use_cache = self.parameterAsBool(parameters, self.USE_CACHE, context)
        warnings = []

        albers = QgsCoordinateReferenceSystem(ALBERS)
        to_alb = QgsCoordinateTransform(src.sourceCrs(), albers, context.transformContext())
        fields = []
        for f in src.getFeatures():
            g = f.geometry()
            if g.isNull() or g.isEmpty():
                continue
            ga = QgsGeometry(g)
            ga.transform(to_alb)
            nm = f[name_field] if name_field else None
            nm = str(nm) if nm is not None and str(nm) not in ("", "NULL") \
                else "Field {}".format(f.id())
            bb = ga.boundingBox()
            fields.append({"name": nm, "geom": QgsGeometry(g), "alb": ga,
                           "box": (bb.xMinimum() - BUFFER, bb.yMinimum() - BUFFER,
                                   bb.xMaximum() + BUFFER, bb.yMaximum() + BUFFER)})
        if not fields:
            raise QgsProcessingException(self.tr("The layer has no field polygons."))
        clusters = self._clusters([fd["box"] for fd in fields])
        feedback.pushInfo(self.tr("{} field(s) in {} download area(s).").format(
            len(fields), len(clusters)))

        # ---- years
        y0 = self.parameterAsInt(parameters, self.START_YEAR, context)
        y1 = self.parameterAsInt(parameters, self.END_YEAR, context)
        first = clusters[0]
        cache = {}                                      # (cluster, year) -> path
        if y1 == 0:
            this_year = datetime.date.today().year
            for y in range(this_year, this_year - 4, -1):
                try:
                    cache[(0, y)] = get_cdl(y, first["box"], feedback, use_cache)[0]
                    y1 = y
                    break
                except CdlUnavailable:
                    continue
                except CdlError as e:
                    raise QgsProcessingException(str(e))
            if y1 == 0:
                raise QgsProcessingException(self.tr(
                    "CropScape has no recent CDL for this area. Is it in the lower 48 "
                    "states? Set the last year to try a specific year."))
            feedback.pushInfo(self.tr("Latest CDL available: {}.").format(y1))
        if y0 > y1:
            raise QgsProcessingException(self.tr("The first year is after the last year."))
        if y0 < FIRST_YEAR:
            warnings.append("Before {} the CDL covers only some states.".format(FIRST_YEAR))
        years = list(range(y0, y1 + 1))

        # ---- download
        n_jobs = len(clusters) * len(years)
        done = 0
        missing = {}
        for ci, cl in enumerate(clusters):
            for y in years:
                if feedback.isCanceled():
                    return {}
                if (ci, y) not in cache:
                    try:
                        cache[(ci, y)], hit = get_cdl(y, cl["box"], feedback, use_cache)
                        if not hit:
                            feedback.pushInfo(self.tr("Downloaded the {} CDL ({}).").format(
                                y, ci + 1))
                    except CdlUnavailable:
                        missing.setdefault(y, []).append(ci)
                    except CdlError as e:
                        raise QgsProcessingException(str(e))
                done += 1
                feedback.setProgress(70.0 * done / n_jobs)
        for y, cis in sorted(missing.items()):
            warnings.append("No CDL for {} in {} of {} download area(s).".format(
                y, len(cis), len(clusters)))
        if not any(cache.get((ci, y)) for ci in range(len(clusters)) for y in years):
            raise QgsProcessingException(self.tr("No CDL could be downloaded for these "
                                                 "years and fields."))

        # ---- stacks and histories
        palette = {}
        cell_area = None
        results = [None] * len(fields)
        rot_pieces, cdl_pieces = [], []
        latest_used = None
        from osgeo import gdal
        for ci, cl in enumerate(clusters):
            paths = [cache.get((ci, y)) for y in years]
            have = [p for p in paths if p]
            if not have:
                continue
            res = min(abs(gdal.Open(p).GetGeoTransform()[1]) for p in have)
            box = cl["box"]
            ncols = int(round((box[2] - box[0]) / res))
            nrows = int(round((box[3] - box[1]) / res))
            box = (box[0], box[3] - nrows * res, box[0] + ncols * res, box[3])
            stack = np.zeros((len(years), nrows, ncols), dtype=np.uint8)
            for k, p in enumerate(paths):
                if p:
                    stack[k] = self._warp(p, box, res)
            last = max(k for k, p in enumerate(paths) if p)
            if not palette:
                palette = self._palette(paths[last])
            latest_used = max(latest_used or 0, years[last])
            ca = res * res / (4046.8564224 if acres else 1e4)
            cell_area = ca
            union = np.zeros((nrows, ncols), dtype=bool)
            for fi in cl["fields"]:
                m = self._rasterize(fields[fi]["alb"], box, res, nrows, ncols)
                if not m.any():
                    warnings.append("{} is smaller than one CDL cell.".format(
                        fields[fi]["name"]))
                union |= m
                results[fi] = field_history(stack, m, years, ca)
                results[fi]["exact_area"] = fields[fi]["alb"].area() / (
                    4046.8564224 if acres else 1e4)
            rot = cell_rotation(stack)
            rot[~union] = 0
            rot_pieces.append((rot, box, res))
            cdl_pieces.append((stack[last], box, res))
            feedback.setProgress(70.0 + 20.0 * (ci + 1) / len(clusters))

        lost = [fd["name"] for fd, r in zip(fields, results) if r is None]
        if lost:
            warnings.append("No CDL at all for: {}.".format(", ".join(lost)))

        # ---- field layer
        ff = QgsFields()
        for nm, typ in (("field", QVariant.String), ("area", QVariant.Double),
                        ("years", QVariant.Int), ("rotation", QVariant.String),
                        ("sequence", QVariant.String), ("main_crop", QVariant.String),
                        ("corn_yrs", QVariant.Int), ("soy_yrs", QVariant.Int),
                        ("grain_yrs", QVariant.Int), ("hay_yrs", QVariant.Int),
                        ("other_yrs", QVariant.Int), ("noncrop_yrs", QVariant.Int),
                        ("crop_pct", QVariant.Double), ("uniform_pct", QVariant.Double),
                        ("rot_pct", QVariant.Double), ("flag", QVariant.String)):
            ff.append(QgsField(nm, typ))
        for y in years:
            ff.append(QgsField("y{}".format(y), QVariant.String))
        sink, fid = self.parameterAsSink(parameters, self.FIELDS_OUT, context, ff,
                                         QgsWkbTypes.multiType(src.wkbType()), src.sourceCrs())
        if sink is None:
            raise QgsProcessingException(self.tr("Could not create the field layer."))
        rot_used = set()

        def r1(v):
            return None if v is None else round(float(v), 1)
        for fd, res_ in zip(fields, results):
            if res_ is None:
                continue
            s = res_["summary"]
            rot_name = ROTATIONS[s["rotation"]][0] if s["rotation"] else "No data"
            rot_used.add(s["rotation"])
            attrs = [fd["name"], round(res_["exact_area"], 2), s["years"], rot_name,
                     s["sequence"], s["main_crop"], s["corn"], s["soy"], s["small_grain"],
                     s["hay"], s["other"], s["noncrop"], r1(s["crop_pct"]),
                     r1(s["uniform_pct"]), r1(s["rotation_pct"]), s["flag"] or None]
            for row in res_["rows"]:
                attrs.append("{} ({:.0f}%)".format(row["crop"], row["pct"])
                             if row["crop"] else None)
            f = QgsFeature(ff)
            g = QgsGeometry(fd["geom"])
            g.convertToMultiType()
            f.setGeometry(g)
            f.setAttributes(attrs)
            sink.addFeature(f, QgsFeatureSink.FastInsert)
        out = {self.FIELDS_OUT: fid}
        if context.willLoadLayerOnCompletion(fid):
            _POST[fid] = _FieldStyler(sorted(rot_used))
            context.layerToLoadOnCompletionDetails(fid).setPostProcessor(_POST[fid])

        # ---- crops by year
        hf = QgsFields()
        for nm, typ in (("field", QVariant.String), ("year", QVariant.Int),
                        ("crop", QVariant.String), ("crop_pct", QVariant.Double),
                        ("crop_area", QVariant.Double), ("second", QVariant.String),
                        ("second_pct", QVariant.Double), ("crops", QVariant.String),
                        ("data_pct", QVariant.Double)):
            hf.append(QgsField(nm, typ))
        hsink, hid = self.parameterAsSink(parameters, self.HISTORY, context, hf,
                                          QgsWkbTypes.NoGeometry, src.sourceCrs())
        if hsink is not None:
            for fd, res_ in zip(fields, results):
                if res_ is None:
                    continue
                for row in res_["rows"]:
                    f = QgsFeature(hf)
                    f.setAttributes([fd["name"], row["year"], row["crop"], r1(row["pct"]),
                                     None if row["area"] is None else round(row["area"], 2),
                                     row["second"], r1(row["second_pct"]),
                                     row["crops"] or None, r1(row["valid_pct"])])
                    hsink.addFeature(f, QgsFeatureSink.FastInsert)
            out[self.HISTORY] = hid

        # ---- rasters
        rpath = self.parameterAsOutputLayer(parameters, self.ROTATION, context)
        if rpath and rot_pieces:
            pal = {k: v[1] for k, v in ROTATIONS.items()}
            self._write_mosaic(rpath, rot_pieces, pal)
            out[self.ROTATION] = rpath
            present = sorted({int(v) for a, _, _ in rot_pieces for v in np.unique(a) if v})
            if context.willLoadLayerOnCompletion(rpath):
                _POST[rpath] = _PalettedStyler(
                    [(v, ROTATIONS[v][1], ROTATIONS[v][0]) for v in present], 0.85)
                context.layerToLoadOnCompletionDetails(rpath).setPostProcessor(_POST[rpath])
        cpath = self.parameterAsOutputLayer(parameters, self.CDL_LATEST, context)
        if cpath and cdl_pieces:
            present = sorted({int(v) for a, _, _ in cdl_pieces for v in np.unique(a) if v})
            pal = {v: palette.get(v, color(v)) for v in present}
            self._write_mosaic(cpath, cdl_pieces, pal)
            out[self.CDL_LATEST] = cpath
            if context.willLoadLayerOnCompletion(cpath):
                context.layerToLoadOnCompletionDetails(cpath).name = \
                    "Cropland Data Layer {}".format(latest_used)
                _POST[cpath] = _PalettedStyler([(v, pal[v], name(v)) for v in present])
                context.layerToLoadOnCompletionDetails(cpath).setPostProcessor(_POST[cpath])

        # ---- log
        feedback.pushInfo("")
        feedback.pushInfo(self.tr("Years {}-{}. Sequence letters: {}.").format(
            years[0], years[-1], LETTER_KEY))
        for fd, res_ in zip(fields, results):
            if res_ is None:
                continue
            s = res_["summary"]
            feedback.pushInfo("{}: {:.1f} {}, {} | {}{}".format(
                fd["name"], res_["exact_area"], a_lbl,
                ROTATIONS[s["rotation"]][0] if s["rotation"] else "no data", s["sequence"],
                " | " + s["flag"] if s["flag"] else ""))
        feedback.pushInfo(self.tr("Source: USDA NASS Cropland Data Layer, via CropScape."))
        for w in warnings:
            feedback.pushWarning(w)
        feedback.setProgress(100)
        out.update({"N_FIELDS": sum(1 for r in results if r is not None),
                    "YEARS": "{}-{}".format(years[0], years[-1]),
                    "WARNINGS": "\n".join(warnings)})
        return out
