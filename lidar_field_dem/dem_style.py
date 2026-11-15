# -*- coding: utf-8 -*-
"""DEM coloring, as in the Depressions and Laylines plugin.

A pseudocolor ramp stretched over the field's elevations (2nd to 98th
percentile, so the margin and odd edge cells don't wash out the field), plus an
optional hillshade layer directly above the DEM, multiplied at 45% with 3x
vertical exaggeration to show micro-relief such as swales and old furrows.
"""

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QColor, QPainter
from qgis.core import (
    QgsColorRampShader,
    QgsGradientColorRamp,
    QgsGradientStop,
    QgsHillshadeRenderer,
    QgsProcessingLayerPostProcessorInterface,
    QgsProject,
    QgsRasterLayer,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
    QgsStyle,
)

SCHEMES = ["Hypsometric (green - yellow - brown)", "Greens", "Tan / brown",
           "Don't color the DEM"]
NO_COLOR = 3
HILLSHADE_EXAGGERATION = 3.0      # flat fields need vertical exaggeration to show relief
HILLSHADE_OPACITY = 0.45
HILLSHADE_KEY = "lidar_field_dem/hillshade_of"

_KEEP = []                        # keep post-processors alive until QGIS calls them


def ramp(scheme):
    """Color ramp for a scheme (index into SCHEMES)."""
    def grad(stops):
        c = [QColor(h) for _, h in stops]
        mid = [QgsGradientStop(t, QColor(h)) for t, h in stops[1:-1]]
        return QgsGradientColorRamp(c[0], c[-1], False, mid)

    if scheme == 0:          # hypsometric: green (low, wet) - yellow - tan - brown (high)
        return grad([(0.0, "#2f7a3e"), (0.25, "#8fbf5a"), (0.5, "#ece39b"),
                     (0.75, "#d6a35c"), (1.0, "#8a5629")])
    if scheme == 1:          # Greens (QGIS built-in)
        r = QgsStyle.defaultStyle().colorRamp("Greens")
        return r if r is not None else grad([(0.0, "#f7fcf5"), (1.0, "#00441b")])
    return grad([(0.0, "#f6eedf"), (0.35, "#dcc196"), (0.7, "#a8743f"),   # tan / brown
                 (1.0, "#5e3b1c")])


def hillshade_z_factor(z_unit_m, xy_unit_m):
    """Hillshade z factor: converts elevation units to map units, with exaggeration."""
    return HILLSHADE_EXAGGERATION * z_unit_m / xy_unit_m


def color_dem(layer, scheme, vmin, vmax, hillshade=True, z_factor=HILLSHADE_EXAGGERATION):
    """Pseudocolor ``layer`` from vmin to vmax; optionally add a hillshade above it."""
    if scheme == NO_COLOR or layer is None:
        return
    if vmin is None or vmax is None:
        st = layer.dataProvider().bandStatistics(1)
        vmin, vmax = st.minimumValue, st.maximumValue
    if not vmax > vmin:
        vmax = vmin + 1.0
    r = ramp(scheme)
    shader = QgsColorRampShader(vmin, vmax)
    shader.setSourceColorRamp(r.clone())
    shader.setColorRampType(QgsColorRampShader.Interpolated)
    n = 9
    shader.setColorRampItemList([
        QgsColorRampShader.ColorRampItem(
            vmin + (vmax - vmin) * i / (n - 1), r.color(i / (n - 1)),
            "%.1f" % (vmin + (vmax - vmin) * i / (n - 1)))
        for i in range(n)])
    rs = QgsRasterShader()
    rs.setRasterShaderFunction(shader)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, rs)
    renderer.setClassificationMin(vmin)
    renderer.setClassificationMax(vmax)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    _refresh_legend(layer)
    if hillshade:
        # wait until the layer is in the layer tree, then put the hillshade above it
        lid = layer.id()
        QTimer.singleShot(0, lambda: add_hillshade(lid, z_factor))


def add_hillshade(layer_id, z_factor):
    """A second layer on the same file, directly above the DEM, multiplied."""
    project = QgsProject.instance()
    layer = project.mapLayer(layer_id)
    if layer is None:
        return
    existing = [l for l in project.mapLayers().values()
                if l.customProperty(HILLSHADE_KEY) == layer_id]
    if existing:
        hs = existing[0]
    else:
        hs = QgsRasterLayer(layer.source(), "Hillshade - %s" % layer.name(),
                            layer.providerType())
        if not hs.isValid():
            return
        hs.setCustomProperty(HILLSHADE_KEY, layer_id)
        project.addMapLayer(hs, False)
        root = project.layerTreeRoot()
        node = root.findLayer(layer_id)
        parent = node.parent() if node is not None else root
        idx = parent.children().index(node) if node is not None else 0
        parent.insertLayer(idx, hs)                   # directly above the DEM
    hr = QgsHillshadeRenderer(hs.dataProvider(), 1, 315.0, 45.0)
    hr.setZFactor(z_factor)
    try:
        hr.setMultiDirectional(True)
    except Exception:
        pass
    hr.setOpacity(HILLSHADE_OPACITY)
    hs.setRenderer(hr)
    hs.setBlendMode(QPainter.CompositionMode_Multiply)
    hs.triggerRepaint()


def _refresh_legend(layer):
    try:
        from qgis.utils import iface
        if iface is not None:
            iface.layerTreeView().refreshLayerSymbology(layer.id())
    except Exception:
        pass


class DemStyler(QgsProcessingLayerPostProcessorInterface):
    """Colors a DEM output when Processing loads it into the map."""

    def __init__(self, scheme, vmin, vmax, hillshade, z_factor, name=None):
        super().__init__()
        self.scheme, self.vmin, self.vmax = scheme, vmin, vmax
        self.hillshade, self.z_factor, self.name = hillshade, z_factor, name

    def postProcessLayer(self, layer, context, feedback):  # noqa: N802
        try:
            if self.name:
                layer.setName(self.name)
            color_dem(layer, self.scheme, self.vmin, self.vmax, self.hillshade, self.z_factor)
        except Exception as e:                      # cosmetic; never fail the run
            if feedback is not None:
                feedback.pushInfo("Could not color the DEM: %s" % e)


def attach_styler(context, path, styler):
    """Style ``path`` when it is loaded at the end of the run (if it will be)."""
    if not context.willLoadLayerOnCompletion(path):
        return False
    _KEEP.append(styler)
    del _KEEP[:-20]
    context.layerToLoadOnCompletionDetails(path).setPostProcessor(styler)
    return True
