"""NASA NEX-GDDP-CMIP6 via the NCCS THREDDS server (point extraction with
the NetCDF Subset Service, one small CSV request per variable-year).

Layout: AMES/NEX/GDDP-CMIP6/<model>/<scenario>/<member>/<var>/
        <var>_day_<model>_<scenario>_<member>_<grid>_<year>[_v1.1].nc
"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..core.net import http_get
from .thredds import read_catalog, parse_ncss_csv, ncss_point_url

THREDDS = "https://ds.nccs.nasa.gov/thredds"
ROOT = "AMES/NEX/GDDP-CMIP6"
SCENARIOS = ["historical", "ssp126", "ssp245", "ssp370", "ssp585"]
MODELS = [
    "ACCESS-CM2", "ACCESS-ESM1-5", "BCC-CSM2-MR", "CanESM5", "CESM2",
    "CESM2-WACCM", "CMCC-CM2-SR5", "CMCC-ESM2", "CNRM-CM6-1", "CNRM-ESM2-1",
    "EC-Earth3", "EC-Earth3-Veg-LR", "FGOALS-g3", "GFDL-CM4", "GFDL-CM4_gr2",
    "GFDL-ESM4", "GISS-E2-1-G", "HadGEM3-GC31-LL", "HadGEM3-GC31-MM",
    "IITM-ESM", "INM-CM4-8", "INM-CM5-0", "IPSL-CM6A-LR", "KACE-1-0-G",
    "KIOST-ESM", "MIROC-ES2L", "MIROC6", "MPI-ESM1-2-HR", "MPI-ESM1-2-LR",
    "MRI-ESM2-0", "NESM3", "NorESM2-LM", "NorESM2-MM", "TaiESM1", "UKESM1-0-LL",
]
_FILE_RE = re.compile(r"_(\d{4})(?:_v(\d+(?:\.\d+)*))?\.nc$")


def fetch_year(base, url_path, var, lat, lon, year, cache_dir=None):
    """One NCSS grid-as-point request for a yearly file.  Tries an explicit
    calendar-year range first; falls back for 360-day calendars (no Dec 31)
    and for servers that need `temporal=all`."""
    variants = [
        ("%d-01-01T00:00:00Z" % year, "%d-12-31T23:59:59Z" % year),
        (None, None),  # temporal=all
        ("%d-01-01T00:00:00Z" % year, "%d-12-30T23:59:59Z" % year),
    ]
    last_err, best = None, None
    for ts, te in variants:
        url = ncss_point_url(base, url_path, var, lat, lon, ts, te)
        try:
            txt = http_get(url, cache_dir=cache_dir, retries=2).decode("utf-8", "replace")
            res = parse_ncss_csv(txt, var)
        except Exception as e:  # noqa
            last_err = e
            continue
        if len(res["dates"]) >= 300:
            return res
        if best is None or len(res["dates"]) > len(best["dates"]):
            best = res
    if best is not None and best["dates"]:
        return best
    raise last_err or ValueError("No data for %s %d" % (var, year))


class NexGddpProvider(object):
    name = "NASA NEX-GDDP-CMIP6 (NCCS THREDDS)"

    def __init__(self, base=THREDDS, cache_dir=None, workers=4):
        self.base = base.rstrip("/")
        self.cache_dir = cache_dir
        self.workers = workers

    def _cat(self, path, cache=True):
        url = "%s/catalog/%s/catalog.xml" % (self.base, path.strip("/"))
        return read_catalog(url, cache_dir=self.cache_dir if cache else None)

    def list_models(self):
        refs, _ = self._cat(ROOT, cache=False)
        return sorted(t for t, _ in refs if t)

    def list_scenarios(self, model):
        refs, _ = self._cat("%s/%s" % (ROOT, model))
        return [t for t, _ in refs]

    def pick_member(self, model, scenario):
        refs, _ = self._cat("%s/%s/%s" % (ROOT, model, scenario))
        members = [t for t, _ in refs]
        if not members:
            raise ValueError("No ensemble member for %s %s" % (model, scenario))
        return "r1i1p1f1" if "r1i1p1f1" in members else sorted(members)[0]

    def year_files(self, model, scenario, member, var):
        _, ds = self._cat("%s/%s/%s/%s/%s" % (ROOT, model, scenario, member, var))
        best = {}
        for d in ds:
            m = _FILE_RE.search(d["name"])
            if not m:
                continue
            y = int(m.group(1))
            ver = tuple(int(x) for x in (m.group(2) or "0").split("."))
            if y not in best or ver > best[y][0]:
                best[y] = (ver, d["urlPath"])
        return {y: v[1] for y, v in best.items()}

    def fetch(self, model, scenario, lat, lon, y0, y1, log=None,
              progress=None, cancelled=None):
        """Return raw dict {var: {dates, values, units}} plus info."""
        member = self.pick_member(model, scenario)
        lon360 = lon % 360.0
        jobs = []
        for var in ("pr", "tasmax", "tasmin"):
            files = self.year_files(model, scenario, member, var)
            for y in range(y0, y1 + 1):
                if y in files:
                    jobs.append((var, y, files[y]))
        if not jobs:
            raise ValueError("%s/%s has no files for %d-%d" % (model, scenario, y0, y1))
        if log:
            log("%s %s %s: %d requests" % (model, scenario, member, len(jobs)))
        parts = {}
        info = {"member": member}
        done = [0]

        def work(job):
            return job, fetch_year(self.base, job[2], job[0], lat, lon360,
                                   job[1], self.cache_dir)

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = [ex.submit(work, j) for j in jobs]
            for fu in as_completed(futs):
                if cancelled and cancelled():
                    for f in futs:
                        f.cancel()
                    raise RuntimeError("Cancelled")
                (var, y, _), res = fu.result()
                parts[(var, y)] = res
                if res.get("cell_lat") is not None:
                    info["cell_lat"], info["cell_lon"] = res["cell_lat"], res["cell_lon"]
                done[0] += 1
                if progress:
                    progress(done[0] / float(len(jobs)))
        raw = {}
        for var in ("pr", "tasmax", "tasmin"):
            dates, vals, units = [], [], ""
            for y in range(y0, y1 + 1):
                r = parts.get((var, y))
                if r:
                    dates += r["dates"]
                    vals += r["values"]
                    units = units or r["units"]
            raw[var] = {"dates": dates, "values": vals, "units": units}
        if info.get("cell_lon") is not None and info["cell_lon"] > 180:
            info["cell_lon"] -= 360.0
        return raw, info
