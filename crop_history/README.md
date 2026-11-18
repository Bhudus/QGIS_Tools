# Crop History (QGIS plugin)

Crop history and rotation of fields from the USDA NASS **Cropland Data Layer**
(CDL), downloaded year by year from the CropScape web service.

This covers the *crops* part of the background-information step of drainage
design. It also gives the crop inputs for return-on-investment and DRAINMOD
studies.

Requires QGIS 3.34 LTR or newer, and an internet connection. Uses only QGIS,
GDAL and numpy.

## Install

1. Download [`crop_history.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/crop_history/crop_history.zip).
2. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**, then pick
   the zip. (Enable *Show also experimental plugins* under Settings if QGIS hides it.)
3. Run it from the toolbar button, **Vector → Tile Drainage**, **Raster → Tile
   Drainage**, **Plugins ▸ Crop History**, or the Processing Toolbox. From Python:

```python
processing.run("crophistory:crophistory", {
    "FIELD": "fields.gpkg", "NAME_FIELD": "name", "START_YEAR": 2015, "END_YEAR": 0,
    "FIELDS_OUT": "TEMPORARY_OUTPUT", "HISTORY": "TEMPORARY_OUTPUT",
    "ROTATION": "TEMPORARY_OUTPUT", "CDL_LATEST": "TEMPORARY_OUTPUT"})
```

## Inputs

| Parameter | Notes |
|---|---|
| Field boundaries | Polygon layer, any coordinate system, one or many fields. |
| Field name attribute | Optional. Default: `Field <id>`. |
| First year | Default 2015. The CDL covers all of the lower 48 states from 2008. Some states start earlier, as far back as 1997. |
| Last year | 0 (default) means the latest year CropScape has. The tool tries this year, then earlier years, until one is found. |
| Area units | Acres or hectares. |
| Reuse downloaded CDL files | Advanced, on by default. |

## How it works

1. **Grouping and download.** Fields are grouped into download areas of up to 10 km × 10 km. For each area and year, `GetCDLFile` is asked for the CDL inside the area's box (in CONUS Albers, EPSG:5070) plus 60 m. The GeoTIFF is downloaded through QGIS's network settings (proxy, SSL). Files are cached in the QGIS profile folder under `cache/crop_history`.
2. **Common grid.** Each area's years are put on one grid at the finest resolution among them, by nearest neighbour. The CDL is 30 m before 2024 and 10 m from 2024.
3. **Per year.** Clouds/no data (code 81) and blank cells are left out of every share. For each field and year the tool finds:
   - the main crop and its share of the field;
   - the second crop;
   - every crop over 1%.
4. **Per field:**
   - the **sequence** of main crops, one letter per year;
   - the **rotation**;
   - the most common crop;
   - the years in corn, soybeans, small grain, hay, other crops and non-crop. A double crop such as winter wheat/soybeans counts as both.
5. **Rotation classes**, from the share of years in each crop group. The first rule that fits wins:

| Rotation | Rule |
|---|---|
| Mostly non-cropland | crops in under half the years |
| Continuous corn | corn ≥ 75% of years |
| Continuous soybean | soybeans ≥ 75% |
| Hay / forage | hay ≥ 50% |
| Row crops with hay | hay ≥ 20%, with some corn or soybeans |
| Corn-soybean-small grain | small grain ≥ 15%, corn and soybeans present, together ≥ 75% |
| Corn-soybean | corn + soybeans ≥ 75%, each ≥ 20% |
| Other cropping | anything else |

The field's rotation comes from its sequence of main crops. The rotation map
applies the same rules to every cell.

## Outputs

**Field crop history** (polygons, styled by rotation and labelled with the
sequence):

| Field | Meaning |
|---|---|
| `field`, `area` | Name; area from the boundary, in acres or hectares. |
| `years` | Years with data. |
| `rotation`, `sequence`, `main_crop` | Rotation class; main-crop letters, one per year; most common crop. |
| `corn_yrs`, `soy_yrs`, `grain_yrs`, `hay_yrs`, `other_yrs`, `noncrop_yrs` | Years with each crop group as the main crop. |
| `crop_pct` | Average share of the field in crops (not grass, woods, water or developed land). |
| `uniform_pct` | Average share of the field in that year's main crop. Low values mean the field is farmed in parts, or its edges are mixed. |
| `rot_pct` | Share of the field's cells whose own rotation matches the field's. |
| `flag` | Field split between rotations (under 70%), split crops in named years (main crop under 70%), years with no data. |
| `y2015` … | That year's main crop and its share, e.g. `Corn (96%)`. |

**Crops by year** (table): field, year, crop, crop_pct, crop_area, second,
second_pct, crops (every crop over 1%, e.g. `Corn 61%; Soybeans 35%`),
data_pct (share of the field with data).

**Rotation map** (raster): the rotation of every cell inside the fields, in
CONUS Albers.

**Latest Cropland Data Layer** (raster): the newest year over each download
area, with the official CDL colours and class names.

Sequence letters: C corn, S soybeans, W wheat, B barley, O oats, G other small
grains, A alfalfa, H hay, Sg sorghum, Ct cotton, R rice, F fallow, P
grass/pasture, X other crop, N non-crop, x/y double crop, - no data.

## Notes

- The CDL is a satellite classification. It is most reliable for major crops in large fields; field edges, waterways and small fields are less reliable. NASS publishes accuracy by state and crop in the CDL metadata. Read `uniform_pct` and the rotation map before trusting a field's rotation.
- This tool uses the CropScape service, which covers the lower 48 states.
- To get the files again after a CDL revision, turn off *Reuse downloaded CDL files*, or delete the cache folder.
- Settings are kept while the project stays open, layers included. A new or different project goes back to the defaults, as does **Plugins ▸ Crop History ▸ Reset saved settings to defaults**.
- Source: USDA National Agricultural Statistics Service Cropland Data Layer, via CropScape (George Mason University). The data are public domain; NASS asks to be acknowledged.

## Testing

`test/run_synthetic.py` checks the field histories and rotation classes on a
synthetic CDL stack: halves in corn-soybean and continuous corn, a
corn-soybean-wheat corner, a waterway, a woodlot, clouds and a missing year.
