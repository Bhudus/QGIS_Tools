import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

MENU = "&Tile Profile"
TILE_DRAINAGE_MENU = "&Tile Drainage"  # submenu shared by the QGIS_Tools drainage plugins


class TileProfilePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dock = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icon, "Tile Profile", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip("Show the profile of the selected lateral or main")
        self.action.toggled.connect(self._toggle)
        self.iface.addPluginToMenu(MENU, self.action)
        self.iface.addPluginToVectorMenu(TILE_DRAINAGE_MENU, self.action)
        self.iface.addToolBarIcon(self.action)

    def _toggle(self, on):
        if on:
            if self.dock is None:
                from .dock import TileProfileDock
                self.dock = TileProfileDock(self.iface, self.iface.mainWindow())
                self.iface.addDockWidget(Qt.BottomDockWidgetArea, self.dock)
                self.dock.visibilityChanged.connect(self._dock_visibility)
            self.dock.show()
            self.dock.raise_()
            self.dock.refresh()
        elif self.dock is not None:
            self.dock.hide()

    def _dock_visibility(self, visible):
        if self.action is not None and self.action.isChecked() != visible:
            self.action.blockSignals(True)
            self.action.setChecked(visible)
            self.action.blockSignals(False)

    def unload(self):
        if self.dock is not None:
            self.dock.cleanup()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removePluginMenu(MENU, self.action)
            self.iface.removePluginVectorMenu(TILE_DRAINAGE_MENU, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
