"""QGIS side of the bill of materials: map layers, project variables and the print
layout (the default quote layout, or your own template through Layout from Template)."""
import datetime

from qgis.core import (
    QgsExpressionContextUtils,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsLayoutFrame,
    QgsLayoutItemAttributeTable,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPage,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutMultiFrame,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsPrintLayout,
    QgsReadWriteContext,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)

from .bom_core import size_text

GROUP = "Tile Size Calculator"
BOM_LAYER = "Bill of materials"
FITTINGS_LAYER = "Fittings"
TABLE_ID = "bom_table"
BOM_FIELDS = ("Category", "Item", "Size", "Quantity", "Unit", "Unit price", "Amount",
              "Practice")
VARIABLES = ("tsc_project", "tsc_client", "tsc_prepared_by", "tsc_date", "tsc_notes",
             "tsc_subtotal", "tsc_markup", "tsc_tax", "tsc_total", "tsc_unpriced")


def money(v):
    return "" if v is None else "${:,.2f}".format(v)


def qty_text(q, unit):
    return "{:,.0f}".format(q) if unit in ("ft", "m") else "{:,.0f}".format(q) \
        if abs(q - round(q)) < 1e-9 else "{:,.2f}".format(q)


def bom_rows(est):
    """Rows of the bill layer and printed table: the priced lines, then the totals."""
    rows = []
    for l in est["lines"]:
        rows.append([l["category"], l["item"], size_text(l["size"]),
                     qty_text(l["qty"], l["unit"]), l["unit"],
                     money(l["price"]) if l["priced"] else "no price",
                     money(l["amount"]), l["practice"] or ""])
    rows.append(["", "", "", "", "", "Subtotal", money(est["subtotal"]), ""])
    if est["markup"]:
        rows.append(["", "", "", "", "", "Markup", money(est["markup"]), ""])
    if est["tax"]:
        rows.append(["", "", "", "", "", "Tax", money(est["tax"]), ""])
    rows.append(["", "", "", "", "", "Total", money(est["total"]), ""])
    return rows


def _string_field(name):
    try:
        from qgis.PyQt.QtCore import QVariant
        return QgsField(name, QVariant.String)
    except AttributeError:
        from qgis.PyQt.QtCore import QMetaType
        return QgsField(name, QMetaType.Type.QString)


def _replace_layer(project, name, layer):
    """Put layer in the Tile Size Calculator group, replacing one of the same name."""
    root = project.layerTreeRoot()
    group = root.findGroup(GROUP) or root.insertGroup(0, GROUP)
    for node in list(group.findLayers()):
        if node.name() == name:
            project.removeMapLayer(node.layerId())
    project.addMapLayer(layer, False)
    group.insertLayer(0, layer)
    return layer


def bill_layer(project, est):
    """A table layer (no geometry) holding the priced bill and the totals."""
    layer = QgsVectorLayer("None", BOM_LAYER, "memory")
    pr = layer.dataProvider()
    pr.addAttributes([_string_field(n) for n in BOM_FIELDS])
    layer.updateFields()
    feats = []
    for row in bom_rows(est):
        f = QgsFeature(layer.fields())
        f.setAttributes(row)
        feats.append(f)
    pr.addFeatures(feats)
    layer.updateExtents()
    return _replace_layer(project, BOM_LAYER, layer)


def fittings_layer(project, fittings, crs):
    """Points for the fittings and the outlet, labelled, for the plan map."""
    layer = QgsVectorLayer("Point?crs={}".format(crs.authid() or crs.toWkt()),
                           FITTINGS_LAYER, "memory")
    pr = layer.dataProvider()
    pr.addAttributes([_string_field("type"), _string_field("size"), _string_field("label")])
    layer.updateFields()
    feats = []
    for ft in fittings:
        if ft.get("x") is None:
            continue
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(ft["x"], ft["y"])))
        f.setAttributes([ft["type"], size_text(ft["size"]), ft["label"]])
        feats.append(f)
    pr.addFeatures(feats)
    layer.updateExtents()
    try:
        s = QgsPalLayerSettings()
        s.fieldName = "label"
        s.enabled = True
        layer.setLabeling(QgsVectorLayerSimpleLabeling(s))
        layer.setLabelsEnabled(True)
    except Exception:  # noqa: BLE001  labels are a nicety
        pass
    return _replace_layer(project, FITTINGS_LAYER, layer)


