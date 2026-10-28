# -*- coding: utf-8 -*-
"""Illini Drainage System Design - standalone QGIS plugin (tools E-K of Illini Drainage Tools combined)."""


def classFactory(iface):  # pylint: disable=invalid-name
    from .system_design_plugin import SystemDesignPlugin
    return SystemDesignPlugin(iface)
