"""Queries against the USDA-NRCS Soil Data Access (SDA) web service."""
from . import http

SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"


def _query(sql):
    resp = http.post_json(SDA_URL, {"query": sql, "format": "JSON+COLUMNNAME"})
    table = resp.get("Table") if isinstance(resp, dict) else None
    if not table:
        return []
    cols = table[0]
    return [dict(zip(cols, row)) for row in table[1:]]


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe(s):
    """Keys used in SQL come from SDA itself, but sanitise anyway."""
    s = str(s)
    if not s.isdigit():
        raise ValueError("Invalid key: %r" % s)
    return s


def mapunit_at(lon, lat):
    """Return dict(mukey, muname, musym, areasymbol) for the map unit at a
    WGS84 point, or None if the point is outside SSURGO coverage."""
    sql = (
        "SELECT mu.mukey, mu.muname, mu.musym, l.areasymbol "
        "FROM mapunit mu "
        "INNER JOIN legend l ON l.lkey = mu.lkey "
        "WHERE mu.mukey IN (SELECT * FROM "
        "SDA_Get_Mukey_from_intersection_with_WktWgs84('point(%.7f %.7f)'))"
        % (float(lon), float(lat)))
    rows = _query(sql)
    return rows[0] if rows else None


def components(mukey):
    """All components of a map unit, largest first."""
    sql = (
        "SELECT cokey, compname, comppct_r, majcompflag, compkind, "
        "drainagecl, hydgrp, taxclname "
        "FROM component WHERE mukey = '%s' "
        "ORDER BY comppct_r DESC, majcompflag DESC" % _safe(mukey))
    rows = _query(sql)
    for r in rows:
        r["comppct_r"] = _f(r.get("comppct_r")) or 0.0
    return rows


def horizons(cokey):
    """Horizon data for one component, ordered top to bottom."""
    sql = (
        "SELECT ch.chkey, ch.hzname, ch.hzdept_r, ch.hzdepb_r, "
        "ch.sandtotal_r, ch.silttotal_r, ch.claytotal_r, ch.om_r, "
        "ch.dbthirdbar_r, ch.ksat_r, ch.wsatiated_r, ch.wthirdbar_r, "
        "ch.wfifteenbar_r, "
        "(SELECT TOP 1 tg.texdesc FROM chtexturegrp tg "
        " WHERE tg.chkey = ch.chkey AND tg.rvindicator = 'Yes') AS texdesc "
        "FROM chorizon ch WHERE ch.cokey = '%s' "
        "ORDER BY ch.hzdept_r" % _safe(cokey))
    out = []
    for r in _query(sql):
        out.append({
            "hzname": r.get("hzname") or "",
            "top": _f(r.get("hzdept_r")),
            "bottom": _f(r.get("hzdepb_r")),
            "sand": _f(r.get("sandtotal_r")),
            "silt": _f(r.get("silttotal_r")),
            "clay": _f(r.get("claytotal_r")),
            "om": _f(r.get("om_r")),
            "db": _f(r.get("dbthirdbar_r")),
            # SSURGO ksat is micrometres/second; 1 um/s = 0.36 cm/hr
            "ksat_cmhr": (_f(r.get("ksat_r")) * 0.36
                          if _f(r.get("ksat_r")) is not None else None),
            "wsat": _f(r.get("wsatiated_r")),
            "w33": _f(r.get("wthirdbar_r")),
            "w1500": _f(r.get("wfifteenbar_r")),
            "texdesc": r.get("texdesc") or "",
        })
    return out


def default_component(comps):
    """Largest major component that is a soil (not a miscellaneous area)."""
    for c in comps:
        if (c.get("majcompflag") == "Yes"
                and (c.get("compkind") or "").lower() != "miscellaneous area"):
            return c
    return comps[0] if comps else None
