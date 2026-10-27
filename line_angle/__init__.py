def classFactory(iface):  # noqa: N802 - QGIS entry point
    from .line_angle import LineAnglePlugin
    return LineAnglePlugin(iface)
