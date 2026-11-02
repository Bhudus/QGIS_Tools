# SSURGO Soil Map (QGIS plugin)

Turns a field boundary into a labeled soil map. It uses USDA-NRCS Soil Data Access, the database behind Web Soil Survey.

## Install

1. Download [`ssurgo_soil_map.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/ssurgo_soil_map/ssurgo_soil_map.zip).
2. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Choose `ssurgo_soil_map.zip` and click **Install Plugin**.
4. QGIS may warn that installing plugins from ZIP can be unsafe. Accept the warning to continue.

Requires QGIS 3.22 or newer and an internet connection.

## Use with a shapefile

1. Load your field boundary shapefile, or any polygon layer. The layer needs a CRS, which for a shapefile means a `.prj` file.
2. Open the tool in either of these ways:
   - **Vector → SSURGO Soil Map → Get Soil Map for Polygons…**
   - The soil-layers toolbar button.
   - In the Processing Toolbox, **SSURGO Soil Map → Soils → Get soil map for polygons (SSURGO)**.
3. Pick the input layer and choose what to label the soils with.
4. Under **Soil map**, click **…**, choose **Save to File**, and pick a `.shp` file name.
5. Click **Run**.

The output loads with a different color for each soil and a label on each polygon. A `.qml` file with the same name is saved next to the shapefile, so the colors and labels come back whenever the shapefile is opened.

## Trace a field on the map

1. Zoom to the field. Aerial imagery helps; add it with the QuickMapServices or XYZ Tiles browser entries.
2. Click **Trace Field and Get Soil Map** on the toolbar, or open it from **Vector → SSURGO Soil Map**.
3. Click each corner of the field. Right-click or press Enter to finish.
   - Backspace removes the last corner.
   - Esc starts over.
   - Snapping applies if you've turned it on.
4. A "Traced field" layer is added and the soil map tool opens with it already selected. Click **Run**.

The traced field is a temporary layer. To keep it, save the **Field boundary outline** output to a `.shp` file, or right-click the layer and choose **Make Permanent**.

## Field boundary outline

**Outline the field boundary on the soil map** is checked by default. It adds a **Field boundary outline** layer on top of the soils: a bold yellow-and-black line with an `acres` field. Uncheck it to get the soil map only.

## Output fields

| Field | Meaning |
|---|---|
| mukey, musym | Map unit key and map unit symbol |
| muname | Soil name, without the slope or flooding phase |
| compname | Dominant component (soil series) |
| drainage | Drainage class |
| hydgrp | Hydrologic soil group |
| acres | Geodesic acres of this soil polygon |

## Notes

- An internet connection is required. The plugin uses QGIS's network settings, so a proxy set in QGIS Options applies.
- Areas outside SSURGO coverage return no soils, and the log shows a warning.
- Soil Data Access can time out on very large polygons, roughly tens of thousands of acres. Split those first.
- Soil properties are for each map unit's dominant component, the same simplification Web Soil Survey's "dominant component" option uses.
