# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider

from .contour_algorithm import BoundaryContoursAlgorithm


class BoundaryContoursProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(BoundaryContoursAlgorithm())

    def id(self):
        return "boundarycontours"

    def name(self):
        return "Tile Drainage"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
