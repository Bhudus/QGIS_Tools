import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import FieldRunonAlgorithm


class FieldRunonProvider(QgsProcessingProvider):
    def id(self):
        return "fieldrunon"

    def name(self):
        return "Field Run-on"

    def longName(self):
        return "Field run-on areas"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def loadAlgorithms(self):
        self.addAlgorithm(FieldRunonAlgorithm())
