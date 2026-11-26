# Yield Compare: QGIS plugin

Compares crop yields from two yield-monitor point layers (two fields, two years or two treatments), and checks whether yield drops midway between tile drains. Started as a QGIS 3.22+ / QGIS 4 port of the University of Illinois VB6 + MapWinGIS **Yield Statistics** program (`prjYields.vbp`).

## Install
1. Download [`yield_compare.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/yield_compare/yield_compare.zip).
   In QGIS: **Plugins → Manage and Install Plugins… → Install from ZIP**, then choose the zip.
2. Open it from **Vector → Tile Drainage → Compare Field Yields…** or the toolbar icon.

## Try it with the sample fields
Click **Load sample fields** at the top of the dialog, then **Calculate Statistics**. This adds two bundled fields, their tile networks and boundaries to the project in a group, and fills in the dialog (yield and pass columns, network, drain spacings).

The sample fields are **synthetic**. They are not, and are not derived from, any real field, and they sit on a made-up local grid in feet (the `.prj` is a local coordinate system), so they have no real-world location and no background map lines up with them. Open them in a new project. If QGIS asks for a coordinate system, choose *No CRS*.

| | Sample Field A (29 ac) | Sample Field B (24 ac) |
|---|---|---|
| Laterals | East–west, **100 ft** apart: too wide for the soil | North–south, **60 ft** apart: well drained |
| Built-in drainage effect | Yield loss grows with distance from the tile, 22 bu/ac midway | 3 bu/ac midway; untiled wet north-east corner about 30 bu/ac lower |
| Harvest passes | 30 ft, parallel to the laterals (1° off) | 30 ft, parallel to the laterals (1.5° off) |

Both fields also have the other features of real yield-monitor data:
* a random offset for each pass (sd 5 bu/ac)
* flow-delay smoothing along the pass
* low readings at the start of each pass
* a few spikes
* soil patches at two scales
* a gentle trend across the field

What the plugin finds with the defaults and **Reset from tails** at 1 %:
* **Field A:** midway yield is **16 bu/ac (8 %) lower** than over the tile (95 % CI 7 to 25 lower, p ≈ 0.007). The spacing is too wide.
* **Field B:** no difference between midway and over the tile (−0.3 bu/ac, CI −4.0 to +3.5). The untiled corner (*Beyond reach*) is about 19 bu/ac lower than the tiled area. It is about 30 bu/ac lower in its core; the average includes the edge of the corner, where the loss is still building.
* **Field A vs Field B:** Field A averages 9 bu/ac less, but the interval (−24.5 to +6.6) includes zero. With soil patches hundreds of feet across, each field carries the information of only a handful of independent points, so a difference in field means of this size can't be told apart from soil differences. Untick *Allow for spatial autocorrelation* to see how confidently the ordinary test (p < 10⁻²⁰⁰) would claim otherwise.
* **Without the pass column**, Field A's interval narrows to 14–18 bu/ac: overconfident, because the pass offsets are ignored.

`tools/make_sample_data.py` (in the repository, not in the plugin zip) regenerates the sample files and documents exactly how they were built.

## Use
1. Load the two yield layers into QGIS.
2. Pick each one as **Field 1** / **Field 2**. Choose the yield column (a `Yld…` column is picked automatically) and an optional scale factor, and the **harvest pass column** (a column with "pass" in its name, such as `Pass_Num`, is picked automatically).
3. Leave **Allow for spatial autocorrelation** ticked (see below).
4. Optional:
   * **Perform regression analysis**: yield against a regressor column (moisture, elevation, speed…), by least squares or **reweighted least squares**.
   * **Confine to range** of a constraining column.
   * **Yield vs distance to tile**: see below.
5. **Calculate Statistics**. Results are on six tabs:
   * **Statistics**
   * **Equivalence tests**, with a plain-language summary of the difference in mean yield and its 95 % confidence interval
   * **Distance to tile**
   * **Histograms**
   * **Regression**
   * **Spatial correlation**: the variograms

   Both layers are coloured on the map by yield.
6. **Reset from tails** trims the given fraction from each tail and recalculates.
7. **Export report…** writes a CSV. Plots can be saved from the toolbar above each plot.

## Spatial autocorrelation
Neighbouring yield-monitor points are not independent: a wet spot, a sandy knoll or a harvest pass affects many points at once. Ordinary tests treat 4,000 points as 4,000 independent observations, so almost any difference comes out "significant". In tests on synthetic fields with identical true yields, the ordinary t-test reported a significant difference 87 % of the time; with the correction it was 3–5 %, as it should be.

For each field the plugin removes a planar trend and fits a correlation model with three parts:
* **Between harvest passes**: points in the same pass share an offset (header, flow delay, operator). This part needs the pass column.
* **Spatial**: correlation that decays with distance (an exponential variogram), fitted on pairs of points in different passes.
* **Along a pass**: the extra correlation between nearby points in the same pass.

Each spatial part is kept only if a permutation test (99 shuffles, 1 % level) finds it, so random noise is left alone.

The model is then used two ways:
* **Comparing the two fields** (means, medians, variances, histograms) uses the *effective number of independent points* (`n_eff`). The Statistics tab shows it: typically about 100 for a 4,000-point field.
* **Comparisons within a field** (over vs between drains, regression slopes) use the variance of the contrast computed from the model. Patches of good or poor ground that both groups share then cancel out, as they should.

The Equivalence tests tab also shows the p-value ignoring autocorrelation, for comparison with the VB6 program.

**Limits:**
* The two fields are treated as independent. For the same field in two years, the shared soil pattern makes the test conservative.
* Pass effects are treated as independent of each other.
* The correlation is assumed the same in all directions apart from the pass terms.

## Yield vs distance to tile
Tick **Yield vs distance to tile**, choose the tile network layer and check the **drain spacing of each field**. Comparing two spacings is often the point, so each field has its own.
* For an Illini Subsurface Drain Layout network, each field's spacing is read from `DRAIN_SPACE` of the laterals within that field, and **Laterals only** uses `kind = 'lateral'`.
* Any line layer works. One layer can hold the networks of both fields.

For each yield point the plugin finds the distance to the nearest tile line (QGIS spatial index, in a projected CRS: the network's, or UTM if the layers are in latitude/longitude). It then reports:
* **Yield in distance bands** from the tile out to half the drain spacing, plotted with 95 % confidence intervals.
* **Midway minus over the tile**: mean yield in the outer quarter (midway between laterals) minus the inner quarter (over the tile), with its 95 % confidence interval and p-value. A clear drop midway suggests the drains are too far apart for the soil.
* **Trend**: the straight-line change in yield from the tile to midway.
* **Beyond reach**: yield more than half a spacing from any tile (untiled parts of the field) compared with yield within reach.

**Choose the pass column.** When laterals run parallel to the harvest passes, distance to tile is largely "which pass", and differences between passes (header, flow lag, direction) can look like a drainage effect.

In tests on real yield data (fd_North-2007) with made-up laterals parallel to the passes at 50 and 70 ft, the ordinary test flagged a "drainage effect" in about half the cases. With the pass column the plugin flagged none.

The analysis shows an association, not a cause. Anything else that repeats at the drain spacing, such as sprayer wheel tracks or old tile, can produce the same pattern. The fd_North-2007 data itself has a strong pattern repeating every ~120 ft parallel to the harvest passes, which would be worth checking against the field's tile map.

## What maps to what (VB6 program)
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
Most of these are bugs in the original that were fixed. With **Allow for spatial autocorrelation** unticked, results are close to the old program but not always identical.

* **Spatial autocorrelation** is allowed for (new, see above).
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
* `dialog.py`: user interface, tile distances, map styling, plots, export
* `plugin.py`, `__init__.py`, `metadata.txt`, `icon.png`: QGIS plugin plumbing
* `sample_data/`: the two synthetic sample fields (yield points), their tile networks and boundaries
* `tests/test_stats_core.py`: checks of the statistics on synthetic fields (run with `python tests/test_stats_core.py`; needs numpy only). In the repository only.
* `tools/make_sample_data.py`: regenerates `sample_data/`. In the repository only.

Layers on a local grid (an engineering CRS, or no CRS) are analysed in their own units. Layers in latitude/longitude are projected to UTM for distances.
