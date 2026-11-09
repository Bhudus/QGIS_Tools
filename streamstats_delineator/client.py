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

# USGS WaterData GeoServer: NHDPlus V2 flowline topology and catchment polygons
# (one catchment per flowline; catchmentsp.featureid == flowline comid).
WFS_URL = "https://api.water.usgs.gov/geoserver/wmadata/ows"
FLOWLINE_TYPE = "wmadata:nhdflowline_network"
CATCHMENT_TYPE = "wmadata:catchmentsp"
WFS_CHUNK = 100                # COMIDs per WFS request
MAX_SUBBASIN_COMIDS = 6000     # ~15,000-20,000 km² of NHDPlus catchments
DEFAULT_MAX_SUBBASINS = 20     # target for automatic split order

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

    # ---------------------------------------------------------------- NHDPlus WFS
    def _wfs_by_ids(self, type_name, id_field, ids, properties):
        """GeoJSON features of ``type_name`` whose ``id_field`` is in ``ids``.

        Uses WFS 1.0.0 (always lon/lat axis order) with an ECQL ``IN`` filter,
        in chunks so URLs stay short and no server-side paging is needed.
        """
        out = []
        ids = list(ids)
        for i in range(0, len(ids), WFS_CHUNK):
            if self.is_canceled():
                raise StreamStatsError("Canceled")
            chunk = ids[i:i + WFS_CHUNK]
            params = {
                "service": "WFS", "version": "1.0.0", "request": "GetFeature",
                "typeName": type_name, "outputFormat": "application/json",
                "propertyName": ",".join(properties),
                "CQL_FILTER": "{} IN ({})".format(id_field, ",".join(str(int(c)) for c in chunk)),
            }
            data = self._get_json(WFS_URL, params)
            feats = data.get("features") if isinstance(data, dict) else None
            if feats is None:
                raise UnexpectedResponseError(
                    "WFS {} response has no features: {}".format(
                        type_name, _short(json.dumps(data))))
            out.extend(f for f in feats if isinstance(f, dict))
        return out

    def network_attributes(self, comids):
        """{comid: {hydroseq, dnhydroseq, order, name}} from NHDPlus V2."""
        feats = self._wfs_by_ids(FLOWLINE_TYPE, "comid", comids,
                                 ["comid", "hydroseq", "dnhydroseq", "streamorde",
                                  "gnis_name"])
        net = {}
        for f in feats:
            p = f.get("properties") or {}
            try:
                c = str(int(p["comid"]))
                net[c] = {"hydroseq": int(p["hydroseq"]),
                          "dnhydroseq": int(p.get("dnhydroseq") or 0),
                          "order": int(p.get("streamorde") or 0),
                          "name": (p.get("gnis_name") or "").strip()}
            except (KeyError, TypeError, ValueError):
                continue
        return net

    def catchments(self, comids):
        """{comid: GeoJSON (Multi)Polygon geometry} of NHDPlus V2 catchments."""
        feats = self._wfs_by_ids(CATCHMENT_TYPE, "featureid", comids,
                                 ["featureid", "the_geom"])
        out = {}
        for f in feats:
            p = f.get("properties") or {}
            g = f.get("geometry")
            if not g or g.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            try:
                out[str(int(p["featureid"]))] = _ensure_lonlat(g)
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def subbasins(self, outlet_comid, comids, split_order=None,
                  max_subbasins=DEFAULT_MAX_SUBBASINS):
        """Group the NHDPlus catchments above ``outlet_comid`` into sub-basins.

        Returns dict(split_order, subbasins=[...] (see build_subbasins),
        network={comid: attrs}, catchments={comid: geometry}).
        """
        comids = list(dict.fromkeys([str(outlet_comid)] + [str(c) for c in comids]))
        if len(comids) > MAX_SUBBASIN_COMIDS:
            raise StreamStatsError(
                "Watershed too large for sub-basins ({:,} NHDPlus reaches; limit "
                "{:,}).".format(len(comids), MAX_SUBBASIN_COMIDS))
        net = self.network_attributes(comids)
        if str(outlet_comid) not in net:
            raise UnexpectedResponseError(
                "Outlet reach {} not found in the NHDPlus network.".format(outlet_comid))
        if split_order is None:
            split_order = choose_split_order(net, str(outlet_comid), max_subbasins)
        groups = build_subbasins(net, str(outlet_comid), split_order)
        cats = self.catchments(list(net))
        return {"split_order": split_order, "subbasins": groups,
                "network": net, "catchments": cats}

    # ---------------------------------------------------------------- pipeline
    def delineate_from_click(self, lat, lon, region=None, snap=True,
                             max_outside_m=100.0, streams=False, subbasins=False,
                             split_order=None, max_subbasins=DEFAULT_MAX_SUBBASINS):
        """
        Full pipeline: region -> snap -> delineate -> validate
        [-> NLDI upstream flowlines] [-> NHDPlus sub-basins].

        Returns a dict:
            region, clicked (lat, lon), snapped (lat, lon), snap_distance_m,
            polygon (GeoJSON geometry dict, WGS84), pour_point (lat, lon),
            properties (dict from the watershed feature), huc, raw (full JSON),
            streams / stream_outlet_comid / streams_error,
            subbasins (see ``subbasins()``, or None) / subbasins_error
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

        # Streams and sub-basins are extras: a failure in either never
        # discards the watershed; it is reported in *_error instead.
        stream_feats, stream_comid, stream_error = [], None, None
        sb, sb_error = None, None
        if streams or subbasins:
            try:
                stream_comid, stream_feats = self.upstream_flowlines(
                    pp[0], pp[1], geojson_area_km2(ws["polygon"]))
            except Exception as e:
                stream_error = _extra_error(e)
                sb_error = stream_error
        if subbasins and stream_comid is not None:
            try:
                comids = [_comid_of(f) for f in stream_feats]
                sb = self.subbasins(stream_comid, [c for c in comids if c],
                                    split_order=split_order,
                                    max_subbasins=max_subbasins)
            except Exception as e:
                sb_error = _extra_error(e)
        if not streams:
            stream_feats, stream_error = [], None

        return {
            "subbasins": sb,
            "subbasins_error": sb_error,
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


# --------------------------------------------------------------------------- sub-basins
def build_subbasins(net, outlet, split_order):
    """Group NHDPlus reaches into sub-basins split at stream confluences.

    ``net`` is {comid: {"hydroseq", "dnhydroseq", "order", "name"}} for every
    reach upstream of (and including) ``outlet``. A reach is the bottom of a
    sub-basin when it is the watershed outlet, or when it has Strahler order
    >= ``split_order`` and meets another such reach at its downstream end.
    Every reach belongs to the first sub-basin bottom found by walking
    downstream from it, so each sub-basin is one link of the order >=
    ``split_order`` network plus all the smaller streams draining to it.

    Returns a list ordered outlet-first (upstream breadth-first) of dicts:
        sb_id, ds_sb_id (None at the outlet), outlet_comid, comids,
        max_order, name
    """
    outlet = str(outlet)
    hs2c = {a["hydroseq"]: c for c, a in net.items()}
    major = [c for c, a in net.items() if a["order"] >= split_order]
    inflows = {}
    for c in major:
        dn = net[c]["dnhydroseq"]
        inflows[dn] = inflows.get(dn, 0) + 1

    heads = set()
    for c, a in net.items():
        dn = a["dnhydroseq"]
        if c == outlet or dn not in hs2c:
            heads.add(c)  # watershed outlet, or a piece cut off from the network
        elif a["order"] >= split_order and inflows.get(dn, 0) >= 2:
            heads.add(c)

    owner = {h: h for h in heads}
    for c in net:
        path, cur = [], c
        while cur not in owner:
            path.append(cur)
            if len(path) > len(net):
                raise UnexpectedResponseError("Loop in NHDPlus network topology.")
            cur = hs2c[net[cur]["dnhydroseq"]]
        for p in path:
            owner[p] = owner[cur]

    members = {h: [] for h in heads}
    for c, h in owner.items():
        members[h].append(c)

    def downstream_head(h):
        if h == outlet:
            return None
        dn = net[h]["dnhydroseq"]
        return owner[hs2c[dn]] if dn in hs2c else None

    ds = {h: downstream_head(h) for h in heads}
    children = {}
    for h, d in ds.items():
        if d is not None:
            children.setdefault(d, []).append(h)

    order, queue = [], [outlet] if outlet in heads else []
    while queue:
        h = queue.pop(0)
        order.append(h)
        queue.extend(sorted(children.get(h, []), key=lambda x: net[x]["hydroseq"]))
    order += sorted((h for h in heads if h not in set(order)),
                    key=lambda x: net[x]["hydroseq"])
    ids = {h: i + 1 for i, h in enumerate(order)}

    out = []
    for h in order:
        comids = sorted(members[h], key=lambda x: net[x]["hydroseq"])
        max_order = max(net[c]["order"] for c in comids)
        name = net[h]["name"] or next(
            (net[c]["name"] for c in comids
             if net[c]["name"] and net[c]["order"] == max_order), "")
        out.append({"sb_id": ids[h], "ds_sb_id": ids.get(ds[h]),
                    "outlet_comid": h, "comids": comids,
                    "max_order": max_order, "name": name})
    return out


def choose_split_order(net, outlet, max_subbasins=DEFAULT_MAX_SUBBASINS):
    """Smallest Strahler order whose split gives at most ``max_subbasins``."""
    top = max((a["order"] for a in net.values()), default=1)
    for n in range(1, max(top, 1) + 1):
        if len(build_subbasins(net, outlet, n)) <= max_subbasins:
            return n
    return max(top, 1)


def _comid_of(feature):
    p = feature.get("properties") or {}
    c = p.get("nhdplus_comid") or p.get("comid") or p.get("identifier")
    try:
        return str(int(c))
    except (TypeError, ValueError):
        return None


def _extra_error(e):
    """Message for an optional step that failed; re-raise cancellation."""
    if isinstance(e, StreamStatsError):
        if str(e) == "Canceled":
            raise e
        return str(e)
    return "{}: {}".format(type(e).__name__, e)


def _ensure_lonlat(geom):
    """Swap axes if a WFS answered in lat/lon order (US data: lon < 0 or |lon| > 90)."""
    c = geom["coordinates"]
    while isinstance(c[0], list):
        c = c[0]
    x, y = c[0], c[1]
    if not ((abs(y) > 90 >= abs(x)) or (x > 0 > y)):
        return geom

    def swap(obj):
        if obj and isinstance(obj[0], (int, float)):
            return [obj[1], obj[0]] + list(obj[2:])
        return [swap(o) for o in obj]
    return {"type": geom["type"], "coordinates": swap(geom["coordinates"])}


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
