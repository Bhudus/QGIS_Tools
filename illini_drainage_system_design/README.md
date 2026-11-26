# Illini Drainage System Design (QGIS 3 plugin)

Designs a complete subsurface tile drainage system in one run. It chains the Illini Drainage Tools routines **E to K** into a single Processing tool, passing each step's output and field names to the next:

| Step | Routine | What it does |
|---|---|---|
| E | Hydraulic Network Fixer | Cleans the proposed lines into topologically-sound, exploded segments |
| F | Network Flow-Path Generator | Connects the segments into a network flowing to the outlet |
| G | Tile Network Ordering | Strahler tile orders |
| H | Network Elevation Exports | Samples the DEM along every pipe |
| I | Network Flow Lengths | Tile length upstream of every line |
| J | Tile Burying System | Buried elevations, depths and slopes, with Routine J or one of three other burying methods; BURY_CHECK flags grade problems |
| K | Network Pipe Sizing | Drainage areas, flows, actual and nominal pipe sizes |

Steps G to K are done by the engine of [Illini Subsurface Drain Layout](../illini_subsurface_drain_layout/), so the design has the same burying methods and writes the same network as the layout tool.

**Processing Toolbox → Illini Drainage System Design → Drainage System Design** (also on the toolbar)

| Parameter | Notes |
|---|---|
| Proposed Tile Lines | Line layer of the mains, submains and laterals |
| Outlet Location | Click on the map near the outlet end of the main; the closest segment becomes the outlet |
| Original Field DEM | Elevation raster covering the tile lines |
| Coordinate Reference System | Default EPSG:3435 |
| Burying parameters (J) | Upper/lower and absolute depths (absolute lower default 6 ft), max/min slope, offset (+ lowers the outlet), optional constant slope for flat terrain, optional lowering of the outlet for low ground (these four for Routine J) |
| Burying Method | *Routine J* (default): the target depth band. *Minimum cover*: as shallow as the absolute upper depth and the minimum slope allow. *Fewest grade breaks*: each pipe in as few straight runs as the depth and slope limits allow, closest to the target band. *Least cost*: grade breaks balanced against distance from the target depth. |
| Outlet Invert Elevation, Bury from the Outlet Invert | Optional. The outlet invert is checked (the main is flagged if it arrives below it); with *Bury from the Outlet Invert*, the outlet tile is held at it and the network buried upstream from it. |
| Pipe sizing parameters (K) | Burying slope (InSlope or OutSlope), drain spacing, pipe material, drainage intensity |
| Advanced | Line extensions (E); Routine J grade break spacing (default 50 ft); shortest run between grade breaks (default 20 ft) and largest change of grade at a break (default any) for fewest breaks and least cost; cost of a grade break (default 100 ft) and excavation weight (default 0) for least cost; DEM elevation units (default US survey feet); drainage coefficient by system or by tile order (K) |
| Drainage Network | Main output: the Illini drainage network (below) |
| Intermediate layers (E-J) | Optional, under Advanced Parameters; the J layer is Routine J's, whatever the burying method |

Defaults match the individual Illini Drainage Tools. Pipe sizing uses a single drain spacing.

## Output: the Drainage Network

The same form as the network of Illini Subsurface Drain Layout, so Tile Profile, the Tile Size Calculator and its bill of materials work on it directly:

- One line from node to node for every pipe between junctions, flowing from `from_node` to `to_node`, with `ds_link` the line it drains into (0 at the outlet). The drawn lines become one **main** (from the outlet up its longest branch), **submains** (each carrying on up its own longest branch) and **laterals**: a line that no other line drains into is a lateral.
- The tile elevation (Z) and ground elevation (M) at every vertex, in feet, including every grade break; the DEM is sampled along the pipes, not only at their ends.
- Fields: `id`, `pipe_id`, `kind`, `pipe`, `from_node`, `to_node`, `ds_link`, `length`, `TileLength`, `ground_us`, `ground_ds`, `terrain_grade_pct`, `invert_us`, `invert_ds`, `design_grade_pct`, `max_depth`, `min_depth`, `depth_ds`, `upstream_length`, `drained_area` (acres), Routine J's `ElevDepth1`, `BuryDepth1`, `ElevDepth2`, `BuryDepth2`, `InSlope`, `OutSlope`, `BURY_CHECK`, Routine K's `TILE_ORDER`, `FLOW_LENGTH`, `SIZE_SLOPE`, `D_COEFF`, `DRAIN_SPACE`, `D_AREA`, `CUM_SEG_FLOW`, `ACTUAL_SIZE`, `NOMINAL`, `PIPE_TYPE`, and `flag` (layout problems, such as a main arriving below the outlet invert or needing more than 42 in pipe).
- Every drawn line is drained tile: a line is sized for the tile length upstream of it (`FLOW_LENGTH`) times the drain spacing, and `drained_area` is that area.
- Mains and submains are drawn red, laterals blue, layout problems orange and grade problems (BURY_CHECK) purple.

Segments that close a loop, or are not connected to the outlet, are left out, with a warning in the log.

This replaces the *Network Pipe Sizings* layer of version 2.

## Re-burying a layout from Illini Subsurface Drain Layout

To re-bury a layout after changing it (moving, adding or deleting pipes):

1. Edit the **Surface Layout** layer of Illini Subsurface Drain Layout (its pipes as plain lines), keeping the pipes joined end to end where they meet.
2. Run Drainage System Design with that layer as the *Proposed Tile Lines*, the outlet clicked at the outlet end of the main, the same DEM, and the same drain spacing, depths and slopes as the layout.
3. The Drainage Network it writes replaces the layout's, ready for Tile Profile and the Tile Size Calculator.

Every line is treated as drained tile, as in the layout. The pipes are rebuilt from the outlet up the longest branches; the lines nothing drains into come out as laterals (`kind`), as in the layout. Where the longest branch runs up a drawn lateral, that lateral carries on the main or submain and the rest of the line it joined becomes a lateral.

## Install
Download [`illini_drainage_system_design.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/illini_drainage_system_design/illini_drainage_system_design.zip), then in QGIS use
`Plugins → Manage and Install Plugins → Install from ZIP`, choose `illini_drainage_system_design.zip`, tick the plugin.
Requires QGIS ≥ 3.16. No SAGA or other add-on Processing provider is needed.
If the earlier **Illini System Design** plugin is installed, uninstall it first; this version replaces it.
Can be installed alongside Illini Drainage Tools.

The plugin is also listed under **Raster → Tile Drainage**, a menu shared by the drainage tools in this repository, as well as under **Plugins → Illini Drainage System Design**.

## Credits
Based on [Illini Drainage Tools](https://github.com/FVW-Services/Illini-Drainage-Tools) by Anamelechi Falasy and Richard Cooke (GPL v2 or later).
Includes the per-segment drainage coefficient fix for Network Pipe Sizing.
