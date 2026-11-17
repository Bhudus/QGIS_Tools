"""Map pages for the DWM plan: styled memory layers rendered to PNG with QGIS.

All layers are built in the calling thread (memory copies), so rendering is safe from
a Processing background thread.
"""
from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsFeature,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsLineSymbol,
    QgsMapRendererCustomPainterJob,
    QgsMapSettings,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsRasterLayer,
    QgsRectangle,
    QgsRendererCategory,
    QgsRuleBasedRenderer,
    QgsSingleSymbolRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, QSize, Qt
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF

from .core.compute import nice_scale, scale_text, scalebar_length

DPI = 200
PAGE_W_IN = 6.5
PAGE_H_IN = 7.4
SOIL_COLORS = ["#e6c27a", "#9fc5e8", "#b6d7a8", "#f4b183", "#d5a6bd", "#ffe599",
               "#a2c4c9", "#c9daf8", "#f9cb9c", "#d9ead3", "#b4a7d6", "#ea9999"]

OSM_URL = ("type=xyz&url=https://tile.openstreetmap.org/%7Bz%7D/%7Bx%7D/%7By%7D.png"
           "&zmax=19&zmin=0")
IMAGERY_URL = ("type=xyz&url=https://server.arcgisonline.com/ArcGIS/rest/services/"
               "World_Imagery/MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D&zmax=19&zmin=0")


def _ft_factor(crs):
    return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), Qgis.DistanceUnit.Feet)


# ---------------------------------------------------------------- layers
def memory_layer(kind, crs, fields, rows, name):
    """rows: (QgsGeometry, [attrs]). Lines and polygons are stored as multi-part 2D."""
    if kind in ("Polygon", "LineString"):
        kind = "Multi" + kind
    lyr = QgsVectorLayer("{}?crs={}".format(kind, crs.authid() or "EPSG:4326"), name,
                         "memory")
    if not crs.authid():
        lyr.setCrs(crs)
    pr = lyr.dataProvider()
    pr.addAttributes([QgsField(n, t) for n, t in fields])
    lyr.updateFields()
    feats = []
    for g, attrs in rows:
        if g is None or g.isNull():
            continue
        ag = g.constGet().clone()
        ag.dropZValue()
        ag.dropMValue()
        g = QgsGeometry(ag)
        if kind.startswith("Multi"):
            if g.type() != (Qgis.GeometryType.Polygon if "Polygon" in kind
                            else Qgis.GeometryType.Line):
                # e.g. a GeometryCollection from an intersection: keep the right parts
                col = g.constGet()
                parts = [QgsGeometry(col.geometryN(k).clone())
                         for k in range(col.numGeometries())] if g.isMultipart() else []
                want = Qgis.GeometryType.Polygon if "Polygon" in kind else Qgis.GeometryType.Line
                parts = [p for p in parts if p.type() == want]
                if not parts:
                    continue
                g = QgsGeometry.collectGeometry(parts)
            g.convertToMultiType()
        f = QgsFeature(lyr.fields())
        f.setGeometry(g)
        f.setAttributes(list(attrs))
        feats.append(f)
    pr.addFeatures(feats)
    lyr.updateExtents()
    return lyr


def _fmt(size=8, bold=False, color="#000000", buffer=True):
    tf = QgsTextFormat()
    font = QFont("Arial")
    font.setBold(bold)
    tf.setFont(font)
    tf.setSize(size)
    tf.setColor(QColor(color))
    if buffer:
        b = QgsTextBufferSettings()
        b.setEnabled(True)
        b.setSize(0.8)
        b.setColor(QColor("white"))
        tf.setBuffer(b)
    return tf


def _label(layer, expr, size=8, bold=False, color="#000000", line=False, priority=5):
    s = QgsPalLayerSettings()
    s.fieldName = expr
    s.isExpression = True
    s.setFormat(_fmt(size, bold, color))
    s.priority = priority
    if line:
        try:
            s.placement = Qgis.LabelPlacement.Curved
        except AttributeError:
            s.placement = QgsPalLayerSettings.Curved
    layer.setLabeling(QgsVectorLayerSimpleLabeling(s))
    layer.setLabelsEnabled(True)


def style_outline(layer, color="#d00000", width=0.8, dash=False):
    sym = QgsFillSymbol.createSimple({"style": "no", "outline_color": color,
                                      "outline_width": str(width),
                                      "outline_style": "dash" if dash else "solid"})
    layer.setRenderer(QgsSingleSymbolRenderer(sym))


def style_fill(layer, fill, outline="#333333", width=0.3, alpha=110):
    c = QColor(fill)
    sym = QgsFillSymbol.createSimple({"color": "{},{},{},{}".format(c.red(), c.green(),
                                                                    c.blue(), alpha),
                                      "outline_color": outline, "outline_width": str(width)})
    layer.setRenderer(QgsSingleSymbolRenderer(sym))


