"""Write DRAINMOD rainfall (.RAI) and temperature (.TEM) files.

Reproduces the output of DRAINMOD's weather conversion utility (metric input:
mm and deg C), checked byte-for-byte against files it produced.

RAI: hourly rainfall in hundredths of an inch. Each day with rain is spread
     over hours 17-20: three equal parts, with the remainder in hour 20.
     Records: station(A6) 1X year(I4) month(I2) 1X, then up to 3 days per line,
     each hour as day(I2) hour(I2) amount(I4).
TEM: daily Tmax/Tmin in whole deg F, two lines per month: header + first 14
     days, then the remaining days, each value I3.
Lines end in CRLF.
"""
import math
import struct
from collections import OrderedDict
from datetime import date, timedelta

_F18 = struct.unpack("f", struct.pack("f", 1.8))[0]  # single-precision 1.8, as DRAINMOD uses
RAIN_HOURS = (17, 18, 19, 20)


def _nint(x):
    """Fortran NINT: round half away from zero."""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def _f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


def _month_day(year, yday):
    d = date(year, 1, 1) + timedelta(days=yday - 1)
    return d.month, d.day


def default_station_id(lat, lon):
    """e.g. 40.2435, -88.0334 -> '402880' (lat and |lon| truncated to 0.1 deg)."""
    a = math.floor(abs(lat) * 10 + 1e-9) % 1000
    b = math.floor(abs(lon) * 10 + 1e-9) % 1000
    return f"{a:03d}{b:03d}"


def rai_lines(rows, station):
    """rows: (year, yday, prcp_mm) with prcp already rounded to 2 decimals."""
    by_month = OrderedDict()
    for y, j, p in rows:
        if p <= 0:
            continue
        m, d = _month_day(y, j)
        total = _nint(p / 25.4 * 100)
        base = total // 4
        by_month.setdefault((y, m), []).append((d, (base, base, base, total - 3 * base)))
    out = []
    for (y, m), days in by_month.items():
        for k in range(0, len(days), 3):
            s = f"{station:>6} {y:4d}{m:2d} "
            for d, vals in days[k:k + 3]:
                for h, v in zip(RAIN_HOURS, vals):
                    s += f"{d:2d}{h:2d}{v:4d}"
            out.append(s)
    return out


def _c2f(c):
    return _nint(_f32(c) * _F18 + 32)


def tem_lines(rows, station):
    """rows: (year, yday, tmax_c, tmin_c) with temps already rounded to 1 decimal."""
    by_month = OrderedDict()
    for y, j, hi, lo in rows:
        m, _ = _month_day(y, j)
        by_month.setdefault((y, m), []).append((_c2f(hi), _c2f(lo)))
    out = []
    for (y, m), vals in by_month.items():
        out.append(f"{station:>6} {y:4d}{m:2d}     "
                   + "".join(f"{a:3d}{b:3d}" for a, b in vals[:14]))
        out.append("".join(f"{a:3d}{b:3d}" for a, b in vals[14:]))
    return out


def write_lines(path, lines):
    with open(path, "w", newline="", encoding="ascii") as f:
        f.write("\r\n".join(lines) + "\r\n")
