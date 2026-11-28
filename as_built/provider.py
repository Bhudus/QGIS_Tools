import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import AsBuiltAlgorithm


class AsBuiltProvider(QgsProcessingProvider):
    def id(self):
        return "asbuilt"

    def name(self):
        return "As-built check"

    def longName(self):
        return "As-built check"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def loadAlgorithms(self):
        self.addAlgorithm(AsBuiltAlgorithm())
