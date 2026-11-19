# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication, QgsProject

from .provider import BoundaryContoursProvider
from . import settings_store

ALG_ID = "boundarycontours:contours"


class BoundaryContoursPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None

    def initProcessing(self):
        self.provider = BoundaryContoursProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "Contours (boundary + index)...", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToRasterMenu("&Tile Drainage", self.action)
        self.iface.addRasterToolBarIcon(self.action)

        # Per-project memory of last-run settings
        prj = QgsProject.instance()
        prj.cleared.connect(settings_store.forget_unsaved)
        prj.projectSaved.connect(settings_store.adopt_unsaved)

    def unload(self):
        prj = QgsProject.instance()
        for sig, slot in ((prj.cleared, settings_store.forget_unsaved),
                          (prj.projectSaved, settings_store.adopt_unsaved)):
            try:
                sig.disconnect(slot)
            except Exception:
                pass
        if self.action:
            self.iface.removePluginRasterMenu("&Tile Drainage", self.action)
            self.iface.removeRasterToolBarIcon(self.action)
            self.action = None
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def run(self):
        import processing
        processing.execAlgorithmDialog(ALG_ID, settings_store.dialog_parameters())
