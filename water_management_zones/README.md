# Water Management Zones (QGIS plugin)

Splits a tile-drained field into water management zones, for controlled drainage
and subirrigation alike, and places a water-control structure for each zone. Each structure gets a
board (stop-log) setting, and the tool reports how much of each zone the
managed water table actually controls. It covers the *Select zone elevation
interval*, *Layout zones* and *Develop zonal system layout* steps of the drainage
design process for controlled drainage and subirrigation.

Requires QGIS 3.34 LTR or newer. Uses only QGIS, GDAL and numpy.

## Install

1. Download [`water_management_zones.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/water_management_zones/water_management_zones.zip).
2. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**.
3. Pick the zip and install. (Enable *Show also experimental plugins* under
   Settings if QGIS hides it.)
4. Run it from the toolbar button, **Vector → Tile Drainage**, **Raster → Tile
   Drainage**, **Plugins ▸ Water Management Zones**, or the Processing Toolbox
   (*Water management zones*). It also runs in the graphical modeler, in batch
   mode and from Python:

```python
processing.run("wmzones:watermanagementzones", {
    "DEM": "dem.tif", "FIELD": "field.gpkg",
    "NETWORK": "drainage_network.gpkg",      # optional: leave out for elevation bands
    "UNITS": 1, "INTERVAL": 1.0, "WT_DEPTH": 1.0, "CONTROL_DEPTH": 2.0,
    "ZONES": "TEMPORARY_OUTPUT", "STRUCTURES": "TEMPORARY_OUTPUT",
    "NETWORK_ZONES": "TEMPORARY_OUTPUT", "STATUS": "TEMPORARY_OUTPUT",
    "OPTIONS": "TEMPORARY_OUTPUT"})
```

## Two ways to use it

**Before a layout (no network).** The field is cut into elevation bands one
interval high, from a lightly smoothed DEM. Each connected patch becomes its
own zone, and patches smaller than the *merge zones smaller than* area are
absorbed by their neighbours. Use the bands to plan the zonal layout: mains along the fall line,
with laterals running along the contours inside each band.

**With a layout.** Give it the drainage network from Illini Subsurface Drain
Layout, Subsurface Drainage Layout, Illini Drainage System Design or Tile
Drainage Layout, or one drawn by hand. The tool places the structures on it and
evaluates the result.

**Several drainage systems.** A field can have several separate systems, for
example one per zone, each discharging to a stream along the field edge. Give all
the systems in one network layer, and either an *Outlets* point layer (one point
per system, where it discharges) or tick *Keep every separate drainage system*.
Each system becomes its own tree with its own outlet structure, and structures are
placed on each one. Zones and structures are numbered across all systems, and
every output carries a `system` number. Add your zone polygons (for example the
*Control zones* layer of an earlier run without a network) so each zone's land
is served only by its own system's pipes.

Give outlet points when the systems discharge to a stream. The automatic outlet
is the lowest free pipe end of each system, which may be a lateral end if the
ground falls along the stream rather than toward it. Pipes of different systems
closer than the snap tolerance are joined into one system, so keep the systems
apart or lower the tolerance.

## Inputs

| Parameter | Notes |
|---|---|
| Elevation (DEM) | Any GDAL raster in a **projected** CRS. |
| Field boundary | Polygon layer; all features are used, holes excluded. |
| Drainage network | Optional line layer. See *Reading the network* below. |
| Outlet location | Optional. Default: the lowest free end of the network (by tile elevation, else ground). |
| Outlets, one per separate drainage system | Optional point layer. Each point is the outlet of the system nearest it. See *Several drainage systems* below. |
| Keep every separate drainage system | Off by default. On: every separate system is kept, each rooted at its outlet point or, if it has none, at its lowest free end. Off: only systems with an outlet (or the one holding the network's lowest free end) are kept. |
| Zone polygons | Optional polygon layer. Land inside each polygon drains only to pipe inside the same polygon. Use your band zones, so each zone's system serves only its own zone. |
| Design units | Feet (default) or metres, for every elevation, depth, distance and output. |
| Zone elevation interval | Default 1.0 ft. The most the ground may rise within one zone. NRCS practice 554 guidance is commonly one structure per 1–2 ft of elevation change. |
| Merge zones smaller than | Acres (hectares in metres). Default 0.5; 0 keeps every zone. Applies to both modes; see *Merging small zones* below. |
| Managed water-table depth below the zone's low ground | Default 1.0 ft. Sets the boards: board elevation = zone low ground − this depth. |
| Deepest water table that still counts as controlled | Default 2.0 ft. |
| Place structures on | *Mains and submains* (default) or *Mains only*. |
| Shortest distance between structures | Default 100 ft. |
| Smooth zone boundaries | Off by default. Rounds the zone outlines and removes the pixel staircase and speckle. The smoothing radius is an advanced setting (default 20 ft). See *Smoothing* below. |
| Lateral spacing | Optional. Land farther than ¾ of a spacing from a tile is shown as *not near a tile* and left out of the zone statistics. |

Advanced settings:

- DEM elevation units (default US survey feet).
- What units the network's tile elevations are in: design units (as the layout plugins write them), DEM units, or ignore them.
- Default tile depth where the network has no elevations (4 ft).
- The intervals to compare (`0.5, 1, 1.5, 2, 3`).
- The percentiles used for a zone's low and high ground (5 and 95).
- The structure-placement percentile (25).
- Analysis cell size.
- Snap tolerance for joining pipe ends.
- Whether to join pipes where they cross.
- For elevation bands: the smoothing radius (15 ft).

## How structures are placed

1. **Reading the network.**
   - Lines need not be split at junctions, digitized in the flow direction, or carry node fields.
   - A line whose end touches the middle of another line is joined to it as a tee.
   - Ends closer than the snap tolerance become one node.
   - Lines that cross without meeting at an end are joined where they cross: hand-drawn laterals drawn across a main. You can turn this off.
   - The network is then made into a tree rooted at the outlet, or one tree per system when there are several (see *Several drainage systems*). Lines that don't connect to an outlet are left out and reported.
2. **Tile elevations** come from, in order of preference:
   1. each vertex's Z value (Illini Subsurface Drain Layout 0.13+);
   2. the `ElevDepth1`/`ElevDepth2` or `invert_us`/`invert_ds` fields, straight between the two ends;
   3. the default depth below the DEM.
3. **Mains and submains** come from a `kind` field (`main`, `submain`, `lateral`).
   - Without one, a pipe with tributaries counts as a submain.
   - The main is the submain path from the outlet with the most pipe upstream.
4. **Which land drains where.** Every field cell is assigned to its nearest pipe. Its water reaches the mains and submains where that pipe joins them: the cell's *attach point*.
5. **Placement.** Structures go only inside the field boundary, and only pipe inside the field drains field land. A structure goes at the outlet, or, if the outlet lies outside the field, where the network enters the field (at each place a main or submain enters). Land whose lateral leaves the field before reaching a main or submain inside it can't be controlled: it is shown as draining freely, and the log says how much. The tool then walks up every main and submain from the outlet (or only the main, with *Mains only*):
   - At each point it adds the land attaching there to the current zone. It measures that land's ground as its 25th and 75th percentiles.
   - A new structure is placed at that point when all three hold:
     - the ground of the current zone spans more than the interval;
     - the zone has risen along the collector by at least half the interval, beyond the spread of the land joining at any one point. A lateral that climbs the slope can't be fixed by a structure on the main, so it doesn't trigger one on its own;
     - the point is at least the shortest distance from the last structure.
   - Branches inherit the zone they leave from, and each branch goes on placing its own structures.

   Set the placement percentile to 50 to use the median, or toward 0 for tighter zones.

### Merging small zones

Zones with less land than *merge zones smaller than* are merged, smallest first,
until none is left or only one zone remains:

- **With a network**, a small zone loses its structure. Its land then drains to
  the next structure downstream and joins that zone. The structure at the outlet
  (or where the network enters the field) is always kept: if its zone is small, the smallest zone draining into it
  loses its structure instead and joins it. The log says how many structures
  were dropped. Merged zones span more than the interval, so check their range
  and control.
- **Without a network**, a small patch of an elevation band is absorbed by the
  neighbouring zones.

The interval options table applies the same merging.

## Board settings and control

For each zone:

- **Low ground** is the 5th percentile of the zone's ground, or the ground at the structure if that is lower.
- **Board elevation** = low ground − managed water-table depth. With a level water table held at this elevation, each drained cell is one of:

| Status | Meaning |
|---|---|
| Controlled | The managed water table is above the cell's tile and no deeper than the deepest controlled depth. |
| Water table too deep (high ground) | The ground is more than the deepest controlled depth above the boards. |
| Tile above managed water table | The nearest tile is higher than the boards, so this land drains freely and the structure can't raise its water table. This is typical of laterals running up and down the slope. |
| Managed water table above ground | Low spots in the zone where the boards would bring water to the surface. |
| Not near a tile | Only when a lateral spacing is given. |

## Smoothing

With *Smooth zone boundaries* on:

- Each zone's footprint is blurred over the smoothing radius and resampled to half the analysis cell.
- Every cell is then given to the zone that is strongest there, and the zones are clipped to the field boundary.
- Every point of the field still belongs to exactly one zone, so the smoothed zones have no gaps or overlaps.
- A zone too narrow to survive the blur is drawn unsmoothed, with a warning.

Smoothing changes only how the zones are drawn. The zone statistics (area, ground, boards, control) and the control-status raster still come from the unsmoothed analysis. So a smoothed polygon's measured area can differ slightly from its `area` field.

## Outputs

**Control zones** (polygons):

| Field | Meaning |
|---|---|
| `zone`, `struct_id` | Zone number; the structure controlling it (network mode). |
| `area` | Acres (feet mode) or hectares. |
| `tile_len` | Tile in the zone, in design units (network mode). |
| `ground_min`, `ground_low`, `ground_med`, `ground_high`, `ground_max` | Zone ground: minimum, low and high percentiles, median, maximum. |
| `range` | `ground_high − ground_low`. |
| `board_elev` | Board (stop-log) top elevation. |
| `wt_depth_med` | Depth to the managed water table at the zone's median ground. |
| `pct_ctrl`, `pct_deep`, `pct_free`, `pct_wet` | Share of the zone in each status. |
| `flag` | Ground range over 1.5 × the interval; no control; less than half controlled. |

**Control structures** (points, network mode):

| Field | Meaning |
|---|---|
| `struct_id`, `zone`, `system` | Structure, zone and drainage-system numbers. |
| `lat`, `lon` | Latitude and longitude of the structure, WGS 84 decimal degrees. |
| `ds_struct` | The structure downstream. |
| `on_pipe` | main, submain or lateral. |
| `pipe_size` | `NOMINAL` or `ACTUAL_SIZE` of the pipe, if the network carries one. |
| `ground`, `invert`, `tile_depth` | At the structure. |
| `board_elev` | Board top elevation. |
| `board_height` | Boards above the tile invert: the stop-log stack to buy. |
| `board_depth` | Board top below the ground at the structure. |
| `zone_area`, `up_area` | Area of this zone; area controlled by this and every structure upstream. |
| `zone_low`, `zone_high` | Zone low and high ground. |
| `pct_ctrl` | Share of the zone controlled. |
| `flag` | As for zones. |

**Lowest points and boundary crossings** (points, on by default):

| Field | Meaning |
|---|---|
| `type` | `lowest point`: the lowest ground cell of a zone (one per zone, both modes). `boundary crossing`: a place where a pipe crosses the field boundary (network mode). |
| `zone` | The zone. For a crossing, the zone of the pipe just inside the field. |
| `lat`, `lon` | WGS 84 decimal degrees. |
| `ground` | Surface elevation (DEM) at the point. |
| `tile_elev`, `tile_depth` | Tile elevation and depth below the ground: at a crossing, of the pipe at the boundary; at a lowest point, of the nearest tile (network mode). |
| `direction` | Crossings: `leaves the field` where the water flows out across the boundary (for example the main toward an outlet beyond the field), `enters the field` where it flows in. |
| `on_pipe` | Crossings: main, submain or lateral. |

Lowest points are drawn as blue triangles, crossings as diamonds (red where the
water leaves the field, green where it enters), labelled with their elevation and
tile depth. Lowest points come from the analysis grid, so they are cell centres.

**Network by zone** (lines with tile Z): the network cut at the structures, with
`zone`, `kind`, Strahler `order`, source feature id, length and end inverts.

**Control status** (raster): the five statuses above, styled.

**Interval options** (table): for each interval to compare, the number of
structures (or zones), the share controlled, too deep, draining freely and too
wet, the mean zone area, and the largest zone ground range.

## Notes and limits

- The managed water table is treated as level at the board elevation. Mounding between laterals during subirrigation, and drawdown during drainage, aren't modelled; use DRAINMOD (see the DRAINMOD file-builder plugins) for that.
- Structures go only on mains and submains. Laterals that climb more than the interval inside a zone show up as *tile above managed water table*. For controlled drainage, lay laterals along the contours and run mains down the slope.
- The tool doesn't move pipes. If a zone controls little, try a smaller interval, structures on submains, or a layout with contour laterals.
- The tool remembers the last run while the project stays open. Closing and reopening it brings back every input and setting, including the DEM, field, network and outlet if they're still in the project. Starting a new project, or loading another one, goes back to the plugin defaults. So does **Plugins ▸ Water Management Zones ▸ Reset saved settings to defaults**.

## Development

The analysis is done by the numpy core in `core/`, which has no QGIS imports:
`network.py` builds the network tree (one per drainage system), `allocate.py`
assigns land to its nearest pipe, `zones.py` places the structures and evaluates
the zones, and `smooth.py` smooths the zone boundaries for drawing. `algorithm.py`
is the QGIS Processing wrapper.
