"""Processing algorithm: create a print layout from a .qpt template
(or a built-in default layout) using the current map canvas."""
import os

from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtGui import QColor, QFont, QIcon
from qgis.PyQt.QtXml import QDomDocument
from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsFillSymbol,
    QgsLayoutExporter,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutMeasurement,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPrintLayout,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterString,
    QgsReadWriteContext,
    QgsRectangle,
    QgsScaleBarSettings,
    QgsTextFormat,
    QgsUnitTypes,
)

try:
    from qgis.utils import iface
except ImportError:  # e.g. running under qgis_process
    iface = None

try:
    MM = Qgis.LayoutUnit.Millimeters
except AttributeError:  # older QGIS
    MM = QgsUnitTypes.LayoutMillimeters

# Portrait (width, height) in mm
PAGE_SIZES = [
    ("A4", 210.0, 297.0),
    ("A3", 297.0, 420.0),
    ("Letter", 215.9, 279.4),
    ("Legal", 215.9, 355.6),
    ("Tabloid", 279.4, 431.8),
]
ORIENTATIONS = ["Auto (from selected layer)", "Portrait", "Landscape"]
FIT_MARGIN = 0.02  # fraction of the layer extent added on each side when fitting
NORTH_ARROW_SVG =":/images/north_arrows/layout_default_north_arrow.svg"


def _place(item, x, y, w=None, h=None):
    item.attemptMove(QgsLayoutPoint(x, y, MM))
    if w is not None and h is not None:
        item.attemptResize(QgsLayoutSize(w, h, MM))


