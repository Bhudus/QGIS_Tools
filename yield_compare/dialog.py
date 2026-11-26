# -*- coding: utf-8 -*-
"""Main dialog of the Yield Compare plugin (replaces frmShapeToXYZ)."""

import csv
import html
import math

import numpy as np
from qgis.core import (Qgis, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsFeature, QgsFeatureRequest,
                       QgsFieldProxyModel, QgsGeometry,
                       QgsGraduatedSymbolRenderer, QgsMapLayerProxyModel,
                       QgsMarkerSymbol, QgsPointXY, QgsProject,
                       QgsRendererRange, QgsSpatialIndex, QgsSymbol,
                       QgsUnitTypes, QgsVectorLayer)
from qgis.gui import QgsColorButton, QgsFieldComboBox, QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                                 QDialog, QDoubleSpinBox, QFileDialog,
                                 QFormLayout, QGridLayout, QGroupBox,
                                 QHBoxLayout, QHeaderView, QLabel,
                                 QMessageBox, QPushButton, QScrollArea,
                                 QSpinBox, QTableWidget, QTableWidgetItem,
                                 QTabWidget, QVBoxLayout, QWidget)

from . import stats_core as sc

try:  # matplotlib ships with QGIS on Windows/macOS and most Linux packages
    from matplotlib.figure import Figure
    try:
        from matplotlib.backends.backend_qtagg import (
            FigureCanvasQTAgg as FigureCanvas,
            NavigationToolbar2QT as NavToolbar)
    except ImportError:
        from matplotlib.backends.backend_qt5agg import (
            FigureCanvasQTAgg as FigureCanvas,
            NavigationToolbar2QT as NavToolbar)
    HAVE_MPL = True
except Exception:  # pragma: no cover
    HAVE_MPL = False


# ---------------------------------------------------------------------------
# Qt5 / Qt6 and QGIS 3 / QGIS 4 enum compatibility
# ---------------------------------------------------------------------------

def _first(*getters):
    for g in getters:
        try:
            return g()
        except AttributeError:
            continue
    raise AttributeError("No compatible enum found")


LAYER_FILTER_VECTOR = _first(lambda: Qgis.LayerFilter.VectorLayer,
                             lambda: QgsMapLayerProxyModel.Filter.VectorLayer,
                             lambda: QgsMapLayerProxyModel.VectorLayer)
LAYER_FILTER_LINE = _first(lambda: Qgis.LayerFilter.LineLayer,
                           lambda: QgsMapLayerProxyModel.Filter.LineLayer,
                           lambda: QgsMapLayerProxyModel.LineLayer)
FIELD_FILTER_NUMERIC = _first(lambda: QgsFieldProxyModel.Filter.Numeric,
                              lambda: QgsFieldProxyModel.Numeric)
NO_GEOMETRY = _first(lambda: Qgis.FeatureRequestFlag.NoGeometry,
                     lambda: QgsFeatureRequest.Flag.NoGeometry,
                     lambda: QgsFeatureRequest.NoGeometry)
STORE_GEOMETRIES = _first(lambda: QgsSpatialIndex.Flag.FlagStoreFeatureGeometries,
                          lambda: QgsSpatialIndex.FlagStoreFeatureGeometries)
UNIT_FEET = _first(lambda: Qgis.DistanceUnit.Feet,
                   lambda: QgsUnitTypes.DistanceUnit.DistanceFeet,
                   lambda: QgsUnitTypes.DistanceFeet)

BIG = 1e12


def fmt(v, nd=3):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, int) or (isinstance(v, float) and math.isfinite(v)
                              and v.is_integer() and abs(v) < 1e9):
        return str(int(v))
    if not math.isfinite(v):
        return "n/a"
    return f"{v:.{nd}f}"


def fmt_p(p):
    if p is None or not math.isfinite(p):
        return "n/a"
    return "< 0.0001" if p < 1e-4 else f"{p:.4f}"


def fmt_p_html(p):
    return fmt_p(p).replace("<", "&lt;")


def singular(unit):
    return {"meters": "meter", "feet": "foot", "kilometers": "kilometer",
            "miles": "mile", "yards": "yard", "centimeters": "centimeter"}.get(unit, unit)


def fmt_ci(d, nd=1):
    return f"{d['diff']:+.{nd}f}  ({d['lo']:+.{nd}f} to {d['hi']:+.{nd}f})"


def unit_name(crs):
    try:
        return QgsUnitTypes.toString(crs.mapUnits())
    except Exception:
        return "map units"


