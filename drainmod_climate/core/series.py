"""Assemble daily point series (pr, tasmax, tasmin) on a real calendar.

Input per variable: list of (y, m, d, value) in the *model* calendar
(dates may be 'impossible', e.g. Feb 30 for 360_day models).
Output: continuous list of real dates with pr [mm/day], tmax/tmin [degC].
"""
import datetime as _dt
from collections import defaultdict

from .cfcal import is_leap_real, is_valid_real_date, days_in_month

VARS = ("pr", "tasmax", "tasmin")


# ---------------------------------------------------------------- units
def pr_to_mm_day(values, units):
    u = (units or "").strip().lower().replace("**", "").replace("^", "")
    if "s-1" in u or "/s" in u or "s^-1" in u or "kg m-2 s" in u:
        f = 86400.0
    elif u in ("m", "m/day", "m d-1", "m day-1"):
        f = 1000.0
    elif u in ("in", "inch", "inches", "in/day"):
        f = 25.4
    elif u in ("cm", "cm/day"):
        f = 10.0
    else:  # mm, mm/day, mm d-1, kg m-2 (daily total) ...
        f = 1.0
    return [None if v is None else v * f for v in values]


def t_to_degc(values, units):
    u = (units or "").strip().lower()
    if u in ("k", "kelvin", "degk", "deg_k"):
        off, fac = -273.15, 1.0
    elif u in ("degf", "f", "fahrenheit", "deg_f"):
        return [None if v is None else (v - 32.0) * 5.0 / 9.0 for v in values]
    elif u in ("", "unknown"):
        # guess from magnitude
        good = [v for v in values if v is not None]
        off = -273.15 if good and sum(good) / len(good) > 150 else 0.0
        fac = 1.0
    else:  # degC, C, celsius, degrees_C
        off, fac = 0.0, 1.0
    return [None if v is None else v * fac + off for v in values]


# ---------------------------------------------------------------- calendar
def detect_calendar(dates):
    """Guess the model calendar from a list of (y, m, d)."""
    per_year = defaultdict(int)
    has_feb29_nonleap = False
    has_invalid = False
    leap_years_with_feb29 = set()
    for y, m, d in dates:
        per_year[y] += 1
        if not is_valid_real_date(y, m, d):
            has_invalid = True
        if m == 2 and d == 29:
            if is_leap_real(y):
                leap_years_with_feb29.add(y)
            else:
                has_feb29_nonleap = True
    full = [n for n in per_year.values()]
    if has_invalid or (full and max(full) == 360):
        return "360_day"
    if has_feb29_nonleap:
        return "all_leap"
    leap_years = [y for y, n in per_year.items() if is_leap_real(y) and n >= 360]
    if leap_years and not any(y in leap_years_with_feb29 for y in leap_years):
        return "noleap"
    return "standard"


def to_real_calendar(records, calendar, is_precip, leap_mode="feb29"):
    """Map {(y,m,d): value} in model calendar to {date: value} real calendar.

    360_day: the 360 model days of each year are spread evenly over the
    365/366 real days (gaps are filled later).  all_leap: Feb 29 of
    non-leap years is dropped (its rain is added to Feb 28).
    """
    out = {}
    if calendar == "360_day":
        by_year = defaultdict(list)
        for (y, m, d), v in records.items():
            by_year[y].append(((m, d), v))
        for y, items in by_year.items():
            items.sort()
            ndays = 366 if is_leap_real(y) else 365
            start = _dt.date(y, 1, 1)
            for (m, d), v in items:
                idx = (m - 1) * 30 + (d - 1)  # 0..359
                real = int(round(idx * (ndays - 1) / 359.0))  # Jan 1..Dec 31
                out[start + _dt.timedelta(days=real)] = v
        return out
    if calendar == "noleap" and leap_mode == "dec31":
        # Daymet style: model day-of-year N -> real day-of-year N; in leap
        # years Dec 31 (day 366) is left empty and filled later.
        for (y, m, d), v in records.items():
            if m == 2 and d == 29:
                continue
            doy = sum(days_in_month(y, mm, "noleap") for mm in range(1, m)) + d
            out[_dt.date(y, 1, 1) + _dt.timedelta(days=doy - 1)] = v
        return out
    for (y, m, d), v in records.items():
        if is_valid_real_date(y, m, d):
            out[_dt.date(y, m, d)] = v
        elif m == 2 and d == 29 and is_precip and v is not None:
            k = _dt.date(y, 2, 28)
            out[k] = (out.get(k) or 0.0) + v
    return out


