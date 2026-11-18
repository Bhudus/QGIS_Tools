# Main Telescoping Worksheet

**Workbook:** `Main_Telescoping_Worksheet.xlsm` · Version 1 ·
[download zip](https://github.com/Bhudus/QGIS_Tools/raw/main/spreadsheets/main_telescoping_worksheet/main_telescoping_worksheet.zip)

Sizes each section of a telescoping main (M1, M2, …, from the upper end down) from the
flow it carries, and gives the actual and nominal pipe size of each section.

## Use

On **Main Sizes**, one row per main section:

| Column | Enter |
|---|---|
| Drainage intensity (in/day) | Drainage coefficient |
| Lateral length and spacing (ft) | Laterals draining into the section (cumulative length) |
| Upstream area (acres) | Any other area draining into the section |
| Attached pipe: size (in), slope (%), pipe type | A pipe flowing full into the section |
| Inflow (cfs) | Any direct inflow |
| Inlet: diameter (in), head (ft) | A surface inlet (orifice flow) |
| Main slope (%), pipe type | The section's grade and pipe |

Pipe type index: 1 single wall, 2 dual wall, 3 clay, 4 concrete.

The sheet adds up the flow (cfs) and sizes the pipe with Manning's equation flowing full
(roughness n = 0.015 / 0.017 / 0.020 for single-wall pipe up to 8 in / 8–12 in / larger,
0.011 dual wall, 0.013 clay and concrete), then rounds up to the next nominal size
(3, 4, 5, 6, 8, 10, 12, 15, 18, 21, 24, 30 in). **Cumulative Pipe Lengths** totals the
lateral length feeding each section.

## Code

`vba/Module1.bas`: the worksheet functions `Flowrate`, `Pipesize`, `NominalPipeSize` and
`Roughness`, which you can also use in your own sheets.

## Versions

* **1** – First published version.
