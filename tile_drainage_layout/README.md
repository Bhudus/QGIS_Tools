# Tile Drainage Layout — QGIS plugin

Builds a tile drainage layout inside field boundaries:

1. **Setback guide**: each boundary is inset by half the tile spacing and drawn
   as a dashed red guide, so laterals in neighboring fields end up one full
   spacing apart.
2. **Laterals** (optional): the inset area is filled with parallel laterals at
   full spacing, at the angle you choose.
3. **Main connections** (optional): the laterals are connected to a mains
   layer you supply.

## Install
1. If an earlier version called *Tile Drainage Inner Buffer* is installed,
   uninstall it first (Plugins → Manage and Install Plugins → Installed).
2. *Install from ZIP* → `tile_drainage_layout.zip` → *Install Plugin*.

## Use
Toolbar button, *Vector → Tile Drainage → Tile Drainage Layout…*, or
*Processing Toolbox → Tile Drainage → Layout → Tile drainage layout*.

### Setback
- **Field boundaries**: polygon layer (tick *Selected features only* to do one field).
- **Tile spacing** + **units** (feet/meters). 60 ft spacing → 30 ft setback.
- **Spacing field** (optional): per-field spacing; blanks/zeros use the default.
- **Corner style**: Miter (default) keeps square corners parallel to the field edges.

### Laterals
Tick **Generate laterals within the buffer**.
- **Lateral direction**: azimuth clockwise from north (0 = N–S, 90 = E–W).
- **Lateral direction field**: per-field azimuth (blank = default).
- **Parallel to longest edge**: picks each field's direction automatically.
- **Lateral alignment**: *Start on guide line* (leftover gap on the far side)
  or *Centered* (leftover split between both sides).
- **Minimum lateral length**: drops short corner stubs.

### Connecting to a main
- **Mains** (optional line layer, any CRS): the main(s) you've digitized.
  Only mains in or near each field are used.
- **Connection method**:
  - *Extend laterals straight to the main* (default). A lateral that crosses
    a main is split there, so each piece ends on it. Stubs shorter than the
    setback left past the main are dropped. Other laterals are extended
    straight along their own direction, from the nearer end, to the main.
  - *Extend straight, else add shortest connector*. Same as above, but a
    lateral whose line never meets a main gets a straight connector to the
    closest point on the main.
- **Maximum connection distance** (spacing units, 0 = no limit).

## Outputs
| Layer | Geometry | Notes |
|---|---|---|
| Tile guide line | MultiLineString | dashed red; snap to it while digitizing |
| Laterals | LineString | solid blue; `lateral_no`, `azimuth`, `tile_spacing`, `length`, `units`, `connection`, `conn_len` |
| Junctions | Point | `junction` = tee / cross, `laterals` = count; only when mains are supplied |
| Inset area | MultiPolygon | optional |

`connection` is `crossing`, `extended`, `connector` or `none`. Filter on
`"connection" = 'none'` to review laterals that couldn't reach a main.

## Notes
- Holes in a boundary (waterways, ponds, farmsteads) get the same setback.
- Geographic layers (WGS84 etc.) are processed and output in the local UTM zone.
- Works from QGIS 3.16 onward; runs in batch mode and graphical models too.
