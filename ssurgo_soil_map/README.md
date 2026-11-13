# SSURGO Soil Map (QGIS plugin)

Turns a field boundary into a labeled soil map. It uses USDA-NRCS Soil Data Access, the database behind Web Soil Survey.

## Install

1. Download [`ssurgo_soil_map.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/ssurgo_soil_map/ssurgo_soil_map.zip).
2. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Choose `ssurgo_soil_map.zip` and click **Install Plugin**.
4. QGIS may warn that installing plugins from ZIP can be unsafe. Accept the warning to continue.

Requires QGIS 3.22 or newer and an internet connection.

The plugin is also listed under **Vector → Tile Drainage**, a menu shared by the drainage tools in this repository, as well as under **Vector → SSURGO Soil Map**.

## Use with a shapefile

1. Load your field boundary shapefile, or any polygon layer. The layer needs a CRS, which for a shapefile means a `.prj` file.
2. Open the tool in either of these ways:
   - **Vector → SSURGO Soil Map → Get Soil Map for Polygons…**
   - The soil-layers toolbar button.
   - In the Processing Toolbox, **SSURGO Soil Map → Soils → Get soil map for polygons (SSURGO)**.
3. Pick the input layer and choose what to label the soils with: the soil name, the map unit symbol, both, the series name only (e.g. Drummer), the drain spacing, soil name and drain spacing, the subirrigation spacing, or soil name and subirrigation spacing. Enter the drain depth and drainage coefficient (see [Drain spacing](#drain-spacing)).
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
| series | First word of the soil name, the series (e.g. Drummer, Sawmill) |
| compname | Dominant component (soil series) |
| drainage | Drainage class |
| hydgrp | Hydrologic soil group |
| acres | Geodesic acres of this soil polygon |
| ka_ftday | Ksat above the drains (ft/day), thickness-weighted from the surface to drain depth |
| kb_ftday | Ksat below the drains (ft/day), thickness-weighted from drain depth to the impermeable layer |
| barrier_ft | Depth to the impermeable layer (ft) |
| spacing_ft | Drain spacing (ft) from the Hooghoudt equation |
| ka_sub | Ksat (ft/day) of the saturated zone above the drains during subirrigation |
| subirr_ft | Subirrigation drain spacing (ft) from the inverse Hooghoudt equation |

## Drain spacing

Set **Drain depth (ft)** (default 3.5) and **Drainage coefficient (in/day)** (default 0.375, i.e. 3/8 in/day). The **Drain diameter** under Advanced Parameters (default 4 in) only affects the equivalent depth.

Spacing comes from the steady-state [Hooghoudt equation](https://en.wikipedia.org/wiki/Drainage_equation), with the water table midway between drains at the soil surface, so the head h equals the drain depth:

q L² = 8 Kb d h + 4 Ka h²

- q is the drainage coefficient and L is the drain spacing.
- Ka and Kb come from SSURGO `ksat_r` for the dominant component's horizons, thickness-weighted above and below drain depth.
- d is the equivalent depth. It is computed from L, the depth D of the impermeable layer below the drains and the drain radius, using van der Molen and Wesseling (1991). Because d depends on L, the equation is solved numerically.
- The impermeable layer is the shallowest SSURGO restrictive layer, such as bedrock, a fragipan or a dense till. Abrupt textural changes don't count. If there is no restrictive layer, the bottom of the soil description is used, usually 60 to 80 in.
- If the impermeable layer is at or above drain depth, D is 0. Spacing then uses only flow above the drains, and the log shows a warning.
- Soils with no Ksat data, such as water, pits or urban land, get no spacing.

## Subirrigation spacing

Set **Irrigation coefficient for subirrigation (in/day)** (default 0.25, typically the design peak ET), **Subirrigation water level at the drains** (default 2.0 ft) and **Subirrigation mid-drain water table** (default 1.5 ft). Both water levels are heights above the drains. The level at the drains must be higher than the mid-drain water table and no higher than the drain depth.

The spacing uses the inverse Hooghoudt equation. Water held at Hw in the drains flows out to the midplane, where the water table is Hm:

q L² = 8 Kb d (Hw − Hm) + 4 Ka (Hw² − Hm²)

- q is the irrigation coefficient.
- Kb, d and the impermeable layer are the same as for drainage.
- Ka (`ka_sub`) is averaged over the saturated zone above the drains, from Hw down to the drains, rather than from the surface.

Treat the results as a design starting point. SSURGO Ksat values are estimates for a soil series, not field measurements.

## Notes

- An internet connection is required. The plugin uses QGIS's network settings, so a proxy set in QGIS Options applies.
- Areas outside SSURGO coverage return no soils, and the log shows a warning.
- Soil Data Access can time out on very large polygons, roughly tens of thousands of acres. Split those first.
- Soil properties are for each map unit's dominant component, the same simplification Web Soil Survey's "dominant component" option uses.
