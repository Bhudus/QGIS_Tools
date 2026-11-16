"""Tile Size Calculator panel: set drainage coefficients and resize the pipes of an
Illini Subsurface Drain Layout network."""
from qgis.core import (
    QgsFeatureRequest,
    QgsField,
    QgsProject,
    QgsSettings,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsMapLayerComboBox, QgsMapToolIdentifyFeature, QgsRubberBand
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import sizing as K
from .size_core import (
    SizeError,
    _num,
    changes_for,
    check_records,
    layer_spacing,
    orders_present,
    pipe_key,
    pipe_label,
    pipe_sections,
    resize,
    row_differs,
)

try:
    from qgis.core import Qgis
    LINE_FILTER = Qgis.LayerFilter.LineLayer
except (ImportError, AttributeError):
    from qgis.core import QgsMapLayerProxyModel
    LINE_FILTER = QgsMapLayerProxyModel.LineLayer

try:
    LINE_GEOMETRY = Qgis.GeometryType.Line
except (NameError, AttributeError):
    LINE_GEOMETRY = QgsWkbTypes.LineGeometry

SETTINGS = "tile_size_calculator/"
MODES = ("single", "order", "pipe")


def _value(v):
    """QGIS attribute value -> Python value, NULL -> None."""
    if v is None:
        return None
    if isinstance(v, QVariant):
        return None if v.isNull() else v.value()
    try:
        if v.isNull():
            return None
    except AttributeError:
        pass
    return v


def _string_field(name):
    """A text field, under Qt 5 (QVariant) or Qt 6 (QMetaType)."""
    try:
        return QgsField(name, QVariant.String)
    except AttributeError:
        from qgis.PyQt.QtCore import QMetaType
        return QgsField(name, QMetaType.Type.QString)


def _coeff_box(value=K.DEFAULT_COEFF):
    b = QDoubleSpinBox()
    b.setDecimals(3)
    b.setRange(0.001, 100.0)
    b.setSingleStep(0.025)
    b.setSuffix(" in/day")
    b.setValue(value)
    return b


class TileSizeDock(QDockWidget):
    def __init__(self, iface, parent=None):
        super().__init__("Tile Size Calculator", parent)
        self.setObjectName("TileSizeCalculatorDock")
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.layer = None
        self.records = []
        self.rows, self.summary = [], None
        self.overrides = {}                 # pipe key -> coefficient (pipe-by-pipe mode)
        self.labels = {}                    # pipe key -> label
        self.sections = {}                  # fid -> pipe type of a line set on its own
        self.picked = None                  # pipe key of the pipe picked on the map
        self.section_rows = []              # (fid, from, to) of the picked pipe
        self.order_boxes = {}               # order -> QDoubleSpinBox
        self.tool = None
        self._ready = False

        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 4, 6, 4)

        row1 = QHBoxLayout()
        self.layer_box = QgsMapLayerComboBox()
        self.layer_box.setFilters(LINE_FILTER)
        self.layer_box.setAllowEmptyLayer(True)
        self.layer_box.setToolTip("The Drainage network layer of Illini Subsurface Drain "
                                  "Layout")
        self.spacing = QSpinBox()
        self.spacing.setRange(1, 10000)
        self.spacing.setSuffix(" ft")
        self.spacing.setToolTip("Drain spacing for Routine K (DRAIN_SPACE in the layer)")
        self.material = QComboBox()
        self.material.addItems(["As in the layer"] + K.MATERIALS)
        self.material.setToolTip("Pipe type (material, for Manning's n) of every line: as in "
                                 "the layer's PIPE_TYPE field, or one type for all. Sections "
                                 "set on their own below keep theirs.")
        row1.addWidget(QLabel("Network"))
        row1.addWidget(self.layer_box, 2)
        row1.addWidget(QLabel("Drain spacing"))
        row1.addWidget(self.spacing)
        row1.addWidget(QLabel("Pipe"))
        row1.addWidget(self.material)
        v.addLayout(row1)

        row2 = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_btns = []
        for i, text in enumerate(("One coefficient", "By Strahler order", "Pipe by pipe")):
            b = QRadioButton(text)
            self.mode_group.addButton(b, i)
            self.mode_btns.append(b)
            row2.addWidget(b)
        self.mode_btns[0].setChecked(True)
        row2.addSpacing(12)
        self.coeff_label = QLabel("Drainage coefficient")
        self.coeff = _coeff_box()
        row2.addWidget(self.coeff_label)
        row2.addWidget(self.coeff)
        row2.addStretch(1)
        v.addLayout(row2)

        # by order: a coefficient per Strahler order
        self.order_panel = QWidget()
        op = QHBoxLayout(self.order_panel)
        op.setContentsMargins(0, 0, 0, 0)
        self.order_row = QHBoxLayout()
        op.addLayout(self.order_row)
        op.addStretch(1)
        v.addWidget(self.order_panel)

        # pick a pipe: its sections (pipe type) and, pipe by pipe, its coefficient
        row3 = QHBoxLayout()
        self.pick_btn = QPushButton("Pick a pipe")
        self.pick_btn.setCheckable(True)
        self.pick_btn.setToolTip("Click a pipe on the map to see its sections, set their "
                                 "pipe type and, pipe by pipe, its drainage coefficient")
        self.pick_label = QLabel("Pick a pipe on the map to see its sections.")
        self.pipe_panel = QWidget()
        pp = QHBoxLayout(self.pipe_panel)
        pp.setContentsMargins(0, 0, 0, 0)
        self.pipe_coeff = _coeff_box()
        self.pipe_coeff.setEnabled(False)
        self.set_btn = QPushButton("Set for this pipe")
        self.set_btn.setEnabled(False)
        pp.addWidget(self.pipe_coeff)
        pp.addWidget(self.set_btn)
        self.clear_btn = QPushButton("Clear settings")
        self.clear_btn.setToolTip("Forget the pipe coefficients and section pipe types set "
                                  "here")
        self.clear_btn.setEnabled(False)
        row3.addWidget(self.pick_btn)
        row3.addWidget(self.pick_label, 1)
        row3.addWidget(self.pipe_panel)
        row3.addWidget(self.clear_btn)
        v.addLayout(row3)

        split = QSplitter(Qt.Horizontal)
        self.sizes = QTableWidget(0, 3)
        self.sizes.setHorizontalHeaderLabels(["Nominal size", "Length now", "Length after"])
        self.sizes.verticalHeader().setVisible(False)
        self.sizes.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.pipes = QTableWidget(0, 4)
        self.pipes.setHorizontalHeaderLabels(["Pipe", "Kind", "Coefficient", "Sizes"])
        self.pipes.verticalHeader().setVisible(False)
        self.pipes.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.pipes.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.pipes.setToolTip("Pipes with their own coefficient. Click one to show it; "
                              "Delete removes it.")
        self.sect = QTableWidget(0, 6)
        self.sect.setHorizontalHeaderLabels(["Section", "Stations", "Order", "Coefficient",
                                             "Size", "Pipe type"])
        self.sect.verticalHeader().setVisible(False)
        self.sect.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sect.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sect.setToolTip("The lines of the picked pipe, from its downstream end up "
                             "(stations from that end). Choose a pipe type for any of them; "
                             "click a row to show it on the map.")
        self.sect.hide()
        split.addWidget(self.sizes)
        split.addWidget(self.sect)
        split.addWidget(self.pipes)
        v.addWidget(split, 1)

        row4 = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("color: #444;")
        self.status.setWordWrap(True)
        self.reload_btn = QPushButton("Reload layer")
        self.apply_btn = QPushButton("Apply to layer…")
        self.apply_btn.setEnabled(False)
        row4.addWidget(self.status, 1)
        row4.addWidget(self.reload_btn)
        row4.addWidget(self.apply_btn)
        v.addLayout(row4)
        self.setWidget(w)

        self.band = QgsRubberBand(self.canvas, LINE_GEOMETRY)      # the picked pipe
        self.band.setColor(QColor(255, 196, 0, 170))
        self.band.setWidth(6)
        self.set_band = QgsRubberBand(self.canvas, LINE_GEOMETRY)  # pipes with their own value
        self.set_band.setColor(QColor(234, 88, 12, 150))
        self.set_band.setWidth(4)
        self.sect_band = QgsRubberBand(self.canvas, LINE_GEOMETRY)  # the section clicked
        self.sect_band.setColor(QColor(220, 38, 38, 200))
        self.sect_band.setWidth(8)

        self._restore()
        self.layer_box.layerChanged.connect(self._layer_changed)
        self.mode_group.buttonClicked.connect(self._mode_changed)
        self.coeff.valueChanged.connect(self._settings_changed)
        self.material.currentIndexChanged.connect(self._settings_changed)
        self.spacing.valueChanged.connect(self._settings_changed)
        self.pick_btn.toggled.connect(self._toggle_pick)
        self.set_btn.clicked.connect(self._set_pipe)
        self.clear_btn.clicked.connect(self._clear_pipes)
        self.pipes.cellClicked.connect(self._pipe_row_clicked)
        self.sect.cellClicked.connect(self._section_clicked)
        self.reload_btn.clicked.connect(self.reload)
        self.apply_btn.clicked.connect(self._apply)
        self._ready = True
        self._pick_network_layer()
        self._layer_changed(self.layer_box.currentLayer())

    # ------------------------------------------------------------ settings
    def _restore(self):
        s = QgsSettings()
        self.coeff.setValue(float(s.value(SETTINGS + "coeff", K.DEFAULT_COEFF)))
        self.material.setCurrentIndex(int(s.value(SETTINGS + "pipe_type", 0)))
        m = str(s.value(SETTINGS + "mode", "single"))
        self.mode_btns[MODES.index(m) if m in MODES else 0].setChecked(True)
        self._update_mode_panels()

    def _save(self):
        if not self._ready:
            return
        s = QgsSettings()
        s.setValue(SETTINGS + "coeff", self.coeff.value())
        s.setValue(SETTINGS + "pipe_type", self.material.currentIndex())
        s.setValue(SETTINGS + "mode", self._mode())
        for o, b in self.order_boxes.items():
            s.setValue(SETTINGS + "order_{}".format(o), b.value())

    def _mode(self):
        return MODES[max(self.mode_group.checkedId(), 0)]

    def _update_mode_panels(self):
        m = self._mode()
        self.order_panel.setVisible(m == "order")
        self.pipe_panel.setVisible(m == "pipe")
        self.pipes.setVisible(m == "pipe")
        self.pick_label.setToolTip("")
        self.coeff_label.setText({"single": "Drainage coefficient",
                                  "order": "Orders not listed",
                                  "pipe": "Lines without a D_COEFF"}[m])

    # ------------------------------------------------------------ layer
    @staticmethod
    def _is_network(layer):
        return (isinstance(layer, QgsVectorLayer)
                and layer.fields().indexOf("FLOW_LENGTH") >= 0
                and layer.fields().indexOf("TILE_ORDER") >= 0)

    def _pick_network_layer(self):
        if self._is_network(self.layer_box.currentLayer()):
            return
        for layer in QgsProject.instance().mapLayers().values():
            if self._is_network(layer):
                self.layer_box.setLayer(layer)
                return

    def _layer_changed(self, layer, *args):
        if layer is not self.layer:
            self.overrides.clear()
            self.sections.clear()
            self.picked = None
            if self.pick_btn.isChecked():
                self.pick_btn.setChecked(False)
        self.layer = layer if isinstance(layer, QgsVectorLayer) else None
        self.reload()

    def reload(self, *args):
        """Read the layer's lines again (after edits made elsewhere)."""
        self.records = []
        if self.layer is not None:
            names = self.layer.fields().names()
            for f in self.layer.getFeatures():
                self.records.append({"fid": f.id(),
                                     "attrs": {n: _value(f[n]) for n in names}})
        self.labels = {pipe_key(r["attrs"], r["fid"]): pipe_label(r["attrs"])
                       for r in self.records}
        sp = layer_spacing(self.records) if self.records else None
        self.spacing.blockSignals(True)
        if sp:
            self.spacing.setValue(sp)
        self.spacing.blockSignals(False)
        self._build_order_boxes()
        self._recalc()

    def _build_order_boxes(self):
        while self.order_row.count():
            item = self.order_row.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.order_boxes = {}
        orders = orders_present(self.records) if self.records else []
        s = QgsSettings()
        counts = {}
        for r in self.records:
            o = _num(r["attrs"], "TILE_ORDER")
            if o is not None:
                counts[int(o)] = counts.get(int(o), 0) + 1
        for o in orders:
            b = _coeff_box(float(s.value(SETTINGS + "order_{}".format(o), self.coeff.value())))
            b.setToolTip("Drainage coefficient for the {} line(s) of Strahler order {}"
                         .format(counts.get(o, 0), o))
            b.valueChanged.connect(self._settings_changed)
            self.order_row.addWidget(QLabel("Order {}".format(o)))
            self.order_row.addWidget(b)
            self.order_boxes[o] = b

    # ------------------------------------------------------------ calculation
    def _mode_changed(self, *args):
        self._update_mode_panels()
        self._settings_changed()

    def _settings_changed(self, *args):
        self._save()
        self._recalc()

    def _recalc(self):
        self.rows, self.summary = [], None
        self.apply_btn.setEnabled(False)
        if self.layer is None:
            self.status.setText("Choose the Drainage network layer of Illini Subsurface "
                                "Drain Layout.")
            self._fill_sizes()
            return
        try:
            check_records(self.records)
            self.rows, self.summary = resize(
                self.records, self._mode(), self.coeff.value(),
                {o: b.value() for o, b in self.order_boxes.items()},
                self.overrides, self._material(), self.spacing.value(), self.sections)
        except SizeError as e:
            self.status.setText(str(e))
            self._fill_sizes()
            return
        s = self.summary
        changed = sum(1 for r in self.rows if r["changed"])
        type_changed = sum(1 for r in self.rows if r["old"]["PIPE_TYPE"] is None
                           or str(r["old"]["PIPE_TYPE"]) != r["new"]["PIPE_TYPE"])
        coeff_changed = sum(1 for r in self.rows if r["old"]["D_COEFF"] is None
                            or abs(float(r["old"]["D_COEFF"]) - r["new"]["D_COEFF"]) > 1e-9)
        txt = ("{} lines: {} change size ({} larger, {} smaller), {} change coefficient, "
               "{} change pipe type").format(s["lines"], changed, s["larger"], s["smaller"],
                                             coeff_changed, type_changed)
        big = sum(1 for r in self.rows if r["new"]["NOMINAL"] == K.AVAILABLE_NOMINAL_SIZES[-1]
                  and r["new"]["ACTUAL_SIZE"] >= K.AVAILABLE_NOMINAL_SIZES[-1])
        if big:
            txt += "; {} need more than {} in".format(big, K.AVAILABLE_NOMINAL_SIZES[-1])
        self.status.setText(txt + ".")
        self.apply_btn.setEnabled(any(row_differs(r) for r in self.rows))
        self._fill_sizes()
        self._fill_pipes()
        self._fill_sections()

    def _fill_sizes(self):
        s = self.summary
        sizes = sorted(set(s["before"]) | set(s["after"])) if s else []
        self.sizes.setRowCount(len(sizes) + (1 if sizes else 0))
        for i, n in enumerate(sizes):
            vals = ["{} in".format(n), "{:,.0f}".format(s["before"].get(n, 0.0)),
                    "{:,.0f}".format(s["after"].get(n, 0.0))]
            for j, t in enumerate(vals):
                self.sizes.setItem(i, j, QTableWidgetItem(t))
        if sizes:
            tot = ["Total", "{:,.0f}".format(sum(s["before"].values())),
                   "{:,.0f}".format(sum(s["after"].values()))]
            for j, t in enumerate(tot):
                self.sizes.setItem(len(sizes), j, QTableWidgetItem(t))
        self.sizes.resizeColumnsToContents()

    def _fill_pipes(self):
        keys = list(self.overrides)
        self.pipes.setRowCount(len(keys))
        for i, k in enumerate(keys):
            rows = [r for r in self.rows if r["key"] == k]
            sizes = sorted({r["new"]["NOMINAL"] for r in rows})
            vals = [self.labels.get(k, str(k)), rows[0]["kind"] if rows else "",
                    "{:.3f}".format(self.overrides[k]),
                    ", ".join("{} in".format(n) for n in sizes)]
            for j, t in enumerate(vals):
                self.pipes.setItem(i, j, QTableWidgetItem(t))
        self.pipes.resizeColumnsToContents()
        self.clear_btn.setEnabled(bool(self.overrides or self.sections))
        self._show_band(self.set_band, keys)

    def _material(self):
        """The one pipe type for every line, or None for each line's own (PIPE_TYPE)."""
        i = self.material.currentIndex()
        return None if i <= 0 else i - 1

    def _fill_sections(self):
        """The picked pipe's lines, downstream first, each with a pipe-type choice."""
        if self.picked is None or not self.rows:
            self.section_rows = []
            self.sect.setRowCount(0)
            self.sect.hide()
            return
        self.section_rows = pipe_sections(self.records, self.picked)
        by_fid = {r["fid"]: r for r in self.rows}
        self.sect.setRowCount(len(self.section_rows))
        for i, (fid, s0, s1) in enumerate(self.section_rows):
            r = by_fid.get(fid)
            if r is None:
                continue
            old = r["old"]["NOMINAL"]
            size = ("{} in".format(r["new"]["NOMINAL"]) if old is None
                    or int(old) == r["new"]["NOMINAL"]
                    else "{} → {} in".format(int(old), r["new"]["NOMINAL"]))
            vals = ["{}{}".format(i + 1, " (downstream)" if i == 0 else ""),
                    "{:,.0f}–{:,.0f}".format(s0, s1), str(r["order"]),
                    "{:.3f}".format(r["new"]["D_COEFF"]), size]
            for j, t in enumerate(vals):
                self.sect.setItem(i, j, QTableWidgetItem(t))
            box = QComboBox()
            box.addItems(K.MATERIALS)
            box.setCurrentIndex(r["material"])
            if fid in self.sections:
                box.setToolTip("Set here")
            box.currentIndexChanged.connect(
                lambda idx, f=fid: self._section_type_changed(f, idx))
            self.sect.setCellWidget(i, 5, box)
        self.sect.resizeColumnsToContents()
        self.sect.show()

    def _section_type_changed(self, fid, idx, *args):
        self.sections[fid] = int(idx)
        self._recalc()

    def _section_clicked(self, row, col=0, *args):
        self.sect_band.reset(LINE_GEOMETRY)
        if self.layer is None or not 0 <= row < len(self.section_rows):
            return
        fid = self.section_rows[row][0]
        for f in self.layer.getFeatures(QgsFeatureRequest().setFilterFids([fid])):
            self.sect_band.addGeometry(f.geometry(), self.layer)

    def _show_band(self, band, keys):
        band.reset(LINE_GEOMETRY)
        if self.layer is None or not keys:
            return
        fids = [r["fid"] for r in self.records if pipe_key(r["attrs"], r["fid"]) in keys]
        for f in self.layer.getFeatures(QgsFeatureRequest().setFilterFids(fids)):
            band.addGeometry(f.geometry(), self.layer)

    # ------------------------------------------------------------ pipe by pipe
    def _toggle_pick(self, on=False, *args):
        if on:
            if self.layer is None:
                self.pick_btn.setChecked(False)
                return
            self.tool = QgsMapToolIdentifyFeature(self.canvas)
            self.tool.setLayer(self.layer)
            self.tool.featureIdentified.connect(self._picked)
            self.canvas.setMapTool(self.tool)
        elif self.tool is not None and self.canvas.mapTool() is self.tool:
            self.canvas.unsetMapTool(self.tool)

    def _picked(self, feature, *args):
        attrs = {n: _value(feature[n]) for n in feature.fields().names()}
        self.select_pipe(pipe_key(attrs, feature.id()))

    def select_pipe(self, key):
        self.picked = key
        self.sect_band.reset(LINE_GEOMETRY)
        rows = [r for r in self.rows if r["key"] == key]
        self._fill_sections()
        if not rows:
            self.pick_label.setText("Pick a pipe on the map to see its sections.")
            self.pipe_coeff.setEnabled(False)
            self.set_btn.setEnabled(False)
            self.band.reset(LINE_GEOMETRY)
            return
        orders = sorted({r["order"] for r in rows})
        cur = self.overrides.get(key)
        if cur is None:
            cur = rows[0]["new"]["D_COEFF"]
        sizes = sorted({r["new"]["NOMINAL"] for r in rows})
        self.pick_label.setText("{} ({}; {} line(s), order {}; {} in)".format(
            self.labels.get(key, key), rows[0]["kind"], len(rows),
            ", ".join(map(str, orders)), ", ".join(map(str, sizes))))
        self.pipe_coeff.blockSignals(True)
        self.pipe_coeff.setValue(cur)
        self.pipe_coeff.blockSignals(False)
        self.pipe_coeff.setEnabled(True)
        self.set_btn.setEnabled(True)
        self._show_band(self.band, [key])

    def _set_pipe(self, *args):
        if self.picked is None:
            return
        self.overrides[self.picked] = self.pipe_coeff.value()
        self._recalc()
        self.select_pipe(self.picked)

    def _clear_pipes(self, *args):
        self.overrides.clear()
        self.sections.clear()
        self._recalc()
        if self.picked is not None:
            self.select_pipe(self.picked)

    def _pipe_row_clicked(self, row, col=0, *args):
        keys = list(self.overrides)
        if 0 <= row < len(keys):
            self.select_pipe(keys[row])

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.pipes.hasFocus():
            r = self.pipes.currentRow()
            keys = list(self.overrides)
            if 0 <= r < len(keys):
                del self.overrides[keys[r]]
                self._recalc()
                return
        super().keyPressEvent(event)

    # ------------------------------------------------------------ apply
    def _apply(self, *args):
        layer = self.layer
        if layer is None or not self.rows:
            return
        rows = [r for r in self.rows if row_differs(r)]
        how = {"single": "one drainage coefficient, {:.3f} in/day".format(self.coeff.value()),
               "order": "a drainage coefficient per Strahler order",
               "pipe": "{} pipe(s) with their own drainage coefficient".format(
                   len(self.overrides))}[self._mode()]
        answer = QMessageBox.question(
            self, "Apply to layer",
            "Resize the pipes of “{}” with {}?\n\n{} of {} lines change: D_COEFF, "
            "PIPE_TYPE, CUM_SEG_FLOW, ACTUAL_SIZE, NOMINAL, D_AREA and DRAIN_SPACE are "
            "written, and "
            "flag notes pipes that need more than {} in. The layer is left in edit mode: "
            "save the edits to keep them, or roll them back.".format(
                layer.name(), how, len(rows), len(self.rows), K.AVAILABLE_NOMINAL_SIZES[-1]),
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return
        if not layer.isEditable() and not layer.startEditing():
            QMessageBox.warning(self, "Apply to layer",
                                "“{}” cannot be edited.".format(layer.name()))
            return
        by_fid = {r["fid"]: r["attrs"] for r in self.records}
        layer.beginEditCommand("Tile Size Calculator: resize with {}".format(how))
        try:
            if layer.fields().indexOf("PIPE_TYPE") < 0:      # layers from before 0.18
                layer.addAttribute(_string_field("PIPE_TYPE"))
            fields = layer.fields()
            for r in rows:
                ch = changes_for(r, by_fid[r["fid"]])
                ch["PIPE_TYPE"] = r["new"]["PIPE_TYPE"]
                for name, value in ch.items():
                    layer.changeAttributeValue(r["fid"], fields.indexOf(name), value)
        except Exception:
            layer.destroyEditCommand()
            raise
        layer.endEditCommand()
        layer.triggerRepaint()
        try:
            level = Qgis.Info
        except NameError:
            level = 0
        self.iface.messageBar().pushMessage(
            "Tile Size Calculator", "Resized {} lines of “{}”. Save the layer's edits to keep "
            "the change.".format(len(rows), layer.name()), level=level, duration=8)
        self.overrides.clear()
        self.sections.clear()
        self.reload()
        if self.picked is not None:
            self.select_pipe(self.picked)

    # ------------------------------------------------------------ teardown
    def cleanup(self):
        if self.tool is not None and self.canvas.mapTool() is self.tool:
            self.canvas.unsetMapTool(self.tool)
        for b in (self.band, self.set_band, self.sect_band):
            b.reset(LINE_GEOMETRY)
            self.canvas.scene().removeItem(b)

    def closeEvent(self, event):
        self.band.reset(LINE_GEOMETRY)
        self.set_band.reset(LINE_GEOMETRY)
        self.sect_band.reset(LINE_GEOMETRY)
        if self.pick_btn.isChecked():
            self.pick_btn.setChecked(False)
        super().closeEvent(event)
