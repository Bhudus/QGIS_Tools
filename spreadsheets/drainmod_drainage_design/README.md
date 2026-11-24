# DRAINMOD Drainage Design

**Workbook:** `DRAINMOD_DrainageDesign.xlsm` · Version 2 ·
[download zip](https://github.com/Bhudus/QGIS_Tools/raw/main/spreadsheets/drainmod_drainage_design/drainmod_drainage_design.zip)

Runs a DRAINMOD project for every combination of weather set, drain depth and drain
spacing, and plots DRAINMOD's average relative yield against drain spacing: one chart per
crop, one line per drain depth and weather set. Use it to choose a depth and spacing
that hold up under both current and projected (future) weather.

For one chosen depth and spacing, the **POE analysis** gives the probability of
exceedance of daily drain flow under each weather set: daily flow statistics, the design
drain flow at a P.O.E. (or the P.O.E. of a flow), monthly design flows and the empirical
distribution function, with the current and future weather on the same charts. It is a
port of the MonthlyPOE (DrainPOE) Visual Basic program.

## Needs

* Windows Excel with macros enabled.
* DRAINMOD (`dmhydro.exe`, from
  [NC State University](https://bae.ncsu.edu/agricultural-water-management/drainmod/download/))
  and a DRAINMOD project (.prj with its .gen) that runs with a
  crop sequence and yield inputs.
* Rainfall (.RAI) and temperature (.TEM) files for the current weather and, optionally,
  for a future climate (for example from the
  [DRAINMOD Climate-Scenario Weather Files](../../drainmod_climate/) plugin).

## Example project (DDESIGN)

The zip includes an example, the **DDESIGN** folder: a Drummer soil project for a
corn–soybean rotation (`inputs\Design.prj`), Champaign weather for the current climate
(1980–2010, `Champaign0`) and a future climate (2040–2099, `Champaign1`), and soil, crop
and DSSAT files for Illinois soils. The workbook is already set up for it. DRAINMOD itself
is not included: you add it.

1. Extract the zip and move the **DDESIGN** folder to `C:\DDESIGN`.
2. Install DRAINMOD (free from NC State University:
   <https://bae.ncsu.edu/agricultural-water-management/drainmod/download/>) and copy
   `dmhydro.exe` and `DMSOILP.EXE` from its program folder into `C:\DDESIGN\Program`.
3. Open the workbook (see *Opening a downloaded workbook* in the
   [spreadsheets README](../README.md)) and click **1. Read project and weather files**,
   then **2. Run design analysis**.

The folder can go elsewhere too: select its `inputs\Design.prj` on the Setup sheet and
the workbook offers to rewrite the paths in the project files (keeping the originals as
`.bak`).

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

### POE analysis (daily drain flow)

1. Under **POE analysis** on the Setup sheet, enter one drain depth and spacing in feet
   (default 3.5 ft and 80 ft; a blank cell uses the default).
2. Click **4. Run POE analysis**. DRAINMOD runs once per weather set with that drain, and
   its daily drain flow is read from the run's daily summary (`.DAY`).
3. The **POE Calculator** sheet shows, for each weather set side by side: number of days
   and of flow days, maximum, average (the median of the flow days, as in MonthlyPOE)
   and mean annual drain flow, the P.O.E. and design drain flow, and the design flow of
   each month.
   * Change the **P.O.E. (%)** to get the design flow, or type a **design drain flow**
     to get its P.O.E.: the results update at once, with no new DRAINMOD runs.
   * Charts (colour = weather set): monthly design flows (columns) with each set's annual
     design flow (dashed line), and the empirical distribution function of daily drain
     flow (P.O.E. against drain flow).
   * The daily flows of each run are on the hidden **POE Daily** sheet.

The statistics follow MonthlyPOE step by step, so they match it for the same daily data.

### DRAINMOD output files

Whatever the project's own output settings, the runs set them in their copies of the
`.gen` (the Printout and Input Control line): design runs write no daily (`.DAY`),
monthly (`.MON`) or plot (`.PLT`) files, and POE runs write `.DAY` (and, with it,
`.MON`) but no `.PLT`.

## Your files

The runs never change your project: they write `<name>_dsg.prj/.gen` (and `_dsg2`,
`_dsg3`, … for runs side by side) next to it. If the project folder was moved or copied
(for example from `C:\DDESIGN`), **1. Read project** (or **Fix paths…**) offers to rewrite
the paths in the project files, keeping each original as `.bak`.

## Code

`vba/modDrain.bas` (all the macros), `vba/ThisWorkbook.cls` (adds the buttons when the
workbook opens) and `vba/Sheet6.cls` (the POE Calculator sheet: recalculates when the
P.O.E. or design flow is changed).

## Versions

* **2** – POE analysis: **4. Run POE analysis** and the POE Calculator sheet (probability
  of exceedance of daily drain flow, monthly design flows, both weather sets on each
  chart). Design runs no longer write daily, monthly or plot files.
* **1** – First published version.
