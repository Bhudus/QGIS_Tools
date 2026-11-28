"""Processing algorithm: export a buried tile network for machine control and staking."""
import datetime
import math
import os
import re

import numpy as np
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsLineString,
    QgsLineSymbol,
    QgsMapLayer,
    QgsPoint,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingFeatureSourceDefinition,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingOutputFile,
    QgsProcessingOutputNumber,
    QgsProcessingOutputString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterCrs,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProject,
    QgsRuleBasedRenderer,
    QgsUnitTypes,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon

from .core import NetworkError, build_network, build_runs, prepare_records, writers
from .core.runs import station_label

PLUGIN_DIR = os.path.dirname(__file__)
PLUGIN_NAME = "Tile Machine Export 0.1.0"
UNIT_FACTORS = [1.0, 0.3048]                              # design units -> metres
Z_FACTORS = [1.0, 0.3048, 1200.0 / 3937.0]                # DEM units -> metres
OUT_UNITS = [("ft", "international feet", 0.3048),
             ("US ft", "US survey feet", 1200.0 / 3937.0),
             ("m", "metres", 1.0)]

_POST = {}

# Settings of the last run, kept while the project stays open; cleared by the
# plugin when a new project is started or another one is loaded.
_LAST_RUN = {}
LAYER_INPUTS = ("NETWORK", "DEM")


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


class _RunStyler(QgsProcessingLayerPostProcessorInterface):
    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsVectorLayer):
            return
        root = QgsRuleBasedRenderer.Rule(None)
        for kind, color, width, label in (("main", "185,28,28", "1.0", "Main"),
                                          ("submain", "185,28,28", "0.6", "Submain"),
                                          ("lateral", "37,99,235", "0.35", "Lateral")):
            root.appendChild(QgsRuleBasedRenderer.Rule(
                QgsLineSymbol.createSimple({"color": color, "width": width}), 0, 0,
                "\"kind\" = '{}' AND (\"flag\" IS NULL OR \"flag\" = '')".format(kind), label))
        root.appendChild(QgsRuleBasedRenderer.Rule(
            QgsLineSymbol.createSimple({"color": "217,119,6", "width": "0.6"}), 0, 0,
            "\"flag\" <> ''", "Run to check (see flag)"))
        layer.setRenderer(QgsRuleBasedRenderer(root))
        from qgis.core import QgsPalLayerSettings, QgsVectorLayerSimpleLabeling
        s = QgsPalLayerSettings()
        s.fieldName = "run"
        try:
            from qgis.core import Qgis
            s.placement = Qgis.LabelPlacement.Line
        except (ImportError, AttributeError):
            s.placement = QgsPalLayerSettings.Line
        layer.setLabeling(QgsVectorLayerSimpleLabeling(s))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()


