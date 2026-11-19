import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import IlliniSubirrigationLayoutAlgorithm


class IlliniSubirrigationLayoutProvider(QgsProcessingProvider):
    def id(self):
        return "illinisubirrigation"

    def name(self):
        return "Illini subirrigation layout"

    def longName(self):
        return "Illini subirrigation layout"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def loadAlgorithms(self):
        self.addAlgorithm(IlliniSubirrigationLayoutAlgorithm())
