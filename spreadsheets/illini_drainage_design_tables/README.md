# Illini Drainage Design Tables and Calculators

**Workbook:** `Illini_Drainage_Design_Tables_and_Calculators.xlsm` · Version 1 ·
[download zip](https://github.com/Bhudus/QGIS_Tools/raw/main/spreadsheets/illini_drainage_design_tables/illini_drainage_design_tables.zip)

Drainage design tables and small calculators, one per sheet, converted from the University of
Illinois Agricultural Engineering (AGENG) Visual Basic programs. Values and equations are
unchanged from the originals.

| Sheet | What it gives |
|---|---|
| **Min Radii (T1)** | Minimum radius of curvature (and degree of curve) for a ditch without bank protection, from ditch top width and slope |
| **Max Length (T6)** | Maximum length of corrugated plastic tubing or clay and concrete tile, from drainage coefficient, spacing, Manning's n, inside diameter and slope |
| **Min Grade (T7)** | Minimum grade of small tile and tubing drains, with and without fine sand or silt entering |
| **Wheel Loads (T8)** | Percentage of a wheel load transmitted to a drain, from backfill depth and trench width |
| **Trench Depth (T9)** | Maximum trench depth for corrugated tubing in loose, fine-textured soil, from tubing diameter, quality and trench width |
| **Max Trench Depth (T10)** | Maximum allowable trench depth for drain tile, from crushing strength, tile diameter and trench width |
| **Pumping Plants** | Drainage pump capacity (GPM), power requirement (HP), and active storage and sump area for automatic or manual operation |
| **Manning** | Manning's equation for open channels (rectangular, triangular, trapezoidal, circular, parabolic or irregular): velocity and discharge, or the depth, slope or Manning's n for a given velocity or discharge |

## Use

On each sheet, edit the white input cells or pick from the drop-downs; results update as you
go. Max Length (**Calculate**, **Clear**), Pumping Plants (**Calculate**, **Reset**) and Manning
(**Calculate**, **Reset Defaults**) also have the buttons of the original programs, and every
sheet except Manning has an **Exit** button that closes the workbook. The table sheets show the
source table next to the inputs.

The table and calculator sheets are protected so that only the input cells can be edited;
**Review → Unprotect Sheet** (no password) to change them. The Manning sheet is not protected.

Manning also adds worksheet functions you can use in any cell:
`=ManningVelocity(shape, depth, width, sideSlope, slopePct, n, [points], [k])`,
`=ManningDischarge(...)` with the same arguments, and `=ChannelArea(shape, depth, width, sideSlope, [points])`.

## Opening the workbook

Windows blocks macros in downloaded files: before opening, right-click the zip →
**Properties** → tick **Unblock** → **OK**, then extract it and click **Enable Content** in
Excel. The lookups and most results are formulas and work without macros; the buttons and the
Manning sheet need them. The buttons are Form Controls, not ActiveX controls.

## Code

`vba/` holds the workbook's macros as text, for reading; the workbook already contains them.

* `modCalculators.bas`: the Max Length, Pumping Plants and Exit buttons.
* `MaxLength_Sheet2.cls`: fills in Manning's n when the drain material changes.
* `modManning.bas`: the Manning calculator (`CalculateManning`, `ManningResetInputs`), the
  channel geometry, the depth solver and the worksheet functions.
* `Manning_shManning.cls`: recalculates the Manning sheet when an input changes.

## Versions

* **1** – First published version: the table sheets, Pumping Plants and Manning in one workbook.
