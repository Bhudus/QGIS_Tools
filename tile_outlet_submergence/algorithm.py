"""Processing algorithm: how a rising ditch water level reduces flow from a tile system."""
import datetime
import math
import os
import uuid

import numpy as np
from qgis.core import (
    QgsColorRampShader,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsGraduatedSymbolRenderer,
    QgsLineString,
    QgsLineSymbol,
    QgsMapLayer,
    QgsPoint,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingFeatureSourceDefinition,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingOutputNumber,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProject,
    QgsRasterLayer,
    QgsRasterShader,
    QgsRendererRange,
    QgsSingleBandPseudoColorRenderer,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QColor, QIcon

from .core import (BackwaterModel, Grid, ModelParams, NetworkError, build_network, fill_tile_z,
                   report_html)
from .core.model import main_profile
from .core.explorer import (NODATA, PROP_KEY, LevelStore, panel_meta, q_field, report_payload,
                            rise_field)
from .core.report import rating_csv

PLUGIN_DIR = os.path.dirname(__file__)
PLUGIN_NAME = "Tile Outlet Submergence 0.1.0"
UNIT_FACTORS = [1.0, 0.3048]                         # design units -> m
Z_FACTORS = [1.0, 0.3048, 1200.0 / 3937.0]           # DEM units -> m
MAX_CELLS = 3_000_000
_POST = {}
# called with a run id once an output layer of that run is loaded and tagged (the panel)
RUN_LISTENERS = []


def _tag(layer, meta_json):
    """Mark an output layer with its run, for the ditch-level explorer panel."""
    if not meta_json:
        return
    layer.setCustomProperty(PROP_KEY, meta_json)
    run = meta_json.split('"run":"', 1)[-1].split('"', 1)[0]
    for cb in list(RUN_LISTENERS):
        try:
            cb(run)
        except Exception:  # noqa: BLE001 - a listener must never break the run
            pass

# Settings of the last run, kept while the project stays open; cleared by the
# plugin when a new project is started or another one is loaded.
_LAST_RUN = {}
LAYER_INPUTS = ("NETWORK", "DEM", "FIELD")


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


def _meters_factor(crs):
    try:
        from qgis.core import Qgis
        return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), Qgis.DistanceUnit.Meters)
    except (ImportError, AttributeError):
        return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), QgsUnitTypes.DistanceMeters)


def _advanced(param):
    try:
        from qgis.core import Qgis
        param.setFlags(param.flags() | Qgis.ProcessingParameterFlag.Advanced)
    except (ImportError, AttributeError):
        param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
    return param


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


class _RatioStyler(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, band=1, meta_json=None):
        super().__init__()
        self.band = band
        self.meta_json = meta_json

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsRasterLayer):
            return
        sh = QgsColorRampShader(0, 100)
        sh.setColorRampType(QgsColorRampShader.Interpolated)
        items = [(0, "#b91c1c", "0% (no drainage)"), (50, "#f59e0b", "50%"),
                 (90, "#fde68a", "90%"), (100, "#16a34a", "100% (as free outlet)")]
        sh.setColorRampItemList([QgsColorRampShader.ColorRampItem(v, QColor(c), lbl)
                                 for v, c, lbl in items])
        rs = QgsRasterShader()
        rs.setRasterShaderFunction(sh)
        r = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), self.band, rs)
        r.setClassificationMin(0)
        r.setClassificationMax(100)
        r.setOpacity(0.8)
        layer.setRenderer(r)
        layer.triggerRepaint()
        _tag(layer, self.meta_json)


class _PipeStyler(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, unit, field="hgl_rise", meta_json=None):
        super().__init__()
        self.unit = unit
        self.field = field
        self.meta_json = meta_json

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsVectorLayer):
            return
        ranges = []
        for lo, hi, col, lbl in ((-1e9, 0.0001, "#16a34a", "no rise"),
                                 (0.0001, 0.25, "#a3e635", "rise up to 0.25"),
                                 (0.25, 1.0, "#f59e0b", "0.25 to 1"),
                                 (1.0, 1e9, "#b91c1c", "more than 1")):
            sym = QgsLineSymbol.createSimple({"color": col, "width": "0.6"})
            ranges.append(QgsRendererRange(lo, hi, sym, "HGL {} {}".format(lbl, self.unit)
                                           if lo > -1 else "HGL not raised"))
        layer.setRenderer(QgsGraduatedSymbolRenderer(self.field, ranges))
        layer.triggerRepaint()
        _tag(layer, self.meta_json)


