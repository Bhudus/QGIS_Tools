# -*- coding: utf-8 -*-
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication

from .provider import DepressionsLaylinesProvider, ICON

TILE_DRAINAGE_MENU = "&Tile Drainage"  # submenu shared by the QGIS_Tools drainage plugins

ALG_ID = "depressionslaylines:depressionslaylines"
MENU = "&Depressions and Laylines"


class DepressionsLaylinesPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None

    def initProcessing(self):  # noqa: N802 (QGIS API name)
        if self.provider is not None:
            return
        self.provider = DepressionsLaylinesProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()
        self.action = QAction(QIcon(ICON), "Depressions and laylines for a field…",
                              self.iface.mainWindow())
        self.action.setToolTip("Depressions deeper than a threshold (filled - unfilled DEM) "
                               "and the laylines (flow channels) within the field")
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToRasterMenu(MENU, self.action)
        self.iface.addPluginToRasterMenu(TILE_DRAINAGE_MENU, self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginRasterMenu(MENU, self.action)
            self.iface.removePluginRasterMenu(TILE_DRAINAGE_MENU, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def run(self):
        import processing
        processing.execAlgorithmDialog(ALG_ID, {})
