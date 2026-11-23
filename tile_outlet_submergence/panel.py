"""Ditch level explorer: a dock panel that steps the map through the ditch levels of a run.

Each run of the tool tags its drainage-rate raster (one band per step) and its pipes
layer (fields q_<i> / rise_<i>) with a JSON description of the run. The panel finds
tagged layers in the project, and the arrows switch the raster band and the field the
pipes are styled by, so nothing is solved again.
"""
import json
import math

from qgis.core import (QgsCoordinateTransform, QgsGeometry, QgsGraduatedSymbolRenderer,
                       QgsPointXY, QgsProject, QgsRasterLayer, QgsRectangle, QgsVectorLayer,
                       QgsFeatureRequest)
from qgis.gui import QgsMapToolEmitPoint, QgsVertexMarker
from qgis.PyQt.QtCore import QPointF, Qt, QTimer
from qgis.PyQt.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPalette, QPen
from qgis.PyQt.QtWidgets import (QButtonGroup, QComboBox, QDockWidget, QFrame, QHBoxLayout,
                                 QLabel, QPushButton, QRadioButton, QScrollArea, QSizePolicy,
                                 QToolButton, QVBoxLayout, QWidget)

try:
    from qgis.PyQt.QtWidgets import QShortcut
except ImportError:                                  # Qt 6
    from qgis.PyQt.QtGui import QShortcut

from .core.explorer import (NODATA, PROP_KEY, flap_text, flow_text, level_text, q_field,
                            rise_field)
from .core.report import _nice


def _enum(scope, name):
    """Qt enum member that works with both PyQt5 and PyQt6."""
    owner = getattr(Qt, scope, None)
    v = getattr(owner, name, None) if owner is not None else None
    return v if v is not None else getattr(Qt, name)


def _scoped(cls, scope, name):
    owner = getattr(cls, scope, None)
    v = getattr(owner, name, None) if owner is not None else None
    return v if v is not None else getattr(cls, name)


