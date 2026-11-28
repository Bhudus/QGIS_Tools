"""Surface and Blind Inlets: a QGIS Processing algorithm.

Places a surface inlet or a blind inlet at the low point of each depression,
runs a lead pipe from it to the nearest tile, sizes the inlet and the lead,
and checks the tile below the junction for the added flow.
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
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon

from . import inlet_core as C

FT_PER_M = 1.0 / 0.3048
ZUNITS = ["Feet", "Metres"]
CONNECT = ["Mains and submains", "Any tile line"]
TYPES = ["Surface inlet (riser)", "Blind inlet", "Blind inlet where the drainage area is small, "
         "surface inlet elsewhere"]
SURFACE, BLIND = "surface inlet", "blind inlet"
MAX_CELLS = 2_000_000

_POST = {}


def _ft_per_unit(crs):
    try:
        from qgis.core import Qgis
        m = QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), Qgis.DistanceUnit.Meters)
    except (ImportError, AttributeError):
        m = QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), QgsUnitTypes.DistanceMeters)
    return m * FT_PER_M


def _read_dem(layer, feedback):
    """The DEM as (array, valid, geotransform), coarsened if it is very large."""
    from osgeo import gdal
    src = gdal.Open(layer.source())
    if src is None:
        raise QgsProcessingException("Could not open the DEM with GDAL.")
    nx, ny = src.RasterXSize, src.RasterYSize
    if nx * ny > MAX_CELLS:
        f = math.sqrt(nx * ny / MAX_CELLS)
        gt0 = src.GetGeoTransform()
        feedback.pushInfo("The DEM has {:,} cells: it is resampled to cells {:.1f} times "
                          "larger.".format(nx * ny, f))
        src = gdal.Warp("/vsimem/inlet_dem.tif", src, xRes=abs(gt0[1]) * f,
                        yRes=abs(gt0[5]) * f, resampleAlg="average")
    band = src.GetRasterBand(1)
    arr = band.ReadAsArray().astype(float)
    nd = band.GetNoDataValue()
    valid = np.isfinite(arr)
    if nd is not None:
        valid &= arr != nd
    gt = src.GetGeoTransform()
    src = None
    try:
        gdal.Unlink("/vsimem/inlet_dem.tif")
    except Exception:  # noqa: BLE001
        pass
    return arr, valid, gt


class _Styler(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsVectorLayer):
            return
        try:
            if self.kind == "inlets":
                cats = []
                for value, colour, shape, label in (
                        (SURFACE, "#1f78b4", "circle", "Surface inlet"),
                        (BLIND, "#33a02c", "square", "Blind inlet")):
                    sym = QgsMarkerSymbol.createSimple({"name": shape, "color": colour,
                                                        "size": "3.2",
                                                        "outline_color": "#000000"})
                    cats.append(QgsRendererCategory(value, sym, label))
                layer.setRenderer(QgsCategorizedSymbolRenderer("type", cats))
            elif self.kind == "leads":
                layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple(
                    {"color": "#1f78b4", "width": "0.7", "line_style": "dash"})))
            else:
                cats = []
                for value, colour, label in (("OK", "#33a02c", "Carries the inlet flow"),
                                             ("Over capacity", "#e31a1c", "Over capacity")):
                    sym = QgsLineSymbol.createSimple({"color": colour, "width": "1.2"})
                    cats.append(QgsRendererCategory(value, sym, label))
                layer.setRenderer(QgsCategorizedSymbolRenderer("status", cats))
            layer.triggerRepaint()
        except Exception:  # noqa: BLE001  (styling is a nicety)
            pass


class InletPlacementAlgorithm(QgsProcessingAlgorithm):
    DEPRESSIONS = "DEPRESSIONS"
    DEM = "DEM"
    Z_UNITS = "Z_UNITS"
    NETWORK = "NETWORK"
    CONNECT = "CONNECT"
    TYPE = "TYPE"
    BLIND_MAX_AC = "BLIND_MAX_AC"
    DRAWDOWN = "DRAWDOWN"
    BLIND_DRAWDOWN = "BLIND_DRAWDOWN"
    COEF = "COEF"
    FILTER_K = "FILTER_K"
    MIN_COVER = "MIN_COVER"
    MIN_SLOPE = "MIN_SLOPE"
    MAX_SLOPE = "MAX_SLOPE"
    MAX_LEAD = "MAX_LEAD"
    MIN_SIZE = "MIN_SIZE"
    MATERIAL = "MATERIAL"
    CD = "CD"
    MIN_VOLUME = "MIN_VOLUME"
    MIN_ORIFICE = "MIN_ORIFICE"
    INLETS = "INLETS"
    LEADS = "LEADS"
    CHECK = "CHECK"

    def tr(self, s):
        return QCoreApplication.translate("InletPlacement", s)

    def createInstance(self):
        return InletPlacementAlgorithm()

    def name(self):
        return "inletplacement"

    def displayName(self):
        return self.tr("Surface and blind inlets")

    def group(self):
        return ""

    def groupId(self):
        return ""

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def shortHelpString(self):
        return self.tr(
            "<p>Places an inlet at the low point of each depression, connects it to the "
            "nearest tile with a lead pipe, sizes both, and checks the tile below the junction "
            "for the added flow.</p>"
            "<p><b>Depressions</b>: polygons, such as the Depressions output of Depressions "
            "and Laylines. For each one the tool finds, from the DEM, the lowest point (the "
            "inlet), the pond level and depth, the volume of the whole pond, and its direct "
            "drainage area (the ground whose runoff reaches it; runoff from depressions that "
            "fill and spill into it is not added).</p>"
            "<p><b>Design flow</b>: the pond drained within the drawdown time, or the drainage "
            "area at the surface-water coefficient (default 1/2 in/day; 0 = not used), "
            "whichever is larger.</p>"
            "<p><b>Inlet</b>: a <b>surface inlet</b> (riser) gets an orifice plate sized to "
            "pass the design flow at full pond depth, no smaller than the smallest orifice "
            "(1 in, against clogging; none is needed when it would be as large as the "
            "riser); a <b>blind inlet</b> (a gravel trench capped with "
            "sand) gets the filter area to pass its flow through the sand, with its own, "
            "longer drawdown time.</p>"
            "<p><b>Lead pipe</b>: straight to the nearest main or submain (or any tile line), "
            "joining at the tile's elevation there (Z of the network). The inlet end is at "
            "the minimum cover when that gives a grade between the minimum and maximum slopes; "
            "with too little fall the lead runs at the minimum slope and is flagged. Its size "
            "is the smallest that carries the design flow full (Manning's n as Routine K).</p>"
            "<p><b>Tile check</b>: down from each junction to the outlet, each pipe's design "
            "flow (CUM_SEG_FLOW) plus the inlet flows above it, against its full-flow "
            "capacity at SIZE_SLOPE, with the size it would need when over capacity. Needs the "
            "Routine K fields of an Illini Drainage Network.</p>"
            "<p>Attributes are in feet (elevations too), inches and cubic feet per second; the "
            "DEM and network may be in feet or metres. The Lead pipes carry the invert (Z) and "
            "ground (M) in the DEM's units, like the network, so Tile Profile can draw them."
            "</p>")

    def _num(self, name, label, default, lo, hi, advanced=False):
        p = QgsProcessingParameterNumber(name, label, QgsProcessingParameterNumber.Double,
                                         defaultValue=default, minValue=lo, maxValue=hi)
        if advanced:
            p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(p)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.DEPRESSIONS, self.tr("Depressions (polygons, e.g. from Depressions and "
                                      "Laylines)"), [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, self.tr("DEM (bare earth, not sink-filled)")))
        self.addParameter(QgsProcessingParameterEnum(
            self.Z_UNITS, self.tr("Elevation units of the DEM and the network"), options=ZUNITS,
            defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.NETWORK, self.tr("Drainage network (tile lines with Z = tile elevation)"),
            [QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterEnum(
            self.CONNECT, self.tr("Connect the inlets to"), options=CONNECT, defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.TYPE, self.tr("Inlet type"), options=TYPES, defaultValue=2))
        self._num(self.BLIND_MAX_AC, self.tr("Blind inlets for drainage areas up to (acres)"),
                  2.0, 0.0, 1000.0)
        self._num(self.DRAWDOWN, self.tr("Surface inlet: drain the pond within (hours)"), 24.0,
                  1.0, 1000.0)
        self._num(self.BLIND_DRAWDOWN, self.tr("Blind inlet: drain the pond within (hours)"),
                  48.0, 1.0, 1000.0)
        self._num(self.COEF, self.tr("Surface-water coefficient over the drainage area "
                                     "(in/day; 0 = not used)"), 0.5, 0.0, 100.0)
        self._num(self.MIN_COVER, self.tr("Lead pipe: minimum cover (ft)"), 2.5, 0.0, 20.0)
        self._num(self.MIN_SLOPE, self.tr("Lead pipe: minimum slope (%)"), 0.2, 0.01, 50.0)
        self._num(self.MAX_SLOPE, self.tr("Lead pipe: maximum slope (%)"), 2.0, 0.01, 50.0)
        self._num(self.MAX_LEAD, self.tr("Lead pipe: longest (ft)"), 600.0, 1.0, 100000.0)
        self._num(self.MIN_SIZE, self.tr("Lead pipe: smallest size (in)"), 4.0, 3.0, 42.0,
                  advanced=True)
        p = QgsProcessingParameterEnum(self.MATERIAL, self.tr("Lead pipe material"),
                                       options=C.MATERIALS, defaultValue=0)
        p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(p)
        self._num(self.FILTER_K, self.tr("Blind inlet: sand filter conductivity (ft/day)"),
                  20.0, 0.01, 10000.0, advanced=True)
        self._num(self.CD, self.tr("Surface inlet: orifice coefficient"), 0.6, 0.1, 1.0,
                  advanced=True)
        self._num(self.MIN_ORIFICE, self.tr("Surface inlet: smallest orifice (in)"), 1.0, 0.1,
                  12.0, advanced=True)
        self._num(self.MIN_VOLUME, self.tr("Skip ponds smaller than (cu yd)"), 0.0, 0.0, 1e9,
                  advanced=True)
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.INLETS, self.tr("Inlets"), QgsProcessing.TypeVectorPoint))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.LEADS, self.tr("Lead pipes"), QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.CHECK, self.tr("Tile check"), QgsProcessing.TypeVectorLine))

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
    def _fields(spec):
        fs = QgsFields()
        for name, typ in spec:
            fs.append(QgsField(name, typ))
        return fs

    def _read_network(self, source, crs, context, xf):
        names = [f.name() for f in source.fields()]
        tr = QgsCoordinateTransform(source.sourceCrs(), crs, context.transformContext())
        lines, no_z = [], 0
        for f in source.getFeatures():
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            g.transform(tr)
            hz = QgsWkbTypes.hasZ(g.wkbType())
            hm = QgsWkbTypes.hasM(g.wkbType())
            attrs = {n: f[n] for n in names}
            for part in g.constParts():
                pts = []
                for v in part.vertices():
                    z = v.z() if hz else float("nan")
                    m = v.m() if hm else float("nan")
                    pts.append((v.x() * xf, v.y() * xf, z, m))
                if len(pts) >= 2:
                    a = np.array(pts, float)
                    lines.append({"xyz": a[:, :3], "m": a[:, 3], "attrs": attrs})
            if not hz:
                no_z += 1
        return lines, names, no_z

    # ----------------------------------------------------------------- run
    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        if dem_layer is None:
            raise QgsProcessingException(self.tr("Could not load the DEM."))
        crs = dem_layer.crs()
        if not crs.isValid() or crs.isGeographic():
            raise QgsProcessingException(self.tr(
                "The DEM must be in a projected coordinate system (metres or feet)."))
        xf = _ft_per_unit(crs)                          # CRS unit -> ft
        zf = 1.0 if self.parameterAsEnum(parameters, self.Z_UNITS, context) == 0 else FT_PER_M
        P = {k: self.parameterAsDouble(parameters, k, context) for k in (
            self.BLIND_MAX_AC, self.DRAWDOWN, self.BLIND_DRAWDOWN, self.COEF, self.FILTER_K,
            self.MIN_COVER, self.MIN_SLOPE, self.MAX_SLOPE, self.MAX_LEAD, self.MIN_SIZE, self.CD,
            self.MIN_VOLUME, self.MIN_ORIFICE)}
        material = self.parameterAsEnum(parameters, self.MATERIAL, context)
        itype = self.parameterAsEnum(parameters, self.TYPE, context)
        mains_only = self.parameterAsEnum(parameters, self.CONNECT, context) == 0
        min_s, max_s = P[self.MIN_SLOPE] / 100.0, P[self.MAX_SLOPE] / 100.0
        if max_s < min_s:
            raise QgsProcessingException(self.tr("The maximum lead slope is below the minimum."))

        # DEM in feet
        arr, valid, gt = _read_dem(dem_layer, feedback)
        cell = 0.5 * (abs(gt[1]) + abs(gt[5])) * xf
        grid = C.Grid(gt[0] * xf, gt[3] * xf, cell, arr.shape[0], arr.shape[1])
        dem = np.where(valid, arr * zf, 0.0)
        feedback.pushInfo(self.tr("DEM: {:,} x {:,} cells of {:.2f} ft.").format(
            grid.ncols, grid.nrows, cell))

        # depressions on the DEM grid
        dsrc = self.parameterAsSource(parameters, self.DEPRESSIONS, context)
        if dsrc is None:
            raise QgsProcessingException(self.tr("Could not load the depressions."))
        dtr = QgsCoordinateTransform(dsrc.sourceCrs(), crs, context.transformContext())
        dnames = [f.name() for f in dsrc.fields()]
        labels = np.zeros(grid.shape, dtype=np.int64)
        deps = []
        for f in dsrc.getFeatures():
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            g.transform(dtr)
            m = C.rasterize_rings(grid, self._rings(g, xf)) & valid & (labels == 0)
            if not m.any():
                continue
            deps.append({"dep_id": f["dep_id"] if "dep_id" in dnames else len(deps) + 1})
            labels[m] = len(deps)
        if not deps:
            raise QgsProcessingException(self.tr("No depression covers a DEM cell."))
        n = len(deps)
        feedback.pushInfo(self.tr("{} depression(s).").format(n))
        feedback.setProgressText(self.tr("Ponds"))
        stats = C.pond_stats(dem, valid, labels, n, cell)
        feedback.setProgress(25)
        feedback.setProgressText(self.tr("Drainage areas"))
        areas, _ = C.drainage_areas(dem, valid, labels, n, cell,
                                    progress=lambda f: feedback.setProgress(25 + 35 * f))
        feedback.setProgress(60)

        # network
        nsrc = self.parameterAsSource(parameters, self.NETWORK, context)
        if nsrc is None:
            raise QgsProcessingException(self.tr("Could not load the drainage network."))
        lines, nnames, no_z = self._read_network(nsrc, crs, context, xf)
        if not lines:
            raise QgsProcessingException(self.tr("The network has no lines."))
        if no_z:
            raise QgsProcessingException(self.tr(
                "{} network line(s) have no Z (tile elevation): use a Drainage Network from "
                "the Illini layout or design tools.").format(no_z))
        for L in lines:
            L["xyz"][:, 2] *= zf
            L["m"] = L["m"] * zf
        kind_f = "kind" in nnames
        eligible = [True] * len(lines)
        if mains_only and kind_f:
            eligible = [str(L["attrs"].get("kind")).lower() in ("main", "submain")
                        for L in lines]
            if not any(eligible):
                feedback.reportError(self.tr("The network has no mains or submains: the inlets "
                                             "connect to any tile line."))
                eligible = [True] * len(lines)
        check_ok = all(k in nnames for k in ("id", "ds_link", "NOMINAL", "SIZE_SLOPE",
                                              "CUM_SEG_FLOW"))
        if not check_ok:
            feedback.reportError(self.tr("The network has no Routine K fields (id, ds_link, "
                                         "NOMINAL, SIZE_SLOPE, CUM_SEG_FLOW): the tile below "
                                         "the inlets is not checked."))

        def ground_at(xs, ys):
            i, j, ok = grid.index(xs, ys)
            gz = dem[i, j]
            return np.where(ok & valid[i, j], gz, np.nan)

        # ------------------------------------------------------ inlets
        inlets = []
        for k, (d, s) in enumerate(zip(deps, stats)):
            if s is None:
                continue
            vol = s["volume"]
            if vol / C.CU_FT_PER_CU_YD < P[self.MIN_VOLUME]:
                continue
            drain_ac = areas[k] / C.SQ_FT_PER_ACRE
            kind = SURFACE
            if itype == 1 or (itype == 2 and drain_ac <= P[self.BLIND_MAX_AC]):
                kind = BLIND
            hours = P[self.BLIND_DRAWDOWN] if kind == BLIND else P[self.DRAWDOWN]
            q = vol / (hours * 3600.0)
            q_area = areas[k] * P[self.COEF] / 12.0 / 86400.0
            governs = "pond"
            if q_area > q:
                q, governs = q_area, "drainage area"
            x, y = grid.centre(s["i"], s["j"])
            dist, li, si, t, jp, jz = C.nearest_on_lines(x, y, lines, eligible)
            rec = {"dep": d["dep_id"], "type": kind, "x": x, "y": y, "ground": s["low"],
                   "level": s["level"], "depth": s["depth"], "vol": vol,
                   "pond_ac": s["pond_area"] / C.SQ_FT_PER_ACRE, "drain_ac": drain_ac,
                   "q": q, "hours": hours, "governs": governs, "flags": [], "lead": None,
                   "link": None, "link_kind": None}
            if li is None or dist > P[self.MAX_LEAD]:
                rec["flags"].append("no tile within {:g} ft".format(P[self.MAX_LEAD]))
            else:
                lead = C.design_lead((x, y), s["low"], jp, jz, ground_at, P[self.MIN_COVER],
                                     min_s, max_s, max(cell, 2.0))
                size = C.size_for(q, lead["slope"], material, smallest=P[self.MIN_SIZE])
                if size is None:
                    rec["flags"].append("design flow larger than a 42 in lead")
                lead["size"] = size
                rec["lead"] = lead
                rec["flags"].extend(lead["flags"])
                la = lines[li]["attrs"]
                rec["link"] = la.get("id")
                rec["link_kind"] = la.get("kind")
            if kind == SURFACE:
                riser = max(rec["lead"]["size"] or 6, 6) if rec["lead"] else 6
                od = C.orifice_diameter(q, s["depth"], P[self.CD])
                if od is not None:
                    od = max(od, P[self.MIN_ORIFICE])
                rec["riser"] = riser
                rec["orifice"] = od if (od is not None and od < riser) else None
                rec["filter"] = None
            else:
                rec["riser"] = None
                rec["orifice"] = None
                rec["filter"] = q / (P[self.FILTER_K] / 86400.0)
            inlets.append(rec)
        feedback.setProgress(80)

        # --------------------------------------------------- tile check
        checks = {}
        links_by_id = {}
        if check_ok:
            for L in lines:
                a = L["attrs"]
                try:
                    lid = int(a["id"])
                except (TypeError, ValueError):
                    continue
                if lid in links_by_id:
                    continue
                mat = str(a.get("PIPE_TYPE") or "Single Wall")
                links_by_id[lid] = {
                    "ds_link": int(a["ds_link"] or 0),
                    "flow": float(a["CUM_SEG_FLOW"] or 0.0),
                    "size": int(a["NOMINAL"] or 0),
                    "slope": float(a["SIZE_SLOPE"] or 0.0),
                    "material": C.MATERIALS.index(mat) if mat in C.MATERIALS else 0,
                    "kind": a.get("kind"), "geom": L}
            joins = [(int(r["link"]), r["q"]) for r in inlets
                     if r["link"] is not None and int(r["link"]) in links_by_id]
            checks = C.tile_check(links_by_id, joins)
            for r in inlets:
                if r["link"] is None or int(r["link"]) not in links_by_id:
                    r["tile"] = None
                    continue
                path = C.downstream(links_by_id, int(r["link"]))
                over = [k for k in path if checks[k]["ratio"] > 1.0]
                r["tile"] = "OK" if not over else "over capacity ({} pipe(s))".format(len(over))
        else:
            for r in inlets:
                r["tile"] = None

        self._write(parameters, context, feedback, crs, xf, zf, inlets, checks, links_by_id)
        n_s = sum(1 for r in inlets if r["type"] == SURFACE)
        n_b = len(inlets) - n_s
        over = sum(1 for c in checks.values() if c["ratio"] > 1.0)
        feedback.pushInfo(self.tr(
            "Inlets: {} surface, {} blind; design flow {:.3f} cfs in all; {:,.0f} ft of lead "
            "pipe. Tile check: {} pipe(s) below the inlets, {} over capacity.").format(
            n_s, n_b, sum(r["q"] for r in inlets),
            sum(r["lead"]["length"] for r in inlets if r["lead"]), len(checks), over))
        flagged = [r for r in inlets if r["flags"]]
        if flagged:
            feedback.reportError(self.tr("{} inlet(s) flagged; see the flag field.").format(
                len(flagged)))
        return {self.INLETS: self._ids.get(self.INLETS), self.LEADS: self._ids.get(self.LEADS),
                self.CHECK: self._ids.get(self.CHECK)}

    # -------------------------------------------------------------- output
    def _write(self, parameters, context, feedback, crs, xf, zf, inlets, checks, links_by_id):
        D, I, S = QVariant.Double, QVariant.Int, QVariant.String
        self._ids = {}
        f_in = self._fields([
            ("inlet_id", I), ("dep_id", I), ("type", S), ("ground_ft", D), ("pond_elev_ft", D),
            ("pond_dep_ft", D), ("pond_cu_yd", D), ("pond_ac", D), ("drain_ac", D),
            ("q_cfs", D), ("governs", S), ("drawdown_h", D), ("riser_in", I),
            ("orifice_in", D), ("filter_sqft", D), ("lead_ft", D), ("lead_pct", D),
            ("lead_in", I), ("inv_in_ft", D), ("inv_tile_ft", D), ("cover_ft", D),
            ("joins_id", I), ("joins_kind", S), ("tile_check", S), ("flag", S)])
        sink, self._ids[self.INLETS] = self.parameterAsSink(
            parameters, self.INLETS, context, f_in, QgsWkbTypes.Point, crs)
        f_lead = self._fields([("inlet_id", I), ("type", S), ("length_ft", D), ("slope_pct", D),
                               ("size_in", I), ("q_cfs", D), ("inv_in_ft", D), ("inv_tile_ft", D),
                               ("cover_ft", D), ("joins_id", I)])
        lsink, self._ids[self.LEADS] = self.parameterAsSink(
            parameters, self.LEADS, context, f_lead, QgsWkbTypes.LineStringZM, crs)
        f_chk = self._fields([("id", I), ("kind", S), ("NOMINAL", I), ("slope_pct", D),
                              ("flow_cfs", D), ("inlet_cfs", D), ("total_cfs", D),
                              ("cap_cfs", D), ("ratio", D), ("needs_in", I), ("status", S)])
        csink, self._ids[self.CHECK] = self.parameterAsSink(
            parameters, self.CHECK, context, f_chk, QgsWkbTypes.LineString, crs)
        el = 1.0 / zf                                     # ft -> elevation units

        def r3(v):
            return None if v is None or not math.isfinite(v) else round(v, 3)

        for n, r in enumerate(inlets, 1):
            lead = r["lead"]
            f = QgsFeature(f_in)
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r["x"] / xf, r["y"] / xf)))
            f.setAttributes([
                n, int(r["dep"]) if r["dep"] is not None else None, r["type"],
                r3(r["ground"]), r3(r["level"]), r3(r["depth"]),
                r3(r["vol"] / C.CU_FT_PER_CU_YD), r3(r["pond_ac"]), r3(r["drain_ac"]),
                round(r["q"], 4), r["governs"], r["hours"], r["riser"],
                r3(r["orifice"]), r3(r["filter"]),
                r3(lead["length"]) if lead else None,
                r3(lead["slope"] * 100) if lead else None,
                lead["size"] if lead else None,
                r3(lead["inv_in"]) if lead else None, r3(lead["inv_out"]) if lead else None,
                r3(lead["min_cover"]) if lead else None,
                int(r["link"]) if r["link"] is not None else None, r["link_kind"], r["tile"],
                "; ".join(r["flags"]) or None])
            sink.addFeature(f, QgsFeatureSink.FastInsert)
            if lead is not None and lead["pts"] is not None:
                pts = lead["pts"]
                ls = QgsLineString([QgsPoint(x / xf, y / xf, zz * el, (mm if np.isfinite(mm)
                                                                         else zz) * el)
                                    for x, y, zz, mm in pts])
                g = QgsGeometry(ls)
                lf = QgsFeature(f_lead)
                lf.setGeometry(g)
                lf.setAttributes([n, r["type"], r3(lead["length"]), r3(lead["slope"] * 100),
                                  lead["size"], round(r["q"], 4), r3(lead["inv_in"]),
                                  r3(lead["inv_out"]), r3(lead["min_cover"]),
                                  int(r["link"]) if r["link"] is not None else None])
                lsink.addFeature(lf, QgsFeatureSink.FastInsert)
        for k, c in sorted(checks.items()):
            L = links_by_id[k]
            xy = L["geom"]["xyz"][:, :2] / xf
            f = QgsFeature(f_chk)
            f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in xy]))
            f.setAttributes([k, L["kind"], L["size"], r3(L["slope"] * 100), r3(L["flow"]),
                             round(c["added"], 4), round(c["total"], 4), round(c["capacity"], 4),
                             r3(c["ratio"]), c["needs"],
                             "OK" if c["ratio"] <= 1.0 else "Over capacity"])
            csink.addFeature(f, QgsFeatureSink.FastInsert)
        for key, kind in ((self.INLETS, "inlets"), (self.LEADS, "leads"), (self.CHECK, "check")):
            dest = self._ids.get(key)
            if dest and context.willLoadLayerOnCompletion(dest):
                _POST[dest] = _Styler(kind)
                context.layerToLoadOnCompletionDetails(dest).setPostProcessor(_POST[dest])
