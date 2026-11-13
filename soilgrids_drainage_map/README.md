# SoilGrids drainage soil map (QGIS plugin)

The worldwide, metric companion to [SSURGO drainage soil map](../ssurgo_soil_map). Give it a field boundary anywhere in the world and it returns soil zones, each with a drain spacing and a subirrigation spacing in metres. It uses [ISRIC SoilGrids 2.0](https://soilgrids.org) for soil properties and [Rosetta 3](https://www.handbook60.org/rosetta/) for hydraulic conductivity.

SoilGrids values are machine-learning predictions on a 250 m grid, not mapped soils like SSURGO. Treat the zones and spacings as a first estimate, and use the SSURGO plugin in the United States.

## Install

1. Download [`soilgrids_drainage_map.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/soilgrids_drainage_map/soilgrids_drainage_map.zip).
2. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Choose `soilgrids_drainage_map.zip` and click **Install Plugin**.
4. QGIS may warn that installing plugins from ZIP can be unsafe. Accept the warning to continue.

Requires QGIS 3.22 or newer and an internet connection. The plugin is listed under **Vector → Tile Drainage** and **Vector → SoilGrids drainage soil map**.

## Use

1. Load your field boundary, or any polygon layer with a CRS. You can also click **Trace Field and Get Drainage Soil Zones (SoilGrids)** on the toolbar and click the field corners on the map. Right-click or press Enter to finish.
2. Open **Vector → SoilGrids drainage soil map → Get Drainage Soil Zones for Polygons (SoilGrids)…**, or find the tool in the Processing Toolbox.
3. Enter the drainage and subirrigation design values (see below).
4. Under **Soil zones**, click **…**, choose **Save to File**, and pick a `.shp` file name if you want to keep the result. A `.qml` style file with the same name is saved next to it.
5. Click **Run**.

## Inputs

| Input | Default | Notes |
|---|---|---|
| Drain depth (m) | 1.0 | |
| Drainage coefficient (mm/day) | 10 | |
| Depth to impermeable layer (m) | 2.0 | SoilGrids has no restrictive-layer data. SoilGrids stops at 2 m; a deeper barrier assumes the 1–2 m layer continues down to it. |
| Drain diameter (mm) | 100 | Advanced. Only affects the equivalent depth. |
| Irrigation coefficient for subirrigation (mm/day) | 6 | Typically the design peak ET. |
| Subirrigation water level at the drains (m above drain) | 0.6 | Must be higher than the mid-drain water table and no higher than the drain depth. |
| Subirrigation mid-drain water table (m above drain) | 0.45 | |

## How the zones are made

1. For each field polygon, the plugin downloads SoilGrids sand, clay and bulk density at the six standard depths (0–5, 5–15, 15–30, 30–60, 60–100 and 100–200 cm) from the SoilGrids Web Coverage Service. If that service fails, it reads the same layers from the SoilGrids cloud files instead.
2. Each 250 m cell inside the field gets a USDA texture class above the drains (surface to drain depth) and below them (drain depth to the impermeable layer, up to 2 m).
3. Cells with the same pair of classes become one zone, named for example `silt loam over clay loam`, or `silty clay` when both are the same.
4. The zone's sand, silt, clay and bulk density are averaged layer by layer, weighted by the area of each cell inside the field.
5. Ksat for each zone and layer comes from Rosetta 3 (sand, silt, clay and bulk density model). If the Rosetta service can't be reached, Rosetta texture-class averages are used instead, and `ksat_src` says so.

Cells with no SoilGrids data (water, urban areas, or outside the SoilGrids soil mask) are left out, and the log says how many hectares that was.

## Spacing equations

Drain spacing uses the steady-state [Hooghoudt equation](https://en.wikipedia.org/wiki/Drainage_equation) with the water table midway between drains at the soil surface, so the head h equals the drain depth:

q L² = 8 Kb d h + 4 Ka h²

Subirrigation spacing uses the inverse Hooghoudt equation. Water held at Hw in the drains flows out to the midplane, where the water table is Hm:

q L² = 8 Kb d (Hw − Hm) + 4 Ka (Hw² − Hm²)

- q is the drainage coefficient, or the irrigation coefficient for subirrigation.
- Ka is the thickness-weighted Ksat above the drains. For drainage it runs from the surface to the drains; for subirrigation, over the saturated zone from Hw down to the drains.
- Kb is the thickness-weighted Ksat from the drains to the impermeable layer.
- d is the equivalent depth, from van der Molen and Wesseling (1991). Because d depends on L, the equation is solved numerically.

Spacings are rounded to the nearest metre.

## Output fields

| Field | Meaning |
|---|---|
| field_id | Input feature the zone belongs to |
| zone | Zone number within that field |
| soil | Zone name: texture above the drains, and below them if different |
| tex_above, tex_below | USDA texture above and below the drains |
| hectares | Area of the zone inside the field |
| ka_mday, kb_mday | Ksat above and below the drains (m/day) |
| barrier_m | Depth to the impermeable layer used (m) |
| spacing_m | Drain spacing (m), Hooghoudt |
| ka_sub | Ksat of the saturated zone above the drains during subirrigation (m/day) |
| subirr_m | Subirrigation spacing (m), inverse Hooghoudt |
| ksat_src | Rosetta 3, or Rosetta class averages if the service was unavailable |

## Data sources

- Poggio, L., de Sousa, L. M., Batjes, N. H., et al. (2021). SoilGrids 2.0: producing soil information for the globe with quantified spatial uncertainty. *SOIL* 7, 217–240. Data licensed CC BY 4.0.
- Zhang, Y. and Schaap, M. G. (2017). Weighted recalibration of the Rosetta pedotransfer model with improved estimates of hydraulic parameter distributions and summary statistics (Rosetta3). *Journal of Hydrology* 547, 39–53.
- Schaap, M. G., Leij, F. J. and van Genuchten, M. Th. (2001). Rosetta: a computer program for estimating soil hydraulic parameters with hierarchical pedotransfer functions. *Journal of Hydrology* 251, 163–176.
