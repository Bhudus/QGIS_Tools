# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsApplication
import processing

from .system_design_provider import SystemDesignProvider

PLUGIN_DIR = os.path.dirname(__file__)
MENU = '&Illini Drainage System Design'


class SystemDesignPlugin(object):

    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None

    def initProcessing(self):
        self.provider = SystemDesignProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        self.action = QAction(QIcon(os.path.join(PLUGIN_DIR, 'icon.svg')),
                              'Drainage System Design (Tile Drainage E-K)', self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(MENU, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
        if self.action is not None:
            self.iface.removePluginMenu(MENU, self.action)
            self.iface.removeToolBarIcon(self.action)

    def run(self):
        processing.execAlgorithmDialog('illinidrainagesystemdesign:Drainage System Design')
