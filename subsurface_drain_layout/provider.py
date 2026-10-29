import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import SubsurfaceDrainLayoutAlgorithm


class DrainLayoutProvider(QgsProcessingProvider):
    def id(self):
        return "drainlayout"

    def name(self):
        return "Subsurface drainage"

    def longName(self):
        return "Subsurface drainage layout"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def loadAlgorithms(self):
        self.addAlgorithm(SubsurfaceDrainLayoutAlgorithm())
