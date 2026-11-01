def classFactory(iface):
    from .drainmod_weather import DrainmodWeatherPlugin
    return DrainmodWeatherPlugin(iface)
