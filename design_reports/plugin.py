import os

from qgis.core import QgsApplication, QgsProject
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .provider import DesignReportsProvider

TILE_DRAINAGE_MENU = "&Tile Drainage"  # submenu shared by the QGIS_Tools drainage plugins
PLUGIN_MENU = "&Design Reports"
TOOLS = (("designreports:drainagereport", "Drainage system design report…", "icon.png"),
         ("designreports:subirrigationreport", "Subirrigation design report…",
          "icon_subirrigation.png"))


class DesignReportsPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.actions = []
        self.reset_action = None

    def initProcessing(self):
        if self.provider is not None:
            return
        self.provider = DesignReportsProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        here = os.path.dirname(__file__)
        for alg_id, label, icon_file in TOOLS:
            a = QAction(QIcon(os.path.join(here, icon_file)), label, self.iface.mainWindow())
            a.triggered.connect(lambda checked=False, i=alg_id: self.run(i))
            self.iface.addPluginToMenu(PLUGIN_MENU, a)
            self.iface.addPluginToRasterMenu(TILE_DRAINAGE_MENU, a)
            self.iface.addPluginToVectorMenu(TILE_DRAINAGE_MENU, a)
            self.iface.addToolBarIcon(a)
            self.actions.append(a)
        self.reset_action = QAction("Reset saved settings to defaults", self.iface.mainWindow())
        self.reset_action.triggered.connect(self.reset_settings)
        self.iface.addPluginToMenu(PLUGIN_MENU, self.reset_action)
        # a new or different project starts from the plugin defaults
        QgsProject.instance().cleared.connect(self._project_cleared)

    def _project_cleared(self):
        from .common import reset_saved_settings
        reset_saved_settings()

    def unload(self):
        try:
            QgsProject.instance().cleared.disconnect(self._project_cleared)
        except (TypeError, RuntimeError):
            pass
        if self.reset_action is not None:
            self.iface.removePluginMenu(PLUGIN_MENU, self.reset_action)
            self.reset_action = None
        for a in self.actions:
            self.iface.removePluginMenu(PLUGIN_MENU, a)
            self.iface.removePluginRasterMenu(TILE_DRAINAGE_MENU, a)
            self.iface.removePluginVectorMenu(TILE_DRAINAGE_MENU, a)
            self.iface.removeToolBarIcon(a)
        self.actions = []
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def reset_settings(self):
        from qgis.core import Qgis
        from .common import reset_saved_settings
        reset_saved_settings()
        self.iface.messageBar().pushMessage(
            "Design Reports", "Settings reset: the tools open with the plugin defaults next "
            "time (as they do after a new project is started or another project is loaded).",
            level=Qgis.Info, duration=5)

    def run(self, alg_id):
        import processing
        processing.execAlgorithmDialog(alg_id, {})
