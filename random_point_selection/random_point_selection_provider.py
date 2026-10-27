# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider

from .random_point_selection_algorithm import RandomPointSelection


class RandomPointSelectionProvider(QgsProcessingProvider):

    def loadAlgorithms(self):  # noqa: N802
        self.addAlgorithm(RandomPointSelection())

    def id(self):
        return 'randompointselection'

    def name(self):
        return QCoreApplication.translate('RandomPointSelectionProvider', 'Random Point Selection')

    def longName(self):  # noqa: N802
        return self.name()

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), 'icon.svg'))
