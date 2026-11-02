# LiDAR Field DEM (QGIS plugin)

Builds a bare-earth DEM for a field boundary from LiDAR ground points.

| Source | Coverage | How the data is fetched |
|---|---|---|
| **ISGS** – Illinois Height Modernization Program | Illinois | Queries the ISGS `ILHMP/Lidar_Collections` service, reads the collection tile index, and extracts only the LAS tiles under the field from the county-wide zip with HTTP range requests (no full download). |
| **USGS 3DEP** | Any U.S. state with 3DEP LiDAR | Streams the field's points from the public AWS Entwine Point Tile archive (`s3://usgs-lidar-public`). |

Processing: keep ground points (ASPRS class 2 by default) → crop → reproject → TIN (Delaunay) → rasterize → clip to the field plus a 100 ft margin (adjustable).

## Requirements
- QGIS 3.22 or newer
- The **PDAL** command-line program (`pdal` / `pdal.exe`). With OSGeo4W it is in `C:\OSGeo4W\bin`. Check with `pdal --version` in the OSGeo4W Shell.

## Install
1. Download [`lidar_field_dem.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/lidar_field_dem/lidar_field_dem.zip).
2. In QGIS: **Plugins → Manage and Install Plugins… → Install from ZIP**.
3. Choose `lidar_field_dem.zip`, click **Install Plugin**.
4. Click the toolbar button (or **Raster → LiDAR Field DEM → Find LiDAR collections and build DEM…**).

## Finding the available collection years
The toolbar window lists every LiDAR collection that covers the field:

1. Choose the field layer (tick *Selected features only* if needed) and click **Find collections**.
2. The table shows source (ISGS / USGS 3DEP), year, collection, native cell size, the share of the field it covers, quality level and status. Newest first.
3. Select a row and click **Build DEM with selected…** (or double-click it). The DEM dialog opens with the source and year filled in; adjust anything else and run.

The same list is available as a Processing tool, **LiDAR Field DEM → LiDAR → Available LiDAR collections for a field**, which writes a table you can use in models and batch runs. Both DEM and listing tools also work from the Processing Toolbox.

## Parameters
| Parameter | Default | Notes |
|---|---|---|
| Field boundary | – | Polygon layer; all (or selected) features are merged into one field. |
| LiDAR source | Automatic | Automatic = ISGS in Illinois, USGS 3DEP elsewhere. A field outside Illinois always uses 3DEP, even if ISGS is chosen. "In Illinois" is checked against ISGS's LiDAR coverage. |
| Collection year | blank | Blank = most recent. Use *Find collections* to see the years available. |
| DEM cell size | blank | In US survey feet. Blank = native resolution of the source DEM: the ISGS DTM cell size for that collection (e.g. 2 ft for Champaign 2020), or 1 m (3.28 ft) for 3DEP. Falls back to an estimate from point spacing. |
| Extend DEM beyond field | 100 ft | The DEM covers the field plus this distance (e.g. for tile outlets and waterways outside the field). |
| Extra interpolation buffer | 50 ft | LiDAR read beyond the DEM extent so its edges interpolate properly; not part of the output. |
| Ground classes | `2` | Comma-separated ASPRS classes. |
| Output CRS | blank | Illinois: EPSG:3435 (NAD83 / Illinois East, ftUS) or EPSG:3436 (West). The zone comes from the ISGS data; for 3DEP it's estimated from longitude (~89.1° W). Outside Illinois: NAD83 UTM zone. |
| Elevation units | Feet (US) | Or meters. |
| Save clipped ground points | off | Writes `<dem>_ground.laz` next to the DEM. |
| PDAL executable | blank | Auto-detected; set the full path if not found. |

## Cache
Tile indexes, downloaded LAS tiles and the 3DEP project index are cached in
`<your temp folder>/lidar_field_dem_cache`. Delete that folder to free space or force fresh downloads.

## Data credits
Illinois Height Modernization Program, Illinois State Geological Survey; USGS 3D Elevation Program.

## Issues
Report bugs or ideas at <https://github.com/Bhudus/QGIS_Tools/issues>.
