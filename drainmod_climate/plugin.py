import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction


class DrainmodClimatePlugin(object):
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dlg = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icon, "DRAINMOD Climate-Scenario Weather Files…",
                              self.iface.mainWindow())
        self.action.triggered.connect(self.show)
        self.iface.addPluginToMenu("&DRAINMOD Climate", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        self.iface.removePluginMenu("&DRAINMOD Climate", self.action)
        self.iface.removeToolBarIcon(self.action)
        if self.dlg:
            self.dlg.close()

    def show(self):
        if self.dlg is None:
            from .dialog import DrainmodClimateDialog
            self.dlg = DrainmodClimateDialog(self.iface)
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()
