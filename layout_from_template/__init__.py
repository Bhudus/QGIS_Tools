def classFactory(iface):
    from .plugin import LayoutFromTemplatePlugin
    return LayoutFromTemplatePlugin(iface)
