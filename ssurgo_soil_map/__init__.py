def classFactory(iface):
    from .plugin import SsurgoSoilMapPlugin
    return SsurgoSoilMapPlugin(iface)
