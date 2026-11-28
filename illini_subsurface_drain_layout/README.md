# Illini Subsurface Drain Layout (QGIS plugin)

Automated layout, burying and pipe sizing of a subsurface (tile) drainage network from a
DEM, a field boundary, an outlet location and the lateral spacing. Laterals run
near parallel or near perpendicular to the field contours, in a
parallel/gridiron or herringbone pattern, as straight lines or bending with the
terrain. The tile is buried with the scheme of Routine J (Tile Burying System)
of the [Illini Drainage Tools](https://github.com/FVW-Services/Illini-Drainage-Tools)
(or, if you choose, as shallow as the minimum cover and grade allow),
and sized with Routine K (Network Pipe Sizing), using the Strahler tile order
of Routine G. The output carries the Routine J and K fields, so it can go on
to Routine L (Sized Pipe Estimations) and the other Illini routines.

Requires QGIS 3.34 LTR or newer. Uses only QGIS, GDAL and numpy.

## Install

1. Download [`illini_subsurface_drain_layout.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/illini_subsurface_drain_layout/illini_subsurface_drain_layout.zip).
2. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**.
3. Pick `illini_subsurface_drain_layout.zip` and install. (Enable *Show also
   experimental plugins* under Settings if QGIS hides it.)
4. Run it from the toolbar button, **Plugins ▸ Illini Subsurface Drain Layout**,
   or the Processing Toolbox (*Illini subsurface drain layout*). As
   a Processing algorithm it also works in the graphical modeler, in batch
   mode and from Python:

```python
processing.run("illinidrainlayout:illinisubsurfacedrainlayout", {
    "DEM": "dem.tif", "FIELD": "field.gpkg", "OUTLET": "612345.0,4441234.0 [EPSG:26916]",
    "MAIN": "my_main.gpkg",   # optional
    "SPACING": 80, "PATTERN": 1, "ORIENTATION": 1, "SHAPE": 1,
    "NETWORK": "TEMPORARY_OUTPUT", "SURFACE_LAYOUT": "TEMPORARY_OUTPUT"})
```

The plugin is also listed under **Raster → Tile Drainage**, a menu shared by the drainage tools in this repository, as well as under **Plugins → Illini Subsurface Drain Layout**.

## Inputs

| Parameter | Notes |
|---|---|
| Elevation (DEM) | Any GDAL raster in a **projected** CRS (metres or feet). LiDAR DEMs at 0.5–3 m work well. |
| Field boundary | Polygon layer. All features are merged; holes (waterways, buildings) are excluded. |
| No-tile zones | Optional polygon layer of areas where no tile goes: ponds, waterways, building sites, wetlands. They are cut out of the field like holes in the boundary: laterals keep the boundary setback (default half the spacing) from their edges and are split where they would cross one, and mains and submains are routed around them. The log gives the area cut out. |
| Mains and submains may cross the no-tile zones | For zones laterals stay out of but a main may run along or across, such as grassed waterways. The laterals keep the setback from the zones and reach a main inside one by a straight connector. Default off. |
| Outlet location | Click on the map. It can sit just outside the field; the main is extended to it. Optional when a main line is given. |
| Main line(s) | Optional line layer with your own main, and any submains. Leave empty to route the main automatically (see below). |
| Design units | Feet (default) or metres, for spacing, cover, depths and the layer outputs. |
| Report units | *US customary* (default: acres and feet) or *Metric* (hectares and metres), for the layout report and the summary in the log, whatever the design units. |
| Lateral spacing | In design units. Default 80 ft. |
| Layout pattern | *Parallel / gridiron* or *Herringbone* (see below). |
| Lateral orientation | *Down the slope* (near perpendicular to contours; default) or *Across the slope* (near parallel). |
| Lateral shape | *Straight* (default), *Contour-following*, or *8-point*: contour-following, but every leg in one of the 8 compass directions. |
| Straight laterals: bearing | *Follow the terrain* (default: set by the lateral orientation), *North-south*, *East-west*, *Northwest-southeast* or *Northeast-southwest*, on the map grid of the DEM's CRS. Straight laterals only. |
| Parallelism | Contour-following and 8-point laterals only, 0–1 (default 0.9). Steers each lateral to run parallel to its neighbour at exactly one spacing; 0 follows the terrain freely, 1 makes every lateral an offset of its neighbour. |
| Burying method | *Routine J* (default): the Illini target depth band, below. *Minimum cover*: as shallow as the minimum cover and minimum grade allow. *Fewest grade breaks*: each pipe in as few straight runs as the depth and slope limits allow. *Least cost*: grade breaks balanced against distance from the target depth (see How the layout is built, step 5). |
| Fewest breaks / least cost: shortest run between grade breaks | Advanced. No straight run shorter than this, except at the top of a pipe. Default 20 ft. |
| Fewest breaks / least cost: largest change of grade at a break | Advanced, percent. The grade may change by no more than this at a grade break, for plows and laser grade control. Default 0 (any change). |
| Least cost: cost of a grade break | Advanced. A grade break costs as much as this length of tile 1 ft (1 m in metres) off the target depth. Default 100 ft. |
| Least cost: excavation weight | Advanced. Adds a cost for depth, pulling the tile shallower: 1 makes a foot more depth cost as much as being a foot off the target. Default 0. |
| Burying: upper and lower tile depth | The target depth band (Routine J). Tile aims for the middle of it. Defaults 3.25 ft and 4.25 ft. |
| Burying: absolute upper and lower tile depth | Tile is never shallower or deeper than these (minimum cover, maximum depth). Defaults 2.5 ft and 6 ft. |
| Burying: maximum slope; minimum slope for laterals and for mains | Percent. Defaults 5%, 0.1% and 0.1%. |
| Burying: offset depth | Added depth at the outlet (+ lowers it). Default 0. |
| Burying: constant slope | For flat terrain only: every segment upstream of the outlet segment is laid at this slope (default 0.5%). Off by default. |
| Burying: lower the outlet for low ground | Deepens the outlet, down to the lower tile depth, when low ground upstream cannot keep the minimum slope. Off by default. |
| Sizing: size the pipes | Routine K pipe sizing. On by default. |
| Sizing: pipe material | Single Wall (default), Smooth Wall, or Clay or Concrete. |
| Sizing: drainage coefficient | inch/day, for every line (default 0.375). To use other coefficients by tile order or pipe by pipe, resize afterwards with the Tile Size Calculator plugin. |
| Outlet invert elevation | Optional, in DEM elevation units. Flags the main if it arrives below the outlet. |
| Bury from the outlet invert | Holds the outlet tile at the outlet invert elevation and buries the network upstream from it, with any burying method. Needs the outlet invert. Off by default. |

Advanced: DEM elevation units (m / ft / US survey ft; default US survey ft), target grade for
contour-following laterals (default 0.1%), sharpest bend (default 120°),
herringbone angle (default 60°),
boundary setback (default half the spacing), terrain smoothing radius (default
the spacing), analysis cell size (automatic), where the main ends and how large
a branch must be to get a submain, and the longest burying segment between
grade breaks (default 50 ft); and, for sizing, whether to size with InSlope
(default) or OutSlope.

## How the layout is built

1. **Terrain.** The DEM is resampled to the analysis grid and smoothed, so
   laterals follow the field's shape rather than LiDAR micro-relief. The fall
   line and contour direction are taken from the smoothed surface.
2. **Collectors.**
   - *Your own main*: if you supply a main line layer, the line with an end
     nearest the outlet is the main and flows to that end; every other line is
     a submain that flows to its end nearest the main (or another submain) and
     joins it there. Direction of digitizing doesn't matter, and multipart
     lines are split into their parts. Lines may run outside the field, e.g.
     out to a ditch; where they leave the DEM, ground is carried along from the
     nearest DEM data and you're warned. If no outlet is given, the lowest end
     of the lines is the outlet. Laterals are then laid around your main as
     usual; the pattern only sets how they leave it (herringbone angle for
     straight laterals).
   - *Herringbone*: a priority-flood from the outlet builds the drainage tree
     of the field. The main follows its largest branch (the thalweg); branches
     draining more than the submain threshold become submains. Mains end where
     their drainage area drops below the stop threshold.
   - *Parallel / gridiron*: one main runs from the outlet perpendicular to the
     laterals — up the slope when laterals run across it, or as a header
     across the low side (both directions from the outlet, rising at the
     minimum main grade and sliding along the field edge) when laterals run
     down it. In a valley, the fall line up from the outlet leaves the valley
     floor up one side, and the far side, lower than that main, could not drain
     to it: where a fall-line main would leave at least 10% of the field lower
     than it by more than the depth band (min cover to max depth), and the low
     ground route leaves 10 points less, the main follows the low ground as in
     the herringbone pattern (with its submains), and you're warned.
3. **Lateral directions.**
   - *Across the slope, contour-following*: along the contour, turned just
     enough downhill to hold the target grade, heading toward the nearest
     collector. Where the ground is flatter than the target grade the lateral
     runs straight downhill.
   - *Down the slope, contour-following*: the fall line.
   - *8-point*: the contour-following direction (across or down the slope),
     rounded to the nearest of the 8 compass directions: north, northeast,
     east, southeast and so on, on the map grid of the DEM's CRS. A lateral
     holds its direction until the terrain has turned 10 degrees past
     halfway to the next one, then turns 45 degrees, and no leg is shorter
     than half the spacing, so it does not zigzag where the contours run near
     a halfway direction. Beside a neighbour (parallelism 0.5 or more) it
     copies the neighbour's legs, turning on the bisector of each of its
     corners (a mitred offset), so the spacing holds through the turns; 8-point
     laterals stop at 0.9 of the spacing from another lateral rather than
     half, since they meet at 45 degrees. The mains and submains are those of
     contour-following laterals. On the test fields 84 to 100% of the lateral
     length runs exactly on a compass direction; the rest is mostly the short
     last piece joining a main or submain at an angle.
   - *Straight*: a constant bearing (dominant contour or fall direction);
     herringbone laterals leave the main at the herringbone angle.
   - *Straight, fixed bearing*: every lateral runs exactly north-south,
     east-west, northwest-southeast or northeast-southwest on the map grid,
     whatever the terrain and the orientation setting. With the parallel pattern the main runs at right
     angles to the laterals, both ways from the outlet, following the field
     edge where a straight line would leave the field and stopping at the
     corners. Laterals that slope uphill to their collector are reported in
     BURY_CHECK; drawing the main along the low side avoids them.
4. **Spacing.** Laterals are seeded along each collector and grown upslope,
   then gaps are filled by seeding new laterals exactly one spacing from
   existing ones (Jobard–Lefer evenly spaced streamlines). Laterals stop half a
   spacing from their neighbours, so spacing holds where laterals converge or
   diverge. With *parallelism* above 0, each new lateral is also steered to
   hold exactly one spacing from the neighbour it was seeded from, so curved
   laterals stay near parallel instead of fanning with the terrain. Laterals that run into a neighbour join it with a tee; laterals that
   end short of a collector get a short connector if a clear path exists.
   Nothing crosses.
5. **Burying (Routine J).** Every pipe is cut at its junctions and at grade
   breaks no more than the grade-break spacing apart. The segments are buried
   in BURY_ORDER, from the outlet upstream:
   - The outlet tile sits at the middle of the target band below the ground,
     plus the offset.
   - Each segment starts from the tile already set at its downstream junction
     and aims for the middle of the band at its upstream end. Its slope is held
     between the minimum and maximum slope (or at the constant slope).
   - The upstream tile is kept between the absolute upper and lower depths, so
     tile is never placed above the ground.
   - With *lower the outlet*, a segment that cannot keep the minimum slope
     without coming too shallow passes a deeper requirement down to its
     downstream junction and on to the outlet, which may go down to the lower
     tile depth; the network is then buried again until nothing changes.

   **Bury from the outlet invert** (any burying method) holds the outlet
   tile at the outlet invert elevation instead: enter the tile invert wanted
   at the outlet, for example the ditch's normal water level plus a
   clearance. Routine J then starts its outlet segment there (the offset and
   *lower the outlet* do not apply); Minimum cover takes its last segment
   down to it; Fewest grade breaks and Least cost start the main there.

   Between grade breaks the tile runs at a constant grade. The port reproduces
   Routine J exactly: run on the same segments, the original Illini code and
   this plugin give the same tile elevations and BURY_CHECK notes.

   **Minimum cover** (the other burying method) lays the tile as shallow as
   it can go, on the same segments. Each pipe starts at the minimum cover at
   its upstream end; each segment's downstream end is as high as it can be
   while the tile falls at least at the minimum grade and keeps the minimum
   cover at every point along the segment. A pipe that joins another is
   buried first, and the pipe it joins is kept no higher than it there
   (BURY_CHECK: "Lowered to a pipe joining here"); where the pipe it joins
   lies lower, its last segment is taken down to meet it ("Dropped to the
   pipe it joins"). The target band, maximum slope, offset, constant slope
   and lower-the-outlet settings do not apply. Tile is never shallower than
   the minimum cover or flatter than the minimum grade; on rolling ground it
   may go deeper than the maximum depth through knolls, which the Tile depth
   check layer and the log show. This replaces the separate Subsurface
   Drainage Layout plugin, which buried tile this way.

   **Fewest grade breaks** lays each pipe as a chain of straight runs with as
   few grade breaks as the limits allow, for laser-graded installation:
   - The tile stays between the absolute upper and lower depths (minimum
     cover, maximum depth) at every point, every run falls at a grade between
     the minimum and maximum slope, and no run is shorter than the *shortest
     run between grade breaks* (the run at the top of a pipe may be).
   - Of the profiles with the fewest breaks, the one closest to the middle of
     the target band (over the length of the pipe) is used. Breaks can fall
     at points about 10 ft apart, and break elevations are searched in steps
     of 0.02 m (about 0.07 ft); within those steps the number of breaks is the
     true minimum, not an estimate.
   - Pipes are buried from the outlet upstream, each starting at the tile of
     the pipe it joins. The outlet tile can be anywhere between the absolute
     depths, and not below the outlet invert when one is given (or at the
     outlet invert, with *bury from the outlet invert*). Where a pipe joins,
     the pipe it joins is kept deep enough for the joining pipe to be buried
     within the limits, as far as its own maximum depth allows (below).
   - Where no profile fits the limits, the maximum depth and then the minimum
     cover are eased a step at a time (BURY_CHECK: "No profile within the
     depth limits"), and failing that the minimum grade ("No profile within
     the depth and slope limits", with "Grade below minimum" or "Reverse or
     zero grade" on the runs concerned).
   - The grade-break spacing, offset, constant slope and lower-the-outlet
     settings do not apply.

   The tile can vary in depth more than with Routine J: anywhere between the
   absolute depths rather than near the target band. On the test fields it
   needed 1 to 52 grade breaks where Routine J used 200 to 500, and on the
   Site 3 field none, with the tile within the depth limits throughout.

   **Least cost** uses the same search and limits, but instead of taking the
   fewest breaks first, it minimizes one total: every grade break costs the
   *cost of a grade break* (default 100 ft: a break is worth 100 ft of tile
   1 ft off the target depth), and every foot of tile costs the square of its
   distance from the middle of the target band in feet (1 ft off costs 1,
   2 ft off costs 4). A high break cost gives the fewest breaks; a low one a
   profile that follows the target band closely, like Routine J's but with
   straight runs. The *excavation weight* (default 0) adds a cost for every
   foot of depth along the tile, which pulls it shallower: with 1, a foot more
   depth costs as much as being a foot off the target.

   **Largest change of grade at a break** (both methods; default 0, any
   change) limits how much the grade may change at a break, for example 0.3%,
   since plows and laser grade control follow gentle changes better. It
   usually needs a few more breaks, stepping the grade down gradually. Where a
   pipe cannot keep it, it is eased for that pipe (BURY_CHECK: "Change-of-slope
   limit eased"); a pipe that joins another is served first. The search takes
   several times longer with a limit set.

   The pipe a lateral or submain joins is kept low enough at the junction for
   that pipe to be buried within the limits (worked out exactly, with the same
   search run down the joining pipe). Where a main cannot meet every junction,
   it keeps as many as it can, from the outlet up, and eases only those in
   conflict; the pipes joining there are then noted in BURY_CHECK.
6. **Pipe sizing (Routines G and K).** Each line of the network is a sizing
   segment:
   - `TILE_ORDER` is its Strahler order: the highest inflowing order, plus one
     if two or more inflows share it.
   - `FLOW_LENGTH` is the length of all tile draining through it, itself
     included (Routine I).
   - The slope is the flattest InSlope (or OutSlope) among its Routine J
     segments, so the flattest stretch governs.
   - Every line uses the drainage coefficient you give (D_COEFF); flow = FLOW_LENGTH ×
     drain spacing (to the nearest foot) × coefficient; the inside diameter
     comes from Manning's equation for a full pipe, and the nominal size is
     the next listed size up (4–42 in).
   - Manning's n: single wall 0.015 up to 8 in, 0.017 over 8 in up to 12 in
     and 0.020 over 12 in (worked out together with the diameter); smooth
     wall 0.011; clay or concrete 0.013.

   This follows Routine K, with three corrections to the Illini 3.7 code:
   single-wall pipe there always used n = 0.015; spacing was truncated to whole
   feet rather than rounded; and Routine G's order rule differed from standard
   Strahler ordering for three inflows of different orders and for three equal
   inflows plus a higher fourth. Otherwise the original Routine K code and this
   plugin give the same coefficients, flows, areas and pipe sizes. Each line is
   sized on its own, then no pipe is left smaller than a pipe draining into it:
   a steep reach that would size smaller than the flatter pipe above it takes
   that pipe's size, and is flagged `sized up to N in to match the pipe above`
   (ACTUAL_SIZE still shows what its own flow and slope need).

## Output

One **Drainage network** line layer, loaded with styling. Mains, submains and
laterals are merged into a single connected network: every pipe is split at
each junction, so each link runs node to node in the direction of flow.

| Field | Meaning |
|---|---|
| `id` | Unique id of each line. |
| `pipe_id`, `pipe`, `kind` | Id and name of the pipe the line belongs to (e.g. `lateral 12`; all segments of one pipe share a `pipe_id`); main / submain / lateral. |
| `from_node`, `to_node` | Upstream and downstream node. Node 1 is the outlet. |
| `ds_link` | `id` of the line this one discharges into (0 at the outlet). |
| `length`, `ground_us/ds`, `invert_us/ds`, `max_depth`, `min_depth`, `depth_ds` | In design units; depths are ground to tile along the whole line. |
| `TileLength` | `length` rounded to the nearest 5 ft, or the nearest metre in metres mode. |
| `terrain_grade_pct`, `design_grade_pct` | Percent. |
| `upstream_length` | All pipe length draining through this link, including itself. |
| `drained_area` | Area served by the laterals upstream (lateral length × spacing), in hectares, or acres in feet mode. Use it with your drainage coefficient to size each reach. |
| `ElevDepth1`, `BuryDepth1`, `ElevDepth2`, `BuryDepth2` | Routine J fields: tile elevation and burial depth at the first vertex (1, upstream end) and last vertex (2, downstream end). |
| `InSlope`, `OutSlope` | Routine J fields: the line's tile slope as a fraction, and that slope held within the minimum and maximum. |
| `BURY_CHECK` | Routine J notes for the burying segments in this line, or `OK`: held at an absolute depth, deepened for low ground, grade below minimum, reverse grade and so on. Lines with grade problems are drawn purple. |
| `TILE_ORDER`, `FLOW_LENGTH`, `SIZE_SLOPE` | Sizing inputs: Strahler order (Routine G), tile length draining through the line in feet (Routine I), and the slope used. |
| `D_COEFF`, `D_AREA`, `CUM_SEG_FLOW`, `ACTUAL_SIZE`, `NOMINAL` | Routine K fields: drainage coefficient (inch/day), drained area (acres), flow (cfs), required inside diameter and nominal pipe size (inches). Always in Routine K's US units. The Tile Size Calculator plugin changes D_COEFF and resizes. |
| `DRAIN_SPACE` | The drain spacing Routine K used: the lateral spacing to the nearest foot. |
| `PIPE_TYPE` | The pipe material the line is sized for: Single Wall, Smooth Wall or Clay or Concrete. The Tile Size Calculator can change it section by section. |
| `flag` | Layout problems (below), with lengths in the design units. Flagged links are drawn orange. Depth problems are not repeated here: they are in `min_depth`/`max_depth`, the Tile elevation raster's Tile depth check layer and the log. |

The Drainage network lines are 3-D: each vertex carries the tile elevation
as its Z value and the ground elevation as its M value, in design units, and
every Routine J grade break is a vertex, so the tile runs straight between
vertices. The Tile Profile plugin draws profiles from them. The Surface
Layout layer stays flat (2-D).

Where to look for problems:

- **Depth** — tile shallower than the minimum cover or deeper than the
  maximum depth: the **Tile depth check** layer (red and purple cells) shows
  exactly where; `min_depth`/`max_depth` give each line's range; the log
  counts the pipes. Usually the ground between two grade breaks: shorten the
  grade-break spacing to follow the ground more closely. Tile Profile's
  **Check network** lists them too.
- **Grade** — reverse or zero grade, grade below the minimum, tile held at a
  depth limit: `BURY_CHECK`, drawn purple.
- **Layout** — `flag`, drawn orange:

- `invert … below outlet` — the main reaches the outlet lower than the
  outlet invert you gave. With *bury from the outlet invert* the outlet is
  held at it instead, and the pipes that then cannot keep the cover or grade
  are noted in BURY_CHECK.
- `connector …` — reaches a collector or another lateral through a straight
  connector.
- `unconnected: needs its own collector` — the low ground this lateral drains
  to has no collector. Add an outlet or submain there, or lower the submain
  threshold.
- `does not reach the outlet` — drains into an unconnected lateral.
- `turned to drain downhill; carried …` — the lateral followed the contours to
  a collector uphill of it (the far side of a submain, or along a rise), so
  the tile could not fall to it. It is turned round and its low end carried
  down the ground to a collector or a lateral that drains. Done where the
  ground rises more than 5 cm toward the collector and falls the other way.
- `carried downhill …` — the lateral's trace stopped short on flat or rough
  ground with no collector near, so it is carried on down the ground to a
  collector or to a lateral that reaches one. Check its route.
- `sized up to N in to match the pipe above` — its own flow and slope need a
  smaller pipe, but a pipe never gets smaller downstream.
- `needs more than 42 in pipe` — the flow is beyond the largest nominal size;
  NOMINAL shows 42, as in Routine K.

A second layer, **Surface Layout**, holds the same lines with only the `id`
field, as input for the Illini Drainage Tools. Lines run upstream to
downstream and connect end to end; save it as a shapefile if needed.

**Saved settings.** Each time the tool runs it saves its settings, and it
opens with them next time, also after QGIS is restarted. The DEM, field
boundary, outlet, main line and output destinations are not saved, since
they belong to one field. **Plugins → Illini Subsurface Drain Layout → Reset
saved settings to defaults** returns to the plugin defaults.

**Sharpest bend.** No tile line turns more sharply than this angle between its
legs: 120° (the default) means a change of direction of at most 60°.
Corners that are sharper, in laterals, their connectors and generated mains
and submains, are rounded with a short straight cut or a few gentle bends.
Junctions move with the corner, so the network stays joined. A short stub
that doubles back at the upper end of a lateral is trimmed. Lines you supply
as the main are left as drawn. The setting goes up to 135°; 0 turns it off.
Bends that cannot be rounded (very short legs) are counted in the log.

An optional raster, **Tile elevation**, maps the buried tile on the analysis
grid: every cell the tile passes through has the tile elevation, and all
other cells are empty. It has three bands, in design units:

| Band | Contents |
|---|---|
| 1 | Tile elevation |
| 2 | Tile depth (ground minus tile) |
| 3 | Depth check: 1 within the limits, 2 shallower than the minimum cover, 3 deeper than the maximum installation depth |

The tile is checked about every third of a cell, so the check finds a
limit broken between grade breaks as well as at them (by more than 1 cm). A
cell that breaks a limit keeps the elevation and depth of its worst point.
It loads as two layers: **Tile elevation**, coloured from low to high, and
**Tile depth check** above it, which shows only the cells outside the
limits, red where the tile is too shallow and purple where it is too deep.
The log gives the number of cells of each. It is made by default as a
temporary layer; choose **Skip output** under **Tile elevation** to leave it
out, or **Save to file** to keep it.

The log and the optional HTML report give the pipe length by nominal size.
The report also summarises the field area, totals, pipe per acre (or per hectare), coverage and warnings, in the report units. Coverage is the share of the drainable area (inside the setback)
within half a spacing of a pipe.

## Import a LANDRAIN network

**Processing ▸ Toolbox ▸ Illini subsurface drain layout ▸ Import LANDRAIN network**
reads a network saved by LANDRAIN (the DOS drainage design program of the
LANDIMPROVE package; a `.NET` file) and writes it as an Illini network layer,
with the same fields, so a LANDRAIN design can be checked in Tile Profile and
sized and priced in the Tile Size Calculator.

- **LANDRAIN network file**: the `.NET` file.
- **Coordinate system**, **Origin easting / northing**: LANDRAIN works in feet
  from its own origin; give the map position of that origin in a projected
  coordinate system, for example the zero point the field survey was tied to
  (State Plane Illinois East, EPSG:3435, by default). Advanced: **Local
  origin** (the LANDRAIN x, y at that point, if it was not 0, 0) and
  **Rotation** of the survey grid, degrees counterclockwise.
- **Pipe sizes**: the inches of LANDRAIN's size classes 1, 2, 3 …
  (default 3, 4, 5, 6, 8, 10, 12, 15, 18, 24, 36).
- **Outlet** (optional): the pipe end nearest the point is the outlet;
  by default the lowest pipe end.
- **Depth for undesigned systems**: a system LANDRAIN had not yet designed
  has its tile at the ground; the tile is put this far below the ground
  (default the file's lower depth limit) and sized here.
- **Pipe type** and **Design units** of the output fields.

The pipes are traced from the outlet into a main, submains and laterals (each
pipe carries on up its longest branch) and cut at junctions and size changes.
The tile (Z) and ground (M) elevations of every point, and the pipe sizes, come
from the file; the drainage coefficient and drain spacing are the file's, and
TILE_ORDER, FLOW_LENGTH, ACTUAL_SIZE and the other sizing fields are worked out
as in the layout tool. BURY_CHECK reads "Imported from LANDRAIN", with any
reverse grades; LR_FLAG keeps LANDRAIN's link flag. Parts of the file not joined
to the main system are imported as systems of their own. The log gives the
counts, the file's design settings and any such notes.

LANDRAIN has 3 in pipe; the Tile Size Calculator sizes from 4 in up, so any
line it resizes becomes at least 4 in.

## Choosing settings

- **Uniform slope:** *Parallel*, laterals *across the slope*, *contour-following*
  gives laterals at the target grade on both sides of a main that climbs the
  slope. *Down the slope* gives a gridiron with a header along the low side.
- **Swales and draws:** *Herringbone* with *down the slope* (flow-line laterals
  into the thalweg main) or *straight* at 45–60°.
- **Flat ground (< 0.2%):** grades come from depth; watch BURY_CHECK, try
  *constant slope* and *lower the outlet*, and check the outlet invert.
- Large orange areas mean the outlet, pattern or orientation doesn't suit that
  part of the field. Try the other orientation, or run the tool separately
  for that zone with its own outlet.

## Limitations

- This is a planning tool. Check every layout against field conditions,
  drainage coefficients and local standards before installation.
- Closed depressions are routed through their spill points; a pit with no
  submain shows up as unconnected laterals.
- One outlet per run. For several outlets, split the field and run once per
  outlet.

## Credits and licence

The tile burying scheme and the pipe sizing are ported from Routine J (Tile
Burying System), Routine K (Network Pipe Sizing) and the tile ordering of
Routine G of the Illini Drainage Tools by Anamelechi Falasy and Richard Cooke, University of
Illinois Urbana-Champaign, released under the GNU General Public License v3
(see `LICENSE_ILLINI_DRAINAGE_TOOLS.txt`). This plugin is distributed under the
same licence, GPL v3 or later.

## Development

The layout engine (`core/`) is pure numpy and has no QGIS imports.
`test/run_synthetic.py` runs it on synthetic terrain (tilted plane, swale,
rolling ground) and writes plots to `./out` (needs matplotlib):

```
python test/run_synthetic.py all
```
