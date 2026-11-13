def classFactory(iface):  # noqa: N802 (QGIS entry point)
    from .plugin import TileProfilePlugin
    return TileProfilePlugin(iface)
