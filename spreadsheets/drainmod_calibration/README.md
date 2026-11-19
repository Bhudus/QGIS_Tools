# DRAINMOD Calibration

**Workbook:** `DRAINMOD_Calibration.xlsm` · Version 1 ·
[download zip](https://github.com/Bhudus/QGIS_Tools/raw/main/spreadsheets/drainmod_calibration/drainmod_calibration.zip)

Calibrates a DRAINMOD project to measured daily drain flow, water table depth or both,
by maximizing the Kling-Gupta efficiency (KGE). The search is Dynamically Dimensioned
Search (DDS, Tolson and Shoemaker 2007), with several DRAINMOD runs side by side and a
final local search ("polish") from the best values. A sensitivity analysis (Morris
elementary effects) ranks the parameters first, to help choose which ones to calibrate.

## Needs

* Windows Excel with macros enabled.
* DRAINMOD (`dmhydro.exe`, from
  [NC State University](https://bae.ncsu.edu/agricultural-water-management/drainmod/download/))
  and a DRAINMOD project (.prj with its .gen) with daily output turned on.
* Measured daily drain flow and/or water table depth.

## Use

On the **Setup** sheet:

1. Select the project (.prj) and `dmhydro.exe`. If the project folder was moved or
   copied, the runs use the paths of where it is now, and **Fix paths...** rewrites the
   paths in the project files themselves (keeping each original as .bak).
2. Paste the measured data on the **Measured** sheet: date, daily drain flow, daily water
   table depth (blank where not measured). Set the units, sign and what to calibrate to
   on the Setup sheet; KGE 2012 is the default, 2009 can be chosen.
3. **1. Read project parameters** fills the current value of each parameter on the
   **Parameters** sheet and sets the calibration period to the simulation period.
4. On the Parameters sheet, give each parameter a minimum and maximum and set
   **Calibrate?** to Yes for those to fit. Parameters: lateral Ksat of each soil layer
   (or a multiplier for all layers), maximum surface storage, Kirkham depth, drainage
   coefficient, drain effective radius, vertical seepage K and piezometric head, PET
   multiplier, root depth multiplier, drain depth, depth to the impermeable layer,
   lateral seepage layer thickness, initial water table depth and drain spacing. The
   equivalent depth and Kirkham's G are recomputed whenever the drain geometry changes.
5. **2. Evaluate current values** runs DRAINMOD once and shows the fit on the
   **Results** sheet.
6. **3. Sensitivity analysis** (optional) tests every parameter with a range that applies
   to the project, ticked or not. The **Sensitivity** sheet ranks them (1 = most
   sensitive) by mu\*, the mean absolute change in the objective when a parameter moves
   across its range, with sigma (non-linearity and interactions), the effect on the flow
   and water table KGE separately, and a bar chart; the ranks also go in column J of the
   Parameters sheet. It takes trajectories × (parameters + 1) runs (10 trajectories by
   default, set on the Setup sheet).
7. **4. Run calibration** runs DDS and the polish. Each run is logged on the **Run Log**
   sheet; press Esc to stop early and keep the best values so far. Runs side by side are
   used only after a check that they reproduce a single run exactly, and a run longer
   than the time limit is stopped.
8. **5. Write calibrated project** saves `<name>_calibrated.prj/.gen` next to the
   project with the best values.

Your project files are never changed by the runs: they write `<name>_cal.prj/.gen` (and
`_cal2`, `_cal3`, ... for runs side by side) next to them. The **Graphs** sheet charts
measured and simulated drain flow and is refreshed after each evaluation.

## Code

`vba/modCalib.bas` (all the macros) and `vba/ThisWorkbook.cls` (adds the buttons when the
workbook opens).

## Versions

* **1** – First published version.
