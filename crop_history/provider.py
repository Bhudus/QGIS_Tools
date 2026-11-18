import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import CropHistoryAlgorithm


class CropHistoryProvider(QgsProcessingProvider):
    def id(self):
        return "crophistory"

    def name(self):
        return "Crop history"

    def longName(self):
        return "Crop history (Cropland Data Layer)"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def loadAlgorithms(self):
        self.addAlgorithm(CropHistoryAlgorithm())
