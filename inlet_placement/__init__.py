def classFactory(iface):  # noqa: N802 (QGIS entry point)
    from .plugin import InletPlacementPlugin
    return InletPlacementPlugin(iface)
