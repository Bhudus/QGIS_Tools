"""HTTP helpers: use QGIS network stack (honours proxy/auth settings) when
running inside QGIS, plain urllib otherwise.  Responses can be cached."""
import hashlib
import os
import time
import urllib.request

USER_AGENT = "QGIS-DRAINMOD-Climate-Plugin/1.0"


class NetError(Exception):
    pass


class NoResponse(NetError):
    """QGIS got no HTTP answer at all (SSL/TLS handshake, proxy, DNS ...)."""


# Set once QGIS networking has failed without any HTTP answer; later requests
# then go straight to Python's HTTPS client instead of failing twice each.
_use_urllib = False


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
    reply = b.reply()
    status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    if err != QgsBlockingNetworkRequest.ErrorCode.NoError:
        if not status:
            raise NoResponse("%s\n%s" % (b.errorMessage(), url))
        raise NetError("HTTP %s: %s\n%s" % (status, b.errorMessage(), url))
    if status and int(status) >= 400:
        raise NetError("HTTP %s for %s" % (status, url))
    return bytes(reply.content())


def _get_urllib(url, timeout):
    """Python's own HTTPS client: a different TLS library from QGIS/Qt, using
    the Windows certificate store on Windows."""
    import ssl
    import urllib.error
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl.create_default_context()) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise NetError("HTTP %s for %s" % (e.code, url))
    except Exception as e:  # noqa
        raise NetError("%s\n%s" % (e, url))


NET_HINT = ("Check that the data server opens in your web browser. If it does, a "
            "VPN, firewall or antivirus program may be blocking secure connections "
            "from QGIS: try again off the VPN, or ask IT to allow the server.")


def _get(url, timeout, use_qgis):
    global _use_urllib
    if not use_qgis or _use_urllib:
        return _get_urllib(url, timeout)
    try:
        return _get_qgis(url, timeout)
    except ImportError:
        return _get_urllib(url, timeout)
    except NoResponse as qe:
        # e.g. "SSL handshake failed": retry with Python's HTTPS client.
        try:
            data = _get_urllib(url, timeout)
        except NetError as pe:
            if str(pe).startswith("HTTP "):
                raise
            raise NetError("%s\n(Retry without QGIS networking also failed: %s)\n\n%s"
                           % (qe, str(pe).split("\n")[0], NET_HINT))
        _use_urllib = True
        return data


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
            data = _get(url, timeout, use_qgis)
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
