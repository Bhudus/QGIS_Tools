# -*- coding: utf-8 -*-
import os

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon

try:
    from qgis.PyQt.QtGui import QAction  # Qt6
except ImportError:
    from qgis.PyQt.QtWidgets import QAction  # Qt5

from .random_point_selection_provider import RandomPointSelectionProvider

TILE_DRAINAGE_MENU = "&Tile Drainage"  # submenu shared by the QGIS_Tools drainage plugins
ALG_ID = "randompointselection:randompointselection"


class RandomPointSelectionPlugin(object):
    """Registers the Processing provider and a Vector → Tile Drainage menu item."""

    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None

    def initProcessing(self):  # noqa: N802
        if self.provider is not None:
            return
        self.provider = RandomPointSelectionProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "Random Point Selection…", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToVectorMenu(TILE_DRAINAGE_MENU, self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginVectorMenu(TILE_DRAINAGE_MENU, self.action)
            self.action = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def run(self):
        import processing
        processing.execAlgorithmDialog(ALG_ID, {})
