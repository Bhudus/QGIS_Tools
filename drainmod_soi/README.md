# DRAINMOD Soil File Builder (QGIS plugin)

Click a point on the QGIS map → the plugin queries USDA-NRCS **Soil Data Access**
(SSURGO), combines the horizons into at most five layers, computes the soil
water characteristic with the **van Genuchten** equation, and writes a
DRAINMOD `.soi` file.

## Install

1. Download [`drainmod_soi.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/drainmod_soi/drainmod_soi.zip).
2. Copy the `drainmod_soi` folder from the zip into your QGIS profile's plugin folder:
   * Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   * macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
   * Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   (or *Plugins → Manage and Install → Install from ZIP* with the zip file)
3. Restart QGIS and enable **DRAINMOD Soil File Builder** in the Plugin Manager.
4. Add any basemap (e.g. OpenStreetMap from XYZ Tiles) so you can see where to click.

## Use

1. Click the toolbar button (soil profile icon) – the dialog opens and the map tool is active.
2. Click the map. The map unit and its components appear; the dominant soil
   component is chosen (miscellaneous areas such as *Water* are skipped). Pick
   another component from the list if you want.
3. Enter maximum and average root depth. The preview updates live.
4. **Save .soi…**

## What goes in the file

| Line | Content | Source |
|---|---|---|
| 1 | Soil name | component name + surface texture, e.g. `Gilford fine sandy loam` (editable) |
| 2 | layers, `0` | after merging (≤ 5) |
| 3 | points, Ksat (cm/hr), layer bottom (cm) | 16 points; SSURGO `ksat_r` (µm/s × 0.36) |
| 4 | θ, suction (cm) | van Genuchten at 0, 20, 40, 60, 80, 100, 150, 200, 250, 330, 500, 1000, 2500, 5000, 10000, 15000 cm |
| 5 | max root, avg root, layer of avg root | user + computed |

Fields use the same fixed column widths as the sample file
(`I2,I2` / `I2,F9.4,F9.1` / `F5.3,F8.1` / `3I4`), with explicit decimal
points, and CRLF line endings.

## Water retention methods

* **Rosetta class averages** (default) – Schaap et al. (2001), by USDA texture class.
* **Carsel & Parrish (1988)** class averages.
* **Rosetta 3 web service (SSC + Db)** – continuous prediction from
  sand/silt/clay and 1/3-bar bulk density (`dbthirdbar_r`).
* **Rosetta 3 web service (SSC + Db + θ33 + θ1500)** – also sends Rosetta's two
  optional inputs: volumetric water content at 33 kPa (`wthirdbar_r`) and
  1500 kPa (`wfifteenbar_r`), converted from percent to cm³/cm³ and
  thickness-weighted for merged layers.

For both Rosetta 3 options, if any layer lacks one of the inputs, the richest
model every layer can supply is used (SSC+BD+TH33 → SSC+BD → SSC) and a
warning names what was missing. If the service can't be reached, Rosetta
class averages are used, with a warning.

The texture class of each (merged) layer is computed from SSURGO sand/silt/clay
with the USDA texture-triangle rules; the texture name is used only when the
fractions are missing.

## How horizons become ≤ 5 layers

1. Horizons are sorted, gaps closed. Bedrock/cemented horizons (R, Cr,
   "bedrock", "cemented" …) and everything below them are dropped.
   Organic or unclassifiable horizons (muck, peat) are folded into the nearest
   mineral horizon. Each of these produces a warning.
2. *Optional* (on by default): adjacent horizons that are near-identical
   (same class, similar texture, Ksat within ~2×) are merged.
3. While there are more layers than allowed, the adjacent pair with the lowest
   merge cost is merged. Cost = texture difference (1 unit per 10 percentage
   points) + Ksat difference (1 unit per ~3×) + 0.5 if classes differ, scaled by
   √(thinner layer thickness / 30 cm) so thin layers are absorbed first.
4. Merged layers get thickness-weighted sand/silt/clay/Db and
   thickness-weighted arithmetic Ksat (lateral flow – DRAINMOD's use);
   a harmonic mean is available.

The deepest layer ends at the SSURGO profile bottom (often 150–200 cm). DRAINMOD
needs the soil profile to reach the impermeable layer depth in your drainage
system inputs; use **Extend deepest layer to** if it must go deeper (the sample
file uses 500 cm).

## Outside QGIS

The `core` package is plain Python (standard library only):

```
python -m drainmod_soi.core.cli --lat 40.10 --lon -88.24 --max-root 45 --avg-root 30 -o drummer.soi
```
