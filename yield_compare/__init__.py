# -*- coding: utf-8 -*-
"""Yield Compare – QGIS port of the VB6 'Yield Statistics' program."""


def classFactory(iface):  # noqa: N802 (QGIS API name)
    from .plugin import YieldComparePlugin
    return YieldComparePlugin(iface)
