def classFactory(iface):  # noqa: N802
    from .plugin import MainTileLocatorPlugin
    return MainTileLocatorPlugin(iface)
