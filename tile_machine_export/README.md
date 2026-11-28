# Tile Machine Export (QGIS plugin)

Exports a buried tile drainage network as installation runs for tile plow and
trencher grade control, GNSS staking and CAD. It covers the *Installation* step
of the drainage design process, from the design to the machine.

Requires QGIS 3.34 LTR or newer. Uses only QGIS and numpy.

## Install

1. Download [`tile_machine_export.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/tile_machine_export/tile_machine_export.zip).
2. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**, then pick
   the zip. (Enable *Show also experimental plugins* under Settings if QGIS hides it.)
3. Run it from the toolbar button, **Vector → Tile Drainage**, **Raster → Tile
   Drainage**, **Plugins ▸ Tile Machine Export**, or the Processing Toolbox. From
   Python:

```python
processing.run("tilemachineexport:tilemachineexport", {
    "NETWORK": "drainage_network.gpkg", "DEM": "dem.tif", "FIELD_NAME": "North 80",
    "UNITS": 1, "OUT_UNITS": 0, "BENCHMARK": "612345.0,4441234.0 [EPSG:26916]",
    "BENCHMARK_ELEV": 718.42, "OUTPUT_FOLDER": "C:/tile/North80",
    "RUNS": "TEMPORARY_OUTPUT"})
```

## What goes in

The plugin needs a **buried network**: lines with tile elevations.

**Tile elevations**, in order of preference:
1. each vertex's Z value (Illini Subsurface Drain Layout 0.13+, Tile Profile);
2. the `ElevDepth1`/`ElevDepth2` fields (Illini Drainage System Design);
3. the `invert_us`/`invert_ds` fields, straight between the two ends.

Lines with no tile elevations are left out, with a warning.

**Ground**, for depths:
1. the DEM, if given, sampled along every pipe;
2. else each vertex's M value (Illini Subsurface Drain Layout);
3. else `ground_us`/`ground_ds`.

**The network itself** need not be split at junctions or drawn in the flow
direction. Tees, crossings and nearby ends are joined, and the network is
rooted at the outlet: the point you give, else the lowest free end.

## Runs

A run is one pipe as the machine installs it:
- the main, from the outlet up;
- each submain and lateral, from its junction up.

Runs follow `pipe_id` where the network has it (Illini Subsurface Drain
Layout). Otherwise a pipe carries on through a junction into the upstream pipe
of the same kind. Failing that, it carries on into a pipe going straight ahead
(within 30°).

Runs are numbered by type, then by distance from the outlet: mains `M1…`,
submains `S1…`, laterals `L1…`. Station 0 is each run's downstream end; that
is where the outlet or the junction with its parent run sits.

Each run is checked for:
- reverse grade;
- grade below the minimum (0.1%);
- cover less than the minimum (2.5 ft);
- depth over the maximum (6 ft).

## Matching the machine's elevations

The DEM and the design are usually on orthometric heights (NAVD 88). The
machine may work in ellipsoid heights, another geoid, or a local benchmark.
**Every elevation must be on the machine's datum.** There are two ways to get
there:

- **Benchmark**: give a benchmark location and the elevation the machine reads
  there. The DEM is read at the same spot, and the difference is added to every
  elevation.
- **Offset**: give the machine-minus-design difference directly.

If you give both, they add up. Check a benchmark or a known tile elevation with
the machine before you install.

**Control point below the tile invert** lowers every control elevation (for
example to the bottom of the plow boot, or by the pipe wall). Tile invert and
ground are always reported unchanged, apart from the datum offset.

## Files written (to the output folder, named after the field)

| File | Contents |
|---|---|
| `<field>_runs.shp` | 3D lines, one per run, in install direction. Z is the control elevation in output units. Fields: run, kind, size, parent, install, length, inv_ds/us, ctrl_ds/us, min/max grade, min/max depth, tgt_depth, flag. WGS 84 by default (most ag displays), or the projected coordinate system. |
| `<field>_stations.shp` | 3D points at every station, with station, chain, ground, invert, control, depth, grade and note. |
| `<field>.kmz` | Runs in folders by type, with each run's details. Altitudes are control elevations in metres. For Google Earth and displays that take KML. |
| `<field>_stations.csv` | Every station: run, kind, size, station, chain, lat, lon, northing, easting, ground, tile invert, control elevation, depth, grade %, note. |
| `<field>_pnezd.csv` | Point, northing, easting, control elevation, description, for survey data collectors and staking. Off by default. |
| `<field>.dxf` | 3D polylines (R12) on layers TILE_MAIN, TILE_SUBMAIN and TILE_LATERAL, plus run names, for CAD and construction machine control (e.g. Trimble Business Center linework). XY are in the output coordinate system and Z in output units. Off by default. |
| `<field>_run_sheet.html` | Printable run sheet. A summary table, then for each run: its ends, the display settings (target, minimum and maximum depth, minimum grade), its grade sections, and its stations. Open it in a browser and print or save as PDF. |

An **Installation runs** layer with the same attributes loads in QGIS, with
runs to check drawn orange.

Stations fall every station interval (50 ft by default), at every grade break,
at every junction and at both ends. Station text is `12+50.0` per 100 ft
(feet), or `1+250.0` per 1000 m (metres).

## Grade-control displays

The native installable formats aren't published: Ag Leader Intellislope
(`.agsetup` tile plans from SMS Advanced) and Trimble WM-Drain (FieldLevel
autoslope packages). So:

- **Ag Leader InCommand / Intellislope and Trimble FmX / WM-Drain**: load
  `<field>_runs.shp` (WGS 84) or the KMZ as reference or guidance lines. Survey
  each run on the display as usual, then enter the run's target, minimum and
  maximum depth and minimum grade from the run sheet. With a design grade, use
  the grade sections.
- **Construction machine control, total stations, GNSS rovers**: use the DXF
  3D polylines or the PNEZD points, in the projected coordinate system, with the
  datum offset applied.
- **Contractors and records**: the run sheet and the KMZ.

## Notes

- Settings are kept while the project stays open, layers included. A new or
  different project goes back to the defaults, as does **Plugins ▸ Tile Machine
  Export ▸ Reset saved settings to defaults**.
- Grade sections join consecutive segments whose grades differ by less than
  0.005%.

## Authors

Richard Cooke and Anamelechi Falasy.
