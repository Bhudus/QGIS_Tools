# Tile Profile (QGIS plugin)

Select a lateral or main in a tile drainage network and a docked panel draws
its profile: the ground surface, the four tile-depth lines and the tile.

Made for the **Drainage network** layer of Illini Subsurface Drain Layout
(0.13 or later for full detail); it also reads the network of Subsurface
Drainage Layout and other line layers with tile elevations.

Requires QGIS 3.34 LTR or newer. Uses only QGIS.

## Install

1. Download [`tile_profile.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/tile_profile/tile_profile.zip).
2. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**.
3. Pick `tile_profile.zip` and install. (Enable *Show also experimental
   plugins* under Settings if QGIS hides it.)

## Use

1. Click the **Tile Profile** toolbar button (or **Vector ▸ Tile Drainage ▸
   Tile Profile**, a menu shared by the drainage tools in this repository, or
   **Plugins ▸ Tile Profile**).
   The panel opens at the bottom of the window and picks the network layer.
2. Select any line of a lateral, submain or main, with the usual select tool
   or with **Pick a pipe** in the panel. The panel draws the profile of the
   whole pipe that line belongs to, and the pipe is highlighted on the map.
3. Move the mouse over the chart to read station, ground, tile and depth,
   and the slope of the tile section under the cursor: the stretch between
   two grade breaks or junctions, highlighted on the chart with its stations.
   Slopes are positive when the tile falls toward the outlet; a section
   that runs flat or uphill is marked (flat) or (reverse).
4. **Save image…** writes a PNG; **Save table…** writes a CSV with station,
   ground, tile, depth and the four depth lines.
5. **Lower network by** moves the whole tile network down by that amount
   (a negative value raises it). The profile, the depth check, the hover
   readout and both exports show it at once, as a preview; the layer is not
   changed. **Apply to layer…** writes it into the network layer after you
   confirm: every tile elevation (the Z values, `invert_us`/`invert_ds`,
   `ElevDepth1`/`ElevDepth2`) goes down by the offset and every depth
   (`min_depth`, `max_depth`, `depth_ds`, `BuryDepth1`/`BuryDepth2`) goes up
   by it. Ground elevations and slopes do not change, and the `flag` and
   `BURY_CHECK` fields are not checked again. The change is one edit, and
   the layer is left in edit mode: save the edits to keep them, undo them,
   or roll them back.
6. **Check network** finds the profiles to check. It goes through every
   pipe of the network, sampling the tile every third of a cell against the
   absolute upper and lower depths (and any offset you are previewing), and:
   - lists the pipes where the tile is shallower than the absolute upper
     depth or deeper than the absolute lower depth, worst first, with the
     length of tile outside each limit and the pipe's shallowest and deepest
     tile. Click a pipe in the list to open its profile and pan the map to it.
   - adds two raster layers in a **Tile Profile check** group: **Tile
     elevation** (the tile coloured from low to high) and **Tile depth
     check** above it, which shows only the cells outside the limits, red
     where the tile is too shallow and purple where it is too deep. The file
     has three bands: tile elevation, tile depth (ground minus tile) and the
     depth check (1 within the limits, 2 too shallow, 3 too deep), in the
     panel's units. Checking again replaces them. They are temporary files:
     use **Export ▸ Save As…** to keep them.

   Choose a DEM for the check: the raster then uses the DEM's own cells and
   the ground is the DEM everywhere along the tile. Without a DEM the ground
   is straight between the line's vertices, so dips between them are missed,
   and the raster uses 1 m cells.

## What the profile shows

| Line | Meaning |
|---|---|
| Ground (brown) | The ground surface along the pipe. |
| Target band (green, dashed, shaded) | Ground minus the upper and lower tile depth. |
| Absolute limits (orange, dotted) | Ground minus the absolute upper and lower depth. |
| Tile (blue) | The tile invert, with a dot at every grade break. Red where it is shallower than the absolute upper depth or deeper than the absolute lower depth. |
| Junctions (dotted verticals, triangles) | Where other pipes join this one. |

Stations run from the pipe's downstream end (0 at the junction or outlet) up
the pipe. The title gives the pipe's length and nominal sizes.

## Settings

| Setting | Default | Notes |
|---|---|---|
| Network | the first layer with `pipe_id` and `to_node` fields | Any line layer works; without `pipe_id`, the selected line alone is profiled. |
| DEM | none | With none, the ground comes from the layer. Choose a DEM for a detailed ground line sampled along the pipe. |
| DEM elevations in | US survey feet | Units of the DEM's elevations. |
| Units | ft | The design units of the network. Stations are converted from the map units of the layer's CRS. |
| Upper / Lower tile depth | 3.25 / 4.25 ft | The target band (Routine J defaults; 1.0 / 1.3 m in metres). |
| Absolute upper / lower | 2.5 / 6.0 ft | The absolute limits (0.75 / 1.8 m in metres). |
| Lower network by | 0 | Preview offset for the whole network, in the units above; not remembered. |

Set the four depths to the values used when the network was designed. The
settings, apart from the offset, are remembered between sessions.

## Where the elevations come from

- **Tile**: the Z value of each vertex. Illini Subsurface Drain Layout 0.13+
  writes the tile elevation at every vertex and keeps every Routine J grade
  break as a vertex, so the profile shows the tile exactly. Without Z values
  the tile is drawn straight between each line's ElevDepth1 and ElevDepth2
  (or invert_us and invert_ds) fields.
- **Ground**: a DEM if one is chosen; else the M value of each vertex
  (Illini Subsurface Drain Layout 0.13+); else straight between each line's
  ground_us and ground_ds fields.

## Licence

GNU General Public License v3 or later.
