# Tile Slopes

**Workbook:** `TileSlopes.xlsm` · Version 2 ·
[download zip](https://github.com/Bhudus/QGIS_Tools/raw/main/spreadsheets/tile_slopes/tile_slopes.zip)

Sets the tile elevation along a main or a lateral from the ground profile, keeping the
tile within the depth and slope limits, and charts surface, depth limits and tile.

## Use

On **Main** (and **Lateral** for a lateral):

1. Paste the profile: distance, easting, northing and surface elevation every few feet
   along the line (for example from a LiDAR DEM).
2. Enter the limits: upper and lower depth (the target band), absolute upper and lower
   depth (never shallower / deeper), minimum and maximum slope (%), and an offset.
3. Choose the **burying method** in L25 (the methods of Illini Subsurface Drain Layout):
   * **Routine J** – the tile follows the middle of the target band where the grade
     allows; where it would be flatter than the minimum or steeper than the maximum
     slope, it is held at the limit slope, but never shallower or deeper than the
     absolute limits (the method of version 1).
   * **Minimum cover** – as shallow as the absolute upper depth and the minimum slope
     allow, worked out from the top of the line down.
   * **Fewest grade breaks** – the fewest straight runs that keep within the absolute
     depths and the slopes; of those, the one closest to the target depth.
   * **Least cost** – each grade break costs as much as the break cost (ft of tile 1 ft
     off the target depth), weighed against distance from the target depth, plus
     excavation when an excavation weight is given.

   Below the method (K26:L30): an **outlet invert** (blank = free; the tile at the outlet,
   with the offset), the shortest run between grade breaks, the largest grade change at
   a break (%, 0 = any), the break cost and the excavation weight. L31:L33 count the grade
   breaks and the rows to check, and give the outlet depth. The **Burying notes** column
   (X on Main, Y on a lateral) marks each grade break and each row outside a limit
   ("Check: ..."); when no profile fits the limits, they are eased a step at a time and
   the first row says so. Column W gives the tile depth below the ground.
4. Optional: a **constant slope** between a start and end distance (tick the box), for
   example to keep a uniform grade through a section. It is held in every method; the
   method buries the tile below and above it.
5. Use the spin buttons to nudge the limits and watch the chart; the chart button rescales
   it to the profile and re-centres the spin buttons.
6. For a lateral, the button on **Main** asks for the lateral's name and its distance along
   the main and passes them to **Lateral**, which also takes the elevation of the bottom
   of the main so the lateral enters above it. On **Lateral** the method defaults to
   *Same as main*, and rows 27-30 follow the Main sheet until a value is typed over them.

The workbook comes with a short example profile; its coordinates are local (the first
point is at 1000, 1000 ft), not a real location.

## Code

`vba/Module1.bas`: the worksheet functions `Elevation` (table interpolation),
`TileDepth` (the version 1 grade rule) and `BuryTile` (the burying methods, entered as
one array formula over the tile, depth and notes columns). `vba/Main_sheet.cls` and `vba/Lateral_sheet.cls`: the
buttons, check box and spin buttons on each sheet.

## Versions

* **2** – Burying methods of Illini Subsurface Drain Layout (Routine J, minimum cover,
  fewest grade breaks, least cost), chosen on each sheet, with outlet invert, grade-break
  options, a notes column and a count of grade breaks. The Main sheet's tile now uses the
  Main sheet's own slope and absolute depth limits (it used the Lateral sheet's).
  Fewest grade breaks and least cost work on the whole line, so a long profile can take
  a moment to recalculate. Enable macros when the workbook opens.
* **1** – First published version.
