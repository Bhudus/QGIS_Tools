"""As-Built Check: a QGIS Processing algorithm.

Compares the tile as logged during installation (RTK points or logged runs)
with the design network, and writes the as-built network.
"""
import math
import os

import numpy as np
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsLineString,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPoint,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsRendererCategory,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon

from . import ab_core as C

FT_PER_M = 1.0 / 0.3048
ZUNITS = ["Feet", "Metres"]
LOGGED = ["The tile (invert)", "A reference above the tile, such as the GPS antenna "
          "(the offset is subtracted)"]
MATERIALS = ["Single Wall", "Smooth Wall", "Clay or Concrete"]
MODES = ["Compare the as-built with the design network",
         "Check the as-built on its own (grade reversals, bellies, depth; no design)"]
_POST = {}


def _ft_per_unit(crs):
    try:
        from qgis.core import Qgis
        m = QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), Qgis.DistanceUnit.Meters)
    except (ImportError, AttributeError):
        m = QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), QgsUnitTypes.DistanceMeters)
    return m * FT_PER_M


def _capacity(d_in, slope, material):
    """Full-pipe Manning flow (cfs), Routine K's roughness."""
    if not d_in or slope is None or not slope > 0:
        return 0.0
    if material == 0:
        n = 0.015 if d_in <= 8 else (0.017 if d_in <= 12 else 0.020)
    else:
        n = 0.011 if material == 1 else 0.013
    d = d_in / 12.0
    return (1.49 / n) * (math.pi * d * d / 4.0) * (d / 4.0) ** (2.0 / 3.0) * math.sqrt(slope)


class _Styler(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsVectorLayer):
            return
        try:
            if self.kind == "points":
                cats = []
                for value, colour, label in (("OK", "#33a02c", "Within tolerance"),
                                             ("Check", "#e31a1c", "To check"),
                                             ("Unmatched", "#999999", "No design line near")):
                    cats.append(QgsRendererCategory(value, QgsMarkerSymbol.createSimple(
                        {"name": "circle", "color": colour, "size": "1.6",
                         "outline_style": "no"}), label))
                layer.setRenderer(QgsCategorizedSymbolRenderer("status", cats))
            else:
                cats = []
                for value, colour, label in (("OK", "#33a02c", "As designed"),
                                             ("Check", "#e31a1c", "Installation to check"),
                                             ("Design issue", "#ff7f00",
                                              "As designed, but the design has an issue"),
                                             ("Not logged", "#999999", "Not logged")):
                    cats.append(QgsRendererCategory(value, QgsLineSymbol.createSimple(
                        {"color": colour, "width": "0.9"}), label))
                layer.setRenderer(QgsCategorizedSymbolRenderer("ab_status", cats))
            layer.triggerRepaint()
        except Exception:  # noqa: BLE001  (styling is a nicety)
            pass


