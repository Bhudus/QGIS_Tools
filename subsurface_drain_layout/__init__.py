def classFactory(iface):  # noqa: N802 (QGIS entry point)
    from .plugin import DrainLayoutPlugin
    return DrainLayoutPlugin(iface)
