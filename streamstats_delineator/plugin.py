# -*- coding: utf-8 -*-
import os

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsProject)
from qgis.gui import QgsVertexMarker
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import QAction

from . import settings_dialog
from .compat import msg_level, scoped
from .layers import add_result
from .map_tool import PourPointTool
from .settings_dialog import AUTO, SettingsDialog
from .task import DelineationTask

MENU = "&StreamStats Delineator"
MAX_CONCURRENT = 3  # StreamStats asks clients to keep it to <= 4 at once


class StreamStatsDelineatorPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.tool = None
        self.actions = []
        self.running = {}  # task -> vertex marker (also keeps tasks from being GC'd)

    # ------------------------------------------------------------------ setup
    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))

        self.delineate_action = QAction(icon, "Delineate watershed (StreamStats)",
                                        self.iface.mainWindow())
        self.delineate_action.setCheckable(True)
        self.delineate_action.setStatusTip(
            "Click a point near a stream to delineate its upstream watershed "
            "with USGS StreamStats")
        self.delineate_action.triggered.connect(self.activate_tool)

        settings_action = QAction("Settings…", self.iface.mainWindow())
        settings_action.triggered.connect(self.show_settings)

        self.iface.addToolBarIcon(self.delineate_action)
        for a in (self.delineate_action, settings_action):
            self.iface.addPluginToMenu(MENU, a)
            self.actions.append(a)

        self.tool = PourPointTool(self.canvas)
        self.tool.setAction(self.delineate_action)
        self.tool.picked.connect(self.on_pick)

    def unload(self):
        for task in list(self.running):
            task.cancel()
        self._clear_markers()
        if self.canvas.mapTool() is self.tool:
            self.canvas.unsetMapTool(self.tool)
        self.iface.removeToolBarIcon(self.delineate_action)
        for a in self.actions:
            self.iface.removePluginMenu(MENU, a)
        self.tool = None

    # ------------------------------------------------------------------ UI
    def activate_tool(self, checked=True):
        if checked:
            self.canvas.setMapTool(self.tool)
            self.iface.messageBar().pushMessage(
                "StreamStats", "Click on or near a stream to delineate its watershed.",
                msg_level("Info"), 4)
        elif self.canvas.mapTool() is self.tool:
            self.canvas.unsetMapTool(self.tool)

    def show_settings(self):
        SettingsDialog(self.iface.mainWindow()).exec()

    def _bar(self, text, level, secs=6):
        self.iface.messageBar().pushMessage("StreamStats", text, msg_level(level), secs)

    # ------------------------------------------------------------------ work
    def on_pick(self, lat, lon, canvas_point):
        if len(self.running) >= MAX_CONCURRENT:
            self._bar("Already running {} delineations; please wait for one to "
                      "finish.".format(MAX_CONCURRENT), "Warning")
            return

        cfg = settings_dialog.load()
        region = None if cfg["region"] == AUTO else cfg["region"]

        marker = QgsVertexMarker(self.canvas)
        marker.setCenter(canvas_point)
        marker.setIconType(scoped(QgsVertexMarker, "IconType", "ICON_CIRCLE"))
        marker.setColor(QColor(227, 26, 28))
        marker.setIconSize(14)
        marker.setPenWidth(3)

        task = DelineationTask(lat, lon, region=region, snap=cfg["snap"],
                               timeout=float(cfg["timeout_s"]),
                               streams=cfg["streams"])
        task.done.connect(lambda res, t=task: self._on_done(t, res, cfg))
        task.failed.connect(lambda msg, t=task: self._on_failed(t, msg))
        self.running[task] = marker
        QgsApplication.taskManager().addTask(task)
        self._bar("Delineating at {:.5f}, {:.5f}… (typically 10–30 s)".format(lat, lon),
                  "Info", 3)

    def _finish(self, task):
        marker = self.running.pop(task, None)
        if marker is not None:
            self.canvas.scene().removeItem(marker)

    def _clear_markers(self):
        for marker in self.running.values():
            self.canvas.scene().removeItem(marker)
        self.running.clear()

    def _on_failed(self, task, message):
        self._finish(task)
        self._bar(message, "Critical", 10)

    def _on_done(self, task, result, cfg):
        self._finish(task)
        try:
            ws_layer, feat = add_result(result, add_streams=cfg["streams"])
        except Exception as e:
            self._bar("Watershed received but could not be added: {}".format(e),
                      "Critical", 10)
            return

        msg = "Watershed #{} ({}): {:,.3f} km² ({:,.3f} mi²).".format(
            feat["ws_id"], result["region"], feat["area_km2"], feat["area_mi2"])
        level = "Success"
        if cfg["streams"]:
            if result.get("streams_error"):
                msg += " Streams unavailable: {}".format(result["streams_error"])
                level = "Warning"
            elif feat["n_streams"]:
                msg += " {:,} stream segments, {:,.1f} km.".format(
                    feat["n_streams"], feat["stream_km"])
            else:
                msg += " No NHDPlus streams inside this watershed (likely a small headwater basin)."
        if cfg["snap"] and result["snap_distance_m"] > cfg["warn_snap_m"]:
            msg += (" Outlet snapped {:,.0f} m from your click — check it is on the "
                    "intended stream.").format(result["snap_distance_m"])
            level = "Warning"
        self._bar(msg, level, 10)

        if cfg["zoom"]:
            xform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                           self.canvas.mapSettings().destinationCrs(),
                                           QgsProject.instance())
            ext = xform.transformBoundingBox(feat.geometry().boundingBox())
            ext.scale(1.15)
            self.canvas.setExtent(ext)
            self.canvas.refresh()
