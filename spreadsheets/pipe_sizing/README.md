# Pipe Sizing

**Workbook:** `Pipe_Sizing.xlsm` · Version 1 ·
[download zip](https://github.com/Bhudus/QGIS_Tools/raw/main/spreadsheets/pipe_sizing/pipe_sizing.zip)

Drain pipe sizing with Manning's equation for a pipe flowing full, with an optional layer of
sediment in the bottom of the pipe. Give any two of tile size, slope, flow rate, velocity and
drained area, and get one of the others, with a pipe capacity chart for the chosen drainage
coefficient and material.

## Use

Click **Click to Load Pipe Sizing Routine** at the top of the **Plot** sheet. In the calculator:

1. Pick the **drainage coefficient** (3/8, 1/2, 3/4 or 1 in/day, or Other).
2. Pick the **material**: Single Wall, Clay or Concrete, Dual Wall, or Other (your own n).
3. Optionally enter **sedimentation**, as a percentage of the pipe diameter or a depth in inches.
4. Choose what to calculate on the tabs, choose the inputs, enter them and calculate:

| Tab | From |
|---|---|
| Area (acres drained) | tile size and slope, tile size and velocity, or flow rate |
| Tile Size (standard size and actual diameter) | area and slope, or flow rate and slope |
| Slope (%) | tile size and area, tile size and velocity, or tile size and flow rate |
| Flow Rate (cfs) | tile size and slope, tile size and velocity, or area |
| Velocity (ft/s) | tile size and slope, tile size and flow rate, or tile size and area |

The tile size is the smallest standard size (3, 4, 5, 6, 8, 10, 12, 15, 18, 21, 24, 30, 36,
42 in) that carries the flow; above 42 in the actual diameter is rounded to the nearest inch.
The chart plots the capacity of each pipe size against slope, with lines of equal velocity
(0.5, 1.5 and 2.5 ft/s). A larger copy of the chart can be opened from the calculator and
saved as a .jpg or printed.

Manning's n: single wall 0.015 up to 8 in, 0.017 for 10–12 in, 0.020 above 12 in; dual wall
0.012; clay and concrete 0.013. Flow rate (cfs) = 9.645 × 10⁻⁷ × area (ft²) × drainage
coefficient (in/day).

## Opening the workbook

Windows blocks macros in downloaded files: before opening, right-click the zip →
**Properties** → tick **Unblock** → **OK**, then extract it and click **Enable Content** in
Excel. The buttons are a picture and Form Controls, not ActiveX controls, so the workbook works
with Microsoft 365 and Office 2024, which disable ActiveX by default.

## Code

`vba/` holds the workbook's macros as text, for reading; the workbook already contains them.

* `frmMain.frm`: the calculator (`cmdCalculate_Click`), tile sizing (`DetD`, `PipeCap`,
  `DiaForQ`) and sediment depth (`SedDepth`).
* `Module1.bas`: pipe geometry with sediment (`PipeArea`, `WP`), roughness, and the worksheet
  functions `Flowrate`, `VSlope` and `VDischarge` used for the chart.
* `Plot_Sheet2.cls`, `ThisWorkbook.cls`: the Plot sheet's buttons and start-up.

## Versions

* **1** – First published version. Compared with the earlier unpublished workbook: slope from
  tile size and flow rate uses the tile size for Manning's n (it used the flow rate, so
  single-wall pipe over 8 in got n = 0.015 and too flat a slope); the tile size is the smallest
  standard size that carries the flow at its own n (near 8 and 12 in it could be one size too
  big); the sediment Depth (in) option works; bad input gives a message
  instead of a VBA error; the ActiveX buttons are replaced.
