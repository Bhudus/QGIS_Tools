# LiDAR Field DEM (QGIS plugin)

Builds a bare-earth DEM for a field boundary from LiDAR ground points.

| Source | Coverage | How the data is fetched |
|---|---|---|
| **ISGS** – Illinois Height Modernization Program | Illinois | Queries the ISGS `ILHMP/Lidar_Collections` service, reads the collection tile index, and extracts only the LAS tiles under the field from the county-wide zip with HTTP range requests (no full download). |
| **USGS 3DEP** | Any U.S. state with 3DEP LiDAR | Streams the field's points from the public AWS Entwine Point Tile archive (`s3://usgs-lidar-public`). |

Processing: keep ground points (ASPRS class 2 by default) → crop → reproject → TIN (Delaunay) → rasterize → clip to the field plus a 100 ft margin (adjustable). Optionally, a second DEM with its sinks filled by the Wang & Liu (2006) method.

## Requirements
- QGIS 3.22 or newer
- The **PDAL** command-line program (`pdal` / `pdal.exe`). With OSGeo4W it is in `C:\OSGeo4W\bin`. Check with `pdal --version` in the OSGeo4W Shell.

## Install
1. Download [`lidar_field_dem.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/lidar_field_dem/lidar_field_dem.zip).
2. In QGIS: **Plugins → Manage and Install Plugins… → Install from ZIP**.
3. Choose `lidar_field_dem.zip`, click **Install Plugin**.
4. Click the toolbar button (or **Raster → LiDAR Field DEM → Find LiDAR collections and build DEM…**).

The plugin is also listed under **Raster → Tile Drainage**, a menu shared by the drainage tools in this repository, as well as under **Raster → LiDAR Field DEM**.

## Finding the available collection years
The toolbar window lists every LiDAR collection that covers the field:

1. Choose the field layer (tick *Selected features only* if needed), or click **Trace on map…** to draw the boundary, then click **Find collections**.
2. The table shows source (ISGS / USGS 3DEP), year, collection, the LiDAR's native cell size, the share of the field it covers, quality level and status. Newest first.
3. Select a row and click **Build DEM with selected…** (or double-click it). The DEM dialog opens with the source and year filled in; adjust anything else and run.

The same list is available as a Processing tool, **LiDAR Field DEM → LiDAR → Available LiDAR collections for a field**, which writes a table you can use in models and batch runs. Both DEM and listing tools also work from the Processing Toolbox.

## Running in the background
The window never freezes QGIS:

- **Find collections** searches in the background; you can keep working while it runs.
- **Build in background** runs the DEM as a QGIS task with the default settings, using the selected collection (or the most recent if none is selected). Tick **Also fill sinks (Wang & Liu)** to get the filled DEM too, and choose **Save to folder** to keep the results as `<field>_<source>_<year>_dem.tif` (and `_dem_filled.tif`); leave it blank for temporary layers.
- Close the window, keep working, or start more fields; each run shows in the task bar at the bottom of QGIS, where it can be canceled. The DEM is added to the map with a message when it is done.
- The run's log goes to **View → Panels → Log Messages**, on the *LiDAR Field DEM* tab.

Use **Build DEM with selected…** instead when you want to change other settings; that dialog also runs in the background but must stay open.

## Tracing a field boundary on the map
No boundary file? Click **Trace on map…** in the toolbar window. The window steps aside while you draw:

- **Left-click** each corner of the field. Snapping follows the project's snapping settings.
- **Backspace** removes the last corner.
- **Right-click** or **Enter** finishes (at least 3 corners).
- **Esc** cancels.

The outline is added to the map as a temporary layer, **Traced field boundary**, with its area in acres, and is selected as the field. To keep it, right-click the layer and choose **Export → Save Features As…**.

## Parameters
| Parameter | Default | Notes |
|---|---|---|
| Field boundary | – | Polygon layer; all (or selected) features are merged into one field. |
| LiDAR source | Automatic | Automatic = ISGS in Illinois, USGS 3DEP elsewhere. A field outside Illinois always uses 3DEP, even if ISGS is chosen. "In Illinois" is checked against ISGS's LiDAR coverage. |
| Collection year | blank | Blank = most recent. Use *Find collections* to see the years available. |
| DEM cell size | blank | In US survey feet. Blank = 10 × the LiDAR's native cell size: the ISGS DTM cell size for that collection (2 ft for Champaign 2020, so 20 ft), or 1 m for 3DEP (so 32.8 ft). The native size falls back to an estimate from point spacing. |
| Extend DEM beyond field | 100 ft | The DEM covers the field plus this distance (e.g. for tile outlets and waterways outside the field). |
| Extra interpolation buffer | 50 ft | LiDAR read beyond the DEM extent so its edges interpolate properly; not part of the output. |
| Ground classes | `2` | Comma-separated ASPRS classes. |
| Output CRS | blank | Illinois: EPSG:3435 (NAD83 / Illinois East, ftUS) or EPSG:3436 (West). The zone comes from the ISGS data; for 3DEP it's estimated from longitude (~89.1° W). Outside Illinois: NAD83 UTM zone. |
| Elevation units | Feet (US) | Or meters. |
| Save clipped ground points | off | Writes `<dem>_ground.laz` next to the DEM. |
| Also produce a DEM with sinks filled (Wang & Liu) | off | Writes a second DEM in which every depression is filled so each cell drains to the DEM's edge. |
| Minimum slope across filled sinks | 0.06° | Keeps a slight gradient over filled areas so flow directions are defined; 0 gives flat fills. |
| Filled DEM | blank | Where the filled DEM is saved. Blank = `<dem>_filled.tif` next to the DEM. |
| PDAL executable | blank | Auto-detected; set the full path if not found. |

## Filling sinks (Wang & Liu)
Tick **Also produce a DEM with sinks filled (Wang & Liu)** to get a second DEM for flow routing and watershed work. Filling follows Wang, L. and Liu, H. (2006), *An efficient method for identifying and filling surface depressions in digital elevation models for hydrologic analysis and modelling*, IJGIS 20(2): 193–213: a priority queue grows inward from the outlets (the edge of the DEM and any NoData) in order of spill elevation, raising each cell to at least the level of the cell it was reached from plus the minimum-slope rise. The fill runs on the DEM plus its interpolation buffer, then is clipped like the DEM, so water can leave across the field margin. The log reports how many cells were raised, the deepest fill and the filled volume. It needs no SAGA install.

## Cache
Tile indexes, downloaded LAS tiles and the 3DEP project index are cached in
`<your temp folder>/lidar_field_dem_cache`. Delete that folder to free space or force fresh downloads.

## Data credits
Illinois Height Modernization Program, Illinois State Geological Survey; USGS 3D Elevation Program.

## Issues
Report bugs or ideas at <https://github.com/Bhudus/QGIS_Tools/issues>.
