"""Small client for USDA-NRCS Soil Data Access (SDA), the database behind Web Soil Survey."""
import json

SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/tabular/post.rest"


class SdaError(Exception):
    pass


def _enum(owner, name, scope):
    """Return an enum member that may be unscoped (Qt5) or scoped (Qt6)."""
    value = getattr(owner, name, None)
    if value is None:
        value = getattr(getattr(owner, scope), name)
    return value


def run_query(sql, feedback=None):
    """POST a query to SDA and return a list of dicts, one per row."""
    body = json.dumps({"query": sql, "format": "JSON+COLUMNNAME"}).encode("utf-8")
    content = _post_qgis(body, feedback)
    if not content.strip():
        return []
    try:
        data = json.loads(content)
    except ValueError:
        raise SdaError("Soil Data Access returned an unexpected response:\n" + content[:500])
    table = data.get("Table") if isinstance(data, dict) else None
    if not table:
        return []
    header = table[0]
    return [dict(zip(header, row)) for row in table[1:]]


def _post_qgis(body, feedback):
    """Use QGIS's network stack so proxy and SSL settings from QGIS Options apply."""
    from qgis.core import QgsBlockingNetworkRequest
    from qgis.PyQt.QtCore import QByteArray, QUrl
    from qgis.PyQt.QtNetwork import QNetworkRequest

    req = QNetworkRequest(QUrl(SDA_URL))
    req.setHeader(_enum(QNetworkRequest, "ContentTypeHeader", "KnownHeaders"), "application/json")
    blocking = QgsBlockingNetworkRequest()
    err = blocking.post(req, QByteArray(body), True, feedback)
    reply = blocking.reply()
    content = bytes(reply.content().data()).decode("utf-8", "replace")
    if err != _enum(QgsBlockingNetworkRequest, "NoError", "ErrorCode"):
        detail = content.strip()[:800] or blocking.errorMessage()
        raise SdaError("Soil Data Access request failed: " + detail)
    return content


def clipped_mapunits_sql(wkt):
    """SQL that clips SSURGO map unit polygons to a WGS84 polygon and joins
    map unit and dominant-component attributes."""
    return f"""
~DeclareGeometry(@aoi)~
select @aoi = geometry::STGeomFromText('{wkt}', 4326).MakeValid()
~DeclareIdGeomTable(@outTable)~
~GetClippedMapunits(@aoi,polygon,geo,@outTable)~
select t.id as mukey, mu.musym, mu.muname,
  c.cokey, c.compname, c.drainagecl, c.hydgrp,
  l.areasymbol,
  t.geom.STAsText() as wkt
from @outTable t
join mapunit mu on mu.mukey = t.id
join legend l on l.lkey = mu.lkey
outer apply (select top 1 c2.* from component c2
             where c2.mukey = t.id order by c2.comppct_r desc, c2.cokey) c
"""


# Restrictive layers that are a change in texture, not a barrier to water.
_NOT_BARRIERS = ("Abrupt textural change", "Strongly contrasting textural stratification")


def horizons_sql(cokeys):
    """Horizon depths (cm) and saturated hydraulic conductivity (um/s) for components."""
    keys = ",".join(str(int(k)) for k in cokeys)
    return f"""
select cokey, hzdept_r, hzdepb_r, ksat_r
from chorizon
where cokey in ({keys})
order by cokey, hzdept_r
"""


def restrictions_sql(cokeys):
    """Depth (cm) to the shallowest restrictive layer that limits water movement."""
    keys = ",".join(str(int(k)) for k in cokeys)
    not_barriers = ",".join("'" + k + "'" for k in _NOT_BARRIERS)
    return f"""
select cokey, min(resdept_r) as resdept_r
from corestrictions
where cokey in ({keys}) and resdept_r is not null
  and (reskind is null or reskind not in ({not_barriers}))
group by cokey
"""
