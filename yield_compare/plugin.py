# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtGui import QIcon

try:  # Qt6 / QGIS 4
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt5 / QGIS 3
    from qgis.PyQt.QtWidgets import QAction

TILE_DRAINAGE_MENU = "&Tile Drainage"  # submenu shared by the QGIS_Tools drainage plugins


class YieldComparePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dlg = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icon, "Compare Field Yields…", self.iface.mainWindow())
        self.action.setToolTip("Compare yield statistics of two fields")
        self.action.triggered.connect(self.run)
        self.iface.addPluginToVectorMenu(TILE_DRAINAGE_MENU, self.action)
        self.iface.addVectorToolBarIcon(self.action)

    def unload(self):
        self.iface.removePluginVectorMenu(TILE_DRAINAGE_MENU, self.action)
        self.iface.removeVectorToolBarIcon(self.action)
        if self.dlg is not None:
            self.dlg.close()
            self.dlg.deleteLater()
            self.dlg = None

    def run(self):
        if self.dlg is None:
            from .dialog import YieldCompareDialog
            self.dlg = YieldCompareDialog(self.iface, self.iface.mainWindow())
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()