def style_soils(layer, symbols):
    cats = []
    for k, sym in enumerate(symbols):
        c = QColor(SOIL_COLORS[k % len(SOIL_COLORS)])
        cats.append(QgsRendererCategory(sym, QgsFillSymbol.createSimple(
            {"color": "{},{},{},200".format(c.red(), c.green(), c.blue()),
             "outline_color": "#555555", "outline_width": "0.25"}), sym))
    layer.setRenderer(QgsCategorizedSymbolRenderer("musym", cats))
    _label(layer, '"musym"', size=9, bold=True)


def style_network(layer, faint=False, labels=True):
    root = QgsRuleBasedRenderer.Rule(None)
    spec = (("main", "#b91c1c", 1.0), ("submain", "#ea580c", 0.7), ("lateral", "#1d4ed8", 0.4))
    for kind, color, width in spec:
        if faint:
            color, width = "#5b6b8a", width * 0.6
        sym = QgsLineSymbol.createSimple({"line_color": color, "line_width": str(width)})
        root.appendChild(QgsRuleBasedRenderer.Rule(sym, 0, 0, "\"kind\" = '{}'".format(kind),
                                                   kind.capitalize()))
    sym = QgsLineSymbol.createSimple({"line_color": "#5b6b8a" if faint else "#1d4ed8",
                                      "line_width": "0.4"})
    root.appendChild(QgsRuleBasedRenderer.Rule(sym, 0, 0, "ELSE", "Tile"))
    layer.setRenderer(QgsRuleBasedRenderer(root))
    if labels and not faint:
        if layer.fields().indexOf("label") >= 0:
            _label(layer, '"label"', size=5.5, color="#1e293b", line=True)
        else:
            _label(layer, "CASE WHEN \"kind\" IN ('main','submain') AND \"size\" IS NOT NULL "
                          "THEN \"size\" END", size=7, color="#7f1d1d", line=True)


def style_existing(layer):
    sym = QgsLineSymbol.createSimple({"line_color": "#6b7280", "line_width": "0.5",
                                      "line_style": "dash"})
    layer.setRenderer(QgsSingleSymbolRenderer(sym))


def style_lines(layer, color, width=0.4, label=None):
    sym = QgsLineSymbol.createSimple({"line_color": color, "line_width": str(width)})
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    if label:
        _label(layer, label, size=6.5, color=color, line=True)


def style_labeled_fill(layer, fill, outline, alpha, label, label_color="#000000", size=8):
    style_fill(layer, fill, outline=outline, width=0.4, alpha=alpha)
    _label(layer, label, size=size, bold=True, color=label_color)


def style_contours(layer, major_label=True):
    root = QgsRuleBasedRenderer.Rule(None)
    root.appendChild(QgsRuleBasedRenderer.Rule(QgsLineSymbol.createSimple(
        {"line_color": "#8a6d3b", "line_width": "0.35"}), 0, 0, "\"major\" = 1", "Index"))
    root.appendChild(QgsRuleBasedRenderer.Rule(QgsLineSymbol.createSimple(
        {"line_color": "#c4a77d", "line_width": "0.15"}), 0, 0, "ELSE", "Contour"))
    layer.setRenderer(QgsRuleBasedRenderer(root))
    if major_label:
        _label(layer, "CASE WHEN \"major\" = 1 THEN format_number(\"elev\", "
                      "CASE WHEN \"elev\" = round(\"elev\") THEN 0 ELSE 1 END) END",
               size=6.5, color="#6b4f1d", line=True, priority=3)


def style_points(layer, shape="square", color="#000000", size=3.0, label=None,
                 label_color="#000000"):
    sym = QgsMarkerSymbol.createSimple({"name": shape, "color": color,
                                        "outline_color": "#ffffff", "size": str(size)})
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    if label:
        _label(layer, label, size=8, bold=True, color=label_color, priority=10)


def basemap(kind):
    url = {1: OSM_URL, 2: IMAGERY_URL}.get(kind)
    if not url:
        return None
    lyr = QgsRasterLayer(url, "Basemap", "wms")
    return lyr if lyr.isValid() else None


# ---------------------------------------------------------------- page layout
def page_scale(rect, crs, width_in=PAGE_W_IN, height_in=PAGE_H_IN, min_scale=None):
    """Standard scale at which `rect` fits on the page."""
    ft = _ft_factor(crs)
    need = max(rect.width() * ft * 12.0 / width_in, rect.height() * ft * 12.0 / height_in)
    s = nice_scale(need)
    if min_scale:
        s = max(s, min_scale)
    return s


