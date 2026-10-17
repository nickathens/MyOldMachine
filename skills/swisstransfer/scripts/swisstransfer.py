#!/usr/bin/env python3
"""Big files as a SwissTransfer download link: free, no account, no app.

The machine's rule (Nick, 2026-10-17): a file over 2 GB goes to the person as
a link, never down the Telegram chat. This sends files the way
swisstransfer.com's own page does and prints the link.

  sender EMAIL     set the sender address. SwissTransfer emails it a six
                   character code; this sends that one email and nothing else
  confirm CODE     confirm the code. The confirmation is kept and reused, so
                   later uploads need no code until SwissTransfer asks again
  resend           a new code for the address waiting to be confirmed
  upload PATH...   files or folders as one transfer; prints the link. Link
                   mode: SwissTransfer emails nobody but the sender
  check LINK       what a link serves right now; --download DIR fetches every
                   file back and compares it with what was sent
  list             this user's transfers, newest first, with expiry
  rule PATH...     does the 2 GB rule send these as a link? exit 0 yes, 1 no
  status           the sender, whether SwissTransfer still accepts it, and the rule

SwissTransfer publishes no upload API. Every step here was read from the
site's own web client (app version 0.71.1, read 2026-10-17): the API under
/api/1, its CSRF cookie, the proof-of-work check (ALTCHA) that every browser
solves before a transfer opens, 50 MiB parts PUT straight to storage, and the
free plan's limits (1, 3, 7, 15 or 30 days, where 30 needs the extension
SwissTransfer emails the sender on day 12; 1, 20, 100 or 250 downloads; 50 GB
and 5000 files a transfer). The site can change any of it without notice;
when a step breaks, the error names the step. Blutsh/swissfer (GPL-3.0) was a
second reading of the same protocol; no code is taken from it.

The proof of work is computing, not a person check: every browser on the site
runs it unseen. If SwissTransfer ever asks for a human check instead (a code
read off an image), this stops and says so. It never tries to answer one.

Exit codes: 0 done, 1 something was sent and did not finish, 2 refused before
anything was sent.
"""

import argparse
import base64
import hashlib
import html
import http.client
import http.cookiejar
import json
import math
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_EXCEPTION
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

BASE = "https://www.swisstransfer.com"
USER_AGENT = "MyOldMachine-swisstransfer/1 (+https://github.com/nickathens/MyOldMachine)"
XSRF_COOKIE = "SWISSTRANSFER-API-XSRF-TOKEN"
LANGUAGE = "en"                 # the API takes a bare language code, not en_GB

# Over this a file goes as a link. 2 GB as the Finder counts it. Telegram's
# own ceiling through a local Bot API server is 2000 MB, so a file at or under
# this always fits the chat. utils/send_to_telegram.py refuses above the same
# number, and a test holds the two equal.
LINK_RULE_BYTES = 2_000_000_000

PART = 50 * 1024 * 1024         # the web client's part size
MAX_TRANSFER = 50 * 1024 ** 3   # its size limit for one transfer
MAX_FILES = 5000                # and its file count limit
DAYS = (1, 3, 7, 15, 30)
DOWNLOADS = (1, 20, 100, 250)
FREE_DAYS = 15                  # past this the sender must accept an extension
IN_FLIGHT = 4                   # parts uploading at once
URL_BATCH = 10                  # storage addresses asked for per request, as the web client does
RETRIES = 3                     # per part, with a fresh address each time
BACKOFF = (1.0, 30.0)           # first wait and longest wait between tries, seconds
API_TIMEOUT = 60
PUT_TIMEOUT = 300               # per socket operation, not per part
SCAN_POLL = 10
SECURE = ("https://",)          # storage and download addresses must start with one of these

