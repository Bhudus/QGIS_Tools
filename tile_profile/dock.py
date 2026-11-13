"""Tile Profile panel: shows the profile of the pipe selected in a network layer."""
import csv

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSettings,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsMapLayerComboBox, QgsMapToolIdentifyFeature, QgsRubberBand
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor, QImage, QPainter
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .chart import ProfileChart
from .chart_model import ChartModel
from .profile_core import (
    ProfileError,
    build_profile,
    check_pipe,
    group_pipes,
    offset_changes,
    offset_profile,
    rasterize_samples,
    table_rows,
)

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


def _shift_z(part, dz):
    """Add dz to the Z of every vertex of an abstract geometry (in place)."""
    if hasattr(part, "numGeometries"):
        for i in range(part.numGeometries()):
            _shift_z(part.geometryN(i), dz)
        return
    for i in range(part.numPoints()):
        part.setZAt(i, part.zAt(i) + dz)


class TileProfileDock(QDockWidget):
    def __init__(self, iface, parent=None):
        super().__init__("Tile Profile", parent)
        self.setObjectName("TileProfileDock")
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.layer = None
        self.fid = None
        self.profile = None
        self.shown = None                   # the profile as drawn (with any offset)
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
        self.offset = QDoubleSpinBox()
        self.offset.setDecimals(2)
        self.offset.setRange(-50.0, 50.0)
        self.offset.setSingleStep(0.1)
        self.offset.setToolTip("Lower the whole tile network by this amount (negative "
                               "raises it). The profile shows it at once; Apply to layer "
                               "writes it into the network layer.")
        self.apply_btn = QPushButton("Apply to layer…")
        self.apply_btn.setToolTip("Lower every tile elevation in the network layer by the "
                                  "offset")
        self.apply_btn.setEnabled(False)
        row2.addWidget(QLabel("Lower network by"))
        row2.addWidget(self.offset)
        row2.addWidget(self.apply_btn)
        self.pick_btn = QPushButton("Pick a pipe")
        self.pick_btn.setCheckable(True)
        self.pick_btn.setToolTip("Click a lateral or main on the map to show its profile")
        self.png_btn = QPushButton("Save image…")
        self.csv_btn = QPushButton("Save table…")
        for b in (self.pick_btn, self.png_btn, self.csv_btn):
            row2.addWidget(b)
        v.addLayout(row2)

        row3 = QHBoxLayout()
        self.check_btn = QPushButton("Check network")
        self.check_btn.setToolTip(
            "Check every pipe against the absolute depths: adds Tile elevation and Tile "
            "depth check layers to the map and lists the pipes to check")
        self.check_label = QLabel("")
        self.check_label.setStyleSheet("color: #444;")
        row3.addWidget(self.check_btn)
        row3.addWidget(self.check_label, 1)
        v.addLayout(row3)

        self.chart = ProfileChart()
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Pipe", "Kind", "Too shallow", "Too deep",
                                              "Min depth", "Max depth"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setToolTip("Pipes with tile outside the absolute depths, worst first. "
                              "Click one to see its profile.")
        self.table.hide()
        self.check_rows = []
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.chart)
        split.addWidget(self.table)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        v.addWidget(split, 1)
        self.status = QLabel("")
        self.status.setStyleSheet("color: #666;")
        v.addWidget(self.status)
        self.setWidget(w)

        self.pick_btn.toggled.connect(self._toggle_pick)
        self.png_btn.clicked.connect(self._save_png)
        self.csv_btn.clicked.connect(self._save_csv)
        self.offset.valueChanged.connect(self._offset_changed)
        self.check_btn.clicked.connect(self._check_network)
        self.table.cellClicked.connect(self._check_row_clicked)
        self.apply_btn.clicked.connect(self._apply_offset)
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
        self.offset.setSuffix(" " + self._unit())
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

    def _offset_changed(self, *args):
        self.apply_btn.setEnabled(bool(self.offset.value()) and self.layer is not None)
        self._redraw()

    def _units_changed(self, *args):
        self.offset.setSuffix(" " + self._unit())
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
        self.apply_btn.setEnabled(bool(self.offset.value()) and self.layer is not None)
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
        self.shown = None
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
        off = self.offset.value()
        p = self.shown = offset_profile(self.profile, off)
        model = ChartModel(p, self._depths(), self._unit())
        self.chart.set_model(model)
        msg = "{} line(s) · {} tile point(s)".format(len(p["line_ids"]), len(p["tile"]))
        if model.bad:
            msg += " · tile outside the absolute depths in places (red)"
        if off:
            msg += " · previewing the network {} {:g} {} (not applied)".format(
                "lowered" if off > 0 else "raised", abs(off), self._unit())
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
        if self.shown is None:
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
        if self.shown is None:
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
            for s, g, t, dep in table_rows(self.shown):
                wr.writerow(["{:.2f}".format(s), "{:.3f}".format(g), "{:.3f}".format(t),
                             "{:.3f}".format(dep), "{:.3f}".format(g - d["upper"]),
                             "{:.3f}".format(g - d["lower"]), "{:.3f}".format(g - d["abs_upper"]),
                             "{:.3f}".format(g - d["abs_lower"])])

    # ------------------------------------------------------------ network check
    def _check_network(self, *args):
        if self.layer is None:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self._run_check()
        except (ProfileError, ValueError) as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Check network", str(e))
            return
        QApplication.restoreOverrideCursor()
        self._show_check(result)

    def _run_check(self):
        """Check every pipe; write and add the raster layers; return the pipes to check."""
        from .check_layers import add_layers, bilinear, read_dem, write_raster
        import math
        import numpy as np

        layer, u, d = self.layer, self._unit(), self._depths()
        off = self.offset.value()
        recs = []
        for f in layer.getFeatures():
            r = _record(f)
            r.pop("geometry", None)
            r["fid"] = f.id()
            recs.append(r)
        if not recs:
            raise ProfileError("“{}” has no lines.".format(layer.name()))
        unit_m = UNITS[self.units.currentIndex()][1]
        m_map = _metres_per_map_unit(layer.crs())
        xy_scale = m_map / unit_m
        dem = self.dem_box.currentLayer()
        if dem is not None:
            tr = QgsCoordinateTransform(layer.crs(), dem.crs(), QgsProject.instance())
            same = layer.crs() == dem.crs()
            rx = abs(dem.rasterUnitsPerPixelX())
            ext = tr.transformBoundingBox(layer.extent())
            ext = QgsRectangle(ext.xMinimum() - 3 * rx, ext.yMinimum() - 3 * rx,
                               ext.xMaximum() + 3 * rx, ext.yMaximum() + 3 * rx)
            arr, grid = read_dem(dem, ext)
            zf = DEM_UNITS[self.dem_units.currentIndex()][1] / unit_m

            def to_grid(x, y):
                if same:
                    return x, y
                p = tr.transform(QgsPointXY(x, y))
                return p.x(), p.y()

            def sampler(x, y):
                gx, gy = to_grid(x, y)
                v = bilinear(arr, grid, gx, gy)
                return None if v is None else v * zf

            cell_map = rx * _metres_per_map_unit(dem.crs()) / m_map
            out_grid, shape, wkt = grid, arr.shape, dem.crs().toWkt()
        else:
            sampler = None

            def to_grid(x, y):
                return x, y

            cell_map = 1.0 / m_map                       # 1 m cells on the layer's CRS
            e = layer.extent()
            x0 = math.floor(e.xMinimum() / cell_map - 3) * cell_map
            y0 = math.ceil(e.yMaximum() / cell_map + 3) * cell_map
            ncols = int(math.ceil((e.xMaximum() - x0) / cell_map)) + 3
            nrows = int(math.ceil((y0 - e.yMinimum()) / cell_map)) + 3
            if ncols * nrows > 60_000_000:
                raise ValueError("The network is too large to check on 1 m cells.")
            out_grid, shape, wkt = (x0, y0, cell_map, cell_map), (nrows, ncols), \
                layer.crs().toWkt()
        step = cell_map / 3.0
        rows, samples, skipped = [], [], 0
        pipes = group_pipes(recs)
        for key, lines in pipes.items():
            try:
                prof = build_profile(lines, recs, ground_sampler=sampler, sample_step=step,
                                     xy_scale=xy_scale)
            except ProfileError:
                skipped += 1
                continue
            prof = offset_profile(prof, off)
            res = check_pipe(prof, d["abs_upper"], d["abs_lower"], step * xy_scale)
            samples += [to_grid(x, y) + (t, dd, c) for x, y, t, dd, c in res["samples"]]
            if res["shallow"] > 0 or res["deep"] > 0:
                rows.append({"name": prof["name"], "kind": prof["kind"],
                             "fid": lines[0]["fid"], "shallow": res["shallow"],
                             "deep": res["deep"], "min_depth": res["min_depth"],
                             "max_depth": res["max_depth"]})
        rows.sort(key=lambda r: -(r["shallow"] + r["deep"]))
        x0, y0, cx = out_grid[0], out_grid[1], out_grid[2]
        tile, depth, code, counts = rasterize_samples(samples, x0, y0, cx, shape[1], shape[0],
                                                      cell_y=out_grid[3])
        path = write_raster(tile, depth, code, out_grid, wkt, u)
        vals = tile[np.isfinite(tile)]
        add_layers(QgsProject.instance(), path,
                   float(vals.min()) if vals.size else 0.0,
                   float(vals.max()) if vals.size else 1.0,
                   "Shallower than {:g} {}".format(d["abs_upper"], u),
                   "Deeper than {:g} {}".format(d["abs_lower"], u))
        return {"rows": rows, "pipes": len(pipes) - skipped, "skipped": skipped,
                "cells": counts, "offset": off, "dem": dem is not None}

    def _show_check(self, result):
        u, d = self._unit(), self._depths()
        rows = self.check_rows = result["rows"]
        txt = "{} of {} pipes to check: tile shallower than {:g} or deeper than {:g} {}".format(
            len(rows), result["pipes"], d["abs_upper"], d["abs_lower"], u)
        if not rows:
            txt = "All {} pipes are within the absolute depths ({:g}–{:g} {})".format(
                result["pipes"], d["abs_upper"], d["abs_lower"], u)
        if result["offset"]:
            txt += ", with the network {} {:g} {} (preview)".format(
                "lowered" if result["offset"] > 0 else "raised", abs(result["offset"]), u)
        if not result["dem"]:
            txt += ". No DEM chosen: ground is straight between vertices"
        if result["skipped"]:
            txt += ". {} pipe(s) without tile elevations skipped".format(result["skipped"])
        self.check_label.setText(txt + ".")
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["name"], r["kind"],
                    "{:,.0f} {}".format(r["shallow"], u) if r["shallow"] else "",
                    "{:,.0f} {}".format(r["deep"], u) if r["deep"] else "",
                    "{:.2f}".format(r["min_depth"]), "{:.2f}".format(r["max_depth"])]
            for j, val in enumerate(vals):
                self.table.setItem(i, j, QTableWidgetItem(val))
        self.table.resizeColumnsToContents()
        self.table.setVisible(bool(rows))

    def _check_row_clicked(self, row, col=0, *args):
        if self.layer is None or not 0 <= row < len(self.check_rows):
            return
        fid = self.check_rows[row]["fid"]
        self.layer.selectByIds([fid])
        try:
            self.canvas.panToFeatureIds(self.layer, [fid])
            self.canvas.refresh()
        except (AttributeError, TypeError):
            pass

    # ------------------------------------------------------------ offset
    def _apply_offset(self, *args):
        """Lower every line of the network layer by the offset, as one undoable edit."""
        layer, d, u = self.layer, self.offset.value(), self._unit()
        if layer is None or not d:
            return
        word = "Lower" if d > 0 else "Raise"
        n = layer.featureCount()
        answer = QMessageBox.question(
            self, "Apply network offset",
            "{} every tile in “{}” by {:g} {}?\n\n"
            "This changes all {} lines: the tile elevations (Z values, invert_us/invert_ds, "
            "ElevDepth1/ElevDepth2) and the depths (min_depth, max_depth, depth_ds, "
            "BuryDepth1/BuryDepth2). Ground elevations and slopes do not change, and the "
            "flag and BURY_CHECK fields are not checked again.\n\n"
            "The layer is left in edit mode: save the edits to keep them, or roll them "
            "back.".format(word, layer.name(), abs(d), u, n),
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return
        if not layer.isEditable() and not layer.startEditing():
            QMessageBox.warning(self, "Apply network offset",
                                "“{}” cannot be edited.".format(layer.name()))
            return
        fields = layer.fields()
        has_z = QgsWkbTypes.hasZ(layer.wkbType())
        layer.beginEditCommand("Tile Profile: {} network by {:g} {}".format(
            word.lower(), abs(d), u))
        changed = 0
        try:
            for f in layer.getFeatures():
                attrs = {name: _value(f[name]) for name in fields.names()}
                for name, value in offset_changes(attrs, d).items():
                    layer.changeAttributeValue(f.id(), fields.indexOf(name), value)
                g = f.geometry()
                if has_z and g is not None and not g.isEmpty():
                    part = g.constGet().clone()
                    _shift_z(part, -d)
                    layer.changeGeometry(f.id(), QgsGeometry(part))
                changed += 1
        except Exception:
            layer.destroyEditCommand()
            raise
        layer.endEditCommand()
        layer.triggerRepaint()
        self.iface.messageBar().pushMessage(
            "Tile Profile",
            "{} {} lines of “{}” by {:g} {}. Save the layer's edits to keep the change."
            .format("Lowered" if d > 0 else "Raised", changed, layer.name(), abs(d), u),
            level=Qgis.Info, duration=8)
        self.offset.blockSignals(True)
        self.offset.setValue(0.0)
        self.offset.blockSignals(False)
        self.apply_btn.setEnabled(False)
        self.refresh()

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
