# Drainage Intensity

QGIS plugin (Processing tool) that maps the **drainage intensity** of a designed tile
network: the rate, in in/day or mm/day, at which the drains can remove water from each part
of the field. It reads the Drainage Network made by **Illini Subsurface Drain Layout**,
**Illini Subirrigation Layout** or **Illini Drainage System Design**, and shows which parts
of the field meet a target drainage coefficient and which are under-drained.

Menu: *Raster ▸ Tile Drainage ▸ Drainage intensity map…*, or the Processing Toolbox
(*Drainage intensity*).

## How it works

For every cell of a grid over the field:

1. **Nearest drain**: its distance, and the drain depth there: the ground (M) less the tile (Z)
   elevation stored at every vertex of the network.
2. **Local spacing**: from the nearest point on that drain, out through the cell, to the
   drainage divide, where another drain becomes nearer, doubled. Where there is no drain
   beyond, the field edge is the divide (a no-flow boundary). So the map follows the real
   layout: the strips between laterals, the wedges near junctions, the ground past the lateral
   ends and along the field edges.
3. **Drainage intensity** of that strip, by one of two equations:

| Equation | Condition | Formula |
|---|---|---|
| **Hooghoudt** | water table between the drains | q = (8 K<sub>b</sub> d<sub>e</sub> m + 4 K<sub>a</sub> m²) / L² |
| **Kirkham** | ponded surface (as in DRAINMOD) | q = 4πK (t + b − r) / (g L) |

* m: height of the water table midway between the drains above the drains (drain depth less
  the midpoint water-table depth; with the water table at the surface, m = drain depth).
* d<sub>e</sub>: equivalent depth of van der Molen and Wesseling (1991), for the depth from the
  drains to the barrier, the spacing and the drain radius. The same equation as the SSURGO
  and SoilGrids drainage soil maps.
* K<sub>a</sub>, K<sub>b</sub>: conductivity above and below the drains; K for Kirkham is the
  thickness-weighted conductivity from the surface to the barrier.
* t: ponded depth; b: drain depth; r: drain effective radius; L: local spacing.
* g: Kirkham's geometry term,
  g = 2 ln[tan(π(2b − r)/4h) / tan(πr/4h)] + 2 Σ<sub>m≥1</sub> ln[(cosh(πmL/2h) + cos(πr/2h))
  (cosh(πmL/2h) − cos(π(2b − r)/2h)) / ((cosh(πmL/2h) − cos(πr/2h))(cosh(πmL/2h) +
  cos(π(2b − r)/2h)))], with h the depth from the surface to the barrier. This form (b the
  drain depth below the surface) was checked against a finite-difference solution of the
  ponded drain, which it matches to within 3% (the difference shrinks as that grid is refined).

## Inputs

| Input | |
|---|---|
| Drainage network | Line layer of tile (Z = tile, M = ground). Lines without Z and M take the default drain depth. |
| Lines that drain | All tile lines (mains and submains are usually perforated too), or laterals only (by the `kind` field). |
| Field boundary | Optional. Limits the map and acts as a drainage divide. Without it the map covers the network plus the farthest distance considered. |
| Units | US (ft, in/day) or metric (m, mm/day): the units of the values typed in and of the map. |
| Equation | Hooghoudt or Kirkham. |
| Target drainage coefficient | The design drainage coefficient the map is drawn against (default 3/8 in/day). |
| Hooghoudt: water table midway | Depth below the surface (0 = at the surface, the usual drainage intensity). |
| Kirkham: ponded depth | Depth of water on the surface (in or cm). |
| Soil map | Optional polygons with Ka, Kb and the depth to the barrier, such as the **SSURGO drainage soil map** (`ka_ftday`, `kb_ftday`, `barrier_ft`) or the **SoilGrids drainage soil map** (`ka_mday`, `kb_mday`, `barrier_m`); these fields and their units are found by themselves. With their layer profile (`ksat_prof`), Ka and Kb are worked out for each drain's own depth instead of the depth the soil map was made for. |
| Ka, Kb, barrier depth | Uniform values wherever there is no soil map. |
| Advanced | Field names and units for other soil layers, the default drain depth, the drain effective radius (default 0.2 in, the DRAINMOD value of about 0.51 cm for 4-in corrugated tubing), the cell size (0 = automatic) and the farthest distance from a drain considered drained. |

## Output

A raster with four bands:

1. **Drainage intensity** (in/day or mm/day), drawn in four classes: less than half the
   target, below the target, meets the target, and half again the target or more.
2. **Local drain spacing** (ft or m).
3. **Drain depth** (ft or m).
4. **Class**: 1 meets the target, 2 below it, 3 no drain within reach.

The log gives the field area, the mean and median drainage intensity, the value the lowest
tenth of the field falls below, the share of the field below the target, the share with no
drain within reach, and the median local spacing.

## Notes

* Both equations are steady-state: they give the rate the drains can carry for the stated
  water table or ponding, not a time to drain. They are the two equations DRAINMOD uses for
  drainage flux (Hooghoudt with the water table below the surface, Kirkham when it is
  ponded).
* The SSURGO and SoilGrids soil maps compute their spacing with the drain's actual radius
  (half the diameter) in the equivalent depth; this tool uses the effective radius, so at the
  soil map's spacing it gives a slightly lower intensity than the coefficient the spacing was
  made for.

## Authors

Richard Cooke and Anamelechi Falasy.
