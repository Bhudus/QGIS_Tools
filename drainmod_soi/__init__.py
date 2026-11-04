def classFactory(iface):
    from .plugin import DrainmodSoiPlugin
    return DrainmodSoiPlugin(iface)