class DailySeries(object):
    """Daily real-calendar series with gap filling and bookkeeping."""

    def __init__(self):
        self.dates = []
        self.pr = []      # mm/day
        self.tmax = []    # degC
        self.tmin = []    # degC
        self.notes = []
        self.calendar = "standard"
        self.filled = {"pr": 0, "tasmax": 0, "tasmin": 0}
        self.swapped_t = 0

    @classmethod
    def build(cls, raw, start_year=None, end_year=None, leap_mode="feb29"):
        """raw: {var: {'dates': [(y,m,d)], 'values': [...], 'units': str}}

        leap_mode (no-leap model calendars only):
          'feb29' - insert Feb 29 with 0 rain, Tmax/Tmin interpolated
          'dec31' - Daymet style: keep the 365 model days in order and add
                    Dec 31 (day 366) with 0 rain and Dec 30 temperatures
        """
        s = cls()
        conv = {}
        cals = {}
        for var in VARS:
            if var not in raw:
                raise ValueError("Missing variable %s" % var)
            r = raw[var]
            if var == "pr":
                vals = pr_to_mm_day(r["values"], r.get("units"))
            else:
                vals = t_to_degc(r["values"], r.get("units"))
            cal = r.get("calendar") or detect_calendar(r["dates"])
            from .cfcal import norm_calendar
            cal = norm_calendar(cal)
            cals[var] = cal
            recs = {}
            for k, v in zip(r["dates"], vals):
                recs[tuple(k)] = v
            conv[var] = to_real_calendar(recs, cal, var == "pr", leap_mode)
        s.calendar = cals["pr"]
        dec31 = s.calendar == "noleap" and leap_mode == "dec31"
        if dec31:
            s.notes.append("Model calendar 'noleap': the 365 model days are kept in "
                           "order; in leap years Dec 31 (day 366) was added with 0 rain "
                           "and Dec 30 temperatures.")
        elif s.calendar != "standard":
            s.notes.append("Model calendar '%s' converted to the real (Gregorian) "
                           "calendar; inserted days get 0 rain and interpolated "
                           "temperatures." % s.calendar)
        common = set(conv["pr"]) | set(conv["tasmax"]) | set(conv["tasmin"])
        if not common:
            raise ValueError("No data returned for this location/period.")
        first, last = min(common), max(common)
        if dec31 and last.month == 12 and last.day == 30 and is_leap_real(last.year):
            last = _dt.date(last.year, 12, 31)
        if start_year:
            first = max(first, _dt.date(int(start_year), 1, 1))
        if end_year:
            last = min(last, _dt.date(int(end_year), 12, 31))
        # trim to whole years so DRAINMOD gets complete years
        if first.month != 1 or first.day != 1:
            first = _dt.date(first.year + 1, 1, 1)
            s.notes.append("Partial first year dropped.")
        if last.month != 12 or last.day != 31:
            last = _dt.date(last.year - 1, 12, 31)
            s.notes.append("Partial last year dropped.")
        if last < first:
            raise ValueError("Less than one complete year of data available.")
        n = (last - first).days + 1
        s.dates = [first + _dt.timedelta(days=i) for i in range(n)]
        s.pr = [conv["pr"].get(d) for d in s.dates]
        s.tmax = [conv["tasmax"].get(d) for d in s.dates]
        s.tmin = [conv["tasmin"].get(d) for d in s.dates]
        if dec31:
            for i, d in enumerate(s.dates):
                if d.month == 12 and d.day == 31 and is_leap_real(d.year) and i > 0:
                    for name, arr in (("tasmax", s.tmax), ("tasmin", s.tmin)):
                        if arr[i] is None:
                            arr[i] = arr[i - 1]
                            s.filled[name] += 1
        s._fill()
        return s

    def _fill(self):
        for i, v in enumerate(self.pr):
            if v is None or v != v:
                self.pr[i] = 0.0
                self.filled["pr"] += 1
            elif v < 0:
                self.pr[i] = 0.0
        for name, arr in (("tasmax", self.tmax), ("tasmin", self.tmin)):
            self.filled[name] += _interp_gaps(arr)
        for i in range(len(self.dates)):
            if self.tmin[i] > self.tmax[i]:
                self.tmin[i], self.tmax[i] = self.tmax[i], self.tmin[i]
                self.swapped_t += 1
        for k, v in self.filled.items():
            if v:
                self.notes.append("%d missing %s day(s) filled." % (v, k))

    @property
    def years(self):
        return (self.dates[0].year, self.dates[-1].year) if self.dates else (None, None)


def _interp_gaps(arr):
    n = len(arr)
    bad = [i for i, v in enumerate(arr) if v is None or v != v]
    if not bad:
        return 0
    good = [i for i in range(n) if not (arr[i] is None or arr[i] != arr[i])]
    if not good:
        raise ValueError("Temperature series is entirely missing.")
    import bisect
    for i in bad:
        j = bisect.bisect_left(good, i)
        if j == 0:
            arr[i] = arr[good[0]]
        elif j >= len(good):
            arr[i] = arr[good[-1]]
        else:
            a, b = good[j - 1], good[j]
            w = (i - a) / float(b - a)
            arr[i] = arr[a] * (1 - w) + arr[b] * w
    return len(bad)
