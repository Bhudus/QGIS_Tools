# Subsurface Drainage Layout (QGIS plugin)

Automated layout of a subsurface (tile) drainage network from a DEM, a field
boundary, an outlet location and the lateral spacing. Laterals run near
parallel or near perpendicular to the field contours, in a parallel/gridiron
or herringbone pattern, as straight lines or bending with the terrain.

Requires QGIS 3.34 LTR or newer. Uses only QGIS, GDAL and numpy.

## Install

1. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**.
2. Pick `subsurface_drain_layout.zip` and install. (Enable *Show also
   experimental plugins* under Settings if QGIS hides it.)
3. Run it from the toolbar button, **Plugins ▸ Subsurface Drainage**, or the
   Processing Toolbox (*Subsurface drainage ▸ Subsurface drainage layout*). As
   a Processing algorithm it also works in the graphical modeler, in batch
   mode and from Python:

```python
processing.run("drainlayout:subsurfacedrainlayout", {
    "DEM": "dem.tif", "FIELD": "field.gpkg", "OUTLET": "612345.0,4441234.0 [EPSG:26916]",
    "MAIN": "my_main.gpkg",   # optional
    "SPACING": 80, "PATTERN": 1, "ORIENTATION": 1, "SHAPE": 1,
    "NETWORK": "TEMPORARY_OUTPUT", "SURFACE_LAYOUT": "TEMPORARY_OUTPUT"})
```

## Inputs

| Parameter | Notes |
|---|---|
| Elevation (DEM) | Any GDAL raster in a **projected** CRS (metres or feet). LiDAR DEMs at 0.5–3 m work well. |
| Field boundary | Polygon layer. All features are merged; holes (waterways, buildings) are excluded. |
| Outlet location | Click on the map. It can sit just outside the field; the main is extended to it. Optional when a main line is given. |
| Main line(s) | Optional line layer with your own main, and any submains. Leave empty to route the main automatically (see below). |
| Design units | Feet (default) or metres, for spacing, cover, depths and all outputs. |
| Lateral spacing | In design units. Default 80 ft. |
| Layout pattern | *Parallel / gridiron* or *Herringbone* (see below). |
| Lateral orientation | *Down the slope* (near perpendicular to contours; default) or *Across the slope* (near parallel). |
| Lateral shape | *Straight* (default) or *Contour-following*. |
| Parallelism | Contour-following laterals only, 0–1 (default 0.9). Steers each lateral to run parallel to its neighbour at exactly one spacing; 0 follows the terrain freely, 1 makes every lateral an offset of its neighbour. |
| Minimum cover, maximum depth | Ground to pipe invert. Defaults 2.5 ft and 6 ft. |
| Minimum lateral and main grade | Percent. |
| Outlet invert elevation | Optional, in DEM elevation units. Flags the main if it arrives below the outlet. |

Advanced: DEM elevation units (m / ft / US survey ft; default US survey ft), target grade for
contour-following laterals (default 0.2%), herringbone angle (default 60°),
boundary setback (default half the spacing), terrain smoothing radius (default
the spacing), analysis cell size (automatic), where the main ends and how large
a branch must be to get a submain.

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
5. **Inverts.** Each pipe is designed from its upstream end: the invert sits
   at minimum cover, or lower where the minimum grade requires it, and every
   junction is kept below the incoming pipe's invert. Depth over the maximum
   is flagged.

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
| `length`, `ground_us/ds`, `invert_us/ds`, `max_depth`, `depth_ds` | In design units. |
| `terrain_grade_pct`, `design_grade_pct` | Percent. |
| `upstream_length` | All pipe length draining through this link, including itself. |
| `drained_area` | Area served by the laterals upstream (lateral length × spacing), in hectares, or acres in feet mode. Use it with your drainage coefficient to size each reach. |
| `flag` | Anything to check (below). Flagged links are drawn orange. |

Flags:

- `deeper than …` — exceeds the maximum depth.
- `connector … m` — reaches a collector or another lateral through a straight
  connector.
- `unconnected: needs its own collector` — the low ground this lateral drains
  to has no collector. Add an outlet or submain there, or lower the submain
  threshold.
- `does not reach the outlet` — drains into an unconnected lateral.

A second layer, **Surface Layout**, holds the same lines with only the `id`
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
- **Flat ground (< 0.2%):** grades come from depth; watch the depth flags and
  the outlet invert check.
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

## Development

The layout engine (`core/`) is pure numpy and has no QGIS imports.
`test/run_synthetic.py` runs it on synthetic terrain (tilted plane, swale,
rolling ground) and writes plots to `./out` (needs matplotlib):

```
python test/run_synthetic.py all
```
