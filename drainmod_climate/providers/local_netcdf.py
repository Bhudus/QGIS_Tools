"""Extract a daily point series from local NetCDF files (LOCA2 downloads,
GDO-DCP subsets, NEX-GDDP files from AWS, CORDEX, ...).

Uses the netCDF4 module when present (fast), otherwise GDAL, which ships
with every QGIS install.
"""
import os
import re

from ..core.cfcal import decode_times

VAR_ALIASES = {
    "pr": ("pr", "precip", "prcp", "precipitation", "rain", "ppt"),
    "tasmax": ("tasmax", "tmax", "tx", "tmaxd", "max_temp"),
    "tasmin": ("tasmin", "tmin", "tn", "tmind", "min_temp"),
}
_LAT = ("lat", "latitude", "y", "rlat")
_LON = ("lon", "longitude", "x", "rlon")


def guess_var(names, target):
    low = {n.lower(): n for n in names}
    for a in VAR_ALIASES[target]:
        if a in low:
            return low[a]
    return None


def guess_var_from_filename(path):
    base = os.path.basename(path).lower()
    toks = re.split(r"[._\-]", base)
    for target, al in VAR_ALIASES.items():
        if any(a in toks for a in al):
            return target
    return None


# ------------------------------------------------------------ netCDF4 path
def _read_netcdf4(path, var, lat, lon):
    import netCDF4  # noqa
    import numpy as np
    with netCDF4.Dataset(path) as nc:
        if var is None:
            raise KeyError
        v = nc.variables[var]
        v.set_auto_maskandscale(True)
        dims = list(v.dimensions)
        latn = next(d for d in dims if d.lower() in _LAT)
        lonn = next(d for d in dims if d.lower() in _LON)
        tn = next(d for d in dims if d.lower().startswith("time"))
        lats = nc.variables[latn][:]
        lons = nc.variables[lonn][:]
        plon = lon % 360.0 if float(np.nanmax(lons)) > 180 else lon
        iy = int(np.abs(lats - lat).argmin())
        ix = int(np.abs(lons - plon).argmin())
        idx = []
        for d in dims:
            if d == latn:
                idx.append(iy)
            elif d == lonn:
                idx.append(ix)
            elif d == tn:
                idx.append(slice(None))
            else:
                idx.append(0)
        data = np.ma.filled(v[tuple(idx)].astype("f8"), np.nan)
        if np.all(np.isnan(data)):
            # nearest valid cell (coastal points)
            for r in (1, 2, 3):
                found = False
                for dy in range(-r, r + 1):
                    for dx in range(-r, r + 1):
                        jy, jx = iy + dy, ix + dx
                        if 0 <= jy < len(lats) and 0 <= jx < len(lons):
                            idx2 = [jy if d == latn else jx if d == lonn else i
                                    for d, i in zip(dims, idx)]
                            d2 = np.ma.filled(v[tuple(idx2)].astype("f8"), np.nan)
                            if not np.all(np.isnan(d2)):
                                data, iy, ix, found = d2, jy, jx, True
                                break
                    if found:
                        break
                if found:
                    break
        t = nc.variables[tn]
        cal = getattr(t, "calendar", "standard")
        dates = decode_times(t[:], t.units, cal)
        vals = [None if x != x else float(x) for x in data]
        cl = float(lons[ix])
        return {"dates": dates, "values": vals,
                "units": getattr(v, "units", ""), "calendar": cal,
                "cell_lat": float(lats[iy]), "cell_lon": cl - 360 if cl > 180 else cl}


# ------------------------------------------------------------ GDAL path
def _md_list(s):
    return [float(x) for x in s.strip("{} ").split(",") if x.strip()]


