"""Dialog: choose a raster/band, run the scan, show and map the results."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QGuiApplication
from qgis.PyQt.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QApplication,
)
from qgis.core import (
    Qgis, QgsMessageLog, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsFeature, QgsField, QgsGeometry, QgsMapLayerProxyModel,
    QgsMarkerSymbol, QgsPointXY, QgsProject, QgsRendererCategory, QgsVectorLayer,
)
from qgis.gui import QgsMapLayerComboBox, QgsRasterBandComboBox

from .extremes import cell_center
from .raster_reader import ExtremesError, find_extremes

try:  # QGIS >= 3.38 prefers QMetaType
    from qgis.PyQt.QtCore import QMetaType
    _T = {"str": QMetaType.Type.QString, "double": QMetaType.Type.Double,
          "int": QMetaType.Type.Int}
except (ImportError, AttributeError):
    _T = None


def _field(name, kind):
    if _T is not None:
        try:
            return QgsField(name, _T[kind])
        except TypeError:
            pass
    from qgis.PyQt.QtCore import QVariant
    return QgsField(name, {"str": QVariant.String, "double": QVariant.Double,
                           "int": QVariant.Int}[kind])


def _fmt(v):
    return f"{v:.6g}" if isinstance(v, float) else str(v)


class RasterExtremesDialog(QDialog):
    COLS = ["Type", "Value", "Row", "Col", "X", "Y", "Lon (WGS84)", "Lat (WGS84)"]

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._cancel = False
        self._results = []  # list of dicts
        self.setWindowTitle("Raster Min / Max Cells")
        self.resize(760, 460)

        # --- inputs ---
        self.layer_cb = QgsMapLayerComboBox()
        self.layer_cb.setFilters(QgsMapLayerProxyModel.Filter.RasterLayer
                                 if hasattr(QgsMapLayerProxyModel, "Filter")
                                 else QgsMapLayerProxyModel.RasterLayer)
        self.band_cb = QgsRasterBandComboBox()
        self.band_cb.setLayer(self.layer_cb.currentLayer())
        self.layer_cb.layerChanged.connect(self.band_cb.setLayer)

        self.max_spin = QSpinBox()
        self.max_spin.setRange(1, 1_000_000)
        self.max_spin.setValue(100)
        self.max_spin.setToolTip("If several cells share the min or max value, "
                                 "list at most this many of each.")
        self.add_layer_chk = QCheckBox("Add result points as a layer")
        self.add_layer_chk.setChecked(True)

        form = QFormLayout()
        form.addRow("Raster layer", self.layer_cb)
        form.addRow("Band", self.band_cb)
        form.addRow("Max cells listed per extreme", self.max_spin)
        form.addRow("", self.add_layer_chk)

        # --- run row ---
        self.run_btn = QPushButton("Find min / max")
        self.run_btn.clicked.connect(self.run)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(lambda: setattr(self, "_cancel", True))
        self.progress = QProgressBar()
        self.progress.setValue(0)
        run_row = QHBoxLayout()
        run_row.addWidget(self.run_btn)
        run_row.addWidget(self.cancel_btn)
        run_row.addWidget(self.progress, 1)

        # --- results ---
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.cellDoubleClicked.connect(self.zoom_to_row)

        self.copy_btn = QPushButton("Copy table")
        self.copy_btn.clicked.connect(self.copy_table)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Double-click a row to zoom to the cell."))
        bottom.addStretch(1)
        bottom.addWidget(self.copy_btn)
        bottom.addWidget(close_btn)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(run_row)
        lay.addWidget(self.summary)
        lay.addWidget(self.table, 1)
        lay.addLayout(bottom)

    # ------------------------------------------------------------------
    def run(self):
        layer = self.layer_cb.currentLayer()
        band = self.band_cb.currentBand()
        if layer is None or band < 1:
            self.summary.setText("Choose a raster layer and band.")
            return

        self._cancel = False
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setValue(0)
        self.summary.setText("Scanning…")

        def progress(p):
            self.progress.setValue(p)
            QApplication.processEvents()

        try:
            tracker, g = find_extremes(layer, band, self.max_spin.value(),
                                       progress, lambda: self._cancel)
        except ExtremesError as e:
            self.summary.setText(f"<b>Error:</b> {e}")
            return
        finally:
            self.run_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)

        if tracker is None:
            self.summary.setText("Canceled.")
            return
        if tracker.valid_count == 0:
            self.summary.setText("The band contains only NoData cells.")
            self.table.setRowCount(0)
            return

        self._build_results(layer, tracker, g)
        self._fill_table()
        crs = layer.crs().authid() or layer.crs().description()

        def coords(kind):
            pts = [r for r in self._results if r["type"] == kind]
            txt = "; ".join(f"X = {r['x']:.6f}, Y = {r['y']:.6f}" for r in pts[:5])
            if len(pts) > 5:
                txt += f"; … (+{len(pts) - 5} more in table)"
            return txt

        max_xy, min_xy = coords("MAX"), coords("MIN")
        self.summary.setText(
            f"<b>Max</b> = {_fmt(tracker.max_value)} ({tracker.max_count} cell(s)) at "
            f"<b>{max_xy}</b><br>"
            f"<b>Min</b> = {_fmt(tracker.min_value)} ({tracker.min_count} cell(s)) at "
            f"<b>{min_xy}</b><br>"
            f"{tracker.valid_count:,} valid cells scanned. X/Y are cell centres in {crs}."
            + (" <i>Only the first cells are listed for ties.</i>"
               if max(tracker.max_count, tracker.min_count) > self.max_spin.value() else ""))

        # Also write the result to the Log Messages panel (tab "Raster Extremes")
        QgsMessageLog.logMessage(
            f"{layer.name()} band {band} ({crs}) | "
            f"MAX {_fmt(tracker.max_value)} at {max_xy} | "
            f"MIN {_fmt(tracker.min_value)} at {min_xy}",
            "Raster Extremes", Qgis.MessageLevel.Info)
        self.iface.messageBar().pushMessage(
            "Raster Extremes",
            f"Max {_fmt(tracker.max_value)} at {max_xy}  —  Min {_fmt(tracker.min_value)} at {min_xy}",
            Qgis.MessageLevel.Success, 15)

        if self.add_layer_chk.isChecked():
            self._add_layer(layer, band)

    def _build_results(self, layer, tracker, g):
        to_wgs = None
        wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        if layer.crs().isValid():
            to_wgs = QgsCoordinateTransform(layer.crs(), wgs, QgsProject.instance())

        self._results = []
        for kind, value, cells in (("MAX", tracker.max_value, tracker.max_cells_list),
                                   ("MIN", tracker.min_value, tracker.min_cells)):
            for row, col in cells:
                x, y = cell_center(g["xmin"], g["ymax"], g["xres"], g["yres"], row, col)
                lon = lat = None
                if to_wgs is not None:
                    try:
                        p = to_wgs.transform(QgsPointXY(x, y))
                        lon, lat = p.x(), p.y()
                    except Exception:
                        pass
                self._results.append(dict(type=kind, value=value, row=row, col=col,
                                          x=x, y=y, lon=lon, lat=lat))

    def _fill_table(self):
        self.table.setRowCount(len(self._results))
        for i, r in enumerate(self._results):
            vals = [r["type"], _fmt(r["value"]), str(r["row"]), str(r["col"]),
                    f"{r['x']:.6f}", f"{r['y']:.6f}",
                    "" if r["lon"] is None else f"{r['lon']:.7f}",
                    "" if r["lat"] is None else f"{r['lat']:.7f}"]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if j == 0:
                    item.setForeground(QColor("#c62828" if v == "MAX" else "#1565c0"))
                self.table.setItem(i, j, item)

    def _add_layer(self, raster, band):
        vl = QgsVectorLayer(f"Point?crs={raster.crs().authid()}",
                            f"{raster.name()} b{band} min-max", "memory")
        if not raster.crs().authid():
            vl.setCrs(raster.crs())
        pr = vl.dataProvider()
        pr.addAttributes([_field("type", "str"), _field("value", "double"),
                          _field("row", "int"), _field("col", "int"),
                          _field("x", "double"), _field("y", "double")])
        vl.updateFields()
        feats = []
        for r in self._results:
            f = QgsFeature(vl.fields())
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r["x"], r["y"])))
            f.setAttributes([r["type"], float(r["value"]), r["row"], r["col"], r["x"], r["y"]])
            feats.append(f)
        pr.addFeatures(feats)
        vl.updateExtents()

        cats = []
        for kind, color in (("MAX", "#e53935"), ("MIN", "#1e88e5")):
            sym = QgsMarkerSymbol.createSimple(
                {"name": "circle", "color": color, "size": "3.2",
                 "outline_color": "white", "outline_width": "0.5"})
            cats.append(QgsRendererCategory(kind, sym, kind))
        vl.setRenderer(QgsCategorizedSymbolRenderer("type", cats))
        QgsProject.instance().addMapLayer(vl)

    # ------------------------------------------------------------------
    def zoom_to_row(self, row, _col):
        if row >= len(self._results):
            return
        r = self._results[row]
        layer = self.layer_cb.currentLayer()
        canvas = self.iface.mapCanvas()
        pt = QgsPointXY(r["x"], r["y"])
        if layer is not None and layer.crs() != canvas.mapSettings().destinationCrs():
            pt = QgsCoordinateTransform(layer.crs(), canvas.mapSettings().destinationCrs(),
                                        QgsProject.instance()).transform(pt)
        canvas.setCenter(pt)
        canvas.refresh()
        canvas.flashGeometries([QgsGeometry.fromPointXY(pt)])

    def copy_table(self):
        lines = ["\t".join(self.COLS)]
        for i in range(self.table.rowCount()):
            lines.append("\t".join(self.table.item(i, j).text()
                                   for j in range(self.table.columnCount())))
        QGuiApplication.clipboard().setText("\n".join(lines))