def render(layers, crs, center, scale, transform_context, height_in=None,
           width_in=PAGE_W_IN, title=None, legend=None, feedback=None):
    """Render layers (top first) at a fixed scale, centred on `center`; the page is
    `width_in` wide and `height_in` tall. Returns PNG bytes."""
    ft = _ft_factor(crs)
    height_in = height_in or PAGE_H_IN
    w_mu = scale * width_in / 12.0 / ft
    h_mu = scale * height_in / 12.0 / ft
    rect = QgsRectangle(center.x() - w_mu / 2, center.y() - h_mu / 2,
                        center.x() + w_mu / 2, center.y() + h_mu / 2)
    wpx, hpx = int(round(width_in * DPI)), int(round(height_in * DPI))
    ms = QgsMapSettings()
    ms.setDestinationCrs(crs)
    ms.setTransformContext(transform_context)
    ms.setOutputSize(QSize(wpx, hpx))
    ms.setOutputDpi(DPI)
    ms.setExtent(rect)
    ms.setLayers([lyr for lyr in layers if lyr is not None])
    ms.setBackgroundColor(QColor("white"))
    ms.setFlag(Qgis.MapSettingsFlag.Antialiasing, True)
    ms.setFlag(Qgis.MapSettingsFlag.DrawLabeling, True)
    img = QImage(wpx, hpx, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor("white"))
    p = QPainter(img)
    try:
        job = QgsMapRendererCustomPainterJob(ms, p)
        job.start()
        job.waitForFinished()
        if feedback is not None:
            for e in job.errors():
                feedback.pushInfo("Map: {} - {}".format(e.layerID, e.message))
        _decorate(p, wpx, hpx, scale, width_in, title, legend)
    finally:
        p.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba.data())


def _decorate(p, wpx, hpx, scale, width_in, title, legend):
    px_pt = DPI / 72.0
    p.setRenderHint(QPainter.Antialiasing, True)
    # frame
    p.setPen(QPen(QColor("#000000"), 2))
    p.setBrush(Qt.NoBrush)
    p.drawRect(1, 1, wpx - 3, hpx - 3)
    font = QFont("Arial")
    # scale bar (bottom left)
    L = scalebar_length(scale, width_in)
    px_per_ft = DPI * 12.0 / scale
    bar = L * px_per_ft
    x0, y0 = 30, hpx - 40
    box = QRectF(x0 - 12, y0 - 52, bar + 160, 70)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 220))
    p.drawRect(box)
    p.setPen(QPen(QColor("#000000"), 2))
    for k in range(4):
        p.setBrush(QColor("#000000") if k % 2 == 0 else QColor("#ffffff"))
        p.drawRect(QRectF(x0 + k * bar / 4, y0 - 10, bar / 4, 10))
    font.setPixelSize(int(7 * px_pt))
    p.setFont(font)
    p.drawText(QPointF(x0 + bar + 10, y0), "{:,.0f} ft".format(L))
    p.drawText(QPointF(x0, y0 - 18), scale_text(scale))
    # north arrow (top right)
    cx, cy = wpx - 60, 40
    p.setPen(QPen(QColor("#000000"), 2))
    p.setBrush(QColor(255, 255, 255, 220))
    p.drawEllipse(QPointF(cx, cy + 22), 30, 30)
    p.setBrush(QColor("#000000"))
    p.drawPolygon(QPolygonF([QPointF(cx, cy), QPointF(cx - 12, cy + 40),
                             QPointF(cx, cy + 32), QPointF(cx + 12, cy + 40)]))
    font.setBold(True)
    font.setPixelSize(int(8 * px_pt))
    p.setFont(font)
    p.drawText(QRectF(cx - 20, cy + 40, 40, 30), Qt.AlignCenter, "N")
    # title (top left)
    if title:
        font.setPixelSize(int(9 * px_pt))
        p.setFont(font)
        r = p.boundingRect(QRectF(16, 12, wpx * 0.7, 200), Qt.AlignLeft | Qt.TextWordWrap, title)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 220))
        p.drawRect(r.adjusted(-6, -4, 6, 4))
        p.setPen(QPen(QColor("#000000"), 1))
        p.drawText(r, Qt.AlignLeft | Qt.TextWordWrap, title)
    # legend (bottom right): list of (kind, color, text) kind in line/fill/point
    if legend:
        font.setBold(False)
        font.setPixelSize(int(7 * px_pt))
        p.setFont(font)
        lh = int(11 * px_pt)
        wbox = int(max(len(t) for _, _, t in legend) * 3.6 * px_pt + 60)
        hbox = lh * len(legend) + 16
        bx, by = wpx - wbox - 14, hpx - hbox - 14
        p.setPen(QPen(QColor("#666666"), 1))
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawRect(QRectF(bx, by, wbox, hbox))
        for k, (kind, color, text) in enumerate(legend):
            yy = by + 8 + k * lh + lh / 2
            c = QColor(color)
            if kind == "line":
                p.setPen(QPen(c, 4))
                p.drawLine(QPointF(bx + 10, yy), QPointF(bx + 40, yy))
            elif kind == "point":
                p.setPen(QPen(QColor("#ffffff"), 1))
                p.setBrush(c)
                p.drawRect(QRectF(bx + 18, yy - 7, 14, 14))
            else:
                p.setPen(QPen(QColor("#333333"), 1))
                p.setBrush(c)
                p.drawRect(QRectF(bx + 10, yy - 9, 30, 18))
            p.setPen(QPen(QColor("#000000"), 1))
            p.drawText(QPointF(bx + 50, yy + lh * 0.3), text)
