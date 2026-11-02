# -*- coding: utf-8 -*-
"""LiDAR Field DEM - QGIS plugin entry point."""


def classFactory(iface):  # noqa: N802 (QGIS API name)
    from .plugin import LidarFieldDemPlugin
    return LidarFieldDemPlugin(iface)