class AsBuiltAlgorithm(QgsProcessingAlgorithm):
    MODE = "MODE"
    NETWORK = "NETWORK"
    RUN_FIELD = "RUN_FIELD"
    ORDER_FIELD = "ORDER_FIELD"
    LOG = "LOG"
    ELEV_FIELD = "ELEV_FIELD"
    LOGGED = "LOGGED"
    OFFSET = "OFFSET"
    Z_UNITS = "Z_UNITS"
    DEM = "DEM"
    MATCH = "MATCH"
    TOL_ELEV = "TOL_ELEV"
    TOL_BELLY = "TOL_BELLY"
    MIN_SLOPE = "MIN_SLOPE"
    WINDOW = "WINDOW"
    MIN_DEPTH = "MIN_DEPTH"
    MAX_DEPTH = "MAX_DEPTH"
    GAP = "GAP"
    SMOOTH = "SMOOTH"
    GRADE_TOL = "GRADE_TOL"
    MOVE = "MOVE"
    POINTS = "POINTS"
    CHECK = "CHECK"
    OUTPUT = "OUTPUT"

    def tr(self, s):
        return QCoreApplication.translate("AsBuilt", s)

    def createInstance(self):
        return AsBuiltAlgorithm()

    def name(self):
        return "asbuiltcheck"

    def displayName(self):
        return self.tr("As-built check")

    def group(self):
        return ""

    def groupId(self):
        return ""

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def shortHelpString(self):
        return self.tr(
            "<p>Compares the tile as logged during installation with the design, and writes "
            "the as-built network for the other drainage tools.</p>"
            "<p><b>Installation log</b>: points (or logged runs as lines, whose vertices are "
            "used) with the elevation in Z or in a field. It is the tile invert, or a "
            "reference above it such as the GPS antenna on the plow, less the offset.</p>"
            "<p>Each logged point is matched to the nearest line of the design network "
            "(within the matching distance) and compared at the same station:</p><ul>"
            "<li><b>Off grade</b>: the logged tile above or below the design tile by more "
            "than the tolerance.</li>"
            "<li><b>Bellies</b>: where water is held in the tile because the tile further "
            "down the pipe is higher; the depth held is the highest tile downstream less "
            "the tile there. Judged on the log smoothed over a short length, so RTK noise "
            "is not taken for a sag.</li>"
            "<li><b>Grade</b>: the fall over a moving window below the minimum slope, or "
            "reversed.</li>"
            "<li><b>Depth</b>: below the ground (the DEM, or the design ground in M) "
            "shallower than the minimum or deeper than the maximum.</li>"
            "<li><b>Not logged</b>: stretches of a line with no logged point.</li></ul>"
            "<p><b>As-built network</b>: the design network with the logged tile elevation "
            "(Z) at each vertex and the ground (M), its invert, grade and depth fields "
            "brought up to date, the results of the check in ab_ fields, and, with the "
            "Routine K fields, the capacity at the as-built grade. Optionally the logged "
            "positions replace the inner vertices of each line. Tile Profile, the Tile Size "
            "Calculator and the other tools then work on what was installed.</p>"
            "<p>Every check is also made on the design tile at the same places: the "
            "installation is flagged (ab_flag, status Check) only where it is worse than the "
            "design; issues already in the design (bellies, reverse grade, depths outside the "
            "limits, capacity) are listed apart (ab_design_flag, status Design issue).</p>"
            "<p><b>Checking the as-built on its own</b> (no design): the as-built is checked "
            "for bellies, reverse grade, grade below the minimum, depth (with a DEM, or the "
            "ground in M) and gaps in the log. It can be logged points, grouped into runs by "
            "the run field and put in order by the order field (or along each run); logged "
            "runs as lines; or a network with Z, such as the As-built network written by this "
            "tool or an Illini Drainage Network, whose lines are chained into pipes by their "
            "id, ds_link and pipe fields. Each run flows downhill (its lower end is the "
            "outlet); a network's lines flow in their own direction. The outputs are the "
            "points, a check of each run or line, and the runs as lines with the tile in Z "
            "and the ground in M (from logged points, ready for Tile Profile).</p>"
            "<p>Lengths, depths and tolerances in feet.</p>")

    def _num(self, name, label, default, lo, hi, advanced=False):
        p = QgsProcessingParameterNumber(name, label, QgsProcessingParameterNumber.Double,
                                         defaultValue=default, minValue=lo, maxValue=hi)
        if advanced:
            p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(p)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterEnum(
            self.MODE, self.tr("Check"), options=MODES, defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.NETWORK, self.tr("Design drainage network (Z = tile elevation) [for the "
                                  "comparison]"),
            [QgsProcessing.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LOG, self.tr("As-built: installation log (RTK points or logged runs), or an "
                              "as-built network"),
            [QgsProcessing.TypeVectorPoint, QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterField(
            self.RUN_FIELD, self.tr("Run (line, pass) field of logged points [checking on its "
                                    "own]"),
            parentLayerParameterName=self.LOG, optional=True))
        p = QgsProcessingParameterField(
            self.ORDER_FIELD, self.tr("Order field of logged points (time or sequence) [blank = "
                                      "along the run]"),
            parentLayerParameterName=self.LOG, optional=True)
        p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(p)
        self.addParameter(QgsProcessingParameterField(
            self.ELEV_FIELD, self.tr("Elevation field [blank = the Z of the geometry]"),
            parentLayerParameterName=self.LOG, optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.LOGGED, self.tr("The logged elevation is"), options=LOGGED, defaultValue=0))
        self._num(self.OFFSET, self.tr("Offset from the logged reference down to the tile "
                                       "invert (ft)"), 0.0, -100.0, 100.0)
        self.addParameter(QgsProcessingParameterEnum(
            self.Z_UNITS, self.tr("Elevation units of the log, network and DEM"),
            options=ZUNITS, defaultValue=0))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, self.tr("DEM for the ground [optional; else the design ground, M]"),
            optional=True))
        self._num(self.TOL_ELEV, self.tr("Tolerance on the tile elevation (ft)"), 0.1, 0.0, 10.0)
        self._num(self.TOL_BELLY, self.tr("Belly: water held deeper than (ft)"), 0.1, 0.0, 10.0)
        self._num(self.MIN_SLOPE, self.tr("Minimum grade (%)"), 0.1, 0.0, 10.0)
        self._num(self.MIN_DEPTH, self.tr("Minimum depth to the tile (ft)"), 2.5, 0.0, 50.0)
        self._num(self.MAX_DEPTH, self.tr("Maximum depth to the tile (ft)"), 6.0, 0.0, 50.0)
        self.addParameter(QgsProcessingParameterBoolean(
            self.MOVE, self.tr("Move the lines to the logged positions (the line ends stay, "
                               "so the network stays joined)"), defaultValue=False))
        self._num(self.MATCH, self.tr("Matching distance to the design line (ft)"), 10.0, 0.1,
                  1000.0, advanced=True)
        self._num(self.WINDOW, self.tr("Grade window (ft)"), 100.0, 1.0, 10000.0, advanced=True)
        self._num(self.SMOOTH, self.tr("Smoothing length for bellies and grade (ft)"), 20.0,
                  0.0, 1000.0, advanced=True)
        self._num(self.GAP, self.tr("Longest stretch without a logged point (ft)"), 30.0, 1.0,
                  10000.0, advanced=True)
        self._num(self.GRADE_TOL, self.tr("Grade tolerance beyond the log's own scatter (%)"),
                  0.01, 0.0, 10.0, advanced=True)
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.POINTS, self.tr("As-built points"), QgsProcessing.TypeVectorPoint))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.CHECK, self.tr("As-built check"), QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("As-built network"), QgsProcessing.TypeVectorLine))

    # ------------------------------------------------------------ readers
    def _read_network(self, src, crs, context, xf, zf):
        names = [f.name() for f in src.fields()]
        tr = QgsCoordinateTransform(src.sourceCrs(), crs, context.transformContext())
        links, no_z = [], 0
        for f in src.getFeatures():
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            g.transform(tr)
            hz = QgsWkbTypes.hasZ(g.wkbType())
            hm = QgsWkbTypes.hasM(g.wkbType())
            if not hz:
                no_z += 1
                continue
            pts = []
            for part in g.constParts():
                for v in part.vertices():
                    pts.append((v.x() * xf, v.y() * xf, v.z() * zf,
                                v.m() * zf if hm else float("nan")))
            if len(pts) < 2:
                continue
            a = np.array(pts, float)
            st = C.link_stations(a[:, :2])
            keep = np.concatenate([[True], np.diff(st) > 1e-9])
            a, st = a[keep], st[keep]
            attrs = {n: f[n] for n in names}
            links.append({"xy": a[:, :2], "z": a[:, 2], "g": a[:, 3], "st": st,
                          "id": attrs.get("id"), "ds_link": attrs.get("ds_link"),
                          "pipe": attrs.get("pipe"), "attrs": attrs})
        return links, names, no_z

    def _read_log(self, src, crs, context, xf, zf, field, offset):
        tr = QgsCoordinateTransform(src.sourceCrs(), crs, context.transformContext())
        pts, no_elev = [], 0
        for f in src.getFeatures():
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            g.transform(tr)
            hz = QgsWkbTypes.hasZ(g.wkbType())
            fv = None
            if field:
                try:
                    fv = float(f[field])
                except (TypeError, ValueError):
                    fv = None
            for part in g.constParts():
                for v in part.vertices():
                    if fv is not None:
                        z = fv
                    elif hz and math.isfinite(v.z()):
                        z = v.z()
                    else:
                        no_elev += 1
                        continue
                    pts.append((v.x() * xf, v.y() * xf, z * zf - offset))
        return (np.array(pts, float) if pts else np.zeros((0, 3))), no_elev

    # ----------------------------------------------------------------- run
    def processAlgorithm(self, parameters, context, feedback):
        if self.parameterAsEnum(parameters, self.MODE, context) == 1:
            return self._check_only(parameters, context, feedback)
        if parameters.get(self.NETWORK) in (None, ""):
            raise QgsProcessingException(self.tr(
                "Give the design network to compare with, or choose to check the as-built on "
                "its own."))
        nsrc = self.parameterAsSource(parameters, self.NETWORK, context)
        lsrc = self.parameterAsSource(parameters, self.LOG, context)
        if nsrc is None or lsrc is None:
            raise QgsProcessingException(self.tr("Could not load the network or the log."))
        crs = nsrc.sourceCrs()
        if not crs.isValid() or crs.isGeographic():
            raise QgsProcessingException(self.tr(
                "The network must be in a projected coordinate system (metres or feet)."))
        xf = _ft_per_unit(crs)
        zf = 1.0 if self.parameterAsEnum(parameters, self.Z_UNITS, context) == 0 else FT_PER_M
        P = {k: self.parameterAsDouble(parameters, k, context) for k in (
            self.OFFSET, self.MATCH, self.TOL_ELEV, self.TOL_BELLY, self.MIN_SLOPE, self.WINDOW,
            self.MIN_DEPTH, self.MAX_DEPTH, self.GAP, self.SMOOTH, self.GRADE_TOL)}
        offset = P[self.OFFSET] if self.parameterAsEnum(parameters, self.LOGGED, context) == 1 \
            else 0.0
        field = self.parameterAsString(parameters, self.ELEV_FIELD, context) or None
        move = self.parameterAsBool(parameters, self.MOVE, context)

        links, names, no_z = self._read_network(nsrc, crs, context, xf, zf)
        if no_z:
            feedback.reportError(self.tr("{} network line(s) have no Z (tile elevation) and are "
                                         "left out.").format(no_z))
        if not links:
            raise QgsProcessingException(self.tr("The design network has no lines with Z."))
        pts, no_elev = self._read_log(lsrc, crs, context, xf, zf, field, offset)
        if no_elev:
            feedback.reportError(self.tr("{} logged point(s) have no elevation and are left "
                                         "out.").format(no_elev))
        if len(pts) == 0:
            raise QgsProcessingException(self.tr("The log has no points with an elevation."))
        feedback.pushInfo(self.tr("{:,} logged points; {} design lines.").format(
            len(pts), len(links)))
        feedback.setProgress(10)

        dem = self.parameterAsRasterLayer(parameters, self.DEM, context) \
            if parameters.get(self.DEM) not in (None, "") else None
        ground = None
        if dem is not None:
            dtr = QgsCoordinateTransform(crs, dem.crs(), context.transformContext())
            prov = dem.dataProvider()

            def ground(xs, ys):
                out = np.full(len(xs), np.nan)
                for k, (x, y) in enumerate(zip(xs, ys)):
                    p = dtr.transform(QgsPointXY(x / xf, y / xf))
                    v, ok = prov.sample(p, 1)
                    if ok and v is not None and math.isfinite(v):
                        out[k] = v * zf
                return out

        p, per, runs = C.analyse(pts, links, P[self.MATCH], P[self.TOL_ELEV], P[self.TOL_BELLY],
                                 P[self.MIN_SLOPE] / 100.0, P[self.WINDOW], P[self.MIN_DEPTH],
                                 P[self.MAX_DEPTH], P[self.GAP], ground=ground,
                                 smoothing=P[self.SMOOTH],
                                 grade_tol=P[self.GRADE_TOL] / 100.0)
        feedback.setProgress(60)
        self._write(parameters, context, feedback, crs, xf, zf, pts, p, links, per, names, move,
                    P, ground)
        feedback.setProgress(100)

        n_match = int((p["link"] >= 0).sum())
        checked = [r for r in per if r["n"]]
        flagged = [r for r in per if r["flags"]]
        design_n = sum(1 for r in per if not r["flags"] and r.get("design_flags"))
        within = float(np.mean(np.abs(p["dev"][p["link"] >= 0]) <= P[self.TOL_ELEV])) * 100 \
            if n_match else 0.0
        feedback.pushInfo(self.tr(
            "Matched {:,} of {:,} points ({:.0f}%); {:.0f}% of them within {:g} ft of the design "
            "tile. {} of {} design lines logged; {} line(s) where the installation is to check; "
            "{} more installed as designed but with an issue in the design itself.").format(
            n_match, len(pts), 100.0 * n_match / len(pts), within, P[self.TOL_ELEV],
            len(checked), len(per), len(flagged), design_n))
        return {self.POINTS: self._ids[self.POINTS], self.CHECK: self._ids[self.CHECK],
                self.OUTPUT: self._ids[self.OUTPUT]}

    # -------------------------------------------------------------- output
    def _write(self, parameters, context, feedback, crs, xf, zf, pts, p, links, per, names, move,
               P, ground):
        D, I, S = QVariant.Double, QVariant.Int, QVariant.String
        self._ids = {}
        el = 1.0 / zf

        def r3(v):
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return round(v, 3) if math.isfinite(v) else None

        def fields(spec):
            fs = QgsFields()
            for n, t in spec:
                fs.append(QgsField(n, t))
            return fs

        # points
        fp = fields([("point", I), ("link_id", I), ("station_ft", D), ("offset_ft", D),
                     ("tile_ft", D), ("design_ft", D), ("dev_ft", D), ("dev_raw_ft", D),
                     ("depth_ft", D),
                     ("held_ft", D), ("grade_pct", D), ("status", S), ("flag", S)])
        sink, self._ids[self.POINTS] = self.parameterAsSink(
            parameters, self.POINTS, context, fp, QgsWkbTypes.Point, crs)
        for k in range(len(pts)):
            li = int(p["link"][k])
            flags = []
            if li < 0:
                status = "Unmatched"
                flags.append("no design line within {:g} ft".format(P[self.MATCH]))
            else:
                if abs(p["dev"][k]) > P[self.TOL_ELEV]:
                    flags.append("{:+.2f} ft off grade".format(p["dev"][k]))
                if p["held"][k] - p["held_design"][k] > P[self.TOL_BELLY]:
                    flags.append("belly {:.2f} ft".format(p["held"][k]))
                g, gd = p["grade"][k], p["grade_design"][k]
                gt = P[self.GRADE_TOL] / 100.0 + 2.0 * (p["grade_se"][k] or 0.0)
                lim = P[self.MIN_SLOPE] / 100.0 - gt - 1e-6
                lim_d = P[self.MIN_SLOPE] / 100.0 - P[self.GRADE_TOL] / 100.0 - 1e-6
                if math.isfinite(g) and g < lim and not (math.isfinite(gd) and gd < lim_d):
                    flags.append("reverse grade" if g < -gt else "below minimum grade")
                d, dd = p["depth"][k], p["depth_design"][k]
                half = 0.5 * P[self.TOL_ELEV]
                if math.isfinite(d) and d < P[self.MIN_DEPTH] - half and \
                        not dd < P[self.MIN_DEPTH]:
                    flags.append("shallow")
                if math.isfinite(d) and d > P[self.MAX_DEPTH] + half and \
                        not dd > P[self.MAX_DEPTH]:
                    flags.append("deep")
                status = "Check" if flags else "OK"
            f = QgsFeature(fp)
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(pts[k, 0] / xf, pts[k, 1] / xf)))
            lid = links[li]["id"] if li >= 0 else None
            f.setAttributes([k + 1, int(lid) if lid is not None else None,
                             r3(p["station"][k]) if li >= 0 else None,
                             r3(p["dist"][k]) if li >= 0 else None, r3(pts[k, 2]),
                             r3(p["design"][k]), r3(p["dev"][k]), r3(p["dev_raw"][k]),
                             r3(p["depth"][k]),
                             r3(p["held"][k]) if li >= 0 else None,
                             r3(p["grade"][k] * 100.0), status, "; ".join(flags) or None])
            sink.addFeature(f, QgsFeatureSink.FastInsert)

        # per-line check and the as-built network
        extra = [("ab_status", S), ("ab_logged_pct", D), ("ab_points", I), ("ab_dev_mean", D),
                 ("ab_dev_max", D), ("ab_dev_rms", D), ("ab_within_pct", D), ("ab_belly_ft", D),
                 ("ab_belly_design", D),
                 ("ab_flat_pct", D), ("ab_depth_min", D), ("ab_depth_max", D),
                 ("ab_grade_pct", D), ("ab_design_pct", D), ("ab_offset_max", D),
                 ("ab_cap_ratio", D), ("ab_flag", S), ("ab_design_flag", S)]
        fc = fields([("id", I), ("kind", S), ("pipe", S), ("length_ft", D)] + extra)
        csink, self._ids[self.CHECK] = self.parameterAsSink(
            parameters, self.CHECK, context, fc, QgsWkbTypes.LineString, crs)
        out_spec = []
        fo = QgsFields()
        for f in self.parameterAsSource(parameters, self.NETWORK, context).fields():
            if f.name() not in dict(extra):
                out_spec.append((f.name(), None))
                fo.append(f)                         # the design field as it is
        for n, t in extra:
            fo.append(QgsField(n, t))
        osink, self._ids[self.OUTPUT] = self.parameterAsSink(
            parameters, self.OUTPUT, context, fo, QgsWkbTypes.LineStringZM, crs)
        upd_ends = 0
        for k, (L, r) in enumerate(zip(links, per)):
            m = p["link"] == k
            xy, z, g, nup = C.as_built_line(L, p["station"][m], p["tile_s"][m], pts[m, :2],
                                            gap=P[self.GAP], move=move)
            if g is None or not np.isfinite(g).all():
                g = np.interp(C.link_stations(xy), L["st"], L["g"]) \
                    if np.isfinite(L["g"]).all() else np.full(len(xy), np.nan)
            if ground is not None:
                gd = ground(xy[:, 0], xy[:, 1])
                g = np.where(np.isfinite(gd), gd, g)
            upd_ends += nup
            a = L["attrs"]
            length = float(C.link_stations(xy)[-1])
            ab_grade = r.get("grade", float("nan"))       # least squares; nan if too short
            cap = cap_d = None
            if all(n in names for n in ("NOMINAL", "CUM_SEG_FLOW")):
                mat = str(a.get("PIPE_TYPE") or "Single Wall")
                mi = MATERIALS.index(mat) if mat in MATERIALS else 0
                try:
                    q = float(a.get("CUM_SEG_FLOW") or 0.0)
                except (TypeError, ValueError):
                    q = 0.0

                def ratio(grade):
                    if grade is None or not math.isfinite(grade):
                        return None
                    c = _capacity(a.get("NOMINAL"), grade, mi)
                    return q / c if c > 0 else (float("inf") if q > 0 else None)
                cap = ratio(ab_grade)
                cap_d = ratio(r.get("grade_design"))
            flags = list(r["flags"])
            design = list(r.get("design_flags", []))
            if cap is not None and cap > max(1.0, cap_d or 0.0) + 0.05:
                flags.append("over capacity at the as-built grade")
            if cap_d is not None and cap_d > 1.0:
                design.append("over capacity at the design grade")
            if flags:
                status = "Check"
            elif not r["n"]:
                status = "Not logged"
            elif design:
                status = "Design issue"
            else:
                status = "OK"
            vals = [status, r3(100 * r["coverage"]), r["n"], r3(r.get("dev_mean")),
                    r3(r.get("dev_max")), r3(r.get("dev_rms")),
                    r3(100 * r["within"]) if "within" in r else None, r3(r.get("belly")),
                    r3(r.get("belly_design")),
                    r3(r.get("flat_pct")), r3(r.get("depth_min")), r3(r.get("depth_max")),
                    r3(100 * ab_grade), r3(100 * r.get("grade_design", float("nan"))),
                    r3(r.get("offset_max")),
                    r3(cap) if cap is not None and math.isfinite(cap) else None,
                    "; ".join(flags) or None, "; ".join(design) or None]
            geo = QgsGeometry.fromPolylineXY([QgsPointXY(x / xf, y / xf) for x, y in xy])
            cf = QgsFeature(fc)
            cf.setGeometry(geo)
            cf.setAttributes([int(a["id"]) if a.get("id") is not None else k + 1, a.get("kind"),
                              a.get("pipe"), r3(length)] + vals)
            csink.addFeature(cf, QgsFeatureSink.FastInsert)
            # as-built network
            na = dict(a)
            depth = g - z
            if r["n"]:
                for key, val in (("invert_us", z[0] * el), ("invert_ds", z[-1] * el),
                                 ("ElevDepth1", z[0] * el), ("ElevDepth2", z[-1] * el),
                                 ("BuryDepth1", depth[0] * el), ("BuryDepth2", depth[-1] * el),
                                 ("depth_ds", depth[-1] * el),
                                 ("max_depth", np.nanmax(depth) * el),
                                 ("min_depth", np.nanmin(depth) * el),
                                 ("design_grade_pct", 100.0 * ab_grade
                                  if math.isfinite(ab_grade) else
                                  100.0 * (z[0] - z[-1]) / max(length, 1e-9))):
                    if key in na and math.isfinite(val):
                        na[key] = round(float(val), 4)
            for (n, _), v in zip(extra, vals):
                na[n] = v
            ls = QgsLineString([QgsPoint(x / xf, y / xf, zz * el,
                                         (gg if math.isfinite(gg) else zz) * el)
                                for (x, y), zz, gg in zip(xy, z, g)])
            of = QgsFeature(fo)
            of.setGeometry(QgsGeometry(ls))
            of.setAttributes([na.get(n) for n, _ in out_spec + extra])
            osink.addFeature(of, QgsFeatureSink.FastInsert)
        for key, kind in ((self.POINTS, "points"), (self.CHECK, "lines")):
            dest = self._ids.get(key)
            if dest and context.willLoadLayerOnCompletion(dest):
                _POST[dest] = _Styler(kind)
                context.layerToLoadOnCompletionDetails(dest).setPostProcessor(_POST[dest])

    # ------------------------------------------------- the as-built on its own
    def _check_only(self, parameters, context, feedback):
        src = self.parameterAsSource(parameters, self.LOG, context)
        if src is None:
            raise QgsProcessingException(self.tr("Could not load the as-built layer."))
        crs = src.sourceCrs()
        if not crs.isValid() or crs.isGeographic():
            raise QgsProcessingException(self.tr(
                "The as-built layer must be in a projected coordinate system (metres or feet)."))
        xf = _ft_per_unit(crs)
        zf = 1.0 if self.parameterAsEnum(parameters, self.Z_UNITS, context) == 0 else FT_PER_M
        P = {k: self.parameterAsDouble(parameters, k, context) for k in (
            self.OFFSET, self.TOL_ELEV, self.TOL_BELLY, self.MIN_SLOPE, self.WINDOW,
            self.MIN_DEPTH, self.MAX_DEPTH, self.GAP, self.SMOOTH, self.GRADE_TOL)}
        offset = P[self.OFFSET] if self.parameterAsEnum(parameters, self.LOGGED, context) == 1 \
            else 0.0
        efield = self.parameterAsString(parameters, self.ELEV_FIELD, context) or None
        rfield = self.parameterAsString(parameters, self.RUN_FIELD, context) or None
        ofield = self.parameterAsString(parameters, self.ORDER_FIELD, context) or None
        names = [f.name() for f in src.fields()]

        # every feature: its vertices (x, y, tile, ground) in ft
        feats, no_elev = [], 0
        for f in src.getFeatures():
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            hz = QgsWkbTypes.hasZ(g.wkbType())
            hm = QgsWkbTypes.hasM(g.wkbType())
            fv = None
            if efield:
                try:
                    fv = float(f[efield])
                except (TypeError, ValueError):
                    fv = None
            pts = []
            for part in g.constParts():
                for v in part.vertices():
                    if fv is not None:
                        zz = fv
                    elif hz and math.isfinite(v.z()):
                        zz = v.z()
                    else:
                        no_elev += 1
                        continue
                    mm = v.m() * zf if hm and math.isfinite(v.m()) else float("nan")
                    tile = zz * zf - offset
                    if not mm > tile:                 # M is the ground only above the tile
                        mm = float("nan")
                    pts.append((v.x() * xf, v.y() * xf, tile, mm))
            if pts:
                feats.append({"pts": np.array(pts, float), "attrs": {n: f[n] for n in names}})
        if no_elev:
            feedback.reportError(self.tr("{} vertices have no elevation and are left out.")
                                 .format(no_elev))
        if not feats:
            raise QgsProcessingException(self.tr("The as-built layer has no elevations."))
        points_mode = all(len(F["pts"]) == 1 for F in feats)

        # pipes: lists of pieces, each a feature's ordered vertices, upstream first
        pipes = []
        if points_mode:
            if not rfield:
                feedback.pushInfo(self.tr("Logged points with no run field: all of them are "
                                          "taken as one run."))
            groups = {}
            for k, F in enumerate(feats):
                key = F["attrs"].get(rfield) if rfield else 0
                groups.setdefault(key, []).append(k)
            for key, ks in groups.items():
                a = np.vstack([feats[k]["pts"] for k in ks])
                if ofield:
                    try:
                        o = np.argsort([float(feats[k]["attrs"].get(ofield)) for k in ks],
                                       kind="stable")
                    except (TypeError, ValueError):
                        o = C.order_points(a[:, :2])
                else:
                    o = C.order_points(a[:, :2])
                pipes.append({"name": key, "pieces": [{"pts": a[o], "src": [ks[i] for i in o],
                                                       "attrs": {rfield or "run": key}}]})
        else:
            chained = all(n in names for n in ("id", "ds_link", "pipe"))
            if chained:
                links = [{"id": F["attrs"].get("id"), "ds_link": F["attrs"].get("ds_link"),
                          "pipe": F["attrs"].get("pipe")} for F in feats]
                for chain in C.pipes_of(links):
                    pipes.append({"name": feats[chain[0]]["attrs"].get("pipe"),
                                  "pieces": [{"pts": feats[k]["pts"], "src": [k],
                                              "attrs": feats[k]["attrs"]} for k in chain],
                                  "fixed": True})
            else:
                for k, F in enumerate(feats):
                    pipes.append({"name": k + 1, "pieces": [{"pts": F["pts"], "src": [k],
                                                             "attrs": F["attrs"]}]})

        dem = self.parameterAsRasterLayer(parameters, self.DEM, context) \
            if parameters.get(self.DEM) not in (None, "") else None
        ground = None
        if dem is not None:
            dtr = QgsCoordinateTransform(crs, dem.crs(), context.transformContext())
            prov = dem.dataProvider()

            def ground(xs, ys):
                out = np.full(len(xs), np.nan)
                for k, (x, y) in enumerate(zip(xs, ys)):
                    p = dtr.transform(QgsPointXY(x / xf, y / xf))
                    v, ok = prov.sample(p, 1)
                    if ok and v is not None and math.isfinite(v):
                        out[k] = v * zf
                return out

        # check each pipe
        results = []
        for pi, pipe in enumerate(pipes):
            st_all, z_all, g_all, xy_all, piece_of, orig = [], [], [], [], [], []
            off = 0.0
            for j, piece in enumerate(pipe["pieces"]):
                a = piece["pts"]
                st = C.link_stations(a[:, :2])
                keep = np.concatenate([[True], np.diff(st) > 1e-9]) if len(st) > 1 \
                    else np.array([True])
                a, st = a[keep], st[keep]
                piece["pts"] = a
                # the vertices as they are: points in between would claim a certainty
                # of the grade the line does not have
                s2, (x2, y2, z2, g2) = st, (a[:, 0], a[:, 1], a[:, 2], a[:, 3])
                piece["st"] = st + off
                st_all.append(s2 + off)
                z_all.append(z2)
                g_all.append(g2)
                xy_all.append(np.column_stack([x2, y2]))
                piece_of.append(np.full(len(s2), j))
                orig.append(np.isin(s2, st))
                off += float(st[-1]) if len(st) else 0.0
            st = np.concatenate(st_all)
            z = np.concatenate(z_all)
            gr = np.concatenate(g_all)
            xy = np.vstack(xy_all)
            piece_of = np.concatenate(piece_of)
            orig = np.concatenate(orig)
            if ground is not None:
                gd = ground(xy[:, 0], xy[:, 1])
                gr = np.where(np.isfinite(gd), gd, gr)
            flipped = False
            if not pipe.get("fixed") and not C.downhill(st, z):
                flipped = True                                 # flows the other way
                st = st[-1] - st[::-1]
                z, gr, xy = z[::-1], gr[::-1], xy[::-1]
                piece_of, orig = piece_of[::-1], orig[::-1]
            arr = C.profile_arrays(st, z, gr, P[self.TOL_BELLY], P[self.WINDOW],
                                   P[self.SMOOTH])
            results.append({"pipe": pipe, "st": st, "z": z, "g": gr, "xy": xy,
                            "piece_of": piece_of, "orig": orig, "a": arr, "flipped": flipped})
        feedback.setProgress(70)
        self._write_check_only(parameters, context, feedback, crs, xf, zf, P, results,
                               points_mode, feats, names)
        n_check = sum(1 for r in self._co_recs if r["flags"])
        feedback.pushInfo(self.tr(
            "{} {} checked on their own: {} to check, {} with no issue.").format(
            len(self._co_recs), self.tr("runs") if points_mode else self.tr("lines"), n_check,
            len(self._co_recs) - n_check))
        return {self.POINTS: self._ids[self.POINTS], self.CHECK: self._ids[self.CHECK],
                self.OUTPUT: self._ids[self.OUTPUT]}

    def _write_check_only(self, parameters, context, feedback, crs, xf, zf, P, results,
                          points_mode, feats, names):
        D, I, S = QVariant.Double, QVariant.Int, QVariant.String
        self._ids = {}
        el = 1.0 / zf

        def r3(v):
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return round(v, 3) if math.isfinite(v) else None

        def fields(spec):
            fs = QgsFields()
            for n, t in spec:
                fs.append(QgsField(n, t))
            return fs

        fp = fields([("point", I), ("run", S), ("station_ft", D), ("tile_ft", D),
                     ("smooth_ft", D), ("depth_ft", D), ("held_ft", D), ("grade_pct", D),
                     ("status", S), ("flag", S)])
        psink, self._ids[self.POINTS] = self.parameterAsSink(
            parameters, self.POINTS, context, fp, QgsWkbTypes.Point, crs)
        extra = [("ab_status", S), ("ab_points", I), ("ab_length_ft", D), ("ab_grade_pct", D),
                 ("ab_belly_ft", D), ("ab_flat_pct", D), ("ab_reverse_pct", D),
                 ("ab_depth_min", D), ("ab_depth_max", D), ("ab_gaps", I), ("ab_flag", S)]
        fc = fields([("run", S)] + extra)
        csink, self._ids[self.CHECK] = self.parameterAsSink(
            parameters, self.CHECK, context, fc, QgsWkbTypes.LineString, crs)
        fo = QgsFields()
        keep_names = [] if points_mode else [n for n in names if n not in dict(extra)]
        if points_mode:
            fo.append(QgsField("run", S))
        else:
            for f in self.parameterAsSource(parameters, self.LOG, context).fields():
                if f.name() in keep_names:
                    fo.append(f)
        for n, t in extra:
            fo.append(QgsField(n, t))
        osink, self._ids[self.OUTPUT] = self.parameterAsSink(
            parameters, self.OUTPUT, context, fo, QgsWkbTypes.LineStringZM, crs)

        self._co_recs = []
        npt = 0
        for res in results:
            a = res["a"]
            st, z = res["st"], res["z"]
            name = res["pipe"]["name"]
            for j, piece in enumerate(res["pipe"]["pieces"]):
                sel = res["piece_of"] == j
                sub = {k: a[k][sel] for k in ("held", "grade", "grade_se", "depth", "tile_s")}
                rec = C.summarise(st[sel], z[sel], sub, P[self.TOL_ELEV], P[self.TOL_BELLY],
                                  P[self.MIN_SLOPE] / 100.0, P[self.MIN_DEPTH],
                                  P[self.MAX_DEPTH], P[self.GAP],
                                  grade_tol=P[self.GRADE_TOL] / 100.0,
                                  check_gaps=points_mode)
                self._co_recs.append(rec)
                status = "Check" if rec["flags"] else "OK"
                vals = [status, int(sel.sum() if not points_mode else rec["n"]),
                        r3(rec["length"]), r3(100 * rec["grade"]), r3(rec["belly"]),
                        r3(rec["flat_pct"]), r3(rec["reverse_pct"]), r3(rec["depth_min"]),
                        r3(rec["depth_max"]), len(rec["gaps"]), "; ".join(rec["flags"]) or None]
                # the piece's own vertices, in flow order
                o = sel & res["orig"]
                xy, zs, gg = res["xy"][o], a["tile_s"][o], res["g"][o]
                label = str(name if points_mode else
                            piece["attrs"].get("id", piece["attrs"].get(names[0]) if names
                                               else name))
                if len(xy) >= 2:
                    cf = QgsFeature(fc)
                    cf.setGeometry(QgsGeometry.fromPolylineXY(
                        [QgsPointXY(x / xf, y / xf) for x, y in xy]))
                    cf.setAttributes([label] + vals)
                    csink.addFeature(cf, QgsFeatureSink.FastInsert)
                    of = QgsFeature(fo)
                    of.setGeometry(QgsGeometry(QgsLineString(
                        [QgsPoint(x / xf, y / xf, t * el, (m if math.isfinite(m) else t) * el)
                         for (x, y), t, m in zip(xy, zs, gg)])))
                    base = [label] if points_mode else [piece["attrs"].get(n) for n in keep_names]
                    of.setAttributes(base + vals)
                    osink.addFeature(of, QgsFeatureSink.FastInsert)
                # points
                idx = np.nonzero(o)[0]
                for k in idx:
                    pf = []
                    if a["held"][k] > P[self.TOL_BELLY]:
                        pf.append("belly {:.2f} ft".format(a["held"][k]))
                    g, se = a["grade"][k], a["grade_se"][k]
                    lim = P[self.MIN_SLOPE] / 100.0 - P[self.GRADE_TOL] / 100.0 - \
                        2.0 * (se if math.isfinite(se) else 0.0) - 1e-6
                    if math.isfinite(g) and g < lim:
                        pf.append("reverse grade" if g < -P[self.GRADE_TOL] / 100.0 - 2.0 *
                                  (se if math.isfinite(se) else 0.0) else "below minimum grade")
                    d = a["depth"][k]
                    half = 0.5 * P[self.TOL_ELEV]
                    if math.isfinite(d) and d < P[self.MIN_DEPTH] - half:
                        pf.append("shallow")
                    if math.isfinite(d) and d > P[self.MAX_DEPTH] + half:
                        pf.append("deep")
                    npt += 1
                    f = QgsFeature(fp)
                    f.setGeometry(QgsGeometry.fromPointXY(
                        QgsPointXY(res["xy"][k, 0] / xf, res["xy"][k, 1] / xf)))
                    f.setAttributes([npt, label, r3(st[k]), r3(z[k]), r3(a["tile_s"][k]),
                                     r3(a["depth"][k]), r3(a["held"][k]),
                                     r3(100 * a["grade"][k]), "Check" if pf else "OK",
                                     "; ".join(pf) or None])
                    psink.addFeature(f, QgsFeatureSink.FastInsert)
        for key, kind in ((self.POINTS, "points"), (self.CHECK, "lines")):
            dest = self._ids.get(key)
            if dest and context.willLoadLayerOnCompletion(dest):
                _POST[dest] = _Styler(kind)
                context.layerToLoadOnCompletionDetails(dest).setPostProcessor(_POST[dest])
