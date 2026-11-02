# DRAINMOD Climate-Scenario Weather Files (QGIS plugin)

Companion to [DRAINMOD Weather Files](../DRAINMOD_Weather_Files), which uses observed Daymet data.

Creates DRAINMOD weather input for **future climate scenarios** from
downscaled CMIP6 daily precipitation and max/min temperature.

## Data sources

| Source | Coverage | How it is accessed |
|---|---|---|
| **NASA NEX-GDDP-CMIP6** | Global, 0.25°, 35 GCMs, historical 1950–2014 + SSP1-2.6/2-4.5/3-7.0/5-8.5 to 2100 | Point extraction from the NASA NCCS THREDDS NetCDF Subset Service – only the grid cell is downloaded (≈3 small requests per year). |
| **LOCA2** | N. America, 1/16° (~6 km), 27 GCMs, historical + SSP2-4.5/3-7.0/5-8.5 | Finds and downloads the regional files from `cirrus.ucsd.edu/~pierce/LOCA2/CONUS_regions_split/` (large), then reads the cell locally. |
| **Local NetCDF** | anything CF-compliant | LOCA2 downloads, LLNL GDO-DCP point subsets, NEX-GDDP files from AWS S3, CORDEX, … |
| **Custom THREDDS NCSS** | any server | URL template with `{var} {model} {scenario} {year}`. |

## Workflow

1. **Location** – type lat/lon and a DRAINMOD station ID, *Pick on map*, or use a point layer (one set of files per point, name from a field).
2. **Climate data** – choose dataset, GCMs and scenarios, and the baseline / future periods (whole years).
3. **Output** – folder, optional Hargreaves PET and full daily CSV.

Runs in the background (QGIS task bar). Progress and errors go to the *DRAINMOD Climate* tab of the Log Messages panel.

## Output (per site / model / scenario)

```
<site>/<model>_<scenario>_<y0>-<y1>/<station>.RAI      DRAINMOD hourly rainfall, hundredths of an inch
<site>/<model>_<scenario>_<y0>-<y1>/<station>.TEM      DRAINMOD daily Tmax/Tmin, deg F
<site>/<model>_<scenario>_<y0>-<y1>/<station>.rai.wea  year,day,precip_mm
<site>/<model>_<scenario>_<y0>-<y1>/<station>.tem.wea  year,day,tmax_C,tmin_C
<site>/<model>_<scenario>_<y0>-<y1>/<station>.PET      DRAINMOD daily PET, hundredths of an inch (Hargreaves, optional)
<site>/<model>_<scenario>_<y0>-<y1>/<station>.pet.wea  year,day,PET_mm (Hargreaves, optional)
<site>/<model>_<scenario>_<y0>-<y1>/<station>_daily.csv, README.txt
ensemble_summary.csv                                   one row per run
```

The `.RAI` and `.TEM` files are written with the same code as the
DRAINMOD_Weather_Files (Daymet) plugin, which matches DRAINMOD's weather
conversion utility (metric input) byte for byte: each rain day is spread over
hours 17–20, temperatures are whole °F. The `.PET` file uses the same
utility layout (two lines per month, hundredths of an inch) and was checked
byte for byte against a utility-made file. The station ID (≤ 6 characters)
defaults to latitude and longitude truncated to 0.1°, e.g. 35.78, −78.68 →
`357786`.

## What the plugin handles for you

* Units: kg m⁻² s⁻¹ → mm/day → hundredths of an inch; K → °C → °F.
* Model calendars: `noleap` – choose on the Output tab: insert Feb 29 (0 rain,
  interpolated T; default) or Daymet style (keep the 365 model days in order and
  add Dec 31 as day 366 with 0 rain and Dec 30 temperatures, as the
  DRAINMOD_Weather_Files plugin does for Daymet);
  `360_day` (360 days spread over 365/366 real days, rain total conserved),
  `all_leap`.
* Partial first/last years are dropped so DRAINMOD gets whole years.
* Coastal cells with no data → nearest valid cell (local NetCDF).
* Tmin > Tmax days are swapped; gaps are filled and reported.
* Longitudes 0–360 vs −180–180.

## Notes

* NEX-GDDP-CMIP6 and LOCA2 are already bias-corrected against gridded
  observations (GMFD and Livneh-unsplit respectively). Compare the
  historical run with your station record before using absolute values;
  many DRAINMOD studies use the *change* between baseline and future.
* Use several GCMs (an ensemble) rather than one.
* For a single LOCA2 site, a point subset from https://gdo-dcp.ucllnl.org is
  far smaller than the regional files – load it under *Local NetCDF files*.
* Local NetCDF reading uses `netCDF4` if installed, otherwise GDAL (bundled with QGIS).

## Install

1. Download [`drainmod_climate.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/drainmod_climate/drainmod_climate.zip).
2. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Choose `drainmod_climate.zip` and click **Install Plugin**.
4. QGIS may warn that installing plugins from ZIP can be unsafe. Accept the warning to continue.

Requires QGIS 3.16 or newer (Qt5 or Qt6 builds) and an internet connection for
the online data sources. No extra Python packages are needed.

## Citation

* Thrasher, B. et al. (2022) NASA Global Daily Downscaled Projections, CMIP6. *Scientific Data* 9, 262.
* Pierce, D. W., Cayan, D. R., Feldman, D. R., Risser, M. D. (2023) Future increases in North American extreme precipitation in CMIP6 downscaled with LOCA. *J. Hydrometeorology*.
