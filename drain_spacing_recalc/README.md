# Drain spacing recalculator (QGIS plugin)

Companion to [SSURGO drainage soil map](../ssurgo_soil_map) and [SoilGrids drainage soil map](../soilgrids_drainage_map). Pick a soil map made by either plugin, enter a new design scenario, and get a copy of the soil map with recalculated drain and subirrigation spacings. It works offline, from the Ksat values stored on each soil polygon.

## Install

1. Download [`drain_spacing_recalc.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/drain_spacing_recalc/drain_spacing_recalc.zip).
2. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Choose `drain_spacing_recalc.zip` and click **Install Plugin**.
4. QGIS may warn that installing plugins from ZIP can be unsafe. Accept the warning to continue.

Requires QGIS 3.22 or newer. The plugin is listed under **Vector → Tile Drainage** and **Vector → Drain spacing recalculator**, and has a toolbar button.

## Use

1. Make a soil map with SSURGO drainage soil map (1.10.0 or later) or SoilGrids drainage soil map (0.2.0 or later).
2. Select that layer in the Layers panel and click **Recalculate Drain Spacing…**. The selected soil layer is filled in for you.
3. Choose the units, enter the scenario, and click **Run**.
4. To keep the result, under **Recalculated spacings** click **…**, choose **Save to File**, and pick a `.shp` file name. A `.qml` style file with the same name is saved next to it.

Each run adds a new layer named after its scenario, for example `Spacing 4 ft, 0.5 in/day`, so you can compare scenarios side by side. Tick **Selected features only** to recalculate just some soils. The output can be used as the input of another run.

## Inputs

All lengths, coefficients and the drain diameter are in the units you choose: **US customary** (ft, in/day, in) or **Metric** (m, mm/day, mm). The output fields use the same units. A SSURGO soil map can be recalculated in metric, and a SoilGrids one in US customary units.

| Input | Default | Notes |
|---|---|---|
| Drain depth | 3.5 | |
| Drainage coefficient | 0.375 | |
| Drainage mid-drain water table, height above drain | blank | Blank puts the water table at the soil surface. |
| Depth to impermeable layer | blank | Blank uses each soil's own depth (`barrier_ft` or `barrier_m`). |
| Irrigation coefficient for subirrigation | 0.25 | |
| Subirrigation water level at the drains, height above drain | 2.0 | Must be higher than the mid-drain water table and no higher than the drain depth. |
| Subirrigation mid-drain water table, height above drain | 1.5 | |
| Drain diameter | blank | Advanced. Blank uses 4 in or 100 mm. Only affects the equivalent depth. |

The defaults are US customary. When you switch to metric, enter metric values too. The tool won't run with a metric drain depth over 5 m, in case feet were entered by mistake.

## Equations

Drain spacing uses the steady-state [Hooghoudt equation](https://en.wikipedia.org/wiki/Drainage_equation), with the mid-drain water table Hm above the drains:

q L² = 8 Kb d Hm + 4 Ka Hm²

Subirrigation spacing uses the inverse Hooghoudt equation. Water held at Hw in the drains flows out to the midplane, where the water table is Hm:

q L² = 8 Kb d (Hw − Hm) + 4 Ka (Hw² − Hm²)

- q is the drainage coefficient, or the irrigation coefficient for subirrigation.
- Ka is the thickness-weighted Ksat of the saturated zone above the drains. Kb is the same from the drains to the impermeable layer.
- d is the equivalent depth, from van der Molen and Wesseling (1991). Because d depends on L, the equation is solved numerically.

Spacings are rounded to the nearest 5 ft or 1 m.

## Where Ksat comes from

The soil plugins save each soil's layer-by-layer Ksat in the `ksat_prof` field, as `top-bottom:Ksat` with depths in cm and Ksat in m/day. That lets this tool average Ksat over the right depths for any drain depth and water level.

Soil maps made before `ksat_prof` was added only have Ka and Kb for the drain depth they were made with. For those, Ka is used above the new drains and Kb below them, and the log says how many soils this applied to. The results are close for small depth changes; re-run the soil plugin for the most accurate results.

## Output fields

The output keeps the soil fields of the input (map unit, soil name, area and so on, plus `ksat_prof`) and replaces the spacing fields with these:

| US customary | Metric | Meaning |
|---|---|---|
| drain_ft | drain_m | Drain depth |
| dc_inday | dc_mmday | Drainage coefficient |
| mid_ft | mid_m | Drainage mid-drain water table, height above drain |
| barrier_ft | barrier_m | Depth to impermeable layer used |
| ka_ftday | ka_mday | Ksat above the drains, drainage (per day) |
| kb_ftday | kb_mday | Ksat from the drains to the impermeable layer (per day) |
| spacing_ft | spacing_m | Drain spacing |
| ic_inday | ic_mmday | Irrigation coefficient |
| hw_ft | hw_m | Subirrigation water level at the drains, height above drain |
| hm_ft | hm_m | Subirrigation mid-drain water table, height above drain |
| ka_sub | ka_sub | Ksat of the saturated zone above the drains, subirrigation (ft/day or m/day) |
| subirr_ft | subirr_m | Subirrigation spacing |
