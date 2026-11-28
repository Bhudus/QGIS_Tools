import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import TileMachineExportAlgorithm


class TileMachineExportProvider(QgsProcessingProvider):
    def id(self):
        return "tilemachineexport"

    def name(self):
        return "Tile machine-control export"

    def longName(self):
        return "Tile machine-control export"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def loadAlgorithms(self):
        self.addAlgorithm(TileMachineExportAlgorithm())
