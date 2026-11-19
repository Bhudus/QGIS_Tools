# -*- coding: utf-8 -*-


def classFactory(iface):
    from .plugin import BoundaryContoursPlugin
    return BoundaryContoursPlugin(iface)
