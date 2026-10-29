# Raster Extremes (QGIS plugin)

Finds the cells with the maximum and minimum values of a raster band and gives
their X and Y coordinates.

## Install
1. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**.
2. Choose `raster_extremes.zip`, click **Install Plugin**.
3. A **Raster Min / Max Cells…** button appears on the Raster toolbar (and under Raster → Raster Extremes).

## Use
1. Click the button. The active raster layer is pre-selected.
2. Choose the **Raster layer** and **Band**.
3. Click **Find min / max**. The result is shown:
   - **In the dialog summary**: the max and min values with their X/Y coordinates.
   - **In the message bar** at the top of the map, for 15 seconds.
   - **In the Log Messages panel**, under the *Raster Extremes* tab, so you can copy it later.
   - **In the results table**: type, value, row, column, X, Y and WGS84 longitude/latitude for every cell.
4. Double-click a table row to zoom to that cell. **Copy table** copies the results (tab-separated, pastes into Excel).

## Options
- **Max cells listed per extreme**: if several cells share the min or max value, all are counted
  but at most this many of each are listed (default 100).
- **Add result points as a layer**: adds a temporary point layer with the cells,
  red for the maximum and blue for the minimum. Fields: `type`, `value`, `row`, `col`, `x`, `y`.

## Notes
- X/Y are the **centres** of the cells, in the raster layer's CRS. Row 0, column 0 is the top-left cell.
- NoData cells are skipped: the file's NoData value, any extra NoData values set in
  Layer Properties → Transparency, and NaN.
- The raster is read in strips, so large rasters don't need to fit in memory. A Cancel button stops the scan.
- Rotated rasters are not supported (north-up rasters only).
- Works with QGIS 3.16 and later, including Qt6 builds.
