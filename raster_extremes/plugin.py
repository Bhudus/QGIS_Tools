import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsRasterLayer

from .dialog import RasterExtremesDialog


class RasterExtremesPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "Raster Min / Max Cells…", self.iface.mainWindow())
        self.action.triggered.connect(self.show_dialog)
        self.iface.addRasterToolBarIcon(self.action)
        self.iface.addPluginToRasterMenu("Raster Extremes", self.action)

    def unload(self):
        self.iface.removeRasterToolBarIcon(self.action)
        self.iface.removePluginRasterMenu("Raster Extremes", self.action)
        if self.dialog:
            self.dialog.close()
            self.dialog = None

    def show_dialog(self):
        if self.dialog is None:
            self.dialog = RasterExtremesDialog(self.iface, self.iface.mainWindow())
        active = self.iface.activeLayer()
        if isinstance(active, QgsRasterLayer):
            self.dialog.layer_cb.setLayer(active)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
