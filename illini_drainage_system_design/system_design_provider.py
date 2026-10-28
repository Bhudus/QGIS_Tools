# -*- coding: utf-8 -*-
import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .system_design_algorithm import SystemDesignAlgorithm

PLUGIN_DIR = os.path.dirname(__file__)


class SystemDesignProvider(QgsProcessingProvider):

    def loadAlgorithms(self):
        self.addAlgorithm(SystemDesignAlgorithm())

    def id(self):
        # Different from Illini Drainage Tools so both plugins can be installed together
        return 'illinidrainagesystemdesign'

    def name(self):
        return self.tr('Illini Drainage System Design')

    def longName(self):
        return self.name()

    def icon(self):
        return QIcon(os.path.join(PLUGIN_DIR, 'icon.svg'))
