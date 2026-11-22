# Illinois Soil Depth Spacing Calculator

**Workbook:** `Illinois_Drain_Spacing.xlsm` · Version 1 ·
[download zip](https://github.com/Bhudus/QGIS_Tools/raw/main/spreadsheets/illinois_drain_spacing/illinois_drain_spacing.zip)

Drain spacing for Illinois soils. Pick a soil and the workbook fills in its drainage group,
hydraulic conductivity, drainable porosity and profile depth from a table of 237 Illinois
soils. It then calculates subsurface drain spacing three ways:

* **Steady state**: the Hooghoudt equation (or Kirkham's equation for ponded water).
  Gives the spacing for a drainage coefficient, or the drainage coefficient for a spacing.
* **Transient**: the van Schilfgaarde falling water table equation, elliptic or parabolic.
  Gives the spacing for a drawdown time, or the drawdown time for a spacing.
* **Subirrigation**: the NRCS NEH 624 Chapter 10 subirrigation equation for tubing.
  Gives the spacing that holds the mid-drain water table up against a given
  evapotranspiration (irrigation coefficient).

All three use Hooghoudt's equivalent depth (van der Molen & Wesseling series) and a 4-inch
corrugated drain (effective radius 0.017 ft, NEH 624 Table 10-1a).

## Use

On the **Spacing** sheet:

1. Pick the soil in the **Soil Name** cell (an in-cell drop-down; you can also type the name).
2. Optionally tick **Adjust Profile Depth** to enter a site-specific depth to the
   impermeable layer.
3. Click **Calculate Drain Spacing** and choose the Steady State, Transient or
   Sub Irrigation tab.

**Navigate to Official Soil Series Description** opens the soil's USDA description,
**View Drainage Groups** shows the Illinois drainage group recommendations, and
**View General Recommendations** shows the general recommendations.

Units: depths and heights in feet, hydraulic conductivity in ft/day, drainage and
irrigation coefficients in in/day.

## Opening the workbook

Windows blocks macros in downloaded files: before opening, right-click the zip →
**Properties** → tick **Unblock** → **OK**, then extract it and click **Enable Content**
in Excel. A splash screen shows for a few seconds when the workbook opens (click it to
close it sooner).

The sheet's buttons, check boxes and drop-down are Form Controls and cell validation, not
ActiveX controls, so the workbook works with Microsoft 365 and Office 2024, which disable
ActiveX by default.

## Code

`vba/` holds the workbook's macros as text, for reading; the workbook already contains them.

* `formSpacing.frm`: the calculator window, with the spacing equations
  (`cmdCalculate_Click`, `cmdTCal_Click`, `cmdSbCalculate_Click`) and the equivalent
  depth (`effdpt2`).
* `Spacing_Sheet9.cls`: the Spacing sheet's controls, soil lookup and splash screen.
* `ThisWorkbook.cls`: sets up the controls, soil list and splash screen when the
  workbook opens.

## Versions

* **1** – First published version. Compared with the earlier unpublished workbook:
  the drain is fixed at 4 inches (the drain-diameter list was never filled, so the
  calculations used a 12-inch drain's effective radius); the equivalent-depth series
  for deep impermeable layers includes its missing 1/n factor; the subirrigation
  calculation stops on a zero irrigation coefficient or an impermeable layer at or
  above the drain; the ActiveX controls are replaced; soils whose drainage group has no
  picture (Algansee, Raveenwash, Medway) no longer stop the macro; splash screen added.
