import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import MainTileLocatorAlgorithm


class MainTileLocatorProvider(QgsProcessingProvider):
    def id(self):
        return "illinimaintilelocator"

    def name(self):
        return "Illini main tile locator"

    def longName(self):
        return "Illini main tile locator"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def loadAlgorithms(self):
        self.addAlgorithm(MainTileLocatorAlgorithm())
