# DRAINMOD Weather Files (QGIS plugin)

Click a point on the map to download daily precipitation and maximum/minimum
temperature from [Daymet](https://daymet.ornl.gov/) and write ready-to-use
DRAINMOD weather files (`.RAI` and `.TEM`) in one step.

## Install
1. Download [`DRAINMOD_Weather_Files.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/DRAINMOD_Weather_Files/DRAINMOD_Weather_Files.zip).
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Choose `DRAINMOD_Weather_Files.zip`, click **Install Plugin**.
4. A raindrop button appears on the Plugins toolbar (and under Web → DRAINMOD Weather Files).

## Use
1. Click the button, then click a point on the map. Any project CRS works.
2. In the dialog, check or edit:
   - **Station ID**: filled in from the point (see below), up to 6 characters.
   - **Start date** and **End date** (Daymet starts in 1980).
   - **Output folder**.
3. Click **OK**. The download runs in the background; the message bar lists the files when done.

## Output
For station `402880`, the output folder gets:

| File | Contents |
|---|---|
| `402880.RAI` | DRAINMOD hourly rainfall, hundredths of an inch |
| `402880.TEM` | DRAINMOD daily Tmax/Tmin, °F |
| `402880.rai.wea` | `year,day,precip_mm`, no header |
| `402880.tem.wea` | `year,day,tmax_C,tmin_C`, no header |

The `.RAI` and `.TEM` files match the output of DRAINMOD's weather conversion
utility (metric input) byte for byte: each rain day is spread over hours 17–20
(three equal parts, remainder in hour 20), and temperatures are converted with
the same single-precision arithmetic. The `.wea` files can still be run through
DRAINMOD's own converter if you prefer.

## Notes
- **Station ID**: latitude and longitude truncated to 0.1°, e.g. 40.2435, −88.0334 → `402880`.
  Points in the same 0.1° cell get the same ID, so a second point overwrites the
  first unless you change the ID. West of 100° W the hundreds digit of longitude
  is dropped to keep 6 characters.
- **Leap years**: Daymet omits Dec 31 in leap years. The plugin adds it back as
  day 366 with zero rainfall and Dec 30's temperatures.
- **Coverage**: Daymet covers North America only, 1980 to the most recent complete year.
- Requires internet access; uses QGIS's network/proxy settings.
- Works with QGIS 3.16 and later, including Qt6 builds.
