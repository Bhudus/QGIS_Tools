# -*- coding: utf-8 -*-
"""
Pure-Python client for the USGS StreamStats delineation services.

No QGIS imports here, so it can be unit-tested outside QGIS.

The legacy ``streamstatsservices/watershed.geojson`` endpoint was decommissioned
on 2026-01-30. This module uses its replacements:

    1. Snap the clicked point to the nearest StreamStats stream-grid cell
       GET /pourpoint/v1/snap/str900?region={r}&lat={lat}&lon={lon}
         -> {"couldSnap": bool, "output": {"type": "Point", "coordinates": [lon, lat]}}

    2. Delineate the watershed upstream of the snapped point
       GET /ss-delineate/v1/delineate/sshydro/{region}?lat={lat}&lon={lon}
         -> {"stateAbbreviation": .., "bcrequest": {"wsresp": {"featurecollection":
               [[{"name": "globalwatershedpoint", "feature": FeatureCollection},
                 {"name": "globalwatershed",      "feature": FeatureCollection}]]}}}

Important: ss-delineate returns HTTP 200 with a tiny "hillslope sliver" polygon
when a point is not on the stream network. The only signal is a ``WarningMsg``
property. This client therefore snaps first and validates the result, raising
instead of returning a misleading polygon.
"""

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://streamstats.usgs.gov"
SNAP_URL = BASE_URL + "/pourpoint/v1/snap/str900"
DELINEATE_URL = BASE_URL + "/ss-delineate/v1/delineate/sshydro/{region}"
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"

# USGS Hydro Network-Linked Data Index (NHDPlus V2 medium-resolution network).
# SS-Hydro computes basin characteristics only; it has no stream-line output,
# so streams come from NLDI upstream-with-tributaries navigation.
NLDI_BASE = "https://api.water.usgs.gov/nldi/linked-data"
NLDI_POSITION_URL = NLDI_BASE + "/comid/position"
NLDI_UT_URL = NLDI_BASE + "/comid/{comid}/navigation/UT/flowlines"

USER_AGENT = "QGIS-StreamStats-Delineator/1.0"

# StreamStats study-area codes: the 50 states, DC and PR, plus regional studies.
# Not every code supports delineation at every time (USGS adds/removes regions);
# an unsupported region surfaces as RegionNotSupportedError.
REGIONS = [
    "AK", "AL", "AR", "AZ", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI", "IA",
    "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MS",
    "MT", "NC", "ND", "NE", "NH", "NJ", "NM", "NV", "NY", "OH", "OK", "OR", "PA",
    "PR", "RI", "SC", "SD", "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY",
    "CRB", "DRB", "RRB",
]


# --------------------------------------------------------------------------- errors
class StreamStatsError(Exception):
    """Base class for all errors raised by this client."""


class TransportError(StreamStatsError):
    """Network failure, timeout or 5xx after retries."""


class RegionNotSupportedError(StreamStatsError):
    """The region code is unknown to StreamStats or has no delineation data."""


class NotSnappableError(StreamStatsError):
    """The point could not be snapped to the StreamStats stream grid."""


class DegenerateDelineationError(StreamStatsError):
    """StreamStats answered, but the watershed is missing, warned or invalid."""


class UnexpectedResponseError(StreamStatsError):
    """The response shape is not what this client understands (service change?)."""


# --------------------------------------------------------------------------- HTTP
def _default_fetch(url, params, timeout):
    """GET ``url`` with ``params`` and return (status_code, body_text)."""
    full = url + "?" + urllib.parse.urlencode(params) if params else url
    req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT,
                                                "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        return e.code, body


