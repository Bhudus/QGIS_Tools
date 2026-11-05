# -*- coding: utf-8 -*-


def classFactory(iface):  # noqa: N802 (QGIS entry point name)
    from .plugin import StreamStatsDelineatorPlugin
    return StreamStatsDelineatorPlugin(iface)
