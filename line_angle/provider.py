"""Processing provider for the Line Angle plugin."""
import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .segment_angles_algorithm import SegmentAnglesAlgorithm


class LineAngleProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(SegmentAnglesAlgorithm())

    def id(self):
        return "lineangle"

    def name(self):
        return "Line Angle"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
