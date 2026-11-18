# Drainage outlet (QGIS plugin)

Traces the natural downhill path from a **drainage system outlet point** (tile-drain outlet, culvert, ditch end), or from the natural outlet on a **field boundary**, to the **nearest downstream stream**, inside the **USGS StreamStats watershed** that contains the point.

## Install
QGIS → *Plugins → Manage and Install Plugins → Install from ZIP* → `drainage_outlet.zip`. Run it from the toolbar button, *Plugins → Drainage outlet*, or the Processing Toolbox (works in models and batch mode).

## Three ways to start
1. **Field boundary (no point needed).** Give a field polygon. The plugin routes every cell of the field, finds where most of its runoff crosses the boundary, and traces from that spot. Optionally also traces secondary outlets draining at least X % of the field (`share_pct`).
2. **Outlet point layer.**
3. **Point picked on the map.**

They can be combined in one run. The `source` column says which one each path came from (`field boundary`, `layer`, `map`).

## Running it
Click the **Drainage outlet** toolbar button (or *Plugins → Drainage outlet*). The tool window opens directly; a layer named like *globalwatershed* / *watershed* is pre-selected as the watershed.
* **Field:** choose the field layer; to run on specific fields, select them on the map first and tick *Selected features only*.
* **Point on the map:** click **…** next to *Outlet point picked on the map*, then click the outlet.

**Background processing:** pressing **Run** closes the window and the job continues as a QGIS background task, so you can keep working. Progress and a cancel button are in the status bar (bottom right); several runs can go at once. When it finishes, the output layers are added to the map and a message appears; warnings and the run log are in *View → Panels → Log Messages*, tab *Drainage outlet*.
(The tool in the Processing Toolbox still runs the standard way, with the log window open.)

## Inputs
| Input | Notes |
|---|---|
| Units | **US customary (default)**: feet, mi², like StreamStats. Or metric. Applies to the inputs below and to every output. |
| Field boundary polygon(s) | Optional. Natural outlet found automatically. Tick *Selected features only* to run on chosen fields. |
| Secondary outlets % | Field mode. 0 = main outlet only. |
| Outlet point layer | Optional point layer. Tick *Selected features only* to run on chosen points. One path per point. |
| Outlet point picked on the map | Optional. Click **…** next to the box and click the outlet on the map. |
| StreamStats watershed | Watershed polygon from StreamStats (shapefile or GeoJSON; the polygon layer, not the StreamStats pour point). |
| DEM | Any file-based elevation raster (3DEP 1/3″, lidar, etc.). |
| Elevation units of the DEM | Feet, US survey feet or metres (3DEP is metres; many state lidar DEMs are US survey feet). |
| Download DEM from USGS 3DEP | Fetches the DEM automatically (US only, internet needed). |
| Cell size | 0 = DEM's native resolution (~33 ft when downloading). |
| Maximum grid cells | Large watersheds are coarsened above this to keep run time reasonable. |
| Stream lines | Optional, e.g. NHD / NHDPlus HR flowlines. Without them, channels are derived from the DEM. |
| Stream snap distance | Mapped streams rarely line up exactly with the DEM valley; lines are widened by this (default 50 ft). |
| DEM-derived channels / initiation area | Treat cells draining more than this area (default 0.2 mi²) as stream. |
| Snap outlet to strongest flow line | 0 = start exactly at the point. Otherwise the start moves to the cell with the most upstream drainage area within this radius (useful when a GPS point sits on a ditch bank). |

## Output
**Drainage paths** (lines with flow arrows). They start exactly at your point (or where flow crosses the field boundary) and keep the point's or field's attributes, plus `source`, `pt_fid`, `field_fid`, `outlet_rank`, `share_pct`, `start_x`, `start_y` and:
`length_ft`, `start_z_ft`, `end_z_ft`, `drop_ft`, `slope_pct`, `end_type` (stream / already at stream / left watershed), `stream_fid`, `stream_nm` (GNIS name if present), `snapped_ft`, `end_x`, `end_y`, `cell_ft`. Field names end in `_m` in metric mode.

**Downloaded DEM** (when *Download DEM from USGS 3DEP* is ticked): saved as a GeoTIFF covering the watershed and added to the map. Elevations are in **feet** in US mode (metres in metric mode). Leave the output as a temporary file, or choose a file name to keep it. To reuse it later, pick it as the DEM input with *Elevation units of the DEM* = Feet (international); this skips the download.

Optional: **Stream cells used** raster, for checking the channel network.

## Method
1. DEM clipped to the watershed, reprojected to a projected CRS (the DEM's own, or UTM).
2. Depressions filled with priority-flood plus a small gradient on flats (Barnes et al., 2014), so water always finds a way out.
3. D8 steepest-descent flow directions.
4. From each outlet the path is followed downhill until it touches a stream cell: the hydrologically nearest stream, which isn't always the closest in a straight line.

## Tips
* Road embankments, culverts and ditches only show up in high-resolution (lidar) DEMs; use the best DEM you have.
* If `end_type` says *left watershed*, increase the stream snap distance or tick *Also treat DEM-derived channels as streams*.
* Only needs what ships with QGIS (Python, NumPy, GDAL).
