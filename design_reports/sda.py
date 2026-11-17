"""Soil Data Access client: soil map units with drainage class and crop yields.

Requests go through QGIS's network stack, so the proxy and SSL settings in QGIS
Options apply.
"""
import json

SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/tabular/post.rest"


class ServiceError(Exception):
    pass


def _enum(owner, name, scope):
    value = getattr(owner, name, None)
    if value is None:
        value = getattr(getattr(owner, scope), name)
    return value


def _request(url, body=None, feedback=None, content_type=None):
    from qgis.core import QgsBlockingNetworkRequest
    from qgis.PyQt.QtCore import QByteArray, QUrl
    from qgis.PyQt.QtNetwork import QNetworkRequest

    req = QNetworkRequest(QUrl(url))
    if content_type:
        req.setHeader(_enum(QNetworkRequest, "ContentTypeHeader", "KnownHeaders"),
                      content_type)
    br = QgsBlockingNetworkRequest()
    if body is None:
        err = br.get(req, True, feedback)
    else:
        err = br.post(req, QByteArray(body), True, feedback)
    data = bytes(br.reply().content().data())
    if err != _enum(QgsBlockingNetworkRequest, "NoError", "ErrorCode"):
        detail = data.decode("utf-8", "replace").strip()[:400] or br.errorMessage()
        raise ServiceError(detail or "no response")
    return data


def _yield(crop_clause):
    return ("(select sum(y.nonirryield_r * c.comppct_r) / nullif(sum(c.comppct_r), 0) "
            "from component c join cocropyld y on y.cokey = c.cokey "
            "where c.mukey = t.id and y.nonirryield_r is not null and " + crop_clause + ")")


def sda_soils(wkt, feedback=None):
    """Map units clipped to a WGS 84 polygon: mukey, musym, muname, drainage class, and
    the component-weighted non-irrigated yields (bu/ac) of corn, soybeans and wheat."""
    sql = f"""
~DeclareGeometry(@aoi)~
select @aoi = geometry::STGeomFromText('{wkt}', 4326).MakeValid()
~DeclareIdGeomTable(@outTable)~
~GetClippedMapunits(@aoi,polygon,geo,@outTable)~
select t.id as mukey, mu.musym, mu.muname, ma.drclassdcd,
  {_yield("y.cropname = 'Corn'")} as corn,
  {_yield("y.cropname = 'Soybeans'")} as soy,
  {_yield("y.cropname like '%wheat%'")} as grain,
  t.geom.STAsText() as wkt
from @outTable t
join mapunit mu on mu.mukey = t.id
left join muaggatt ma on ma.mukey = t.id
"""
    body = json.dumps({"query": sql, "format": "JSON+COLUMNNAME"}).encode("utf-8")
    data = _request(SDA_URL, body, feedback, "application/json")
    text = data.decode("utf-8", "replace")
    if not text.strip():
        return []
    try:
        js = json.loads(text)
    except ValueError:
        raise ServiceError("Soil Data Access returned: " + text[:300])
    table = js.get("Table") if isinstance(js, dict) else None
    if not table:
        return []
    header = table[0]
    return [dict(zip(header, row)) for row in table[1:]]
