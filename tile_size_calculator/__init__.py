def classFactory(iface):
    from .plugin import TileSizeCalculatorPlugin
    return TileSizeCalculatorPlugin(iface)
