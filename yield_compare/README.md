# Yield Compare: QGIS plugin

A QGIS 3.22+ / QGIS 4 port of the VB6 + MapWinGIS **Yield Statistics** program (`prjYields.vbp`).

## Install
1. Download [`yield_compare.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/yield_compare/yield_compare.zip).
   In QGIS: **Plugins → Manage and Install Plugins… → Install from ZIP**, then choose the zip.
2. Open it from **Vector → Tile Drainage → Compare Field Yields…** or the toolbar icon.

## Use
1. Load the two yield shapefiles into QGIS (e.g. `md_North-2007.shp`, `fd_North-2007.shp`).
2. Pick each one as **Field 1** / **Field 2**, then choose the yield column (auto-picks a `Yld…` column) and an optional scale factor.
3. Optional: **Perform regression analysis** (yield vs a regressor column), **Reweighted least squares**, **Confine to range** of a constraining column.
4. **Calculate Statistics**. Results appear on four tabs: Statistics, Equivalence tests, Histograms, Regression.
   Both layers are coloured on the map with a graduated yield renderer (minimum → maximum colour).
5. **Reset from tails** sets the thresholds so the given fraction is trimmed from each tail, then recalculates.
6. **Export report…** writes a CSV. Plots can be saved from the toolbar above each plot.

## What maps to what
| VB6 form | Plugin |
|---|---|
| File pickers + Map 1 / Map 2 mini-maps | Layer drop-downs; layers display in the QGIS map canvas |
| Yield column, Scale factor, Regressor column | Same, per field |
| Confine to Range (Upper/Lower Limit, constraining column) | Same |
| Lower/Upper Threshold, Reset (`txtGProb(4)`) | Thresholds group, "Reset from tails" |
| Gaussian / Empirical Exceedance Probability + recalc buttons | "Exceedance probability" per field; both values always shown |
| No. of Points, Min, Max, Average, Std. Dev., Median, I.Q. Range/Ratio | Statistics tab |
| Testing for Normality (J-B, skewness, kurtosis) | Statistics tab |
| Equivalence of Means / Medians / Variances / Histograms / Slopes | Equivalence tests tab |
| picHist / picLine graphs, Save Bitmap | Histograms / Regression tabs (matplotlib, with save) |
| Min/Max Color, map colouring | Map colouring group (QGIS graduated renderer) |
| Enter Mapview form (layers, labels, zoom, colour breaks) | Native QGIS. Not needed |
| Contour button | Was an empty stub in the VB code. Not ported (QGIS Processing has interpolation + contour tools) |

## Differences from the VB6 program
Most of these are bugs in the original that I fixed. Expect results that are close to the old program but not always identical.

* **Scale factors**: VB applied Field 1's yield scale factor to Field 2 in most calculations. Each field now uses its own.
* **Reset thresholds**: VB used Field 2's yield column for both fields and kept the old thresholds in effect. It now uses each field's own column on the untrimmed data.
* **Reweighted LS**: VB scanned slopes with a malformed angle range and a random intercept offset (results changed run to run). The new version scans slope angles ±90° around the OLS slope and computes the exact least-median-of-squares intercept for each one, so it is deterministic. The 2.5-sigma two-pass reweighting and weighted refit are unchanged. The LMS step now also respects thresholds and constraints.
* **Robust median test**: same reweighting as VB, now applied to the filtered points only.
* **Histogram χ² test**: same statistic. Degrees of freedom = (bins with data in both fields) − 1 instead of a fixed 29, and the p-value is the standard upper tail instead of "2 × min(P, 1−P)".
* **Variance F test**: exact two-tailed p-value.
* **Jarque-Bera p-value**: asymptotic χ²(2), instead of ALGLIB's small-sample table (identical for yield-monitor-sized samples).
* **Inverse normal**: exact, instead of the Abramowitz–Stegun approximation (differences < 0.0005 σ).
* **Min / Max**: reported for the points actually analysed (after thresholds). VB reported pre-threshold values.
* **Missing values (NULL)** are skipped. There is also an option to use only the selected features.
* **Slope tests** appear only when regression is on (VB showed meaningless values otherwise).

## Files
* `stats_core.py`: all statistics (pure Python + numpy, no QGIS dependency; replaces the ALGLIB `.bas` modules)
* `dialog.py`: user interface, map styling, plots, export
* `plugin.py`, `__init__.py`, `metadata.txt`, `icon.png`: QGIS plugin plumbing
