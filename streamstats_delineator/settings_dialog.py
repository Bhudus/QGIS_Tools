# -*- coding: utf-8 -*-
from qgis.core import QgsSettings
from qgis.PyQt.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                                 QDoubleSpinBox, QFormLayout, QLabel, QSpinBox)

from .client import REGIONS

PREFIX = "streamstats_delineator/"
AUTO = "Auto-detect"

DEFAULTS = {
    "region": AUTO,
    "snap": True,
    "warn_snap_m": 500.0,
    "zoom": True,
    "timeout_s": 120,
    "streams": True,
}


def load():
    s = QgsSettings()
    out = {}
    for k, default in DEFAULTS.items():
        v = s.value(PREFIX + k, default)
        if isinstance(default, bool):
            v = v in (True, "true", "True", 1, "1")
        elif isinstance(default, float):
            v = float(v)
        elif isinstance(default, int):
            v = int(v)
        out[k] = v
    return out


def save(values):
    s = QgsSettings()
    for k, v in values.items():
        s.setValue(PREFIX + k, v)


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("StreamStats Delineator settings")
        cfg = load()
        form = QFormLayout(self)

        self.region = QComboBox()
        self.region.addItems([AUTO] + REGIONS)
        self.region.setCurrentText(cfg["region"])
        self.region.setToolTip("StreamStats study area. Auto-detect looks up the "
                               "state from the clicked point. Choose CRB/DRB/RRB "
                               "manually for the regional studies.")
        form.addRow("Region", self.region)

        self.snap = QCheckBox("Snap clicked point to nearest StreamStats stream cell")
        self.snap.setChecked(cfg["snap"])
        form.addRow(self.snap)

        self.warn = QDoubleSpinBox()
        self.warn.setRange(0, 100000)
        self.warn.setDecimals(0)
        self.warn.setSuffix(" m")
        self.warn.setValue(cfg["warn_snap_m"])
        self.warn.setToolTip("Warn when the snapped outlet is farther than this "
                             "from where you clicked (it may be on a different stream).")
        form.addRow("Warn if snap moves point more than", self.warn)

        self.streams = QCheckBox("Add streams inside the watershed "
                                 "(NHDPlus via USGS NLDI)")
        self.streams.setChecked(cfg["streams"])
        self.streams.setToolTip("Fetches NHDPlus V2 (1:100k) flowlines upstream of "
                                "the outlet and clips them to the watershed. Small "
                                "headwater channels may not be in NHDPlus.")
        form.addRow(self.streams)

        self.zoom = QCheckBox("Zoom to new watershed")
        self.zoom.setChecked(cfg["zoom"])
        form.addRow(self.zoom)

        self.timeout = QSpinBox()
        self.timeout.setRange(15, 900)
        self.timeout.setSuffix(" s")
        self.timeout.setValue(cfg["timeout_s"])
        form.addRow("Request timeout", self.timeout)

        note = QLabel("<small>Results come from USGS StreamStats "
                      "(ss-delineate). Always check delineations against "
                      "hydrography before using them.</small>")
        note.setWordWrap(True)
        form.addRow(note)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _accept(self):
        save({"region": self.region.currentText(), "snap": self.snap.isChecked(),
              "warn_snap_m": float(self.warn.value()), "zoom": self.zoom.isChecked(),
              "timeout_s": int(self.timeout.value()),
              "streams": self.streams.isChecked()})
        self.accept()