class MiniChart(QWidget):
    """A small line chart drawn with QPainter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(160)
        self.setSizePolicy(_scoped(QSizePolicy, "Policy", "Expanding"),
                           _scoped(QSizePolicy, "Policy", "Fixed"))
        self.series, self.xr, self.yr = [], (0.0, 1.0), (0.0, 1.0)
        self.xlabel = self.ylabel = ""
        self.vline = self.point = None

    def set_data(self, series, xr, yr, xlabel, ylabel, vline=None, point=None):
        """series: [(points, color, width, dashed)]."""
        self.series, self.xr, self.yr = series, xr, yr
        self.xlabel, self.ylabel = xlabel, ylabel
        self.vline, self.point = vline, point
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt name
        p = QPainter(self)
        p.setRenderHint(_scoped(QPainter, "RenderHint", "Antialiasing"))
        fg = self.palette().color(_scoped(QPalette, "ColorRole", "WindowText"))
        f = QFont(self.font())
        f.setPointSizeF(max(6.5, f.pointSizeF() * 0.8))
        p.setFont(f)
        L, R, T, B = 42, 8, 6, 30
        w, h = self.width() - L - R, self.height() - T - B
        if w < 20 or h < 20:
            p.end()
            return
        x0, x1, xt = _nice(*self.xr, n=4)
        y0, y1, yt = _nice(*self.yr, n=4)

        def sx(x):
            return L + (x - x0) / (x1 - x0) * w

        def sy(y):
            return T + (1 - (min(max(y, y0), y1) - y0) / (y1 - y0)) * h
        grid = QPen(QColor(128, 128, 128, 60))
        for t in yt:
            p.setPen(grid)
            p.drawLine(QPointF(L, sy(t)), QPointF(L + w, sy(t)))
            p.setPen(fg)
            p.drawText(QPointF(2, sy(t) + 4), "{:g}".format(t))
        for t in xt:
            p.setPen(grid)
            p.drawLine(QPointF(sx(t), T), QPointF(sx(t), T + h))
            p.setPen(fg)
            s = "{:g}".format(t)
            p.drawText(QPointF(sx(t) - 3 * len(s), T + h + 12), s)
        p.setPen(QPen(QColor(128, 128, 128)))
        p.drawRect(L, T, int(w), int(h))
        for pts, col, width, dashed in self.series:
            pts = [(x, y) for x, y in pts if y is not None and math.isfinite(y)]
            if len(pts) < 2:
                continue
            path = QPainterPath(QPointF(sx(pts[0][0]), sy(pts[0][1])))
            for x, y in pts[1:]:
                path.lineTo(QPointF(sx(x), sy(y)))
            pen = QPen(QColor(col))
            pen.setWidthF(width)
            if dashed:
                pen.setStyle(_enum("PenStyle", "DashLine"))
            p.setPen(pen)
            p.drawPath(path)
        blue = QColor("#1d4ed8")
        if self.vline is not None:
            pen = QPen(blue)
            pen.setStyle(_enum("PenStyle", "DashLine"))
            p.setPen(pen)
            p.drawLine(QPointF(sx(self.vline), T), QPointF(sx(self.vline), T + h))
        if self.point is not None:
            p.setPen(QPen(QColor("white")))
            p.setBrush(blue)
            p.drawEllipse(QPointF(sx(self.point[0]), sy(self.point[1])), 4.5, 4.5)
        p.setPen(fg)
        p.drawText(QPointF(L + w / 2 - 2.6 * len(self.xlabel), T + h + 26), self.xlabel)
        p.save()
        p.translate(10, T + h / 2 + 2.6 * len(self.ylabel))
        p.rotate(-90)
        p.drawText(QPointF(0, 0), self.ylabel)
        p.restore()
        p.end()


def _legend(items):
    return " ".join("<span style='color:{}'>{}</span>&nbsp;{}".format(
        c, "- - -" if dashed else "&#9644;", t) for t, c, dashed in items)


class ExplorerDock(QDockWidget):
    def __init__(self, iface):
        super().__init__("Ditch level explorer", iface.mainWindow())
        self.setObjectName("TileOutletSubmergenceExplorer")
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.runs = {}
        self.run = None
        self.steps = {}
        self.want_run = None
        self.pick = None
        self.marker = None
        self.tool = None
        self.prev_tool = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.refresh_runs)
        self._build()
        prj = QgsProject.instance()
        prj.layersAdded.connect(self.schedule_refresh)
        prj.layersRemoved.connect(self.schedule_refresh)
        prj.readProject.connect(self.schedule_refresh)
        prj.cleared.connect(self.schedule_refresh)
        self.refresh_runs()

    # ------------------------------------------------------------ layout
    def _build(self):
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(6, 6, 6, 6)
        self.combo = QComboBox()
        self.combo.setToolTip("Runs of the tool whose outputs are in the project")
        self.combo.currentIndexChanged.connect(self.on_run_changed)
        v.addWidget(self.combo)
        self.hint = QLabel("Run the Tile outlet submergence tool. Its drainage-rate and pipe "
                           "layers show up here.")
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)

        self.content = QWidget()
        c = QVBoxLayout(self.content)
        c.setContentsMargins(0, 0, 0, 0)
        self.up = QPushButton("▲")
        self.up.setToolTip("Raise the ditch level (Up arrow key)")
        self.dn = QPushButton("▼")
        self.dn.setToolTip("Lower the ditch level (Down arrow key)")
        for b in (self.up, self.dn):
            f = b.font()
            f.setPointSizeF(f.pointSizeF() * 1.4)
            b.setFont(f)
        self.up.clicked.connect(lambda: self.step(1))
        self.dn.clicked.connect(lambda: self.step(-1))
        self.h1, self.h2, self.h3, self.nlab = QLabel(), QLabel(), QLabel(), QLabel()
        f = self.h1.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.h1.setFont(f)
        center = _enum("AlignmentFlag", "AlignHCenter")
        for lab in (self.h1, self.h2, self.h3, self.nlab):
            lab.setAlignment(center)
            lab.setWordWrap(True)
        c.addWidget(self.up)
        for lab in (self.h1, self.h2, self.h3, self.nlab):
            c.addWidget(lab)
        c.addWidget(self.dn)

        c.addWidget(self._heading("System outflow"))
        self.qlab, self.dqlab = QLabel(), QLabel()
        f = self.qlab.font()
        f.setBold(True)
        self.qlab.setFont(f)
        self.qlab.setWordWrap(True)
        self.dqlab.setWordWrap(True)
        c.addWidget(self.qlab)
        c.addWidget(self.dqlab)
        self.flap = QLabel()
        self.flap.setWordWrap(True)
        c.addWidget(self.flap)

        c.addWidget(self._heading("Rating curve"))
        self.rchart = MiniChart()
        c.addWidget(self.rchart)
        c.addWidget(self._heading("Main profile"))
        self.pchart = MiniChart()
        self.pchart.setMinimumHeight(180)
        c.addWidget(self.pchart)
        self.plegend = QLabel(_legend([("Ground", "#166534", False),
                                       ("Design water table", "#a16207", True),
                                       ("Crown", "#9ca3af", True), ("Invert", "#4b5563", False),
                                       ("HGL free", "#7dd3fc", False),
                                       ("HGL at this level", "#1d4ed8", False)]))
        self.plegend.setWordWrap(True)
        c.addWidget(self.plegend)

        c.addWidget(self._heading("Show"))
        row = QHBoxLayout()
        self.show_group = QButtonGroup(self)
        for k, t in enumerate(("Drainage rate", "Pipes", "Both")):
            rb = QRadioButton(t)
            self.show_group.addButton(rb, k)
            row.addWidget(rb)
        self.show_group.button(2).setChecked(True)
        self.show_group.buttonClicked.connect(self.on_show)
        row.addStretch(1)
        c.addLayout(row)

        c.addWidget(self._heading("At a point"))
        self.pick_btn = QToolButton()
        self.pick_btn.setText("Pick a point on the map")
        self.pick_btn.setCheckable(True)
        self.pick_btn.toggled.connect(self.on_pick_toggled)
        c.addWidget(self.pick_btn)
        self.read = QLabel()
        self.read.setWordWrap(True)
        self.read.setTextFormat(_enum("TextFormat", "RichText"))
        c.addWidget(self.read)
        c.addStretch(1)
        v.addWidget(self.content)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(_scoped(QFrame, "Shape", "NoFrame"))
        scroll.setWidget(body)
        self.setWidget(scroll)
        self.setMinimumWidth(280)
        ctx = _enum("ShortcutContext", "WidgetWithChildrenShortcut")
        for key, d in (("Up", 1), ("Down", -1)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(ctx)
            sc.activated.connect(lambda d=d: self.step(d))

    @staticmethod
    def _heading(text):
        lab = QLabel("<b>{}</b>".format(text))
        lab.setContentsMargins(0, 8, 0, 0)
        return lab

    # ------------------------------------------------------------ runs
    def schedule_refresh(self, *args):
        self._timer.start()

    def on_new_run(self, run):
        """Called when the tool has loaded and tagged an output layer of a new run."""
        self.want_run = run
        self.schedule_refresh()

    def refresh_runs(self):
        runs = {}
        for lyr in QgsProject.instance().mapLayers().values():
            prop = lyr.customProperty(PROP_KEY)
            if not prop:
                continue
            try:
                meta = json.loads(prop)
                run = meta["run"]
            except (ValueError, KeyError, TypeError):
                continue
            d = runs.setdefault(run, {"meta": meta, "raster": None, "pipes": None})
            if isinstance(lyr, QgsRasterLayer):
                d["raster"] = lyr.id()
            elif isinstance(lyr, QgsVectorLayer):
                d["pipes"] = lyr.id()
        self.runs = runs
        keep = self.want_run if self.want_run in runs else self.run
        self.combo.blockSignals(True)
        self.combo.clear()
        for run, d in sorted(runs.items(), key=lambda kv: kv[1]["meta"].get("name", "")):
            self.combo.addItem(d["meta"].get("name", run), run)
        k = self.combo.findData(keep) if keep in runs else self.combo.count() - 1
        self.combo.setCurrentIndex(k)
        self.combo.blockSignals(False)
        self.combo.setVisible(bool(runs))
        self.hint.setVisible(not runs)
        self.content.setVisible(bool(runs))
        if self.want_run in runs:
            self.want_run = None
            self.show()
            self.raise_()
        self.on_run_changed()

    def on_run_changed(self, *args):
        run = self.combo.currentData() if self.combo.count() else None
        self.run = run if run in self.runs else None
        if self.run is None:
            self._clear_pick()
            return
        meta = self.runs[self.run]["meta"]
        self.set_step(self.steps.get(self.run, meta.get("map_index", 0)))

    def _layer(self, key):
        if self.run is None:
            return None
        lid = self.runs[self.run].get(key)
        return QgsProject.instance().mapLayer(lid) if lid else None

    # ------------------------------------------------------------ stepping
    def step(self, d):
        if self.run is not None:
            self.set_step(self.steps.get(self.run, 0) + d)

    def set_step(self, i):
        meta = self.runs[self.run]["meta"]
        n = len(meta["steps"]["level"])
        i = max(0, min(n - 1, int(i)))
        self.steps[self.run] = i
        self._apply_layers(i)
        h1, h2, h3 = level_text(meta, i)
        self.h1.setText(h1)
        self.h2.setText(h2)
        self.h3.setText(h3)
        self.nlab.setText("Step {} of {}".format(i + 1, n))
        self.up.setEnabled(i < n - 1)
        self.dn.setEnabled(i > 0)
        q, dq = flow_text(meta, i)
        self.qlab.setText(q)
        self.dqlab.setText(dq)
        warn, txt = flap_text(meta, i)
        self.flap.setText(txt)
        self.flap.setStyleSheet(
            "padding:4px 6px;color:#111;background:{};border-left:5px solid {};".format(
                *(("#fef2f2", "#b91c1c") if warn else ("#f0fdf4", "#16a34a"))))
        self._charts(meta, i)
        self._readout()

    def _apply_layers(self, i):
        ras = self._layer("raster")
        if ras is not None and i + 1 <= ras.bandCount():
            r = ras.renderer()
            if hasattr(r, "setInputBand"):
                r.setInputBand(i + 1)
            elif hasattr(r, "setBand"):
                r.setBand(i + 1)
            ras.triggerRepaint()
            self._refresh_legend(ras)
        pipes = self._layer("pipes")
        if pipes is not None and pipes.fields().indexOf(rise_field(i)) >= 0:
            r = pipes.renderer()
            if isinstance(r, QgsGraduatedSymbolRenderer):
                r.setClassAttribute(rise_field(i))
                pipes.triggerRepaint()
                self._refresh_legend(pipes)

    def _refresh_legend(self, layer):
        try:
            self.iface.layerTreeView().refreshLayerSymbology(layer.id())
        except (AttributeError, RuntimeError):
            pass

    def _charts(self, meta, i):
        st, lu = meta["steps"], meta["units"]["l"]
        pts = [(lv, p) for lv, p in zip(st["level"], st["pct"]) if lv is not None]
        lv = st["level"][i]
        xmax = max([x for x, _ in pts] + [1e-6])
        self.rchart.set_data([(pts, "#13294B", 2.0, False)], (0.0, xmax), (0.0, 100.0),
                             "Ditch level above invert ({})".format(lu), "% of free outlet",
                             vline=lv, point=None if lv is None else (lv, st["pct"][i]))
        pr = meta["profile"]
        d = pr["dist"]
        vals = [v for k in ("ground", "invert", "crown", "wt") for v in pr[k] if v is not None]
        vals += [v for h in pr["hgl"] for v in h if v is not None]
        if not d or not vals:
            return
        ser = [(list(zip(d, pr["ground"])), "#166534", 1.6, False),
               (list(zip(d, pr["wt"])), "#a16207", 1.3, True),
               (list(zip(d, pr["crown"])), "#9ca3af", 1.2, True),
               (list(zip(d, pr["invert"])), "#4b5563", 1.4, False),
               (list(zip(d, pr["hgl"][0])), "#7dd3fc", 1.6, False),
               (list(zip(d, pr["hgl"][i])), "#1d4ed8", 2.4, False)]
        self.pchart.set_data(ser, (min(d), max(d)), (min(vals), max(vals)),
                             "Distance up the main ({})".format(lu), "Elevation ({})".format(lu))

    # ------------------------------------------------------------ display toggle
    def on_show(self, *args):
        k = self.show_group.checkedId()
        root = QgsProject.instance().layerTreeRoot()
        for key, on in (("raster", k in (0, 2)), ("pipes", k in (1, 2))):
            lyr = self._layer(key)
            node = root.findLayer(lyr.id()) if lyr is not None else None
            if node is not None:
                node.setItemVisibilityChecked(on)

    # ------------------------------------------------------------ pick a point
    def on_pick_toggled(self, on):
        if on:
            self.tool = QgsMapToolEmitPoint(self.canvas)
            self.tool.canvasClicked.connect(self.on_click)
            self.tool.deactivated.connect(self._tool_off)
            self.prev_tool = self.canvas.mapTool()
            self.canvas.setMapTool(self.tool)
            if self.pick is None:
                self.read.setText("Click a pipe or a field cell on the map.")
        elif self.tool is not None:
            tool, self.tool = self.tool, None
            if self.canvas.mapTool() is tool:
                if self.prev_tool is not None:
                    self.canvas.setMapTool(self.prev_tool)
                else:
                    self.canvas.unsetMapTool(tool)

    def _tool_off(self):
        if self.pick_btn.isChecked():
            self.tool = None
            self.pick_btn.setChecked(False)

    def on_click(self, point, button=None):
        self.pick = QgsPointXY(point)
        if self.marker is None:
            self.marker = QgsVertexMarker(self.canvas)
            self.marker.setIconType(_scoped(QgsVertexMarker, "IconType", "ICON_CIRCLE"))
            self.marker.setIconSize(12)
            self.marker.setPenWidth(2)
            self.marker.setColor(QColor("#111111"))
        self.marker.setCenter(self.pick)
        self._readout()

    def _clear_pick(self):
        self.pick = None
        if self.marker is not None:
            self.canvas.scene().removeItem(self.marker)
            self.marker = None
        self.read.setText("")

    def _xform(self, layer):
        return QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), layer.crs(),
                                      QgsProject.instance())

    def _readout(self):
        if self.run is None:
            return
        if self.pick is None:
            self.read.setText("Turn on <i>Pick a point</i> and click a pipe or a field cell.")
            return
        meta = self.runs[self.run]["meta"]
        i = self.steps.get(self.run, 0)
        lu, qu = meta["units"]["l"], meta["units"]["q"]
        out = []
        try:
            pipes = self._layer("pipes")
            if pipes is not None and pipes.fields().indexOf(q_field(i)) >= 0:
                xf = self._xform(pipes)
                tol = self.canvas.mapUnitsPerPixel() * 6
                rect = QgsRectangle(self.pick.x() - tol, self.pick.y() - tol,
                                    self.pick.x() + tol, self.pick.y() + tol)
                rr = xf.transformBoundingBox(rect)
                pg = QgsGeometry.fromPointXY(xf.transform(self.pick))
                lim = max(rr.width(), rr.height()) / 2
                best, bd = None, None
                for f in pipes.getFeatures(QgsFeatureRequest().setFilterRect(rr)):
                    dd = f.geometry().distance(pg)
                    if dd <= lim and (bd is None or dd < bd):
                        best, bd = f, dd
                if best is not None:
                    out.append("<b>Pipe</b>: {}, {:g} in<br>Flow {:.4f} {} (free outlet "
                               "{:.4f})<br>HGL raised {:.2f} {}".format(
                                   best["kind"], float(best["d_in"]), float(best[q_field(i)]),
                                   qu, float(best[q_field(0)]), float(best[rise_field(i)]), lu))
            ras = self._layer("raster")
            if ras is not None and i + 1 <= ras.bandCount():
                pr = self._xform(ras).transform(self.pick)
                res = ras.dataProvider().sample(pr, i + 1)
                val, ok = res if isinstance(res, tuple) else (res, True)
                if not ok or val is None or not math.isfinite(val) or val >= NODATA - 0.5:
                    out.append("<b>Field</b>: not in the drained area")
                else:
                    out.append("<b>Field</b>: drains at {:.0f}% of the free-outlet rate"
                               .format(val))
        except Exception as err:  # noqa: BLE001 - show it, don't break the panel
            out.append("Could not read the layers here: {}".format(err))
        self.read.setText("<br>".join(out) if out else "Nothing here: click on a pipe or "
                          "inside the drained area.")

    # ------------------------------------------------------------ cleanup
    def cleanup(self):
        prj = QgsProject.instance()
        for sig in (prj.layersAdded, prj.layersRemoved, prj.readProject, prj.cleared):
            try:
                sig.disconnect(self.schedule_refresh)
            except (TypeError, RuntimeError):
                pass
        if self.pick_btn.isChecked():
            self.pick_btn.setChecked(False)
        self._clear_pick()
