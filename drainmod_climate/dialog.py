import os

from qgis.PyQt.QtCore import Qt, QSettings
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QDoubleSpinBox, QSpinBox, QPushButton, QComboBox,
    QListWidget, QListWidgetItem, QCheckBox, QRadioButton, QButtonGroup,
    QStackedWidget, QFileDialog, QGroupBox, QMessageBox, QDialogButtonBox,
    QPlainTextEdit,
)
from qgis.core import (
    QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsProject, Qgis,
)
from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox, QgsMapToolEmitPoint

from .providers import nex_gddp, loca2
from .providers.local_netcdf import classify_files
from .task import BuildWeatherTask, Loca2Task
from .core.drainmod_native import default_station_id

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
S = "drainmod_climate/"


def _checklist(items, checked=()):
    w = QListWidget()
    for it in items:
        li = QListWidgetItem(it)
        li.setFlags(li.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        li.setCheckState(Qt.CheckState.Checked if it in checked else Qt.CheckState.Unchecked)
        w.addItem(li)
    return w


def _checked(w):
    return [w.item(i).text() for i in range(w.count())
            if w.item(i).checkState() == Qt.CheckState.Checked]


def _scen_boxes(names, default):
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    cbs = []
    for n in names:
        cb = QCheckBox(n)
        cb.setChecked(n in default)
        lay.addWidget(cb)
        cbs.append(cb)
    lay.addStretch()
    return box, cbs


class DrainmodClimateDialog(QDialog):
    def __init__(self, iface, parent=None):
        QDialog.__init__(self, parent or iface.mainWindow())
        self.iface = iface
        self.tasks = []
        self.setWindowTitle("DRAINMOD Climate-Scenario Weather Files")
        self.resize(640, 640)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._loc_tab(), "1  Location")
        tabs.addTab(self._data_tab(), "2  Climate data")
        tabs.addTab(self._out_tab(), "3  Output")
        lay.addWidget(tabs)
        bb = QDialogButtonBox()
        self.run_btn = bb.addButton("Create weather files", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Close)
        self.run_btn.clicked.connect(self.run)
        bb.rejected.connect(self.close)
        lay.addWidget(bb)
        self._load_settings()

    # ------------------------------------------------------------ tab 1
    def _loc_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.rb_point = QRadioButton("Single location")
        self.rb_layer = QRadioButton("All points of a layer (one set of files per point)")
        self.rb_point.setChecked(True)
        g = QButtonGroup(w)
        g.addButton(self.rb_point)
        g.addButton(self.rb_layer)

        v.addWidget(self.rb_point)
        f = QFormLayout()
        self.site_name = QLineEdit("site1")
        self.lat = QDoubleSpinBox()
        self.lat.setRange(-90, 90)
        self.lat.setDecimals(5)
        self.lat.setValue(35.78)
        self.lon = QDoubleSpinBox()
        self.lon.setRange(-180, 360)
        self.lon.setDecimals(5)
        self.lon.setValue(-78.68)
        pick = QPushButton("Pick on map…")
        pick.clicked.connect(self.pick_on_map)
        self.station = QLineEdit()
        self.station.setMaxLength(6)
        self.station.setToolTip("Up to 6 characters; written into the .RAI/.TEM records and "
                                "used as the file name. Defaults to lat/lon truncated to 0.1°.")
        self._station_auto = True
        self.station.textEdited.connect(lambda _t: setattr(self, "_station_auto", False))
        self.lat.valueChanged.connect(self._auto_station)
        self.lon.valueChanged.connect(self._auto_station)
        self._auto_station()
        f.addRow("Site name (folder)", self.site_name)
        f.addRow("Latitude (°N)", self.lat)
        f.addRow("Longitude (°E, west negative)", self.lon)
        f.addRow("DRAINMOD station ID", self.station)
        f.addRow("", pick)
        v.addLayout(f)

        v.addWidget(self.rb_layer)
        f2 = QFormLayout()
        self.layer_cb = QgsMapLayerComboBox()
        try:
            from qgis.core import QgsMapLayerProxyModel
            self.layer_cb.setFilters(QgsMapLayerProxyModel.Filter.PointLayer)
        except Exception:  # noqa
            self.layer_cb.setFilters(Qgis.LayerFilter.PointLayer)
        self.name_field = QgsFieldComboBox()
        self.name_field.setLayer(self.layer_cb.currentLayer())
        self.layer_cb.layerChanged.connect(self.name_field.setLayer)
        self.sel_only = QCheckBox("Selected features only")
        f2.addRow("Point layer", self.layer_cb)
        f2.addRow("Site name field", self.name_field)
        f2.addRow("", self.sel_only)
        v.addLayout(f2)
        v.addStretch()
        return w

    def _auto_station(self, *_a):
        if self._station_auto:
            self.station.setText(default_station_id(self.lat.value(), self.lon.value()))

    def pick_on_map(self):
        canvas = self.iface.mapCanvas()
        self._prev_tool = canvas.mapTool()
        self._tool = QgsMapToolEmitPoint(canvas)
        self._tool.canvasClicked.connect(self._picked)
        canvas.setMapTool(self._tool)
        self.hide()
        self.iface.messageBar().pushInfo("DRAINMOD Climate", "Click the site location on the map")

    def _picked(self, pt, _button):
        canvas = self.iface.mapCanvas()
        tr = QgsCoordinateTransform(canvas.mapSettings().destinationCrs(), WGS84, QgsProject.instance())
        p = tr.transform(pt)
        self.lat.setValue(p.y())
        self.lon.setValue(p.x())
        self.rb_point.setChecked(True)
        if self._prev_tool:
            canvas.setMapTool(self._prev_tool)
        else:
            canvas.unsetMapTool(self._tool)
        self.show()
        self.raise_()

    def _sites(self):
        if self.rb_point.isChecked():
            sid = self.station.text().strip() or default_station_id(self.lat.value(), self.lon.value())
            return [{"name": self.site_name.text().strip() or sid, "station": sid,
                     "lat": self.lat.value(), "lon": self.lon.value()}]
        lyr = self.layer_cb.currentLayer()
        if lyr is None:
            raise ValueError("Choose a point layer.")
        tr = QgsCoordinateTransform(lyr.crs(), WGS84, QgsProject.instance())
        fld = self.name_field.currentField()
        feats = lyr.selectedFeatures() if self.sel_only.isChecked() else lyr.getFeatures()
        sites = []
        for i, ft in enumerate(feats):
            g = ft.geometry()
            if g is None or g.isEmpty():
                continue
            p = tr.transform(g.centroid().asPoint())
            name = str(ft[fld]) if fld else "pt%d" % ft.id()
            sites.append({"name": name, "lat": p.y(), "lon": p.x(),
                          "station": default_station_id(p.y(), p.x())})
        if not sites:
            raise ValueError("The layer has no (selected) points.")
        return sites

    # ------------------------------------------------------------ tab 2
    def _data_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        h = QHBoxLayout()
        h.addWidget(QLabel("Dataset"))
        self.source = QComboBox()
        self.source.addItems([
            "NASA NEX-GDDP-CMIP6 (global, 0.25°, direct from NCCS THREDDS)",
            "LOCA2 (CONUS/N. America, 1/16°, download from UCSD)",
            "Local NetCDF files (LOCA2, NEX, GDO-DCP subsets, CORDEX…)",
            "Custom THREDDS NetCDF Subset Service (URL template)",
        ])
        h.addWidget(self.source, 1)
        v.addLayout(h)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._nex_page())
        self.stack.addWidget(self._loca_page())
        self.stack.addWidget(self._local_page())
        self.stack.addWidget(self._ncss_page())
        self.source.currentIndexChanged.connect(self.stack.setCurrentIndex)
        v.addWidget(self.stack, 1)

        gb = QGroupBox("Periods (whole calendar years)")
        f = QFormLayout(gb)
        self.h0, self.h1 = self._yr(1985), self._yr(2014)
        self.f0, self.f1 = self._yr(2041), self._yr(2070)
        f.addRow("Historical baseline", self._pair(self.h0, self.h1))
        f.addRow("Future (SSP scenarios)", self._pair(self.f0, self.f1))
        v.addWidget(gb)
        return w

    def _yr(self, val):
        s = QSpinBox()
        s.setRange(1950, 2100)
        s.setValue(val)
        return s

    def _pair(self, a, b):
        w = QWidget()
        l = QHBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.addWidget(a)
        l.addWidget(QLabel("to"))
        l.addWidget(b)
        l.addStretch()
        return w

    def _nex_page(self):
        w = QWidget()
        f = QFormLayout(w)
        self.nex_models = _checklist(nex_gddp.MODELS, ("ACCESS-CM2", "GFDL-ESM4", "MRI-ESM2-0"))
        btns = QHBoxLayout()
        b_all = QPushButton("All")
        b_none = QPushButton("None")
        b_ref = QPushButton("Refresh list from server")
        b_all.clicked.connect(lambda: self._set_all(self.nex_models, True))
        b_none.clicked.connect(lambda: self._set_all(self.nex_models, False))
        b_ref.clicked.connect(self.refresh_nex_models)
        for b in (b_all, b_none, b_ref):
            btns.addWidget(b)
        box, self.nex_scen = _scen_boxes(nex_gddp.SCENARIOS, ("historical", "ssp245", "ssp585"))
        self.thredds = QLineEdit(nex_gddp.THREDDS)
        f.addRow("Models (GCMs)", self.nex_models)
        f.addRow("", self._wrap(btns))
        f.addRow("Scenarios", box)
        f.addRow("THREDDS server", self.thredds)
        note = QLabel("One small request per variable-year is sent to NASA NCCS "
                      "(≈ 3 × number of years per model/scenario). Responses are cached.")
        note.setWordWrap(True)
        f.addRow(note)
        return w

    def _loca_page(self):
        w = QWidget()
        f = QFormLayout(w)
        self.loca_models = _checklist(loca2.MODELS, ("ACCESS-CM2",))
        self.loca_region = QComboBox()
        for k, d in loca2.REGIONS.items():
            self.loca_region.addItem("%s — %s" % (k, d), k)
        self.loca_member = QComboBox()
        self.loca_member.setEditable(True)
        self.loca_member.addItem("r1i1p1f1")
        b_mem = QPushButton("List members")
        b_mem.clicked.connect(self.list_loca_members)
        mh = QHBoxLayout()
        mh.addWidget(self.loca_member, 1)
        mh.addWidget(b_mem)
        box, self.loca_scen = _scen_boxes(loca2.SCENARIOS, ("historical", "ssp245", "ssp585"))
        self.loca_dir = QLineEdit(os.path.join(os.path.expanduser("~"), "LOCA2"))
        b_dir = QPushButton("…")
        b_dir.clicked.connect(lambda: self._browse_dir(self.loca_dir))
        dh = QHBoxLayout()
        dh.addWidget(self.loca_dir, 1)
        dh.addWidget(b_dir)
        go = QPushButton("Find && download files, then use them")
        go.clicked.connect(self.download_loca)
        f.addRow("Models", self.loca_models)
        f.addRow("Region", self.loca_region)
        f.addRow("Ensemble member", self._wrap(mh))
        f.addRow("Scenarios", box)
        f.addRow("Download folder", self._wrap(dh))
        f.addRow(go)
        note = QLabel("LOCA2 is distributed as whole regional files (often several GB "
                      "per variable and period). Files already in the folder are reused. "
                      "For a single site, a point subset from the LLNL GDO-DCP portal "
                      "(gdo-dcp.ucllnl.org) is much smaller — load it under "
                      "'Local NetCDF files'.")
        note.setWordWrap(True)
        f.addRow(note)
        return w

    def _local_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.local_list = QListWidget()
        h = QHBoxLayout()
        for txt, fn in (("Add files…", self.add_local), ("Add folder…", self.add_local_dir),
                        ("Remove", self.rm_local), ("Clear", self.local_list.clear)):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            h.addWidget(b)
        v.addWidget(QLabel("NetCDF files (pr, tasmax, tasmin; several periods allowed). "
                           "They are grouped by model and scenario from the file names."))
        v.addWidget(self.local_list, 1)
        v.addLayout(h)
        f = QFormLayout()
        self.lv = {k: QLineEdit() for k in ("pr", "tasmax", "tasmin")}
        for k, e in self.lv.items():
            e.setPlaceholderText("auto")
            f.addRow("Variable name for %s" % k, e)
        v.addLayout(f)
        self.local_info = QPlainTextEdit()
        self.local_info.setReadOnly(True)
        self.local_info.setMaximumHeight(90)
        v.addWidget(self.local_info)
        self.local_list.model().rowsInserted.connect(self._update_groups)
        self.local_list.model().rowsRemoved.connect(self._update_groups)
        return w

    def _ncss_page(self):
        w = QWidget()
        f = QFormLayout(w)
        self.ncss_tpl = QLineEdit()
        self.ncss_tpl.setPlaceholderText(
            "https://host/thredds/ncss/grid/path/{var}_{model}_{scenario}_{year}.nc")
        self.ncss_models = QLineEdit("model")
        self.ncss_scen = QLineEdit("historical, ssp245")
        self.nv = {k: QLineEdit(k) for k in ("pr", "tasmax", "tasmin")}
        f.addRow("URL template", self.ncss_tpl)
        f.addRow("Models (comma separated)", self.ncss_models)
        f.addRow("Scenarios (comma separated)", self.ncss_scen)
        for k, e in self.nv.items():
            f.addRow("Server variable for %s" % k, e)
        note = QLabel("Placeholders: {var} {model} {scenario} {year}. Without {year} the "
                      "whole period is requested at once. Scenarios named 'historical' "
                      "use the baseline period.")
        note.setWordWrap(True)
        f.addRow(note)
        return w

    def _wrap(self, layout):
        w = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(layout)
        return w

    def _set_all(self, lw, on):
        for i in range(lw.count()):
            lw.item(i).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _browse_dir(self, edit):
        d = QFileDialog.getExistingDirectory(self, "Folder", edit.text())
        if d:
            edit.setText(d)

    def refresh_nex_models(self):
        try:
            QgsApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            prov = nex_gddp.NexGddpProvider(self.thredds.text())
            models = prov.list_models()
        except Exception as e:  # noqa
            QMessageBox.warning(self, "NEX-GDDP-CMIP6", "Could not read catalog:\n%s" % e)
            return
        finally:
            QgsApplication.restoreOverrideCursor()
        keep = set(_checked(self.nex_models))
        self.nex_models.clear()
        for m in models:
            li = QListWidgetItem(m)
            li.setFlags(li.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            li.setCheckState(Qt.CheckState.Checked if m in keep else Qt.CheckState.Unchecked)
            self.nex_models.addItem(li)

    def list_loca_members(self):
        models = _checked(self.loca_models)
        if not models:
            return
        try:
            QgsApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            mem = loca2.list_members(models[0], self.loca_region.currentData())
        except Exception as e:  # noqa
            QMessageBox.warning(self, "LOCA2", "Could not list members:\n%s" % e)
            return
        finally:
            QgsApplication.restoreOverrideCursor()
        self.loca_member.clear()
        self.loca_member.addItems(mem or ["r1i1p1f1"])

    def download_loca(self):
        models = _checked(self.loca_models)
        scens = [c.text() for c in self.loca_scen if c.isChecked()]
        if not models or not scens:
            QMessageBox.information(self, "LOCA2", "Select at least one model and scenario.")
            return
        folder = self.loca_dir.text()
        if QMessageBox.question(
                self, "LOCA2",
                "This downloads the full regional pr, tasmax and tasmin files for %d "
                "model/scenario combination(s) into\n%s\n\nThese files are large "
                "(GBs). Continue?" % (len(models) * len(scens), folder)) != QMessageBox.StandardButton.Yes:
            return
        t = Loca2Task([(m, s) for m in models for s in scens],
                      self.loca_region.currentData(),
                      self.loca_member.currentText().strip(), folder)
        t.taskCompleted.connect(lambda: self._loca_done(t))
        t.taskTerminated.connect(lambda: self._fail(t, "LOCA2 download"))
        self._start(t)

    def _loca_done(self, t):
        for p in t.done:
            if not self.local_list.findItems(p, Qt.MatchFlag.MatchExactly):
                self.local_list.addItem(p)
        self.source.setCurrentIndex(2)
        msg = "%d LOCA2 file(s) ready." % len(t.done)
        if t.missing:
            msg += " Not found: " + "; ".join(t.missing)
        self.iface.messageBar().pushSuccess("DRAINMOD Climate", msg)

    def add_local(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "NetCDF files", "", "NetCDF (*.nc *.nc4);;All (*)")
        for p in fs:
            self.local_list.addItem(p)

    def add_local_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Folder with NetCDF files")
        if d:
            for n in sorted(os.listdir(d)):
                if n.lower().endswith((".nc", ".nc4")):
                    self.local_list.addItem(os.path.join(d, n))

    def rm_local(self):
        for it in self.local_list.selectedItems():
            self.local_list.takeItem(self.local_list.row(it))

    def _local_files(self):
        return [self.local_list.item(i).text() for i in range(self.local_list.count())]

    def _update_groups(self, *a):
        g = classify_files(self._local_files())
        lines = []
        for (m, s), d in sorted(g.items()):
            lines.append("%s / %s: %s" % (m, s, ", ".join("%s×%d" % (k, len(v)) for k, v in sorted(d.items()))))
        self.local_info.setPlainText("\n".join(lines))

    # ------------------------------------------------------------ tab 3
    def _out_tab(self):
        w = QWidget()
        f = QFormLayout(w)
        self.out_dir = QLineEdit(os.path.join(os.path.expanduser("~"), "drainmod_climate"))
        b = QPushButton("…")
        b.clicked.connect(lambda: self._browse_dir(self.out_dir))
        h = QHBoxLayout()
        h.addWidget(self.out_dir, 1)
        h.addWidget(b)
        self.leap = QComboBox()
        self.leap.addItem("Insert Feb 29 (0 rain, interpolated temperatures)", "feb29")
        self.leap.addItem("Daymet style: add Dec 31 (0 rain, Dec 30 temperatures)", "dec31")
        self.leap.setToolTip("Only used for models with a no-leap (365-day) calendar.")
        self.pet = QCheckBox("Also write Hargreaves PET files (.PET and .pet.wea)")
        self.pet.setChecked(True)
        self.csv = QCheckBox("Also write full daily CSV (mm, °C)")
        self.csv.setChecked(True)
        self.cache = QCheckBox("Cache server responses (faster re-runs)")
        self.cache.setChecked(True)
        f.addRow("Output folder", self._wrap(h))
        f.addRow("Leap years (no-leap models)", self.leap)
        f.addRow("", self.pet)
        f.addRow("", self.csv)
        f.addRow("", self.cache)
        note = QLabel(
            "Each site / model / scenario gets its own folder <site>/<model>_<scenario>_<years>/ "
            "with ready-to-use DRAINMOD <station>.RAI (hourly rain, 0.01 in, each rain day "
            "spread over hours 17–20), <station>.TEM (daily Tmax/Tmin, °F) and optionally "
            "<station>.PET (daily Hargreaves PET, 0.01 in), identical to DRAINMOD's weather "
            "utility output, plus .wea text files. Model "
            "calendars (no-leap, 360-day) are converted to real dates; ensemble_summary.csv "
            "compares all runs.")
        note.setWordWrap(True)
        f.addRow(note)
        return w

    # ------------------------------------------------------------ run
    def _job(self):
        src = ["nex", "loca", "local", "ncss"][self.source.currentIndex()]
        if src == "loca":
            src = "local"
            if not self._local_files():
                raise ValueError("Download LOCA2 files first (button on the LOCA2 page), "
                                 "or add files under 'Local NetCDF files'.")
        out = self.out_dir.text().strip()
        if not out:
            raise ValueError("Choose an output folder.")
        job = {
            "source": src,
            "source_label": self.source.currentText(),
            "sites": self._sites(),
            "hist_y0": self.h0.value(), "hist_y1": self.h1.value(),
            "fut_y0": self.f0.value(), "fut_y1": self.f1.value(),
            "out_dir": out,
            "pet": self.pet.isChecked(), "csv": self.csv.isChecked(),
            "leap_mode": self.leap.currentData(),
            "cache_dir": os.path.join(out, ".cache") if self.cache.isChecked() else None,
        }
        if job["hist_y0"] > job["hist_y1"] or job["fut_y0"] > job["fut_y1"]:
            raise ValueError("Start year must be <= end year.")
        if src == "nex":
            job["models"] = _checked(self.nex_models)
            job["scenarios"] = [c.text() for c in self.nex_scen if c.isChecked()]
            job["thredds"] = self.thredds.text().strip()
        elif src == "ncss":
            if "{var}" not in self.ncss_tpl.text():
                raise ValueError("The URL template needs at least {var}.")
            job["ncss_template"] = self.ncss_tpl.text().strip()
            job["ncss_vars"] = {k: e.text().strip() or k for k, e in self.nv.items()}
            job["models"] = [s.strip() for s in self.ncss_models.text().split(",") if s.strip()]
            job["scenarios"] = [s.strip() for s in self.ncss_scen.text().split(",") if s.strip()]
        else:
            groups = classify_files(self._local_files())
            bad = [k for k, d in groups.items() if not all(v in d for v in ("pr", "tasmax", "tasmin"))]
            if bad:
                raise ValueError("Missing pr/tasmax/tasmin files for: %s" %
                                 ", ".join("%s/%s" % b for b in bad))
            if not groups:
                raise ValueError("Add NetCDF files first.")
            job["local_groups"] = groups
            job["local_vars"] = {k: e.text().strip() for k, e in self.lv.items() if e.text().strip()}
        if src != "local" and (not job["models"] or not job["scenarios"]):
            raise ValueError("Select at least one model and one scenario.")
        return job

    def run(self):
        try:
            job = self._job()
        except Exception as e:  # noqa
            QMessageBox.warning(self, "DRAINMOD Climate", str(e))
            return
        self._save_settings()
        t = BuildWeatherTask(job)
        t.taskCompleted.connect(lambda: self._done(t))
        t.taskTerminated.connect(lambda: self._fail(t, "Weather file creation"))
        self._start(t)
        self.iface.messageBar().pushInfo(
            "DRAINMOD Climate", "Running in the background — see the task bar and the "
            "'DRAINMOD Climate' log panel.")

    def _start(self, t):
        self.tasks.append(t)
        QgsApplication.taskManager().addTask(t)

    def _done(self, t):
        ok, bad = len(t.results), len(t.errors)
        msg = "%d weather set(s) written to %s" % (ok, t.job["out_dir"])
        if bad:
            msg += "; %d failed (see log)" % bad
            self.iface.messageBar().pushWarning("DRAINMOD Climate", msg)
        else:
            self.iface.messageBar().pushSuccess("DRAINMOD Climate", msg)
        if bad:
            QMessageBox.warning(self, "DRAINMOD Climate", "Some runs failed:\n\n" + "\n".join(t.errors[:20]))

    def _fail(self, t, what):
        err = getattr(t, "exception", None)
        self.iface.messageBar().pushCritical(
            "DRAINMOD Climate", "%s stopped%s" % (what, (": %s" % err) if err else " (cancelled)"))

    # ------------------------------------------------------------ settings
    def _save_settings(self):
        s = QSettings()
        s.setValue(S + "out_dir", self.out_dir.text())
        s.setValue(S + "lat", self.lat.value())
        s.setValue(S + "lon", self.lon.value())
        s.setValue(S + "site", self.site_name.text())
        s.setValue(S + "source", self.source.currentIndex())
        s.setValue(S + "ncss_tpl", self.ncss_tpl.text())
        s.setValue(S + "loca_dir", self.loca_dir.text())
        s.setValue(S + "leap_mode", self.leap.currentData())

    def _load_settings(self):
        s = QSettings()
        self.out_dir.setText(s.value(S + "out_dir", self.out_dir.text()))
        self.lat.setValue(float(s.value(S + "lat", self.lat.value())))
        self.lon.setValue(float(s.value(S + "lon", self.lon.value())))
        self.site_name.setText(s.value(S + "site", self.site_name.text()))
        self.source.setCurrentIndex(int(s.value(S + "source", 0)))
        self.ncss_tpl.setText(s.value(S + "ncss_tpl", ""))
        self.loca_dir.setText(s.value(S + "loca_dir", self.loca_dir.text()))
        i = self.leap.findData(s.value(S + "leap_mode", "feb29"))
        self.leap.setCurrentIndex(max(i, 0))
