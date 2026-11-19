import os

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .provider import IlliniSubirrigationLayoutProvider

TILE_DRAINAGE_MENU = "&Tile Drainage"  # submenu shared by the QGIS_Tools drainage plugins
PLUGIN_MENU = "&Illini Subirrigation Layout"
ALGORITHM_ID = "illinisubirrigation:illinisubirrigationlayout"


class IlliniSubirrigationLayoutPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None
        self.reset_action = None

    def initProcessing(self):
        if self.provider is not None:
            return
        self.provider = IlliniSubirrigationLayoutProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icon, "Illini subirrigation layout…", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(PLUGIN_MENU, self.action)
        self.iface.addPluginToRasterMenu(TILE_DRAINAGE_MENU, self.action)
        self.iface.addToolBarIcon(self.action)
        self.reset_action = QAction("Reset saved settings to defaults", self.iface.mainWindow())
        self.reset_action.triggered.connect(self.reset_settings)
        self.iface.addPluginToMenu(PLUGIN_MENU, self.reset_action)

    def unload(self):
        if self.reset_action is not None:
            self.iface.removePluginMenu(PLUGIN_MENU, self.reset_action)
            self.reset_action = None
        if self.action is not None:
            self.iface.removePluginMenu(PLUGIN_MENU, self.action)
            self.iface.removePluginRasterMenu(TILE_DRAINAGE_MENU, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def reset_settings(self):
        from qgis.core import Qgis
        from .algorithm import reset_saved_settings
        reset_saved_settings()
        self.iface.messageBar().pushMessage(
            "Illini subirrigation layout",
            "Settings reset: the tool opens with the plugin defaults next time.",
            level=Qgis.Info, duration=5)

    def run(self):
        import processing
        processing.execAlgorithmDialog(ALGORITHM_ID, {})
