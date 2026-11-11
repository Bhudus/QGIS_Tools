# Depressions and Laylines (QGIS plugin)

Maps the depressions and laylines (flow channels) in a field from a LiDAR DEM, for **subsurface (tile) drainage** design:

- **Depressions** – where water ponds: candidate spots for surface inlets, extra laterals or closer spacing.
- **Laylines** – where water runs: the natural routes for mains and submains, and the outlets the field drains to.
- **Shaded, colored DEM** – green low ground to brown high ground over a hillshade.
- **Contours** – the lay of the land, at 1 ft by default, for planning grades and lateral directions.

## Requirements
- QGIS 3.22 or newer. Nothing else (no SAGA or GRASS).
- A bare-earth DEM of the field. The **LiDAR Field DEM** plugin builds one from ISGS or USGS 3DEP LiDAR, with the filled DEM as well.

## Install
1. Download [`depressions_laylines.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/depressions_laylines/depressions_laylines.zip).
2. In QGIS: **Plugins → Manage and Install Plugins… → Install from ZIP**, choose the zip, click **Install Plugin**.
3. Click the toolbar button, or **Raster → Depressions and Laylines → Depressions and laylines for a field…**, or Processing Toolbox → *Depressions and Laylines → Subsurface drainage*.

## Use
1. In LiDAR Field DEM, build the DEM with **Also produce a DEM with sinks filled (Wang & Liu)** ticked. Keep the default 100 ft *Extend DEM beyond field*, so water from outside the field is counted.
2. Run **Depressions and laylines for a field** with the **DEM**, the **Filled DEM** and the **Field boundary**.

| Output | What it is |
|---|---|
| **Filled DEM** (only when none is supplied) | The DEM with sinks filled here by the Wang & Liu method, over the whole DEM, added to the map so it can be supplied as the Filled DEM on later runs. Not written when a filled DEM is supplied. |
| **Depression depth** | Filled DEM − unfilled DEM, in feet, inside the field. |
| **Depressions** | Polygons of the cells deeper than the threshold (default **0.75 ft**). Cells touching at edges or corners are one depression. Fields: `area_ac`, `max_dep_ft`, `mean_dep_ft`, `vol_cu_yd` (storage within the polygon), `pond_elev` (filled-DEM level), `cells`. Numbered largest volume first. |
| **Laylines** | The flow channels within the field: cells draining at least the contributing-area threshold (default **2 acres**), joined into lines between channel heads, junctions and outlets, clipped to the field. Fields: `strahler` (stream order), `ac_top` / `ac_bottom` (contributing acres at each end), `length_ft`. Drawn thicker with higher order. |
| **Contours** (optional, on by default) | Contour lines of the filled DEM at the contour interval (default **1 ft**), clipped to the field, so they show the surface water follows: depressions appear as flat pond levels instead of closed rings. Fields: `elev_ft`, `elev` (DEM units), `is_index`. Every 5th is an index contour, drawn heavier and labeled with its elevation. Untick the output to skip it. |
| **Contributing area** (optional) | Acres draining through each cell. |

How it works: water is routed downhill (D8, steepest descent) over the filled DEM, re-filled with a tiny gradient so flats and filled depressions drain toward their spill point; contributing area is accumulated from high to low, and Strahler order is carried down the network. Routing uses the whole DEM, the field plus its margin. Pure Python + NumPy, so it needs no SAGA or GRASS.

| Parameter | Default | Notes |
|---|---|---|
| DEM | – | The unfilled bare-earth DEM. |
| Filled DEM | blank | Must be on the same grid as the DEM (the same LiDAR Field DEM run). Blank = fill here with Wang & Liu. |
| Field boundary | blank | Blank = the whole DEM. |
| DEM elevation units | Feet (US) | Thresholds and results are always in feet and acres. |
| Depression depth threshold | 0.75 ft | Cells deeper than this are depressions. |
| Minimum depression area | 0 ac | Drop tiny depressions, e.g. 0.05 ac. |
| Layline contributing area threshold | 2 ac | Lower it for more, shorter laylines; raise it to see only the main flow paths (routes for mains). |
| Minimum slope across filled sinks | 0.06° | Only used when the filled DEM is made here. A slope above 0 adds a small ramp across filled areas, which adds a little to the depth. |
| Contour interval | 1 ft | Smaller intervals on flat ground (e.g. 0.5 ft); larger on rolling ground. |
| Index contour every | 5 | Every nth contour is heavier and labeled; 0 = none. |
| DEM color scheme | Hypsometric | Colors the DEM layer in the map when the tool finishes. **Hypsometric**: green on low ground through yellow and tan to brown on high ground, so blue is left for the depressions and laylines. Also **Greens**, **Tan / brown** (single hue, easier for color-blind viewers) or **Don't color the DEM**. Colors are stretched over the field's elevations (2nd to 98th percentile), not the whole DEM, so the margin and odd edge cells don't wash out the field. |
| Add a hillshade over the DEM colors | on | Adds *Hillshade - <DEM>*, a shaded-relief layer on the same file just above the DEM, multiplied at 45% with 3x vertical exaggeration, to show micro-relief: swales, old furrows, tile ridges. Re-running reuses it. |
| Smooth laylines | on | Rounds off the cell-to-cell stair steps. |

## Notes for drainage design
- Depression depth is relative to the spill (overflow) level: a 0.75 ft threshold maps the parts of each depression where water would stand more than 9 in deep before it overflows.
- `vol_cu_yd` is the storage within each depression polygon, a guide to how much runoff a surface inlet will need to pass.
- Laylines pass through filled depressions to their spill point, so a layline leaving a depression marks where it overflows.

## Issues
Report bugs or ideas at <https://github.com/Bhudus/QGIS_Tools/issues>.
