# Tile Slopes

**Workbook:** `TileSlopes.xlsm` · Version 1 ·
[download zip](https://github.com/Bhudus/QGIS_Tools/raw/main/spreadsheets/tile_slopes/tile_slopes.zip)

Sets the tile elevation along a main or a lateral from the ground profile, keeping the
tile within the depth and slope limits, and charts surface, depth limits and tile.

## Use

On **Main** (and **Lateral** for a lateral):

1. Paste the profile: distance, easting, northing and surface elevation every few feet
   along the line (for example from a LiDAR DEM).
2. Enter the limits: upper and lower depth (the target band), absolute upper and lower
   depth (never shallower / deeper), minimum and maximum slope (%), and an offset.
3. The tile follows the middle of the target band where the grade allows; where it would
   be flatter than the minimum or steeper than the maximum slope, it is held at the limit
   slope, but never shallower or deeper than the absolute limits.
4. Optional: a **constant slope** between a start and end distance (tick the box), for
   example to keep a uniform grade through a section.
5. Use the spin buttons to nudge the limits and watch the chart; the chart button rescales
   it to the profile and re-centres the spin buttons.
6. For a lateral, the button on **Main** asks for the lateral's name and its distance along
   the main and passes them to **Lateral**, which also takes the elevation of the bottom
   of the main so the lateral enters above it.

The workbook comes with a short example profile; its coordinates are local (the first
point is at 1000, 1000 ft), not a real location.

## Code

`vba/Module1.bas`: the worksheet functions `Elevation` (table interpolation) and
`TileDepth` (the grade rule). `vba/Main_sheet.cls` and `vba/Lateral_sheet.cls`: the
buttons, check box and spin buttons on each sheet.

## Versions

* **1** – First published version.
