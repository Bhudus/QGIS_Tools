# Tile Outlet Submergence (QGIS plugin)

How much a rising water level in the outlet ditch reduces the flow from a tile
drainage system. When the ditch rises over the outlet, water backs up the
tile. The water stands higher in the drains, so less water moves from the soil
into them. This plugin puts numbers on that for a real network.

Requires QGIS 3.34 LTR or newer. Uses only QGIS, GDAL and numpy.

## Install

1. Download [`tile_outlet_submergence.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/tile_outlet_submergence/tile_outlet_submergence.zip).
2. In QGIS: **Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**, then pick
   the zip. (Enable *Show also experimental plugins* under Settings if QGIS hides it.)
3. Run it from the toolbar button, **Vector → Tile Drainage**, **Raster → Tile
   Drainage**, **Plugins ▸ Tile Outlet Submergence**, or the Processing Toolbox. From
   Python:

```python
processing.run("tileoutletsubmergence:tileoutletsubmergence", {
    "NETWORK": "drainage_network.gpkg", "DEM": "dem.tif", "UNITS": 1, "MAP_LEVEL": 2.0,
    "RATING": "TEMPORARY_OUTPUT", "PIPES": "TEMPORARY_OUTPUT",
    "REDUCTION": "TEMPORARY_OUTPUT", "REPORT": "C:/drainage/submergence.html",
    "RATING_CSV": "C:/drainage/submergence_rating.csv"})
```

## Inputs

| Parameter | Notes |
|---|---|
| Buried drainage network | From Illini Subsurface Drain Layout: tile elevation as each vertex's Z, and pipe size in `NOMINAL` (else `ACTUAL_SIZE`), in inches. Other networks work if they carry tile elevations (Z, or `ElevDepth1/2`, or `invert_us/ds`) and sizes. Missing sizes are taken as 4 in. |
| Elevation (DEM) | Ground for every drained cell and at the outlet. Projected coordinate system. |
| Field boundary | Optional. Without it, the drained area is the land within ¾ of a lateral spacing of a pipe. |
| Outlet | Optional. Default: the lowest free end of the network. |
| Design units | Feet (default) or metres. Network elevations are in design units, as the layout plugins write them. |
| Drainage coefficient | 0 (default) takes the median of the network's `D_COEFF`, else 3/8 in/day. |
| Lateral spacing | 0 (default) estimates it from the laterals' `drained_area ÷ length`, else 80 ft. |
| Design water-table depth midway between drains | Default 1.0 ft. |
| Depth of the impermeable layer below the drains | Default 5 ft. Used for Hooghoudt's equivalent depth. |
| Pipe | Single-wall corrugated (Manning's n 0.015 up to 8 in, 0.017 up to 12 in, 0.020 above, as in Illini Drainage Tools), smooth wall (0.011), or clay or concrete (0.013). |
| Highest ditch level | Height above the outlet invert. 0 (default) uses the ground at the outlet plus 0.5 ft, or at least 4 outlet diameters. About 20 levels are run from the invert up. |
| Ditch level to map | Height above the outlet invert for the map outputs. Default 2 ft. |

## Method

1. **Drained cells.** Every drained cell of the DEM drains to its nearest pipe. With a free outlet, it drains at the drainage coefficient.
2. **Hydraulic grade line.** Flows add up down the network. The hydraulic grade line (HGL) starts at the ditch level (or normal depth, with a free outlet) and is stepped up the network. In each step it rises by the Manning friction loss of the flow at the depth just downstream, using the full-pipe loss once the pipe is surcharged. It never falls below normal depth. At a junction each branch starts from its own invert.
3. **Drainage response.** Where the HGL in a drain stands above its invert, the water table midway between drains is higher above the water in the drain by less than before. Hooghoudt's steady-state equation gives the drainage rate as a share of the design rate:

   `q / q_design = f(h_wt − rise) / f(h_wt)`, with `f(h) = 8 de h + 4 h²`

   Here `h_wt` is the height of the design water table above the drain (drain depth minus design water-table depth), `rise` is the HGL above the invert, and `de` is Hooghoudt's equivalent depth (Moody's approximation). The hydraulic conductivity and the spacing cancel out of the ratio. The share runs from 0 to 1.
4. **Steady state.** Less inflow means less friction loss, so steps 2 and 3 are repeated to a steady state. This takes about 20 iterations.

## Outputs

**Rating curve** (table, and the chart in the report). One row for the free outlet, then one per ditch level:

| Field | Meaning |
|---|---|
| `level`, `ditch_elev` | Ditch level above the outlet invert, and its elevation. |
| `outflow` | System outflow, in cfs (feet) or L/s (metres). |
| `pct_free` | Outflow as a % of the free outlet. |
| `area_10`, `area_50` | % of the drained area whose drainage rate is reduced by more than 10% and 50%. |
| `area_none` | % of the drained area not draining at all. |
| `backflow` | % of the drained area where the ditch stands above the design water table, so water could run back into the field without a flap gate. |
| `max_rise` | Largest HGL rise over the free outlet. |
| `surch_len` | Length of pipe running surcharged. |

**Rating curve as CSV** (optional, off by default): the same table as a CSV file, with the
units in the column names (`level_above_invert_ft`, `outflow_cfs`, …; `_m` and `Lps` in
metres). The free-outlet row has `free` as its level. The report's **Export CSV** button
downloads the same file.
The [Tile Outlet Flows](../spreadsheets/tile_outlet_flows/) workbook imports this CSV and gives the flow for each of a column of stream depths.

**Pipes** (lines): `kind`, `d_in`, flows free and at the mapped level, the HGL at the downstream end free and at the mapped level, `hgl_rise`, `above_crown` and `surch_len`. Then, for every step of the rating curve, `q_00`, `q_01`, … (flow) and `rise_00`, `rise_01`, … (HGL rise over the free outlet). Step 00 is the free outlet and the others follow the rows of the rating curve. Styled by HGL rise at the mapped level.

**Drainage rate** (raster, one band per step): each cell's drainage rate as a % of its free-outlet rate. Band 1 is the free outlet and band *i* + 1 is step *i*. Each band's description gives its ditch level. Shown at the mapped level. Red is 0, green is 100, and 255 is outside the drained area.

**Ditch level explorer** (panel). It opens after a run; open it again from **Plugins ▸ Tile Outlet Submergence ▸ Ditch level explorer** or the Tile Drainage menus. The ▲ and ▼ buttons, or the Up and Down keys, step through the ditch levels. They switch the raster band and the pipe styling, so nothing is solved again. For each level the panel shows:
- the ditch level above the outlet invert, its elevation, and how high it stands over the outlet crown (in feet and pipe diameters);
- the system outflow, its % of the free outlet, and the change from the step below;
- a flap-gate warning when the ditch stands above the design water table anywhere in the drained area;
- the rating curve with the current level marked, and the main's profile with the HGL at that level;
- a switch to show the drainage rate, the pipes, or both;
- **Pick a point**: click a pipe or a field cell for its flow and HGL rise, or its drainage rate, at the current level.

The panel lists every run whose layers are in the project. The layers carry the run's description, so it works again after the project is saved and reopened.

**Report** (HTML):
- the inputs;
- the rating-curve table, with an **Export CSV** button;
- the ditch level explorer: the same arrows, readouts and charts, with a map of the drainage rate and the pipes. Click the map for the values at a point;
- the rating-curve chart, marking the outlet crown, the ground at the outlet and the mapped level;
- the main's profile: ground, invert, crown, design water table, and the HGL with a free outlet and at the mapped level;
- the method and assumptions.

## Notes and limits

- **Steady state.** The results show the drainage the system can deliver while the ditch stays at a level. They don't show the timing of a storm, or how long the ditch stays high.
- **Losses left out.** Junction, bend, entrance and exit losses are ignored, so head losses are a little low and the reductions slightly understated.
- **No flow back from the ditch.** The `backflow` column shows where that could happen without a flap gate.
- **Free-outlet surcharging.** Where laterals join a main at the main's invert, the main's own flow backs up into them even with a free outlet. That shows in the free-outlet surcharged length. All results are relative to the free outlet, so this doesn't distort them.
- **Settings.** They are kept while the project stays open, layers included. A new or different project goes back to the defaults, as does **Plugins ▸ Tile Outlet Submergence ▸ Reset saved settings to defaults**.

## Testing

`test/run_synthetic.py` needs `illini_subsurface_drain_layout` and
`controlled_drainage_zones` beside this folder. It checks:
- normal depth: a pipe at half its full capacity runs half full;
- the model on parallel and herringbone layouts generated by the Illini engine, where outflow falls steadily as the ditch rises.

It also writes a sample report.
