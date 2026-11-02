"""LOCA2 (CMIP6, 1/16 deg, North America) helper.

LOCA2 daily files are served as plain files (no THREDDS subsetting) from
the author's server, by region:
  https://cirrus.ucsd.edu/~pierce/LOCA2/CONUS_regions_split/<model>/<region>/0p0625deg/<member>/...
This module crawls those directory listings, finds the pr/tasmax/tasmin
files for a scenario, downloads them (they are large), and the local
NetCDF reader then extracts the point series.
"""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from ..core.net import http_get

BASE = "https://cirrus.ucsd.edu/~pierce/LOCA2/CONUS_regions_split/"
MODELS = [
    "ACCESS-CM2", "ACCESS-ESM1-5", "AWI-CM-1-1-MR", "BCC-CSM2-MR", "CESM2-LENS",
    "CNRM-CM6-1", "CNRM-CM6-1-HR", "CNRM-ESM2-1", "CanESM5", "EC-Earth3",
    "EC-Earth3-Veg", "FGOALS-g3", "GFDL-CM4", "GFDL-ESM4", "HadGEM3-GC31-LL",
    "HadGEM3-GC31-MM", "INM-CM4-8", "INM-CM5-0", "IPSL-CM6A-LR", "KACE-1-0-G",
    "MIROC6", "MPI-ESM1-2-HR", "MPI-ESM1-2-LR", "MRI-ESM2-0", "NorESM2-LM",
    "NorESM2-MM", "TaiESM1",
]
SCENARIOS = ["historical", "ssp245", "ssp370", "ssp585"]
# NOAA climate regions used to split the CONUS domain
REGIONS = {
    "cent": "Central (IL IN KY MO OH TN WV)",
    "e_n_cent": "East North Central (IA MI MN WI)",
    "n_east": "Northeast (CT DE ME MD MA NH NJ NY PA RI VT)",
    "n_west": "Northwest (ID OR WA)",
    "s_east": "Southeast (AL FL GA NC SC VA)",
    "s_west": "Southwest (AZ CO NM UT)",
    "south": "South (AR KS LA MS OK TX)",
    "w_n_cent": "West North Central (MT NE ND SD WY)",
    "west": "West (CA NV)",
    "west105": "west105 (larger western split; check server)",
}


class _Links(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.rows = []
        self._href = None
        self._row_text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
        if tag == "tr":
            self._row_text = []

    def handle_data(self, data):
        self._row_text.append(data)
        if self._href:
            self.rows.append([self._href, ""])
            self._href = None

    def handle_endtag(self, tag):
        if tag == "tr" and self.rows:
            self.rows[-1][1] = " ".join(self._row_text)


_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)([KMG])\s*$")


def list_dir(url):
    """Return (subdir_urls, [(file_url, size_text)]) for an Apache index."""
    html = http_get(url, timeout=60).decode("utf-8", "replace")
    p = _Links()
    p.feed(html)
    dirs, files = [], []
    for href, rowtxt in p.rows:
        if not href or href.startswith(("?", "/", "#", "..", "mailto:")) or "://" in href:
            continue
        full = urljoin(url, href)
        if href.endswith("/"):
            dirs.append(full)
        elif href.endswith(".nc"):
            m = _SIZE_RE.search(rowtxt.strip())
            files.append((full, m.group(0) if m else ""))
    return dirs, files


def list_members(model, region):
    dirs, _ = list_dir("%s%s/%s/0p0625deg/" % (BASE, model, region))
    return [d.rstrip("/").rsplit("/", 1)[-1] for d in dirs]


def find_files(model, region, member, scenario, max_depth=4, log=None):
    """Crawl below the member folder and return {var: [(url, size)]}."""
    root = "%s%s/%s/0p0625deg/%s/" % (BASE, model, region, member)
    found = {"pr": [], "tasmax": [], "tasmin": []}
    stack = [(root, 0)]
    seen = set()
    while stack:
        url, depth = stack.pop()
        if url in seen:
            continue
        seen.add(url)
        try:
            dirs, files = list_dir(url)
        except Exception as e:  # noqa
            if log:
                log("Cannot list %s: %s" % (url, e))
            continue
        for f, size in files:
            path = f[len(root):]
            toks = set(re.split(r"[./_]", path))
            if scenario not in toks:
                continue
            for v in found:
                if v in toks:
                    found[v].append((f, size))
        if depth < max_depth:
            for d in dirs:
                seg = d[len(root):].strip("/").split("/")[-1]
                # prune other scenarios / variables early
                if seg in SCENARIOS and seg != scenario:
                    continue
                if seg in ("hurs", "huss", "rsds", "rhmax", "rhmin", "uas", "vas", "wspeed"):
                    continue
                stack.append((d, depth + 1))
    for v in found:
        found[v].sort()
    return found
