"""Minimal CF-calendar time decoding (no cftime / netCDF4 dependency).

Climate models use non-standard calendars:
  standard / gregorian / proleptic_gregorian / julian -> real dates
  noleap / 365_day   -> no Feb 29
  all_leap / 366_day -> Feb 29 every year
  360_day            -> 12 months x 30 days
"""
import datetime as _dt
import math
import re

_UNITS_RE = re.compile(
    r"^\s*(\w+)\s+since\s+(-?\d{1,4})-(\d{1,2})-(\d{1,2})"
    r"(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}(?:\.\d*)?))?)?",
    re.IGNORECASE,
)
_UNIT_SECONDS = {
    "day": 86400.0, "days": 86400.0, "d": 86400.0,
    "hour": 3600.0, "hours": 3600.0, "h": 3600.0, "hr": 3600.0,
    "minute": 60.0, "minutes": 60.0, "min": 60.0,
    "second": 1.0, "seconds": 1.0, "s": 1.0, "sec": 1.0,
}
_MDAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

STANDARD = ("standard", "gregorian", "proleptic_gregorian", "julian", "")


def norm_calendar(cal):
    cal = (cal or "standard").strip().lower()
    if cal in ("365_day",):
        return "noleap"
    if cal in ("366_day",):
        return "all_leap"
    if cal in STANDARD:
        return "standard"
    return cal


def is_leap_real(y):
    return (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)


def days_in_month(y, m, cal="standard"):
    cal = norm_calendar(cal)
    if cal == "360_day":
        return 30
    if m == 2:
        if cal == "noleap":
            return 28
        if cal == "all_leap":
            return 29
        return 29 if is_leap_real(y) else 28
    return _MDAYS[m - 1]


def _year_len(y, cal):
    if cal == "360_day":
        return 360
    if cal == "noleap":
        return 365
    if cal == "all_leap":
        return 366
    return 366 if is_leap_real(y) else 365


def _fixed_ordinal(y, m, d, cal):
    """Days since 0000-01-01 for fixed-length-year calendars."""
    ylen = _year_len(y, cal)
    o = y * ylen
    for mm in range(1, m):
        o += days_in_month(y, mm, cal)
    return o + d - 1


def _fixed_from_ordinal(o, cal):
    ylen = _year_len(2001, cal)
    y, r = divmod(o, ylen)
    m = 1
    while True:
        dim = days_in_month(y, m, cal)
        if r < dim:
            return y, m, r + 1
        r -= dim
        m += 1


def parse_units(units):
    mo = _UNITS_RE.match(units or "")
    if not mo:
        raise ValueError("Unrecognised CF time units: %r" % units)
    unit = mo.group(1).lower()
    if unit not in _UNIT_SECONDS:
        raise ValueError("Unsupported time unit: %r" % unit)
    y, m, d = int(mo.group(2)), int(mo.group(3)), int(mo.group(4))
    hh = int(mo.group(5) or 0)
    mi = int(mo.group(6) or 0)
    ss = float(mo.group(7) or 0)
    frac_day = (hh * 3600 + mi * 60 + ss) / 86400.0
    return _UNIT_SECONDS[unit], (y, m, d), frac_day


def decode_times(values, units, calendar="standard"):
    """Return list of (year, month, day) tuples in the model calendar."""
    scale, (ry, rm, rd), frac = parse_units(units)
    cal = norm_calendar(calendar)
    out = []
    if cal == "standard":
        ref = _dt.date(ry, rm, rd).toordinal()
        for v in values:
            off = math.floor(float(v) * scale / 86400.0 + frac + 1e-6)
            dd = _dt.date.fromordinal(ref + off)
            out.append((dd.year, dd.month, dd.day))
    else:
        ref = _fixed_ordinal(ry, rm, rd, cal)
        for v in values:
            off = math.floor(float(v) * scale / 86400.0 + frac + 1e-6)
            out.append(_fixed_from_ordinal(ref + off, cal))
    return out


def is_valid_real_date(y, m, d):
    return 1 <= m <= 12 and 1 <= d <= days_in_month(y, m, "standard")
