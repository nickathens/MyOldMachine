#!/usr/bin/env python3
"""
One bounded GET for the web tools (fingerprint, libscan, unminify): redirects are
followed by hand and their own bodies are never read, and the final body is read
as a stream up to a cap.

Why by hand, and below the session: requests reads a redirect response's body
whole, and unpacks it when it is gzip, before following the Location, whatever
cap the caller puts on the final page; it does so even with allow_redirects=False,
because Session.send builds response.next by consuming that body. 597 KB of gzip
in a 302 took a fetch up 618 MB, so a few MB from a hostile site would take the
bot's shared memory. Here each request goes straight to the transport adapter, a
redirect is closed unread, and the final body is read with urllib3's limit on
decoded bytes. Cookies set along the way and proxy settings are kept, as a
session would keep them.

The whole fetch, redirects and headers included, also ends at a deadline. The
timeout given is per read, so a server sending one byte inside each timeout
never let a download end (5 MB at five bytes a second is eleven days). The
deadline is a SIGALRM timer, so it holds in a tool's main thread, where every
web tool calls this; called from another thread, only the per-read timeout
applies.
"""
import signal
import threading
import time
import urllib.parse

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
MAX_REDIRECTS = 10
DEADLINE = 180   # seconds for one whole fetch, however slowly the bytes arrive


class DeadlineExceeded(Exception):
    """Not an OSError or TimeoutError, so no layer of requests or urllib3 catches it as its own."""


def get(url, timeout, verify=True, limit=5_000_000, check=False, deadline=None):
    """GET url. Returns (final_url, status, headers, body, encoding), where body holds at
    most limit + 1 bytes, so a caller can tell a body at the cap from one over it.
    check=True raises for a 4xx or 5xx status, as raise_for_status does. Past `deadline`
    seconds (default DEADLINE, read at call time) it raises DeadlineExceeded."""
    limit_s = DEADLINE if deadline is None else deadline
    if threading.current_thread() is not threading.main_thread():
        return _get(url, timeout, verify, limit, check)

    def expire(signum, frame):
        raise DeadlineExceeded(f"{url}: no complete answer within {limit_s} s")
    previous = signal.signal(signal.SIGALRM, expire)
    started = time.monotonic()
    outer, _ = signal.setitimer(signal.ITIMER_REAL, limit_s)
    try:
        return _get(url, timeout, verify, limit, check)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if outer:                       # a caller's own timer, put back with what it had left
            signal.setitimer(signal.ITIMER_REAL, max(0.001, outer - (time.monotonic() - started)))


def _get(url, timeout, verify, limit, check):
    import requests
    from requests.cookies import extract_cookies_to_jar
    with requests.Session() as session:
        for _ in range(MAX_REDIRECTS + 1):
            req = session.prepare_request(requests.Request("GET", url, headers={"User-Agent": UA}))
            env = session.merge_environment_settings(req.url, {}, True, verify, None)
            resp = session.get_adapter(req.url).send(req, timeout=timeout, **env)
            extract_cookies_to_jar(session.cookies, req, resp.raw)
            if resp.is_redirect:
                nxt = urllib.parse.urljoin(resp.url, resp.headers["location"])
                resp.close()                     # closed unread: its body never reaches memory
                if urllib.parse.urlsplit(nxt).scheme not in ("http", "https"):
                    raise ValueError(f"{url} redirects to {nxt!r}, which is not http(s)")
                url = nxt
                continue
            with resp:
                if check:
                    resp.raise_for_status()
                body = resp.raw.read(limit + 1, decode_content=True)
                return resp.url, resp.status_code, resp.headers, body, resp.encoding
    raise ValueError(f"more than {MAX_REDIRECTS} redirects, the last to {url}")
