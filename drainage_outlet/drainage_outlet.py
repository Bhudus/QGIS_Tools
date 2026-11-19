# -*- coding: utf-8 -*-
import os

from qgis.core import QgsApplication, QgsProject, QgsVectorLayer, QgsWkbTypes
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .provider import DrainageOutletProvider

ALG_ID = "drainageoutlet:drainagepath"
MENU = "&Drainage outlet"


def _is_geom_layer(lyr, kind):
    """kind = 'Polygon' or 'Line'."""
    if not isinstance(lyr, QgsVectorLayer):
        return False
    try:
        return lyr.geometryType() == getattr(QgsWkbTypes, kind + "Geometry")
    except AttributeError:  # QGIS >= 3.30 enum
        from qgis.core import Qgis
        return lyr.geometryType() == getattr(Qgis.GeometryType, kind)


def _is_polygon_layer(lyr):
    return _is_geom_layer(lyr, "Polygon")


class DrainageOutletPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None

    def initProcessing(self):
        self.provider = DrainageOutletProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icon, "Drainage outlet…", self.iface.mainWindow())
        self.action.setToolTip(
            "Trace the natural drainage path from a field boundary or outlet point to the nearest stream "
            "(and, optionally, to a district drain)")
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(MENU, self.action)

    def unload(self):
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu(MENU, self.action)
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)

    def run(self):
        """Open the tool window; Run executes as a background task."""
        params = {}
        ws = self.guess_watershed_layer()
        if ws is not None:
            params["WATERSHED"] = ws.id()
        dd = self.guess_district_drain_layer()
        if dd is not None:
            params["DRAINS"] = dd.id()
        try:
            from .background import open_background_dialog
            if open_background_dialog(ALG_ID, params):
                return
        except Exception as e:  # older/newer QGIS without the expected dialog API
            from qgis.core import Qgis, QgsMessageLog
            QgsMessageLog.logMessage("Background mode unavailable ({}); using the standard "
                                     "Processing window.".format(e), "Drainage outlet", Qgis.Warning)
        import processing
        processing.execAlgorithmDialog(ALG_ID, params)

    @staticmethod
    def guess_watershed_layer():
        """Pre-select a polygon layer whose name looks like a StreamStats watershed."""
        polys = [l for l in QgsProject.instance().mapLayers().values() if _is_polygon_layer(l)]
        for key in ("globalwatershed", "watershed", "basin"):
            for l in polys:
                if key in l.name().lower().replace(" ", ""):
                    return l
        return None

    @staticmethod
    def guess_district_drain_layer():
        """Pre-select a line layer whose name looks like a district / legal drain."""
        lines = [l for l in QgsProject.instance().mapLayers().values() if _is_geom_layer(l, "Line")]
        for key in ("districtdrain", "legaldrain", "countydrain", "drainagedistrict"):
            for l in lines:
                if key in l.name().lower().replace(" ", "").replace("_", ""):
                    return l
        return None
