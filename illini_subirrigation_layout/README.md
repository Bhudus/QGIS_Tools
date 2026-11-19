# Illini Subirrigation Layout (QGIS plugin)

A subirrigation (controlled drainage) tile layout in which **every drainage
subsystem is contained within its own zone**. The field is cut into zones one
elevation interval high. Each zone gets its own laterals and collectors, and
water leaves each zone only through its own control structure, so the boards in
that structure hold the zone's water table.

The plugin is built on the [Illini Subsurface Drain
Layout](../illini_subsurface_drain_layout) engine: the collectors, evenly spaced
laterals, bend limiting, tile burying (Routine J, Minimum cover, Fewest grade
breaks or Least cost) and pipe sizing (Routine K, with the Strahler tile order
of Routine G) are the same, ported from the
[Illini Drainage Tools](https://github.com/FVW-Services/Illini-Drainage-Tools).
The output keeps the Routine J and K fields, so it can go on to Routine L
(Sized Pipe Estimations) and the other Illini routines.

Requires QGIS 3.34 LTR or newer. Uses only QGIS, GDAL and numpy.

## Install

1. Download [`illini_subirrigation_layout.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/illini_subirrigation_layout/illini_subirrigation_layout.zip).
2. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**.
3. Pick `illini_subirrigation_layout.zip` and install. (Enable *Show also
   experimental plugins* under Settings if QGIS hides it.)
4. Run it from the toolbar button, **Plugins ▸ Illini Subirrigation Layout**,
   **Raster ▸ Tile Drainage**, or the Processing Toolbox (*Illini subirrigation
   layout*). From Python:

```python
processing.run("illinisubirrigation:illinisubirrigationlayout", {
    "DEM": "dem.tif", "FIELD": "field.gpkg", "OUTLET": "612345.0,4441234.0 [EPSG:26916]",
    "SPACING": 40, "INTERVAL": 1.0,
    "NETWORK": "TEMPORARY_OUTPUT", "SURFACE_LAYOUT": "TEMPORARY_OUTPUT",
    "ZONES_OUT": "TEMPORARY_OUTPUT", "STRUCTURES": "TEMPORARY_OUTPUT"})
```

## Inputs

Everything on the Illini Subsurface Drain Layout form is here too: DEM, field
boundary, outlet, optional main line(s), design units, lateral spacing,
pattern, orientation, shape, burying and sizing settings (one drainage
coefficient, default 0.375 inch/day, for every line; the burying methods,
shortest run and largest change of grade at a break; *bury from the outlet
invert*). See that plugin's README for the details. This plugin adds one
burying method, *Level within zones* (below). Three defaults differ, to suit
subirrigation:

| Setting | Default here | Why |
|---|---|---|
| Lateral spacing | 40 ft | Subirrigation laterals are usually closer than drainage-only laterals. |
| Lateral orientation / shape | Across the slope, contour-following | Laterals on the contour run parallel to the zone boundaries. |
| Target grade for contour-following laterals | 0% | On the contour. The tile still gets its grade (at least the minimum lateral slope) from the burying. A steeper target grade makes long laterals cut across zones. |

Zone and water-management settings:

| Parameter | Notes |
|---|---|
| Zone polygons | Optional. Leave empty for elevation bands. Each connected piece of a polygon is a zone; field land outside the polygons joins the neighbouring zone. The *Control zones* layer of the Water Management Zones plugin (band mode) works directly. |
| Zone elevation interval | Height of each elevation band, in design units. Default 1 ft. |
| Smallest zone | Acres (hectares in metres). Smaller patches join the neighbour they share most border with. Default 1. |
| Managed water-table depth | Below the zone's low ground. Sets each zone's board elevation. Default 1.5 ft. |
| Control depth | The deepest water table that still counts as controlled. Default 3 ft. |
| Water supply rate | Inch/day (mm/day in metres) over each zone's area. Default 0.3 in/day (about 5.7 gpm per acre). |
| Give zones no collector reaches their own submain | On by default. |
| Advanced: lateral setback from zone boundaries | Default half the spacing, so laterals in neighbouring zones end up about one spacing apart. |
| Advanced: low-ground percentile | The percentile of a zone's ground taken as its low ground. Default 5. |

## How the zones and subsystems are built

1. **Zones.** The DEM is smoothed (the terrain smoothing radius, default the
   spacing) and cut into bands one interval high, from the field's lowest
   point. Each connected patch of a band is a zone. Patches smaller than the
   smallest zone are merged into a neighbour. Zones are numbered from the lowest
   (1) up.
2. **Collectors.** The main (and any submains) are routed as in the Illini
   Subsurface Drain Layout, or taken from your main line layer. Each collector
   vertex belongs to the zone it lies in. A collector that wanders back and
   forth across a zone boundary keeps one zone for at least a spacing, so it
   does not get a structure at every wiggle.
3. **Zone submains.** A zone that no collector runs through (inside the field
   and zone setbacks) gets its own submain. The submain starts on the zone's low
   ground, at the point nearest a collector of a lower zone. It follows the
   zone's low ground, or climbs the fall line where the low ground runs along
   the contours (a band on even ground). It then joins that collector through a
   straight, unperforated **connector** (kind `connector`, drawn grey dashed),
   or directly if it is within half a spacing. No laterals join a connector.
4. **Laterals.** Laterals are placed as in the Illini engine, with three extra
   rules:
   - they keep the zone setback from every zone boundary and never cross one;
   - they join only the collectors of their own zone;
   - short infill laterals (under two spacings) that would need a long
     connector, the slivers left beside a zone boundary, are left out.
   If, after placement, a zone has many laterals that reach a collector only
   through a long connector or not at all, it gets a submain through the poorly
   served part and the laterals are placed again. If that doesn't help, the
   first layout is kept.
5. **Control structures.** A structure goes wherever a collector carries
   water out of a zone:
   - where a main or submain crosses into the next zone;
   - where a submain joins a collector of another zone;
   - where a zone submain enters its connector;
   - at the outlet.

   Every structure lies inside the field boundary. One that would fall outside
   moves up its pipe to the first point inside the field in the same zone. For
   example, when the outlet lies beyond the field edge, the outlet structure
   goes where the main enters the field. Each pipe of the network is split at
   every structure, so every link lies in one zone.
6. **Burying and sizing.** The whole network is buried from the outlet
   upstream with the burying method chosen and sized with Routine K, exactly
   as in the Illini Subsurface Drain Layout. One more burying method is made
   for subirrigation:

   **Level within zones** lays the tile of each zone as one near-level
   system below the zone's board, so that all of it can take water in from
   the structure:
   - In each zone the tile aims for one level line: the middle of the target
     depth band below the zone's low ground, which is a fixed depth below the
     zone's board (with the defaults, 3.75 - 1.5 = 2.25 ft below it).
   - The tile is kept at or below the board, between the minimum cover and
     the maximum depth, and falls at least at the minimum slope. Under higher
     ground in the zone it is deeper, up to the maximum depth.
   - It uses the least-cost search of the Fewest grade breaks and Least cost
     methods: straight runs, the *cost of a grade break*, the *shortest run*
     and the *largest change of grade at a break* apply. A main or submain
     steps down to the next zone's level at that zone's structure.
   - Where the tile cannot stay under the board within the maximum depth
     (high ground in a large zone), it goes as deep as allowed and BURY_CHECK
     says "Tile above the board in places".

   With 2.5 ft zones on the test fields, Routine J had 73 to 84% of the
   lateral length below the boards and 34 to 146 grade breaks; Level within
   zones had 99 to 100% below them and 4 to 7 breaks; with four large zones
   drawn by hand (2.6 ft of relief each) it raised the share from 66% to 97%.
   With 1 ft zones every method keeps all the tile below the boards.
7. **Boards and water management.** Each zone's board elevation sets the managed
   water table the water-table depth below the zone's low ground. The low ground
   is the low-ground percentile of the zone's ground, or the ground at its
   structure if that is lower. For each zone the plugin then reports:
   - the share of its land where the water table is within the control depth
     of the ground;
   - the share that is too deep (high ground), too wet (above the ground), or
     more than half a spacing from a tile;
   - the share of its lateral length whose invert is below the board, which is
     tile that can carry water out into the soil;
   - the water supply for the given rate.

## Output

- **Drainage network**: the Illini Subsurface Drain Layout network (3-D lines
  with tile Z and ground M, every Routine J and K field). It adds a `zone`
  field (0 for connectors) and pipes of kind `connector`. Zone submains are
  named `zone N submain`, and their connectors `zone N connector`.
- **Subirrigation zones** (polygons, clipped to the field):

| Field | Meaning |
|---|---|
| `zone` | Zone number, 1 = lowest. |
| `area` | Acres (hectares in metres). |
| `zmin`, `p_low`, `median`, `p_high`, `zmax`, `range` | Ground elevations, in design units. The percentiles are the low-ground percentile and its complement (5th/95th by default). `range` is `p_high − p_low`. |
| `board_elev` | Board (stop-log) elevation, in design units. |
| `structures`, `laterals`, `lateral_len`, `collector_len` | The zone's structures and pipe. |
| `tile_below_board_pct` | Share of the zone's lateral length below the board. |
| `pct_controlled`, `pct_deep`, `pct_wet`, `pct_undrained` | Shares of the zone's land, as above. |
| `supply` | Water supply rate, gpm (L/s in metres). |
| `flag` | `no control structure`, `no laterals`, `most tile above the board`, `drains into higher zone N`. |

- **Control structures** (points):

| Field | Meaning |
|---|---|
| `id`, `zone`, `to_zone` | The structure, the zone it holds, and the zone it discharges into (0 = outlet or outside the zones). |
| `kind` | `zone boundary`, `junction`, `zone submain outlet` or `outlet`. |
| `pipe` | The pipe it sits on. |
| `ground`, `invert` | Ground and tile invert at the structure. |
| `board_elev`, `board_height`, `board_depth` | Board elevation; height of the boards above the invert; depth of the board top below the ground. |
| `flag` | `board below the tile invert` (the zone cannot be raised), `board above the ground`, `drains into a higher zone`. |

- **Surface Layout**, **Tile elevation** and the **Layout report** as in the
  Illini Subsurface Drain Layout. The report adds tables of the zones and the
  structures.
- **Water-table status** (optional raster, off by default): each field cell is
  marked controlled, too deep, too wet, or more than half a spacing from a
  tile, for the zone boards.

**Saved settings.** As in the Illini Subsurface Drain Layout, the tool saves
its settings each time it runs and opens with them next time. Inputs and
outputs are not saved. **Plugins → Illini Subirrigation Layout → Reset saved
settings to defaults** returns to the plugin defaults.

## Choosing settings

- **Uniform slope:** keep the defaults. The parallel main climbs the slope
  through every zone, with one structure at each zone boundary and laterals
  along the contours on both sides.
- **Swales and draws:** *Herringbone* with laterals across the slope. The main
  follows the thalweg, the side zones get their own submains, and structures
  sit where each submain leaves its zone.
- **Zone interval:** a smaller interval gives more zones and structures but a
  more even water table. Watch `pct_controlled` and `pct_deep`. The Controlled
  Drainage Zones plugin's *Interval options* table compares intervals before
  you lay out the tile.
- **Burying method:** *Level within zones* keeps the most tile below the
  boards, with the fewest grade breaks; it matters most with large zones
  (an interval of 2 ft or more, or your own zone polygons). Routine J
  follows the ground instead, so in a large zone its tile rises above the
  board under higher ground.
- **Board below the tile invert:** the zone's low ground is too close to the
  tile at the structure. Reduce the water-table depth, or deepen the tile
  (target band, or lower the outlet).
- **Bill of materials:** in the Tile Size Calculator, **From map** on the Bill
  of materials tab counts the water control structures from this plugin's
  *Control structures* layer, for the quote.
- **Orange laterals with long connectors** in a zone mean the zone's collector
  doesn't reach most of it. Draw a submain for that zone in the main line
  layer, or split the zone with your own zone polygons.

## Limitations

- A planning tool. Check every layout, structure location and board setting
  against field conditions, the water supply and local standards.
- Structures are placed on collectors only. A lateral never crosses a zone
  boundary, so none is needed on a lateral.
- The water-table status is a static estimate from the boards and the ground.
  It does not model lateral flow between tiles, seepage or ET.
- One outlet per run, as in the Illini Subsurface Drain Layout.

## Credits and licence

The layout engine, tile burying scheme and pipe sizing come from the Illini
Subsurface Drain Layout plugin, which ports Routine J (Tile Burying System),
Routine K (Network Pipe Sizing) and the tile ordering of Routine G of the Illini
Drainage Tools by Anamelechi Falasy and Richard Cooke, University of Illinois
Urbana-Champaign, released under the GNU General Public License v3 (see
`LICENSE_ILLINI_DRAINAGE_TOOLS.txt`). This plugin is distributed under the same
licence, GPL v3 or later.

## Development

The engine (`core/`) is pure numpy with no QGIS imports. `core/zonal.py`
subclasses the Illini `DrainageLayout`. `test/run_synthetic.py` runs it on
synthetic terrain (tilted plane, swale, rolling ground, and supplied zones),
checks that no lateral leaves its zone, and writes plots to `./out` (needs
matplotlib):

```
python test/run_synthetic.py all
```
