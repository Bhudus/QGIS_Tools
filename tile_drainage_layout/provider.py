import os

from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider

from .algorithm import TileDrainageLayoutAlgorithm


class TileDrainageProvider(QgsProcessingProvider):

    def loadAlgorithms(self):
        self.addAlgorithm(TileDrainageLayoutAlgorithm())

    def id(self):
        return 'tiledrainage'

    def name(self):
        return 'Tile Drainage'

    def longName(self):
        return self.name()

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), 'icon.png'))
