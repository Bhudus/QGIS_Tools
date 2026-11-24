# -*- coding: utf-8 -*-
"""Main dialog of the Yield Compare plugin (replaces frmShapeToXYZ)."""

import csv
import math

from qgis.core import (Qgis, QgsFeatureRequest, QgsFieldProxyModel,
                       QgsGraduatedSymbolRenderer, QgsMapLayerProxyModel,
                       QgsMarkerSymbol, QgsProject, QgsRendererRange,
                       QgsSymbol, QgsVectorLayer)
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
FIELD_FILTER_NUMERIC = _first(lambda: QgsFieldProxyModel.Filter.Numeric,
                              lambda: QgsFieldProxyModel.Numeric)
NO_GEOMETRY = _first(lambda: Qgis.FeatureRequestFlag.NoGeometry,
                     lambda: QgsFeatureRequest.Flag.NoGeometry,
                     lambda: QgsFeatureRequest.NoGeometry)

BIG = 1e12


def fmt(v, nd=3):
    if v is None:
        return ""
    if isinstance(v, (int,)) or (isinstance(v, float) and v.is_integer() and abs(v) < 1e9):
        return str(int(v))
    if not math.isfinite(v):
        return "n/a"
    return f"{v:.{nd}f}"


def fmt_p(p):
    if p is None or not math.isfinite(p):
        return "n/a"
    return "< 0.0001" if p < 1e-4 else f"{p:.4f}"


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

        self.exceed = QDoubleSpinBox()
        self.exceed.setRange(0.0, 1.0)
        self.exceed.setDecimals(3)
        self.exceed.setSingleStep(0.05)
        self.exceed.setValue(0.9)
        self.exceed.setToolTip("Report the yield exceeded with this probability "
                               "(Gaussian and empirical distribution).")
        form.addRow("Exceedance probability", self.exceed)

        for cb in (self.yield_field, self.reg_field, self.con_field):
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
                return

    def label(self):
        lyr = self.layer.currentLayer()
        return lyr.name() if lyr else self.title()

    def read(self, use_reg, use_con, z_limits, thresholds):
        """Read and filter the attribute values (returns arrays y, x)."""
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

        names = [n for n in (yname, xname, zname) if n]
        idx = [lyr.fields().indexFromName(n) for n in names]
        req = QgsFeatureRequest()
        req.setFlags(NO_GEOMETRY)
        req.setSubsetOfAttributes(idx)
        feats = (lyr.getSelectedFeatures(req) if self.selected_only.isChecked()
                 else lyr.getFeatures(req))
        ys, xs, zs = [], [], []
        iy = idx[0]
        ix = lyr.fields().indexFromName(xname) if xname else -1
        iz = lyr.fields().indexFromName(zname) if zname else -1
        for f in feats:
            ys.append(f[iy])
            if ix >= 0:
                xs.append(f[ix])
            if iz >= 0:
                zs.append(f[iz])
        return sc.prepare(ys, xs if ix >= 0 else None, zs if iz >= 0 else None,
                          self.scale_y.value(), self.scale_x.value(),
                          z_limits if iz >= 0 else None, thresholds)

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
        self.resize(1250, 780)

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
        self.chk_reg = QCheckBox("Perform regression analysis")
        self.chk_rls = QCheckBox("Reweighted least squares analysis")
        self.iter = QSpinBox()
        self.iter.setRange(10, 20000)
        self.iter.setValue(500)
        self.iter.setToolTip("Number of trial slopes in the least-median-of-squares search")
        og.addWidget(self.chk_reg, 0, 0, 1, 2)
        og.addWidget(self.chk_rls, 1, 0)
        og.addWidget(QLabel("Iterations"), 1, 1, alignment=Qt.AlignmentFlag.AlignRight)
        og.addWidget(self.iter, 1, 2)

        self.chk_con = QCheckBox("Confine to range of constraining column")
        self.lower_lim = self._num_box(0.0)
        self.upper_lim = self._num_box(99999.0)
        og.addWidget(self.chk_con, 2, 0, 1, 3)
        og.addWidget(QLabel("Lower limit"), 3, 0)
        og.addWidget(self.lower_lim, 3, 1, 1, 2)
        og.addWidget(QLabel("Upper limit"), 4, 0)
        og.addWidget(self.upper_lim, 4, 1, 1, 2)
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
        scroll.setMinimumWidth(430)
        root.addWidget(scroll, 0)

        # ---------------- right: results ----------------
        self.tabs = QTabWidget()
        self.tbl_stats = self._table(["Statistic", "Field 1", "Field 2"])
        self.tbl_tests = self._table(["Test", "Statistic", "Deg. of freedom",
                                      "p-value", "Different at 5 %?"])
        self.tabs.addTab(self.tbl_stats, "Statistics")
        self.tabs.addTab(self.tbl_tests, "Equivalence tests")
        if HAVE_MPL:
            self.fig_hist, w = self._figure()
            self.tabs.addTab(w, "Histograms")
            self.fig_reg, w = self._figure()
            self.tabs.addTab(w, "Regression")
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
        """Pick the first two vector layers in the project, if any."""
        vecs = [lyr for lyr in QgsProject.instance().mapLayers().values()
                if isinstance(lyr, QgsVectorLayer)]
        if vecs:
            self.f1.layer.setLayer(vecs[0])
        if len(vecs) > 1:
            self.f2.layer.setLayer(vecs[1])

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

    def _clear_thresholds(self, *args):
        self.lthresh.setValue(0.0)
        self.uthresh.setValue(99999.0)

    def _z_limits(self):
        return (self.lower_lim.value(), self.upper_lim.value())

    # ---------------------------------------------------------------- actions
    def reset_thresholds(self):
        try:
            use_con = self.chk_con.isChecked()
            y1, _ = self.f1.read(False, use_con, self._z_limits(), None)
            y2, _ = self.f2.read(False, use_con, self._z_limits(), None)
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
        thr = (self.lthresh.value(), self.uthresh.value())
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            y1, x1 = self.f1.read(reg, con, self._z_limits(), thr)
            y2, x2 = self.f2.read(reg, con, self._z_limits(), thr)
            res = sc.analyse({"y": y1, "x": x1, "exceed_prob": self.f1.exceed.value()},
                             {"y": y2, "x": x2, "exceed_prob": self.f2.exceed.value()},
                             regression=reg, rls=rls, rls_iter=self.iter.value())
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Yield Compare", str(e))
            return
        res["data"] = ((y1, x1), (y2, x2))
        res["labels"] = (self.f1.label(), self.f2.label())
        res["ynames"] = tuple(p.yield_field.currentField() for p in (self.f1, self.f2))
        res["xnames"] = tuple(p.reg_field.currentField() for p in (self.f1, self.f2))
        res["settings"] = dict(regression=reg, rls=rls, thresholds=thr,
                               constraint=self._z_limits() if con else None)
        self.result = res
        try:
            self._fill_stats(res)
            self._fill_tests(res)
            if HAVE_MPL:
                self._plot_hist(res)
                self._plot_reg(res)
            if self.chk_style.isChecked():
                self._style_layers(res)
        finally:
            QApplication.restoreOverrideCursor()
        self.btn_export.setEnabled(True)

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
        for key, name in (("ols", "Least squares"), ("rls", "Reweighted LS")):
            if key in a:
                out += [(f"{name}: slope", fmt(a[key]["slope"], 4), fmt(b[key]["slope"], 4)),
                        (f"{name}: intercept", fmt(a[key]["intercept"], 3),
                         fmt(b[key]["intercept"], 3)),
                        (f"{name}: r²", fmt(a[key]["r2"], 4), fmt(b[key]["r2"], 4)),
                        (f"{name}: points used", fmt(a[key]["n"], 0), fmt(b[key]["n"], 0))]
        return out

    def _test_rows(self, res):
        rows = []
        t, df, p = res["means"]
        rows.append(("Equivalence of means (Welch t)", fmt(t), fmt(df, 1), p))
        t, df, p = res["medians"]
        rows.append(("Equivalence of medians (robust Welch t)", fmt(t), fmt(df, 1), p))
        f, d1, d2, p = res["variances"]
        rows.append(("Equivalence of variances (F)", fmt(f), f"{d1}, {d2}", p))
        h = res["histogram"]
        rows.append(("Equivalence of histograms (χ², 30 bins)", fmt(h["stat"]),
                     fmt(h["df"], 0), h["p"]))
        s = res.get("slopes")
        if s:
            kind = "reweighted LS" if res["settings"]["rls"] else "least squares"
            rows.append((f"Slope, Field 1 vs 0 ({kind})", fmt(s["t1"]), fmt(s["df1"], 0), s["p1"]))
            rows.append((f"Slope, Field 2 vs 0 ({kind})", fmt(s["t2"]), fmt(s["df2"], 0), s["p2"]))
            rows.append(("Slope, Field 1 vs Field 2", fmt(s["t12"]), fmt(s["df12"], 0), s["p12"]))
        return rows

    def _fill_stats(self, res):
        rows = self._stats_rows(res)
        t = self.tbl_stats
        t.setHorizontalHeaderLabels(["Statistic", "Field 1: " + res["labels"][0],
                                     "Field 2: " + res["labels"][1]])
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                it = QTableWidgetItem(v)
                if c:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                t.setItem(r, c, it)

    def _fill_tests(self, res):
        rows = self._test_rows(res)
        t = self.tbl_tests
        t.setRowCount(len(rows))
        for r, (name, stat, df, p) in enumerate(rows):
            vals = [name, stat, df, fmt_p(p), "yes" if p < 0.05 else "no"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == 4 and p < 0.05:
                    it.setForeground(QColor(170, 0, 0))
                t.setItem(r, c, it)

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
            w.writerow([])
            w.writerow(["Statistic", "Field 1", "Field 2"])
            w.writerows(self._stats_rows(res))
            w.writerow([])
            w.writerow(["Test", "Statistic", "Degrees of freedom", "p-value"])
            for name, stat, df, p in self._test_rows(res):
                w.writerow([name, stat, df, f"{p:.6g}"])
        self.iface.messageBar().pushSuccess("Yield Compare", f"Report saved to {path}")
