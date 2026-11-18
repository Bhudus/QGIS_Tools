# -*- coding: utf-8 -*-
def classFactory(iface):
    from .drainage_outlet import DrainageOutletPlugin
    return DrainageOutletPlugin(iface)
