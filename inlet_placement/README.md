# Surface and Blind Inlets

QGIS plugin (Processing tool) that places an **inlet** at the low point of each depression in
a field, connects it to the tile network with a **lead pipe**, sizes the inlet and the lead,
and **checks the tile below** the junction for the added flow.

Menu: *Vector ▸ Tile Drainage ▸ Surface and blind inlets…*, or the Processing Toolbox
(*Surface and blind inlets*).

## Inputs

| Input | |
|---|---|
| Depressions | Polygons, such as the **Depressions** output of the Depressions and Laylines plugin (its `dep_id` is carried over). |
| DEM | The bare-earth DEM, **not** sink-filled (for example from LiDAR Field DEM). |
| Elevation units | Feet or metres, for the DEM and the network's elevations. |
| Drainage network | A Drainage Network from Illini Subsurface Drain Layout, Illini Subirrigation Layout or Illini Drainage System Design (tile elevation in Z, ground in M, Routine K fields). |
| Connect the inlets to | The nearest main or submain (default), or the nearest tile line of any kind. |
| Inlet type | Surface inlets, blind inlets, or blind inlets where the drainage area is at most a set size (default 2 acres) and surface inlets elsewhere. |
| Drawdown times | Hours to drain the pond: 24 for a surface inlet, 48 for a blind inlet. |
| Surface-water coefficient | Inches per day over the drainage area (default 1/2; 0 = not used). |
| Lead pipe | Minimum cover (2.5 ft), minimum and maximum slope (0.2% and 2%), longest lead (600 ft). |
| Advanced | Smallest lead size (4 in), lead material, sand filter conductivity of a blind inlet (20 ft/day), orifice coefficient (0.6), smallest orifice (1 in), and a pond volume below which no inlet is placed. |

## What it does

**Pond.** The inlet goes at the lowest DEM cell in the depression. The DEM is sink-filled;
the pond is the connected water the fill holds there (it can reach past the depression's
polygon), giving the pond level, depth and volume.

**Drainage area.** The direct drainage area of each depression: the DEM is filled toward its
edges and toward every depression (with a slight gradient so flats drain), and each cell
follows its steepest descent to one of them. Runoff from depressions that fill and spill into
another is not added to it.

**Design flow** (cfs): the larger of the pond volume over the drawdown time, and the drainage
area at the surface-water coefficient.

**Inlet.**
* **Surface inlet** (riser): riser size the lead size, at least 6 in; an **orifice plate**
  sized by the orifice equation, Q = C<sub>d</sub> A √(2 g h), to pass the design flow at
  full pond depth, no smaller than 1 in (none is needed when it would be as large as the
  riser).
* **Blind inlet** (gravel trench under a sand filter): the **filter area** that passes the
  design flow through the sand at a unit gradient, Q = K A.

**Lead pipe.** Straight to the nearest point on the chosen tile lines, joining at the tile
elevation there. The inlet end goes at the minimum cover when that gives a grade between
the minimum and maximum slopes. With too little fall, the lead runs at the minimum slope and
is flagged (less cover at the inlet); with more fall than the maximum slope allows, it runs at
the maximum (deeper at the inlet). The cover is checked along its length on the DEM. Its size
is the smallest standard size that carries the design flow flowing full, by Manning's
equation with Routine K's roughness (0.015 for single wall up to 8 in).

**Tile check.** From each junction down to the outlet (by `ds_link`), each pipe's design flow
(`CUM_SEG_FLOW`) plus the inlet flows joining above it, against its full-flow capacity at its
size (`NOMINAL`) and sizing slope (`SIZE_SLOPE`), with the size it would need when it is over
capacity.

## Outputs

* **Inlets** (points): type, ground and pond level, pond depth, volume and area, drainage
  area, design flow and what governs it, drawdown time, riser and orifice size or filter
  area, lead length, slope, size, inverts and least cover, the network line it joins and its
  kind, the tile check (OK, or the number of pipes over capacity) and any flags.
* **Lead pipes** (lines): with the invert (Z) and ground (M) at each vertex in the DEM's
  units, like the network, so Tile Profile can draw them.
* **Tile check** (lines): the network lines below the inlets with their design flow, inlet
  flow, total, capacity, ratio, the size needed and a status (OK / Over capacity), drawn
  green and red.

Attributes are in feet (elevations too), inches, acres and cubic feet per second.

## Notes

* Rerun the **Tile Size Calculator** on the network after adding inlets if pipes are over
  capacity, or resize the flagged lines.
* A blind inlet drains slowly by design; a large drainage area needs a large filter, which is
  why the default puts blind inlets only on small drainage areas.

## Authors

Richard Cooke and Anamelechi Falasy.
