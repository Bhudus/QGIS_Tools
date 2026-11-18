# -*- coding: utf-8 -*-
import os
from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon
from .algorithm import DrainagePathAlgorithm


class DrainageOutletProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(DrainagePathAlgorithm())

    def id(self):
        return "drainageoutlet"

    def name(self):
        return "Drainage outlet"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
