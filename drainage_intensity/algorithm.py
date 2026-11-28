"""Drainage Intensity: a QGIS Processing algorithm.

Maps how fast a designed tile network can drain each part of a field, by the
Hooghoudt equation (water table between the drains) or Kirkham's equation
(ponded surface), from the network's own drain depths and local spacings and
the soil's conductivity and depth to a barrier.
"""
import math
import os

import numpy as np
from qgis.core import (
    QgsColorRampShader,
    QgsCoordinateTransform,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingOutputNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsRasterFileWriter,
    QgsRasterLayer,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
    QgsUnitTypes,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor, QIcon

from . import di_core as C

FT, IN = 0.3048, 0.0254
NODATA = -9999.0
UNIT_OPTIONS = ["US (ft, in/day)", "Metric (m, mm/day)"]
METHODS = ["Hooghoudt (water table between the drains)", "Kirkham (ponded surface)"]
DRAINS = ["All tile lines", "Laterals only"]
SOIL_UNITS = ["Auto (from the field names)", "ft/day and ft", "m/day and m",
              "in/hr and in", "um/s and cm"]
# Ksat factor to m/day and depth factor to m, by SOIL_UNITS index
SOIL_FACTORS = {1: (FT, FT), 2: (1.0, 1.0), 3: (IN * 24.0, IN), 4: (0.0864, 0.01)}
KA_NAMES = ("ka_ftday", "ka_mday")
KB_NAMES = ("kb_ftday", "kb_mday")
BARRIER_NAMES = ("barrier_ft", "barrier_m")
PROFILE_NAMES = ("ksat_prof",)

# classes of band 1, as fractions of the target
CLASSES = [(0.5, "#b2182b", "Less than half the target"),
           (1.0, "#ef8a62", "Below the target"),
           (1.5, "#67a9cf", "Meets the target"),
           (None, "#2166ac", "Half again the target or more")]

_POST = {}


def _meters_factor(crs):
    try:
        from qgis.core import Qgis
        return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), Qgis.DistanceUnit.Meters)
    except (ImportError, AttributeError):
        return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), QgsUnitTypes.DistanceMeters)


def _intensity_renderer(provider, target, unit_label):
    items = []
    for frac, colour, label in CLASSES:
        if frac is None:
            value = 1e9
            text = "{} (≥ {:.3g} {})".format(label, 1.5 * target, unit_label)
        else:
            value = frac * target
            lo = 0.0 if frac == 0.5 else (frac - 0.5) * target
            text = "{} ({:.3g}–{:.3g} {})".format(label, lo, value, unit_label)
        items.append(QgsColorRampShader.ColorRampItem(value, QColor(colour), text))
    fn = QgsColorRampShader(0.0, 2.0 * target)
    try:
        from qgis.core import Qgis
        fn.setColorRampType(Qgis.ShaderInterpolationMethod.Discrete)
    except (ImportError, AttributeError):
        fn.setColorRampType(QgsColorRampShader.Discrete)
    fn.setColorRampItemList(items)
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(fn)
    r = QgsSingleBandPseudoColorRenderer(provider, 1, shader)
    r.setClassificationMin(0.0)
    r.setClassificationMax(2.0 * target)
    return r


class _Styler(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, target, unit_label, name):
        super().__init__()
        self.target, self.unit_label, self.name = target, unit_label, name

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsRasterLayer):
            return
        layer.setName(self.name)
        layer.setRenderer(_intensity_renderer(layer.dataProvider(), self.target,
                                              self.unit_label))
        layer.triggerRepaint()


