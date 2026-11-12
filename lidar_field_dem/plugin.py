# -*- coding: utf-8 -*-
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication

from .provider import LidarFieldDemProvider, ICON

TILE_DRAINAGE_MENU = "&Tile Drainage"  # submenu shared by the QGIS_Tools drainage plugins

ALG_ID = "lidarfielddem:lidarfielddem"
MENU = "&LiDAR Field DEM"


class LidarFieldDemPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.actions = []

    def initProcessing(self):  # noqa: N802 (QGIS API name)
        if self.provider is not None:
            return
        self.provider = LidarFieldDemProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()
        win = self.iface.mainWindow()

        a_find = QAction(QIcon(ICON), "Find LiDAR collections and build DEM…", win)
        a_find.setToolTip("Show the LiDAR collection years available for a field, "
                          "then build a bare-earth DEM")
        a_find.triggered.connect(self.open_collections)
        self.iface.addToolBarIcon(a_find)
        self.iface.addPluginToRasterMenu(MENU, a_find)
        self.iface.addPluginToRasterMenu(TILE_DRAINAGE_MENU, a_find)

        a_dem = QAction(QIcon(ICON), "Bare-earth DEM for a field (Processing dialog)…", win)
        a_dem.triggered.connect(self.run_dem)
        self.iface.addPluginToRasterMenu(MENU, a_dem)
        self.iface.addPluginToRasterMenu(TILE_DRAINAGE_MENU, a_dem)

        self.actions = [a_find, a_dem]
        self._toolbar_action = a_find

    def unload(self):
        dlg = getattr(self, "_dlg", None)
        if dlg is not None:
            dlg.cancel_jobs()
            dlg.close()
            dlg.deleteLater()
            self._dlg = None
        for a in self.actions:
            self.iface.removePluginRasterMenu(MENU, a)
            self.iface.removePluginRasterMenu(TILE_DRAINAGE_MENU, a)
        if getattr(self, "_toolbar_action", None) is not None:
            self.iface.removeToolBarIcon(self._toolbar_action)
            self._toolbar_action = None
        self.actions = []
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def open_collections(self):
        from .dialog import CollectionsDialog
        dlg = getattr(self, "_dlg", None)
        if dlg is None:
            dlg = CollectionsDialog(self.iface, ALG_ID, ICON)
            self._dlg = dlg
        dlg.show()          # non-modal, so the map stays usable for tracing
        dlg.raise_()
        dlg.activateWindow()

    def run_dem(self):
        import processing
        processing.execAlgorithmDialog(ALG_ID, {})
