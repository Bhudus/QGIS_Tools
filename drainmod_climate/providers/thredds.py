"""THREDDS catalog browsing and NCSS grid-as-point CSV parsing."""
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

from ..core.net import http_get

NS = {
    "t": "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0",
    "xlink": "http://www.w3.org/1999/xlink",
}
XL = "{http://www.w3.org/1999/xlink}"


def read_catalog(url, **kw):
    """Return (sub_catalog_titles, datasets) for a THREDDS catalog.xml.
    datasets = list of dicts {name, urlPath}."""
    data = http_get(url, timeout=60, **kw)
    root = ET.fromstring(data)
    refs = []
    for cr in root.iter("{%s}catalogRef" % NS["t"]):
        title = cr.get(XL + "title") or cr.get("name") or ""
        href = cr.get(XL + "href") or ""
        refs.append((title, href))
    ds = []
    for d in root.iter("{%s}dataset" % NS["t"]):
        up = d.get("urlPath")
        if up:
            ds.append({"name": d.get("name") or up.rsplit("/", 1)[-1], "urlPath": up})
    return refs, ds


_DATE_RE = re.compile(r"(-?\d{1,4})-(\d{1,2})-(\d{1,2})")
_UNIT_RE = re.compile(r'\[unit="([^"]*)"\]')


def parse_ncss_csv(text, var):
    """Parse NCSS grid-as-point CSV.  Returns dict with dates, values,
    units, cell_lat, cell_lon.  Dates are kept as raw (y,m,d) so that
    360-day 'Feb 30' etc. survive."""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise ValueError("Empty NCSS response")
    if lines[0].lstrip().startswith("<"):
        raise ValueError("Server returned an error page:\n" + text[:500])
    header = [h.strip() for h in lines[0].split(",")]
    names = [h.split("[")[0].strip().lower() for h in header]
    try:
        vi = next(i for i, n in enumerate(names) if n == var.lower())
    except StopIteration:
        vi = len(names) - 1
    mo = _UNIT_RE.search(header[vi])
    units = mo.group(1) if mo else ""
    ti = 0
    for i, n in enumerate(names):
        if n in ("time", "date"):
            ti = i
            break
    lat_i = next((i for i, n in enumerate(names) if n.startswith("lat")), None)
    lon_i = next((i for i, n in enumerate(names) if n.startswith("lon")), None)
    dates, values = [], []
    cell_lat = cell_lon = None
    for l in lines[1:]:
        parts = [p.strip().strip('"') for p in l.split(",")]
        if len(parts) <= vi:
            continue
        m = _DATE_RE.search(parts[ti])
        if not m:
            continue
        try:
            v = float(parts[vi])
            if v != v or abs(v) > 1e19:
                v = None
        except ValueError:
            v = None
        dates.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        values.append(v)
        if cell_lat is None and lat_i is not None:
            try:
                cell_lat = float(parts[lat_i])
                cell_lon = float(parts[lon_i])
            except (ValueError, TypeError, IndexError):
                pass
    return {"dates": dates, "values": values, "units": units,
            "cell_lat": cell_lat, "cell_lon": cell_lon}


def ncss_point_url(base, url_path, var, lat, lon, time_start=None, time_end=None):
    q = [("var", var), ("latitude", "%.5f" % lat), ("longitude", "%.5f" % lon)]
    if time_start and time_end:
        q += [("time_start", time_start), ("time_end", time_end)]
    else:
        q += [("temporal", "all")]
    q += [("accept", "csv")]
    return "%s/ncss/grid/%s?%s" % (base.rstrip("/"), url_path.lstrip("/"), urlencode(q))