class TileOutletSubmergenceAlgorithm(QgsProcessingAlgorithm):
    NETWORK = "NETWORK"
    DEM = "DEM"
    FIELD = "FIELD"
    OUTLET = "OUTLET"
    UNITS = "UNITS"
    DC = "DC"
    SPACING = "SPACING"
    WT_DEPTH = "WT_DEPTH"
    IMP_DEPTH = "IMP_DEPTH"
    PIPE_TYPE = "PIPE_TYPE"
    LEVEL_MAX = "LEVEL_MAX"
    LEVEL_STEP = "LEVEL_STEP"
    MAP_LEVEL = "MAP_LEVEL"
    Z_UNITS = "Z_UNITS"
    STEP = "STEP"
    RATING = "RATING"
    RATING_CSV = "RATING_CSV"
    PIPES = "PIPES"
    REDUCTION = "REDUCTION"
    REPORT = "REPORT"

    NOT_SAVED = ("RATING", "RATING_CSV", "PIPES", "REDUCTION", "REPORT")

    def tr(self, s):
        return QCoreApplication.translate("TileOutletSubmergence", s)

    def createInstance(self):
        return TileOutletSubmergenceAlgorithm()

    def name(self):
        return "tileoutletsubmergence"

    def displayName(self):
        return self.tr("Tile outlet submergence")

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
            "<p>How much a rising water level in the outlet ditch reduces the flow from a "
            "tile drainage system, for a network from Illini Subsurface Drain Layout (or any "
            "network with tile elevations and pipe sizes) and the DEM.</p>"
            "<p>For each ditch level, the hydraulic grade line (HGL) is stepped up the "
            "network from the outlet with Manning's equation: it never falls below normal "
            "depth, and once a pipe runs full the full-pipe friction loss applies. Where the "
            "HGL in a drain stands above its invert, the head that drives water from the soil "
            "into the drain is smaller, and Hooghoudt's steady-state equation gives the "
            "reduced drainage rate. Less inflow means less friction loss, so the two are "
            "iterated to a steady state. Every cell drains to its nearest pipe at the "
            "drainage coefficient when the outlet is free.</p>"
            "<p><b>Rating curve</b>: system outflow and the area whose drainage is reduced, "
            "against the ditch level above the outlet invert. <b>Map</b>: drainage rate as a "
            "share of the free-outlet rate (one raster band per ditch level) and the HGL rise "
            "in every pipe, shown at the mapped ditch level. <b>Ditch level explorer</b> "
            "(Plugins menu, opens after a run): up and down arrows step the map through the "
            "ditch levels, with the outflow, the main's profile and the values at a picked "
            "point. <b>Report</b>: the rating-curve table (with an Export CSV button), the "
            "same explorer, the rating chart and the main's profile. <b>Rating curve as CSV</b> (optional): the same table "
            "as a CSV file, units in the column names.</p>"
            "<p>Steady state: it shows the drainage the system can deliver while the ditch "
            "stays at a level, not the timing of a storm. Junction, bend and outlet losses "
            "are left out; flow is assumed not to run back into the field (a flap gate, or "
            "the soil already as wet as the ditch).</p>")

    def initAlgorithm(self, config=None):
        num = QgsProcessingParameterNumber.Double
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.NETWORK, self.tr("Buried drainage network (tile elevations and pipe sizes)"),
            [QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterRasterLayer(self.DEM, self.tr("Elevation (DEM)")))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.FIELD, self.tr("Field boundary [optional; default: land within 3/4 of a "
                                "spacing of a pipe]"), [QgsProcessing.TypeVectorPolygon],
            optional=True))
        self.addParameter(QgsProcessingParameterPoint(
            self.OUTLET, self.tr("Outlet [optional; default: lowest free end]"), optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.UNITS, self.tr("Design units"), options=[self.tr("Metres"), self.tr("Feet")],
            defaultValue=1))
        self.addParameter(QgsProcessingParameterNumber(
            self.DC, self.tr("Drainage coefficient, in/day or mm/day (0 = the network's "
                             "D_COEFF, else 3/8 in/day)"), num, defaultValue=0.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SPACING, self.tr("Lateral spacing (0 = estimate from the network)"), num,
            defaultValue=0.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.WT_DEPTH, self.tr("Design water-table depth midway between drains"), num,
            defaultValue=1.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.IMP_DEPTH, self.tr("Depth of the impermeable layer below the drains"), num,
            defaultValue=5.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.PIPE_TYPE, self.tr("Pipe"), options=[
                self.tr("Single-wall corrugated (n 0.015 to 8 in, 0.017 to 12 in, 0.020)"),
                self.tr("Smooth wall (n 0.011)"), self.tr("Clay or concrete (n 0.013)")],
            defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.LEVEL_MAX, self.tr("Highest ditch level above the outlet invert (0 = ground "
                                    "at the outlet plus a little)"), num, defaultValue=0.0,
            minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAP_LEVEL, self.tr("Ditch level above the outlet invert to map"), num,
            defaultValue=2.0, minValue=0.0))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.LEVEL_STEP, self.tr("Ditch level step (0 = automatic, about 20 levels)"), num,
            defaultValue=0.0, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.Z_UNITS, self.tr("DEM elevation units"),
            options=[self.tr("Metres"), self.tr("International feet"),
                     self.tr("US survey feet")], defaultValue=2)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.STEP, self.tr("Network calculation step (0 = automatic)"), num,
            defaultValue=0.0, minValue=0.0)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.RATING, self.tr("Rating curve"), QgsProcessing.TypeVector))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.RATING_CSV, self.tr("Rating curve as CSV"), self.tr("CSV files (*.csv)"),
            optional=True, createByDefault=False))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.PIPES, self.tr("Pipes (HGL rise at each ditch level)"), QgsProcessing.TypeVectorLine,
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.REDUCTION, self.tr("Drainage rate at each ditch level (% of free outlet, "
                                    "one band per level)"), optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.REPORT, self.tr("Report"), self.tr("HTML files (*.html)"), optional=True,
            createByDefault=True))
        self.addOutput(QgsProcessingOutputNumber("FREE_OUTFLOW",
                                                 self.tr("Free-outlet outflow")))
        self.addOutput(QgsProcessingOutputNumber("MAP_PCT",
                                                 self.tr("Outflow at the mapped level (%)")))

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _read_dem(layer, x0, y0, cell, nrows, ncols):
        from osgeo import gdal
        tmp = "/vsimem/backwater_dem.tif"
        ds = gdal.Warp(tmp, layer.dataProvider().dataSourceUri(), format="GTiff",
                       outputBounds=(x0, y0 - nrows * cell, x0 + ncols * cell, y0),
                       width=ncols, height=nrows, resampleAlg="bilinear",
                       dstNodata=-99999.0, outputType=gdal.GDT_Float32)
        if ds is None:
            raise QgsProcessingException("Could not read the DEM with GDAL.")
        arr = ds.GetRasterBand(1).ReadAsArray().astype(float)
        ds = None
        gdal.Unlink(tmp)
        arr[~np.isfinite(arr) | (arr <= -99998.0)] = np.nan
        return arr

    @staticmethod
    def _rasterize(geoms, x0, y0, cell, nrows, ncols):
        from osgeo import gdal, ogr
        ds = gdal.GetDriverByName("MEM").Create("", ncols, nrows, 1, gdal.GDT_Byte)
        ds.SetGeoTransform((x0, cell, 0.0, y0, 0.0, -cell))
        drv = ogr.GetDriverByName("Memory") or ogr.GetDriverByName("MEM")
        src = drv.CreateDataSource("bw_field")
        lyr = src.CreateLayer("f", geom_type=ogr.wkbMultiPolygon)
        for g in geoms:
            f = ogr.Feature(lyr.GetLayerDefn())
            f.SetGeometry(ogr.CreateGeometryFromWkb(bytes(g.asWkb())))
            lyr.CreateFeature(f)
        gdal.RasterizeLayer(ds, [1], lyr, burn_values=[1])
        return ds.GetRasterBand(1).ReadAsArray().astype(bool)

    # ------------------------------------------------------------ run
    def processAlgorithm(self, parameters, context, feedback):
        self._save_settings(parameters)
        src = self.parameterAsSource(parameters, self.NETWORK, context)
        if src is None:
            raise QgsProcessingException(self.tr("Could not load the drainage network."))
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        if dem_layer is None:
            raise QgsProcessingException(self.tr("Could not load the DEM."))
        crs = dem_layer.crs()
        if not crs.isValid() or crs.isGeographic():
            raise QgsProcessingException(self.tr("The DEM must be in a projected coordinate "
                                                 "system."))
        feet = self.parameterAsEnum(parameters, self.UNITS, context) == 1
        uf = UNIT_FACTORS[1 if feet else 0]
        zf = Z_FACTORS[self.parameterAsEnum(parameters, self.Z_UNITS, context)]
        xf = _meters_factor(crs)
        lu = "ft" if feet else "m"
        qu, qk = ("cfs", 1.0 / 0.028316846592) if feet else ("L/s", 1000.0)
        tctx = context.transformContext()
        warnings = []

        def num(key):
            return _num(self.parameterAsDouble(parameters, key, context))

        # ---- network (map coordinates of the DEM, elevations in metres)
        t = QgsCoordinateTransform(src.sourceCrs(), crs, tctx)
        names = {f.name().lower(): f.name() for f in src.fields()}
        kind_f = names.get("kind")
        size_fs = [names[k] for k in ("nominal", "actual_size") if k in names]
        ends = next(((names[a], names[b]) for a, b in (("elevdepth1", "elevdepth2"),
                                                       ("invert_us", "invert_ds"))
                     if a in names and b in names), None)
        lines = []
        dcs, sp_est = [], []
        for f in src.getFeatures():
            g = QgsGeometry(f.geometry())
            if g.isNull() or g.isEmpty():
                continue
            has_z = QgsWkbTypes.hasZ(g.wkbType())
            g.transform(t)
            parts = list(g.constGet().parts()) if g.isMultipart() else [g.constGet()]
            kind = f[kind_f] if kind_f else None
            kind = kind.strip().lower() if isinstance(kind, str) else None
            size = None
            for sf in size_fs:
                size = _num(f[sf])
                if size:
                    break
            if "d_coeff" in names:
                v = _num(f[names["d_coeff"]])
                if v:
                    dcs.append(v)
            if kind == "lateral" and "drained_area" in names and "length" in names:
                ar, ln = _num(f[names["drained_area"]]), _num(f[names["length"]])
                if ar and ln:
                    sp_est.append(ar * (43560.0 if feet else 1e4) / ln)
            for part in parts:
                pts = [(v.x(), v.y(), v.z() if has_z else float("nan"))
                       for v in part.vertices()]
                if len(pts) < 2:
                    continue
                a = np.array(pts, dtype=float)
                if not np.isfinite(a[:, 2]).any() or np.all(a[:, 2] == 0):
                    a[:, 2] = np.nan
                    if ends and len(parts) == 1:
                        v0, v1 = _num(f[ends[0]]), _num(f[ends[1]])
                        if v0 is not None and v1 is not None:
                            a[0, 2], a[-1, 2] = v0, v1
                a[:, 2] *= uf
                lines.append((a, kind, int(f.id()), {"size": size}))
        if not lines:
            raise QgsProcessingException(self.tr("The network has no lines."))

        dc = num(self.DC) or 0.0
        if dc <= 0:
            dc = float(np.median(dcs)) if dcs else 0.375
            dc_txt = "{:.3f} in/day ({})".format(dc, "from the network" if dcs else "default")
            dc_m = dc * 0.0254 / 86400.0
        else:
            dc_txt = "{:.3f} {}".format(dc, "in/day" if feet else "mm/day")
            dc_m = dc * (0.0254 if feet else 0.001) / 86400.0
        spacing = num(self.SPACING) or 0.0
        if spacing <= 0:
            spacing = float(np.median(sp_est)) if sp_est else (80.0 if feet else 25.0)
            sp_txt = "{:.1f} {} ({})".format(spacing, lu, "estimated from the network"
                                             if sp_est else "default")
        else:
            sp_txt = "{:.1f} {}".format(spacing, lu)
        spacing_m = spacing * uf

        # ---- grid
        xs = np.concatenate([a[:, 0] for a, _, _, _ in lines])
        ys = np.concatenate([a[:, 1] for a, _, _, _ in lines])
        fgeoms = []
        fsrc = self.parameterAsSource(parameters, self.FIELD, context)
        if fsrc is not None:
            tf = QgsCoordinateTransform(fsrc.sourceCrs(), crs, tctx)
            for f in fsrc.getFeatures():
                g = QgsGeometry(f.geometry())
                if not g.isNull() and not g.isEmpty():
                    g.transform(tf)
                    fgeoms.append(g)
        pad = spacing_m / xf + 10.0 / xf
        xmin, xmax = xs.min() - pad, xs.max() + pad
        ymin, ymax = ys.min() - pad, ys.max() + pad
        for g in fgeoms:
            bb = g.boundingBox()
            xmin, ymin = min(xmin, bb.xMinimum()), min(ymin, bb.yMinimum())
            xmax, ymax = max(xmax, bb.xMaximum()), max(ymax, bb.yMaximum())
        native = max(abs(dem_layer.rasterUnitsPerPixelX()), abs(dem_layer.rasterUnitsPerPixelY()))
        cell = max(native, 1.0 / xf)
        if (xmax - xmin) * (ymax - ymin) / cell ** 2 > MAX_CELLS:
            cell = math.sqrt((xmax - xmin) * (ymax - ymin) / MAX_CELLS)
        ncols = int(math.ceil((xmax - xmin) / cell))
        nrows = int(math.ceil((ymax - ymin) / cell))
        grid = Grid(xmin, ymax, cell, nrows, ncols)
        ground = self._read_dem(dem_layer, xmin, ymax, cell, nrows, ncols) * zf
        mask = np.isfinite(ground)
        if fgeoms:
            mask &= self._rasterize(fgeoms, xmin, ymax, cell, nrows, ncols)
        feedback.setProgress(10)

        from .core.sampling import bilinear

        def ground_at(x, y):
            return bilinear(grid, ground, x, y)

        outlet_xy = None
        if parameters.get(self.OUTLET) not in (None, ""):
            pt = self.parameterAsPoint(parameters, self.OUTLET, context, crs)
            if pt is not None and not (pt.x() == 0 and pt.y() == 0):
                outlet_xy = (pt.x(), pt.y())
        try:
            net = build_network(lines, max(0.5 / xf, 0.5 * cell), outlet_xy, ground_at)
        except NetworkError as e:
            raise QgsProcessingException(str(e))
        n_def = fill_tile_z(net, ground_at, 1.2)
        if n_def:
            warnings.append("{} network vertices had no tile elevation: taken 1.2 m below "
                            "the ground.".format(n_def))
        if net.unconnected:
            warnings.append("{} line(s) are not connected to the outlet and were left out."
                            .format(net.unconnected))
        diam = [e.attrs.get("size") for e in net.edges]
        if any(d is None for d in diam):
            warnings.append("{} pipe(s) have no size (NOMINAL or ACTUAL_SIZE): taken as "
                            "4 in.".format(sum(d is None for d in diam)))

        step = num(self.STEP) or 0.0
        step_m = step * uf if step > 0 else max(cell * xf, min(5.0, spacing_m / 4.0))
        p = ModelParams(dc_m_per_s=dc_m, wt_depth=num(self.WT_DEPTH) * uf,
                        imp_depth=num(self.IMP_DEPTH) * uf, spacing=spacing_m,
                        step=step_m / xf,
                        pipe_type=self.parameterAsEnum(parameters, self.PIPE_TYPE, context),
                        max_dist=None if fgeoms else 0.75 * spacing_m / xf)
        model = BackwaterModel(grid, ground, mask, net, p, xf, diam)
        if not model.drained.any():
            raise QgsProcessingException(self.tr("No drained land found near the pipes."))
        free = model.run_free()
        feedback.setProgress(25)

        inv = float(model.S.z[model.outlet])
        d_out = float(model.D[model.outlet])
        gx, gy = model.S.x[model.outlet], model.S.y[model.outlet]
        g_out = float(ground_at(np.array([gx]), np.array([gy]))[0])
        bank = g_out - inv if math.isfinite(g_out) else None
        lmax = (num(self.LEVEL_MAX) or 0.0) * uf
        if lmax <= 0:
            lmax = max((bank or 0.0) + 0.15, 4 * d_out, 0.6)
        lstep = (num(self.LEVEL_STEP) or 0.0) * uf
        if lstep <= 0:
            raw = lmax / 20.0
            base = (0.05 if not feet else 0.25 * 0.3048)
            lstep = max(base, round(raw / base) * base)
        levels = list(np.arange(0.0, lmax + 1e-9, lstep))
        map_h = (num(self.MAP_LEVEL) or 0.0) * uf
        if all(abs(h - map_h) > 1e-6 for h in levels):
            levels.append(map_h)
            levels.sort()

        # ---- rating curve
        rating = [{"level": None, "elev": None, "outflow": free["outflow"] * qk,
                   "pct_free": 100.0, "area_10": 0.0, "area_50": 0.0, "area_none": 0.0,
                   "backflow": 0.0, "max_rise": 0.0,
                   "surch_len": float(model.L[free["surcharged"]].sum()) / uf}]
        res_map = None
        map_index = 0
        store = LevelStore(model, uf, qk)
        store.add(free)
        for k, h in enumerate(levels):
            if feedback.isCanceled():
                return {}
            r = model.solve(inv + h)
            s = model.summary(r)
            rating.append({"level": h / uf, "elev": (inv + h) / uf, "outflow": s["outflow"] * qk,
                           "pct_free": s["pct_free"], "area_10": s["pct_area_10"],
                           "area_50": s["pct_area_50"], "area_none": s["pct_area_none"],
                           "backflow": s["pct_backflow"], "max_rise": s["max_rise"] / uf,
                           "surch_len": s["surcharged_len"] / uf})
            store.add(r)
            if abs(h - map_h) < 1e-6:
                res_map = r
                map_index = k + 1
            feedback.setProgress(25 + 60.0 * (k + 1) / len(levels))

        rf = QgsFields()
        for nm, typ in (("level", QVariant.Double), ("ditch_elev", QVariant.Double),
                        ("outflow", QVariant.Double), ("pct_free", QVariant.Double),
                        ("area_10", QVariant.Double), ("area_50", QVariant.Double),
                        ("area_none", QVariant.Double), ("backflow", QVariant.Double),
                        ("max_rise", QVariant.Double), ("surch_len", QVariant.Double)):
            rf.append(QgsField(nm, typ))
        rsink, rid = self.parameterAsSink(parameters, self.RATING, context, rf,
                                          QgsWkbTypes.NoGeometry, crs)
        if rsink is None:
            raise QgsProcessingException(self.tr("Could not create the rating table."))
        for row in rating:
            f = QgsFeature(rf)
            f.setAttributes([None if row["level"] is None else round(row["level"], 3),
                             None if row["elev"] is None else round(row["elev"], 3),
                             round(row["outflow"], 4), round(row["pct_free"], 2),
                             round(row["area_10"], 2), round(row["area_50"], 2),
                             round(row["area_none"], 2), round(row["backflow"], 2),
                             round(row["max_rise"], 3), round(row["surch_len"], 1)])
            rsink.addFeature(f, QgsFeatureSink.FastInsert)
        out = {self.RATING: rid, "FREE_OUTFLOW": free["outflow"] * qk}
        csv_path = self.parameterAsFileOutput(parameters, self.RATING_CSV, context)
        if csv_path:
            if not csv_path.lower().endswith(".csv"):
                csv_path += ".csv"
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
                fh.write(rating_csv(rating, lu, qu))
            out[self.RATING_CSV] = csv_path

        # ---- what the ditch-level explorer needs (panel and report)
        S = model.S
        nlayer = self.parameterAsVectorLayer(parameters, self.NETWORK, context)
        run_name = "{}, {}".format(nlayer.name() if nlayer is not None else "network",
                                   datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
        pg = ground_at(S.x[store.pidx], S.y[store.pidx])
        meta_json = panel_meta(str(uuid.uuid4()), run_name, rating, store, pg, pg - p.wt_depth,
                               lu, qu, d_out / uf, map_index)
        nsteps = len(store)

        # ---- pipes: HGL rise and flow at every ditch level
        pf = QgsFields()
        for nm, typ in (("kind", QVariant.String), ("d_in", QVariant.Double),
                        ("q_free", QVariant.Double), ("q_map", QVariant.Double),
                        ("hgl_free_ds", QVariant.Double), ("hgl_map_ds", QVariant.Double),
                        ("hgl_rise", QVariant.Double), ("above_crown", QVariant.Double),
                        ("surch_len", QVariant.Double)):
            pf.append(QgsField(nm, typ))
        for i in range(nsteps):
            pf.append(QgsField(q_field(i), QVariant.Double))
            pf.append(QgsField(rise_field(i), QVariant.Double))
        psink, pid = self.parameterAsSink(parameters, self.PIPES, context, pf,
                                          QgsWkbTypes.LineStringZ, crs)
        if psink is not None and res_map is not None:
            for ei, e in enumerate(net.edges):
                a, b = S.edge_first[ei], S.edge_last[ei]
                idx = np.arange(a, b + 1)
                rise = res_map["H"][idx] - free["H"][idx]
                crown = res_map["H"][idx] - (S.z[idx] + model.D[idx])
                f = QgsFeature(pf)
                f.setGeometry(QgsGeometry(QgsLineString(
                    [QgsPoint(float(x), float(y), float(z) / uf) for x, y, z in e.xyz[:, :3]])))
                f.setAttributes([
                    e.kind or ("main" if e.main else ("submain" if e.collector else "lateral")),
                    round(float(model.d_in_edge[ei]), 1),
                    round(float(free["Q"][a]) * qk, 4), round(float(res_map["Q"][a]) * qk, 4),
                    round(float(free["H"][a]) / uf, 3), round(float(res_map["H"][a]) / uf, 3),
                    round(float(rise.max()) / uf, 3), round(float(max(crown.max(), 0.0)) / uf, 3),
                    round(float(model.L[idx][res_map["surcharged"][idx]].sum()) / uf, 1)]
                    + [v for i in range(nsteps) for v in (
                        round(float(store.edge_q[i][ei]), 4),
                        round(float(store.edge_rise[i][ei]), 3))])
                psink.addFeature(f, QgsFeatureSink.FastInsert)
            out[self.PIPES] = pid
            if context.willLoadLayerOnCompletion(pid):
                _POST[pid] = _PipeStyler(lu, rise_field(map_index), meta_json)
                context.layerToLoadOnCompletionDetails(pid).setPostProcessor(_POST[pid])

        # ---- reduction raster
        rpath = self.parameterAsOutputLayer(parameters, self.REDUCTION, context)
        if rpath and res_map is not None:
            from osgeo import gdal
            ds = gdal.GetDriverByName("GTiff").Create(
                rpath, ncols, nrows, nsteps, gdal.GDT_Byte,
                ["COMPRESS=DEFLATE", "PREDICTOR=2", "TILED=YES", "INTERLEAVE=BAND"])
            ds.SetGeoTransform((xmin, cell, 0.0, ymax, 0.0, -cell))
            ds.SetProjection(crs.toWkt())
            for i in range(nsteps):
                band = ds.GetRasterBand(i + 1)
                band.WriteArray(store.band(i, nrows, ncols))
                band.SetNoDataValue(NODATA)
                lv = rating[i]["level"]
                band.SetDescription("Free outlet" if lv is None else
                                    "Ditch {:.2f} {} above the outlet invert".format(lv, lu))
            ds = None
            out[self.REDUCTION] = rpath
            if context.willLoadLayerOnCompletion(rpath):
                _POST[rpath] = _RatioStyler(map_index + 1, meta_json)
                context.layerToLoadOnCompletionDetails(rpath).setPostProcessor(_POST[rpath])

        # ---- report
        s_map = model.summary(res_map) if res_map is not None else None
        map_txt = None
        if s_map:
            out["MAP_PCT"] = s_map["pct_free"]
            map_txt = ("Ditch at {:.2f} {u} above the outlet invert (elevation {:.2f} {u}): "
                       "outflow {:.1f}% of the free outlet; drainage reduced by more than 10% "
                       "on {:.1f}% of the drained area and by more than half on {:.1f}%; the "
                       "ditch stands above the design water table under {:.1f}% of it; {:,.0f} "
                       "{u} of pipe run surcharged.").format(
                map_h / uf, (inv + map_h) / uf, s_map["pct_free"], s_map["pct_area_10"],
                s_map["pct_area_50"], s_map["pct_backflow"], s_map["surcharged_len"] / uf, u=lu)
        rep = self.parameterAsFileOutput(parameters, self.REPORT, context)
        if rep:
            idx, dist = main_profile(model)
            gr = ground_at(S.x[idx], S.y[idx])
            prof = {"dist": list(dist / uf), "invert": list(S.z[idx] / uf),
                    "crown": list((S.z[idx] + model.D[idx]) / uf), "ground": list(gr / uf),
                    "hgl_free": list(free["H"][idx] / uf),
                    "hgl_map": list(res_map["H"][idx] / uf) if res_map is not None else None,
                    "wt": list((gr - p.wt_depth) / uf)}
            meta = {"title": "Tile outlet submergence", "plugin": PLUGIN_NAME,
                    "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "units": {"l": lu, "q": qu}, "outlet_d": d_out / uf,
                    "csv_name": os.path.splitext(os.path.basename(rep))[0] + "_rating.csv",
                    "bank": bank / uf if bank is not None else None, "map_level": map_h / uf,
                    "inputs": [
                        ("Outlet invert", "{:.2f} {}".format(inv / uf, lu)),
                        ("Outlet pipe", "{:.0f} in at {:.2f}%".format(
                            d_out / 0.0254, 100 * model.slope[model.outlet])),
                        ("Ground at the outlet", "-" if bank is None else "{:.2f} {}".format(
                            g_out / uf, lu)),
                        ("Drained area", "{:,.1f} {}".format(
                            model.drained.sum() * model.cell_area / (4046.86 if feet else 1e4),
                            "ac" if feet else "ha")),
                        ("Free-outlet outflow", "{:.3f} {}".format(free["outflow"] * qk, qu)),
                        ("Drainage coefficient", dc_txt), ("Lateral spacing", sp_txt),
                        ("Design water-table depth", "{:.2f} {}".format(num(self.WT_DEPTH), lu)),
                        ("Impermeable layer below drains", "{:.2f} {}".format(
                            num(self.IMP_DEPTH), lu)),
                        ("Equivalent depth (Hooghoudt)", "{:.2f} {}".format(model.de / uf, lu))],
                    "notes": [
                        "Steady state: the drainage the system can deliver while the ditch "
                        "stays at a level, not the timing of a storm.",
                        "HGL stepped up the network with Manning's equation: never below normal "
                        "depth; full-pipe friction once a pipe runs full. Junction, bend, "
                        "entrance and exit losses are left out, so the losses are a little "
                        "low.",
                        "Drainage rate from Hooghoudt's equation, 8 de h + 4 h^2, with h the "
                        "height of the design water table above the water in the drain; each "
                        "cell drains to its nearest pipe at the drainage coefficient when the "
                        "outlet is free. K and the spacing cancel out of the ratio.",
                        "Flow back from the ditch into the field is not modelled; the "
                        "'ditch above water table' column shows where it could happen "
                        "without a flap gate.",
                        "Where laterals join a main at the main's invert, the main's own flow "
                        "backs up into them even with a free outlet; results are given "
                        "relative to the free outlet."]}
            with open(rep, "w", encoding="utf-8") as fh:
                kinds = [e.kind or ("main" if e.main else ("submain" if e.collector
                                                           else "lateral"))
                         for e in net.edges]
                try:
                    ex = report_payload(meta_json, store, grid, net.edges, kinds,
                                        model.d_in_edge)
                except (MemoryError, ValueError) as err:
                    ex = None
                    warnings.append("The report's ditch-level explorer was left out: "
                                    "{}".format(err))
                fh.write(report_html(meta, rating, prof, map_txt, ex))
            out[self.REPORT] = rep

        # ---- log
        feedback.pushInfo(self.tr("Outlet invert {:.2f} {u}, {:.0f} in pipe; free-outlet "
                                  "outflow {:.3f} {}.").format(inv / uf, d_out / 0.0254,
                                                              free["outflow"] * qk, qu, u=lu))
        for row in rating[1:]:
            feedback.pushInfo("  ditch {:.2f} {} above invert: {:.1f}% of free outflow, "
                              "{:.1f}% of area reduced >10%".format(
                                  row["level"], lu, row["pct_free"], row["area_10"]))
        if map_txt:
            feedback.pushInfo(map_txt)
        for w in warnings:
            feedback.pushWarning(w)
        feedback.setProgress(100)
        return out
