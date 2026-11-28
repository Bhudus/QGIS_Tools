import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import InletPlacementAlgorithm


class InletPlacementProvider(QgsProcessingProvider):
    def id(self):
        return "inletplacement"

    def name(self):
        return "Surface and blind inlets"

    def longName(self):
        return "Surface and blind inlets"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def loadAlgorithms(self):
        self.addAlgorithm(InletPlacementAlgorithm())
