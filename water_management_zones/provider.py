import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import WaterManagementZonesAlgorithm


class WaterManagementZonesProvider(QgsProcessingProvider):
    def id(self):
        return "wmzones"

    def name(self):
        return "Water management zones"

    def longName(self):
        return "Water management zones"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def loadAlgorithms(self):
        self.addAlgorithm(WaterManagementZonesAlgorithm())