def set_variables(project, info, est):
    """Project variables for labels in a template: [% @tsc_project %] and so on."""
    vals = {"tsc_project": info.get("project", ""), "tsc_client": info.get("client", ""),
            "tsc_prepared_by": info.get("prepared_by", ""),
            "tsc_date": info.get("date") or datetime.date.today().strftime("%B %d, %Y"),
            "tsc_notes": info.get("notes", ""), "tsc_subtotal": money(est["subtotal"]),
            "tsc_markup": money(est["markup"]), "tsc_tax": money(est["tax"]),
            "tsc_total": money(est["total"]), "tsc_unpriced": str(est["unpriced"])}
    for k, v in vals.items():
        QgsExpressionContextUtils.setProjectVariable(project, k, v)
    return vals


# ---------------------------------------------------------------- layout
def _mm(x, y):
    return QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters)


def _size(w, h):
    return QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters)


def _label(layout, text, x, y, w, h, page=0, size=10, bold=False, item_id=None):
    lab = QgsLayoutItemLabel(layout)
    lab.setText(text)
    try:
        from qgis.core import QgsTextFormat
        from qgis.PyQt.QtGui import QFont
        fmt = QgsTextFormat()
        font = QFont()
        font.setBold(bold)
        fmt.setFont(font)
        fmt.setSize(size)
        lab.setTextFormat(fmt)
    except Exception:  # noqa: BLE001
        pass
    if item_id:
        lab.setId(item_id)
    layout.addLayoutItem(lab)
    lab.attemptResize(_size(w, h))
    lab.attemptMove(_mm(x, y), page=page)
    return lab


def _unique_name(manager, base):
    names = {l.name() for l in manager.layouts()}
    if base not in names:
        return base
    i = 2
    while "{} ({})".format(base, i) in names:
        i += 1
    return "{} ({})".format(base, i)


def bind_table(layout, bom):
    """Point every attribute table whose frame has the ID bom_table at the bill layer."""
    n = 0
    for mf in layout.multiFrames():
        if not isinstance(mf, QgsLayoutItemAttributeTable):
            continue
        if any(fr.id() == TABLE_ID for fr in mf.frames()):
            mf.setVectorLayer(bom)
            mf.setMaximumNumberOfFeatures(10000)
            mf.refresh()
            n += 1
    return n