def working_crs(layers, network=None):
    """A projected CRS for distances: the network's, a field layer's, or UTM."""
    for lyr in ([network] if network else []) + layers:
        if lyr is not None and lyr.crs().isValid() and not lyr.crs().isGeographic():
            return lyr.crs()
    lyr = next((l for l in layers if l is not None), None)
    if lyr is None:
        raise ValueError("Choose a layer for Field 1 and Field 2.")
    wgs = QgsCoordinateReferenceSystem("EPSG:4326")
    ext = QgsCoordinateTransform(lyr.crs(), wgs, QgsProject.instance()) \
        .transformBoundingBox(lyr.extent())
    lon, lat = ext.center().x(), ext.center().y()
    zone = min(60, max(1, int((lon + 180.0) // 6) + 1))
    return QgsCoordinateReferenceSystem(f"EPSG:{(32600 if lat >= 0 else 32700) + zone}")


def tile_distances(network, laterals_only, selected_only, crs, point_sets):
    """Distance from every point to the nearest tile line, in crs units."""
    xform = QgsCoordinateTransform(network.crs(), crs, QgsProject.instance())
    req = QgsFeatureRequest()
    if laterals_only and network.fields().indexFromName("kind") >= 0:
        req.setFilterExpression("\"kind\" = 'lateral'")
    feats = network.getSelectedFeatures(req) if selected_only else network.getFeatures(req)
    index = QgsSpatialIndex(STORE_GEOMETRIES)
    geoms = {}
    for f in feats:
        g = QgsGeometry(f.geometry())
        if g.isNull() or g.isEmpty():
            continue
        g.transform(xform)
        nf = QgsFeature(f.id())
        nf.setGeometry(g)
        index.addFeature(nf)
        geoms[f.id()] = g
    if not geoms:
        raise ValueError("The tile network has no lines to measure distances to"
                         + (" (no lines with kind = 'lateral')." if laterals_only else "."))
    out = []
    for xy in point_sets:
        d = np.empty(len(xy))
        for i, (x, y) in enumerate(xy):
            p = QgsPointXY(float(x), float(y))
            pg = QgsGeometry.fromPointXY(p)
            ids = index.nearestNeighbor(p, 1)
            d[i] = min(geoms[k].distance(pg) for k in ids) if ids else np.inf
            if i % 5000 == 4999:
                QApplication.processEvents()
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# One "Field" input panel
# ---------------------------------------------------------------------------

class FieldPanel(QGroupBox):
    def __init__(self, title, parent=None):
        super().__init__(title, parent)
        form = QFormLayout(self)

        self.layer = QgsMapLayerComboBox()
        self.layer.setFilters(LAYER_FILTER_VECTOR)
        self.layer.setAllowEmptyLayer(True)
        form.addRow("Layer", self.layer)

        self.selected_only = QCheckBox("Selected features only")
        form.addRow("", self.selected_only)

        self.yield_field = QgsFieldComboBox()
        self.yield_field.setFilters(FIELD_FILTER_NUMERIC)
        self.scale_y = self._scale_box()
        form.addRow("Yield column", self._pair(self.yield_field, self.scale_y))

        self.reg_field = QgsFieldComboBox()
        self.reg_field.setFilters(FIELD_FILTER_NUMERIC)
        self.reg_field.setAllowEmptyFieldName(True)
        self.scale_x = self._scale_box()
        self.reg_label = QLabel("Regressor column")
        form.addRow(self.reg_label, self._pair(self.reg_field, self.scale_x))

        self.con_field = QgsFieldComboBox()
        self.con_field.setFilters(FIELD_FILTER_NUMERIC)
        self.con_field.setAllowEmptyFieldName(True)
        self.con_label = QLabel("Constraining column")
        form.addRow(self.con_label, self.con_field)

        self.pass_field = QgsFieldComboBox()
        self.pass_field.setAllowEmptyFieldName(True)
        self.pass_field.setToolTip(
            "Harvest pass (track) number from the yield monitor, e.g. Pass_Num. "
            "Points in the same pass share an offset (header, flow delay, operator); "
            "with this column the spatial analysis allows for it. Important for the "
            "distance-to-tile analysis when laterals run parallel to the passes.")
        form.addRow("Harvest pass column", self.pass_field)

        self.exceed = QDoubleSpinBox()
        self.exceed.setRange(0.0, 1.0)
        self.exceed.setDecimals(3)
        self.exceed.setSingleStep(0.05)
        self.exceed.setValue(0.9)
        self.exceed.setToolTip("Report the yield exceeded with this probability "
                               "(Gaussian and empirical distribution).")
        form.addRow("Exceedance probability", self.exceed)

        for cb in (self.yield_field, self.reg_field, self.con_field, self.pass_field):
            self.layer.layerChanged.connect(cb.setLayer)
        self.layer.layerChanged.connect(self._guess_yield_field)
        self.layer.setLayer(None)

    @staticmethod
    def _scale_box():
        s = QDoubleSpinBox()
        s.setRange(-1e6, 1e6)
        s.setDecimals(4)
        s.setValue(1.0)
        s.setToolTip("Scale factor applied to the column values")
        s.setPrefix("× ")
        return s

    @staticmethod
    def _pair(a, b):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(a, 3)
        h.addWidget(b, 1)
        return w

    def _guess_yield_field(self, layer):
        if layer is None:
            return
        for f in layer.fields():
            n = f.name().lower()
            if ("yld" in n or "yield" in n) and f.isNumeric():
                self.yield_field.setField(f.name())
                break
        self.pass_field.setField("")
        for f in layer.fields():
            if "pass" in f.name().lower():
                self.pass_field.setField(f.name())
                break

    def label(self):
        lyr = self.layer.currentLayer()
        return lyr.name() if lyr else self.title()

    def read(self, use_reg, use_con, z_limits, thresholds, crs=None):
        """Read and filter the attribute values.

        Returns arrays y, x, xy, g; xy (point locations in crs) is None when
        no crs is given or the layer has no geometry; g (harvest pass) is
        None without a pass column.
        """
        lyr = self.layer.currentLayer()
        if lyr is None:
            raise ValueError(f"{self.title()}: choose a layer.")
        yname = self.yield_field.currentField()
        if not yname:
            raise ValueError(f"{self.title()}: choose a yield column.")
        xname = self.reg_field.currentField() if use_reg else ""
        zname = self.con_field.currentField() if use_con else ""
        if use_reg and not xname:
            raise ValueError(f"{self.title()}: choose a regressor column.")
        if use_con and not zname:
            raise ValueError(f"{self.title()}: choose a constraining column.")

        gname = self.pass_field.currentField() if crs is not None else ""
        names = [n for n in (yname, xname, zname, gname) if n]
        idx = [lyr.fields().indexFromName(n) for n in names]
        want_xy = crs is not None and lyr.isSpatial()
        req = QgsFeatureRequest()
        if not want_xy:
            req.setFlags(NO_GEOMETRY)
        req.setSubsetOfAttributes(idx)
        xform = QgsCoordinateTransform(lyr.crs(), crs, QgsProject.instance()) \
            if want_xy else None
        feats = (lyr.getSelectedFeatures(req) if self.selected_only.isChecked()
                 else lyr.getFeatures(req))
        ys, xs, zs, pts, gs = [], [], [], [], []
        ig = lyr.fields().indexFromName(gname) if gname else -1
        iy = idx[0]
        ix = lyr.fields().indexFromName(xname) if xname else -1
        iz = lyr.fields().indexFromName(zname) if zname else -1
        for f in feats:
            ys.append(f[iy])
            if ix >= 0:
                xs.append(f[ix])
            if iz >= 0:
                zs.append(f[iz])
            if want_xy:
                pts.append(self._point(f, xform))
            if ig >= 0:
                v = f[ig]
                gs.append(None if v is None or str(v) in ("NULL", "") else v)
        out = sc.prepare(ys, xs if ix >= 0 else None, zs if iz >= 0 else None,
                         self.scale_y.value(), self.scale_x.value(),
                         z_limits if iz >= 0 else None, thresholds,
                         pts if want_xy else None, gs if ig >= 0 else None)
        return out if len(out) == 4 else (*out, None)

    @staticmethod
    def _point(f, xform):
        g = f.geometry()
        if g is None or g.isNull() or g.isEmpty():
            return None
        try:
            p = g.asPoint()
        except Exception:
            p = g.centroid().asPoint()
        try:
            p = xform.transform(p)
        except Exception:
            return None
        return (p.x(), p.y())

    def yield_expression(self):
        name = self.yield_field.currentField()
        s = self.scale_y.value()
        q = '"' + name.replace('"', '""') + '"'
        return q if s == 1 else f"{q} * {s!r}"


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class YieldCompareDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.result = None
        self.setWindowTitle("Yield Compare – Yield Statistics")
        self.resize(1300, 820)

        root = QHBoxLayout(self)

        # ---------------- left: inputs ----------------
        left = QWidget()
        lv = QVBoxLayout(left)
        self.f1 = FieldPanel("Field 1")
        self.f2 = FieldPanel("Field 2")
        lv.addWidget(self.f1)
        lv.addWidget(self.f2)

        opt = QGroupBox("Analysis options")
        og = QGridLayout(opt)
        self.chk_spatial = QCheckBox("Allow for spatial autocorrelation (recommended)")
        self.chk_spatial.setChecked(True)
        self.chk_spatial.setToolTip(
            "Neighbouring yield-monitor points are not independent. Without this, "
            "thousands of points make almost any difference 'significant'. The "
            "plugin fits a variogram to each field and uses it to give honest "
            "p-values and confidence intervals.")
        self.chk_reg = QCheckBox("Perform regression analysis")
        self.chk_rls = QCheckBox("Reweighted least squares analysis")
        self.iter = QSpinBox()
        self.iter.setRange(10, 20000)
        self.iter.setValue(500)
        self.iter.setToolTip("Number of trial slopes in the least-median-of-squares search")
        og.addWidget(self.chk_spatial, 0, 0, 1, 3)
        og.addWidget(self.chk_reg, 1, 0, 1, 2)
        og.addWidget(self.chk_rls, 2, 0)
        og.addWidget(QLabel("Iterations"), 2, 1, alignment=Qt.AlignmentFlag.AlignRight)
        og.addWidget(self.iter, 2, 2)

        self.chk_con = QCheckBox("Confine to range of constraining column")
        self.lower_lim = self._num_box(0.0)
        self.upper_lim = self._num_box(99999.0)
        og.addWidget(self.chk_con, 3, 0, 1, 3)
        og.addWidget(QLabel("Lower limit"), 4, 0)
        og.addWidget(self.lower_lim, 4, 1, 1, 2)
        og.addWidget(QLabel("Upper limit"), 5, 0)
        og.addWidget(self.upper_lim, 5, 1, 1, 2)
        lv.addWidget(opt)

        thr = QGroupBox("Yield thresholds (applied to both fields)")
        tg = QGridLayout(thr)
        self.lthresh = self._num_box(0.0)
        self.uthresh = self._num_box(99999.0)
        tg.addWidget(QLabel("Lower threshold"), 0, 0)
        tg.addWidget(self.lthresh, 0, 1, 1, 2)
        tg.addWidget(QLabel("Upper threshold"), 1, 0)
        tg.addWidget(self.uthresh, 1, 1, 1, 2)
        self.trim = QDoubleSpinBox()
        self.trim.setRange(0.0, 0.5)
        self.trim.setDecimals(3)
        self.trim.setSingleStep(0.005)
        self.trim.setValue(0.01)
        self.trim.setToolTip("Fraction trimmed from each tail of each field")
        self.btn_trim = QPushButton("Reset from tails")
        self.btn_trim.setToolTip("Set thresholds so that this fraction of points is "
                                 "excluded at each end, then recalculate")
        self.btn_clear_thr = QPushButton("Clear")
        tg.addWidget(QLabel("Tail fraction"), 2, 0)
        tg.addWidget(self.trim, 2, 1)
        tg.addWidget(self.btn_trim, 2, 2)
        tg.addWidget(self.btn_clear_thr, 3, 2)
        lv.addWidget(thr)

        dist = QGroupBox("Yield vs distance to tile")
        dist.setCheckable(True)
        dist.setChecked(False)
        dist.setToolTip("Compare yield over the tile with yield midway between "
                        "laterals. A drop midway suggests the drains are too far apart.")
        self.grp_dist = dist
        dg = QGridLayout(dist)
        self.network = QgsMapLayerComboBox()
        self.network.setFilters(LAYER_FILTER_LINE)
        self.network.setAllowEmptyLayer(True)
        self.network.setLayer(None)
        self.chk_laterals = QCheckBox("Laterals only (kind = 'lateral')")
        self.chk_laterals.setChecked(True)
        self.chk_net_sel = QCheckBox("Selected lines only")
        self.spacing = QDoubleSpinBox()
        self.spacing.setRange(0.0, 1e6)
        self.spacing.setDecimals(1)
        self.spacing.setValue(0.0)
        self.spacing.setToolTip("Drain (lateral) spacing. Filled in from DRAIN_SPACE "
                                "for Illini networks. Points more than half a spacing "
                                "from any tile count as beyond the reach of the tile.")
        self.spacing_units = QLabel("")
        self.bands = QSpinBox()
        self.bands.setRange(3, 30)
        self.bands.setValue(6)
        dg.addWidget(QLabel("Tile network"), 0, 0)
        dg.addWidget(self.network, 0, 1, 1, 2)
        dg.addWidget(self.chk_laterals, 1, 0, 1, 3)
        dg.addWidget(self.chk_net_sel, 2, 0, 1, 3)
        dg.addWidget(QLabel("Drain spacing"), 3, 0)
        dg.addWidget(self.spacing, 3, 1)
        dg.addWidget(self.spacing_units, 3, 2)
        dg.addWidget(QLabel("Distance bands"), 4, 0)
        dg.addWidget(self.bands, 4, 1)
        lv.addWidget(dist)

        sty = QGroupBox("Map colouring")
        sg = QGridLayout(sty)
        self.chk_style = QCheckBox("Colour both layers by yield after calculating")
        self.chk_style.setChecked(True)
        self.min_color = QgsColorButton()
        self.min_color.setColor(QColor(255, 255, 0))
        self.max_color = QgsColorButton()
        self.max_color.setColor(QColor(0, 64, 0))
        self.classes = QSpinBox()
        self.classes.setRange(2, 30)
        self.classes.setValue(7)
        sg.addWidget(self.chk_style, 0, 0, 1, 4)
        sg.addWidget(QLabel("Minimum colour"), 1, 0)
        sg.addWidget(self.min_color, 1, 1)
        sg.addWidget(QLabel("Maximum colour"), 1, 2)
        sg.addWidget(self.max_color, 1, 3)
        sg.addWidget(QLabel("Classes"), 2, 0)
        sg.addWidget(self.classes, 2, 1)
        lv.addWidget(sty)
        lv.addStretch(1)

        btns = QHBoxLayout()
        self.btn_calc = QPushButton("Calculate Statistics")
        self.btn_calc.setDefault(True)
        self.btn_export = QPushButton("Export report…")
        self.btn_export.setEnabled(False)
        self.btn_close = QPushButton("Close")
        btns.addWidget(self.btn_calc)
        btns.addWidget(self.btn_export)
        btns.addWidget(self.btn_close)
        lv.addLayout(btns)

        scroll = QScrollArea()
        scroll.setWidget(left)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(440)
        root.addWidget(scroll, 0)

        # ---------------- right: results ----------------
        self.tabs = QTabWidget()
        self.tbl_stats = self._table(["Statistic", "Field 1", "Field 2"])
        self.tabs.addTab(self.tbl_stats, "Statistics")

        w = QWidget()
        v = QVBoxLayout(w)
        self.lbl_summary = self._summary_label()
        self.tbl_tests = self._table(["Test", "Statistic", "Deg. of freedom", "p-value",
                                      "Different at 5 %?", "p ignoring autocorrelation"])
        v.addWidget(self.lbl_summary)
        v.addWidget(self.tbl_tests)
        self.tabs.addTab(w, "Equivalence tests")

        w = QWidget()
        v = QVBoxLayout(w)
        self.lbl_dist = self._summary_label()
        v.addWidget(self.lbl_dist)
        if HAVE_MPL:
            self.fig_dist, fw = self._figure()
            v.addWidget(fw, 3)
        self.tbl_dist = self._table(["Result", "Field 1", "Field 2"])
        v.addWidget(self.tbl_dist, 2)
        self.tab_dist = w
        self.tabs.addTab(w, "Distance to tile")

        if HAVE_MPL:
            self.fig_hist, w = self._figure()
            self.tabs.addTab(w, "Histograms")
            self.fig_reg, w = self._figure()
            self.tabs.addTab(w, "Regression")
            self.fig_vg, w = self._figure()
            self.tabs.addTab(w, "Spatial correlation")
        else:
            self.tabs.addTab(QLabel("matplotlib is not available – plots disabled."),
                             "Plots")
        root.addWidget(self.tabs, 1)

        # ---------------- signals ----------------
        self.chk_reg.toggled.connect(self._update_enabled)
        self.chk_rls.toggled.connect(self._update_enabled)
        self.chk_con.toggled.connect(self._update_enabled)
        self.btn_calc.clicked.connect(self.calculate)
        self.btn_trim.clicked.connect(self.reset_thresholds)
        self.btn_clear_thr.clicked.connect(self._clear_thresholds)
        self.btn_export.clicked.connect(self.export_report)
        self.btn_close.clicked.connect(self.close)
        self.network.layerChanged.connect(self._network_changed)
        for p in (self.f1, self.f2):
            p.yield_field.fieldChanged.connect(self._clear_thresholds)
            p.layer.layerChanged.connect(self._clear_thresholds)
        self._update_enabled()
        self._preselect_layers()

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _num_box(v):
        s = QDoubleSpinBox()
        s.setRange(-BIG, BIG)
        s.setDecimals(4)
        s.setValue(v)
        return s

    @staticmethod
    def _summary_label():
        lbl = QLabel("")
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return lbl

    @staticmethod
    def _table(headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setAlternatingRowColors(True)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(headers)):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        return t

    def _figure(self):
        fig = Figure(figsize=(7, 5), tight_layout=True)
        canvas = FigureCanvas(fig)
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(NavToolbar(canvas, w))   # zoom / pan / save-as-image
        v.addWidget(canvas)
        fig._canvas = canvas
        return fig, w

    def _preselect_layers(self):
        """Pick the first two point layers (else vector layers) in the project."""
        vecs = [lyr for lyr in QgsProject.instance().mapLayers().values()
                if isinstance(lyr, QgsVectorLayer)]
        pts = [l for l in vecs if l.geometryType() == _first(
            lambda: Qgis.GeometryType.Point, lambda: 0)] or vecs
        if pts:
            self.f1.layer.setLayer(pts[0])
        if len(pts) > 1:
            self.f2.layer.setLayer(pts[1])

    def _update_enabled(self):
        reg = self.chk_reg.isChecked()
        self.chk_rls.setEnabled(reg)
        self.iter.setEnabled(reg and self.chk_rls.isChecked())
        con = self.chk_con.isChecked()
        self.lower_lim.setEnabled(con)
        self.upper_lim.setEnabled(con)
        for p in (self.f1, self.f2):
            for w in (p.reg_field, p.scale_x, p.reg_label):
                w.setEnabled(reg)
            for w in (p.con_field, p.con_label):
                w.setEnabled(con)

    def _network_changed(self, lyr):
        """Laterals filter and drain spacing from an Illini network."""
        if lyr is None:
            self.spacing_units.setText("")
            return
        crs = lyr.crs() if not lyr.crs().isGeographic() else \
            QgsCoordinateReferenceSystem("EPSG:3857")
        units = unit_name(crs) if not lyr.crs().isGeographic() else "meters"
        self.spacing_units.setText(units)
        has_kind = lyr.fields().indexFromName("kind") >= 0
        self.chk_laterals.setEnabled(has_kind)
        self.chk_laterals.setChecked(has_kind)
        i = lyr.fields().indexFromName("DRAIN_SPACE")
        if i < 0:
            return
        vals = []
        req = QgsFeatureRequest()
        req.setFlags(NO_GEOMETRY)
        req.setSubsetOfAttributes([i])
        for f in lyr.getFeatures(req):
            v = sc._num(f[i])
            if v and v > 0:
                vals.append(v)
        if vals:
            ft = float(np.median(vals))
            try:
                fac = 0.3048 if lyr.crs().isGeographic() else \
                    QgsUnitTypes.fromUnitToUnitFactor(UNIT_FEET, crs.mapUnits())
            except Exception:
                fac = 1.0
            self.spacing.setValue(ft * fac)

    def _clear_thresholds(self, *args):
        self.lthresh.setValue(0.0)
        self.uthresh.setValue(99999.0)

    def _z_limits(self):
        return (self.lower_lim.value(), self.upper_lim.value())

    # ---------------------------------------------------------------- actions
    def reset_thresholds(self):
        try:
            use_con = self.chk_con.isChecked()
            y1 = self.f1.read(False, use_con, self._z_limits(), None)[0]
            y2 = self.f2.read(False, use_con, self._z_limits(), None)[0]
            if len(y1) == 0 or len(y2) == 0:
                raise ValueError("A field has no usable points.")
            lo, hi = sc.reset_thresholds(y1, y2, self.trim.value())
        except Exception as e:
            QMessageBox.warning(self, "Yield Compare", str(e))
            return
        self.lthresh.setValue(lo)
        self.uthresh.setValue(hi)
        self.calculate()

    def calculate(self):
        reg = self.chk_reg.isChecked()
        rls = reg and self.chk_rls.isChecked()
        con = self.chk_con.isChecked()
        spatial = self.chk_spatial.isChecked()
        use_dist = self.grp_dist.isChecked()
        thr = (self.lthresh.value(), self.uthresh.value())
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            net = self.network.currentLayer() if use_dist else None
            if use_dist and net is None:
                raise ValueError("Yield vs distance to tile: choose the tile network layer.")
            if use_dist and self.spacing.value() <= 0:
                raise ValueError("Yield vs distance to tile: enter the drain spacing.")
            crs = None
            if spatial or use_dist:
                crs = working_crs([self.f1.layer.currentLayer(),
                                   self.f2.layer.currentLayer()], net)
            y1, x1, xy1, g1 = self.f1.read(reg, con, self._z_limits(), thr, crs)
            y2, x2, xy2, g2 = self.f2.read(reg, con, self._z_limits(), thr, crs)
            if (spatial or use_dist) and (xy1 is None or xy2 is None):
                raise ValueError("Both layers need point locations (geometry) for the "
                                 "spatial analysis.")
            f1 = {"y": y1, "x": x1, "xy": xy1, "g": g1, "exceed_prob": self.f1.exceed.value()}
            f2 = {"y": y2, "x": x2, "xy": xy2, "g": g2, "exceed_prob": self.f2.exceed.value()}
            distance = None
            reach = 0.0
            if use_dist:
                # spacing is in the network CRS units; convert to the working CRS
                reach = self.spacing.value() / 2.0
                if not net.crs().isGeographic() and net.crs() != crs:
                    reach *= QgsUnitTypes.fromUnitToUnitFactor(net.crs().mapUnits(),
                                                               crs.mapUnits())
                f1["d"], f2["d"] = tile_distances(net, self.chk_laterals.isChecked()
                                                  and self.chk_laterals.isEnabled(),
                                                  self.chk_net_sel.isChecked(), crs,
                                                  (xy1, xy2))
                distance = {"reach": reach, "n_bins": self.bands.value()}
            res = sc.analyse(f1, f2, regression=reg, rls=rls,
                             rls_iter=self.iter.value(), spatial=spatial,
                             distance=distance)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Yield Compare", str(e))
            return
        res["data"] = ((y1, x1), (y2, x2))
        res["labels"] = (self.f1.label(), self.f2.label())
        res["ynames"] = tuple(p.yield_field.currentField() for p in (self.f1, self.f2))
        res["xnames"] = tuple(p.reg_field.currentField() for p in (self.f1, self.f2))
        res["units"] = unit_name(crs) if crs is not None else ""
        res["has_passes"] = (g1 is not None, g2 is not None)
        res["settings"] = dict(regression=reg, rls=rls, thresholds=thr, spatial=spatial,
                               constraint=self._z_limits() if con else None,
                               network=net.name() if net else None, reach=reach)
        self.result = res
        try:
            self._fill_stats(res)
            self._fill_tests(res)
            self._fill_distance(res)
            if HAVE_MPL:
                self._plot_hist(res)
                self._plot_reg(res)
                self._plot_variograms(res)
                self._plot_distance(res)
            if self.chk_style.isChecked():
                self._style_layers(res)
        finally:
            QApplication.restoreOverrideCursor()
        self.btn_export.setEnabled(True)
        if use_dist:
            self.tabs.setCurrentWidget(self.tab_dist)

    # ---------------------------------------------------------------- output
    def _stats_rows(self, res):
        a, b = res["field1"], res["field2"]
        rows = [
            ("No. of points", "n", 0),
            ("Minimum", "min", 3), ("Maximum", "max", 3),
            ("Average", "mean", 3), ("Std. Dev.", "sd", 3),
            ("Median", "median", 3), ("I.Q. Range", "iqr", 3),
            ("I.Q. Ratio", "iq_ratio", 3),
            ("Robust (reweighted) mean", "robust_mean", 3),
            ("Robust (reweighted) std. dev.", "robust_sd", 3),
            ("Points kept by robust weighting", "robust_n", 0),
            ("Empirical: value exceeded by 5 %", "emp_05", 3),
            ("Empirical: value exceeded by 95 %", "emp_95", 3),
            ("Gaussian: value exceeded by 5 %", "gauss_05", 3),
            ("Gaussian: value exceeded by 95 %", "gauss_95", 3),
            ("Exceedance probability used", "exceed_prob", 3),
            ("  Gaussian value at that probability", "gauss_exceed", 3),
            ("  Empirical value at that probability", "emp_exceed", 3),
            ("Skewness", "skewness", 3), ("Kurtosis (excess)", "kurtosis", 3),
            ("Jarque-Bera test statistic", "jb_stat", 3),
        ]
        out = [(lbl, fmt(a.get(k), nd), fmt(b.get(k), nd)) for lbl, k, nd in rows]
        out.append(("Jarque-Bera p-value (normality)", fmt_p(a["jb_p"]), fmt_p(b["jb_p"])))
        if res["spatial"]:
            sa, sb = a["spatial"], b["spatial"]
            ma, mb = sa["model"] or {}, sb["model"] or {}

            def rng_(m, share, key):
                return fmt(m.get(key) if m.get(share, 0) > 0 else float("nan"), 0)
            out += [
                ("Effective no. of independent points", fmt(round(sa["n_eff"])),
                 fmt(round(sb["n_eff"]))),
                ("Variance share: between harvest passes",
                 fmt(ma.get("pass_share", 0.0), 2) if ma.get("has_groups") else "no pass column",
                 fmt(mb.get("pass_share", 0.0), 2) if mb.get("has_groups") else "no pass column"),
                ("Variance share: spatially correlated",
                 fmt(ma.get("spatial_share", 0.0), 2), fmt(mb.get("spatial_share", 0.0), 2)),
                (f"  correlation range ({res['units']})", rng_(ma, "spatial_share", "range"),
                 rng_(mb, "spatial_share", "range")),
                ("Variance share: extra correlation along a pass",
                 fmt(ma.get("along_share", 0.0), 2), fmt(mb.get("along_share", 0.0), 2)),
                (f"  along-pass range ({res['units']})", rng_(ma, "along_share", "along_range"),
                 rng_(mb, "along_share", "along_range")),
                ("Variance share: independent (nugget)",
                 fmt(ma.get("nugget_share", 1.0), 2), fmt(mb.get("nugget_share", 1.0), 2)),
            ]
        for key, name in (("ols", "Least squares"), ("rls", "Reweighted LS")):
            if key in a:
                out += [(f"{name}: slope", fmt(a[key]["slope"], 4), fmt(b[key]["slope"], 4)),
                        (f"{name}: intercept", fmt(a[key]["intercept"], 3),
                         fmt(b[key]["intercept"], 3)),
                        (f"{name}: r²", fmt(a[key]["r2"], 4), fmt(b[key]["r2"], 4)),
                        (f"{name}: points used", fmt(a[key]["n"], 0), fmt(b[key]["n"], 0))]
        return out

    def _test_rows(self, res):
        return [(t["name"], fmt(t["stat"]),
                 t["df"] if isinstance(t["df"], str) else fmt(t["df"], 1),
                 t["p"], t["p_naive"]) for t in res["tests"]]

    def _fill_stats(self, res):
        rows = self._stats_rows(res)
        t = self.tbl_stats
        t.setHorizontalHeaderLabels(["Statistic", "Field 1: " + res["labels"][0],
                                     "Field 2: " + res["labels"][1]])
        self._put_rows(t, rows)

    @staticmethod
    def _put_rows(t, rows):
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                it = QTableWidgetItem(v)
                if c:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                t.setItem(r, c, it)

    def _summary_text(self, res):
        d = res["difference"]
        a, b = res["labels"]
        p = res["tests"][0]["p"]
        more = "more" if d["diff"] >= 0 else "less"
        a, b = html.escape(a), html.escape(b)
        txt = (f"<b>{a}</b> averaged <b>{abs(d['diff']):.1f}</b> ({abs(d['pct']):.1f} %) "
               f"{more} than <b>{b}</b> (95 % confidence interval "
               f"{d['lo']:+.1f} to {d['hi']:+.1f}; p = {fmt_p_html(p)}).")
        if d["lo"] <= 0 <= d["hi"]:
            txt += " The interval includes zero: no clear difference in mean yield."
        if res["spatial"]:
            n1 = round(res["field1"]["spatial"]["n_eff"])
            n2 = round(res["field2"]["spatial"]["n_eff"])
            txt += (f"<br><span style='color:#555'>Allowing for spatial autocorrelation, "
                    f"the {res['field1']['n']} and {res['field2']['n']} points carry the "
                    f"information of about {n1} and {n2} independent points.</span>")
        else:
            txt += ("<br><span style='color:#a00'>Spatial autocorrelation is ignored: "
                    "p-values will be far too small for yield-monitor data.</span>")
        return txt

    def _fill_tests(self, res):
        self.lbl_summary.setText(self._summary_text(res))
        rows = self._test_rows(res)
        t = self.tbl_tests
        t.setColumnHidden(5, not res["spatial"])
        t.setRowCount(len(rows))
        for r, (name, stat, df, p, p0) in enumerate(rows):
            vals = [name, stat, df, fmt_p(p), "yes" if p < 0.05 else "no", fmt_p(p0)]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == 4 and p < 0.05:
                    it.setForeground(QColor(170, 0, 0))
                if c == 5:
                    it.setForeground(QColor(120, 120, 120))
                t.setItem(r, c, it)

    # -------- distance to tile
    def _distance_rows(self, res):
        u = res["units"]
        cols = []
        for prof in res["distance"]:
            col = {}
            if prof is None or "error" in prof:
                col["note"] = prof["error"] if prof else "no distances"
                cols.append(col)
                continue
            col["Points within half a spacing of tile"] = fmt(prof["n_within"])
            col["Points beyond half a spacing"] = fmt(prof["n_beyond"])
            m = prof["mid_vs_near"]
            if m:
                col["Mean yield over tile (inner quarter)"] = fmt(m["mean_a"], 1)
                col["Mean yield midway (outer quarter)"] = fmt(m["mean_b"], 1)
                col["Midway minus over tile (95 % CI)"] = fmt_ci(m)
                col["  as % of yield over tile"] = f"{m['pct']:+.1f} %"
                col["  p-value"] = fmt_p(m["p"])
                if res["spatial"]:
                    col["  p ignoring autocorrelation"] = fmt_p(m["p_naive"])
            t = prof.get("trend")
            if t:
                col[f"Trend: yield change per {singular(u)}"] = \
                    f"{t['slope']:+.3f}  ({t['lo']:+.3f} to {t['hi']:+.3f})"
                col["Trend: change from tile to midway"] = \
                    f"{t['change']:+.1f}  ({t['change_pct']:+.1f} %)"
                col["  p-value "] = fmt_p(t["p"])
            bw = prof.get("beyond_vs_within")
            if bw:
                col["Beyond reach minus within reach (95 % CI)"] = fmt_ci(bw)
                col["  p-value  "] = fmt_p(bw["p"])
            cols.append(col)
        keys = []
        for c in cols:
            for k in c:
                if k != "note" and k not in keys:
                    keys.append(k)
        rows = [(k.rstrip(), cols[0].get(k, ""), cols[1].get(k, "")) for k in keys]
        notes = [c.get("note", "") for c in cols]
        if any(notes):
            rows.insert(0, ("Note", notes[0], notes[1]))
        return rows

    def _distance_summary(self, res):
        lines = []
        for prof, lbl in zip(res["distance"], res["labels"]):
            lbl = html.escape(lbl)
            if prof is None or "error" in prof:
                lines.append(f"<b>{lbl}</b>: {prof['error'] if prof else 'no distances'}")
                continue
            m = prof["mid_vs_near"]
            if not m:
                lines.append(f"<b>{lbl}</b>: too few points over the tile or midway "
                             "between drains to compare.")
                continue
            if m["p"] < 0.05 and m["diff"] < 0:
                verdict = ("<span style='color:#a00'>yield drops midway between drains: "
                           "the spacing may be too wide for this soil</span>")
            elif m["p"] < 0.05:
                verdict = "yield is higher midway between drains than over them"
            else:
                verdict = "no clear difference between over the tile and midway"
            lines.append(
                f"<b>{lbl}</b>: midway yield {m['mean_b']:.1f} vs {m['mean_a']:.1f} over "
                f"the tile, a difference of {m['diff']:+.1f} ({m['pct']:+.1f} %; 95 % CI "
                f"{m['lo']:+.1f} to {m['hi']:+.1f}; p = {fmt_p_html(m['p'])}) – {verdict}.")
        if res["spatial"] and not all(res["has_passes"]):
            lines.append("<span style='color:#a00'>No harvest pass column: if the laterals "
                         "run parallel to the harvest passes, differences between passes "
                         "(header, flow delay) can look like a drainage effect. Choose the "
                         "pass column (e.g. Pass_Num) for each field.</span>")
        if not res["spatial"]:
            lines.append("<span style='color:#a00'>Spatial autocorrelation is ignored: "
                         "p-values will be far too small.</span>")
        reach = res["settings"]["reach"]
        lines.append(f"<span style='color:#555'>Over the tile = within {reach / 4:.1f} "
                     f"{res['units']} of a tile line; midway = {0.75 * reach:.1f} to "
                     f"{reach:.1f} {res['units']} (half the drain spacing).</span>")
        return "<br>".join(lines)

    def _fill_distance(self, res):
        if not res.get("distance"):
            self.lbl_dist.setText("Tick <b>Yield vs distance to tile</b> and choose the "
                                  "tile network to compare yield over the tile with "
                                  "yield midway between drains.")
            self.tbl_dist.setRowCount(0)
            if HAVE_MPL:
                self.fig_dist.clear()
                self.fig_dist._canvas.draw_idle()
            return
        self.lbl_dist.setText(self._distance_summary(res))
        self.tbl_dist.setHorizontalHeaderLabels(["Result", "Field 1: " + res["labels"][0],
                                                 "Field 2: " + res["labels"][1]])
        self._put_rows(self.tbl_dist, self._distance_rows(res))

    def _plot_distance(self, res):
        fig = self.fig_dist
        fig.clear()
        if not res.get("distance"):
            fig._canvas.draw_idle()
            return
        ax = fig.add_subplot(111)
        reach = res["settings"]["reach"]
        ax.axvspan(0, reach / 4, color="#2563eb", alpha=0.07, lw=0)
        ax.axvspan(0.75 * reach, reach, color="#dc2626", alpha=0.07, lw=0)
        colors = ("#1d4ed8", "#c2410c")
        for k, (prof, lbl) in enumerate(zip(res["distance"], res["labels"])):
            if prof is None or "error" in prof:
                continue
            bins = [b for b in prof["bins"] if math.isfinite(b["mean"])]
            xs = [(b["lo"] + b["hi"]) / 2 for b in bins]
            ys = [b["mean"] for b in bins]
            es = [b["ci"] if math.isfinite(b["ci"]) else 0 for b in bins]
            ax.errorbar(xs, ys, yerr=es, color=colors[k], marker="o", capsize=3,
                        lw=1.5, label=lbl)
            t = prof.get("trend")
            if t:
                ax.plot([0, reach], [t["intercept"], t["intercept"] + t["slope"] * reach],
                        color=colors[k], ls="--", lw=1, alpha=0.7)
        ax.set_xlim(0, reach)
        ax.set_xlabel(f"Distance to nearest tile ({res['units']})")
        ax.set_ylabel(f"Mean yield ({res['ynames'][0]})")
        ax.set_title("Yield across the drain spacing  (shaded: over tile | midway)",
                     fontsize=10)
        ax.text(0.01, 0.01, "Error bars: 95 % CI of each band's difference from the "
                "average within reach" + (", allowing for spatial autocorrelation"
                                          if res["spatial"] else ""),
                transform=ax.transAxes, fontsize=7, color="#555")
        ax.legend(fontsize=8)
        fig._canvas.draw_idle()

    # -------- other plots
    def _plot_hist(self, res):
        fig = self.fig_hist
        fig.clear()
        h = res["histogram"]
        edges = h["edges"]
        axes = fig.subplots(2, 1, sharex=True)
        for ax, counts, key, lbl in zip(axes, (h["counts1"], h["counts2"]),
                                        ("field1", "field2"), res["labels"]):
            s = res[key]
            ax.bar(edges[:-1], counts, width=edges[1:] - edges[:-1], align="edge",
                   color="#e33", edgecolor="#a00", linewidth=0.5)
            ax.axvline(s["mean"], color="black", lw=1.5, label=f"Mean {s['mean']:.2f}")
            ax.axvline(s["median"], color="blue", lw=1.5, label=f"Median {s['median']:.2f}")
            ax.set_title(lbl, fontsize=10)
            ax.set_ylabel("Points")
            ax.legend(fontsize=8)
        axes[-1].set_xlabel(f"Yield ({res['ynames'][0]} / {res['ynames'][1]})")
        fig._canvas.draw_idle()

    def _plot_reg(self, res):
        fig = self.fig_reg
        fig.clear()
        if not res["settings"]["regression"]:
            ax = fig.add_subplot(111)
            ax.axis("off")
            ax.text(0.5, 0.5, "Tick 'Perform regression analysis' to see the "
                    "yield vs regressor plots.", ha="center", va="center")
            fig._canvas.draw_idle()
            return
        axes = fig.subplots(1, 2, sharey=True)
        for ax, (y, x), key, lbl, xname in zip(axes, res["data"], ("field1", "field2"),
                                                res["labels"], res["xnames"]):
            s = res[key]
            if "rls" in s:
                w = s["rls"]["weights"].astype(bool)
                ax.scatter(x[w], y[w], s=2, color="#2156c9", label="Points")
                ax.scatter(x[~w], y[~w], s=2, color="#bbb", label="Down-weighted")
            else:
                ax.scatter(x, y, s=2, color="#2156c9", label="Points")
            xs = [float(x.min()), float(x.max())]
            o = s["ols"]
            ax.plot(xs, [o["intercept"] + o["slope"] * v for v in xs], color="red",
                    lw=1.5, label=f"Least squares (slope {o['slope']:.3f})")
            if "rls" in s:
                r = s["rls"]
                ax.plot(xs, [r["intercept"] + r["slope"] * v for v in xs],
                        color="#0a0", lw=1.5,
                        label=f"Reweighted LS (slope {r['slope']:.3f})")
            ax.set_title(lbl, fontsize=10)
            ax.set_xlabel(xname)
            ax.legend(fontsize=7, markerscale=4)
        axes[0].set_ylabel(res["ynames"][0])
        fig._canvas.draw_idle()

    def _plot_variograms(self, res):
        fig = self.fig_vg
        fig.clear()
        if not res["spatial"]:
            ax = fig.add_subplot(111)
            ax.axis("off")
            ax.text(0.5, 0.5, "Tick 'Allow for spatial autocorrelation' to see the "
                    "variograms.", ha="center", va="center")
            fig._canvas.draw_idle()
            return
        axes = fig.subplots(1, 2)
        for ax, key, lbl in zip(axes, ("field1", "field2"), res["labels"]):
            sp = res[key]["spatial"]
            m = sp["model"]
            vg = m["vg"] if m else None
            if vg is None:
                ax.text(0.5, 0.5, "Too few points for a variogram", ha="center",
                        transform=ax.transAxes)
                continue
            tag = "different passes" if m["has_groups"] else "all pairs"
            ax.scatter(vg["h"], vg["gamma"], color="#1d4ed8", s=18, label=f"Pairs in {tag}")
            if m.get("vg_same") is not None:
                v2 = m["vg_same"]
                ax.scatter(v2["h"], v2["gamma"], color="#c2410c", s=18, marker="^",
                           label="Pairs in the same pass")
            hh = np.linspace(0, vg["max_dist"], 300)[1:]
            wv = m["within_var"]
            total = m["sill"]
            s_x = m["spatial_share"] * total / wv
            s_a = m["along_share"] * total / wv
            ax.plot(hh, wv * (1 - s_x * np.exp(-3 * hh / m["range"])), color="#1d4ed8",
                    lw=1.5, label="Model, different passes" if m["has_groups"] else "Model")
            if m["has_groups"]:
                ax.plot(hh, wv * (1 - s_x * np.exp(-3 * hh / m["range"])
                                  - s_a * np.exp(-3 * hh / m["along_range"])),
                        color="#c2410c", lw=1.5, label="Model, same pass")
            ax.set_xscale("log")
            ax.set_title(f"{lbl}\neffective points {sp['n_eff']:.0f} of {sp['n']}", fontsize=9)
            ax.set_xlabel(f"Distance between points ({res['units']}, log scale)")
            ax.set_ylim(bottom=0)
            ax.legend(fontsize=7)
        axes[0].set_ylabel("Semivariance (after trend" +
                           (" and pass means)" if any(res["has_passes"]) else ")"))
        fig._canvas.draw_idle()

    def _style_layers(self, res):
        """Graduated colours from min to max colour (VB LinearPlots)."""
        a, b = res["field1"], res["field2"]
        vmin = min(a["gauss_95"], b["gauss_95"])
        vmax = max(a["gauss_05"], b["gauss_05"])
        if not (math.isfinite(vmin) and math.isfinite(vmax)) or vmax <= vmin:
            vmin, vmax = min(a["min"], b["min"]), max(a["max"], b["max"])
        n = self.classes.value()
        c1, c2 = self.min_color.color(), self.max_color.color()
        step = (vmax - vmin) / n
        for panel in (self.f1, self.f2):
            lyr = panel.layer.currentLayer()
            if lyr is None:
                continue
            ranges = []
            for i in range(n):
                t = i / (n - 1) if n > 1 else 0
                col = QColor(round(c1.red() + t * (c2.red() - c1.red())),
                             round(c1.green() + t * (c2.green() - c1.green())),
                             round(c1.blue() + t * (c2.blue() - c1.blue())))
                lo = vmin + i * step
                hi = vmin + (i + 1) * step
                lab = f"{lo:.1f} – {hi:.1f}"
                if i == 0:
                    lo, lab = -BIG, f"< {hi:.1f}"
                if i == n - 1:
                    hi, lab = BIG, f"≥ {lo:.1f}"
                sym = QgsSymbol.defaultSymbol(lyr.geometryType())
                sym.setColor(col)
                if isinstance(sym, QgsMarkerSymbol):
                    sym.setSize(1.6)
                    try:
                        sym.symbolLayer(0).setStrokeStyle(Qt.PenStyle.NoPen)
                    except AttributeError:
                        pass
                ranges.append(QgsRendererRange(lo, hi, sym, lab))
            lyr.setRenderer(QgsGraduatedSymbolRenderer(panel.yield_expression(), ranges))
            lyr.triggerRepaint()
            try:
                self.iface.layerTreeView().refreshLayerSymbology(lyr.id())
            except Exception:
                pass

    def export_report(self):
        if not self.result:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export report", "yield_comparison.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        res = self.result
        st = res["settings"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Yield comparison"])
            w.writerow(["Field 1", res["labels"][0]])
            w.writerow(["Field 2", res["labels"][1]])
            w.writerow(["Thresholds", *st["thresholds"]])
            if st["constraint"]:
                w.writerow(["Constraint limits", *st["constraint"]])
            w.writerow(["Spatial autocorrelation allowed for", "yes" if st["spatial"] else "no"])
            d = res["difference"]
            w.writerow(["Field 1 minus Field 2 (mean)", f"{d['diff']:.3f}",
                        "95% CI", f"{d['lo']:.3f}", f"{d['hi']:.3f}",
                        "% of Field 2", f"{d['pct']:.2f}"])
            w.writerow([])
            w.writerow(["Statistic", "Field 1", "Field 2"])
            w.writerows(self._stats_rows(res))
            w.writerow([])
            w.writerow(["Test", "Statistic", "Degrees of freedom", "p-value",
                        "p ignoring autocorrelation"])
            for name, stat, df, p, p0 in self._test_rows(res):
                w.writerow([name, stat, df, f"{p:.6g}", f"{p0:.6g}"])
            if res.get("distance"):
                w.writerow([])
                w.writerow(["Yield vs distance to tile", f"network: {st['network']}",
                            f"half spacing: {st['reach']:.2f} {res['units']}"])
                w.writerow(["Result", "Field 1", "Field 2"])
                w.writerows(self._distance_rows(res))
                w.writerow([])
                w.writerow(["Field", "Band from", "Band to", "Points", "Mean yield",
                            "95% CI half-width"])
                for prof, lbl in zip(res["distance"], res["labels"]):
                    if prof is None or "bins" not in prof:
                        continue
                    for b in prof["bins"]:
                        w.writerow([lbl, f"{b['lo']:.2f}", f"{b['hi']:.2f}", b["n"],
                                    f"{b['mean']:.3f}", f"{b['ci']:.3f}"])
        self.iface.messageBar().pushSuccess("Yield Compare", f"Report saved to {path}")
