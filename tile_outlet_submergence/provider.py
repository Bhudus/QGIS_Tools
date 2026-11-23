import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import TileOutletSubmergenceAlgorithm


class TileOutletSubmergenceProvider(QgsProcessingProvider):
    def id(self):
        return "tileoutletsubmergence"

    def name(self):
        return "Tile outlet submergence"

    def longName(self):
        return "Tile outlet submergence"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def loadAlgorithms(self):
        self.addAlgorithm(TileOutletSubmergenceAlgorithm())
