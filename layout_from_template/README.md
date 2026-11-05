# Layout from Template (QGIS plugin)

Creates a print layout of the current map in one step. Use your own print layout template (`.qpt`), or leave the template blank to get a ready-made layout with a title, legend, scale bar, north arrow and a frame around everything.

## Install

1. Download [`layout_from_template.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/layout_from_template/layout_from_template.zip).
2. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Choose `layout_from_template.zip` and click **Install Plugin**.
4. QGIS may warn that installing plugins from ZIP can be unsafe. Accept the warning to continue.

Requires QGIS 3.16 or newer.

## Use

1. Zoom the map to the area you want, and select the layer the map is about in the Layers panel.
2. Open the tool in any of these ways:
   - The layout toolbar button.
   - **Plugins → Layout from Template → Create Print Layout from Template…**
   - In the Processing Toolbox, **Layout from template → Create print layout from template**.
3. Pick a layout template, or leave it blank for the default layout.
4. Click **Run**. The new layout opens in the layout designer.

## Default layout (no template)

- **Title** at the top: the selected layer's name, or the text you enter under **Title**.
- **Map** with a thin border.
- **Legend** with a frame, listing only what is visible in the map.
- **North arrow** that rotates with the map.
- **Scale bar** with round values (for example 0 – 500 – 1,000 m), sized so its numbers don't overlap.
- **Frame** around all of the above, 10 mm in from the page edge.

**Orientation:** the page is landscape when the selected layer is wider than it is tall, portrait otherwise. You can also force portrait or landscape. In landscape the legend, north arrow and scale bar sit in a column to the right of the map; in portrait they sit in a strip below it.

**Page size:** A4, A3, Letter, Legal or Tabloid.

## Options

| Option | What it does |
|---|---|
| Layout template | A `.qpt` file. Leave blank for the default layout. |
| Layout name | Name in the Layout Manager. Defaults to the title or template name; a number is added if the name is taken. |
| Title | Title text. With a template, it replaces the text of a label whose item ID is `title`. |
| Page size, Orientation | Default layout only. |
| Fit map to selected layer | On by default. Zooms the map to the selected layer (with a 2% margin) and shrinks the map frame to the layer's shape, so the layer just fits. The map is drawn north-up. |
| Extent | An explicit extent. Overrides the fit. |
| Map item ID | Template only: which map to set. Defaults to an item with ID `main_map`, otherwise the largest map, so insets and overviews are left alone. |
| Lock map to current canvas layers | Freezes the map to the layers visible now. |
| Open layout designer when done | On by default. |
| Export layout to | Optional PDF, PNG, JPEG or TIFF, chosen by the file extension. |

With **Fit map to selected layer** off and no extent, the map shows the current map canvas view, with its CRS and rotation.

## Notes

- The tool runs on QGIS's main thread, because layouts must be created there. QGIS pauses briefly while it runs.
- Templates saved from another project often keep a locked layer list that doesn't exist in the current project. The tool clears it so the map shows your layers.
- A long legend can run into the north arrow in a landscape layout. Drag or resize the legend in the designer, or give it more than one column.
- From Python: `processing.run("layoutfromtemplate:layoutfromtemplate", {...})`.
