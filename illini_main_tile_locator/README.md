# Illini Main Tile Locator (QGIS plugin)

Locates a subsurface (tile) drainage **main** from an **outlet location**, a
**DEM** and the **field boundary**, for laterals to be installed either
**parallel** or **perpendicular** to the contour lines.

Requires QGIS 3.34 LTR or newer. Uses only QGIS, GDAL and numpy.

## Install

1. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**.
2. Pick `illini_main_tile_locator.zip` and install. (Enable *Show also experimental
   plugins* under Settings if QGIS hides it.)
3. Run it from the toolbar button, **Plugins ▸ Illini Main Tile Locator**, or the
   Processing Toolbox (*Illini main tile locator*). The toolbar button fills in the
   DEM and field from the active layer (or the first raster and polygon layer
   in the project); click the **…** button next to *Outlet location* to pick the
   outlet on the map.

From Python:

```python
processing.run("illinimaintilelocator:illinimaintilelocator", {
    "OUTLET": "612345.0,4441234.0 [EPSG:26916]", "DEM": "dem.tif", "FIELD": "field.gpkg",
    "ORIENTATION": 0,            # 0 parallel to contours, 1 perpendicular
    "SPACING": 80, "CANDIDATES": "TEMPORARY_OUTPUT"})
```

The plugin is also listed under **Raster → Tile Drainage**, a menu shared by the drainage tools in this repository, as well as under **Plugins → Illini Main Tile Locator**.

## Inputs

| Parameter | Notes |
|---|---|
| Outlet location | Click on the map. It can sit just outside the field (e.g. at a ditch); the main is extended to it. |
| Elevation (DEM) | Any GDAL raster in a **projected** CRS. LiDAR DEMs at 0.5–3 m work well. |
| Field boundary | Polygon layer. All features are merged; holes (waterways, buildings) are excluded. |
| Laterals to be installed | *Parallel to the contour lines* (across the slope, default) or *Perpendicular to the contour lines* (down the slope). |
| Lateral shape | *Bend with the terrain* (follow the contours, or the fall line; default) or *Straight* (on the field's dominant contour or fall bearing). |
| Shape of the chosen main, if it is curved | *As located* (default), *Smoothed* or *Straight sections of a minimum length*. Applies only when the chosen main follows the low ground, the fall line or the contours; straight mains are left alone. |
| Smoothing distance | Default 200 ft. Width of the Gaussian used to smooth the main along its length; larger is smoother. |
| Minimum section length | Default 300 ft. Each straight section is at least this long; the sections chosen are those that follow the curve most closely. |
| Add submains to cover the field | Off by default. When on, submains are added to the chosen main until no further submain is worth adding (see below). |
| Main alignment | *Best of all candidates* (default), *Bend with the terrain only*, or *Straight lines only*. |
| Design units | Feet (default) or metres, for every length, depth and output. |
| Lateral spacing | Default 80 ft. Sets the boundary setback, the terrain smoothing and the scoring. |
| Maximum lateral length | Laterals that would run farther than this to reach the main count as not served. Default: *Capacity of the lateral tile (maxLength routine)*. Alternatives: *Fixed length* (default 1320 ft) or *No limit*. |
| Lateral capacity: drainage intensity, inside diameter, slope, tile material, Manning's n | Inputs to the maxLength routine (Table 6). Defaults: 3/8 in/day, 4 in, 0.1%, corrugated plastic tubing (n = 0.013; clay or concrete tile 0.012). An entered Manning's n overrides the material. With the default 80 ft spacing this gives about 2,080 ft. |
| Burying: target band, absolute depths, slopes, offset, lower the outlet | Routine J settings for burying the main. Defaults 3.25–4.25 ft band, 2.5 ft and 6 ft absolute, 0.1% minimum and 5% maximum slope. |
| Minimum slope of the laterals | Default 0.1%. A lateral must be able to reach the main at this grade, using the ground fall plus the depth range (absolute upper to absolute lower tile depth). |
| Outlet invert elevation | Optional, in DEM elevation units. Flags the main if it arrives below the outlet. |

Advanced: DEM elevation units (default US survey feet), target grade of
contour-parallel laterals (0.2%), longest burying segment between grade breaks
(50 ft), bearing step of straight mains (5°), where the low-ground main ends
(2% of the field), the scoring cost of main length, boundary setback, smoothing
radius and analysis cell size, and the most submains to add (20).

## How the main is located

1. **Terrain.** The DEM is resampled to the analysis grid and smoothed at the
   lateral spacing, so the main follows the shape of the field rather than
   LiDAR micro-relief. Fall line and contour directions come from the smoothed
   surface.
2. **Lateral paths.** From every part of the drainable area (inside the
   setback) the tool traces the path a lateral would take:
   - *Parallel to the contours*: along the contour, either way, turned just
     enough downhill to hold the target lateral grade (straight down where the
     ground is flatter than that grade); or, if straight, along the dominant
     contour bearing.
   - *Perpendicular to the contours*: down the fall line; or, if straight, on
     the dominant downhill bearing.

   Along each path it tracks how quickly neighbouring laterals converge. Where
   they would close to a third of the spacing (in a draw or swale) the laterals
   would bunch up, so ground beyond that point needs a submain.
3. **Candidate mains**, all starting at the outlet:
   - the **low-ground (thalweg)** line, the largest branch of a priority-flood
     drainage tree from the outlet, ending at two drainage-area thresholds;
   - the **fall line** uphill from the outlet;
   - **headers** along the contours, rising at the minimum main grade, one way
     or both ways, with and without following the field edge round corners
     (an L-shaped header);
   - **straight mains** on every bearing (5° steps), one-armed and as a line
     through the outlet;
   - **dog-legs** that run along the low side for 20–80% of its length, then
     turn up the slope (following the fall line or straight).
4. **Scoring.** For each candidate, a lateral path is *served* if it meets the
   main within the maximum lateral length, before converging, and with enough
   fall. The score is the served area less a cost for the main's length (by
   default half a spacing-wide strip per unit length) and a larger cost for any
   length that cannot be buried within the depth and slope limits.
