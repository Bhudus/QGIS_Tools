import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import RecalcAlgorithm


class RecalcProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(RecalcAlgorithm())

    def id(self):
        return "drainspacingrecalc"

    def name(self):
        return "Drain spacing recalculator"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
