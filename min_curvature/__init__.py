# -*- coding: utf-8 -*-
"""Minimum Curvature Interpolation - QGIS 3 Processing plugin."""


def classFactory(iface):  # noqa: N802 (QGIS naming convention)
    from .min_curvature_plugin import MinCurvaturePlugin
    return MinCurvaturePlugin(iface)
