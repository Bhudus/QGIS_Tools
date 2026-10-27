# -*- coding: utf-8 -*-
from qgis.core import QgsApplication

from .min_curvature_provider import MinCurvatureProvider


class MinCurvaturePlugin(object):
    """Registers the Processing provider when the plugin is loaded."""

    def __init__(self, iface):
        self.iface = iface
        self.provider = None

    def initProcessing(self):  # noqa: N802
        self.provider = MinCurvatureProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
