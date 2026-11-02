"""HTTP helpers: use QGIS network stack (honours proxy/auth settings) when
running inside QGIS, plain urllib otherwise.  Responses can be cached."""
import hashlib
import os
import time
import urllib.request

USER_AGENT = "QGIS-DRAINMOD-Climate-Plugin/1.0"


class NetError(Exception):
    pass


def _get_qgis(url, timeout):
    from qgis.core import QgsBlockingNetworkRequest
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtNetwork import QNetworkRequest
    req = QNetworkRequest(QUrl(url))
    req.setRawHeader(b"User-Agent", USER_AGENT.encode())
    try:
        req.setTransferTimeout(int(timeout * 1000))
    except Exception:
        pass
    b = QgsBlockingNetworkRequest()
    err = b.get(req, True)
    if err != QgsBlockingNetworkRequest.ErrorCode.NoError:
        raise NetError("%s\n%s" % (b.errorMessage(), url))
    reply = b.reply()
    status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    if status and int(status) >= 400:
        raise NetError("HTTP %s for %s" % (status, url))
    return bytes(reply.content())


def _get_urllib(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:  # noqa
        raise NetError("%s\n%s" % (e, url))


def http_get(url, timeout=120, retries=3, cache_dir=None, use_qgis=True):
    cache_path = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, hashlib.sha1(url.encode()).hexdigest())
        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
            with open(cache_path, "rb") as f:
                return f.read()
    last = None
    for attempt in range(retries):
        try:
            if use_qgis:
                try:
                    data = _get_qgis(url, timeout)
                except ImportError:
                    data = _get_urllib(url, timeout)
            else:
                data = _get_urllib(url, timeout)
            if cache_path:
                tmp = cache_path + ".part"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, cache_path)
            return data
        except NetError as e:
            last = e
            if "HTTP 404" in str(e):
                break
            time.sleep(2 * (attempt + 1))
    raise last


def download_file(url, dest, progress=None, cancelled=None, chunk=1 << 20):
    """Stream a (possibly multi-GB) file to disk with urllib."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = dest + ".part"
    with urllib.request.urlopen(req, timeout=300) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            while True:
                if cancelled and cancelled():
                    raise NetError("Download cancelled")
                buf = r.read(chunk)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                if progress and total:
                    progress(done, total)
    os.replace(tmp, dest)
    return dest
