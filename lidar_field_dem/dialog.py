# -*- coding: utf-8 -*-
"""Launcher window: pick a field, see the LiDAR collections available, build the DEM."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
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
    QgsMapLayerProxyModel,
    QgsProcessingFeatureSourceDefinition,
    QgsProcessingFeedback,
    QgsProject,
)
from qgis.gui import QgsMapLayerComboBox

from .algorithms.collections import field_to_wgs, list_collections

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

        self.status = QLabel("Choose a field and click Find collections.")
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
