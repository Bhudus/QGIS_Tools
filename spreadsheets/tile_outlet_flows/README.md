# Tile Outlet Flows

**Workbook:** `TileOutletFlows.xlsm` · Version 1 ·
[download zip](https://github.com/Bhudus/QGIS_Tools/raw/main/spreadsheets/tile_outlet_flows/tile_outlet_flows.zip)

Gives the flow from a tile drainage system for each of a column of stream depths, from the
rating curve of the QGIS plugin [Tile Outlet Submergence](../../tile_outlet_submergence/),
and charts stream depth against flow. Use it for a record of stream depths (daily
readings, a gauge record, a design storm) to see how much the stream backing up over the
outlet cuts the flow from the system.

## Use

1. In QGIS, run **Tile Outlet Submergence** and save the rating curve as CSV: the
   **Rating curve as CSV** output, or the **Export CSV** button in its report.
2. On **Flows**, click **Import rating curve CSV...** and pick the file. The units (ft and
   cfs, or m and L/s) come from the CSV and appear in the column headings.
3. Enter the height of the outlet invert above the zero of the stream depths (the stream
   bed, or the gauge's zero) in C4. Enter 0 if the depths are already measured from the
   outlet invert.
4. Paste the dates (or any numbers or labels) in column A and the stream depths in
   column B, from row 11 (up to 5,000 rows).

For each depth the sheet gives the ditch level above the outlet invert, the flow, its %
of the free-outlet flow and the flow reduction. The flow is interpolated in a straight
line between the ditch levels of the rating curve.

- **Depth below the outlet invert:** the free-outlet flow, with a note.
- **Depth above the highest ditch level in the rating curve:** no flow is given; the note
  asks you to rerun the plugin with a higher **Highest ditch level**. The sheet does not
  extrapolate.

**Chart** plots the rating curve as stream depth (ditch level plus the invert height)
against flow, flat at the free-outlet flow below the invert, with each stream depth on
**Flows** as a point. It follows the invert height and the imported rating curve.

**Rating** holds the imported CSV: row 4 is the free outlet and rows 5 on are the ditch
levels, rising. **Clear stream depths** clears columns A and B on **Flows**.

The flows are worksheet formulas, so they work with macros disabled; the macros only
import the CSV and clear the depths. The workbook comes with an example rating curve from
a synthetic layout, and example dates, depths and invert height to show the format:
replace them.

## Code

`vba/Module1.bas`: `ImportRatingCurve` (the button: picks the file and reports the
result), `LoadRatingCsv` (reads the CSV into **Rating**, checks that it came from the
plugin and that the ditch levels rise) and `ClearStreamDepths`.

## Versions

* **1** – First published version.
