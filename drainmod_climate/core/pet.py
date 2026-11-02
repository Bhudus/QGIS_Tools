"""Hargreaves-Samani (1985) reference ET from Tmax/Tmin and latitude.

DRAINMOD can compute Thornthwaite PET internally from the .TEM file, so
this file is optional.  Hargreaves responds to the diurnal temperature
range and is often preferred for climate-change runs.
"""
import math


def extraterrestrial_radiation_mm(lat_deg, doy, ndays=365):
    """FAO-56 eq. 21, converted to mm/day of evaporation equivalent."""
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / ndays)
    delta = 0.409 * math.sin(2 * math.pi * doy / ndays - 1.39)
    x = -math.tan(phi) * math.tan(delta)
    x = max(-1.0, min(1.0, x))
    ws = math.acos(x)
    gsc = 0.0820  # MJ m-2 min-1
    ra = (24 * 60 / math.pi) * gsc * dr * (
        ws * math.sin(phi) * math.sin(delta)
        + math.cos(phi) * math.cos(delta) * math.sin(ws))
    return max(ra, 0.0) * 0.408  # MJ m-2 d-1 -> mm/d


def hargreaves(lat_deg, dates, tmax, tmin):
    out = []
    for d, tx, tn in zip(dates, tmax, tmin):
        doy = d.timetuple().tm_yday
        nd = 366 if (d.year % 4 == 0 and (d.year % 100 != 0 or d.year % 400 == 0)) else 365
        ra = extraterrestrial_radiation_mm(lat_deg, doy, nd)
        tmean = (tx + tn) / 2.0
        dtr = max(tx - tn, 0.0)
        out.append(max(0.0023 * ra * (tmean + 17.8) * math.sqrt(dtr), 0.0))
    return out
