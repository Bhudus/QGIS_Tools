"""Any THREDDS server with the NetCDF Subset Service, described by a URL
template.  Placeholders: {var} {model} {scenario} {year}.  Without {year}
a single request for the whole period is made.

Example (one file per variable/year):
  https://myserver/thredds/ncss/grid/loca2/{model}/{scenario}/{var}_{year}.nc
"""
from urllib.parse import urlencode

from ..core.net import http_get
from .thredds import parse_ncss_csv


def build_url(template, var, model, scenario, lat, lon, year=None, y0=None, y1=None):
    base = template.format(var=var, model=model, scenario=scenario,
                           year=year if year is not None else "")
    q = [("var", var), ("latitude", "%.5f" % lat), ("longitude", "%.5f" % lon)]
    if year is not None:
        q += [("time_start", "%d-01-01T00:00:00Z" % year), ("time_end", "%d-12-31T23:59:59Z" % year)]
    else:
        q += [("time_start", "%d-01-01T00:00:00Z" % y0), ("time_end", "%d-12-31T23:59:59Z" % y1)]
    q.append(("accept", "csv"))
    return base + ("&" if "?" in base else "?") + urlencode(q)


def fetch(template, varnames, model, scenario, lat, lon, y0, y1,
          cache_dir=None, progress=None, cancelled=None, log=None):
    per_year = "{year}" in template
    raw, info = {}, {}
    targets = ("pr", "tasmax", "tasmin")
    total = len(targets) * ((y1 - y0 + 1) if per_year else 1)
    done = 0
    for t in targets:
        v = varnames.get(t, t)
        dates, vals, units = [], [], ""
        years = range(y0, y1 + 1) if per_year else [None]
        for y in years:
            if cancelled and cancelled():
                raise RuntimeError("Cancelled")
            url = build_url(template, v, model, scenario, lat, lon, y, y0, y1)
            try:
                txt = http_get(url, cache_dir=cache_dir).decode("utf-8", "replace")
                r = parse_ncss_csv(txt, v)
                dates += r["dates"]
                vals += r["values"]
                units = units or r["units"]
                if r.get("cell_lat") is not None:
                    info["cell_lat"], info["cell_lon"] = r["cell_lat"], r["cell_lon"]
            except Exception as e:  # noqa
                if log:
                    log("Skipped %s: %s" % (url, e))
            done += 1
            if progress:
                progress(done / float(total))
        raw[t] = {"dates": dates, "values": vals, "units": units}
    return raw, info
