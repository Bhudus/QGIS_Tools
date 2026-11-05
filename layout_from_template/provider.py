"""Processing provider for the Layout from Template plugin."""
import os

from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider

from .algorithm import LayoutFromTemplate


class LayoutFromTemplateProvider(QgsProcessingProvider):

    def loadAlgorithms(self):
        self.addAlgorithm(LayoutFromTemplate())

    def id(self):
        return "layoutfromtemplate"

    def name(self):
        return "Layout from template"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
