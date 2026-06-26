"""Shared helpers for the greek-law grounding scripts.

Thin HTTP and HTML to text utilities used by diavgeia.py, eurlex.py and
fetch_source.py. Kept dependency light (httpx, plus beautifulsoup4 only when
HTML is actually parsed) so the skill stays portable across the machines MOM
provisions, and so the JSON only Diavgeia client needs no HTML stack at all.
"""
from __future__ import annotations

import json
import re
import time

import httpx

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_DROP_TAGS = ("script", "style", "noscript", "template", "svg")
_CHROME_TAGS = ("nav", "header", "footer", "form", "aside")
_JUNK_RE = re.compile(
    r"(menu|navbar|nav-|sidebar|header|footer|cookie|breadcrumb|"
    r"share|social|banner|advert|modal|offcanvas)",
    re.IGNORECASE,
)


def http_get(url, *, params=None, headers=None, accept_json=False,
             timeout=30.0, retries=3, retry_on=(202,)):
    """GET a URL with a browser User-Agent and bounded retries.

    Uses a session so cookies set on one response carry into the next attempt.
    Retries on transient network errors and on the status codes in retry_on.
    202 is how EUR-Lex answers while it renders a document asynchronously: it
    sets a session cookie on the 202 and only serves the body once the same
    session asks again, so the shared Client is what makes the retry work.
    Returns the final httpx.Response; raises the last error if all attempts fail.
    """
    hdrs = {"User-Agent": UA}
    if accept_json:
        hdrs["Accept"] = "application/json"
    if headers:
        hdrs.update(headers)
    last_exc = None
    delay = 1.0
    r = None
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=hdrs) as client:
        for attempt in range(retries):
            try:
                r = client.get(url, params=params)
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt == retries - 1:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            if r.status_code in retry_on and attempt < retries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            return r
    if last_exc:
        raise last_exc
    return r


def _soup(content, encoding=None):
    from bs4 import BeautifulSoup  # lazy: only when HTML is parsed

    if isinstance(content, bytes):
        return BeautifulSoup(content, "html.parser", from_encoding=encoding)
    return BeautifulSoup(content, "html.parser")


def _strip_chrome(soup):
    for tag in soup(list(_DROP_TAGS) + list(_CHROME_TAGS)):
        tag.decompose()
    for attr in ("class", "id"):
        for tag in soup.find_all(attrs={attr: _JUNK_RE}):
            tag.decompose()


def _main_node(soup):
    """Pick the readable content block.

    Prefers an explicit article or main region. Otherwise scores candidate
    containers by text length weighted down by link density, so a statute body
    beats a link heavy menu or breadcrumb that survived chrome stripping.
    """
    explicit = soup.find("article") or soup.find("main")
    if explicit:
        return explicit
    best, best_score = None, 0.0
    for node in soup.find_all(["div", "section", "td"]):
        text = node.get_text(" ", strip=True)
        n = len(text)
        if n < 200:
            continue
        link_text = sum(len(a.get_text(" ", strip=True)) for a in node.find_all("a"))
        density = link_text / n
        score = n * (1.0 - density)
        if score > best_score:
            best, best_score = node, score
    return best or soup.body or soup


def extract_text(content, *, encoding=None, max_chars=None):
    """Extract readable main text from an HTML document (bytes or str).

    Drops scripts, styles and page chrome, selects the main content block,
    collapses whitespace. Encoding is sniffed by the parser unless overridden
    (Greek legacy pages are often windows-1253 with no charset header).
    """
    soup = _soup(content, encoding)
    _strip_chrome(soup)
    main = _main_node(soup)
    text = main.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n[... truncated ...]"
    return text


def page_title(content, encoding=None):
    soup = _soup(content, encoding)
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    return h1.get_text(strip=True) if h1 else ""


def emit(payload, *, as_json):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(payload)
