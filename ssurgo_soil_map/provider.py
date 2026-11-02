import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import SoilMapAlgorithm


class SsurgoProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(SoilMapAlgorithm())

    def id(self):
        return "ssurgosoilmap"

    def name(self):
        return "SSURGO Soil Map"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
