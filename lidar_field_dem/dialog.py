# -*- coding: utf-8 -*-
"""Launcher window: pick a field, see the LiDAR collections available, build the DEM."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from qgis.core import (
    QgsDistanceArea,
    QgsFeature,
    QgsMapLayerProxyModel,
    QgsProcessingFeatureSourceDefinition,
    QgsProcessingFeedback,
    QgsProject,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.gui import QgsMapLayerComboBox

from .algorithms.collections import field_to_wgs, list_collections
from .trace_tool import TraceFieldTool

SQ_M_PER_ACRE = 4046.8564224

COLS = ["Source", "Year", "Collection", "Cell size", "Covers field", "Quality", "Status"]


class _LogFeedback(QgsProcessingFeedback):
    def __init__(self):
        super().__init__()
        self.messages = []

    def pushInfo(self, info):  # noqa: N802
        self.messages.append(info)

    def reportError(self, error, fatalError=False):  # noqa: N802
        self.messages.append("ERROR: " + error)


class CollectionsDialog(QDialog):

    def __init__(self, iface, alg_id, icon_path, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface
        self.alg_id = alg_id
        self.rows = []
        self.setWindowTitle("LiDAR Field DEM")
        self.setWindowIcon(QIcon(icon_path))
        self.resize(820, 420)

        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Field boundary:"))
        self.layer_cb = QgsMapLayerComboBox()
        self.layer_cb.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        top.addWidget(self.layer_cb, 1)
        self.trace_btn = QPushButton("Trace on map…")
        self.trace_btn.setToolTip("Draw the field boundary on the map: click the corners, "
                                  "right-click or Enter to finish")
        self.trace_btn.clicked.connect(self.start_trace)
        top.addWidget(self.trace_btn)
        self.sel_only = QCheckBox("Selected features only")
        top.addWidget(self.sel_only)
        self.find_btn = QPushButton("Find collections")
        self.find_btn.setDefault(True)
        self.find_btn.clicked.connect(self.find)
        top.addWidget(self.find_btn)
        lay.addLayout(top)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._sel_changed)
        self.table.doubleClicked.connect(lambda *_: self.build())
        lay.addWidget(self.table, 1)

        self.status = QLabel("Choose a field layer, or trace one on the map, then click "
                             "Find collections.")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        bb = QDialogButtonBox()
        self.build_btn = bb.addButton("Build DEM with selected…", QDialogButtonBox.AcceptRole)
        self.build_btn.setEnabled(False)
        self.build_btn.clicked.connect(self.build)
        latest = bb.addButton("Build DEM (most recent)…", QDialogButtonBox.ActionRole)
        latest.clicked.connect(lambda: self.build(latest=True))
        bb.addButton(QDialogButtonBox.Close).clicked.connect(self.reject)
        lay.addWidget(bb)

        self.tool = None
        self._prev_tool = None
        self._tracing = False

    # ------------------------------------------------------------------
    # tracing a boundary on the map
    def start_trace(self):
        canvas = self.iface.mapCanvas()
        self._prev_tool = canvas.mapTool()
        self.tool = TraceFieldTool(canvas)
        self.tool.finished.connect(self._trace_done)
        self.tool.canceled.connect(self._trace_canceled)
        self._tracing = True
        canvas.mapToolSet.connect(self._tool_changed)
        canvas.setMapTool(self.tool)
        canvas.setFocus()
        self.hide()
        self.iface.messageBar().pushInfo(
            "LiDAR Field DEM",
            "Click the field's corners. Right-click or Enter to finish, "
            "Backspace to undo a corner, Esc to cancel.")

    def _end_trace(self):
        if not self._tracing:
            return
        self._tracing = False
        canvas = self.iface.mapCanvas()
        try:
            canvas.mapToolSet.disconnect(self._tool_changed)
        except Exception:
            pass
        if canvas.mapTool() is self.tool:
            if self._prev_tool is not None:
                canvas.setMapTool(self._prev_tool)
            else:
                canvas.unsetMapTool(self.tool)
        self.iface.messageBar().clearWidgets()
        self.show()
        self.raise_()
        self.activateWindow()

    def _tool_changed(self, new_tool, old_tool=None):
        if self._tracing and new_tool is not self.tool:
            self._prev_tool = None               # the user picked another tool; keep it
            self._end_trace()
            self.status.setText("Tracing was stopped because another map tool was chosen.")

    def _trace_canceled(self):
        self._end_trace()
        self.status.setText("Tracing canceled.")

    def _trace_done(self, geom):
        canvas = self.iface.mapCanvas()
        crs = canvas.mapSettings().destinationCrs()
        layer = QgsVectorLayer("Polygon?field=name:string(50)&field=acres:double",
                               "Traced field boundary", "memory")
        layer.setCrs(crs)
        acres = self._acres(geom, crs)
        f = QgsFeature(layer.fields())
        f.setGeometry(geom)
        f["name"] = "Traced field"
        f["acres"] = round(acres, 2) if acres is not None else None
        layer.dataProvider().addFeatures([f])
        layer.updateExtents()
        try:
            sym = layer.renderer().symbol()
            sym.setColor(QColor(0, 0, 0, 0))
            sl = sym.symbolLayer(0)
            sl.setStrokeColor(QColor(47, 107, 79))
            sl.setStrokeWidth(0.6)
        except Exception:
            pass
        QgsProject.instance().addMapLayer(layer)
        self._end_trace()
        self.layer_cb.setLayer(layer)
        self.sel_only.setChecked(False)
        self.rows = []
        self._fill()
        size = (" (%.1f acres)" % acres) if acres is not None else ""
        self.status.setText("Traced field added to the map as 'Traced field boundary'%s. "
                            "Click Find collections. Use Export > Save Features As to keep it."
                            % size)

    @staticmethod
    def _acres(geom, crs):
        try:
            da = QgsDistanceArea()
            da.setSourceCrs(crs, QgsProject.instance().transformContext())
            da.setEllipsoid(QgsProject.instance().ellipsoid() or "EPSG:7019")
            a = da.convertAreaMeasurement(da.measureArea(geom), QgsUnitTypes.AreaSquareMeters)
            return a / SQ_M_PER_ACRE
        except Exception:
            return None

    def closeEvent(self, e):  # noqa: N802
        if self._tracing:
            self._end_trace()
        super().closeEvent(e)

    # ------------------------------------------------------------------
    def _field_geoms(self):
        layer = self.layer_cb.currentLayer()
        if layer is None:
            raise RuntimeError("Choose a polygon layer with the field boundary.")
        feats = layer.selectedFeatures() if self.sel_only.isChecked() else layer.getFeatures()
        geoms = [f.geometry() for f in feats if f.hasGeometry()]
        if not geoms:
            raise RuntimeError("No field polygons" +
                               (" selected." if self.sel_only.isChecked() else " in the layer."))
        return layer, geoms

    def find(self):
        try:
            layer, geoms = self._field_geoms()
        except RuntimeError as e:
            QMessageBox.warning(self, "LiDAR Field DEM", str(e))
            return
        self.status.setText("Searching ISGS and USGS 3DEP…")
        self.find_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        fb = _LogFeedback()
        try:
            field_wgs = field_to_wgs(geoms, layer.crs(),
                                     QgsProject.instance().transformContext())
            self.rows = list_collections(field_wgs, fb)
        except Exception as e:
            self.rows = []
            fb.messages.append("ERROR: %s" % e)
        finally:
            QApplication.restoreOverrideCursor()
            self.find_btn.setEnabled(True)
        self._fill()
        errs = [m for m in fb.messages if m.startswith("ERROR")]
        n_isgs = sum(1 for r in self.rows if r["source"] == "ISGS")
        if not self.rows:
            msg = "No LiDAR collections found for this field."
        else:
            msg = "%d collection(s) found (%d ISGS, %d USGS 3DEP). Select one and click Build DEM." % (
                len(self.rows), n_isgs, len(self.rows) - n_isgs)
            if n_isgs == 0:
                msg += " The field is outside ISGS coverage, so only USGS 3DEP is listed."
        if errs:
            msg += "\n" + "\n".join(errs)
        self.status.setText(msg)

    def _fill(self):
        self.table.setRowCount(0)
        for r in self.rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            vals = [r["source"], r["year"] or "?", r["collection"],
                    ("%g ft" % r["cell_ft"]) if r["cell_ft"] else "-",
                    ("%.0f%%" % r["coverage"]) if r["coverage"] is not None else "-",
                    ("QL%s" % r["quality"]) if r["quality"] not in (None, "") else "",
                    r["status"] + ("" if r["usable"] else " (no LAS download)")]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if not r["usable"]:
                    it.setFlags(it.flags() & ~Qt.ItemIsEnabled)
                self.table.setItem(i, j, it)
        if self.rows:
            for i, r in enumerate(self.rows):
                if r["usable"]:
                    self.table.selectRow(i)
                    break

    def _sel_changed(self):
        r = self._selected()
        self.build_btn.setEnabled(bool(r and r["usable"]))

    def _selected(self):
        idx = self.table.selectionModel().selectedRows()
        if not idx:
            return None
        return self.rows[idx[0].row()]

    def build(self, latest=False):
        try:
            layer, _ = self._field_geoms()
        except RuntimeError as e:
            QMessageBox.warning(self, "LiDAR Field DEM", str(e))
            return
        if self.sel_only.isChecked():
            inp = QgsProcessingFeatureSourceDefinition(layer.id(), True)
        else:
            inp = layer.id()
        params = {"INPUT": inp}
        if not latest:
            r = self._selected()
            if not r or not r["usable"]:
                return
            params["SOURCE"] = 1 if r["source"] == "ISGS" else 2
            params["YEAR"] = r["year"]
        import processing
        self.accept()
        processing.execAlgorithmDialog(self.alg_id, params)