class StreamStatsClient:
    """
    Parameters
    ----------
    fetch : callable(url, params, timeout) -> (status:int, body:str)
        HTTP GET implementation. Defaults to urllib. The QGIS plugin injects one
        based on QgsBlockingNetworkRequest so QGIS proxy/SSL settings apply.
    timeout : float
        Per-request timeout, seconds. Large basins can take well over 30 s.
    retries : int
        Retries for timeouts and 5xx responses (exponential backoff).
    is_canceled : callable() -> bool, optional
        Polled between steps so a QgsTask can abort.
    log : callable(str), optional
    """

    def __init__(self, fetch=None, timeout=120.0, retries=2,
                 is_canceled=None, log=None):
        self.fetch = fetch or _default_fetch
        self.timeout = timeout
        self.retries = retries
        self.is_canceled = is_canceled or (lambda: False)
        self.log = log or (lambda msg: None)

    # ---------------------------------------------------------------- plumbing
    def _get_json(self, url, params):
        delay = 2.0
        last_err = None
        for attempt in range(self.retries + 1):
            if self.is_canceled():
                raise StreamStatsError("Canceled")
            self.log("GET {} {}".format(url, params))
            try:
                status, body = self.fetch(url, params, self.timeout)
            except Exception as e:  # timeouts, DNS, TLS...
                last_err = TransportError("{}: {}".format(url, e))
            else:
                if status == 404:
                    # pourpoint/ss-delineate answer 404 for unknown regions
                    raise RegionNotSupportedError(_short(body) or "404 Not Found")
                if status == 422:
                    raise StreamStatsError("Request rejected (422): " + _short(body))
                if 500 <= status < 600 or status == 429:
                    last_err = TransportError("HTTP {} from {}: {}".format(
                        status, url, _short(body)))
                elif status != 200:
                    raise StreamStatsError("HTTP {} from {}: {}".format(
                        status, url, _short(body)))
                else:
                    try:
                        return json.loads(body)
                    except ValueError:
                        raise UnexpectedResponseError(
                            "Non-JSON response from {}: {}".format(url, _short(body)))
            if attempt < self.retries:
                time.sleep(delay)
                delay *= 2
        raise last_err

    # ---------------------------------------------------------------- steps
    def find_region(self, lat, lon):
        """Return the 2-letter state/territory code containing (lat, lon), or None.

        Uses the US Census geocoder. Regional studies (CRB, DRB, RRB) are never
        returned here; choose them manually in the plugin settings.
        """
        params = {"x": "{:.6f}".format(lon), "y": "{:.6f}".format(lat),
                  "benchmark": "Public_AR_Current", "vintage": "Current_Current",
                  "layers": "States", "format": "json"}
        try:
            data = self._get_json(CENSUS_URL, params)
        except StreamStatsError:
            return None
        try:
            states = data["result"]["geographies"]["States"]
            return states[0]["STUSAB"] if states else None
        except (KeyError, IndexError, TypeError):
            return None

    def snap(self, region, lat, lon):
        """Snap to the nearest stream-grid cell. Returns (snap_lat, snap_lon)."""
        data = self._get_json(SNAP_URL, {"region": region,
                                         "lat": "{:.6f}".format(lat),
                                         "lon": "{:.6f}".format(lon)})
        if not isinstance(data, dict) or "couldSnap" not in data:
            raise UnexpectedResponseError("Snap response missing 'couldSnap': "
                                          + _short(json.dumps(data)))
        if not data["couldSnap"]:
            raise NotSnappableError(
                "StreamStats could not snap this point to a stream in region {}. "
                "Click closer to a mapped stream (zoom in and turn on a "
                "hydrography layer).".format(region))
        coords = _point_coords(data.get("output"))
        if coords is None:
            raise UnexpectedResponseError("Snap response has no point output: "
                                          + _short(json.dumps(data)))
        lon_s, lat_s = coords
        return lat_s, lon_s

    def delineate(self, region, lat, lon):
        """Delineate the watershed draining to (lat, lon). Returns the raw JSON."""
        return self._get_json(DELINEATE_URL.format(region=region),
                              {"lat": "{:.6f}".format(lat),
                               "lon": "{:.6f}".format(lon)})

    def nldi_comid(self, lat, lon):
        """NHDPlus COMID of the catchment containing (lat, lon)."""
        data = self._get_json(NLDI_POSITION_URL,
                              {"coords": "POINT({:.6f} {:.6f})".format(lon, lat),
                               "f": "json"})
        try:
            props = data["features"][0]["properties"]
        except (KeyError, IndexError, TypeError):
            raise UnexpectedResponseError("NLDI found no NHDPlus catchment at this point.")
        comid = props.get("comid") or props.get("identifier") or props.get("nhdplus_comid")
        if not comid:
            raise UnexpectedResponseError("NLDI position response has no comid.")
        return str(comid)

    def upstream_flowlines(self, lat, lon, area_km2):
        """NHDPlus flowlines upstream (with tributaries) of the outlet.

        Returns (comid, [GeoJSON Feature, ...]). Lines are NOT clipped: the
        outlet reach extends downstream past the outlet, and a too-coarse
        catchment can pick up a neighbouring stream, so the caller clips them
        to the StreamStats watershed polygon.
        """
        comid = self.nldi_comid(lat, lon)
        data = self._get_json(NLDI_UT_URL.format(comid=comid),
                              {"distance": "{:.0f}".format(upstream_distance_km(area_km2)),
                               "f": "json"})
        feats = data.get("features") if isinstance(data, dict) else None
        if feats is None:
            raise UnexpectedResponseError("NLDI flowline response has no features.")
        lines = [f for f in feats if isinstance(f, dict) and
                 (f.get("geometry") or {}).get("type") in ("LineString", "MultiLineString")]
        return comid, lines

    # ---------------------------------------------------------------- pipeline
    def delineate_from_click(self, lat, lon, region=None, snap=True,
                             max_outside_m=100.0, streams=False):
        """
        Full pipeline: region -> snap -> delineate -> validate.

        Returns a dict:
            region, clicked (lat, lon), snapped (lat, lon), snap_distance_m,
            polygon (GeoJSON geometry dict, WGS84), pour_point (lat, lon),
            properties (dict from the watershed feature), huc, raw (full JSON)
        """
        if not region:
            region = self.find_region(lat, lon)
            if not region:
                raise RegionNotSupportedError(
                    "Could not determine the state for this point (is it in the "
                    "US?). Pick a region manually in the plugin settings.")
        region = region.upper()

        if snap:
            s_lat, s_lon = self.snap(region, lat, lon)
        else:
            s_lat, s_lon = lat, lon
        if self.is_canceled():
            raise StreamStatsError("Canceled")

        raw = self.delineate(region, s_lat, s_lon)
        ws = parse_delineation(raw)
        validate_watershed(ws, (s_lat, s_lon), max_outside_m=max_outside_m)

        pp = ws["pour_point"] or (s_lat, s_lon)

        # Streams are a bonus: a failure here never discards the watershed.
        stream_feats, stream_comid, stream_error = [], None, None
        if streams:
            try:
                stream_comid, stream_feats = self.upstream_flowlines(
                    pp[0], pp[1], geojson_area_km2(ws["polygon"]))
            except StreamStatsError as e:
                if str(e) == "Canceled":
                    raise
                stream_error = str(e)
            except Exception as e:
                stream_error = "{}: {}".format(type(e).__name__, e)

        return {
            "streams": stream_feats,
            "stream_outlet_comid": stream_comid,
            "streams_error": stream_error,
            "region": region,
            "clicked": (lat, lon),
            "snapped": (s_lat, s_lon),
            "snap_distance_m": haversine_m(lat, lon, s_lat, s_lon),
            "polygon": ws["polygon"],
            "pour_point": pp,
            "properties": ws["properties"],
            "huc": ws["huc"],
            "raw": raw,
        }


