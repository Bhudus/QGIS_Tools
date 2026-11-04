"""Tiny HTTP helper.  Uses urllib by default; the QGIS plugin replaces
`post_json` with a QgsBlockingNetworkRequest version so that QGIS proxy and
SSL settings are respected."""
import json
import urllib.request

TIMEOUT = 60


def _urllib_post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json",
                                 "User-Agent": "drainmod-soi-builder"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = r.read().decode("utf-8")
    return json.loads(body) if body.strip() else {}


post_json = _urllib_post_json


def set_post_json(func):
    global post_json
    post_json = func