class TileMachineExportAlgorithm(QgsProcessingAlgorithm):
    NETWORK = "NETWORK"
    DEM = "DEM"
    OUTLET = "OUTLET"
    FIELD_NAME = "FIELD_NAME"
    UNITS = "UNITS"
    OUT_UNITS = "OUT_UNITS"
    OUT_CRS = "OUT_CRS"
    ELEV_OFFSET = "ELEV_OFFSET"
    BENCHMARK = "BENCHMARK"
    BENCHMARK_ELEV = "BENCHMARK_ELEV"
    CONTROL_OFFSET = "CONTROL_OFFSET"
    DIRECTION = "DIRECTION"
    STATION_INTERVAL = "STATION_INTERVAL"
    MIN_GRADE = "MIN_GRADE"
    MIN_COVER = "MIN_COVER"
    MAX_DEPTH = "MAX_DEPTH"
    Z_UNITS = "Z_UNITS"
    SNAP = "SNAP"
    JOIN_CROSSINGS = "JOIN_CROSSINGS"
    FMT_SHP = "FMT_SHP"
    SHP_WGS84 = "SHP_WGS84"
    FMT_KMZ = "FMT_KMZ"
    FMT_CSV = "FMT_CSV"
    FMT_PNEZD = "FMT_PNEZD"
    FMT_DXF = "FMT_DXF"
    FMT_SHEET = "FMT_SHEET"
    OUTPUT_FOLDER = "OUTPUT_FOLDER"
    RUNS = "RUNS"

    NOT_SAVED = ("OUTPUT_FOLDER", "RUNS")

    def tr(self, s):
        return QCoreApplication.translate("TileMachineExport", s)

    def createInstance(self):
        return TileMachineExportAlgorithm()

    def name(self):
        return "tilemachineexport"

    def displayName(self):
        return self.tr("Tile machine-control export")

    def group(self):
        return ""

    def groupId(self):
        return ""

    def icon(self):
        return QIcon(os.path.join(PLUGIN_DIR, "icon.png"))

    # ------------------------------------------------------------ remembered settings
    def addParameter(self, definition, createOutput=True):
        name = definition.name()
        if name not in self.NOT_SAVED:
            if not hasattr(self, "_saved_names"):
                self._saved_names = []
            self._saved_names.append(name)
            if name in _LAST_RUN:
                value = _LAST_RUN[name]
                try:
                    ok = (definition.checkValueIsAcceptable(value)
                          if hasattr(definition, "checkValueIsAcceptable") else True)
                    if ok and (name not in LAYER_INPUTS or _layer_still_there(value)):
                        definition.setDefaultValue(value)
                except (ValueError, TypeError):
                    pass
        return super().addParameter(definition, createOutput)

    def _save_settings(self, parameters):
        for name in getattr(self, "_saved_names", []):
            if name not in parameters:
                continue
            value = parameters[name]
            if isinstance(value, QgsMapLayer):
                value = value.id()
            elif isinstance(value, QgsProcessingFeatureSourceDefinition):
                value = QgsProcessingFeatureSourceDefinition(value)
            _LAST_RUN[name] = value

    def shortHelpString(self):
        return self.tr(
            "<p>Exports a buried tile drainage network (from Illini Subsurface Drain Layout, "
            "Illini Drainage System Design, Tile Profile or any line layer with tile "
            "elevations) as installation runs for tile plow and trencher grade control, "
            "GNSS staking and the office.</p>"
            "<p>Each <b>run</b> is one pipe as the machine installs it: the main from the "
            "outlet up, each submain and lateral from its junction up. Runs follow "
            "<i>pipe_id</i> where the network has it, else pipes of the same kind straight "
            "through junctions.</p>"
            "<p><b>Files</b> (into the output folder): 3D shapefiles of runs and stations "
            "(WGS 84 by default, for ag displays); a KMZ; a station CSV (station, lat/lon, "
            "northing/easting, ground, tile invert, control elevation, depth, grade); a PNEZD "
            "point file for survey data collectors; a DXF of 3D polylines for CAD and "
            "construction machine control; and a printable run sheet with each run's grade "
            "sections, depths and the settings to key into the display.</p>"
            "<p><b>Elevations must match the machine's.</b> The DEM and the design are usually "
            "on NAVD 88 (orthometric) heights; the machine may report ellipsoid heights or a "
            "local benchmark. Give the machine's reading at a benchmark (with a DEM) or an "
            "offset, and every elevation is shifted to the machine's datum. The <i>control "
            "point below the tile invert</i> lowers every control elevation, e.g. to the "
            "bottom of the plow boot.</p>"
            "<p>The native installable formats of Ag Leader Intellislope (.agsetup) and "
            "Trimble WM-Drain (FieldLevel) are not published; load these files as "
            "reference lines and enter the run settings from the run sheet.</p>")

    # ------------------------------------------------------------ parameters
    def initAlgorithm(self, config=None):
        num = QgsProcessingParameterNumber.Double
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.NETWORK, self.tr("Buried drainage network (lines with tile elevations)"),
            [QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, self.tr("Ground (DEM) [optional; default: the network's ground values]"),
            optional=True))
        self.addParameter(QgsProcessingParameterPoint(
            self.OUTLET, self.tr("Outlet location [optional; default: lowest free end]"),
            optional=True))
        self.addParameter(QgsProcessingParameterString(
            self.FIELD_NAME, self.tr("Field name (used for file and plan names)"),
            defaultValue="Field"))
        self.addParameter(QgsProcessingParameterEnum(
            self.UNITS, self.tr("Network design units (tile elevations, depths, distances)"),
            options=[self.tr("Metres"), self.tr("Feet")], defaultValue=1))
        self.addParameter(QgsProcessingParameterEnum(
            self.OUT_UNITS, self.tr("Output units"),
            options=[self.tr("International feet"), self.tr("US survey feet"),
                     self.tr("Metres")], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.ELEV_OFFSET, self.tr("Elevation offset added to every elevation, in output "
                                      "units (machine datum minus design datum)"),
            num, defaultValue=0.0))
        self.addParameter(QgsProcessingParameterPoint(
            self.BENCHMARK, self.tr("Benchmark location [optional; needs the DEM]"),
            optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.BENCHMARK_ELEV, self.tr("Machine's elevation at the benchmark, in output "
                                         "units [optional]"), num, optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.CONTROL_OFFSET, self.tr("Control point below the tile invert, in design "
                                         "units"), num, defaultValue=0.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.DIRECTION, self.tr("Install each run from"),
            options=[self.tr("Downstream end (outlet or junction) up"),
                     self.tr("Upstream end down")], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.STATION_INTERVAL, self.tr("Station interval, in design units"), num,
            defaultValue=50.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FMT_SHP, self.tr("Write shapefiles (runs and stations, 3D)"), defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SHP_WGS84, self.tr("Shapefiles in WGS 84 latitude/longitude (for ag "
                                    "displays)"), defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FMT_KMZ, self.tr("Write KMZ"), defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FMT_CSV, self.tr("Write station CSV"), defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FMT_PNEZD, self.tr("Write PNEZD point file (survey data collectors)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FMT_DXF, self.tr("Write DXF (3D polylines)"), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FMT_SHEET, self.tr("Write run sheet (HTML, printable)"), defaultValue=True))

        self.addParameter(_advanced(QgsProcessingParameterCrs(
            self.OUT_CRS, self.tr("Coordinate system for northing/easting, DXF and projected "
                                  "shapefiles [optional; default: the network's]"),
            optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MIN_GRADE, self.tr("Check: minimum grade (%)"), num, defaultValue=0.1)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MIN_COVER, self.tr("Check: minimum cover over the tile (design units)"), num,
            defaultValue=2.5)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MAX_DEPTH, self.tr("Check: maximum tile depth (design units)"), num,
            defaultValue=6.0)))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.Z_UNITS, self.tr("DEM elevation units"),
            options=[self.tr("Metres"), self.tr("International feet"),
                     self.tr("US survey feet")], defaultValue=2)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SNAP, self.tr("Snap tolerance for joining pipe ends, in design units "
                               "(0 = automatic)"), num, defaultValue=0.0, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterBoolean(
            self.JOIN_CROSSINGS, self.tr("Join pipes where they cross"), defaultValue=True)))

        self.addParameter(QgsProcessingParameterFolderDestination(
            self.OUTPUT_FOLDER, self.tr("Output folder")))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.RUNS, self.tr("Installation runs"), QgsProcessing.TypeVectorLine,
            optional=True, createByDefault=True))
        self.addOutput(QgsProcessingOutputNumber("N_RUNS", self.tr("Number of runs")))
        self.addOutput(QgsProcessingOutputNumber("N_FLAGGED", self.tr("Runs to check")))
        self.addOutput(QgsProcessingOutputFile("RUN_SHEET", self.tr("Run sheet")))
        self.addOutput(QgsProcessingOutputString("FILES", self.tr("Files written")))

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _dem_sampler(dem_layer, to_dem, zfac):
        """Bilinear-free sampler: nearest DEM cell, in design units (NaN off the DEM)."""
        prov = dem_layer.dataProvider()

        def f(xs, ys):
            out = np.full(len(xs), np.nan)
            for k, (x, y) in enumerate(zip(xs, ys)):
                p = to_dem.transform(QgsPointXY(float(x), float(y)))
                v, ok = prov.sample(p, 1)
                if ok and v is not None and math.isfinite(v):
                    out[k] = v * zfac
            return out
        return f

    @staticmethod
    def _densify(arr, step):
        """Insert vertices so no segment is longer than step (x, y in design units)."""
        if step <= 0 or len(arr) < 2:
            return arr
        out = [arr[0]]
        for a, b in zip(arr[:-1], arr[1:]):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            n = int(math.ceil(L / step))
            for k in range(1, n):
                out.append(a + (b - a) * (k / n))
            out.append(b)
        return np.array(out)

    # ------------------------------------------------------------ run
    def processAlgorithm(self, parameters, context, feedback):
        self._save_settings(parameters)
        src = self.parameterAsSource(parameters, self.NETWORK, context)
        if src is None:
            raise QgsProcessingException(self.tr("Could not load the drainage network."))
        ncrs = src.sourceCrs()
        if not ncrs.isValid() or ncrs.isGeographic():
            raise QgsProcessingException(self.tr(
                "The network must be in a projected coordinate system (metres or feet)."))
        units = self.parameterAsEnum(parameters, self.UNITS, context)
        uf = UNIT_FACTORS[units]                     # design units -> m
        xf = _meters_factor(ncrs)                    # map units -> m
        sc = xf / uf                                 # map units -> design units
        ou = self.parameterAsEnum(parameters, self.OUT_UNITS, context)
        o_lbl, o_long, o_m = OUT_UNITS[ou]
        k_out = uf / o_m                             # design -> output units
        d_lbl = "ft" if units == 1 else "m"
        warnings = []

        def num(key, optional=False):
            v = parameters.get(key)
            if optional and (v is None or v == ""):
                return None
            return _num(self.parameterAsDouble(parameters, key, context))

        tctx = context.transformContext()
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        zf = Z_FACTORS[self.parameterAsEnum(parameters, self.Z_UNITS, context)] / uf
        dem_at = None
        if dem_layer is not None:
            to_dem = QgsCoordinateTransform(ncrs, dem_layer.crs(), tctx)
            raw = self._dem_sampler(dem_layer, to_dem, zf)

            def dem_at(xd, yd):                       # design-unit coordinates
                return raw(np.asarray(xd) / sc, np.asarray(yd) / sc)

        # ---- read the network (coordinates scaled to design units)
        names = [f.name() for f in src.fields()]
        low = {n.lower(): n for n in names}
        kind_f = low.get("kind")
        tile_fields = next(((low[a], low[b]) for a, b in (("elevdepth1", "elevdepth2"),
                                                          ("invert_us", "invert_ds"))
                            if a in low and b in low), None)
        ground_fields = (low["ground_us"], low["ground_ds"]) \
            if "ground_us" in low and "ground_ds" in low else None
        keep = [low[k] for k in ("pipe_id", "pipe", "nominal", "actual_size", "id")
                if k in low]
        lines = []
        no_tile = 0
        no_ground = 0
        dens = (num(self.STATION_INTERVAL) or 50.0) / 5.0 if dem_at else 0.0
        for f in src.getFeatures():
            g = f.geometry()
            if g.isNull() or g.isEmpty():
                continue
            has_z = QgsWkbTypes.hasZ(g.wkbType())
            has_m = QgsWkbTypes.hasM(g.wkbType())
            parts = list(g.constGet().parts()) if g.isMultipart() else [g.constGet()]
            kind = f[kind_f] if kind_f else None
            kind = kind.strip().lower() if isinstance(kind, str) else None
            attrs = {k: f[k] for k in keep}
            for part in parts:
                pts = [(v.x() * sc, v.y() * sc, v.z() if has_z else float("nan"),
                        v.m() if has_m else float("nan")) for v in part.vertices()]
                if len(pts) < 2:
                    continue
                a = np.array(pts, dtype=float)
                z = a[:, 2]
                if not np.isfinite(z).any() or np.all(z == 0):
                    z[:] = np.nan
                    if tile_fields and len(parts) == 1:
                        v0, v1 = _num(f[tile_fields[0]]), _num(f[tile_fields[1]])
                        if v0 is not None and v1 is not None:
                            z[0], z[-1] = v0, v1
                gm = a[:, 3]
                if not np.isfinite(gm).any() or np.all(gm == 0):
                    gm[:] = np.nan
                    if ground_fields and len(parts) == 1:
                        v0, v1 = _num(f[ground_fields[0]]), _num(f[ground_fields[1]])
                        if v0 is not None and v1 is not None:
                            gm[0], gm[-1] = v0, v1
                s = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(a[:, 0]),
                                                              np.diff(a[:, 1])))])
                for col in (2, 3):
                    ok = np.isfinite(a[:, col])
                    if ok.sum() >= 2 and not ok.all():
                        a[~ok, col] = np.interp(s[~ok], s[ok], a[ok, col])
                if not np.isfinite(a[:, 2]).all():
                    no_tile += 1
                    continue
                if dem_at is not None:
                    a = self._densify(a, dens)
                    a[:, 3] = dem_at(a[:, 0], a[:, 1])
                if not np.isfinite(a[:, 3]).all():
                    no_ground += 1
                lines.append((a, kind, int(f.id()), attrs))
        if no_tile:
            msg = ("{} line(s) have no tile elevations (vertex Z, ElevDepth1/2 or "
                   "invert_us/ds).".format(no_tile))
            if not lines:
                raise QgsProcessingException(self.tr(
                    msg + " Bury the network first, e.g. with Illini Subsurface Drain Layout "
                    "or Illini Drainage System Design."))
            warnings.append(msg + " They were left out.")
        if no_ground:
            warnings.append("{} line(s) have no ground elevations: depths are blank there. "
                            "Give a DEM to fill them.".format(no_ground))

        outlet_xy = None
        if parameters.get(self.OUTLET) not in (None, ""):
            p = self.parameterAsPoint(parameters, self.OUTLET, context, ncrs)
            if p is not None and not (p.x() == 0 and p.y() == 0):
                outlet_xy = (p.x() * sc, p.y() * sc)
        tol = num(self.SNAP) or 0.0
        if tol <= 0:
            tol = 0.5 / uf                               # 0.5 m
        try:
            net = build_network(lines, tol, outlet_xy, None,
                                self.parameterAsBool(parameters, self.JOIN_CROSSINGS, context))
        except NetworkError as e:
            raise QgsProcessingException(str(e))
        if net.unconnected:
            warnings.append("{} line(s) are not connected to the outlet and were left out."
                            .format(net.unconnected))
        if net.loops:
            warnings.append("{} pipe(s) closed a loop and were left out.".format(net.loops))
        runs = build_runs(net)
        feedback.pushInfo(self.tr("{} installation runs from {} pipes.").format(
            len(runs), len(net.edges)))
        feedback.setProgress(30)

        # ---- datum offset
        offset = num(self.ELEV_OFFSET) or 0.0
        bm_elev = num(self.BENCHMARK_ELEV, True)
        if parameters.get(self.BENCHMARK) not in (None, "") and bm_elev is not None:
            if dem_at is None:
                raise QgsProcessingException(self.tr(
                    "A benchmark needs the DEM, to read the design-datum elevation there."))
            bp = self.parameterAsPoint(parameters, self.BENCHMARK, context, ncrs)
            gz = dem_at(np.array([bp.x() * sc]), np.array([bp.y() * sc]))[0]
            if not math.isfinite(gz):
                raise QgsProcessingException(self.tr("The benchmark is off the DEM."))
            bm_off = bm_elev - gz * k_out
            feedback.pushInfo(self.tr("Benchmark: DEM {:.3f} {u}, machine {:.3f} {u}: offset "
                                      "{:+.3f} {u}.").format(gz * k_out, bm_elev, bm_off, u=o_lbl))
            offset += bm_off
        if offset:
            feedback.pushInfo(self.tr("Elevation offset applied: {:+.3f} {}.").format(
                offset, o_lbl))

        # ---- coordinate conversions
        out_crs = self.parameterAsCrs(parameters, self.OUT_CRS, context)
        if not out_crs.isValid():
            out_crs = ncrs
        if out_crs.isGeographic():
            warnings.append("The northing/easting coordinate system is geographic; the "
                            "network's coordinate system is used for them instead.")
            out_crs = ncrs
        wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        t_out = QgsCoordinateTransform(ncrs, out_crs, tctx)
        t_ll = QgsCoordinateTransform(ncrs, wgs, tctx)

        def _tx(tr):
            def f(xd, yd):
                xs = np.empty(len(xd))
                ys = np.empty(len(yd))
                for k, (x, y) in enumerate(zip(xd, yd)):
                    p = tr.transform(QgsPointXY(float(x) / sc, float(y) / sc))
                    xs[k], ys[k] = p.x(), p.y()
                return xs, ys
            return f

        def size_of(r):
            for k in ("NOMINAL", "nominal", "ACTUAL_SIZE", "actual_size"):
                v = r.attrs.get(k)
                if v is not None and str(v) not in ("", "NULL"):
                    return "{} in".format(v)
            return None

        upstream_first = self.parameterAsEnum(parameters, self.DIRECTION, context) == 1
        per = 1000.0 if o_lbl == "m" else 100.0
        ctrl_off = num(self.CONTROL_OFFSET) or 0.0
        recs = prepare_records(
            runs, _tx(t_out), _tx(t_ll), k_out=k_out, k_m=uf, offset_out=offset,
            control_offset=ctrl_off, interval=num(self.STATION_INTERVAL) or 0.0,
            min_grade=num(self.MIN_GRADE), min_cover=num(self.MIN_COVER),
            max_depth=num(self.MAX_DEPTH), upstream_first=upstream_first,
            station_per=per, size_of=size_of)
        feedback.setProgress(55)

        # ---- files
        folder = self.parameterAsString(parameters, self.OUTPUT_FOLDER, context)
        os.makedirs(folder, exist_ok=True)
        field = (self.parameterAsString(parameters, self.FIELD_NAME, context) or "Field").strip()
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", field).strip("_") or "Field"
        base = os.path.join(folder, stem)
        offset_text = "{:+.3f} {}".format(offset, o_lbl)
        ctrl_note = ("Control elevations are the tile invert." if not ctrl_off else
                     "Control elevations are {:.3f} {} below the tile invert.".format(
                         ctrl_off * k_out, o_lbl))
        meta = {"field": field, "len_unit": o_lbl, "elev_unit": o_lbl, "elev_unit_long": o_long,
                "crs": "{} (northing/easting); WGS 84 (lat/lon)".format(
                    out_crs.authid() or out_crs.description()),
                "offset_text": offset_text, "control_note": ctrl_note,
                "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "plugin": PLUGIN_NAME, "label": lambda c: station_label(c, per)}
        written = []
        if self.parameterAsBool(parameters, self.FMT_CSV, context):
            writers.write_stations_csv(base + "_stations.csv", recs, meta)
            written.append(base + "_stations.csv")
        if self.parameterAsBool(parameters, self.FMT_PNEZD, context):
            writers.write_pnezd(base + "_pnezd.csv", recs, meta)
            written.append(base + "_pnezd.csv")
        if self.parameterAsBool(parameters, self.FMT_KMZ, context):
            writers.write_kmz(base + ".kmz", recs, meta)
            written.append(base + ".kmz")
        if self.parameterAsBool(parameters, self.FMT_DXF, context):
            writers.write_dxf(base + ".dxf", recs, meta, text_height=0.6 if o_lbl == "m" else 2.0)
            written.append(base + ".dxf")
        sheet = None
        if self.parameterAsBool(parameters, self.FMT_SHEET, context):
            sheet = base + "_run_sheet.html"
            writers.write_run_sheet(sheet, recs, meta)
            written.append(sheet)
        if self.parameterAsBool(parameters, self.FMT_SHP, context):
            wgs_out = self.parameterAsBool(parameters, self.SHP_WGS84, context)
            written += self._write_shapefiles(base, recs, wgs if wgs_out else out_crs, wgs_out,
                                              context, warnings)
        feedback.setProgress(85)

        # ---- runs layer in QGIS (network CRS, Z = control elevation in output units)
        out = {self.OUTPUT_FOLDER: folder, "N_RUNS": len(recs),
               "N_FLAGGED": sum(1 for r in recs if r["stats"]["flags"]),
               "FILES": "\n".join(written)}
        if sheet:
            out["RUN_SHEET"] = sheet
        rf = self._run_fields()
        sink, rid = self.parameterAsSink(parameters, self.RUNS, context, rf,
                                         QgsWkbTypes.LineStringZ, ncrs)
        if sink is not None:
            for r, rr in zip(recs, runs):
                a = rr.xyzg[::-1] if upstream_first else rr.xyzg
                zs = [p[2] for p in r["xyz"]]
                f = QgsFeature(rf)
                f.setGeometry(QgsGeometry(QgsLineString(
                    [QgsPoint(float(x) / sc, float(y) / sc, float(z))
                     for (x, y), z in zip(a[:, :2], zs)])))
                f.setAttributes(self._run_attrs(r))
                sink.addFeature(f, QgsFeatureSink.FastInsert)
            out[self.RUNS] = rid
            if context.willLoadLayerOnCompletion(rid):
                _POST[rid] = _RunStyler()
                context.layerToLoadOnCompletionDetails(rid).setPostProcessor(_POST[rid])

        # ---- log
        tot = sum(r["stats"]["length"] for r in recs)
        feedback.pushInfo(self.tr("{} runs, {:,.0f} {} of tile; {} to check (see the flag "
                                  "field and the run sheet).").format(
            len(recs), tot, o_lbl, out["N_FLAGGED"]))
        for pth in written:
            feedback.pushInfo("  " + pth)
        for w in warnings:
            feedback.pushWarning(w)
        feedback.setProgress(100)
        return out

    # ------------------------------------------------------------ shapefiles
    @staticmethod
    def _run_fields():
        f = QgsFields()
        for name, typ in (("run", QVariant.String), ("kind", QVariant.String),
                          ("size", QVariant.String), ("parent", QVariant.String),
                          ("install", QVariant.String), ("length", QVariant.Double),
                          ("inv_ds", QVariant.Double), ("inv_us", QVariant.Double),
                          ("ctrl_ds", QVariant.Double), ("ctrl_us", QVariant.Double),
                          ("min_grade", QVariant.Double), ("max_grade", QVariant.Double),
                          ("min_depth", QVariant.Double), ("max_depth", QVariant.Double),
                          ("tgt_depth", QVariant.Double), ("flag", QVariant.String)):
            f.append(QgsField(name, typ))
        return f

    @staticmethod
    def _run_attrs(r):
        st = r["stats"]

        def v(x, nd=3):
            return round(float(x), nd) if x is not None and math.isfinite(x) else None
        return [r["name"], r["kind"], r["size"], r["parent"] or None,
                "upstream end" if r["install_from"] == "upstream end" else "downstream end",
                v(st["length"], 2), v(st["inv_ds"]), v(st["inv_us"]), v(st["control_ds"]),
                v(st["control_us"]), v(st["min_grade"], 4), v(st["max_grade"], 4),
                v(st["min_depth"]), v(st["max_depth"]), v(st["mean_depth"]),
                "; ".join(st["flags"]) or None]

    def _write_shapefiles(self, base, recs, crs, use_lonlat, context, warnings):
        written = []
        rf = self._run_fields()
        runs_lyr = QgsVectorLayer("LineStringZ?crs={}".format(crs.authid() or "EPSG:4326"),
                                  "runs", "memory")
        if not crs.authid():
            runs_lyr.setCrs(crs)
        runs_lyr.dataProvider().addAttributes(rf.toList())
        runs_lyr.updateFields()
        feats = []
        for r in recs:
            pts = r["lonlat"] if use_lonlat else r["xyz"]
            zs = [p[2] for p in r["xyz"]]               # Z in output units either way
            f = QgsFeature(runs_lyr.fields())
            f.setGeometry(QgsGeometry(QgsLineString(
                [QgsPoint(p[0], p[1], z) for p, z in zip(pts, zs)])))
            f.setAttributes(self._run_attrs(r))
            feats.append(f)
        runs_lyr.dataProvider().addFeatures(feats)

        st_lyr = QgsVectorLayer("PointZ?crs={}".format(crs.authid() or "EPSG:4326"),
                                "stations", "memory")
        if not crs.authid():
            st_lyr.setCrs(crs)
        sf = QgsFields()
        for name, typ in (("run", QVariant.String), ("station", QVariant.String),
                          ("chain", QVariant.Double), ("ground", QVariant.Double),
                          ("invert", QVariant.Double), ("control", QVariant.Double),
                          ("depth", QVariant.Double), ("grade_pct", QVariant.Double),
                          ("note", QVariant.String)):
            sf.append(QgsField(name, typ))
        st_lyr.dataProvider().addAttributes(sf.toList())
        st_lyr.updateFields()
        feats = []
        for r in recs:
            for s in r["stations"]:
                x, y = (s["lon"], s["lat"]) if use_lonlat else (s["e"], s["n"])
                f = QgsFeature(st_lyr.fields())
                f.setGeometry(QgsGeometry(QgsPoint(x, y, s["control"])))

                def v(val, nd=3):
                    return round(float(val), nd) if math.isfinite(val) else None
                f.setAttributes([r["name"], s["label"], v(s["chain"], 2), v(s["ground"]),
                                 v(s["tile"]), v(s["control"]), v(s["depth"]),
                                 v(s["grade"], 4), s["note"][:250] or None])
                feats.append(f)
        st_lyr.dataProvider().addFeatures(feats)

        for lyr, suffix in ((runs_lyr, "_runs.shp"), (st_lyr, "_stations.shp")):
            path = base + suffix
            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = "ESRI Shapefile"
            opts.fileEncoding = "UTF-8"
            res = QgsVectorFileWriter.writeAsVectorFormatV3(
                lyr, path, context.transformContext(), opts)
            err = res[0] if isinstance(res, tuple) else res
            if err != QgsVectorFileWriter.NoError:
                warnings.append("Could not write {}: {}".format(
                    path, res[1] if isinstance(res, tuple) and len(res) > 1 else err))
            else:
                written.append(path)
        return written