# --------------------------------------------------------------------------- parsing
def parse_delineation(data):
    """Extract the watershed polygon and pour point from an sshydro response.

    Returns dict(polygon, properties, pour_point, huc, warnings).
    Raises UnexpectedResponseError if the structure is not recognised.
    """
    try:
        fc = data["bcrequest"]["wsresp"]["featurecollection"]
    except (KeyError, TypeError):
        # tolerate the legacy layout {"featurecollection": [...]}
        fc = data.get("featurecollection") if isinstance(data, dict) else None
    if not fc:
        raise UnexpectedResponseError(
            "Delineation response has no featurecollection: "
            + _short(json.dumps(data)))

    # flatten the [[{name, feature}, ...]] nesting
    entries = []
    for item in fc:
        if isinstance(item, list):
            entries.extend(item)
        else:
            entries.append(item)

    polygon = props = pour_point = huc = None
    warnings = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").lower()
        feats = _features(entry.get("feature"))
        for f in feats:
            p = f.get("properties") or {}
            w = (p.get("WarningMsg") or "").strip(" ,")
            if w:
                warnings.append(w)
        if name == "globalwatershed":
            chosen = [f for f in feats if (f.get("properties") or {}).get("GlobalWshd") == 1]
            if not chosen and len(feats) == 1:
                chosen = feats
            if len(chosen) != 1:
                raise DegenerateDelineationError(
                    "Expected one global watershed feature, found {}".format(len(chosen)))
            polygon = chosen[0].get("geometry")
            props = dict(chosen[0].get("properties") or {})
        elif name == "globalwatershedpoint" and feats:
            g = feats[0].get("geometry")
            c = _point_coords(g)
            if c:
                pour_point = (c[1], c[0])
            huc = (feats[0].get("properties") or {}).get("HUCID")

    if polygon is None:
        raise DegenerateDelineationError(
            "StreamStats returned no 'globalwatershed' polygon.")

    # de-duplicate warnings while preserving order
    seen = set()
    warnings = [w for w in warnings if not (w in seen or seen.add(w))]
    return {"polygon": polygon, "properties": props or {},
            "pour_point": pour_point, "huc": huc, "warnings": warnings}


