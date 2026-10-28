# Illini System Design (QGIS 3 plugin)

Designs a complete subsurface tile drainage system in one run. It chains the Illini Drainage Tools routines **E to K** into a single Processing tool, passing each step's output and field names to the next:

| Step | Routine | What it does |
|---|---|---|
| E | Hydraulic Network Fixer | Cleans the proposed lines into topologically-sound, exploded segments |
| F | Network Flow-Path Generator | Connects the segments into a network flowing to the outlet |
| G | Tile Network Ordering | Assigns flow, burying and Strahler orders |
| H | Network Elevation Exports | Samples the DEM; segment lengths and slopes |
| I | Network Flow Lengths | Accumulates segment lengths downstream |
| J | Tile Burying System | Buried elevations, depths and slopes |
| K | Network Pipe Sizing | Drainage areas, flows, actual and nominal pipe sizes |

**Processing Toolbox → Illini System Design → System Design** (also on the toolbar)

| Parameter | Notes |
|---|---|
| Proposed Tile Lines | Line layer of the mains and submains |
| Outlet Location | Click on the map near the outlet end of the main; the closest segment becomes the outlet |
| Original Field DEM | Elevation raster covering the tile lines |
| Coordinate Reference System | Default EPSG:3435 |
| Burying parameters (J) | Upper/lower and absolute depths, max/min slope, offset, optional constant slope for flat terrain |
| Pipe sizing parameters (K) | Burying slope (InSlope or OutSlope), drain spacing, pipe material, drainage intensity |
| Advanced | Line extensions (E), segment length (H), drainage coefficient by system or by tile order (K) |
| Network Pipe Sizings | Main output |
| Intermediate layers (E-J) | Optional, under Advanced Parameters |

Defaults match the individual Illini Drainage Tools. Pipe sizing uses a single drain spacing.

## Install
`Plugins → Manage and Install Plugins → Install from ZIP`, choose `illini_system_design.zip`, tick the plugin.
Requires QGIS ≥ 3.16 and the **Processing Saga NextGen Provider** plugin (used by step H).
Can be installed alongside Illini Drainage Tools.

## Credits
Based on [Illini Drainage Tools](https://github.com/FVW-Services/Illini-Drainage-Tools) by Anamelechi Falasy and Richard Cooke (GPL v2 or later).
Includes the per-segment drainage coefficient fix for Network Pipe Sizing.
