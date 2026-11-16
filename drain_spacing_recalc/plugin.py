import os

from qgis.core import QgsApplication, QgsVectorLayer
from qgis.PyQt.QtGui import QIcon

try:
    from qgis.PyQt.QtGui import QAction  # Qt6
except ImportError:
    from qgis.PyQt.QtWidgets import QAction  # Qt5

from .provider import RecalcProvider

TILE_DRAINAGE_MENU = "&Tile Drainage"  # submenu shared by the QGIS_Tools drainage plugins
MENU = "&Drain spacing recalculator"
ALG_ID = "drainspacingrecalc:recalculate"
SOIL_FIELDS = {"ksat_prof", "ka_ftday", "ka_mday"}


class DrainSpacingRecalcPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None

    def initProcessing(self):
        if self.provider is not None:
            return
        self.provider = RecalcProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icon, "Recalculate Drain Spacing…", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToVectorMenu(MENU, self.action)
        self.iface.addPluginToVectorMenu(TILE_DRAINAGE_MENU, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginVectorMenu(MENU, self.action)
            self.iface.removePluginVectorMenu(TILE_DRAINAGE_MENU, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def run(self, _checked=False):
        import processing
        params = {}
        layer = self.iface.activeLayer()
        # Start with the selected layer if it is a soil map from one of the soil plugins.
        if isinstance(layer, QgsVectorLayer) and SOIL_FIELDS & set(layer.fields().names()):
            params["INPUT"] = layer.id()
        processing.execAlgorithmDialog(ALG_ID, params)