def validate_watershed(ws, snapped_latlon, max_outside_m=100.0):
    """Raise DegenerateDelineationError if the parsed watershed is not usable."""
    if ws["warnings"]:
        raise DegenerateDelineationError(
            "StreamStats flagged this delineation: " + "; ".join(ws["warnings"]))

    geom = ws["polygon"]
    gtype = geom.get("type") if isinstance(geom, dict) else None
    if gtype == "Polygon":
        polys = [geom["coordinates"]]
    elif gtype == "MultiPolygon":
        polys = geom["coordinates"]
    else:
        raise DegenerateDelineationError(
            "Watershed geometry is {}, not a polygon.".format(gtype))

    for rings in polys:
        if not rings:
            raise DegenerateDelineationError("Empty polygon in watershed.")
        for ring in rings:
            if len(ring) < 4 or list(ring[0][:2]) != list(ring[-1][:2]):
                raise DegenerateDelineationError("Watershed has an unclosed/degenerate ring.")

    # The outlet lies on the basin boundary by construction (typically 1-2 DEM
    # cells inside). A point far outside means the polygon is for another basin.
    lat, lon = snapped_latlon
    if max_outside_m is not None and not _point_in_multipolygon(lon, lat, polys):
        d = _distance_to_boundary_m(lon, lat, polys)
        if d > max_outside_m:
            raise DegenerateDelineationError(
                "Snapped outlet is {:.0f} m outside the returned watershed.".format(d))


# --------------------------------------------------------------------------- geometry helpers
def geojson_area_km2(geom):
    """Approximate area of a GeoJSON (Multi)Polygon in km², holes subtracted.

    Local equirectangular projection; accurate to a few percent, which is all
    upstream_distance_km needs. The plugin reports ellipsoidal area separately.
    """
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    total = 0.0
    for rings in polys:
        for i, ring in enumerate(rings):
            lat0 = math.radians(sum(c[1] for c in ring) / len(ring))
            kx = 111.320 * math.cos(lat0)
            ky = 110.540
            a = 0.0
            for (x1, y1), (x2, y2) in zip((c[:2] for c in ring), (c[:2] for c in ring[1:])):
                a += (x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky)
            total += abs(a) / 2.0 * (1 if i == 0 else -1)
    return max(total, 0.0)


def upstream_distance_km(area_km2):
    """Navigation distance to request from NLDI for a basin of this area.

    Hack's law (L ≈ 1.4·A^0.6, miles) gives the main-stem length; ×3 for
    safety, clamped to [10, 3000] km. NLDI defaults to 9999 km, which for an
    outlet on a large river can return tens of thousands of flowlines.
    """
    a_mi2 = max(area_km2, 0.0) / 2.589988
    l_km = 1.4 * (a_mi2 ** 0.6) * 1.609344
    return max(10.0, min(3000.0, 3.0 * l_km))


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _point_in_ring(x, y, ring):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-300) + xi:
            inside = not inside
        j = i
    return inside


def _point_in_multipolygon(x, y, polys):
    for rings in polys:
        if _point_in_ring(x, y, rings[0]) and not any(
                _point_in_ring(x, y, h) for h in rings[1:]):
            return True
    return False


def _distance_to_boundary_m(x, y, polys):
    """Approximate min distance (m) from lon/lat point to any ring segment."""
    kx = 111320.0 * math.cos(math.radians(y))
    ky = 110540.0
    best = float("inf")
    for rings in polys:
        for ring in rings:
            for (x1, y1), (x2, y2) in zip((c[:2] for c in ring), (c[:2] for c in ring[1:])):
                ax, ay = (x1 - x) * kx, (y1 - y) * ky
                bx, by = (x2 - x) * kx, (y2 - y) * ky
                dx, dy = bx - ax, by - ay
                L2 = dx * dx + dy * dy
                t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
                px, py = ax + t * dx, ay + t * dy
                best = min(best, math.hypot(px, py))
    return best


def _features(feature):
    if not isinstance(feature, dict):
        return []
    if feature.get("type") == "FeatureCollection":
        return [f for f in feature.get("features") or [] if isinstance(f, dict)]
    if feature.get("type") == "Feature":
        return [feature]
    return []


def _point_coords(obj):
    """Return [lon, lat] from a GeoJSON Point / Feature, or None."""
    if not isinstance(obj, dict):
        return None
    if obj.get("type") == "Feature":
        obj = obj.get("geometry") or {}
    c = obj.get("coordinates")
    if isinstance(c, (list, tuple)) and len(c) >= 2:
        try:
            return float(c[0]), float(c[1])
        except (TypeError, ValueError):
            return None
    # tolerate flat {"lat":..,"lon":..} outputs
    if "lat" in obj and ("lon" in obj or "lng" in obj):
        return float(obj.get("lon", obj.get("lng"))), float(obj["lat"])
    return None


def _short(text, n=300):
    text = text or ""
    return text if len(text) <= n else text[:n] + "..."