ID = r"[A-Za-z0-9_-]{6,80}"     # transfer, file, link and validation ids: no slash, no dot
UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
LINK_RE = re.compile(rf"^https://(?:www\.)?swisstransfer\.com/dl?/({UUID})/?(?:[?#].*)?$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CODE_RE = re.compile(r"^[A-Za-z0-9]{6}$")

# The only calls this script makes to the SwissTransfer host. Anything else is
# refused before it leaves, so the session and the sender's confirmation can
# only ever reach these paths.
ALLOWED = (
    ("GET", re.compile(r"/en")),
    ("GET", re.compile(r"/api/1/altcha-challenge")),
    ("POST", re.compile(r"/api/1/email-validations")),
    ("GET", re.compile(rf"/api/1/email-validations/{ID}")),
    ("POST", re.compile(rf"/api/1/email-validations/{ID}/(confirm|resend)")),
    ("POST", re.compile(r"/api/1/transfers")),
    ("POST", re.compile(rf"/api/1/transfers/{ID}/files")),
    ("PATCH", re.compile(rf"/api/1/transfers/{ID}(/files/{ID})?")),
    ("GET", re.compile(rf"/dl/{UUID}")),
    ("GET", re.compile(rf"/api/1/links/{UUID}/files/{ID}")),
)

# What a link page can say, from the web client's own page names.
PAGE_STATES = {
    "link/show": "ready",
    "link/password": "password protected",
    "errors/transfer/waiting-for-antivirus": "still being scanned by SwissTransfer's antivirus",
    "errors/transfer/not-found": "not found: expired or deleted",
    "errors/transfer/unavailable": "no longer available: out of downloads or expired",
    "errors/transfer/infected": "blocked: SwissTransfer's antivirus flagged it",
    "errors/transfer/country-blocked": "blocked in this country",
}
LAPSED = ("verified_email_check_failed", "email_not_verified")
TOO_MANY = ("email_validation_rate_limit_error", "email_validation_request_locked")


class Refused(Exception):
    """Nothing was sent: no part of any file left this machine."""


class Failed(Exception):
    """Something was sent and the transfer did not finish."""


class _Transient(Exception):
    """One try at a part failed in a way worth another try."""


# ── where this user's sender and transfers are kept ─────────────────────────

def state_dir():
    """One OS account hosts several people, each with a private folder in
    JARVIS_USER_DIR. The sender's confirmation lets anyone holding it open
    transfers in that address's name, so it lives there and nowhere shared.
    Outside the bot, $XDG_STATE_HOME or ~/.local/state."""
    user_dir = os.environ.get("JARVIS_USER_DIR")
    base = user_dir or os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    folder = Path(base) / "swisstransfer"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder


def _write_private(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def load_sender():
    path = state_dir() / "sender.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_sender(sender):
    _write_private(state_dir() / "sender.json", json.dumps(sender, indent=1))


def records():
    path = state_dir() / "transfers.jsonl"
    found = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return found
    for line in lines:
        try:
            found.append(json.loads(line))
        except ValueError:
            continue
    return found


def add_record(entry):
    path = state_dir() / "transfers.jsonl"
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def as_time(value):
    """SwissTransfer gives times as Unix seconds; records keep ISO text."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, timezone.utc).replace(microsecond=0).isoformat()
    return str(value) if value else ""


def human(n):
    for unit, size in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= size:
            return f"{n / size:.2f} {unit}"
    return f"{n} bytes"


def say(*parts):
    print(*parts, flush=True)


# ── files ───────────────────────────────────────────────────────────────────

class Local:
    def __init__(self, path, name):
        self.path = path
        self.name = name              # what the recipient sees, folders kept
        self.size = path.stat().st_size
        self.sha256 = None


def gather(paths):
    """The files a list of paths stands for. A folder sends what is inside it
    under its own name, skipping hidden files and links, as a browser drop
    would. Refused for anything missing, empty of files, or named twice."""
    found = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_symlink() and not p.exists():
            raise Refused(f"{raw} is a broken link")
        if p.is_file():
            found.append(Local(p.resolve(), p.name))
        elif p.is_dir():
            root = p.resolve()
            inside = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for fn in sorted(filenames):
                    full = Path(dirpath) / fn
                    if fn.startswith(".") or full.is_symlink() or not full.is_file():
                        continue
                    inside.append(Local(full, f"{root.name}/{full.relative_to(root).as_posix()}"))
            if not inside:
                raise Refused(f"{raw} holds no files to send")
            found.extend(inside)
        else:
            raise Refused(f"{raw} does not exist")
    seen = set()
    for f in found:
        if f.name in seen:
            raise Refused(f"two files would arrive as {f.name}; rename one or send them apart")
        seen.add(f.name)
    if len(found) > MAX_FILES:
        raise Refused(f"{len(found)} files; SwissTransfer takes {MAX_FILES} at most in one transfer")
    total = sum(f.size for f in found)
    if total > MAX_TRANSFER:
        raise Refused(f"{human(total)} in all; SwissTransfer takes 50 GB at most in one transfer")
    return found


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ── talking to the site ─────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect from any of these calls means something changed; show it
    rather than follow it somewhere unexpected."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open(opener, request, timeout):
    """Status, headers and body. An HTTP error is an answer, not an exception."""
    try:
        with opener.open(request, timeout=timeout) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except (OSError, http.client.HTTPException):
            body = b""
        return e.code, e.headers, body


# Storage addresses carry their own signature. Nothing from the SwissTransfer
# session (cookies, CSRF token) goes to them, so they get an opener of their own.
_STORAGE = urllib.request.build_opener(_NoRedirect)


def inertia(page_html):
    """The JSON state every swisstransfer.com page ships in one script tag."""
    m = re.search(r'<script data-page="app" type="application/json">(.*?)</script>', page_html, re.S)
    if not m:
        return None
    try:
        return json.loads(html.unescape(m.group(1)))
    except ValueError:
        return None


def error_text(doc):
    err = doc.get("error") if isinstance(doc, dict) else None
    if not isinstance(err, dict):
        return (doc.get("message") if isinstance(doc, dict) else "") or "no reason given"
    parts = []
    for e in err.get("errors") or []:
        if isinstance(e, dict):
            field = (e.get("context") or {}).get("attribute") or "request"
            parts.append(f"{field}: {e.get('description') or e.get('code') or 'refused'}")
    return "; ".join(parts) or err.get("description") or err.get("code") or "no reason given"


def error_codes(doc):
    """Every error code and field message an answer carries, for matching."""
    found = set()
    err = doc.get("error") if isinstance(doc, dict) else None
    if isinstance(err, dict):
        found.add(str(err.get("code") or ""))
        for e in err.get("errors") or []:
            if isinstance(e, dict):
                found.add(str(e.get("code") or ""))
                found.add(str(e.get("description") or ""))
    if isinstance(doc, dict) and isinstance(doc.get("errors"), dict):
        for v in doc["errors"].values():
            found.update(str(x) for x in (v if isinstance(v, list) else [v]))
    found.discard("")
    return found


def mentions(codes, needles):
    return any(n in c for c in codes for n in needles)


class Site:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect)
        self.lock = threading.Lock()

    @staticmethod
    def _fence(method, path):
        if not any(m == method and rx.fullmatch(path) for m, rx in ALLOWED):
            raise Refused(f"{method} {path} is not a call this script makes to SwissTransfer")

    def page(self, path):
        self._fence("GET", path)
        req = urllib.request.Request(BASE + path, headers={
            "User-Agent": USER_AGENT, "Accept": "text/html", "Accept-Language": "en-GB,en;q=0.9"})
        return _open(self.opener, req, API_TIMEOUT)

    def start(self):
        """Load the front page for the session and CSRF cookies every call needs."""
        try:
            status, _, _ = self.page("/en")
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            raise Refused(f"could not reach SwissTransfer: {e}") from None
        if status != 200 or not self.xsrf():
            raise Refused(f"SwissTransfer's front page answered HTTP {status} without a session; "
                          "the site may have changed")
        return self

    def xsrf(self):
        for c in self.jar:
            if c.name == XSRF_COOKIE:
                return urllib.parse.unquote(c.value)
        return ""

    def api(self, method, path, payload=None, query=None, _again=True):
        self._fence(method, path)
        data = None if payload is None else json.dumps(payload).encode()
        url = BASE + path + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json",
                   "X-Requested-With": "XMLHttpRequest", "X-XSRF-TOKEN": self.xsrf()}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with self.lock:
            status, _, body = _open(self.opener, req, API_TIMEOUT)
        if status == 419 and _again:
            # The CSRF token went stale. The front page hands out a fresh one,
            # which is what the web client does on the same answer.
            self.start()
            return self.api(method, path, payload, query, _again=False)
        try:
            doc = json.loads(body.decode("utf-8")) if body else {}
        except (ValueError, UnicodeDecodeError):
            doc = {}
        return status, doc if isinstance(doc, dict) else {"data": doc}


# ── the proof of work ───────────────────────────────────────────────────────

def solve_altcha(challenge):
    """Answer an ALTCHA proof of work: the number whose SHA-256 after the salt
    is the challenge. The browser widget does the same in a web worker. The
    payload is the widget's own: base64 of the challenge plus the number."""
    if not isinstance(challenge, dict) or challenge.get("algorithm") != "SHA-256":
        if isinstance(challenge, dict) and any("code" in k.lower() for k in challenge):
            raise Refused("SwissTransfer asked for a human check (a code to read off an image). "
                          "This script does not answer those: upload through the website instead")
        raise Refused("SwissTransfer's proof-of-work check changed shape; the script needs updating")
    try:
        salt = str(challenge["salt"])
        target = bytes.fromhex(challenge["challenge"])
        top = int(challenge.get("maxNumber", 1_000_000))
        signature = challenge["signature"]
    except (KeyError, ValueError, TypeError):
        raise Refused("SwissTransfer's proof-of-work check is missing a field; the script needs updating") from None
    started = time.monotonic()
    seed = hashlib.sha256(salt.encode())
    for number in range(top + 1):
        h = seed.copy()
        h.update(str(number).encode())
        if h.digest() == target:
            payload = {"algorithm": "SHA-256", "challenge": challenge["challenge"], "number": number,
                       "salt": salt, "signature": signature,
                       "took": int((time.monotonic() - started) * 1000)}
            return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    raise Refused("SwissTransfer's proof-of-work check has no answer in its range; the script needs updating")


# ── sender ──────────────────────────────────────────────────────────────────

def request_code(email, site=None):
    if not EMAIL_RE.fullmatch(email or ""):
        raise Refused(f"{email!r} is not an email address")
    site = site or Site().start()
    status, doc = site.api("POST", "/api/1/email-validations", {"email": email})
    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    if doc.get("result") != "success" or not data.get("id"):
        if mentions(error_codes(doc), TOO_MANY):
            raise Refused("SwissTransfer says too many code requests came from here; try again later")
        raise Refused(f"SwissTransfer would not send a code to {email} (HTTP {status}): {error_text(doc)}")
    save_sender({"email": email, "validation_id": str(data["id"]), "requested": now_iso(), "confirmed": None})
    return str(data["id"])


def confirm_code(code, site=None):
    sender = load_sender()
    if not sender.get("validation_id"):
        raise Refused("no code is waiting: run `sender EMAIL` first")
    code = re.sub(r"[^A-Za-z0-9]", "", code or "")
    if not CODE_RE.fullmatch(code):
        raise Refused("a SwissTransfer code is six letters or digits")
    site = site or Site().start()
    status, doc = site.api("POST", f"/api/1/email-validations/{sender['validation_id']}/confirm",
                           {"token": code})
    if doc.get("result") == "success":
        sender["confirmed"] = now_iso()
        save_sender(sender)
        return sender
    codes = error_codes(doc)
    if mentions(codes, ("invalid_validation_token",)):
        raise Refused("SwissTransfer did not accept that code. Check it, or run `resend` for a new one")
    if mentions(codes, TOO_MANY):
        raise Refused("SwissTransfer says too many tries; wait, then run `resend`")
    raise Refused(f"SwissTransfer would not confirm the code (HTTP {status}): {error_text(doc)}")


def still_confirmed(site=None):
    """Ask SwissTransfer whether the kept confirmation still holds: True,
    False, or None when it cannot be read. Nothing is sent to anyone."""
    sender = load_sender()
    if not sender.get("validation_id") or not sender.get("email"):
        return None
    try:
        site = site or Site().start()
        status, doc = site.api("GET", f"/api/1/email-validations/{sender['validation_id']}",
                               query={"email": sender["email"]})
    except Refused:
        return None
    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    if status != 200 or "is_validated" not in data:
        return None
    return bool(data["is_validated"])


def resend_code(site=None):
    sender = load_sender()
    if not sender.get("validation_id"):
        raise Refused("no code is waiting: run `sender EMAIL` first")
    site = site or Site().start()
    status, doc = site.api("POST", f"/api/1/email-validations/{sender['validation_id']}/resend")
    if doc.get("result") == "error" or status >= 400:
        if mentions(error_codes(doc), TOO_MANY):
            raise Refused("SwissTransfer says too many code requests came from here; try again later")
        raise Refused(f"SwissTransfer would not resend the code (HTTP {status}): {error_text(doc)}")
    sender["requested"] = now_iso()
    save_sender(sender)
    return sender


# ── upload ──────────────────────────────────────────────────────────────────

class _Progress:
    """Prints a line at every tenth of the whole transfer, so a long upload is
    never silent."""

    def __init__(self, total):
        self.total = max(total, 1)
        self.sent = 0
        self.started = time.monotonic()
        self.next_mark = 10
        self.lock = threading.Lock()

    def add(self, n):
        with self.lock:
            self.sent += n
            pct = self.sent * 100 // self.total
            if n > 0 and pct >= self.next_mark and self.sent < self.total and self.total > PART:
                rate = self.sent / max(time.monotonic() - self.started, 0.001)
                say(f"  {pct}% sent ({human(self.sent)} of {human(self.total)}, {human(rate)}/s)")
                self.next_mark = pct // 10 * 10 + 10


class _Part:
    """One part of a file, read off disk as it is sent, never held whole in memory."""

    def __init__(self, path, offset, length, progress):
        self.f = open(path, "rb")
        self.f.seek(offset)
        self.left = length
        self.counted = 0
        self.progress = progress

    def read(self, n=-1):
        if self.left <= 0:
            return b""
        n = self.left if n is None or n < 0 else min(n, self.left)
        data = self.f.read(n)
        if not data:
            raise _Transient("the file got shorter while it was being sent")
        self.left -= len(data)
        self.counted += len(data)
        self.progress.add(len(data))
        return data

    def undo(self):
        self.progress.add(-self.counted)
        self.counted = 0

    def close(self):
        self.f.close()


class _Addresses:
    """Signed storage addresses for one file's parts, asked for ten at a time
    just ahead of use, the way the web client keeps its pool."""

    def __init__(self, site, transfer_id, file_id, keys):
        self.site, self.transfer_id, self.file_id = site, transfer_id, file_id
        self.waiting = list(keys)
        self.ready = {}
        self.lock = threading.Lock()

    def take(self, key):
        with self.lock:
            if key not in self.ready:
                ahead = [k for k in self.waiting if k != key and k not in self.ready][:URL_BATCH - 1]
                self._fetch([key] + ahead)
            if key in self.waiting:
                self.waiting.remove(key)
            return self.ready.pop(key)

    def _fetch(self, keys):
        asks = [{"file": self.file_id, "chunk": k} for k in keys]
        status, doc = self.site.api("POST", f"/api/1/transfers/{self.transfer_id}/files",
                                    {"upload_url_requests": asks})
        if status == 404:
            raise Failed("the transfer is gone on SwissTransfer's side")
        if status == 409:
            raise Failed(f"SwissTransfer no longer takes parts for this transfer ({error_text(doc)})")
        items = doc.get("data")
        if status >= 400 or not isinstance(items, list):
            raise Failed(f"no storage addresses from SwissTransfer (HTTP {status}): {error_text(doc)}")
        for pos, item in enumerate(items):
            if not isinstance(item, dict) or str(item.get("file", self.file_id)) != str(self.file_id):
                continue
            key = item["chunk"] if "chunk" in item else (keys[pos] if pos < len(keys) else None)
            url = str(item.get("url") or "")
            if not url.startswith(SECURE):
                raise Failed("SwissTransfer handed out a storage address that is not https")
            self.ready[key] = url
        if keys[0] not in self.ready:
            raise Failed(f"SwissTransfer gave no storage address for part {keys[0] or 1}")


def _put(url, path, offset, length, progress):
    part = _Part(path, offset, length, progress)
    req = urllib.request.Request(url, data=part, method="PUT", headers={
        "Content-Type": "application/octet-stream", "Content-Length": str(length), "User-Agent": USER_AGENT})
    try:
        status, headers, _ = _open(_STORAGE, req, PUT_TIMEOUT)
    except _Transient:
        part.undo()
        raise
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        part.undo()
        raise _Transient(str(e)) from None
    finally:
        part.close()
    if status >= 300:
        part.undo()
        raise _Transient(f"storage answered HTTP {status}")
    etag = (headers.get("ETag") or "").strip()
    if not etag:
        part.undo()
        raise _Transient("storage gave no ETag for the part")
    # Storage sends the ETag quoted and the API checks it with the quotes on.
    return etag if etag.startswith('"') else f'"{etag.strip(chr(34))}"'


def _send_file(site, transfer_id, local, file_id, progress):
    count = max(1, math.ceil(local.size / PART))
    keys = [None] if count == 1 else list(range(1, count + 1))
    addresses = _Addresses(site, transfer_id, file_id, keys)

    def one(key):
        offset = 0 if key is None else (key - 1) * PART
        length = local.size if key is None else min(PART, local.size - offset)
        for attempt in range(RETRIES + 1):
            url = addresses.take(key)
            try:
                return key, _put(url, local.path, offset, length, progress)
            except _Transient as e:
                if attempt == RETRIES:
                    raise Failed(f"part {key or 1} of {local.name} failed {RETRIES + 1} times: {e}") from None
                time.sleep(min(BACKOFF[0] * 2 ** attempt, BACKOFF[1]))

    etags = {}
    pool = ThreadPoolExecutor(IN_FLIGHT)
    try:
        futures = [pool.submit(one, k) for k in keys]
        wait(futures, return_when=FIRST_EXCEPTION)
        for fut in futures:
            if fut.done() and fut.exception():
                raise fut.exception()
        wait(futures)
        for fut in futures:
            key, etag = fut.result()
            etags[key] = etag
    finally:
        pool.shutdown(wait=True, cancel_futures=True)

    body = {} if count == 1 else {"etags": [{"chunk_index": k, "etag": etags[k]} for k in sorted(etags)]}
    status, doc = site.api("PATCH", f"/api/1/transfers/{transfer_id}/files/{file_id}", body)
    if status >= 400 or doc.get("result") == "error":
        raise Failed(f"SwissTransfer would not assemble {local.name} (HTTP {status}): {error_text(doc)}")


def upload(paths, days=FREE_DAYS, downloads=250, message="", title="", password="", site=None):
    """Send files as one transfer and return its record. Raises Refused when
    nothing was sent, Failed when something was and the transfer did not finish."""
    if days not in DAYS:
        raise Refused(f"days must be one of {', '.join(map(str, DAYS))}")
    if downloads not in DOWNLOADS:
        raise Refused(f"downloads must be one of {', '.join(map(str, DOWNLOADS))}")
    sender = load_sender()
    if not sender.get("email") or not sender.get("confirmed"):
        if sender.get("email"):
            raise Refused(f"{sender['email']} is waiting for its code: run `confirm CODE`")
        raise Refused("no confirmed sender yet: run `sender EMAIL`, then `confirm CODE`")
    files = gather(paths)
    total = sum(f.size for f in files)
    say(f"Sending {len(files)} file{'s' if len(files) != 1 else ''}, {human(total)}, as {sender['email']}")
    for f in files:
        f.sha256 = sha256_of(f.path)

    site = site or Site().start()
    status, challenge = site.api("GET", "/api/1/altcha-challenge")
    if status != 200:
        raise Refused(f"SwissTransfer's proof-of-work check answered HTTP {status}")
    altcha = solve_altcha(challenge)
    payload = {
        "method": "link", "title": title, "recipients": [], "email": sender["email"],
        "message": message, "password": password, "language": LANGUAGE,
        "max_download": downloads, "expires_in_days": days,
        "files": [{"path": f.name, "size": f.size, "mime_type": mimetypes.guess_type(f.name)[0]}
                  for f in files],
        "altcha": altcha, "email_validation_id": sender["validation_id"],
    }
    status, doc = site.api("POST", "/api/1/transfers", payload)
    transfer = doc.get("data") if isinstance(doc.get("data"), dict) else None
    if doc.get("result") != "success" or not transfer or not transfer.get("id"):
        codes = error_codes(doc)
        if mentions(codes, LAPSED):
            sender["confirmed"] = None
            save_sender(sender)
            raise Refused(f"SwissTransfer no longer accepts the confirmation for {sender['email']}. "
                          f"Run `sender {sender['email']}` (a new code is emailed), then `confirm CODE`")
        if mentions(codes, ("altcha",)) or "altcha" in error_text(doc):
            raise Refused("SwissTransfer refused the proof-of-work check, so it may want a human check "
                          f"now; upload through the website instead ({error_text(doc)})")
        raise Refused(f"SwissTransfer would not open the transfer (HTTP {status}): {error_text(doc)}")

    transfer_id = str(transfer["id"])
    remote = {str(r.get("path")): str(r.get("id")) for r in transfer.get("files") or [] if isinstance(r, dict)}
    progress = _Progress(total)
    try:
        for f in files:
            if f.name not in remote:
                raise Failed(f"SwissTransfer did not take {f.name} into the transfer")
            _send_file(site, transfer_id, f, remote[f.name], progress)
        status, doc = site.api("PATCH", f"/api/1/transfers/{transfer_id}", {"status": "completed"})
        data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
        link = (data.get("link") or {}) if isinstance(data.get("link"), dict) else {}
        url = link.get("download_url") or (f"{BASE}/dl/{link['id']}" if link.get("id") else "")
        if status >= 400 or doc.get("result") == "error" or not url:
            raise Failed(f"every part arrived but SwissTransfer would not finish the transfer "
                         f"(HTTP {status}): {error_text(doc)}")
        if not LINK_RE.match(url):
            raise Failed(f"SwissTransfer finished the transfer but answered with an odd link ({url[:80]})")
    except BaseException as e:
        # Leave nothing half open on their side, as the web client does.
        try:
            site.api("PATCH", f"/api/1/transfers/{transfer_id}", {"status": "failed"})
        except Exception:
            pass
        if isinstance(e, Refused):
            raise Failed(str(e)) from None   # parts may already have gone
        raise

    # The link exists now. One read of its page, as a recipient gets it, gives
    # SwissTransfer's own expiry (rounded up to the hour, and day 15 for a
    # 30 day transfer until the extension is accepted). The record must not
    # wait on it, so a failed read falls back to the asked number of days.
    state, page = "", {}
    try:
        state, page = read_link(url, site)
    except (Refused, Failed):
        pass
    expires = as_time(page.get("expires_at") or data.get("expires_at")) or \
        (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat()
    entry = {"created": now_iso(), "link": url, "transfer": transfer_id, "sender": sender["email"],
             "days": days, "downloads": downloads, "expires": expires,
             "extension_mail": as_time(page.get("extension_mail_due_at")), "bytes": total,
             "message": message, "password": bool(password), "page": state,
             "files": [{"name": f.name, "size": f.size, "sha256": f.sha256} for f in files]}
    add_record(entry)
    return entry


# ── checking a link ─────────────────────────────────────────────────────────

def link_id(link):
    m = LINK_RE.match((link or "").strip())
    if not m:
        raise Refused(f"{link!r} is not a SwissTransfer download link")
    return m.group(1).lower()


def read_link(link, site=None):
    """(state, transfer) as a recipient's browser sees the page right now."""
    uuid = link_id(link)
    site = site or Site()
    try:
        status, _, body = site.page(f"/dl/{uuid}")
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        raise Refused(f"could not reach SwissTransfer: {e}") from None
    if status == 404:
        return PAGE_STATES["errors/transfer/not-found"], {}
    page = inertia(body.decode("utf-8", "replace")) if status == 200 else None
    if not page:
        raise Refused(f"the link page answered HTTP {status} without its page data; the site may have changed")
    component = str(page.get("component") or "")
    transfer = (page.get("props") or {}).get("transfer") or {}
    return PAGE_STATES.get(component, f"in a state this script does not know ({component})"), transfer


def download_file(site, uuid, remote, folder):
    status, doc = site.api("GET", f"/api/1/links/{uuid}/files/{remote['id']}")
    url = str(((doc.get("data") or {}) if isinstance(doc.get("data"), dict) else {}).get("url") or "")
    if status != 200 or not url.startswith(SECURE):
        raise Failed(f"no download address for {remote.get('path')} (HTTP {status}): {error_text(doc)}")
    # Keep the sender's folders, but never let a name climb out of the target.
    parts = [x for x in PurePosixPath(str(remote.get("path") or remote["id"]).replace("\\", "/")).parts
             if x not in ("/", ".", "..")]
    target = Path(folder).joinpath(*(parts or [str(remote["id"])]))
    name = "/".join(parts)
    if target.exists():
        raise Refused(f"{target} already exists; pick an empty folder")
    target.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _STORAGE.open(req, timeout=PUT_TIMEOUT) as r, open(target, "xb") as out:
            for block in iter(lambda: r.read(8 << 20), b""):
                h.update(block)
                out.write(block)
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        raise Failed(f"download of {name} broke off: {e}") from None
    return target, h.hexdigest()


# ── commands ────────────────────────────────────────────────────────────────

def cmd_sender(args):
    request_code(args.email)
    say(f"SwissTransfer emailed a six character code to {args.email}. Read it from that inbox "
        "(or its spam folder), then run: confirm CODE")


def cmd_confirm(args):
    sender = confirm_code(args.code)
    say(f"Confirmed. Uploads now go out as {sender['email']}.")


def cmd_resend(args):
    sender = resend_code()
    say(f"SwissTransfer emailed a new code to {sender['email']}.")


def cmd_upload(args):
    password = ""
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            raise Refused("--password-stdin was given but no password came in")
    entry = upload(args.paths, days=args.days, downloads=args.downloads,
                   message=args.message, title=args.title, password=password)
    say(f"Link: {entry['link']}")
    note = (" (a 30 day link ends on day 15 unless the sender accepts the extension SwissTransfer "
            f"emails on {entry['extension_mail'][:10] or 'day 12'})") if entry["days"] > FREE_DAYS else ""
    count, downloads = len(entry["files"]), entry["downloads"]
    say(f"{count} file{'s' if count != 1 else ''}, {human(entry['bytes'])}, "
        f"expires {entry['expires'][:16].replace('T', ' ')} UTC{note}, "
        f"up to {downloads} download{'s' if downloads != 1 else ''}, sent as {entry['sender']}")
    say(f"The link page says: {entry['page'] or 'nothing (it could not be read back; run check)'}.")


def cmd_check(args):
    uuid = link_id(args.link)
    deadline = time.monotonic() + max(args.wait, 0)
    site = Site()
    while True:
        state, transfer = read_link(args.link, site)
        if state != PAGE_STATES["errors/transfer/waiting-for-antivirus"] or time.monotonic() >= deadline:
            break
        time.sleep(SCAN_POLL)
    say(f"{args.link}: {state}")
    files = [f for f in transfer.get("files") or [] if isinstance(f, dict)]
    for f in files:
        say(f"  {f.get('path')}  {human(int(f.get('size') or 0))}")
    if transfer.get("expires_at"):
        say(f"  expires {as_time(transfer['expires_at'])[:16].replace('T', ' ')} UTC")
    sent = next((r for r in reversed(records()) if LINK_RE.match(str(r.get("link")))
                 and link_id(r["link"]) == uuid), None)
    if sent and files:
        want = {(x["name"], x["size"]) for x in sent["files"]}
        have = {(str(f.get("path")), int(f.get("size") or 0)) for f in files}
        say("  Names and sizes match what was sent." if want == have
            else f"  DIFFERS from what was sent: missing {sorted(want - have)}, extra {sorted(have - want)}")
    if args.download:
        if state != PAGE_STATES["link/show"]:
            raise Refused(f"nothing to download: the link is {state}")
        folder = Path(args.download).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        site.start()
        hashes = {x["name"]: x["sha256"] for x in sent["files"]} if sent else {}
        bad = 0
        for f in files:
            target, digest = download_file(site, uuid, f, folder)
            expected = hashes.get(str(f.get("path")))
            verdict = ("identical to what was sent" if digest == expected else
                       "DIFFERENT from what was sent" if expected else "no record to compare with")
            bad += bool(expected) and digest != expected
            say(f"  downloaded {f.get('path')}: {verdict}")
        if bad:
            raise Failed(f"{bad} file(s) came back different")


def cmd_list(args):
    found = records()
    if not found:
        say("No transfers yet.")
        return
    today = datetime.now(timezone.utc).date()
    for r in reversed(found[-args.limit:]):
        try:
            left = (datetime.fromisoformat(str(r["expires"])[:10]).date() - today).days
            when = f"expires {str(r['expires'])[:10]} ({left} days left)" if left >= 0 else "expired"
        except ValueError:
            when = f"expires {r.get('expires')}"
        names = ", ".join(x["name"] for x in r["files"][:3]) + (" ..." if len(r["files"]) > 3 else "")
        say(f"{r['created'][:16]}  {r['link']}  {human(r['bytes'])}  {when}  {names}")


def cmd_rule(args):
    over = False
    for f in gather(args.paths):
        big = f.size > LINK_RULE_BYTES
        over |= big
        say(f"{f.name}: {human(f.size)}, " + ("over 2 GB: send it as a link" if big else "2 GB or less: Telegram"))
    sys.exit(0 if over else 1)


def cmd_status(args):
    sender = load_sender()
    if not sender.get("email"):
        say("Sender: none yet. Run `sender EMAIL`.")
    elif sender.get("confirmed"):
        say(f"Sender: {sender['email']}, confirmed {sender['confirmed']}.")
        holds = still_confirmed()
        if holds is False:
            sender["confirmed"] = None
            save_sender(sender)
            say(f"SwissTransfer no longer accepts that confirmation: run `sender {sender['email']}` "
                "for a new code before the next upload.")
        else:
            say("SwissTransfer says it still holds." if holds else
                "Could not ask SwissTransfer whether it still holds; an upload will say.")
    else:
        say(f"Sender: {sender['email']}, waiting for its code since {sender.get('requested')}.")
    say(f"Rule: a file over {human(LINK_RULE_BYTES)} goes as a link. Transfers recorded: {len(records())}.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Big files as a SwissTransfer download link.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("sender", help="set the sender address and have SwissTransfer email it a code")
    p.add_argument("email")
    p.set_defaults(run=cmd_sender)
    p = sub.add_parser("confirm", help="confirm the code SwissTransfer emailed")
    p.add_argument("code")
    p.set_defaults(run=cmd_confirm)
    sub.add_parser("resend", help="a new code for the address waiting to be confirmed").set_defaults(run=cmd_resend)
    p = sub.add_parser("upload", help="send files or folders as one transfer and print the link")
    p.add_argument("paths", nargs="+")
    p.add_argument("--days", type=int, default=FREE_DAYS, choices=DAYS)
    p.add_argument("--downloads", type=int, default=250, choices=DOWNLOADS)
    p.add_argument("--message", default="")
    p.add_argument("--title", default="")
    p.add_argument("--password-stdin", action="store_true", help="read a download password from stdin")
    p.set_defaults(run=cmd_upload)
    p = sub.add_parser("check", help="what a link serves right now")
    p.add_argument("link")
    p.add_argument("--wait", type=int, default=0, help="seconds to wait out the antivirus scan")
    p.add_argument("--download", metavar="DIR", help="fetch every file back and compare (uses a download)")
    p.set_defaults(run=cmd_check)
    p = sub.add_parser("list", help="this user's transfers, newest first")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(run=cmd_list)
    p = sub.add_parser("rule", help="does the 2 GB rule send these as a link (exit 0) or not (1)")
    p.add_argument("paths", nargs="+")
    p.set_defaults(run=cmd_rule)
    sub.add_parser("status", help="the sender and the rule").set_defaults(run=cmd_status)
    args = parser.parse_args(argv)
    try:
        args.run(args)
    except Refused as e:
        print(f"Refused, nothing sent: {e}", file=sys.stderr)
        sys.exit(2)
    except Failed as e:
        print(f"Failed: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Stopped. Any transfer that was open was marked failed on SwissTransfer.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
