# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider

from .min_curvature_algorithm import MinCurvatureAlgorithm


class MinCurvatureProvider(QgsProcessingProvider):

    def loadAlgorithms(self):  # noqa: N802
        self.addAlgorithm(MinCurvatureAlgorithm())

    def id(self):
        return 'mincurvature'

    def name(self):
        return QCoreApplication.translate('MinCurvatureProvider', 'Surface interpolation')

    def longName(self):  # noqa: N802
        return self.name()

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), 'icon.svg'))
