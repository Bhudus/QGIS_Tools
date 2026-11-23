import os

from qgis.core import QgsApplication, QgsProject
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .provider import TileOutletSubmergenceProvider

TILE_DRAINAGE_MENU = "&Tile Drainage"  # submenu shared by the QGIS_Tools drainage plugins
PLUGIN_MENU = "&Tile Outlet Submergence"
ALGORITHM_ID = "tileoutletsubmergence:tileoutletsubmergence"


class TileOutletSubmergencePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None
        self.reset_action = None
        self.dock = None
        self.dock_action = None

    def initProcessing(self):
        if self.provider is not None:
            return
        self.provider = TileOutletSubmergenceProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icon, "Tile outlet submergence…",
                              self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(PLUGIN_MENU, self.action)
        self.iface.addPluginToRasterMenu(TILE_DRAINAGE_MENU, self.action)
        self.iface.addPluginToVectorMenu(TILE_DRAINAGE_MENU, self.action)
        self.iface.addToolBarIcon(self.action)
        # ditch level explorer: steps the map through the ditch levels of a run
        from qgis.PyQt.QtCore import Qt
        from .algorithm import RUN_LISTENERS
        from .panel import ExplorerDock
        self.dock = ExplorerDock(self.iface)
        area = getattr(getattr(Qt, "DockWidgetArea", Qt), "RightDockWidgetArea")
        self.iface.addDockWidget(area, self.dock)
        self.dock.hide()
        RUN_LISTENERS.append(self.dock.on_new_run)
        self.dock_action = self.dock.toggleViewAction()
        self.dock_action.setText("Ditch level explorer")
        self.dock_action.setIcon(icon)
        self.iface.addPluginToMenu(PLUGIN_MENU, self.dock_action)
        self.iface.addPluginToRasterMenu(TILE_DRAINAGE_MENU, self.dock_action)
        self.iface.addPluginToVectorMenu(TILE_DRAINAGE_MENU, self.dock_action)
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
        if self.dock is not None:
            from .algorithm import RUN_LISTENERS
            if self.dock.on_new_run in RUN_LISTENERS:
                RUN_LISTENERS.remove(self.dock.on_new_run)
            self.iface.removePluginMenu(PLUGIN_MENU, self.dock_action)
            self.iface.removePluginRasterMenu(TILE_DRAINAGE_MENU, self.dock_action)
            self.iface.removePluginVectorMenu(TILE_DRAINAGE_MENU, self.dock_action)
            self.dock.cleanup()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
            self.dock_action = None
        if self.reset_action is not None:
            self.iface.removePluginMenu(PLUGIN_MENU, self.reset_action)
            self.reset_action = None
        self.dock = None
        self.dock_action = None
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
            "Tile outlet submergence", "Settings reset: the tool opens with the plugin "
            "defaults next time (as it does after a new project is started or another "
            "project is loaded).", level=Qgis.Info, duration=5)

    def run(self):
        import processing
        processing.execAlgorithmDialog(ALGORITHM_ID, {})
