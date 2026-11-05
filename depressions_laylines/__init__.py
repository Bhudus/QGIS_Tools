# -*- coding: utf-8 -*-
"""Depressions and Laylines - QGIS plugin entry point."""


def classFactory(iface):  # noqa: N802 (QGIS API name)
    from .plugin import DepressionsLaylinesPlugin
    return DepressionsLaylinesPlugin(iface)
