"""Tile Profile panel: shows the profile of the pipe selected in a network layer."""
import csv

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsPointXY,
    QgsProject,
    QgsSettings,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsMapLayerComboBox, QgsMapToolIdentifyFeature, QgsRubberBand
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor, QImage, QPainter
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .chart import ProfileChart
from .chart_model import ChartModel
from .profile_core import ProfileError, build_profile, table_rows

try:
    from qgis.core import Qgis
    LINE_FILTER = Qgis.LayerFilter.LineLayer
    RASTER_FILTER = Qgis.LayerFilter.RasterLayer
except (ImportError, AttributeError):
    from qgis.core import QgsMapLayerProxyModel
    LINE_FILTER = QgsMapLayerProxyModel.LineLayer
    RASTER_FILTER = QgsMapLayerProxyModel.RasterLayer

try:
    LINE_GEOMETRY = Qgis.GeometryType.Line
except (NameError, AttributeError):
    LINE_GEOMETRY = QgsWkbTypes.LineGeometry

UNITS = [("ft", 0.3048), ("m", 1.0)]
DEM_UNITS = [("US survey feet", 1200.0 / 3937.0), ("international feet", 0.3048),
             ("metres", 1.0)]
DEFAULTS = {"ft": dict(upper=3.25, lower=4.25, abs_upper=2.5, abs_lower=6.0),
            "m": dict(upper=1.0, lower=1.3, abs_upper=0.75, abs_lower=1.8)}
SETTINGS = "tile_profile/"


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


def _metres_per_map_unit(crs):
    try:
        return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), Qgis.DistanceUnit.Meters)
    except (NameError, AttributeError):
        return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), QgsUnitTypes.DistanceMeters)


def _record(feature):
    """Feature -> {"attrs", "xy", "z", "m"} for profile_core."""
    names = feature.fields().names()
    attrs = {n: _value(feature[n]) for n in names}
    geom = feature.geometry()
    xy, z, m = [], [], []
    if geom is not None and not geom.isEmpty():
        has_z = QgsWkbTypes.hasZ(geom.wkbType())
        has_m = QgsWkbTypes.hasM(geom.wkbType())
        for v in geom.vertices():
            xy.append((v.x(), v.y()))
            z.append(v.z() if has_z else None)
            m.append(v.m() if has_m else None)
        if not has_z:
            z = None
        if not has_m:
            m = None
    return {"attrs": attrs, "xy": xy, "z": z, "m": m, "geometry": geom}


