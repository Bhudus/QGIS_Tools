"""Shared styling for the field boundary outline."""
from qgis.core import QgsFillSymbol, QgsSingleSymbolRenderer


def boundary_symbol():
    """Hollow fill with a dark outline under a bright one, so it reads on soils and on imagery."""
    symbol = QgsFillSymbol.createSimple({
        "style": "no", "outline_color": "30,30,30,255", "outline_width": "1.4",
        "outline_width_unit": "MM", "joinstyle": "round",
    })
    top = QgsFillSymbol.createSimple({
        "style": "no", "outline_color": "255,214,0,255", "outline_width": "0.7",
        "outline_width_unit": "MM", "joinstyle": "round",
    })
    symbol.appendSymbolLayer(top.symbolLayer(0).clone())
    return symbol


def apply_boundary_style(layer):
    layer.setRenderer(QgsSingleSymbolRenderer(boundary_symbol()))
    layer.triggerRepaint()
