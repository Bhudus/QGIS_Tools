# As-Built Check

QGIS plugin (Processing tool) that compares the tile **as installed**, from the RTK log of the
plow or trencher, with the **design** network, and writes the **as-built network** so Tile
Profile, the Tile Size Calculator, Yield Compare, Design Reports and the other tools can work
on what is in the ground. It can also **check the as-built on its own**, with no design, for
bellies, reverse grade, grade below the minimum, depth and gaps in the log.

Menu: *Vector ▸ Tile Drainage ▸ As-built check…*, or the Processing Toolbox (*As-built
check*).

## Inputs

| Input | |
|---|---|
| Check | **Compare the as-built with the design network** (default), or **check the as-built on its own** (no design). |
| Design drainage network | A Drainage Network from Illini Subsurface Drain Layout, Illini Subirrigation Layout or Illini Drainage System Design (tile elevation in Z, ground in M; links run upstream to downstream). |
| As-built | The installation log: points from the grade-control system, or logged runs as lines (their vertices are used). Checking on its own, it can also be a network with Z, such as the As-built network this tool writes. |
| Run field, order field | Checking logged points on their own: the field naming each run (line, pass) and, optionally, a time or sequence field to put its points in order; without it they are put in order along the run (from one end to the nearest point each time, which follows curved runs). |
| Elevation field | Blank to use the Z of the geometry, or a field holding the elevation. |
| The logged elevation is | The tile invert, or a reference above it, such as the GPS antenna on the plow; then the offset (ft) from that reference down to the tile invert is subtracted. |
| Elevation units | Feet or metres, for the log, the network and the DEM. |
| DEM | Optional: the ground for the depth check; otherwise the design ground (M). |
| Tolerances | Tile elevation ±0.1 ft; water held in a belly 0.1 ft; minimum grade 0.1%; minimum and maximum depth 2.5 and 6 ft. |
| Move the lines | Optional: the logged positions replace each line's inner vertices (the ends stay, so the network stays joined). |
| Advanced | Matching distance (10 ft), grade window (100 ft), smoothing length (20 ft), longest unlogged stretch (30 ft), grade tolerance beyond the log's own scatter (0.01%). |

## The checks

Each logged point is matched to the nearest design line within the matching distance and
compared with the design at the same station (distance along the line). RTK logs are noisy
(a few hundredths of a foot), so:

* **Off grade**: the logged tile less the design tile, smoothed over the smoothing length,
  beyond the tolerance. The raw deviation is kept too.
* **Belly**: water held in the tile because the tile further down the pipe is higher. At
  each point, the highest (smoothed) tile downstream in the same pipe less the tile there.
  This is what a sag or a hump does to the drain, whatever its shape.
* **Grade**: a least-squares line through the logged points over the grade window, against
  the minimum grade. A window is short of it only by more than twice the standard error of its
  fit (the log's own scatter) and the grade tolerance (0.01%); a line is flagged when a
  quarter of its windows (and at least two) are short (a fifth, for reverse grade), or when
  its overall least-squares grade is.
* **Depth**: below the ground (DEM, or the design ground), outside the minimum and maximum
  depth by more than half the elevation tolerance.
* **Not logged**: stretches longer than the longest unlogged stretch with no logged point.
* **Capacity**: with the Routine K fields, the design flow (`CUM_SEG_FLOW`) against the
  full-flow capacity of the pipe (`NOMINAL`) at the as-built grade.

**Installation or design?** Every check is also made on the design tile at the same places.
A line is flagged for the installation (status **Check**, `ab_flag`) only where the
installation is worse than the design. Issues already in the design (bellies, reverse grade,
depths outside the limits, capacity) are listed apart (status **Design issue**,
`ab_design_flag`), so a contractor who laid the design exactly is not blamed for it.

## Checking the as-built on its own

With no design to compare with, each run (or line, or pipe of a network) is checked against
the limits alone: water held in bellies, reverse grade, grade below the minimum, depth (from
a DEM, or the ground in M where it lies above the tile) and, for logged points, gaps in the
log. Logged runs flow downhill: the lower end of each is its outlet, whichever way it was
logged. A network with `id`, `ds_link` and `pipe` fields (an Illini or As-built network) is
chained into pipes and flows in the direction of its lines, so a belly is found across the
joins between its lines. Grades of lines are fitted to their own vertices only.

The outputs are the same three layers: the points with their water held, grade, depth and
flags; a check of each run or line (OK or Check, with its grade, largest belly, share of
windows below the minimum grade or reversed, depth range, gaps and flags); and the runs or
lines with the (smoothed) tile in Z and the ground in M, which turns a log of points into
lines Tile Profile can draw.

Here a belly or reverse stretch is reported whether it was built that way or designed that
way: to tell the two apart, compare with the design.

## Outputs

* **As-built points**: each logged point with its design line, station, distance from the
  line, logged and design tile, deviation (smoothed and raw), depth, water held, window grade,
  status (OK / Check / Unmatched) and flags.
* **As-built check** (lines): each design line with its status (OK, Check, Design issue, Not
  logged), share logged, deviations (mean, largest, RMS, share within tolerance), water held
  as installed and as designed, share of windows below the minimum grade, depth range,
  as-built and design grade, largest distance from the design line, capacity ratio, and the
  installation and design flags.
* **As-built network**: the design network with the logged tile (smoothed) at each vertex in
  Z and the ground in M; `invert_us`, `invert_ds`, `ElevDepth1/2`, `BuryDepth1/2`,
  `depth_ds`, `min_depth`, `max_depth` and `design_grade_pct` brought up to date; and the
  `ab_` fields of the check. Stretches with no logged point keep the design tile.

Lengths, depths and tolerances are in feet.

## Authors

Richard Cooke and Anamelechi Falasy.
