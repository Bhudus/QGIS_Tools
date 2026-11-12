def classFactory(iface):  # noqa: N802 (QGIS API name)
    from .plugin import TileDrainageLayoutPlugin
    return TileDrainageLayoutPlugin(iface)
