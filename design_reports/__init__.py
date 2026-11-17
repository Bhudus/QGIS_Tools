def classFactory(iface):  # noqa: N802 (QGIS entry point)
    from .plugin import DesignReportsPlugin
    return DesignReportsPlugin(iface)
