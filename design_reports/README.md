# Design Reports (QGIS plugin)

Two tools that write Word (.docx) reports from the Illinois design templates, which
ship with the plugin:

* **Drainage system design report** (Drainage System Design template)
* **Subirrigation design report** (Subirrigation Design template)

Menu: **Vector / Raster → Tile Drainage**, the plugin menu, or Processing Toolbox →
Design Reports. The red instructions and example pictures of the templates are removed,
the tables and blanks filled, and the signature page kept.

## Inputs (both reports)

| Input | Used for |
|---|---|
| Field boundary | Lat/long, acres, every map |
| DEM (LiDAR, projected, extended beyond the field) + elevation units | Contours, depressions, run-on areas, laylines, lowest ground, ground where the network has no M |
| Drainage network (Illini Subsurface Drainage) + design units | System maps, pipe labels and schedule, components, depth limits, lateral spacing, D_COEFF, flowline elevations at the field boundary |
| Existing tile (optional) | Dashed on the background and system maps |
| Soils (SSURGO drainage soil map output) or Soil Data Access | Soil map and table; dominant soil and recommended spacing |
| Crop History fields, or text | Crops grown and planned rotation |
| Watershed, outlet, streams (StreamStats Delineator outputs) | Watershed map; outlet coordinates (subirrigation) |
| Depressions / Laylines (Depressions and Laylines outputs, optional) | Used as they are; otherwise found from the DEM |
| Text | County/township, background narrative, existing tile note, depth limits, drainage coefficient, materials, other considerations |

**Drainage only**: the four depth/spacing simulation charts saved from the Illinois
Drainage Guide county simulations spreadsheet (current and projected climate, corn and
soybeans; any image format), a note on county and soil, and the benchmark.

**Subirrigation only**: the Controlled Drainage Zones zones and structures, and the
irrigation coefficient (in/day).

## What is computed

* **Depressions**: cells of the field deeper than 0.75 ft below the Wang & Liu filled
  surface (8-connected), labeled D1, D2… with acres, maximum depth and storage.
* **Run-on areas**: land outside the field whose D8 flow path (over the filled surface)
  enters the field, labeled R1, R2… with acres. If a run-on area reaches the edge of the
  DEM it is flagged ("extends beyond the DEM") — extend the DEM to map all of it.
* **Laylines**: D8 flow channels draining at least 2 acres (as in Depressions and
  Laylines), drawn on the topographic map.
* **Depth limits**: entered, or from the design — upper/lower limit = shallowest/deepest
  lateral, absolute = shallowest/deepest tile anywhere.
* **Pipe labels and schedule**: size, length to the nearest 10 ft, slope to the nearest
  0.05 % (end tile elevations over length), depth range.
* **Irrigated area** (subirrigation): tile buffered by half the lateral spacing, or by a
  third of the recommended spacing when the laterals are more than two thirds of the
  recommended spacing apart (dominant soil under the tile, `spacing_ft`).
* **Subirrigation zones**: the Controlled Drainage Zones zones with acres and minimum
  elevation (lowest ground, 3×3 mean).
* **Zonal overlay maps**: per structure, the impacted area = its zone within the
  irrigated area; when the next structure upstream is set more than 1 ft higher, only the
  ground within 1 ft above the control elevation. Control elevation = lowest ground in it.
* **DC ≥ IC check**: a warning (and a note under the table) when the drainage coefficient
  is below the irrigation coefficient.

## Maps

6.5 in wide with scale bar, north arrow and legend, at standard engineering scales. The
system and topographic maps share sheets: one page when the field fits at the largest
scale (Advanced: 1:3,600 drainage, 1:2,400 subirrigation), otherwise several sheets at
that scale. Location and watershed maps use OpenStreetMap or Esri imagery (Advanced; needs
internet); the background map uses Esri imagery.

## Notes

* Elevations are in feet, on the DEM's vertical datum.
* Settings are kept while the project is open and reset when a new project is started or
  another one is loaded (or from the plugin menu).
* Review the report in Word before submitting it.

License: GPL-3.0-or-later. `core/sinkfill.py` and `core/terrain.py` are from the
Depressions and Laylines plugin.
