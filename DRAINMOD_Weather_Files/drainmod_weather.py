"""DRAINMOD_Weather_Files - QGIS plugin.

Click a point -> download Daymet daily prcp, tmax, tmin from the ORNL
Single Pixel API -> write .wea text files and DRAINMOD .RAI/.TEM files.
"""
import calendar
import csv
import json
import os
from datetime import date, timedelta

from qgis.PyQt.QtCore import QDate, QUrl, QUrlQuery
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtWidgets import (
    QAction, QCheckBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget,
)
from qgis.core import (
    Qgis, QgsApplication, QgsBlockingNetworkRequest, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsProject, QgsSettings, QgsTask, QgsVectorLayer,
)
from qgis.gui import QgsMapToolEmitPoint

from . import drainmod

API_URL = "https://daymet.ornl.gov/single-pixel/api/data"
FIRST_YEAR = 1980
SETTINGS_KEY = "drainmod_weather_files/"


# --------------------------------------------------------------------------
# Dialog: dates + output folder
# --------------------------------------------------------------------------
class DaymetDialog(QDialog):
    def __init__(self, lat, lon, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DRAINMOD weather files (Daymet)")
        s = QgsSettings()
        form = QFormLayout(self)

        form.addRow("Point (WGS84):", QLabel(f"lat {lat:.5f}, lon {lon:.5f}"))

        self.station = QLineEdit(drainmod.default_station_id(lat, lon))
        self.station.setMaxLength(6)
        self.station.setToolTip("Up to 6 characters; used in the DRAINMOD files and as the file name.")
        form.addRow("Station ID:", self.station)

        self.start = QDateEdit(calendarPopup=True)
        self.start.setDisplayFormat("yyyy-MM-dd")
        self.start.setMinimumDate(QDate(FIRST_YEAR, 1, 1))
        self.start.setDate(QDate.fromString(
            s.value(SETTINGS_KEY + "start", "2000-01-01"), "yyyy-MM-dd"))
        form.addRow("Start date:", self.start)

        self.end = QDateEdit(calendarPopup=True)
        self.end.setDisplayFormat("yyyy-MM-dd")
        self.end.setMinimumDate(QDate(FIRST_YEAR, 1, 1))
        last_year = date.today().year - 1
        self.end.setDate(QDate.fromString(
            s.value(SETTINGS_KEY + "end", f"{last_year}-12-31"), "yyyy-MM-dd"))
        form.addRow("End date:", self.end)

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        self.folder = QLineEdit(s.value(SETTINGS_KEY + "folder", os.path.expanduser("~")))
        browse = QPushButton("…")
        browse.clicked.connect(self._browse)
        h.addWidget(self.folder)
        h.addWidget(browse)
        form.addRow("Output folder:", row)

        self.add_to_project = QCheckBox("Add .wea tables to the project")
        self.add_to_project.setChecked(s.value(SETTINGS_KEY + "add", True, type=bool))
        form.addRow(self.add_to_project)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.folder.text())
        if d:
            self.folder.setText(d)

    def _accept(self):
        sid = self.station.text().strip()
        if not sid or any(c in sid for c in '\\/:*?"<>|'):
            self.station.setFocus()
            return
        if self.start.date() > self.end.date():
            self.start.setFocus()
            return
        if not os.path.isdir(self.folder.text()):
            self.folder.setFocus()
            return
        s = QgsSettings()
        s.setValue(SETTINGS_KEY + "start", self.start.date().toString("yyyy-MM-dd"))
        s.setValue(SETTINGS_KEY + "end", self.end.date().toString("yyyy-MM-dd"))
        s.setValue(SETTINGS_KEY + "folder", self.folder.text())
        s.setValue(SETTINGS_KEY + "add", self.add_to_project.isChecked())
        self.accept()


