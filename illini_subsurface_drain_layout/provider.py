import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import IlliniSubsurfaceDrainLayoutAlgorithm
from .algorithm_landrain import ImportLandrainAlgorithm


class IlliniDrainLayoutProvider(QgsProcessingProvider):
    def id(self):
        return "illinidrainlayout"

    def name(self):
        return "Illini subsurface drain layout"

    def longName(self):
        return "Illini subsurface drain layout"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def loadAlgorithms(self):
        self.addAlgorithm(IlliniSubsurfaceDrainLayoutAlgorithm())
        self.addAlgorithm(ImportLandrainAlgorithm())
