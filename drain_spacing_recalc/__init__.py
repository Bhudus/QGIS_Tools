def classFactory(iface):
    from .plugin import DrainSpacingRecalcPlugin
    return DrainSpacingRecalcPlugin(iface)
