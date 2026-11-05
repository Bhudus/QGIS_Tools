"""Plugin entry: registers the Processing provider and adds a menu/toolbar action."""
import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication

from .provider import LayoutFromTemplateProvider

ALG_ID = "layoutfromtemplate:layoutfromtemplate"


class LayoutFromTemplatePlugin:

    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None

    def initProcessing(self):
        self.provider = LayoutFromTemplateProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()

        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "Create Print Layout from Template…",
                              self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Layout from Template", self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginMenu("&Layout from Template", self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def run(self):
        import processing
        processing.execAlgorithmDialog(ALG_ID)