class TileProfileDock(QDockWidget):
    def __init__(self, iface, parent=None):
        super().__init__("Tile Profile", parent)
        self.setObjectName("TileProfileDock")
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.layer = None
        self.fid = None
        self.profile = None
        self.tool = None
        self.band = QgsRubberBand(self.canvas, LINE_GEOMETRY)
        self.band.setColor(QColor(255, 196, 0, 170))
        self.band.setWidth(6)
        self._ready = False                 # no saving settings until they are loaded
        self._build()
        self._restore()
        self._ready = True
        self._pick_network_layer()
        self.layer_box.layerChanged.connect(self._layer_changed)
        self._layer_changed(self.layer_box.currentLayer())
        self.iface.currentLayerChanged.connect(self._current_layer_changed)
        self.canvas.mapToolSet.connect(self._map_tool_set)

    # ------------------------------------------------------------ widgets
    def _build(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 4, 6, 4)

        row1 = QHBoxLayout()
        self.layer_box = QgsMapLayerComboBox()
        self.layer_box.setFilters(LINE_FILTER)
        self.dem_box = QgsMapLayerComboBox()
        self.dem_box.setFilters(RASTER_FILTER)
        try:
            self.dem_box.setAllowEmptyLayer(True, "None: use the layer's ground values")
        except TypeError:
            self.dem_box.setAllowEmptyLayer(True)
        self.dem_box.setLayer(None)
        self.dem_units = QComboBox()
        for name, _ in DEM_UNITS:
            self.dem_units.addItem(name)
        self.units = QComboBox()
        for name, _ in UNITS:
            self.units.addItem(name)
        for label, widget in (("Network", self.layer_box), ("DEM", self.dem_box),
                              ("DEM elevations in", self.dem_units), ("Units", self.units)):
            row1.addWidget(QLabel(label))
            row1.addWidget(widget, 2 if isinstance(widget, QgsMapLayerComboBox) else 0)
        v.addLayout(row1)

        row2 = QHBoxLayout()
        self.spins = {}
        for key, label in (("upper", "Upper tile depth"), ("lower", "Lower tile depth"),
                           ("abs_upper", "Absolute upper"), ("abs_lower", "Absolute lower")):
            sp = QDoubleSpinBox()
            sp.setDecimals(2)
            sp.setRange(0.0, 100.0)
            sp.setSingleStep(0.25)
            self.spins[key] = sp
            row2.addWidget(QLabel(label))
            row2.addWidget(sp)
        row2.addStretch(1)
        self.pick_btn = QPushButton("Pick a pipe")
        self.pick_btn.setCheckable(True)
        self.pick_btn.setToolTip("Click a lateral or main on the map to show its profile")
        self.png_btn = QPushButton("Save image…")
        self.csv_btn = QPushButton("Save table…")
        for b in (self.pick_btn, self.png_btn, self.csv_btn):
            row2.addWidget(b)
        v.addLayout(row2)

        self.chart = ProfileChart()
        v.addWidget(self.chart, 1)
        self.status = QLabel("")
        self.status.setStyleSheet("color: #666;")
        v.addWidget(self.status)
        self.setWidget(w)

        self.pick_btn.toggled.connect(self._toggle_pick)
        self.png_btn.clicked.connect(self._save_png)
        self.csv_btn.clicked.connect(self._save_csv)
        self.dem_box.layerChanged.connect(self.refresh)
        self.dem_units.currentIndexChanged.connect(self.refresh)
        self.units.currentIndexChanged.connect(self._units_changed)
        for sp in self.spins.values():
            sp.valueChanged.connect(self._redraw)

    def _restore(self):
        s = QgsSettings()
        u = s.value(SETTINGS + "units", "ft")
        self.units.blockSignals(True)
        self.units.setCurrentIndex(0 if u == "ft" else 1)
        self.units.blockSignals(False)
        self.dem_units.blockSignals(True)
        self.dem_units.setCurrentIndex(int(s.value(SETTINGS + "dem_units", 0)))
        self.dem_units.blockSignals(False)
        for key, sp in self.spins.items():
            sp.blockSignals(True)
            sp.setValue(float(s.value(SETTINGS + key, DEFAULTS[self._unit()][key])))
            sp.blockSignals(False)

    def _save_settings(self):
        if not self._ready:
            return
        s = QgsSettings()
        s.setValue(SETTINGS + "units", self._unit())
        s.setValue(SETTINGS + "dem_units", self.dem_units.currentIndex())
        for key, sp in self.spins.items():
            s.setValue(SETTINGS + key, sp.value())

    def _unit(self):
        return UNITS[self.units.currentIndex()][0]

    def _units_changed(self, *args):
        # switch the four depths to the defaults of the new units
        for key, sp in self.spins.items():
            sp.blockSignals(True)
            sp.setValue(DEFAULTS[self._unit()][key])
            sp.blockSignals(False)
        self.refresh()

    # ------------------------------------------------------------ layers
    @staticmethod
    def _is_network(layer):
        return (isinstance(layer, QgsVectorLayer)
                and layer.fields().indexOf("pipe_id") >= 0
                and layer.fields().indexOf("to_node") >= 0)

    def _pick_network_layer(self):
        if self._is_network(self.layer_box.currentLayer()):
            return
        for layer in QgsProject.instance().mapLayers().values():
            if self._is_network(layer):
                self.layer_box.setLayer(layer)
                return

    def _current_layer_changed(self, layer):
        if self._is_network(layer) and layer is not self.layer:
            self.layer_box.setLayer(layer)

    def _layer_changed(self, layer):
        if self.layer is not None:
            try:
                self.layer.selectionChanged.disconnect(self._selection_changed)
            except (TypeError, RuntimeError):
                pass
        self.layer = layer if isinstance(layer, QgsVectorLayer) else None
        if self.tool is not None and self.layer is not None:
            self.tool.setLayer(self.layer)
        if self.layer is not None:
            self.layer.selectionChanged.connect(self._selection_changed)
            ids = self.layer.selectedFeatureIds()
            self.fid = ids[-1] if ids else None
        else:
            self.fid = None
        self.refresh()

    def _selection_changed(self, selected, deselected, clear_and_select):
        if selected:
            self.fid = selected[-1]
        else:
            ids = self.layer.selectedFeatureIds() if self.layer is not None else []
            self.fid = ids[-1] if ids else None
        self.refresh()

    # ------------------------------------------------------------ profile
    def refresh(self, *args):
        self._save_settings()
        self.band.reset(LINE_GEOMETRY)
        self.profile = None
        if self.layer is None:
            self.chart.set_message("Choose the network layer made by Illini Subsurface Drain "
                                   "Layout (or Subsurface Drainage Layout).")
            self.status.setText("")
            return
        if self.fid is None:
            self.chart.set_message("Select a lateral or main in “{}”, or use Pick a "
                                   "pipe, to see its profile.".format(self.layer.name()))
            self.status.setText("")
            return
        try:
            self.profile = self._build_profile()
        except ProfileError as e:
            self.chart.set_message(str(e))
            self.status.setText("")
            return
        self._redraw()

    def _build_profile(self):
        layer = self.layer
        feat = layer.getFeature(self.fid)
        if not feat.isValid():
            raise ProfileError("The selected feature could not be read.")
        rec = _record(feat)
        pipe_id = rec["attrs"].get("pipe_id")
        lines, all_lines = [rec], None
        if pipe_id is not None:
            req = QgsFeatureRequest().setFilterExpression(
                "\"pipe_id\" = {}".format(int(pipe_id)))
            lines = [_record(f) for f in layer.getFeatures(req)]
            nodes = [l["attrs"].get("from_node") for l in lines]
            nodes = [int(n) for n in nodes if n is not None]
            if nodes:
                req = QgsFeatureRequest().setFilterExpression(
                    "\"to_node\" IN ({})".format(",".join(str(n) for n in sorted(set(nodes)))))
                all_lines = [_record(f) for f in layer.getFeatures(req)]
        for l in lines:
            g = l.pop("geometry", None)
            if g is not None and not g.isEmpty():
                self.band.addGeometry(g, layer)
        for l in all_lines or []:
            l.pop("geometry", None)

        unit_m = UNITS[self.units.currentIndex()][1]
        xy_scale = _metres_per_map_unit(layer.crs()) / unit_m
        sampler, step = None, None
        dem = self.dem_box.currentLayer()
        if dem is not None:
            prov = dem.dataProvider()
            tr = QgsCoordinateTransform(layer.crs(), dem.crs(), QgsProject.instance())
            zf = DEM_UNITS[self.dem_units.currentIndex()][1] / unit_m

            def sampler(x, y):
                try:
                    pt = tr.transform(QgsPointXY(x, y))
                    val, ok = prov.sample(pt, 1)
                except Exception:      # noqa: BLE001  (outside the raster, bad transform)
                    return None
                return float(val) * zf if ok else None

            # about one DEM cell, in the network layer's map units
            res_m = abs(dem.rasterUnitsPerPixelX()) * _metres_per_map_unit(dem.crs())
            step = max(res_m / _metres_per_map_unit(layer.crs()), 1e-6)
        return build_profile(lines, all_lines, ground_sampler=sampler, sample_step=step,
                             xy_scale=xy_scale)

    def _depths(self):
        return {k: sp.value() for k, sp in self.spins.items()}

    def _redraw(self, *args):
        self._save_settings()
        if self.profile is None:
            return
        model = ChartModel(self.profile, self._depths(), self._unit())
        self.chart.set_model(model)
        p = self.profile
        msg = "{} line(s) · {} tile point(s)".format(len(p["line_ids"]), len(p["tile"]))
        if model.bad:
            msg += " · tile outside the absolute depths in places (red)"
        self.status.setText(msg)

    # ------------------------------------------------------------ pick tool
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

    def _picked(self, feature):
        if self.layer is not None:
            self.layer.selectByIds([feature.id()])

    def _map_tool_set(self, new_tool, old_tool=None):
        if new_tool is not self.tool and self.pick_btn.isChecked():
            self.pick_btn.blockSignals(True)
            self.pick_btn.setChecked(False)
            self.pick_btn.blockSignals(False)

    # ------------------------------------------------------------ export
    def _save_png(self, *args):
        if self.profile is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save profile image",
                                              self.profile["name"] + ".png",
                                              "PNG image (*.png)")
        if not path:
            return
        w, h = max(self.chart.width(), 900), max(self.chart.height(), 420)
        img = QImage(w * 2, h * 2, QImage.Format_ARGB32)
        img.fill(QColor(255, 255, 255))
        painter = QPainter(img)
        painter.scale(2, 2)
        self.chart.paint_to(painter, w, h, hover=False)
        painter.end()
        img.save(path)

    def _save_csv(self, *args):
        if self.profile is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save profile table",
                                              self.profile["name"] + ".csv",
                                              "CSV file (*.csv)")
        if not path:
            return
        u = self._unit()
        d = self._depths()
        with open(path, "w", newline="", encoding="utf-8") as fh:
            wr = csv.writer(fh)
            wr.writerow(["station_" + u, "ground", "tile", "depth_" + u,
                         "upper_tile_depth", "lower_tile_depth", "absolute_upper",
                         "absolute_lower"])
            for s, g, t, dep in table_rows(self.profile):
                wr.writerow(["{:.2f}".format(s), "{:.3f}".format(g), "{:.3f}".format(t),
                             "{:.3f}".format(dep), "{:.3f}".format(g - d["upper"]),
                             "{:.3f}".format(g - d["lower"]), "{:.3f}".format(g - d["abs_upper"]),
                             "{:.3f}".format(g - d["abs_lower"])])

    # ------------------------------------------------------------ teardown
    def cleanup(self):
        try:
            self.iface.currentLayerChanged.disconnect(self._current_layer_changed)
        except (TypeError, RuntimeError):
            pass
        try:
            self.canvas.mapToolSet.disconnect(self._map_tool_set)
        except (TypeError, RuntimeError):
            pass
        if self.layer is not None:
            try:
                self.layer.selectionChanged.disconnect(self._selection_changed)
            except (TypeError, RuntimeError):
                pass
        if self.tool is not None and self.canvas.mapTool() is self.tool:
            self.canvas.unsetMapTool(self.tool)
        self.band.reset(LINE_GEOMETRY)
        self.canvas.scene().removeItem(self.band)

    def closeEvent(self, event):
        self.band.reset(LINE_GEOMETRY)
        if self.pick_btn.isChecked():
            self.pick_btn.setChecked(False)
        super().closeEvent(event)
