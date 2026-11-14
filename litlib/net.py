"""HTTP for lit: per-host rate limit, retries with backoff, on-disk cache.

No keys, no names, no emails: every request carries only a generic User-Agent.
"""
import hashlib
import ipaddress
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "lit/0.1 (research tool)"
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "lit")
CACHE_TTL = 24 * 3600

# Minimum seconds between requests to one host.
HOST_INTERVAL = {
    "export.arxiv.org": 3.0,
    "api.semanticscholar.org": 1.2,
    "api.openalex.org": 0.2,
    "api.crossref.org": 0.3,
    "api2.openreview.net": 0.5,
    "openreview.net": 1.0,
    "www.lesswrong.com": 0.5,
    "www.alignmentforum.org": 0.5,
    "arxiv.org": 1.0,
    "ar5iv.labs.arxiv.org": 1.0,
}
DEFAULT_INTERVAL = 0.5

_host_locks = {}
_host_last = {}
_glock = threading.Lock()
QUIET = False


class NetError(Exception):
    """A network failure, with a short message naming the source."""

    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


def warn(msg):
    if not QUIET:
        print("lit: " + msg, file=sys.stderr)


NOT_PUBLIC = "skipped: not a public address"


def _public_ip(text):
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_loopback
                                 or ip.is_link_local or ip.is_unspecified)


def host_state(url):
    """Say where a URL's host points: "public", "private" or "unresolved".

    The name is resolved here, and it counts as public only when every address
    it maps to is a public one. Loopback, private-range, link-local, shared,
    reserved and multicast addresses are all "private", and so is a URL that
    is not http(s) or names no host.
    """
    try:
        parts = urllib.parse.urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        return "private"
    if parts.scheme not in ("http", "https") or not host:
        return "private"
    try:
        infos = socket.getaddrinfo(host, port or (443 if parts.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return "unresolved"
    addrs = {i[4][0] for i in infos}
    if not addrs:
        return "unresolved"
    return "public" if all(_public_ip(a) for a in addrs) else "private"


def public_only(url):
    """A guard for fetch(): refuse a URL whose host is not a public address."""
    state = host_state(url)
    if state == "private":
        raise NetError(NOT_PUBLIC)
    if state == "unresolved":
        raise NetError("url: unreachable (the host name did not resolve)")


class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """Run the guard on every redirect target before following it."""

    def __init__(self, guard):
        self.guard = guard

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.guard(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _wait_turn(host):
    with _glock:
        lock = _host_locks.setdefault(host, threading.Lock())
    with lock:
        gap = HOST_INTERVAL.get(host, DEFAULT_INTERVAL)
        last = _host_last.get(host, 0.0)
        now = time.monotonic()
        if now - last < gap:
            time.sleep(gap - (now - last))
        _host_last[host] = time.monotonic()


def _cache_path(key):
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, h[:2], h)


def cache_get(key, ttl=CACHE_TTL):
    p = _cache_path(key)
    try:
        st = os.stat(p)
    except OSError:
        return None
    if ttl is not None and time.time() - st.st_mtime > ttl:
        return None
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None


def cache_put(key, data):
    p = _cache_path(key)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".%d.tmp" % os.getpid()
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
    except OSError:
        pass


def fetch(url, data=None, headers=None, source=None, timeout=25, retries=3,
          cache=True, max_bytes=60 * 1024 * 1024, give_up_on_429=False, guard=None):
    """GET (or POST when data is given) and return bytes.

    Raises NetError with a one-line message naming the source on failure.
    A guard is called with the URL and with each redirect target before any
    request is made to it; it refuses one by raising NetError.
    """
    source = source or urllib.parse.urlsplit(url).hostname or "net"
    if guard:
        guard(url)
        opener = urllib.request.build_opener(_GuardedRedirects(guard))
    else:
        opener = None
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode("utf-8")
        headers = dict(headers or {}, **{"Content-Type": "application/json"})
    key = url + "\n" + (data.decode("utf-8", "replace") if data else "")
    if cache:
        hit = cache_get(key)
        if hit is not None:
            return hit
    host = urllib.parse.urlsplit(url).hostname or ""
    hdrs = {"User-Agent": UA, "Accept-Encoding": "identity"}
    hdrs.update(headers or {})
    delay = 2.0
    last_err = None
    for attempt in range(retries + 1):
        _wait_turn(host)
        req = urllib.request.Request(url, data=data, headers=hdrs)
        try:
            with (opener.open(req, timeout=timeout) if opener
                  else urllib.request.urlopen(req, timeout=timeout)) as r:
                body = r.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise NetError("%s: response too large" % source)
                if cache:
                    cache_put(key, body)
                return body
        except urllib.error.HTTPError as e:
            last_err = NetError("%s: HTTP %d" % (source, e.code), e.code)
            if e.code == 429:
                last_err = NetError("%s: rate-limited (HTTP 429)" % source, 429)
                if give_up_on_429 and attempt >= 1:
                    break
            elif e.code not in (500, 502, 503, 504):
                break
            ra = e.headers.get("Retry-After") if e.headers else None
            wait = delay
            if ra and ra.isdigit():
                wait = min(float(ra), 20.0)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", e)
            last_err = NetError("%s: network error (%s)" % (source, reason))
            wait = delay
        if attempt < retries:
            time.sleep(wait)
            delay *= 2
    raise last_err


def get_json(url, **kw):
    body = fetch(url, **kw)
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        raise NetError("%s: response was not JSON" % (kw.get("source") or url))


def qs(params):
    return urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