def _read_gdal(path, var, lat, lon):
    from osgeo import gdal
    gdal.UseExceptions()
    if var is None:
        ds0 = gdal.Open(path)
        subs = [s[0] for s in ds0.GetSubDatasets()]
        raise ValueError("Choose a variable; subdatasets: %s" % subs)
    ds = gdal.Open('NETCDF:"%s":%s' % (path, var))
    md = ds.GetMetadata()
    units = md.get("%s#units" % var, "")
    tunits = md.get("time#units")
    cal = md.get("time#calendar", "standard")
    gt = ds.GetGeoTransform()
    xmax = gt[0] + ds.RasterXSize * gt[1]
    plon = lon % 360.0 if xmax > 180.5 else lon
    col = int((plon - gt[0]) / gt[1])
    row = int((lat - gt[3]) / gt[5])
    if not (0 <= col < ds.RasterXSize and 0 <= row < ds.RasterYSize):
        raise ValueError("Point (%.4f, %.4f) is outside %s" % (lat, lon, os.path.basename(path)))
    nb = ds.RasterCount
    b1 = ds.GetRasterBand(1)
    nodata = b1.GetNoDataValue()
    scale = b1.GetScale() or 1.0
    offset = b1.GetOffset() or 0.0
    tvals = md.get("NETCDF_DIM_time_VALUES")
    tvals = _md_list(tvals) if tvals else []
    keep = list(range(nb))
    if len(tvals) != nb:  # extra dims (e.g. GDO-DCP 'projection'): use band metadata
        tvals, keep = [], []
        for i in range(1, nb + 1):
            bmd = ds.GetRasterBand(i).GetMetadata()
            others = [k for k in bmd if k.startswith("NETCDF_DIM_") and k != "NETCDF_DIM_time"]
            if all(float(bmd[k]) == float(md.get(k + "_VALUES", "{%s}" % bmd[k]).strip("{}").split(",")[0])
                   for k in others):
                tvals.append(float(bmd["NETCDF_DIM_time"]))
                keep.append(i - 1)

    def read_at(c, r):
        arr = ds.ReadAsArray(c, r, 1, 1)
        flat = arr.reshape(-1) if hasattr(arr, "reshape") else [arr]
        out = []
        for i in keep:
            x = float(flat[i])
            if x != x or (nodata is not None and abs(x - nodata) <= abs(nodata) * 1e-6) or abs(x) > 1e19:
                out.append(None)
            else:
                out.append(x * scale + offset)
        return out

    vals = read_at(col, row)
    if all(v is None for v in vals):
        for r in (1, 2):
            cand = [(col + dx, row + dy) for dy in range(-r, r + 1) for dx in range(-r, r + 1)]
            for c, rr in cand:
                if 0 <= c < ds.RasterXSize and 0 <= rr < ds.RasterYSize:
                    v2 = read_at(c, rr)
                    if not all(v is None for v in v2):
                        vals, col, row = v2, c, rr
                        break
            else:
                continue
            break
    dates = decode_times(tvals, tunits, cal)
    clon = gt[0] + (col + 0.5) * gt[1]
    return {"dates": dates, "values": vals, "units": units, "calendar": cal,
            "cell_lat": gt[3] + (row + 0.5) * gt[5],
            "cell_lon": clon - 360 if clon > 180 else clon}


def list_variables(path):
    try:
        import netCDF4
        with netCDF4.Dataset(path) as nc:
            return [k for k, v in nc.variables.items() if len(v.dimensions) >= 3]
    except ImportError:
        from osgeo import gdal
        ds = gdal.Open(path)
        subs = ds.GetSubDatasets() if ds else []
        if subs:
            return [s[0].rsplit(":", 1)[-1] for s in subs]
        md = ds.GetMetadata() if ds else {}
        return [k.split("#")[0] for k in md if k.endswith("#units")
                and not k.split("#")[0] in ("time", "lat", "lon")][:1]


def read_point(path, var, lat, lon):
    if var is None:
        names = list_variables(path)
        tgt = guess_var_from_filename(path)
        var = (guess_var(names, tgt) if tgt else None) or (names[0] if len(names) == 1 else None)
    try:
        import netCDF4  # noqa
        return _read_netcdf4(path, var, lat, lon)
    except ImportError:
        return _read_gdal(path, var, lat, lon)


def merge(parts):
    """Concatenate several period files of the same variable."""
    rows = {}
    units, cal, cl = "", None, (None, None)
    for p in parts:
        for d, v in zip(p["dates"], p["values"]):
            rows[tuple(d)] = v
        units = units or p["units"]
        cal = cal or p.get("calendar")
        cl = (p.get("cell_lat"), p.get("cell_lon"))
    keys = sorted(rows)
    return {"dates": keys, "values": [rows[k] for k in keys], "units": units,
            "calendar": cal, "cell_lat": cl[0], "cell_lon": cl[1]}


def fetch_local(files_by_var, lat, lon, varnames=None, log=None):
    """files_by_var: {'pr': [paths], 'tasmax': [...], 'tasmin': [...]}"""
    raw, info = {}, {}
    for target in ("pr", "tasmax", "tasmin"):
        paths = files_by_var.get(target) or []
        if not paths:
            raise ValueError("No file assigned to %s" % target)
        parts = []
        for p in paths:
            names = list_variables(p)
            v = (varnames or {}).get(target) or guess_var(names, target)
            if v is None and len(names) == 1:
                v = names[0]
            if log:
                log("Reading %s [%s]" % (os.path.basename(p), v))
            parts.append(read_point(p, v, lat, lon))
        raw[target] = merge(parts)
        info["cell_lat"], info["cell_lon"] = raw[target]["cell_lat"], raw[target]["cell_lon"]
    return raw, info


def classify_files(paths):
    """Group local files by (model, scenario) and variable using CMIP-style
    names, e.g. pr.ACCESS-CM2.ssp245.r1i1p1f1.2015-2044.LOCA_16thdeg_v20240915.nc
    or pr_day_ACCESS-CM2_ssp245_r1i1p1f1_gn_2050.nc"""
    groups = {}
    for p in paths:
        base = os.path.basename(p)
        toks = re.split(r"[._]", base)
        var = guess_var_from_filename(p) or "?"
        scen = next((t for t in toks if re.match(r"^(ssp\d{3}|historical|rcp\d{2})$", t, re.I)), "unknown")
        model = "model"
        if scen in toks:
            i = toks.index(scen)
            if i > 0:
                prev = toks[i - 1]
                model = prev if prev.lower() not in ("day", "pr", "tasmax", "tasmin") else "model"
        groups.setdefault((model, scen), {}).setdefault(var, []).append(p)
    for g in groups.values():
        for v in g:
            g[v].sort()
    return groups
