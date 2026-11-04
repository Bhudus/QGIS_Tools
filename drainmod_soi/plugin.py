import json
import os

from qgis.core import (QgsBlockingNetworkRequest, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsProject)
from qgis.gui import QgsMapToolEmitPoint, QgsVertexMarker
from qgis.PyQt.QtCore import QByteArray, QUrl
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtWidgets import QAction

from .core import http
from .compat import qenum


def qgis_post_json(url, payload):
    """POST JSON through QGIS's network stack (honours QGIS proxy/SSL)."""
    req = QNetworkRequest(QUrl(url))
    req.setHeader(qenum(QNetworkRequest, "KnownHeaders", "ContentTypeHeader"),
                  "application/json")
    br = QgsBlockingNetworkRequest()
    err = br.post(req, QByteArray(json.dumps(payload).encode("utf-8")))
    body = bytes(br.reply().content()).decode("utf-8", "replace")
    if err != qenum(QgsBlockingNetworkRequest, "ErrorCode", "NoError"):
        raise RuntimeError("%s\n%s" % (br.errorMessage(), body[:500]))
    return json.loads(body) if body.strip() else {}


class DrainmodSoiPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.action = None
        self.tool = None
        self.dialog = None
        self.marker = None

    # ---- QGIS plugin interface -------------------------------------------
    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "DRAINMOD soil file from SSURGO",
                              self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.triggered.connect(self.activate)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&DRAINMOD", self.action)

        self.tool = QgsMapToolEmitPoint(self.canvas)
        self.tool.setAction(self.action)
        self.tool.canvasClicked.connect(self.on_click)
        http.set_post_json(qgis_post_json)

    def unload(self):
        if self.canvas.mapTool() is self.tool:
            self.canvas.unsetMapTool(self.tool)
        self._clear_marker()
        self.iface.removeToolBarIcon(self.action)
        self.iface.removePluginMenu("&DRAINMOD", self.action)
        if self.dialog:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None

    # ---- behaviour ---------------------------------------------------------
    def activate(self):
        self.canvas.setMapTool(self.tool)
        self._ensure_dialog()
        self.iface.messageBar().pushInfo(
            "DRAINMOD", "Click a location on the map to fetch SSURGO soil data.")

    def _ensure_dialog(self):
        if self.dialog is None:
            from .dialog import SoiDialog
            self.dialog = SoiDialog(self.iface.mainWindow())
            self.dialog.finished.connect(self._clear_marker)
        self.dialog.show()
        self.dialog.raise_()

    def on_click(self, point, button):
        src = self.canvas.mapSettings().destinationCrs()
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        xform = QgsCoordinateTransform(src, wgs84, QgsProject.instance())
        p = xform.transform(point)

        self._clear_marker()
        self.marker = QgsVertexMarker(self.canvas)
        self.marker.setCenter(point)
        self.marker.setColor(QColor(220, 30, 30))
        self.marker.setIconSize(14)
        self.marker.setPenWidth(3)

        self._ensure_dialog()
        self.dialog.set_location(p.x(), p.y())

    def _clear_marker(self, *args):
        if self.marker is not None:
            self.canvas.scene().removeItem(self.marker)
            self.marker = None
