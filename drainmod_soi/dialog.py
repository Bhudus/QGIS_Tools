import os
import re

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFontDatabase
from qgis.PyQt.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                                 QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                                 QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                                 QMessageBox, QPlainTextEdit, QPushButton,
                                 QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .compat import qenum
from .core import sda
from .core.pipeline import METHODS, Options, build

SETTINGS_KEY = "drainmod_soi/"


def _spin(cls, lo, hi, val, decimals=None, suffix=None):
    w = cls()
    if decimals is not None:
        w.setDecimals(decimals)
    w.setRange(lo, hi)
    w.setValue(val)
    if suffix:
        w.setSuffix(suffix)
    return w


class SoiDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DRAINMOD soil file from SSURGO")
        self.resize(720, 760)
        self.mapunit = None
        self.comps = []
        self.hz_cache = {}
        self.result = None
        self._building = False
        self._ui()
        self._load_settings()

    # ---------------------------------------------------------------- UI
    def _ui(self):
        lay = QVBoxLayout(self)

        loc = QGroupBox("Location (click the map, or type coordinates)")
        lh = QHBoxLayout(loc)
        self.lat = _spin(QDoubleSpinBox, -90, 90, 0, decimals=6)
        self.lon = _spin(QDoubleSpinBox, -180, 180, 0, decimals=6)
        lh.addWidget(QLabel("Lat"))
        lh.addWidget(self.lat)
        lh.addWidget(QLabel("Lon"))
        lh.addWidget(self.lon)
        btn = QPushButton("Get SSURGO data")
        btn.clicked.connect(self.fetch)
        lh.addWidget(btn)
        lay.addWidget(loc)

        self.mu_label = QLabel("No location selected.")
        self.mu_label.setWordWrap(True)
        lay.addWidget(self.mu_label)

        form = QFormLayout()
        self.comp = QComboBox()
        form.addRow("Component", self.comp)

        self.name = QComboBox()
        self.name.setEditable(True)
        form.addRow("Soil name (line 1)", self.name)

        self.max_root = _spin(QSpinBox, 1, 500, 45, suffix=" cm")
        self.avg_root = _spin(QSpinBox, 1, 500, 30, suffix=" cm")
        roots = QHBoxLayout()
        roots.addWidget(QLabel("Maximum"))
        roots.addWidget(self.max_root)
        roots.addWidget(QLabel("Average"))
        roots.addWidget(self.avg_root)
        roots.addStretch()
        form.addRow("Root depth", roots)

        self.method = QComboBox()
        for k, v in METHODS.items():
            self.method.addItem(v, k)
        form.addRow("Water retention", self.method)

        self.ksat_src = QComboBox()
        self.ksat_src.addItem("SSURGO Ksat (ksat_r)", "ssurgo")
        self.ksat_src.addItem("Texture-class value from the method above", "texture")
        form.addRow("Hydraulic conductivity", self.ksat_src)

        merge = QHBoxLayout()
        self.max_layers = _spin(QSpinBox, 1, 5, 5)
        self.merge_similar = QCheckBox("also merge near-identical adjacent horizons")
        self.merge_similar.setChecked(True)
        merge.addWidget(self.max_layers)
        merge.addWidget(self.merge_similar)
        merge.addStretch()
        form.addRow("Maximum layers", merge)

        self.ksat_mean = QComboBox()
        self.ksat_mean.addItem("Thickness-weighted arithmetic (lateral flow)", "arithmetic")
        self.ksat_mean.addItem("Thickness-weighted harmonic (vertical flow)", "harmonic")
        form.addRow("Ksat of merged layers", self.ksat_mean)

        ext = QHBoxLayout()
        self.extend = QCheckBox("Extend deepest layer to")
        self.extend_to = _spin(QSpinBox, 10, 5000, 500, suffix=" cm")
        ext.addWidget(self.extend)
        ext.addWidget(self.extend_to)
        ext.addStretch()
        form.addRow("Profile bottom", ext)
        lay.addLayout(form)

        self.tabs = QTabWidget()
        mono = QFontDatabase.systemFont(qenum(QFontDatabase, "SystemFont", "FixedFont"))
        self.preview = QPlainTextEdit()
        self.details = QPlainTextEdit()
        for w in (self.preview, self.details):
            w.setReadOnly(True)
            w.setFont(mono)
            w.setLineWrapMode(qenum(QPlainTextEdit, "LineWrapMode", "NoWrap"))
        self.tabs.addTab(self.preview, ".soi file")
        self.tabs.addTab(self.details, "Layers && SSURGO data")
        lay.addWidget(self.tabs, 1)

        bb = QDialogButtonBox()
        self.save_btn = bb.addButton("Save .soi…", qenum(QDialogButtonBox, "ButtonRole", "AcceptRole"))
        close = bb.addButton(qenum(QDialogButtonBox, "StandardButton", "Close"))
        self.save_btn.setEnabled(False)
        bb.accepted.connect(self.save)
        close.clicked.connect(self.close)
        lay.addWidget(bb)

        self.comp.currentIndexChanged.connect(self._component_changed)
        for w in (self.max_root, self.avg_root, self.max_layers, self.extend_to):
            w.valueChanged.connect(self.rebuild)
        for w in (self.method, self.ksat_src, self.ksat_mean):
            w.currentIndexChanged.connect(self.rebuild)
        for w in (self.merge_similar, self.extend):
            w.toggled.connect(self.rebuild)
        self.name.currentTextChanged.connect(self._name_edited)

    # ---------------------------------------------------------- settings
    def _load_settings(self):
        s = QgsSettings()
        self.max_root.setValue(int(s.value(SETTINGS_KEY + "max_root", 45)))
        self.avg_root.setValue(int(s.value(SETTINGS_KEY + "avg_root", 30)))
        i = self.method.findData(s.value(SETTINGS_KEY + "method", "rosetta"))
        self.method.setCurrentIndex(max(i, 0))

    def _save_settings(self):
        s = QgsSettings()
        s.setValue(SETTINGS_KEY + "max_root", self.max_root.value())
        s.setValue(SETTINGS_KEY + "avg_root", self.avg_root.value())
        s.setValue(SETTINGS_KEY + "method", self.method.currentData())

    # ------------------------------------------------------------- data
    def set_location(self, lon, lat):
        self.lon.setValue(lon)
        self.lat.setValue(lat)
        self.fetch()

    def _busy(self, on):
        if on:
            QApplication.setOverrideCursor(qenum(Qt, "CursorShape", "WaitCursor"))
            QApplication.processEvents()
        else:
            QApplication.restoreOverrideCursor()

    def fetch(self):
        lon, lat = self.lon.value(), self.lat.value()
        self.mu_label.setText("Querying Soil Data Access at %.5f, %.5f …" % (lat, lon))
        self._busy(True)
        try:
            mu = sda.mapunit_at(lon, lat)
            comps = sda.components(mu["mukey"]) if mu else []
        except Exception as e:  # noqa: BLE001
            self._busy(False)
            self.mu_label.setText("Soil Data Access request failed.")
            QMessageBox.warning(self, "SSURGO", "Soil Data Access request failed:\n%s" % e)
            return
        self._busy(False)

        self.mapunit, self.comps, self.hz_cache = mu, comps, {}
        self.comp.blockSignals(True)
        self.comp.clear()
        if not mu:
            self.mu_label.setText("No SSURGO map unit at this location "
                                  "(outside survey coverage?).")
            self.comp.blockSignals(False)
            self._show_error("")
            return
        self.mu_label.setText("<b>%s</b><br>mukey %s, symbol %s, survey area %s"
                              % (mu["muname"], mu["mukey"], mu["musym"], mu["areasymbol"]))
        default = sda.default_component(comps)
        for c in comps:
            self.comp.addItem("%s  (%g%%%s)" % (c["compname"], c["comppct_r"],
                              ", major" if c.get("majcompflag") == "Yes" else ""),
                              c["cokey"])
        if default:
            self.comp.setCurrentIndex(self.comp.findData(default["cokey"]))
        self.comp.blockSignals(False)
        self._component_changed()

    def _component_changed(self, *args):
        cokey = self.comp.currentData()
        if not cokey:
            return
        if cokey not in self.hz_cache:
            self._busy(True)
            try:
                self.hz_cache[cokey] = sda.horizons(cokey)
            except Exception as e:  # noqa: BLE001
                self._busy(False)
                QMessageBox.warning(self, "SSURGO", "Horizon query failed:\n%s" % e)
                return
            self._busy(False)
        self._name_dirty = True
        self.rebuild()

    def _options(self):
        return Options(
            max_root=self.max_root.value(), avg_root=self.avg_root.value(),
            method=self.method.currentData(), ksat_source=self.ksat_src.currentData(),
            max_layers=self.max_layers.value(), merge_similar=self.merge_similar.isChecked(),
            ksat_mean=self.ksat_mean.currentData(),
            extend_to=self.extend_to.value() if self.extend.isChecked() else None)

    def rebuild(self, *args):
        cokey = self.comp.currentData()
        if not cokey or cokey not in self.hz_cache or self._building:
            return
        comp = next(c for c in self.comps if c["cokey"] == cokey)
        hz = self.hz_cache[cokey]
        self._building = True
        if self.method.currentData() in ("rosetta3", "rosetta3w"):
            self._busy(True)
        try:
            res = build(comp["compname"], hz, self._options())
        except Exception as e:  # noqa: BLE001
            self.result = None
            self._show_error(str(e), hz)
            return
        finally:
            if self.method.currentData() in ("rosetta3", "rosetta3w"):
                self._busy(False)
            self._building = False

        # soil-name box: offer SSURGO name variants, keep user edits otherwise
        if getattr(self, "_name_dirty", True):
            self.name.clear()
            self.name.addItems([res.name, comp["compname"], self.mapunit["muname"]])
            self._name_dirty = False

        self.result = res
        self.preview.setPlainText(self._final_text())
        self.details.setPlainText(self._details_text(comp, hz, res))
        self.save_btn.setEnabled(True)
        if res.warnings:
            self.tabs.setTabText(1, "Layers && SSURGO data (%d warning%s)"
                                 % (len(res.warnings), "s" if len(res.warnings) > 1 else ""))
        else:
            self.tabs.setTabText(1, "Layers && SSURGO data")

    def _name_edited(self, *args):
        if self.result:
            self.preview.setPlainText(self._final_text())

    def _final_text(self):
        lines = self.result.text.split("\n")
        lines[0] = self.name.currentText().strip()[:80] or self.result.name
        return "\n".join(lines)

    def _show_error(self, msg, hz=None):
        self.result = None
        self.save_btn.setEnabled(False)
        self.preview.setPlainText(("Cannot build file: " + msg) if msg else "")
        if hz is not None:
            self.details.setPlainText(self._hz_table(hz))

    @staticmethod
    def _hz_table(hz):
        out = ["SSURGO horizons (representative values):",
               "%-6s %5s %5s %6s %6s %6s %6s %8s  %s"
               % ("Hz", "Top", "Bot", "Sand", "Silt", "Clay", "Db", "Ksat", "Texture"),
               "%-6s %5s %5s %6s %6s %6s %6s %8s"
               % ("", "cm", "cm", "%", "%", "%", "g/cc", "cm/h")]

        def f(v, fmt):
            return fmt % v if v is not None else "-"
        for h in hz:
            out.append("%-6s %5s %5s %6s %6s %6s %6s %8s  %s" % (
                h["hzname"], f(h["top"], "%.0f"), f(h["bottom"], "%.0f"),
                f(h["sand"], "%.1f"), f(h["silt"], "%.1f"), f(h["clay"], "%.1f"),
                f(h["db"], "%.2f"), f(h["ksat_cmhr"], "%.3f"), h["texdesc"]))
        return "\n".join(out)

    def _details_text(self, comp, hz, res):
        parts = ["Component: %s (cokey %s, %g%%)  %s  hydrologic group %s"
                 % (comp["compname"], comp["cokey"], comp["comppct_r"],
                    comp.get("drainagecl") or "", comp.get("hydgrp") or "-"),
                 "", res.summary(), ""]
        if res.warnings:
            parts += ["Warnings:"] + ["  - " + w for w in res.warnings] + [""]
        parts.append(self._hz_table(hz))
        return "\n".join(parts)

    # ------------------------------------------------------------- save
    def save(self):
        if not self.result:
            return
        s = QgsSettings()
        folder = s.value(SETTINGS_KEY + "folder", os.path.expanduser("~"))
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", self.comp.currentText().split("(")[0].strip()) or "soil"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save DRAINMOD soil file", os.path.join(folder, base + ".soi"),
            "DRAINMOD soil files (*.soi)")
        if not path:
            return
        if not path.lower().endswith(".soi"):
            path += ".soi"
        # DRAINMOD is a Windows program: write CRLF line endings
        with open(path, "w", newline="\r\n") as f:
            f.write(self._final_text())
        s.setValue(SETTINGS_KEY + "folder", os.path.dirname(path))
        self._save_settings()
        QMessageBox.information(self, "DRAINMOD", "Saved %s" % path)
