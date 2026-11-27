# County Simulations

**Workbook:** `County_Simulations.xlsm` · Version 2.1 ·
[download zip](https://github.com/Bhudus/QGIS_Tools/raw/main/spreadsheets/county_simulations/county_simulations.zip)

Runs DRAINMOD for a county and soil over a range of drain depths and spacings, for
observed weather or for projected (future) climate, and charts corn and soybean relative
yield and the water balance against drain spacing. Use it to find the depth and spacing
that maximize yield under current and projected climate stress, with or without
drainage water management.

The site, weather and soil come online: the site is the county centroid (editable), the
observed weather comes from [Daymet](https://daymet.ornl.gov/), the simulated weather
from [NASA NEX-GDDP-CMIP6](https://www.nccs.nasa.gov/services/data-collections/land-based-products/nex-gddp-cmip6)
(downscaled CMIP6 climate models), and the soil from the Illinois soil database or
[SSURGO](https://sdmdataaccess.sc.egov.usda.gov/) at the site. You can also use
weather files you already have.

## Needs

* Windows Excel with macros enabled, and an internet connection for the online data.
* DRAINMOD (`dmhydro.exe`, free from
  [NC State University](https://bae.ncsu.edu/agricultural-water-management/drainmod/download/)).
  It can be anywhere on your computer: the workbook asks where it is.
* A DRAINMOD folder for the workbook with `crops`, `inputs` and `outputs` folders
  (for example `C:\ILLWIS`). For "Soil from dropdown", the Illinois soil files in
  `Illinois\Soils` inside it.

## Use

1. Open the workbook (see *Opening a downloaded workbook* in the
   [spreadsheets README](../README.md)). It asks for the DRAINMOD folder, then for
   `dmhydro.exe`; the dialog opens at your last choice, which is remembered.
2. On **Main**, pick the state, county and soil, and the **time period**:
   * observed weather (Daymet, the years set on New Flow File),
   * a simulated baseline, 1996–2025 (the climate model's historical run to 2014, then the
     chosen emissions scenario),
   * 2040–2069 or 2070–2099 (the chosen scenario).
3. Click **Change Simulation Parameters**. On the **New Flow File** sheet, set the drain
   spacing and depth (min, max, interval, in feet) and, in the **Online data** table,
   check the site and choose:
   * the **climate model** (default GFDL-ESM4) and **emissions scenario** (default SSP2-4.5),
   * the **soil source** (Illinois soil from the dropdown, or SSURGO at the site),
   * **Weather files**: *Download*, or *My own files* to use DRAINMOD `.RAI` and `.TEM`
     files you already have (you pick them when you run; the paths are kept in the two
     rows below).
4. Click **Generate Daily Drain Flows** to run DRAINMOD for every depth and spacing.
   The weather file is created the first time a site and period are used and saved, so
   the next run starts at once (set *Re-download online data* to Yes to fetch it again).
   **Download Weather Files** and **Download Soil Files** fetch the inputs without
   running DRAINMOD. After changing only the time period on Main, click **Run New
   Simulation after Changing Time Period**.
5. Back on **Main**, the option buttons switch the charts of corn and soybean relative
   yield (overall, excess water, drought, SDI excess, drought stress) and of drained
   volume, runoff, infiltration, evapotranspiration, SEW, dry days and work days against
   drain spacing. **View Report** and **Economic Analysis** open those sheets.

### Weather downloads

Daymet is one download. NEX-GDDP-CMIP6 needs one download per variable and year (about 90
for a 30-year period), made three at a time. If NASA's server does not return some of
them, the workbook waits and tries again (20 s, 1 min, 2 min) with the status bar showing
what it is waiting for, and each year is saved as it arrives: if the server is still not
answering, run again later and it continues where it stopped. If secure connections from
Excel are blocked (for example by a VPN or firewall), downloads are retried with the
`curl.exe` that comes with Windows 10 and 11.

## Code

`vba/` holds the workbook's macros as text, for reading; the workbook already contains them.

* `Module4.bas`: online data (site, Daymet, NEX-GDDP-CMIP6, SSURGO), the weather files,
  the downloads, and the DRAINMOD program location.
* `Module1.bas`, `Module2.bas`, `Module3.bas`: DRAINMOD input files, runs and results.
* `New_Flow_File_Sheet9.cls`: the New Flow File sheet, with **Generate Daily Drain Flows**
  and the download buttons.
* `Main_Sheet4.cls`: the Main sheet's time periods, buttons and chart options.
* `Economic_Analysis_Sheet13.cls`: the Economic Analysis sheet.
* `FrmIntroduction.frm`: the start-up window (DRAINMOD folder).

## Versions

* **2.1** – County centroid lookup works with the reorganized US Census TIGERweb service:
  it tries the current counties layer, then the stable Census 2020 counties layer, and
  if neither answers the message shows what the server returned.
* **2** – First published version. Online site, weather (Daymet, NEX-GDDP-CMIP6) and soil
  (SSURGO) data; `dmhydro.exe` chosen by the user instead of the DRAINMOD folder's
  `program` folder; option to use your own `.RAI`/`.TEM` files; NEX-GDDP-CMIP6 downloads
  three at a time with patient retries and resume; downloads fall back to Windows'
  `curl.exe` when Excel cannot open a secure connection.
