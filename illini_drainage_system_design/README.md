# Illini Drainage System Design (QGIS 3 plugin)

Designs a complete subsurface tile drainage system in one run. It chains the Illini Drainage Tools routines **E to K** into a single Processing tool, passing each step's output and field names to the next:

| Step | Routine | What it does |
|---|---|---|
| E | Hydraulic Network Fixer | Cleans the proposed lines into topologically-sound, exploded segments |
| F | Network Flow-Path Generator | Connects the segments into a network flowing to the outlet |
| G | Tile Network Ordering | Assigns flow, burying and Strahler orders |
| H | Network Elevation Exports | Samples the DEM; segment lengths and slopes, end point elevations and terrain profile points |
| I | Network Flow Lengths | Accumulates segment lengths downstream |
| J | Tile Burying System | Buried elevations, depths and slopes; BURY_CHECK flags segments outside the slope limits |
| K | Network Pipe Sizing | Drainage areas, flows, actual and nominal pipe sizes |

**Processing Toolbox → Illini Drainage System Design → Drainage System Design** (also on the toolbar)

| Parameter | Notes |
|---|---|
| Proposed Tile Lines | Line layer of the mains and submains |
| Outlet Location | Click on the map near the outlet end of the main; the closest segment becomes the outlet |
| Original Field DEM | Elevation raster covering the tile lines |
| Coordinate Reference System | Default EPSG:3435 |
| Burying parameters (J) | Upper/lower and absolute depths (absolute lower default 6 ft), max/min slope, offset (+ lowers the outlet), optional constant slope for flat terrain, optional lowering of the outlet for low ground |
| Pipe sizing parameters (K) | Burying slope (InSlope or OutSlope), drain spacing, pipe material, drainage intensity |
| Advanced | Line extensions (E), segment length (H), drainage coefficient by system or by tile order (K) |
| Network Pipe Sizings | Main output |
| Intermediate layers (E-J) | Optional, under Advanced Parameters |

Defaults match the individual Illini Drainage Tools. Pipe sizing uses a single drain spacing.

## Install
Download [`illini_drainage_system_design.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/illini_drainage_system_design/illini_drainage_system_design.zip), then in QGIS use
`Plugins → Manage and Install Plugins → Install from ZIP`, choose `illini_drainage_system_design.zip`, tick the plugin.
Requires QGIS ≥ 3.16. No SAGA or other add-on Processing provider is needed.
If the earlier **Illini System Design** plugin is installed, uninstall it first; this version replaces it.
Can be installed alongside Illini Drainage Tools.

## Credits
Based on [Illini Drainage Tools](https://github.com/FVW-Services/Illini-Drainage-Tools) by Anamelechi Falasy and Richard Cooke (GPL v2 or later).
Includes the per-segment drainage coefficient fix for Network Pipe Sizing.