# --------------------------------------------------------------------------
# Background task: download + write CSVs
# --------------------------------------------------------------------------
def _find_key(data, prefix):
    """Daymet JSON keys look like 'prcp (mm/day)'; match on the variable name."""
    for k in data:
        if k.split(" ")[0] == prefix:
            return k
    raise KeyError(f"'{prefix}' not found in Daymet response (keys: {list(data)})")


def _c1(v):
    """Format to one decimal, avoiding '-0.0'."""
    out = f"{float(v):.1f}"
    return "0.0" if out == "-0.0" else out


class DaymetTask(QgsTask):
    def __init__(self, lat, lon, start, end, folder, station):
        super().__init__(f"Daymet download ({lat:.4f}, {lon:.4f})", QgsTask.Flag.CanCancel)
        self.lat, self.lon = lat, lon
        self.start, self.end = start, end
        self.folder = folder
        self.station = station
        self.paths = []
        self.prcp_path = self.temp_path = None
        self.error = None
        self.filled = 0

    def run(self):
        try:
            url = QUrl(API_URL)
            q = QUrlQuery()
            q.addQueryItem("lat", f"{self.lat:.6f}")
            q.addQueryItem("lon", f"{self.lon:.6f}")
            q.addQueryItem("vars", "prcp,tmax,tmin")
            q.addQueryItem("start", self.start)
            q.addQueryItem("end", self.end)
            q.addQueryItem("format", "json")
            url.setQuery(q)

            # Uses QGIS network settings (proxy, SSL) automatically.
            req = QgsBlockingNetworkRequest()
            err = req.get(QNetworkRequest(url), True)
            reply = req.reply()
            body = bytes(reply.content()).decode("utf-8", errors="replace")
            if err != QgsBlockingNetworkRequest.ErrorCode.NoError:
                msg = body.strip() or req.errorMessage()
                raise RuntimeError(f"Daymet request failed: {msg[:500]}")
            if self.isCanceled():
                return False
            self.setProgress(60)

            payload = json.loads(body)
            data = payload["data"]
            years = data["year"]
            ydays = data["yday"]
            prcp = data[_find_key(data, "prcp")]
            tmax = data[_find_key(data, "tmax")]
            tmin = data[_find_key(data, "tmin")]
            if not years:
                raise RuntimeError("Daymet returned no records for this point/period.")

            # Daymet uses a 365-day year: in leap years Dec 31 is dropped, so yday
            # 365 is Dec 30. Add Dec 31 back as yday 366 with zero precipitation
            # and temperatures copied from Dec 30, when the requested period
            # includes Dec 31.
            years, ydays = [int(y) for y in years], [int(j) for j in ydays]
            prcp, tmax, tmin = list(prcp), list(tmax), list(tmin)
            end_date = date.fromisoformat(self.end)
            self.filled = 0
            i = 0
            while i < len(years):
                y = years[i]
                is_leap = calendar.isleap(y)
                last_of_year = i + 1 == len(years) or years[i + 1] != y
                if (is_leap and ydays[i] == 365 and last_of_year
                        and end_date >= date(y, 12, 31)):
                    years.insert(i + 1, y)
                    ydays.insert(i + 1, 366)
                    prcp.insert(i + 1, 0.0)
                    tmax.insert(i + 1, tmax[i])
                    tmin.insert(i + 1, tmin[i])
                    self.filled += 1
                    i += 1
                i += 1

            # Round once; the .wea files and the DRAINMOD files use the same values.
            rain = [(y, j, round(float(p), 2)) for y, j, p in zip(years, ydays, prcp)]
            temp = [(y, j, round(float(hi), 1), round(float(lo), 1))
                    for y, j, hi, lo in zip(years, ydays, tmax, tmin)]

            sid = self.station
            rai_wea = os.path.join(self.folder, f"{sid}.rai.wea")
            tem_wea = os.path.join(self.folder, f"{sid}.tem.wea")
            rai_out = os.path.join(self.folder, f"{sid}.RAI")
            tem_out = os.path.join(self.folder, f"{sid}.TEM")

            # .wea: no header; year,yday,prcp_mm and year,yday,tmax_c,tmin_c
            with open(rai_wea, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                for y, j, p in rain:
                    w.writerow([y, j, f"{p:.2f}"])
            with open(tem_wea, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                for y, j, hi, lo in temp:
                    w.writerow([y, j, _c1(hi), _c1(lo)])
            self.setProgress(80)

            # DRAINMOD weather files
            drainmod.write_lines(rai_out, drainmod.rai_lines(rain, sid))
            drainmod.write_lines(tem_out, drainmod.tem_lines(temp, sid))
            self.prcp_path, self.temp_path = rai_wea, tem_wea
            self.paths = [rai_wea, tem_wea, rai_out, tem_out]
            self.setProgress(100)
            return True
        except Exception as e:  # reported in finished()
            self.error = str(e)
            return False


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------
class DrainmodWeatherPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.tool = None
        self.tasks = []  # keep references so tasks aren't garbage-collected

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "DRAINMOD weather files from Daymet", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.triggered.connect(self.activate)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToWebMenu("DRAINMOD Weather Files", self.action)

        self.tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.tool.setAction(self.action)
        self.tool.canvasClicked.connect(self.on_click)

    def unload(self):
        canvas = self.iface.mapCanvas()
        if canvas.mapTool() is self.tool:
            canvas.unsetMapTool(self.tool)
        self.iface.removeToolBarIcon(self.action)
        self.iface.removePluginWebMenu("DRAINMOD Weather Files", self.action)

    def activate(self):
        self.iface.mapCanvas().setMapTool(self.tool)
        self.iface.messageBar().pushMessage(
            "Daymet", "Click a point on the map (North America only).",
            level=Qgis.MessageLevel.Info, duration=4)

    def on_click(self, point, _button):
        canvas = self.iface.mapCanvas()
        src = canvas.mapSettings().destinationCrs()
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        xform = QgsCoordinateTransform(src, wgs84, QgsProject.instance())
        p = xform.transform(point)
        lat, lon = p.y(), p.x()

        dlg = DaymetDialog(lat, lon, self.iface.mainWindow())
        if not dlg.exec():
            return

        task = DaymetTask(lat, lon,
                          dlg.start.date().toString("yyyy-MM-dd"),
                          dlg.end.date().toString("yyyy-MM-dd"),
                          dlg.folder.text(),
                          dlg.station.text().strip())
        add = dlg.add_to_project.isChecked()
        task.taskCompleted.connect(lambda t=task, a=add: self._done(t, a))
        task.taskTerminated.connect(lambda t=task: self._failed(t))
        self.tasks.append(task)
        QgsApplication.taskManager().addTask(task)

    def _done(self, task, add_to_project):
        if add_to_project:
            for path in (task.prcp_path, task.temp_path):
                uri = QUrl.fromLocalFile(path).toString() + "?delimiter=,&useHeader=no&detectTypes=yes"
                lyr = QgsVectorLayer(uri, os.path.splitext(os.path.basename(path))[0],
                                     "delimitedtext")
                if lyr.isValid():
                    QgsProject.instance().addMapLayer(lyr)
        self.iface.messageBar().pushMessage(
            "Daymet", f"Saved in {task.folder}: "
            + ", ".join(os.path.basename(p) for p in task.paths)
            + (f" ({task.filled} leap-year Dec 31 day(s) filled)" if task.filled else ""),
            level=Qgis.MessageLevel.Success, duration=8)
        self._forget(task)

    def _failed(self, task):
        msg = task.error or "Download cancelled."
        self.iface.messageBar().pushMessage(
            "Daymet", msg, level=Qgis.MessageLevel.Critical, duration=10)
        self._forget(task)

    def _forget(self, task):
        if task in self.tasks:
            self.tasks.remove(task)