class LayoutFromTemplate(QgsProcessingAlgorithm):
    TEMPLATE = "TEMPLATE"
    NAME = "NAME"
    TITLE = "TITLE"
    PAGE_SIZE = "PAGE_SIZE"
    ORIENTATION = "ORIENTATION"
    EXTENT = "EXTENT"
    MAP_ID = "MAP_ID"
    LOCK_LAYERS = "LOCK_LAYERS"
    FIT_TO_LAYER = "FIT_TO_LAYER"
    OPEN_DESIGNER = "OPEN_DESIGNER"
    EXPORT = "EXPORT"
    LAYOUT_NAME = "LAYOUT_NAME"

    # ------------------------------------------------------------------ meta
    def tr(self, text):
        return QCoreApplication.translate("LayoutFromTemplate", text)

    def createInstance(self):
        return LayoutFromTemplate()

    def name(self):
        return "layoutfromtemplate"

    def displayName(self):
        return self.tr("Create print layout from template")

    def group(self):
        return ""

    def groupId(self):
        return ""

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))

    def shortHelpString(self):
        return self.tr(
            "Creates a new print layout and sets its main map to the current map "
            "canvas (extent, CRS, rotation).<br><br>"
            "<b>Layout template</b>: a .qpt file. <b>Leave empty</b> to build a "
            "default layout with a title, map, legend, north arrow, scale bar and "
            "a frame around everything.<br>"
            "<b>Title</b>: title text. For the default layout it defaults to the "
            "selected layer's name; for a template it replaces the text of a label "
            "whose item ID is 'title'.<br>"
            "<b>Page size / Orientation</b> (default layout only): 'Auto' makes the "
            "page landscape when the selected layer is wider than it is tall, "
            "portrait otherwise (falls back to the map extent if no layer is "
            "selected).<br>"
            "<b>Fit map to selected layer</b>: zooms the map to the selected "
            "layer and resizes the map frame (centered in its space) to the "
            "layer's shape, so the layer just fits. Falls back to the canvas "
            "extent if no layer is selected.<br>"
            "<b>Extent</b>: an explicit extent overrides the fit; leave empty "
            "to use the selected layer or the canvas.<br>"
            "<b>Map item ID</b> (template only): the map to drive. If empty, an "
            "item with ID 'main_map' is used, otherwise the largest map.<br>"
            "<b>Lock layers</b>: freeze the map to the layers currently visible on "
            "the canvas.<br>"
            "<b>Export</b>: optionally export to PDF or an image (format follows "
            "the file extension)."
        )

    def flags(self):
        # Layouts and the GUI must be touched from the main thread.
        try:
            return super().flags() | Qgis.ProcessingAlgorithmFlag.NoThreading
        except AttributeError:  # QGIS < 3.36
            return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    # ------------------------------------------------------------ parameters
    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile(
            self.TEMPLATE, self.tr("Layout template (leave empty for default layout)"),
            extension="qpt", optional=True))

        self.addParameter(QgsProcessingParameterString(
            self.NAME, self.tr("Layout name (default: title or template name)"),
            optional=True))

        self.addParameter(QgsProcessingParameterString(
            self.TITLE, self.tr("Title (default: selected layer name)"),
            optional=True))

        self.addParameter(QgsProcessingParameterEnum(
            self.PAGE_SIZE, self.tr("Page size (default layout)"),
            options=[p[0] for p in PAGE_SIZES], defaultValue=0))

        self.addParameter(QgsProcessingParameterEnum(
            self.ORIENTATION, self.tr("Orientation (default layout)"),
            options=ORIENTATIONS, defaultValue=0))

        self.addParameter(QgsProcessingParameterBoolean(
            self.FIT_TO_LAYER,
            self.tr("Fit map to selected layer (resize map frame to the layer's shape)"),
            defaultValue=True))

        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Extent (overrides fit; default: current map canvas)"),
            optional=True))

        self.addParameter(QgsProcessingParameterString(
            self.MAP_ID, self.tr("Map item ID (template only)"),
            optional=True))

        self.addParameter(QgsProcessingParameterBoolean(
            self.LOCK_LAYERS, self.tr("Lock map to current canvas layers"),
            defaultValue=False))

        self.addParameter(QgsProcessingParameterBoolean(
            self.OPEN_DESIGNER, self.tr("Open layout designer when done"),
            defaultValue=True))

        self.addParameter(QgsProcessingParameterFileDestination(
            self.EXPORT, self.tr("Export layout to"),
            fileFilter="PDF (*.pdf);;PNG (*.png);;JPEG (*.jpg *.jpeg);;TIFF (*.tif *.tiff)",
            optional=True, createByDefault=False))

        self.addOutput(QgsProcessingOutputString(
            self.LAYOUT_NAME, self.tr("Layout name")))

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _unique_name(manager, base):
        name, i = base, 1
        while manager.layoutByName(name):
            i += 1
            name = f"{base} ({i})"
        return name

    @staticmethod
    def _pick_map(layout, map_id):
        maps = [i for i in layout.items() if isinstance(i, QgsLayoutItemMap)]
        if not maps:
            return None
        wanted = map_id or "main_map"
        for m in maps:
            if m.id() == wanted:
                return m
        if map_id:
            raise QgsProcessingException(
                f"No map item with ID '{map_id}' in the template. "
                f"Available: {', '.join(m.id() or '(no id)' for m in maps)}")
        return max(maps, key=lambda m: m.rect().width() * m.rect().height())

    @staticmethod
    def _active_layer():
        layer = iface.activeLayer() if iface else None
        return layer if (layer is not None and layer.isValid()) else None

    def _layer_extent(self, crs, project, feedback):
        """Selected layer's extent in `crs`, or None if unusable."""
        layer = self._active_layer()
        if layer is None:
            return None
        try:
            le = QgsRectangle(layer.extent())
            if layer.crs().isValid() and layer.crs() != crs:
                le = QgsCoordinateTransform(
                    layer.crs(), crs, project).transformBoundingBox(le)
            if not le.isEmpty() and le.width() > 0 and le.height() > 0:
                return le
            feedback.pushInfo(f"Layer '{layer.name()}' has an empty or zero-size extent.")
        except Exception as e:  # bad CRS etc.
            feedback.pushInfo(f"Could not use selected layer extent ({e}).")
        return None

    def _orientation(self, choice, crs, fallback_extent, project, feedback):
        """Return True for landscape."""
        if choice == 1:
            return False
        if choice == 2:
            return True

        ext = self._layer_extent(crs, project, feedback)
        source = f"layer '{self._active_layer().name()}'" if ext else "map extent"
        if ext is None:
            ext = fallback_extent

        landscape = ext.width() >= ext.height()
        feedback.pushInfo(
            f"Orientation: {'landscape' if landscape else 'portrait'} (from {source}).")
        return landscape

    @staticmethod
    def _fit_frame_to_extent(map_item, extent):
        """Shrink the map frame (centered in its current box) so its shape
        matches `extent` exactly, leaving no empty space around the data."""
        layout = map_item.layout()
        units = layout.units()
        pos = layout.convertToLayoutUnits(map_item.positionWithUnits())
        size = layout.convertToLayoutUnits(map_item.sizeWithUnits())
        box_w, box_h = size.width(), size.height()

        aspect = extent.width() / extent.height()
        if box_w / box_h > aspect:
            new_w, new_h = box_h * aspect, box_h
        else:
            new_w, new_h = box_w, box_w / aspect

        map_item.attemptResize(QgsLayoutSize(new_w, new_h, units))
        map_item.attemptMove(QgsLayoutPoint(
            pos.x() + (box_w - new_w) / 2, pos.y() + (box_h - new_h) / 2, units))

    @staticmethod
    def _set_label_font(label, size, bold=False):
        font = QFont()
        font.setPointSizeF(size)
        font.setBold(bold)
        try:  # QGIS >= 3.24
            fmt = QgsTextFormat()
            fmt.setFont(font)
            fmt.setSize(size)
            label.setTextFormat(fmt)
        except AttributeError:
            label.setFont(font)

    def _build_default_layout(self, layout, page_w, page_h, title):
        """Build title, map, legend, north arrow and frame.

        Returns (map_item, scalebar_slot) — the scale bar is added after the
        map extent is set, because its size depends on the map scale.
        """
        margin, pad, gap, title_h = 10.0, 5.0, 5.0, 12.0

        layout.pageCollection().page(0).setPageSize(QgsLayoutSize(page_w, page_h, MM))

        # Frame around all other elements
        frame = QgsLayoutItemShape(layout)
        frame.setShapeType(QgsLayoutItemShape.Rectangle)
        frame.setSymbol(QgsFillSymbol.createSimple({
            "color": "255,255,255,0",
            "outline_color": "0,0,0,255",
            "outline_style": "solid",
            "outline_width": "0.6",
            "outline_width_unit": "MM",
        }))
        frame.setId("frame")
        layout.addLayoutItem(frame)
        _place(frame, margin, margin, page_w - 2 * margin, page_h - 2 * margin)

        x0, y0 = margin + pad, margin + pad
        x1, y1 = page_w - margin - pad, page_h - margin - pad

        # Title
        label = QgsLayoutItemLabel(layout)
        label.setId("title")
        label.setText(title)
        self._set_label_font(label, 20, bold=True)
        label.setHAlign(Qt.AlignHCenter)
        label.setVAlign(Qt.AlignVCenter)
        layout.addLayoutItem(label)
        _place(label, x0, y0, x1 - x0, title_h)
        top = y0 + title_h + gap

        # Map
        map_item = QgsLayoutItemMap(layout)
        map_item.setId("main_map")
        map_item.setFrameEnabled(True)
        layout.addLayoutItem(map_item)

        arrow_w, arrow_h, sb_h = 15.0, 20.0, 12.0
        if page_w > page_h:
            # Landscape: map left, side panel right
            panel_w = 65.0
            _place(map_item, x0, top, x1 - x0 - panel_w - gap, y1 - top)
            px = x1 - panel_w
            legend_pos = (px, top)
            arrow_pos = (px + (panel_w - arrow_w) / 2, y1 - sb_h - gap - arrow_h)
            scalebar_slot = (px, y1 - sb_h, panel_w, sb_h)
        else:
            # Portrait: map top, panel bottom (legend left, arrow + scale bar right)
            panel_h = 55.0
            _place(map_item, x0, top, x1 - x0, y1 - top - panel_h - gap)
            py = y1 - panel_h
            half = (x1 - x0) / 2
            legend_pos = (x0, py)
            arrow_pos = (x0 + half + (half - arrow_w) / 2, py)
            scalebar_slot = (x0 + half, y1 - sb_h, half, sb_h)

        # Legend
        legend = QgsLayoutItemLegend(layout)
        legend.setId("legend")
        legend.setTitle("Legend")
        layout.addLayoutItem(legend)
        legend.setLinkedMap(map_item)
        legend.setLegendFilterByMapEnabled(True)  # only what's in the map
        legend.setFrameEnabled(True)
        legend.setFrameStrokeColor(QColor(0, 0, 0))
        legend.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, MM))
        legend.setBoxSpace(2.0)  # padding between frame and legend text
        _place(legend, *legend_pos)

        # North arrow
        arrow = QgsLayoutItemPicture(layout)
        arrow.setId("north_arrow")
        arrow.setPicturePath(NORTH_ARROW_SVG)
        layout.addLayoutItem(arrow)
        _place(arrow, arrow_pos[0], arrow_pos[1], arrow_w, arrow_h)
        arrow.setLinkedMap(map_item)  # rotates with the map

        return map_item, scalebar_slot

    @staticmethod
    def _add_scalebar(layout, map_item, slot):
        x, y, w, h = slot
        font_pt = 8.0

        sb = QgsLayoutItemScaleBar(layout)
        sb.setId("scale_bar")
        sb.applyDefaultSettings()
        sb.setStyle("Single Box")
        sb.setLinkedMap(map_item)
        try:
            sb.applyDefaultSize(sb.guessUnits())  # picks m / km etc. + unit label
        except AttributeError:
            sb.applyDefaultSize()
        layout.addLayoutItem(sb)

        # Smaller, consistent label font
        font = QFont()
        font.setPointSizeF(font_pt)
        try:  # QGIS >= 3.14
            fmt = sb.textFormat()
            fmt.setFont(font)
            fmt.setSize(font_pt)
            sb.setTextFormat(fmt)
        except AttributeError:
            sb.setFont(font)

        # Whole segments only (no left/"0" subdivisions), and let QGIS choose a
        # round segment length that makes the bar fit a width range.
        sb.setNumberOfSegmentsLeft(0)
        try:
            fit_mode = Qgis.ScaleBarSegmentSizeMode.FitWidth
        except AttributeError:
            fit_mode = QgsScaleBarSettings.SegmentSizeFitWidth
        sb.setSegmentSizeMode(fit_mode)

        def text_mm(s):
            # Approximate rendered width of a label in mm at font_pt
            return len(s) * font_pt * 0.3528 * 0.62

        def num_label(v):
            return f"{v:,.10g}"

        def fit(segs, min_w, max_w):
            sb.setNumberOfSegments(segs)
            sb.setMinimumBarWidth(min_w)
            sb.setMaximumBarWidth(max_w)
            try:
                sb.resizeToMinimumWidth()  # size now includes all label text
            except AttributeError:
                pass
            return sb.sizeWithUnits()

        # Keep a safety margin so end labels never touch the outer frame,
        # and leave room for the unit label after the last number.
        safety = 4.0
        fit_w = w - 2 * safety
        max_bar = fit_w - text_mm(sb.unitLabel() or "km") - 4.0
        segs = 2

        for _ in range(8):
            size = fit(segs, max_bar * 0.5, max_bar)
            # Each segment must be wider than the longest number + a gap,
            # otherwise neighbouring labels collide.
            longest = num_label(sb.unitsPerSegment() * segs)
            need_bar = segs * (text_mm(longest) + 3.0)
            if need_bar > max_bar and segs > 1:
                segs -= 1
                continue
            if need_bar > max_bar * 0.5:
                size = fit(segs, min(need_bar, max_bar), max_bar)
            if size.width() > fit_w:  # still too wide overall: shrink and retry
                max_bar -= size.width() - fit_w + 1.0
                continue
            break

        # Center horizontally, then clamp inside the slot (with the margin);
        # bottom-align to the slot so it can't drop below the inner edge.
        left_limit, right_limit = x + safety, x + w - safety
        px = x + (w - size.width()) / 2
        px = min(max(px, left_limit), right_limit - size.width())
        px = max(px, left_limit)
        py = y + h - size.height()  # bottom edge sits on the slot's bottom
        _place(sb, px, py)
        return sb

    # ---------------------------------------------------------------- run
    def processAlgorithm(self, parameters, context, feedback):
        project = context.project()
        manager = project.layoutManager()
        canvas = iface.mapCanvas() if iface else None

        template = self.parameterAsFile(parameters, self.TEMPLATE, context)
        name = self.parameterAsString(parameters, self.NAME, context).strip()
        title = self.parameterAsString(parameters, self.TITLE, context).strip()
        page_idx = self.parameterAsEnum(parameters, self.PAGE_SIZE, context)
        orient_idx = self.parameterAsEnum(parameters, self.ORIENTATION, context)
        map_id = self.parameterAsString(parameters, self.MAP_ID, context).strip()
        lock_layers = self.parameterAsBool(parameters, self.LOCK_LAYERS, context)
        fit_to_layer = self.parameterAsBool(parameters, self.FIT_TO_LAYER, context)
        open_designer = self.parameterAsBool(parameters, self.OPEN_DESIGNER, context)
        export_path = self.parameterAsFileOutput(parameters, self.EXPORT, context)

        # CRS: canvas if available, else project
        crs = canvas.mapSettings().destinationCrs() if canvas else project.crs()

        # Extent priority: user-supplied > selected layer (if fitting) > canvas
        extent_given = parameters.get(self.EXTENT) not in (None, "")
        fit_frame = False
        layer_ext = (self._layer_extent(crs, project, feedback)
                     if fit_to_layer and not extent_given else None)
        if extent_given:
            extent = self.parameterAsExtent(parameters, self.EXTENT, context, crs)
        elif layer_ext is not None:
            extent = QgsRectangle(layer_ext)
            extent.scale(1.0 + 2 * FIT_MARGIN)  # small margin around the data
            fit_frame = True
            feedback.pushInfo(f"Fitting map to layer '{self._active_layer().name()}'.")
        elif canvas:
            extent = canvas.extent()
            if fit_to_layer:
                feedback.pushInfo("No usable selected layer; using the canvas extent.")
        else:
            raise QgsProcessingException(
                "No map canvas available; please provide an extent.")

        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        scalebar_slot = None

        if template:
            # --- From template
            with open(template, encoding="utf-8") as f:
                content = f.read()
            doc = QDomDocument()
            res = doc.setContent(content)
            if not (res[0] if isinstance(res, tuple) else res):
                raise QgsProcessingException(f"Could not parse template: {template}")
            _items, ok = layout.loadFromTemplate(doc, QgsReadWriteContext())
            if not ok:
                raise QgsProcessingException("QGIS failed to load the layout template.")

            if title:
                for item in layout.items():
                    if isinstance(item, QgsLayoutItemLabel) and item.id() == "title":
                        item.setText(title)

            main_map = self._pick_map(layout, map_id)
            base = name or os.path.splitext(os.path.basename(template))[0]
        else:
            # --- Built-in default layout
            layer = self._active_layer()
            if not title:
                title = (layer.name() if layer else "") or project.title() or "Map"
            landscape = self._orientation(orient_idx, crs, extent, project, feedback)
            _label, pw, ph = PAGE_SIZES[page_idx]
            page_w, page_h = (ph, pw) if landscape else (pw, ph)
            main_map, scalebar_slot = self._build_default_layout(
                layout, page_w, page_h, title)
            base = name or title

        layout.setName(self._unique_name(manager, base))

        # --- Drive the map item
        if main_map is None:
            feedback.reportError("Template has no map item; layout created without a map.")
        else:
            main_map.setCrs(crs)
            if fit_frame:
                main_map.setMapRotation(0)  # fitting assumes north-up
                self._fit_frame_to_extent(main_map, extent)
            elif canvas and not extent_given:
                main_map.setMapRotation(canvas.rotation())

            main_map.setFollowVisibilityPreset(False)
            main_map.setKeepLayerSet(lock_layers)
            if lock_layers:
                layers = (canvas.layers() if canvas
                          else [l.layer() for l in project.layerTreeRoot().findLayers()
                                if l.isVisible() and l.layer()])
                main_map.setLayers(layers)
            else:
                main_map.setLayers([])

            main_map.zoomToExtent(extent)
            main_map.refresh()
            feedback.pushInfo(f"Map item '{main_map.id() or '(no id)'}' set to extent "
                              f"{extent.toString(2)} ({crs.authid()}).")

            if scalebar_slot:
                self._add_scalebar(layout, main_map, scalebar_slot)

        manager.addLayout(layout)
        feedback.pushInfo(f"Created layout '{layout.name()}'.")

        # --- Optional export
        if export_path:
            exporter = QgsLayoutExporter(layout)
            if export_path.lower().endswith(".pdf"):
                result = exporter.exportToPdf(
                    export_path, QgsLayoutExporter.PdfExportSettings())
            else:
                result = exporter.exportToImage(
                    export_path, QgsLayoutExporter.ImageExportSettings())
            if result != QgsLayoutExporter.Success:
                raise QgsProcessingException(
                    f"Export failed ({exporter.errorMessage() or result}).")
            feedback.pushInfo(f"Exported to {export_path}")

        if open_designer and iface:
            iface.openLayoutDesigner(layout)

        return {self.LAYOUT_NAME: layout.name(), self.EXPORT: export_path or None}