def default_layout(project, title, network, fittings, bom, extent):
    """A two-page quote: a landscape plan of the network, then the bill of materials
    and cost estimate (more pages are added if the table runs on)."""
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(_unique_name(project.layoutManager(), title))
    pages = layout.pageCollection()
    pages.page(0).setPageSize("Letter", QgsLayoutItemPage.Landscape)
    p2 = QgsLayoutItemPage(layout)
    p2.setPageSize("Letter", QgsLayoutItemPage.Portrait)
    pages.addPage(p2)
    W, H = 279.4, 215.9                                       # landscape letter, mm

    _label(layout, title, 12, 8, W - 24, 12, size=18, bold=True, item_id="title")
    _label(layout, "[% @tsc_project %]  ·  [% @tsc_client %]  ·  [% @tsc_date %]",
           12, 20, W - 24, 7, size=10)
    mp = QgsLayoutItemMap(layout)
    mp.setId("main_map")
    layout.addLayoutItem(mp)
    mp.attemptMove(_mm(12, 30))
    mp.attemptResize(_size(W - 24 - 62, H - 30 - 12))
    if extent is not None:
        e = extent.buffered(max(extent.width(), extent.height()) * 0.04)
        mp.zoomToExtent(e)
    mp.setFrameEnabled(True)
    x2 = W - 12 - 58
    leg = QgsLayoutItemLegend(layout)
    leg.setTitle("Legend")
    leg.setLinkedMap(mp)
    try:
        leg.setLegendFilterByMapEnabled(True)
    except AttributeError:
        pass
    layout.addLayoutItem(leg)
    leg.attemptMove(_mm(x2, 30))
    arrow = QgsLayoutItemPicture(layout)
    arrow.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
    arrow.setLinkedMap(mp)
    layout.addLayoutItem(arrow)
    arrow.attemptResize(_size(12, 16))
    arrow.attemptMove(_mm(x2, H - 12 - 60))
    sb = QgsLayoutItemScaleBar(layout)
    sb.setStyle("Single Box")
    sb.setLinkedMap(mp)
    sb.applyDefaultSize()
    layout.addLayoutItem(sb)
    sb.attemptMove(_mm(x2, H - 12 - 40))
    _label(layout, "Estimated total: [% @tsc_total %]\nPrepared by [% @tsc_prepared_by %]",
           x2, H - 12 - 26, 58, 22, size=10, bold=True)

    # page 2: the bill of materials and the cost estimate
    PW = 215.9
    _label(layout, "Bill of materials and cost estimate", 15, 12, PW - 30, 10, page=1,
           size=16, bold=True)
    _label(layout, "[% @tsc_project %]  ·  [% @tsc_client %]  ·  [% @tsc_date %]\n"
           "[% @tsc_notes %]", 15, 23, PW - 30, 12, page=1, size=9)
    table = QgsLayoutItemAttributeTable.create(layout)
    table.setVectorLayer(bom)
    table.setMaximumNumberOfFeatures(10000)
    table.setResizeMode(QgsLayoutMultiFrame.RepeatUntilFinished)
    layout.addMultiFrame(table)
    frame = QgsLayoutFrame(layout, table)
    frame.setId(TABLE_ID)
    table.addFrame(frame)
    frame.attemptResize(_size(PW - 30, 279.4 - 40 - 20))
    frame.attemptMove(_mm(15, 40), page=1)
    _label(layout, "Quantities from the tile network; prices from the price table. "
           "Practice: NRCS conservation practice code.", 15, 279.4 - 14, PW - 30, 8,
           page=1, size=7)
    project.layoutManager().addLayout(layout)
    return layout


def template_layout(project, template, title, network, bom, iface=None):
    """Your .qpt template: through Layout from Template when it is installed (it fits
    the map to the network and sets the title), else loaded directly. Tables with the
    frame ID bom_table show the bill; labels can use the @tsc_ project variables."""
    layout = None
    try:
        import processing
        from qgis.core import QgsApplication
        alg = QgsApplication.processingRegistry().algorithmById(
            "layoutfromtemplate:layoutfromtemplate")
    except Exception:  # noqa: BLE001
        alg = None
    if alg is not None:
        if iface is not None:
            iface.setActiveLayer(network)
        out = processing.run("layoutfromtemplate:layoutfromtemplate",
                             {"TEMPLATE": template, "TITLE": title, "NAME": title,
                              "OPEN_DESIGNER": False})
        layout = project.layoutManager().layoutByName(out.get("LAYOUT_NAME"))
    if layout is None:
        from qgis.PyQt.QtXml import QDomDocument
        doc = QDomDocument()
        with open(template, encoding="utf-8") as fh:
            doc.setContent(fh.read())
        layout = QgsPrintLayout(project)
        layout.loadFromTemplate(doc, QgsReadWriteContext())
        layout.setName(_unique_name(project.layoutManager(), title))
        for item in layout.items():
            if isinstance(item, QgsLayoutItemLabel) and item.id() == "title":
                item.setText(title)
        project.layoutManager().addLayout(layout)
    bind_table(layout, bom)
    return layout
