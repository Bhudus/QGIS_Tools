# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider

from .algorithms.field_dem import LidarFieldDEM
from .algorithms.list_collections import ListLidarCollections

ICON = os.path.join(os.path.dirname(__file__), "icon.svg")


class LidarFieldDemProvider(QgsProcessingProvider):

    def id(self):
        return "lidarfielddem"

    def name(self):
        return "LiDAR Field DEM"

    def longName(self):
        return "LiDAR Field DEM (ISGS / USGS 3DEP)"

    def icon(self):
        return QIcon(ICON)

    def loadAlgorithms(self):
        self.addAlgorithm(LidarFieldDEM())
        self.addAlgorithm(ListLidarCollections())
