import os

from qgis.core import QgsApplication, QgsProject
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .provider import WaterManagementZonesProvider

TILE_DRAINAGE_MENU = "&Tile Drainage"  # submenu shared by the QGIS_Tools drainage plugins
PLUGIN_MENU = "&Water Management Zones"
ALGORITHM_ID = "wmzones:watermanagementzones"


class WaterManagementZonesPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None
        self.reset_action = None

    def initProcessing(self):
        if self.provider is not None:
            return
        self.provider = WaterManagementZonesProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icon, "Water management zones and structures…",
                              self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(PLUGIN_MENU, self.action)
        self.iface.addPluginToRasterMenu(TILE_DRAINAGE_MENU, self.action)
        self.iface.addPluginToVectorMenu(TILE_DRAINAGE_MENU, self.action)
        self.iface.addToolBarIcon(self.action)
        self.reset_action = QAction("Reset saved settings to defaults", self.iface.mainWindow())
        self.reset_action.triggered.connect(self.reset_settings)
        self.iface.addPluginToMenu(PLUGIN_MENU, self.reset_action)
        # a new or different project starts from the plugin defaults
        QgsProject.instance().cleared.connect(self._project_cleared)

    def _project_cleared(self):
        from .algorithm import reset_saved_settings
        reset_saved_settings()

    def unload(self):
        try:
            QgsProject.instance().cleared.disconnect(self._project_cleared)
        except (TypeError, RuntimeError):
            pass
        if self.reset_action is not None:
            self.iface.removePluginMenu(PLUGIN_MENU, self.reset_action)
            self.reset_action = None
        if self.action is not None:
            self.iface.removePluginMenu(PLUGIN_MENU, self.action)
            self.iface.removePluginRasterMenu(TILE_DRAINAGE_MENU, self.action)
            self.iface.removePluginVectorMenu(TILE_DRAINAGE_MENU, self.action)
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
            "Water management zones", "Settings reset: the tool opens with the plugin "
            "defaults next time (as it does after a new project is started or another "
            "project is loaded).", level=Qgis.Info, duration=5)

    def run(self):
        import processing
        processing.execAlgorithmDialog(ALGORITHM_ID, {})
