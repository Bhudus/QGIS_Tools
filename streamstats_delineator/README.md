# StreamStats Delineator (QGIS plugin)

Click on or near a stream; the plugin snaps the point to the nearest StreamStats
stream-grid cell and delineates the watershed draining to it using USGS
StreamStats. Each result is added to two scratch layers:

* **StreamStats watersheds** – polygon, with `area_km2`, `area_mi2`, `region`,
  `huc`, clicked and snapped coordinates, `snap_dist_m`, `created_utc`
* **StreamStats outlets** – the snapped pour point, same attributes
* **StreamStats streams** – NHDPlus flowlines inside the watershed, with
  `ws_id` (links to the watershed), `comid`, `length_km` and `clipped`
  (1 where the line was cut at the watershed boundary). The watershed also
  gets `n_streams` and `stream_km` (total stream length, so drainage density
  is `stream_km / area_km2`). Toggle in Settings.

Right-click a layer › *Make Permanent* to save it.

## Install

1. Download [`streamstats_delineator.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/streamstats_delineator/streamstats_delineator.zip).
2. QGIS › *Plugins › Manage and Install Plugins › Install from ZIP*, choose the
   zip and install.
3. A toolbar button (blue basin, red outlet) appears. Settings are under
   *Plugins › StreamStats Delineator › Settings…*.

For development, symlink or copy the `streamstats_delineator` folder into your
profile's `python/plugins` directory and use the *Plugin Reloader* plugin.

## How it works

The original `streamstatsservices/watershed.geojson` API was **decommissioned on
30 January 2026**. This plugin uses the replacement services:

| Step | Request |
|---|---|
| Region (if "Auto-detect") | US Census geocoder › state code |
| Snap to stream | `GET https://streamstats.usgs.gov/pourpoint/v1/snap/str900?region=&lat=&lon=` |
| Delineate | `GET https://streamstats.usgs.gov/ss-delineate/v1/delineate/sshydro/{region}?lat=&lon=` |

| Outlet COMID (streams) | `GET https://api.water.usgs.gov/nldi/linked-data/comid/position?coords=POINT(lon lat)` |
| Upstream flowlines | `GET https://api.water.usgs.gov/nldi/linked-data/comid/{comid}/navigation/UT/flowlines?distance=` |

**Why NLDI for streams:** SS-Hydro only computes basin characteristics; neither
it nor SS-Delineate returns stream lines. NLDI's upstream-with-tributaries
navigation returns the NHDPlus V2 network above the outlet. The navigation
distance is sized from the basin area (Hack's law × 3, 10–3000 km) instead of
NLDI's 9999 km default. The lines are then clipped to the StreamStats polygon,
which trims the outlet reach (NHDPlus reaches extend past the outlet) and
discards anything from a neighbouring stream. A streams failure never discards
the watershed; it is reported as a warning.

Caveat: NHDPlus V2 is 1:100,000 scale, so it is coarser than the 10 m grid
StreamStats delineates on. Small headwater basins may contain no NHDPlus lines,
and lines can sit slightly off the StreamStats channel.

The polygon is the `globalwatershed` feature (`GlobalWshd == 1`) inside
`bcrequest.wsresp.featurecollection`.

**Validation.** ss-delineate answers HTTP 200 with a tiny hillslope polygon
when a point is off the stream network, and the only sign is a `WarningMsg`
property. The plugin therefore refuses rather than returning a wrong basin:

* snap fails (`couldSnap: false`) › "could not snap" error
* any `WarningMsg` on the result › error showing the message
* missing / unclosed / non-polygon geometry › error
* snapped outlet more than 100 m outside the polygon › error
* snapped outlet far from your click › result added, with a warning (threshold in Settings)

Network requests go through QGIS's own network stack, so proxy and SSL settings
from *Settings › Options › Network* apply. The work runs as a background task
(cancel it from the task manager in the status bar). Up to 3 delineations can
run at once, within USGS's request to keep it to four.

## Notes and limits

* Delineation typically takes 10–30 s; very large basins can take longer.
  If they time out, raise the timeout in Settings and in
  *Settings › Options › Network › Timeout*.
* Not every state supports delineation at all times (as of late October 2026
  the snap service reported no stream grid for FL, MI and NE). Unsupported
  regions surface as a clear error.
* For the regional studies (CRB, DRB, RRB) choose the region manually.
* Always check results against hydrography; StreamStats itself warns that
  automated delineations can be wrong.

## Code layout

| File | Role |
|---|---|
| `client.py` | Pure-Python API client + validation (no QGIS imports, unit-tested) |
| `network.py` | HTTP via `QgsBlockingNetworkRequest` |
| `task.py` | `QgsTask` that runs the client off the GUI thread |
| `map_tool.py` | Click tool; converts the click to WGS84 |
| `layers.py` | Output layers, geometry conversion, ellipsoidal area |
| `plugin.py` | Toolbar/menu wiring, messages, zoom |
| `settings_dialog.py` | Region, snapping, warning distance, zoom, timeout |
| `test/test_client.py` | Offline tests: `python -m unittest discover -s test` |

## Ideas for next steps

* Basin characteristics: `POST https://streamstats.usgs.gov/ss-hydro/v1/basin-characteristics/calculate-using-ssdelineate/?region=&lat=&lon=&BCs=*`
  with the delineate response's `bcrequest` as the body. Characteristics a
  region cannot compute come back as `-999`; treat those as missing.
* Higher-resolution streams: query NHDPlus HR / NHD flowlines from The National
  Map with the watershed polygon, instead of NLDI.
* Stream names and Strahler order: the USGS `wmadata:nhdflowline_network` WFS
  has `gnis_name` and `streamorde` per COMID, for labelling or width-by-order styling.
* A Processing algorithm that delineates every point in a layer (batch mode).
* Flow statistics through the NSS services (`/nssservices/...`).
