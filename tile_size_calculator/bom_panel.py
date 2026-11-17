"""The Bill of materials tab of the Tile Size Calculator panel."""
import json
import os

from qgis.core import QgsProject, QgsSettings
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .bom_core import (
    DEFAULT_STRUCTURES,
    PRICE_FIELDS,
    bill_csv,
    default_prices,
    estimate,
    missing_rows,
    prices_from_csv,
    prices_to_csv,
    quantities,
    size_text,
)

SETTINGS = "tile_size_calculator/bom/"
PRICE_HEADERS = ("Category", "Item", "Size", "Unit", "Price", "Practice")
BILL_HEADERS = ("Category", "Item", "Size", "Quantity", "Unit", "Unit price", "Amount",
                "Practice")


def _spin(lo, hi, value, suffix="", decimals=1, step=1.0):
    b = QDoubleSpinBox()
    b.setDecimals(decimals)
    b.setRange(lo, hi)
    b.setSingleStep(step)
    b.setSuffix(suffix)
    b.setValue(value)
    return b


class BomPanel(QWidget):
    def __init__(self, dock):
        super().__init__()
        self.dock = dock
        self.prices = []
        self.est = None
        self.q = None
        self._filling = False
        s = QgsSettings()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 4, 0, 0)

        grid = QGridLayout()
        self.project = QLineEdit(s.value(SETTINGS + "project", ""))
        self.client = QLineEdit(s.value(SETTINGS + "client", ""))
        self.prepared = QLineEdit(s.value(SETTINGS + "prepared_by", ""))
        self.notes = QLineEdit(s.value(SETTINGS + "notes", ""))
        for i, (lab, w) in enumerate((("Project / field", self.project), ("Client", self.client),
                                      ("Prepared by", self.prepared), ("Notes", self.notes))):
            grid.addWidget(QLabel(lab), i // 2, (i % 2) * 2)
            grid.addWidget(w, i // 2, (i % 2) * 2 + 1)
        v.addLayout(grid)

        row = QHBoxLayout()
        self.units = QComboBox()
        self.units.addItems(["ft", "m"])
        self.units.setCurrentIndex(int(s.value(SETTINGS + "units", 0)))
        self.units.setToolTip("Units of the network layer's length field")
        self.waste = _spin(0, 50, float(s.value(SETTINGS + "waste", 0.0)), " %")
        self.waste.setToolTip("Extra pipe allowed for waste and overlaps")
        self.outlet_len = _spin(0, 500, float(s.value(SETTINGS + "outlet_length", 20.0)), "",
                                0, 1)
        self.outlet_len.setToolTip("Length of rigid outlet pipe (0 for none)")
        self.guard = QCheckBox("Rodent guard")
        self.guard.setChecked(str(s.value(SETTINGS + "guard", "true")).lower() == "true")
        self.install = QCheckBox("Installation")
        self.install.setChecked(str(s.value(SETTINGS + "install", "true")).lower() == "true")
        self.install.setToolTip("Add installation per length of tile, priced by size")
        self.markup = _spin(0, 500, float(s.value(SETTINGS + "markup", 0.0)), " %")
        self.tax = _spin(0, 50, float(s.value(SETTINGS + "tax", 0.0)), " %", 2, 0.25)
        for lab, w in (("Lengths in", self.units), ("Waste", self.waste),
                       ("Outlet pipe", self.outlet_len)):
            row.addWidget(QLabel(lab))
            row.addWidget(w)
        row.addWidget(self.guard)
        row.addWidget(self.install)
        row.addWidget(QLabel("Markup"))
        row.addWidget(self.markup)
        row.addWidget(QLabel("Tax"))
        row.addWidget(self.tax)
        row.addStretch(1)
        v.addLayout(row)

        row = QHBoxLayout()
        self.struct = {}
        for name in DEFAULT_STRUCTURES + ("Mobilization",):
            b = QSpinBox()
            b.setRange(0, 999)
            b.setValue(int(s.value(SETTINGS + "qty_" + name, 0)))
            b.setToolTip("Number of {} (priced from the price table)".format(name.lower()))
            row.addWidget(QLabel(name))
            row.addWidget(b)
            self.struct[name] = b
        row.addStretch(1)
        v.addLayout(row)

        split = QSplitter(Qt.Horizontal)
        self.bill = QTableWidget(0, len(BILL_HEADERS))
        self.bill.setHorizontalHeaderLabels(list(BILL_HEADERS))
        self.bill.verticalHeader().setVisible(False)
        self.bill.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.bill.setToolTip("Quantities from the network (with the sizes as shown on the "
                             "Pipe sizes tab), priced from the price table")
        pw = QWidget()
        pv = QVBoxLayout(pw)
        pv.setContentsMargins(0, 0, 0, 0)
        self.price_table = QTableWidget(0, len(PRICE_HEADERS))
        self.price_table.setHorizontalHeaderLabels(list(PRICE_HEADERS))
        self.price_table.verticalHeader().setVisible(False)
        self.price_table.setToolTip(
            "Unit prices: edit any cell. A row prices every item of the same category and "
            "item with that size; a fitting without its own size row takes the row of its "
            "run size, and size * covers all sizes. Practice is the NRCS practice code.")
        pb = QHBoxLayout()
        self.import_btn = QPushButton("Import prices…")
        self.export_btn = QPushButton("Export prices…")
        self.add_row_btn = QPushButton("Add row")
        pb.addWidget(self.import_btn)
        pb.addWidget(self.export_btn)
        pb.addWidget(self.add_row_btn)
        pb.addStretch(1)
        pv.addWidget(self.price_table, 1)
        pv.addLayout(pb)
        split.addWidget(self.bill)
        split.addWidget(pw)
        v.addWidget(split, 1)

        row = QHBoxLayout()
        self.totals = QLabel("")
        self.totals.setWordWrap(True)
        self.layers_btn = QPushButton("Add to map")
        self.layers_btn.setToolTip("Add a Bill of materials table and a Fittings point layer "
                                   "to the project")
        self.csv_btn = QPushButton("Save bill…")
        self.template = QLineEdit(s.value(SETTINGS + "template", ""))
        self.template.setPlaceholderText("Template (.qpt), optional")
        self.template.setToolTip(
            "Your own print layout template. Its attribute table with the frame ID "
            "bom_table shows the bill, and labels can show [% @tsc_project %], "
            "@tsc_client, @tsc_prepared_by, @tsc_date, @tsc_notes, @tsc_subtotal, "
            "@tsc_markup, @tsc_tax and @tsc_total. Leave blank for the built-in quote.")
        self.template_btn = QPushButton("…")
        self.print_btn = QPushButton("Print layout…")
        self.print_btn.setToolTip("Make a print layout with the plan and the bill of materials "
                                  "and open it in the layout designer")
        row.addWidget(self.totals, 1)
        row.addWidget(self.layers_btn)
        row.addWidget(self.csv_btn)
        row.addWidget(self.template)
        row.addWidget(self.template_btn)
        row.addWidget(self.print_btn)
        v.addLayout(row)

        self._load_prices()
        for w in (self.project, self.client, self.prepared, self.notes, self.template):
            w.editingFinished.connect(self._save)
        for w in (self.waste, self.outlet_len, self.markup, self.tax):
            w.valueChanged.connect(self._changed)
        for w in self.struct.values():
            w.valueChanged.connect(self._changed)
        self.units.currentIndexChanged.connect(self._changed)
        self.guard.toggled.connect(self._changed)
        self.install.toggled.connect(self._changed)
        self.price_table.itemChanged.connect(self._price_edited)
        self.import_btn.clicked.connect(self._import)
        self.export_btn.clicked.connect(self._export)
        self.add_row_btn.clicked.connect(self._add_row)
        self.layers_btn.clicked.connect(self.add_layers)
        self.csv_btn.clicked.connect(self._save_bill)
        self.template_btn.clicked.connect(self._choose_template)
        self.print_btn.clicked.connect(self.print_layout)

    # ------------------------------------------------------------ settings
    def _save(self, *args):
        s = QgsSettings()
        for k, w in (("project", self.project), ("client", self.client),
                     ("prepared_by", self.prepared), ("notes", self.notes),
                     ("template", self.template)):
            s.setValue(SETTINGS + k, w.text())
        s.setValue(SETTINGS + "units", self.units.currentIndex())
        s.setValue(SETTINGS + "waste", self.waste.value())
        s.setValue(SETTINGS + "outlet_length", self.outlet_len.value())
        s.setValue(SETTINGS + "guard", "true" if self.guard.isChecked() else "false")
        s.setValue(SETTINGS + "install", "true" if self.install.isChecked() else "false")
        s.setValue(SETTINGS + "markup", self.markup.value())
        s.setValue(SETTINGS + "tax", self.tax.value())
        for name, b in self.struct.items():
            s.setValue(SETTINGS + "qty_" + name, b.value())

    def _load_prices(self):
        raw = QgsSettings().value(SETTINGS + "prices", "")
        try:
            self.prices = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            self.prices = []
        if not self.prices:
            self.prices = default_prices()

    def _save_prices(self):
        QgsSettings().setValue(SETTINGS + "prices", json.dumps(self.prices))

    def _changed(self, *args):
        self._save()
        self.refresh()

    # ------------------------------------------------------------ bill
    def info(self):
        return {"project": self.project.text(), "client": self.client.text(),
                "prepared_by": self.prepared.text(), "notes": self.notes.text()}

    def refresh(self):
        """Quantities from the network as shown on the Pipe sizes tab, then prices."""
        lines, sizes = self.dock.bom_input()
        if not lines:
            self.q, self.est = None, None
            self.bill.setRowCount(0)
            self.totals.setText("No network.")
            return
        unit = self.units.currentText()
        self.q = quantities(lines, sizes, self.waste.value(), self.outlet_len.value(),
                            self.guard.isChecked(), {k: b.value() for k, b in
                                                     self.struct.items() if k != "Mobilization"},
                            self.install.isChecked(), unit)
        added = missing_rows(self.prices, self.q["items"])
        if added:
            self.prices += added
            self._save_prices()
        self.est = estimate(self.q["items"], self.prices, self.markup.value(), self.tax.value(),
                            {"Mobilization": self.struct["Mobilization"].value()})
        self._fill_prices()
        self._fill_bill()

    def _fill_bill(self):
        from .bom_layers import money, qty_text
        lines = self.est["lines"]
        self.bill.setRowCount(len(lines))
        for i, l in enumerate(lines):
            vals = [l["category"], l["item"], size_text(l["size"]), qty_text(l["qty"], l["unit"]),
                    l["unit"], money(l["price"]) if l["priced"] else "no price",
                    money(l["amount"]), l["practice"] or ""]
            for j, t in enumerate(vals):
                self.bill.setItem(i, j, QTableWidgetItem(t))
        self.bill.resizeColumnsToContents()
        e = self.est
        txt = "Subtotal {}".format(money(e["subtotal"]))
        if e["markup"]:
            txt += " · markup {}".format(money(e["markup"]))
        if e["tax"]:
            txt += " · tax {}".format(money(e["tax"]))
        txt += " · <b>total {}</b>".format(money(e["total"]))
        if e["unpriced"]:
            txt += " · <span style='color:#b91c1c'>{} item(s) without a price</span>".format(
                e["unpriced"])
        practice = ", ".join("{} {}".format(k, money(v)) for k, v in e["by_practice"].items()
                             if k)
        if practice:
            txt += "<br>By practice: " + practice
        self.totals.setText(txt)

    # ------------------------------------------------------------ prices
    def _fill_prices(self):
        self._filling = True
        self.price_table.setRowCount(len(self.prices))
        for i, p in enumerate(self.prices):
            for j, k in enumerate(PRICE_FIELDS):
                v = p.get(k)
                if k == "price":
                    t = "" if v in (None, "") else "{:.2f}".format(float(v))
                elif k == "size":
                    t = size_text(v) if v not in ("*", "", None) else "*"
                else:
                    t = "" if v is None else str(v)
                self.price_table.setItem(i, j, QTableWidgetItem(t))
        self.price_table.resizeColumnsToContents()
        self._filling = False

    def _price_edited(self, item, *args):
        if self._filling:
            return
        i, j = item.row(), item.column()
        if not 0 <= i < len(self.prices):
            return
        key = PRICE_FIELDS[j]
        t = item.text().strip()
        if key == "price":
            try:
                val = float(t.replace("$", "").replace(",", "")) if t else None
            except ValueError:
                val = None
        elif key == "size":
            val = (t.replace(" ", "").replace("×", "x").replace("in", "").replace('"', "")
                   .lower() or "*")
        else:
            val = t
        self.prices[i][key] = val
        self._save_prices()
        if self.q is not None:
            self.est = estimate(self.q["items"], self.prices, self.markup.value(),
                                self.tax.value(),
                                {"Mobilization": self.struct["Mobilization"].value()})
            self._fill_bill()

    def _add_row(self, *args):
        self.prices.append({"category": "Other", "item": "", "size": "*", "unit": "each",
                            "price": None, "practice": ""})
        self._save_prices()
        self._fill_prices()

    def _import(self, *args):
        path, _ = QFileDialog.getOpenFileName(self, "Import prices", "", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig") as fh:
                rows = prices_from_csv(fh.read())
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import prices", str(e))
            return
        self.prices = rows
        self._save_prices()
        self.refresh()

    def _export(self, *args):
        path, _ = QFileDialog.getSaveFileName(self, "Export prices", "tile_prices.csv",
                                              "CSV (*.csv)")
        if path:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(prices_to_csv(self.prices))

    # ------------------------------------------------------------ outputs
    def _save_bill(self, *args):
        if self.est is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save bill of materials",
                                              "bill_of_materials.csv", "CSV (*.csv)")
        if path:
            head = {"Project": self.project.text(), "Client": self.client.text(),
                    "Prepared by": self.prepared.text(), "Notes": self.notes.text()}
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(bill_csv(self.est, head))

    def _choose_template(self, *args):
        path, _ = QFileDialog.getOpenFileName(self, "Print layout template", "",
                                              "Layout templates (*.qpt)")
        if path:
            self.template.setText(path)
            self._save()

    def add_layers(self, *args):
        """Bill of materials table and Fittings points in the project; returns them."""
        from .bom_layers import bill_layer, fittings_layer, set_variables
        if self.est is None or self.dock.layer is None:
            return None, None
        project = QgsProject.instance()
        set_variables(project, self.info(), self.est)
        bom = bill_layer(project, self.est)
        fit = fittings_layer(project, self.q["fittings"], self.dock.layer.crs())
        return bom, fit

    def print_layout(self, *args):
        from .bom_layers import default_layout, template_layout
        if self.est is None or self.dock.layer is None:
            return
        bom, fit = self.add_layers()
        project = QgsProject.instance()
        title = "Tile drainage quote" + (" – " + self.project.text()
                                         if self.project.text() else "")
        tpl = self.template.text().strip()
        try:
            if tpl:
                if not os.path.exists(tpl):
                    raise ValueError("The template {} does not exist.".format(tpl))
                layout = template_layout(project, tpl, title, self.dock.layer, bom,
                                         self.dock.iface)
            else:
                layout = default_layout(project, title, self.dock.layer, fit, bom,
                                        self.dock.layer.extent())
        except Exception as e:  # noqa: BLE001  show the reason, keep QGIS running
            QMessageBox.warning(self, "Print layout", "Could not make the layout: {}".format(e))
            return
        self.dock.iface.openLayoutDesigner(layout)
