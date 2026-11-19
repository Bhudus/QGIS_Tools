# Boundary Contours (QGIS plugin)

Contour lines from a DEM, like **Raster ▸ Extraction ▸ Contour**, plus:

- **Boundary clipping**: pick a polygon layer (or only its selected features). The tool reads only the part of the DEM inside the boundary, so it runs faster on big rasters, and clips the contours exactly to the boundary. An optional buffer grows the boundary, or shrinks it if negative.
- **Index contours**: every Nth contour, counted from the offset, gets `INDEX = 1` and `TYPE = 'Index'`. For example, a 2 m interval with N = 5 gives an index line every 10 m.
- **Filled DEM option**: fill sinks before contouring, so hollows don't produce closed "bullseye" contours. Each pit is filled flat to its spill elevation. Use the built-in Priority-Flood method, which needs no extra software, or SAGA *Fill sinks (Wang & Liu)* if the SAGA NextGen provider is installed. You can also save the filled DEM.
- **Smoothing**: *DEM smoothing* (Gaussian, in pixels) removes noise before contouring, and the contours can't cross. *Line smoothing* (Chaikin passes) rounds off jagged lines. When both smoothing and filling are on, the DEM is smoothed first, then filled.
- **Auto-style**: index lines are drawn thicker and labelled with their elevation. Intermediate lines are thin.

## Install
1. Download [`boundary_contours.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/boundary_contours/boundary_contours.zip).
2. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**.
3. Choose `boundary_contours.zip` and click **Install Plugin**.

## Use
- Menu: **Raster ▸ Tile Drainage ▸ Contours (boundary + index)…**, or the raster toolbar icon
- Processing Toolbox: **Tile Drainage ▸ Contours ▸ Contours (boundary + index)**. It works in the Graphical Modeler and in batch mode too.

## Remembered settings
When you open the tool from **Raster ▸ Tile Drainage** or the toolbar, it fills in the settings from your last run in the current project. This includes the DEM and boundary layers, if they're still loaded, and the "selected features only" choice. This carries over after you restart QGIS. Opening a different project, or starting a new one, gives you the defaults, or that project's own last settings. Settings from a new, unsaved project carry over once you save it. Output file names are deliberately not remembered, so an earlier result can't be overwritten by accident.

## Parameters
| Parameter | Notes |
|---|---|
| Input DEM / Band | Any raster GDAL can read |
| Interval | Contour spacing |
| Index contour every Nth line | 0 turns index contours off |
| Boundary polygon layer | Optional. Use "Selected features only" for a subset. Reprojected to the DEM's CRS if needed |
| Buffer boundary by | In DEM units. Negative values shrink the boundary |
| Clip contours exactly to boundary | If off, the DEM is only cropped to the boundary's bounding box |
| Attribute name for elevation | Default `ELEV` |
| Contour from a filled DEM | No / Built-in Priority-Flood / SAGA Wang & Liu |
| DEM smoothing strength | Gaussian sigma in pixels. 0 = off, 1–3 is typical for lidar |
| Contour line smoothing passes | Chaikin passes. 0 = off, 1–3 is typical |
| Apply index contour style and labels | Styles the output layer when it loads |
| Filled / smoothed DEM (output) | Optional. Saves the processed DEM that the contours were built from |
| *Advanced:* Extra DEM margin for filling | With a boundary, also reads and fills terrain this far outside it, so pits crossing the boundary fill correctly |
| *Advanced:* Line smoothing corner offset | Default 0.25. Higher values round corners more per pass |
| *Advanced:* Offset, Minimum contour length, 3D, NoData handling | Same as the GDAL tool. Minimum length removes small slivers left along the clip edge |

## Output fields
`ID`, `ELEV` (or your name), `INDEX` (1/0), `TYPE` (`Index`/`Intermediate`)

## Python
```python
processing.run("boundarycontours:contours", {
    "INPUT": "dem.tif", "BAND": 1, "INTERVAL": 2, "INDEX_EVERY": 5,
    "BOUNDARY": "parcels.gpkg", "BUFFER": 0, "CLIP": True,
    "SMOOTH_DEM": 1.5, "SMOOTH_LINES": 2, "FILL_METHOD": 1, "FILL_MARGIN": 200, "FILLED_DEM": "filled.tif",
    "OUTPUT": "contours.gpkg"})
```

## Notes
- Uses only QGIS, GDAL and numpy. The SAGA fill method also needs the *Processing Saga NextGen Provider* plugin.
- Works with QGIS 3.16 and later, including Qt6 (QGIS 4) builds.
- Rotated rasters are read in full, without cropping to the boundary.

## License
GPL-3.0-or-later. See [LICENSE](LICENSE).
