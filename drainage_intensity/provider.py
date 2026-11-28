import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import DrainageIntensityAlgorithm


class DrainageIntensityProvider(QgsProcessingProvider):
    def id(self):
        return "drainageintensity"

    def name(self):
        return "Drainage intensity"

    def longName(self):
        return "Drainage intensity"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def loadAlgorithms(self):
        self.addAlgorithm(DrainageIntensityAlgorithm())