5. **Trimming.** The upper end of each arm of the best main is shortened while
   that raises the score.
6. **Shaping** (optional). A curved chosen main can be smoothed (Gaussian
   along its length, end points and their directions kept) or replaced by the
   straight sections that follow it most closely with none shorter than the
   minimum section length (dynamic programming over breakpoints spaced a
   quarter of that length apart). The reshaped main is scored and buried
   again, and the log reports any change in the area its laterals reach.
7. **Submains** (optional). The tool finds the largest patches (up to five at a
   time) whose laterals still cannot drain to the network: they converge in a
   draw, fall short of the maximum lateral length, or would run uphill. For
   each patch it tries submains from several points in it: down the low ground
   (priority-flood route) to the network, straight to the nearest point of the
   network, and headers along the contours from where the low-ground route
   meets the network. Each try is scored on the whole network (main plus
   submains) the same way as the main; the best is trimmed at its upper end
   and added if it drains at least one spacing-square more area than it costs.
   This repeats until no submain is worth adding or the limit is reached.
8. **Burying.** The chosen main is buried with the scheme of Routine J (Tile
   Burying System) of the Illini Drainage Tools, from the outlet upstream,
   exactly as in the Illini Subsurface Drain Layout plugin; the four runners-up
   are buried the same way. With submains, the main is buried together with
   them, so its depths and grade in the candidate layer are those of the network.

## Maximum lateral length from tile capacity

By default the longest lateral is the length a lateral tile can drain flowing
full, from the maxLength routine (Maximum Lengths of Corrugated Plastic Tubing
and Clay and Concrete Drain Tile, Table 6). Manning's equation for a full
circular pipe is solved for the length of a strip one lateral spacing wide
that it can serve at the drainage intensity:

    L = 10000 / (DI · S · n) · (D · s^0.1875 / 51.7)^(1/0.375)

