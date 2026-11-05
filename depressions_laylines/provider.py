# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider

from .algorithms.depressions_laylines import DepressionsLaylines

ICON = os.path.join(os.path.dirname(__file__), "icon.svg")


class DepressionsLaylinesProvider(QgsProcessingProvider):

    def id(self):
        return "depressionslaylines"

    def name(self):
        return "Depressions and Laylines"

    def longName(self):
        return "Depressions and Laylines (subsurface drainage)"

    def icon(self):
        return QIcon(ICON)

    def loadAlgorithms(self):
        self.addAlgorithm(DepressionsLaylines())
