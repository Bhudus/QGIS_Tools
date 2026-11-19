# Field Run-on (QGIS plugin)

Maps the **run-on areas** of a field: the land outside it whose surface water flows
into it. Run-on is extra water that the field's surface inlets, waterways and drainage
outlet have to carry, so it belongs in the background information of a drainage design.

Requires QGIS 3.34 LTR or newer. Uses only QGIS, GDAL and numpy.

## Install

1. Download [`field_runon.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/field_runon/field_runon.zip).
2. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**, then pick
   the zip. (Enable *Show also experimental plugins* under Settings if QGIS hides it.)
3. Run it from the toolbar button, **Vector → Tile Drainage**, **Raster → Tile
   Drainage**, **Plugins ▸ Field Run-on**, or the Processing Toolbox. From Python:

```python
processing.run("fieldrunon:fieldrunon", {
    "FIELD": "field.gpkg", "DEM": "watershed_dem.tif", "Z_UNITS": 2,
    "WATERSHED": "globalwatershed.shp", "SPLIT": 0, "MIN_AREA": 0.5, "SMOOTH": True,
    "AREAS": "TEMPORARY_OUTPUT", "RASTER": "TEMPORARY_OUTPUT",
    "INLETS": "TEMPORARY_OUTPUT"})
```

## Inputs

| Parameter | Notes |
|---|---|
| Field boundary | Polygons. Selected features are honoured. Each field is done on its own. |
| Field name attribute | Optional; names the fields in the outputs (else the feature id). |
| DEM | A DEM of the field's **watershed**, in a projected CRS: run-on can come from far upslope. A LiDAR DEM (for example from the LiDAR Field DEM plugin, extended well beyond the field) or a USGS 3DEP DEM (for example the one the Drainage Outlet plugin downloads). |
| DEM elevation units | Metres, international feet or US survey feet (default). |
| Watershed | Optional polygon, for example from the StreamStats Delineator. The analysis is limited to it. |
| Split the run-on | **By inlet** (default): one area for each place water enters the field, as a concentrated inlet or a stretch of sheet flow. **Connected areas**: 8-connected groups of run-on land. |
| An inlet carries at least | Default 2 ac (as the laylines of Depressions and Laylines). Boundary cells carrying at least this much water are concentrated inlets; the stretches of boundary between inlets take sheet flow. |
| Smallest run-on area to keep | Default 0.5 ac. Smaller areas are left off the maps but counted in the total. |
| Smooth the boundaries | On by default. The polygons are simplified (to 0.6 cell) and smoothed (Chaikin), then clipped so they never overlap the field. The areas reported are those of the DEM cells, not of the smoothed outline. |
| Smoothing passes | Advanced. Default 3. |
| Most DEM cells to route | Advanced. Default 6,000,000. Larger DEMs are resampled (block mean) to coarser cells for routing. |

## Outputs

**Run-on areas** (polygons), labeled R1, R2, … with their acres, largest first:

| Field | Meaning |
|---|---|
| `field` | Field name or id |
| `runon_id` | Area number, 1 = largest, for each field |
| `kind` | `inlet` (enters at a concentrated inlet), `sheet` (enters as sheet flow along a stretch of boundary), or `connected` |
| `area_ac`, `area_ha` | Area of the run-on (DEM cells) |
| `pct_field` | Run-on area as a % of the field area |
| `open` | 1 where the area reaches the edge of the DEM or its no-data: it may go on beyond, so its area is a minimum |
| `inlet_ac` | Acres draining through the area's main inlet |
| `n_inlet` | Boundary cells where the area's water crosses into the field |
| `field_ac` | Field area (DEM cells) |

**Run-on area raster**: each run-on cell holds the area (acres) of the run-on area it
belongs to; no data elsewhere. Styled yellow to red. For several fields, a cell draining
to more than one shows the larger area.

**Run-on inlets** (points, optional): the boundary cell outside the field that carries the
most of each area's water in, with `kind`, `inlet_ac` and `area_ac`. Concentrated inlets
(red triangles) are where to look for a waterway, a grade-control structure or a surface
inlet; for sheet flow (orange circles) the point is just the busiest cell of the stretch.

## Method

1. The DEM is filled by Wang & Liu (2006), then re-filled with a tiny gradient so flats
   and filled depressions drain toward their spill point: every cell has a downhill
   neighbour. (Depressions and Laylines and Design Reports route water the same way.)
2. D8 flow directions: each cell drains to its steepest downhill neighbour.
3. Cells are visited from low to high. A cell outside the field is run-on when its
   receiver is in the field or is itself run-on.
4. **By inlet**: the cells just outside the field that drain straight into it are where
   water crosses in. Those carrying at least the inlet acres are concentrated inlets
   (touching ones form one inlet); the others, between inlets, are grouped into stretches
   of sheet flow. Each run-on cell takes the inlet or stretch its flow path reaches.
   **Connected**: run-on cells are grouped by 8-connectivity.
5. Areas are counted in DEM cells, polygonized and (optionally) smoothed.

## Tips

* Use a DEM that covers the whole watershed. Run-on that reaches the edge of the DEM is
  flagged *open*.
* LiDAR shows road ditches, culverts and terraces that intercept or pass run-on; coarse
  DEMs do not. A culvert under a road shows as a dam in a LiDAR DEM unless the DEM was
  hydro-flattened or breached there.
* Surface flow only: tile and other subsurface flow are not traced.

License: GPL-3.0-or-later. `core/sinkfill.py` and `core/terrain.py` are from the
Depressions and Laylines plugin.
