# DRAINMOD Drainage Design

**Workbook:** `DRAINMOD_DrainageDesign.xlsm` · Version 1 ·
[download zip](https://github.com/Bhudus/QGIS_Tools/raw/main/spreadsheets/drainmod_drainage_design/drainmod_drainage_design.zip)

Runs a DRAINMOD project for every combination of weather set, drain depth and drain
spacing, and plots DRAINMOD's average relative yield against drain spacing: one chart per
crop, one line per drain depth and weather set. Use it to choose a depth and spacing
that hold up under both current and projected (future) weather.

## Needs

* Windows Excel with macros enabled.
* DRAINMOD (`dmhydro.exe`) and a DRAINMOD project (.prj with its .gen) that runs with a
  crop sequence and yield inputs.
* Rainfall (.RAI) and temperature (.TEM) files for the current weather and, optionally,
  for a future climate (for example from the
  [DRAINMOD Climate-Scenario Weather Files](../../drainmod_climate/) plugin).

## Use

On the **Setup** sheet:

1. Select the DRAINMOD project. `dmhydro.exe` is filled in from the project's Program
   folder if it has one.
2. Choose the rainfall and temperature files of the current and the future weather.
   Station IDs default to those in the project. Leave the Future row empty to run only
   the current weather.
3. Click **1. Read project and weather files**. Each set's simulation years come from its
   files; type a first and last year to override them.
4. Enter the range of drain depths and spacings in **feet** (from, to, step). Depths at or
   below the impermeable layer are skipped; equivalent depth and Kirkham G are
   recalculated for every depth and spacing.
5. Click **2. Run design analysis**. Runs go several side by side (set how many) after a
   check that they match a single run. Press Esc to stop and keep what has run.
6. **Charts**: relative yield vs spacing per crop; the buttons switch between overall,
   excess water, drought and planting delay. Colour = drain depth; solid = first weather
   set, dashed = second.

**Results** has the averages for each run; the hidden **Yearly** sheet has every year and
the hidden **Run Log** lists each run (right-click a sheet tab → Unhide).

## Your files

The runs never change your project: they write `<name>_dsg.prj/.gen` (and `_dsg2`,
`_dsg3`, … for runs side by side) next to it. If the project folder was moved or copied
(for example from `C:\DDESIGN`), **1. Read project** (or **Fix paths…**) offers to rewrite
the paths in the project files, keeping each original as `.bak`.

## Code

`vba/modDrain.bas` (all the macros) and `vba/ThisWorkbook.cls` (adds the buttons when the
workbook opens).

## Versions

* **1** – First published version.