with DI the drainage intensity (mm/day), S the lateral spacing (m), n Manning's
roughness coefficient, D the inside diameter (mm) and s the slope (m/m), giving
L in metres. The plugin takes the lateral spacing from the *Lateral spacing*
input, so a wider spacing gives a shorter maximum length. The log reports the
length used.

## Outputs

**Candidate mains**: the chosen main and the four best distinct runners-up
(candidates lying within half a spacing of a better one are dropped). Each is
one multi-line feature (a two-way header has two arms), digitised from the
upper end to the outlet. The chosen main is drawn red, the runners-up blue and
dashed, and every candidate is labelled with its rank.

| Field | Meaning |
|---|---|
| `rank`, `chosen` | 1 is the chosen main. |
| `name`, `method`, `arms` | How the candidate was built, and its number of arms. |
| `served_pct` | Share of the drainable area whose laterals reach it. |
| `length`, `problem_length` | Main length, and length outside the depth or slope limits (design units). |
| `design_grade_pct`, `max_depth`, `min_depth`, `outlet_depth` | From burying it with Routine J. |
| `BURY_CHECK` | Routine J notes, or `OK`. |
| `score` | Served hectares after the length and burying costs. |

The log summarises the result and lists warnings.

**Submains** (only when *Add submains to cover the field* is on): one line per
submain, digitised from its upper end to where it joins, drawn orange and
labelled with its name.

| Field | Meaning |
|---|---|
| `name`, `joins` | e.g. `submain 2` joins `main` or `submain 1`. |
| `method` | Along the low ground, straight, or header along the contours. |
| `length`, `ground_us/ds`, `invert_us/ds`, `max_depth`, `min_depth` | Design units. |
| `design_grade_pct` | Percent. |
| `gain_pct` | Share of the drainable area it added when it was placed. |
| `BURY_CHECK`, `flag` | Routine J notes; depth problems (drawn purple). |

## Going on to a full layout

Select the chosen main (or any runner-up) in the **Candidate mains** layer and
use it as the *Main line(s)* input of the **Illini Subsurface Drain Layout**
plugin with *Selected features only* ticked, and the same outlet. The arm
ending nearest the outlet becomes the main and any second arm a submain. To
include the submains, merge them with the chosen main into one line layer
first (Vector ▸ Data Management Tools ▸ Merge Vector Layers).

## Tips

- On a **uniform slope** with laterals parallel to the contours, expect a main
  straight up the slope (or a dog-leg when the outlet is near a corner and the
  maximum lateral length matters). With laterals perpendicular to the
  contours, expect a header across the low side, often turning up a low edge.
- In **draws and swales**, laterals perpendicular to the contours drain to a
  main in the low ground; contour-parallel laterals there run nearly parallel
  to the draw, so a single main may serve little; tick *Add submains to cover
  the field* to fill the gaps.
- A low `served_pct` means the outlet or the orientation doesn't suit part of
  the field. Try the other orientation, a larger maximum lateral length, or a
  second outlet for that zone.

## Limitations

- This is a planning tool. Check the main against field conditions and local
  standards before installation.
- One outlet per run. Submains are added greedily, one at a time, so the set is
  good but not guaranteed optimal; ground below the outlet cannot be covered.
- The lateral check uses net fall over the whole lateral; humps along a
  lateral are caught only when they exceed the available depth range.

## Credits and licence

Terrain handling, collector tracing and the Routine J burying scheme come from
the Illini Subsurface Drain Layout plugin, and through it from the Illini
Drainage Tools by Anamelechi Falasy and Richard Cooke, University of Illinois
Urbana-Champaign, released under the GNU General Public License v3 (see
`LICENSE_ILLINI_DRAINAGE_TOOLS.txt`). This plugin is distributed under the same
licence, GPL v3 or later.

## Development

The engine (`core/`) is pure numpy with no QGIS imports.
`test/run_synthetic.py` runs it on synthetic terrain (tilted plane, swale,
rolling ground) and writes plots to `./out` (needs matplotlib):

```
python test/run_synthetic.py all
```
