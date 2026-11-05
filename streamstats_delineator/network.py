# -*- coding: utf-8 -*-
"""HTTP GET through QGIS's network stack, so the user's proxy, SSL and
authentication settings (Settings > Options > Network) apply.

QgsBlockingNetworkRequest is designed to be used from a QgsTask worker thread.
"""

from qgis.core import QgsBlockingNetworkRequest
from qgis.PyQt.QtCore import QUrl, QUrlQuery
from qgis.PyQt.QtNetwork import QNetworkRequest

from .client import USER_AGENT
from .compat import scoped


def qgis_fetch(url, params, timeout):
    qurl = QUrl(url)
    if params:
        query = QUrlQuery()
        for k, v in params.items():
            query.addQueryItem(str(k), str(v))
        qurl.setQuery(query)

    request = QNetworkRequest(qurl)
    request.setRawHeader(b"User-Agent", USER_AGENT.encode())
    request.setRawHeader(b"Accept", b"application/json")
    if hasattr(request, "setTransferTimeout"):  # Qt >= 5.15
        request.setTransferTimeout(int(timeout * 1000))

    blocking = QgsBlockingNetworkRequest()
    err = blocking.get(request, True)  # forceRefresh: never serve from cache
    reply = blocking.reply()

    status = reply.attribute(scoped(QNetworkRequest, "Attribute",
                                    "HttpStatusCodeAttribute"))
    body = bytes(reply.content()).decode("utf-8", "replace")

    no_error = scoped(QgsBlockingNetworkRequest, "ErrorCode", "NoError")
    if status is None:
        # no HTTP response at all: DNS, TLS, timeout, proxy...
        raise IOError(blocking.errorMessage() or "network error ({})".format(err))
    if err != no_error and not body:
        body = blocking.errorMessage()
    return int(status), body
