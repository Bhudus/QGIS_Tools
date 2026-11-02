"""Write DRAINMOD daily potential evapotranspiration (.PET) files.

Layout (matches DRAINMOD's weather conversion utility, metric input, checked
byte for byte against a utility-made file):
  daily PET in hundredths of an inch, Fortran NINT rounding,
  two lines per month: station(A6) 1X year(I4) month(I2) 3X then the first
  14 days as I4; second line the remaining days as I4.  CRLF line ends.
"""
from collections import OrderedDict

from .drainmod_native import _nint, _month_day


def pet_lines(rows, station):
    """rows: (year, yday, pet_mm) with PET already rounded to 2 decimals."""
    by_month = OrderedDict()
    for y, j, p in rows:
        m, _ = _month_day(y, j)
        by_month.setdefault((y, m), []).append(_nint(p / 25.4 * 100))
    out = []
    for (y, m), vals in by_month.items():
        out.append(f"{station:>6} {y:4d}{m:2d}   " + "".join(f"{v:4d}" for v in vals[:14]))
        out.append("".join(f"{v:4d}" for v in vals[14:]))
    return out
