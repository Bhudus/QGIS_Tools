# -*- coding: utf-8 -*-
"""Random Point Selection - QGIS 3 Processing plugin."""


def classFactory(iface):  # noqa: N802 (QGIS naming convention)
    from .random_point_selection_plugin import RandomPointSelectionPlugin
    return RandomPointSelectionPlugin(iface)
