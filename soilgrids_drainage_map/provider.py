import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import SoilGridsDrainageAlgorithm


class SoilGridsProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(SoilGridsDrainageAlgorithm())

    def id(self):
        return "soilgridsdrainage"

    def name(self):
        return "SoilGrids drainage soil map"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
