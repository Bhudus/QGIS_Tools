"""Write DRAINMOD weather files for one site / model / scenario.

Output folder <out>/<site>/<model>_<scenario>_<y0>-<y1>/ contains:
  <station>.RAI      DRAINMOD hourly rainfall (hundredths of an inch)
  <station>.TEM      DRAINMOD daily Tmax/Tmin (deg F)
  <station>.rai.wea  year,day,precip_mm          (no header)
  <station>.tem.wea  year,day,tmax_C,tmin_C      (no header)
  <station>.PET      DRAINMOD daily PET, hundredths of an inch (optional)
  <station>.pet.wea  year,day,pet_mm  Hargreaves (optional)
  <station>_daily.csv, README.txt

.RAI/.TEM use drainmod_native (byte-identical to DRAINMOD's weather
conversion utility with metric input): values are rounded to 0.01 mm and
0.1 deg C first, exactly as the utility would read them from .wea files.
"""
import io
import os
import re

from .pet import hargreaves
from . import drainmod_native as dm
from .drainmod_pet import pet_lines


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(s)).strip("_") or "site"


def _doy(d):
    return d.timetuple().tm_yday


def write_all(series, out_dir, station, site, write_pet=True, write_csv=True, meta=None):
    """Returns list of written paths."""
    os.makedirs(out_dir, exist_ok=True)
    sid = station[:6]
    paths = []
    rain = [(d.year, _doy(d), round(float(p), 2)) for d, p in zip(series.dates, series.pr)]
    temp = [(d.year, _doy(d), round(float(hi), 1), round(float(lo), 1))
            for d, hi, lo in zip(series.dates, series.tmax, series.tmin)]

    p = os.path.join(out_dir, sid + ".RAI")
    dm.write_lines(p, dm.rai_lines(rain, sid))
    paths.append(p)
    p = os.path.join(out_dir, sid + ".TEM")
    dm.write_lines(p, dm.tem_lines(temp, sid))
    paths.append(p)

    p = os.path.join(out_dir, sid + ".rai.wea")
    dm.write_lines(p, ["%d,%d,%.2f" % r for r in rain])
    paths.append(p)
    p = os.path.join(out_dir, sid + ".tem.wea")
    dm.write_lines(p, ["%d,%d,%.1f,%.1f" % r for r in temp])
    paths.append(p)

    pet = None
    if write_pet:
        pet = hargreaves(site["lat"], series.dates, series.tmax, series.tmin)
        prow = [(d.year, _doy(d), round(float(v), 2)) for d, v in zip(series.dates, pet)]
        p = os.path.join(out_dir, sid + ".PET")
        dm.write_lines(p, pet_lines(prow, sid))
        paths.append(p)
        p = os.path.join(out_dir, sid + ".pet.wea")
        dm.write_lines(p, ["%d,%d,%.2f" % r for r in prow])
        paths.append(p)

    if write_csv:
        p = os.path.join(out_dir, sid + "_daily.csv")
        with io.open(p, "w", newline="") as f:
            f.write("date,year,doy,pr_mm,tmax_c,tmin_c%s\n" % (",pet_mm" if pet else ""))
            for i, d in enumerate(series.dates):
                row = "%s,%d,%d,%.3f,%.2f,%.2f" % (
                    d.isoformat(), d.year, _doy(d), series.pr[i],
                    series.tmax[i], series.tmin[i])
                if pet:
                    row += ",%.3f" % pet[i]
                f.write(row + "\n")
        paths.append(p)

    p = os.path.join(out_dir, "README.txt")
    y0, y1 = series.years
    nyr = y1 - y0 + 1
    tot = sum(series.pr) / nyr
    with io.open(p, "w") as f:
        f.write("DRAINMOD climate-scenario weather files\n" + "=" * 40 + "\n")
        for k, v in (meta or {}).items():
            f.write("%-22s %s\n" % (k + ":", v))
        f.write("%-22s %s\n" % ("Station ID:", sid))
        f.write("%-22s %s (lat %.4f, lon %.4f)\n" % ("Site:", site["name"], site["lat"], site["lon"]))
        f.write("%-22s %d-%d (%d years)\n" % ("Period:", y0, y1, nyr))
        f.write("%-22s %.0f mm (%.1f in)\n" % ("Mean annual precip:", tot, tot / 25.4))
        f.write("%-22s %.1f C / %.1f C\n" % ("Mean Tmax / Tmin:",
                sum(series.tmax) / len(series.tmax), sum(series.tmin) / len(series.tmin)))
        if series.notes:
            f.write("\nNotes:\n")
            for n in series.notes:
                f.write("  - %s\n" % n)
        f.write(
            "\nFiles:\n"
            "  %s.RAI      DRAINMOD hourly rainfall, hundredths of an inch\n"
            "              (each rain day spread over hours 17-20)\n"
            "  %s.TEM      DRAINMOD daily Tmax/Tmin, deg F\n"
            "  %s.rai.wea  year,day,precip_mm\n"
            "  %s.tem.wea  year,day,tmax_C,tmin_C\n"
            "  %s.PET      DRAINMOD daily PET, hundredths of an inch (Hargreaves;\n"
            "              optional - DRAINMOD can instead compute Thornthwaite PET\n"
            "              from the .TEM file)\n"
            "  %s.pet.wea  year,day,PET_mm\n"
            % (sid, sid, sid, sid, sid, sid))
    paths.append(p)
    return paths
