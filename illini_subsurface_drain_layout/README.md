# Illini Subsurface Drain Layout (QGIS plugin)

Automated layout and burying of a subsurface (tile) drainage network from a
DEM, a field boundary, an outlet location and the lateral spacing. Laterals run
near parallel or near perpendicular to the field contours, in a
parallel/gridiron or herringbone pattern, as straight lines or bending with the
terrain. The tile is buried with the scheme of Routine J (Tile Burying System)
of the [Illini Drainage Tools](https://github.com/FVW-Services/Illini-Drainage-Tools),
and the output carries the Routine J fields so it can go on to the later
Illini routines.

Requires QGIS 3.34 LTR or newer. Uses only QGIS, GDAL and numpy.

## Install

1. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**.
2. Pick `illini_subsurface_drain_layout.zip` and install. (Enable *Show also
   experimental plugins* under Settings if QGIS hides it.)
3. Run it from the toolbar button, **Plugins ▸ Illini Subsurface Drain Layout**,
   or the Processing Toolbox (*Illini subsurface drain layout*). As
   a Processing algorithm it also works in the graphical modeler, in batch
   mode and from Python:

```python
processing.run("illinidrainlayout:illinisubsurfacedrainlayout", {
    "DEM": "dem.tif", "FIELD": "field.gpkg", "OUTLET": "612345.0,4441234.0 [EPSG:26916]",
    "SPACING": 80, "PATTERN": 1, "ORIENTATION": 1, "SHAPE": 1,
    "NETWORK": "TEMPORARY_OUTPUT", "LINE_IDS": "TEMPORARY_OUTPUT"})
```

## Inputs

| Parameter | Notes |
|---|---|
| Elevation (DEM) | Any GDAL raster in a **projected** CRS (metres or feet). LiDAR DEMs at 0.5–3 m work well. |
| Field boundary | Polygon layer. All features are merged; holes (waterways, buildings) are excluded. |
| Outlet location | Click on the map. It can sit just outside the field; the main is extended to it. |
| Design units | Feet (default) or metres, for spacing, cover, depths and all outputs. |
| Lateral spacing | In design units. Default 80 ft. |
| Layout pattern | *Parallel / gridiron* or *Herringbone* (see below). |
| Lateral orientation | *Across the slope* (near parallel to contours) or *Down the slope* (near perpendicular). |
| Lateral shape | *Straight* (default) or *Contour-following*. |
| Parallelism | Contour-following laterals only, 0–1 (default 0.9). Steers each lateral to run parallel to its neighbour at exactly one spacing; 0 follows the terrain freely, 1 makes every lateral an offset of its neighbour. |
| Burying: upper and lower tile depth | The target depth band (Routine J). Tile aims for the middle of it. Defaults 3.25 ft and 4.25 ft. |
| Burying: absolute upper and lower tile depth | Tile is never shallower or deeper than these (minimum cover, maximum depth). Defaults 2.5 ft and 6 ft. |
| Burying: maximum slope; minimum slope for laterals and for mains | Percent. Defaults 5%, 0.1% and 0.1%. |
| Burying: offset depth | Added depth at the outlet (+ lowers it). Default 0. |
| Burying: constant slope | For flat terrain only: every segment upstream of the outlet segment is laid at this slope (default 0.5%). Off by default. |
| Burying: lower the outlet for low ground | Deepens the outlet, down to the lower tile depth, when low ground upstream cannot keep the minimum slope. Off by default. |
| Outlet invert elevation | Optional, in DEM elevation units. Flags the main if it arrives below the outlet. |

Advanced: DEM elevation units (m / ft / US survey ft; default US survey ft), target grade for
contour-following laterals (default 0.2%), herringbone angle (default 60°),
boundary setback (default half the spacing), terrain smoothing radius (default
the spacing), analysis cell size (automatic), where the main ends and how large
a branch must be to get a submain, and the longest burying segment between
grade breaks (default 50 ft).

## How the layout is built

1. **Terrain.** The DEM is resampled to the analysis grid and smoothed, so
   laterals follow the field's shape rather than LiDAR micro-relief. The fall
   line and contour direction are taken from the smoothed surface.
2. **Collectors.**
   - *Herringbone*: a priority-flood from the outlet builds the drainage tree
     of the field. The main follows its largest branch (the thalweg); branches
     draining more than the submain threshold become submains. Mains end where
     their drainage area drops below the stop threshold.
   - *Parallel / gridiron*: one main runs from the outlet perpendicular to the
     laterals — up the slope when laterals run across it, or as a header
     across the low side (both directions from the outlet, rising at the
     minimum main grade and sliding along the field edge) when laterals run
     down it.
3. **Lateral directions.**
   - *Across the slope, contour-following*: along the contour, turned just
     enough downhill to hold the target grade, heading toward the nearest
     collector. Where the ground is flatter than the target grade the lateral
     runs straight downhill.
   - *Down the slope, contour-following*: the fall line.
   - *Straight*: a constant bearing (dominant contour or fall direction);
     herringbone laterals leave the main at the herringbone angle.
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

   Between grade breaks the tile runs at a constant grade. The port reproduces
   Routine J exactly: run on the same segments, the original Illini code and
   this plugin give the same tile elevations and BURY_CHECK notes.

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
| `terrain_grade_pct`, `design_grade_pct` | Percent. |
| `upstream_length` | All pipe length draining through this link, including itself. |
| `drained_area` | Area served by the laterals upstream (lateral length × spacing), in hectares, or acres in feet mode. Use it with your drainage coefficient to size each reach. |
| `ElevDepth1`, `BuryDepth1`, `ElevDepth2`, `BuryDepth2` | Routine J fields: tile elevation and burial depth at the first vertex (1, upstream end) and last vertex (2, downstream end). |
| `InSlope`, `OutSlope` | Routine J fields: the line's tile slope as a fraction, and that slope held within the minimum and maximum. |
| `BURY_CHECK` | Routine J notes for the burying segments in this line, or `OK`: held at an absolute depth, deepened for low ground, grade below minimum, reverse grade and so on. Lines with grade problems are drawn purple. |
| `flag` | Anything else to check (below). Flagged links are drawn orange. |

Flags:

- `deeper than …` / `cover under … between grade breaks` — the ground between
  two grade breaks takes the tile outside the absolute depths. Shorten the
  grade-break spacing to follow the ground more closely.
- `connector … m` — reaches a collector or another lateral through a straight
  connector.
- `unconnected: needs its own collector` — the low ground this lateral drains
  to has no collector. Add an outlet or submain there, or lower the submain
  threshold.
- `does not reach the outlet` — drains into an unconnected lateral.

A second layer, **Line IDs**, holds the same lines with only the `id`
field, as input for the Illini Drainage Tools. Lines run upstream to
downstream and connect end to end; save it as a shapefile if needed.

An optional HTML report summarises totals, pipe per hectare, coverage and
warnings. Coverage is the share of the drainable area (inside the setback)
within half a spacing of a pipe.

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
  drainage coefficients and local standards before installation. It does
  not size pipes, but `drained_area` gives what sizing needs.
- Closed depressions are routed through their spill points; a pit with no
  submain shows up as unconnected laterals.
- One outlet per run. For several outlets, split the field and run once per
  outlet.

## Credits and licence

The tile burying scheme is ported from Routine J (Tile Burying System) of the
Illini Drainage Tools by Anamelechi Falasy and Richard Cooke, University of
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
