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
    "subbasins": True,
    "split_order": "Auto",   # "Auto" or "1".."8"
    "max_subbasins": 20,
}
SPLIT_AUTO = "Auto"


def split_order_value(cfg):
    """Settings value -> int split order, or None for automatic."""
    v = str(cfg.get("split_order", SPLIT_AUTO))
    return int(v) if v.isdigit() else None


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
        else:
            v = str(v)
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

        self.subbasins = QCheckBox("Add sub-basins (NHDPlus catchments grouped "
                                   "between stream confluences)")
        self.subbasins.setChecked(cfg["subbasins"])
        self.subbasins.setToolTip(
            "Splits the watershed at confluences of the larger streams, using "
            "NHDPlus V2 catchments clipped to the StreamStats boundary. Each "
            "sub-basin records the sub-basin it drains to (ds_sb_id).")
        form.addRow(self.subbasins)

        self.split_order = QComboBox()
        self.split_order.addItems([SPLIT_AUTO] + [str(i) for i in range(1, 9)])
        self.split_order.setCurrentText(str(cfg["split_order"]))
        self.split_order.setToolTip(
            "Split where two streams of at least this Strahler order meet. Lower "
            "= more, smaller sub-basins. Auto picks the lowest order that gives "
            "no more than the number below.")
        form.addRow("Split at confluences of stream order ≥", self.split_order)

        self.max_sb = QSpinBox()
        self.max_sb.setRange(2, 500)
        self.max_sb.setValue(cfg["max_subbasins"])
        form.addRow("Auto: at most this many sub-basins", self.max_sb)

        def sync():
            on = self.subbasins.isChecked()
            self.split_order.setEnabled(on)
            self.max_sb.setEnabled(on and self.split_order.currentText() == SPLIT_AUTO)
        self.subbasins.toggled.connect(lambda *_: sync())
        self.split_order.currentTextChanged.connect(lambda *_: sync())
        sync()

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
              "streams": self.streams.isChecked(),
              "subbasins": self.subbasins.isChecked(),
              "split_order": self.split_order.currentText(),
              "max_subbasins": int(self.max_sb.value())})
        self.accept()