class DrainageIntensityAlgorithm(QgsProcessingAlgorithm):
    NETWORK = "NETWORK"
    DRAINS = "DRAINS"
    FIELD = "FIELD"
    UNITS = "UNITS"
    METHOD = "METHOD"
    SOIL = "SOIL"
    KA_FIELD = "KA_FIELD"
    KB_FIELD = "KB_FIELD"
    BARRIER_FIELD = "BARRIER_FIELD"
    PROFILE_FIELD = "PROFILE_FIELD"
    SOIL_UNITS = "SOIL_UNITS"
    USE_PROFILE = "USE_PROFILE"
    KA = "KA"
    KB = "KB"
    BARRIER = "BARRIER"
    WT_DEPTH = "WT_DEPTH"
    PONDED = "PONDED"
    RADIUS = "RADIUS"
    DEPTH = "DEPTH"
    TARGET = "TARGET"
    CELL = "CELL"
    REACH = "REACH"
    OUTPUT = "OUTPUT"

    def tr(self, s):
        return QCoreApplication.translate("DrainageIntensity", s)

    def createInstance(self):
        return DrainageIntensityAlgorithm()

    def name(self):
        return "drainageintensity"

    def displayName(self):
        return self.tr("Drainage intensity map")

    def group(self):
        return ""

    def groupId(self):
        return ""

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def shortHelpString(self):
        return self.tr(
            "<p>Maps the drainage intensity of a tile network: the rate (in/day or mm/day) at "
            "which the drains can remove water from each part of the field, from each "
            "drain's depth and the local spacing between drains, and the soil.</p>"
            "<p><b>Hooghoudt</b> (water table between the drains): q = (8 Kb de m + 4 Ka m²) / "
            "L², with m the height of the water table midway between the drains above the "
            "drains (the drain depth less the midpoint water-table depth, 0 = at the "
            "surface), de the equivalent depth (van der Molen and Wesseling) of the barrier "
            "below the drains, Ka the conductivity above the drains and Kb below them.</p>"
            "<p><b>Kirkham</b> (ponded surface, as in DRAINMOD): q = 4πK(t + b − r) / (gL), "
            "with t the ponded depth, b the drain depth, r the drain's effective radius, K "
            "the profile's conductivity and g Kirkham's geometry term for the depth to the "
            "barrier.</p>"
            "<p><b>Local spacing</b>: for each cell, the distance from its nearest drain out "
            "to the drainage divide (where another drain becomes nearer), doubled. Where "
            "there is no drain beyond, the field edge is the divide. So the map shows the "
            "strips between laterals, the wedges near junctions, and the poorly drained "
            "ground past the lateral ends or along the edges.</p>"
            "<p><b>Network</b>: a Drainage Network from Illini Subsurface Drain Layout, "
            "Illini Subirrigation Layout or Illini Drainage System Design. Each drain's "
            "depth is its ground (M) less its tile (Z) elevation; lines without Z and M "
            "take the default drain depth.</p>"
            "<p><b>Soil</b> (optional): a polygon layer with Ka, Kb and the barrier depth, "
            "such as the SSURGO or SoilGrids drainage soil maps (fields ka_ftday, kb_ftday, "
            "barrier_ft or ka_mday, kb_mday, barrier_m are found by themselves). With their "
            "layer profile (ksat_prof), Ka and Kb are worked out for each drain's own "
            "depth. Elsewhere the uniform values below are used.</p>"
            "<p><b>Output</b>: a raster with four bands: 1 drainage intensity, 2 local "
            "spacing, 3 drain depth, 4 class (1 meets the target, 2 below it, 3 no drain "
            "within reach). It is drawn in four classes against the target, the design "
            "drainage coefficient. The log gives the field's mean and median intensity and "
            "the share of it below the target.</p>")

    def _num(self, name, label, default, lo=0.0, hi=1e6, advanced=False, optional=False):
        p = QgsProcessingParameterNumber(name, label, QgsProcessingParameterNumber.Double,
                                         defaultValue=default, minValue=lo, maxValue=hi,
                                         optional=optional)
        if advanced:
            p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(p)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.NETWORK, self.tr("Drainage network (tile lines)"),
            [QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterEnum(
            self.DRAINS, self.tr("Lines that drain"), options=DRAINS, defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.FIELD, self.tr("Field boundary [optional; the edge is a drainage divide]"),
            [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.UNITS, self.tr("Units"), options=UNIT_OPTIONS, defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.METHOD, self.tr("Equation"), options=METHODS, defaultValue=0))
        self._num(self.TARGET, self.tr("Target drainage coefficient (in/day or mm/day)"),
                  0.375, 0.001, 1000.0)
        self._num(self.WT_DEPTH, self.tr("Hooghoudt: water table midway between the drains, "
                                         "depth below the surface (ft or m; 0 = at the "
                                         "surface)"), 0.0, 0.0, 50.0)
        self._num(self.PONDED, self.tr("Kirkham: ponded depth (in or cm)"), 0.0, 0.0, 100.0)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.SOIL, self.tr("Soil map [optional; e.g. SSURGO or SoilGrids drainage soil "
                               "map]"), [QgsProcessing.TypeVectorPolygon], optional=True))
        for name, label in ((self.KA_FIELD, "Soil field: Ka, conductivity above the drains"),
                            (self.KB_FIELD, "Soil field: Kb, conductivity below the drains"),
                            (self.BARRIER_FIELD, "Soil field: depth to the barrier"),
                            (self.PROFILE_FIELD, "Soil field: layer profile (ksat_prof)")):
            p = QgsProcessingParameterField(name, self.tr(label + " [blank = find it]"),
                                            parentLayerParameterName=self.SOIL, optional=True)
            p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
            self.addParameter(p)
        p = QgsProcessingParameterEnum(self.SOIL_UNITS, self.tr("Soil field units"),
                                       options=SOIL_UNITS, defaultValue=0)
        p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(p)
        p = QgsProcessingParameterBoolean(
            self.USE_PROFILE, self.tr("Work out Ka and Kb at each drain's own depth from the "
                                      "layer profile, where there is one"), defaultValue=True)
        p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(p)
        self._num(self.KA, self.tr("Ka where there is no soil map (ft/day or m/day)"), 1.0,
                  0.0, 1000.0)
        self._num(self.KB, self.tr("Kb where there is no soil map (ft/day or m/day)"), 0.5,
                  0.0, 1000.0)
        self._num(self.BARRIER, self.tr("Depth to the barrier where there is no soil map "
                                        "(ft or m)"), 6.0, 0.0, 1000.0)
        self._num(self.DEPTH, self.tr("Drain depth for lines without Z and M (ft or m)"), 3.5,
                  0.1, 50.0, advanced=True)
        self._num(self.RADIUS, self.tr("Drain effective radius (in or cm)"), 0.2, 0.001,
                  100.0, advanced=True)
        self._num(self.CELL, self.tr("Cell size (ft or m; 0 = automatic)"), 0.0, 0.0, 1000.0,
                  advanced=True)
        self._num(self.REACH, self.tr("Farthest distance from a drain considered drained "
                                      "(ft or m)"), 150.0, 1.0, 10000.0, advanced=True)
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Drainage intensity")))
        self.addOutput(QgsProcessingOutputNumber("MEAN", self.tr("Mean drainage intensity")))
        self.addOutput(QgsProcessingOutputNumber("MEDIAN", self.tr("Median drainage intensity")))
        self.addOutput(QgsProcessingOutputNumber("BELOW_PCT",
                                                 self.tr("Share of the field below the target")))

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _rings(geom, xf):
        rings = []
        if geom is None or geom.isEmpty():
            return rings
        polys = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
        for poly in polys:
            for ring in poly:
                if len(ring) >= 3:
                    rings.append(np.array([[p.x() * xf, p.y() * xf] for p in ring]))
        return rings

    @staticmethod
    def _pick_field(names, chosen, candidates):
        if chosen:
            return chosen if chosen in names else None
        low = {n.lower(): n for n in names}
        for c in candidates:
            if c in low:
                return low[c]
        return None

    def _read_lines(self, source, xf, depth_default, laterals_only, feedback):
        names = [f.name() for f in source.fields()]
        kind = "kind" if "kind" in names else None
        lines, flat, skipped = [], 0, 0
        for f in source.getFeatures():
            if laterals_only and kind is not None:
                if str(f[kind]).lower() != "lateral":
                    skipped += 1
                    continue
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            zm = QgsWkbTypes.hasZ(g.wkbType()) and QgsWkbTypes.hasM(g.wkbType())
            for part in g.constParts():
                pts = []
                for v in part.vertices():
                    if zm and math.isfinite(v.z()) and math.isfinite(v.m()):
                        d = (v.m() - v.z()) * xf
                    else:
                        d = depth_default
                    pts.append((v.x() * xf, v.y() * xf, d))
                if not zm:
                    flat += 1
                if len(pts) >= 2:
                    lines.append(np.array(pts, float))
        if laterals_only and kind is None:
            feedback.pushInfo(self.tr("The network has no kind field: every line is taken "
                                      "as a drain."))
        return lines, flat, skipped

    # ----------------------------------------------------------------- run
    def processAlgorithm(self, parameters, context, feedback):
        out_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        net = self.parameterAsSource(parameters, self.NETWORK, context)
        if net is None:
            raise QgsProcessingException(self.tr("Could not load the drainage network."))
        crs = net.sourceCrs()
        if not crs.isValid() or crs.isGeographic():
            raise QgsProcessingException(self.tr(
                "The network must be in a projected coordinate system (metres or feet)."))
        xf = _meters_factor(crs)
        us = self.parameterAsEnum(parameters, self.UNITS, context) == 0
        lf = FT if us else 1.0                      # design length -> m
        rate_f = IN if us else 0.001                # rate (in/day or mm/day) -> m/day
        small_f = IN if us else 0.01                # radius, ponding (in or cm) -> m
        u_len, u_rate = ("ft", "in/day") if us else ("m", "mm/day")
        method = "kirkham" if self.parameterAsEnum(parameters, self.METHOD, context) == 1 \
            else "hooghoudt"
        target = self.parameterAsDouble(parameters, self.TARGET, context)
        wt = self.parameterAsDouble(parameters, self.WT_DEPTH, context) * lf
        ponded = self.parameterAsDouble(parameters, self.PONDED, context) * small_f
        r = self.parameterAsDouble(parameters, self.RADIUS, context) * small_f
        depth_default = self.parameterAsDouble(parameters, self.DEPTH, context) * lf
        reach = self.parameterAsDouble(parameters, self.REACH, context) * lf
        laterals_only = self.parameterAsEnum(parameters, self.DRAINS, context) == 1

        lines, flat, skipped = self._read_lines(net, xf, depth_default, laterals_only,
                                                feedback)
        if not lines:
            raise QgsProcessingException(self.tr("The network has no drain lines."))
        segs = C.drain_segments(lines)
        if flat:
            feedback.pushInfo(self.tr("{} line(s) have no Z and M values: they are taken at "
                                      "the default drain depth ({:g} {}).").format(
                flat, round(depth_default / lf, 2), u_len))
        if skipped:
            feedback.pushInfo(self.tr("{} main and submain line(s) left out (laterals "
                                      "only).").format(skipped))

        # field
        rings = []
        if parameters.get(self.FIELD) not in (None, ""):
            fsrc = self.parameterAsSource(parameters, self.FIELD, context)
            if fsrc is not None:
                ftr = QgsCoordinateTransform(fsrc.sourceCrs(), crs, context.transformContext())
                for f in fsrc.getFeatures():
                    g = f.geometry()
                    if g is None or g.isEmpty():
                        continue
                    g.transform(ftr)
                    rings.extend(self._rings(g, xf))
        if rings:
            allp = np.vstack(rings)
            x0, y0 = allp.min(axis=0)
            x1, y1 = allp.max(axis=0)
        else:
            allp = np.vstack([s[:, :2] for s in lines])
            x0, y0 = allp.min(axis=0) - reach
            x1, y1 = allp.max(axis=0) + reach
        cell = self.parameterAsDouble(parameters, self.CELL, context) * lf
        if cell <= 0:
            cell = max(1.0, math.sqrt((x1 - x0) * (y1 - y0) / 1.5e6))
        ncols = int(math.ceil((x1 - x0) / cell))
        nrows = int(math.ceil((y1 - y0) / cell))
        if nrows * ncols > 2.5e7:
            raise QgsProcessingException(self.tr(
                "The grid would have {:,} cells; use a larger cell size.").format(nrows * ncols))
        grid = C.Grid(x0, y0 + nrows * cell, cell, nrows, ncols)
        mask = C.rasterize_rings(grid, rings) if rings else np.ones(grid.shape, dtype=bool)
        if not mask.any():
            raise QgsProcessingException(self.tr("The field boundary covers no cells."))
        feedback.pushInfo(self.tr("Grid: {:,} x {:,} cells of {:.2f} {}.").format(
            ncols, nrows, cell / lf, u_len))

        soil = self._soil(parameters, context, feedback, grid, crs, xf, lf, us)
        feedback.setProgress(10)
        res = C.drainage_intensity(
            grid, mask, segs, soil, method, reach, r, wt=wt, ponded=ponded,
            progress=lambda f: feedback.setProgress(10 + 80 * f))
        if feedback.isCanceled():
            return {}
        s = C.summary(res, mask, target * rate_f, cell)
        self._write(out_path, res, mask, grid, xf, rate_f, lf, target, crs)
        area = s["area_m2"] / (4046.8564224 if us else 1e4)
        feedback.pushInfo(self.tr(
            "{}: field {:.2f} {}; drainage intensity mean {:.3f}, median {:.3f}, lowest tenth "
            "below {:.3f} {}; {:.1f}% of the field below the target ({:g} {}); {:.1f}% with "
            "no drain within {:g} {}. Median local spacing {:.1f} {}.").format(
            "Kirkham" if method == "kirkham" else "Hooghoudt",
            area, "ac" if us else "ha", s["mean"] / rate_f, s["median"] / rate_f,
            s["p10"] / rate_f, u_rate, s["below_pct"], target, u_rate, s["beyond_pct"],
            round(reach / lf, 1), u_len, s["spacing_median"] / lf, u_len))
        if context.willLoadLayerOnCompletion(out_path):
            _POST[out_path] = _Styler(target, u_rate, self.tr("Drainage intensity ({})").format(
                "Kirkham" if method == "kirkham" else "Hooghoudt"))
            context.layerToLoadOnCompletionDetails(out_path).setPostProcessor(_POST[out_path])
        return {self.OUTPUT: out_path, "MEAN": s["mean"] / rate_f,
                "MEDIAN": s["median"] / rate_f, "BELOW_PCT": s["below_pct"]}

    def _soil(self, parameters, context, feedback, grid, crs, xf, lf, us):
        kf = FT if us else 1.0
        soil = {"ka": np.full(grid.shape, self.parameterAsDouble(parameters, self.KA, context) * kf),
                "kb": np.full(grid.shape, self.parameterAsDouble(parameters, self.KB, context) * kf),
                "barrier": np.full(grid.shape,
                                   self.parameterAsDouble(parameters, self.BARRIER, context) * lf)}
        if parameters.get(self.SOIL) in (None, ""):
            return soil
        src = self.parameterAsSource(parameters, self.SOIL, context)
        if src is None:
            return soil
        names = [f.name() for f in src.fields()]
        ka_f = self._pick_field(names, self.parameterAsString(parameters, self.KA_FIELD, context),
                                KA_NAMES)
        kb_f = self._pick_field(names, self.parameterAsString(parameters, self.KB_FIELD, context),
                                KB_NAMES)
        bar_f = self._pick_field(names, self.parameterAsString(parameters, self.BARRIER_FIELD,
                                                               context), BARRIER_NAMES)
        prof_f = self._pick_field(names, self.parameterAsString(parameters, self.PROFILE_FIELD,
                                                                context), PROFILE_NAMES)
        if not self.parameterAsBool(parameters, self.USE_PROFILE, context):
            prof_f = None
        if not (ka_f or kb_f or bar_f or prof_f):
            feedback.reportError(self.tr("The soil map has none of the fields Ka, Kb, barrier "
                                         "depth or ksat_prof: the uniform soil values are "
                                         "used."))
            return soil
        su = self.parameterAsEnum(parameters, self.SOIL_UNITS, context)
        if su == 0:
            ref = (ka_f or kb_f or bar_f or "").lower()
            su = 1 if ref.endswith("_ftday") or ref.endswith("_ft") else 2
            if not ref:
                su = 1 if us else 2
        kfac, dfac = SOIL_FACTORS[su]
        feedback.pushInfo(self.tr("Soil map: Ka {}, Kb {}, barrier {}, profile {} ({}).").format(
            ka_f or "-", kb_f or "-", bar_f or "-", prof_f or "-", SOIL_UNITS[su]))
        tr = QgsCoordinateTransform(src.sourceCrs(), crs, context.transformContext())
        pid = np.full(grid.shape, -1, dtype=int) if prof_f else None
        profiles = []
        covered = np.zeros(grid.shape, dtype=bool)

        def val(f, name):
            if not name:
                return None
            v = f[name]
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return v if math.isfinite(v) else None

        for f in src.getFeatures():
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            g.transform(tr)
            m = C.rasterize_rings(grid, self._rings(g, xf))
            if not m.any():
                continue
            covered |= m
            for key, name, fac in (("ka", ka_f, kfac), ("kb", kb_f, kfac),
                                   ("barrier", bar_f, dfac)):
                v = val(f, name)
                if v is not None:
                    soil[key][m] = v * fac
            if prof_f:
                lay = C.parse_ksat_profile(f[prof_f])
                if lay:
                    pid[m] = len(profiles)
                    profiles.append(lay)
        if pid is not None and profiles:
            soil["profile_id"] = pid
            soil["profiles"] = profiles
        feedback.pushInfo(self.tr("The soil map covers {:.0f}% of the grid.").format(
            100.0 * covered.mean()))
        return soil

    def _write(self, path, res, mask, grid, xf, rate_f, lf, target, crs):
        from osgeo import gdal, osr
        q = res["q"]
        band1 = np.where(mask & np.isfinite(q), q / rate_f, NODATA)
        band2 = np.where(mask & np.isfinite(res["spacing"]), res["spacing"] / lf, NODATA)
        band3 = np.where(mask & np.isfinite(res["depth"]), res["depth"] / lf, NODATA)
        code = np.zeros(grid.shape)
        code[mask & np.isfinite(q)] = 2
        code[mask & np.isfinite(q) & (q >= target * rate_f)] = 1
        code[mask & (res["beyond"] | ~np.isfinite(q))] = 3
        mem = gdal.GetDriverByName("MEM").Create("", grid.ncols, grid.nrows, 4, gdal.GDT_Float32)
        mem.SetGeoTransform((grid.x0 / xf, grid.cell / xf, 0.0, grid.y0 / xf, 0.0,
                             -grid.cell / xf))
        srs = osr.SpatialReference()
        srs.ImportFromWkt(crs.toWkt())
        mem.SetProjection(srs.ExportToWkt())
        for b, (arr, desc, nd) in enumerate((
                (band1, "Drainage intensity", NODATA),
                (band2, "Local drain spacing", NODATA),
                (band3, "Drain depth", NODATA),
                (code, "Class: 1 meets the target, 2 below it, 3 no drain within reach", 0.0)),
                start=1):
            band = mem.GetRasterBand(b)
            band.WriteArray(arr.astype(np.float32))
            band.SetNoDataValue(nd)
            band.SetDescription(desc)
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        driver = None
        if ext and ext not in ("tif", "tiff"):
            try:
                driver = QgsRasterFileWriter.driverForExtension(ext)
            except Exception:  # noqa: BLE001
                driver = None
        drv = gdal.GetDriverByName(driver or "GTiff")
        opts = ["COMPRESS=DEFLATE"] if (driver or "GTiff") == "GTiff" else []
        out = drv.CreateCopy(path, mem, 0, opts)
        if out is None:
            raise QgsProcessingException(self.tr("Could not write the raster to {}.").format(path))
        out.FlushCache()
        out = mem = None
