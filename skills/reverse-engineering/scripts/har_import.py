#!/usr/bin/env python3
"""
Read a saved browser session (a .har file exported from the browser's network
panel) as ground truth about what a page fetched, without running anything. This
is the way to analyse a page behind a login: capture_net.py drives a fresh
browser with no session, but a HAR you save while logged in already holds the
requests and the response bodies.

  python har_import.py session.har                      # what was fetched (redacted)
  python har_import.py session.har --out net.json        # a capture_net-shaped JSON
  python har_import.py session.har --extract OUT --purpose own_work --note "..."

Secrets are dropped by construction: only method, URL, resource type, status,
content type and size are read from each entry, never the request's headers,
cookies or POST body, and a URL query value that looks like a token or password
is replaced with REDACTED. Extraction writes the response bodies out (under the
gate), so libscan.py and unminify.py can read the real captured code locally
instead of re-fetching it (which a logged-in page would refuse).

Pure Python, bounded and safe on a file you do not trust; it never executes the
captured content.
"""
import argparse
import base64
import binascii
import json
import os
import sys
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

MAX_ENTRIES = 1_000_000
MAX_BODY = 64 << 20            # per extracted body
MAX_EXTRACT_BYTES = 2 * 1024 ** 3
MAX_HAR_FILE = 1024 ** 3      # a HAR is JSON read whole into memory; refuse a hostile multi-GB one
SECRET_PARAMS = {"token", "access_token", "refresh_token", "id_token", "key", "api_key", "apikey",
                 "auth", "authorization", "session", "sessionid", "sid", "password", "passwd", "pwd",
                 "sig", "signature", "secret", "code", "client_secret", "jwt", "bearer"}


class HarError(ValueError):
    pass


def redact_url(url):
    """Replace the value of any query parameter whose name looks like a credential."""
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    q = [(k, "REDACTED" if k.lower() in SECRET_PARAMS else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunparse(parts._replace(query=urlencode(q)))


def _resource_type(entry, mime):
    rt = entry.get("_resourceType")
    if rt:
        return rt
    m = (mime or "").lower()
    if "javascript" in m or "ecmascript" in m:
        return "script"
    if m.startswith("text/html"):
        return "document"
    if m.startswith("text/css"):
        return "stylesheet"
    if m.startswith("application/json"):
        return "xhr"
    if m.startswith("image/"):
        return "image"
    if m.startswith(("font/", "application/font")) or "woff" in m:
        return "font"
    return "other"


def parse(har_text):
    """The entries of a HAR, reduced to the safe fields (no headers/cookies/bodies).
    URLs have credential-looking query values redacted."""
    try:
        doc = json.loads(har_text)
    except json.JSONDecodeError as e:
        raise HarError(f"not valid HAR JSON: {e}")
    log = doc.get("log")
    if not isinstance(log, dict) or not isinstance(log.get("entries"), list):
        raise HarError("not a HAR file (no log.entries list)")
    raw = log["entries"]
    entries, truncated = [], False
    for e in raw:
        if len(entries) >= MAX_ENTRIES:
            truncated = True
            break
        if not isinstance(e, dict):
            continue
        req = e.get("request") or {}
        resp = e.get("response") or {}
        content = resp.get("content") or {}
        mime = content.get("mimeType")
        url = req.get("url", "")
        entries.append({
            "method": req.get("method"),
            "url": redact_url(url) if isinstance(url, str) else "",
            "resource_type": _resource_type(e, mime),
            "status": resp.get("status"),
            "content_type": mime,
            "size": content.get("size") if isinstance(content.get("size"), int) else resp.get("bodySize"),
            "has_body": bool(content.get("text")),
        })
    pages = [p.get("title") or p.get("id") for p in (log.get("pages") or []) if isinstance(p, dict)]
    return {"pages": pages, "creator": (log.get("creator") or {}).get("name"),
            "entries": entries, "truncated": truncated, "summary": _summary(entries)}


def _summary(entries):
    by_type = {}
    for e in entries:
        by_type[e["resource_type"]] = by_type.get(e["resource_type"], 0) + 1
    return {"total": len(entries), "by_type": by_type,
            "with_body": sum(1 for e in entries if e["has_body"])}


def _body_bytes(content):
    """The response body bytes, base64-decoded when the HAR says so. None if absent
    or undecodable."""
    text = content.get("text")
    if not isinstance(text, str) or not text:
        return None
    if content.get("encoding") == "base64":
        try:
            return base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            return None
    return text.encode("utf-8", errors="replace")


def _safe_rel(url):
    """A relative, non-escaping file path for a response, from its URL path."""
    parts = urlparse(url)
    segs = [s for s in parts.path.split("/") if s not in ("", ".")]
    if any(s == ".." for s in segs):
        segs = [s for s in segs if s != ".."]
    if not segs or parts.path.endswith("/"):
        segs.append("index")
    host = (parts.netloc or "host").replace(":", "_")
    return os.path.join(host.lstrip("."), *segs)


def extract(har_text, outdir, purpose, note, max_bytes=None):
    """GATED. Write the response bodies the HAR carries out to outdir (new or
    empty). Returns (written paths, [(url, reason) skipped])."""
    purposes = lawful_purposes()
    if purpose not in purposes:
        raise HarError(f"--purpose must be one of {', '.join(purposes)}. {REFUSED}")
    if not note or not note.strip():
        raise HarError(f"--note must name the concrete basis in one sentence. {REFUSED}")
    if os.path.lexists(outdir) and (not os.path.isdir(outdir) or os.listdir(outdir)):
        raise HarError(f"--extract needs a new or empty folder; {outdir} is not one")
    cap = MAX_EXTRACT_BYTES if max_bytes is None else max_bytes
    doc = json.loads(har_text)
    os.makedirs(outdir, exist_ok=True)
    root = os.path.realpath(outdir)
    written, skipped, total, seen = [], [], 0, {}
    for e in (doc.get("log") or {}).get("entries") or []:
        if not isinstance(e, dict):
            continue
        url = (e.get("request") or {}).get("url", "")
        body = _body_bytes((e.get("response") or {}).get("content") or {})
        if body is None:
            continue
        if len(body) > MAX_BODY or total + len(body) > cap:
            skipped.append((url, f"body over the cap ({len(body)} B)"))
            continue
        rel = _safe_rel(url)
        dst = os.path.join(root, rel)
        # de-duplicate a repeated path (two responses from one URL path)
        if dst in seen:
            seen[dst] += 1
            dst = f"{dst}.{seen[dst]}"
        else:
            seen[dst] = 0
        if not dst.startswith(root + os.sep) or os.path.realpath(dst) != dst:
            skipped.append((url, "resolves outside the output folder or through a link"))
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(body)
        total += len(body)
        written.append(dst)
    return written, skipped


def lawful_purposes():
    schema = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dossier-schema.json")
    with open(schema, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    return doc["dossier_schema"]["properties"]["lawful_basis"]["properties"]["purpose"]["enum"]


REFUSED = ("Extraction serves understanding and interoperability with a page you are authorized to use, "
           "never redistributing another site's code or assets. That is refused, the same as in the dossier.")


def main():
    ap = argparse.ArgumentParser(description="Read a saved browser session (.har) as a network capture.")
    ap.add_argument("file", help="Path to the .har file.")
    ap.add_argument("--out", metavar="JSON", help="Write a capture_net-shaped JSON here (redacted).")
    ap.add_argument("--extract", metavar="OUTDIR", help="GATED: write the response bodies to OUTDIR.")
    ap.add_argument("--purpose", help="Lawful basis for extraction.")
    ap.add_argument("--note", help="One concrete sentence naming the basis.")
    ap.add_argument("--max-mb", type=int, default=MAX_EXTRACT_BYTES >> 20, help="With --extract: MB cap.")
    ap.add_argument("--limit", type=int, default=60, help="Rows in the console listing.")
    args = ap.parse_args()

    if not os.path.isfile(args.file):
        print(f"No such file: {args.file}", file=sys.stderr)
        sys.exit(1)
    if os.path.getsize(args.file) > MAX_HAR_FILE:
        print(f"HAR is larger than {MAX_HAR_FILE >> 20} MB; it is read whole into memory, so this is refused. "
              f"Trim it in the browser's network panel first.", file=sys.stderr)
        sys.exit(2)
    try:
        with open(args.file, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if args.extract:
            written, skipped = extract(text, args.extract, args.purpose, args.note, max_bytes=args.max_mb << 20)
            print(f"Extracted {len(written)} body/bodies to {os.path.abspath(args.extract)}:")
            for p in written[:args.limit]:
                print(f"  {p}")
            if skipped:
                print(f"\nSkipped {len(skipped)}:")
                for url, why in skipped[:args.limit]:
                    print(f"  {redact_url(url)}: {why}")
            sys.exit(0)
        rep = parse(text)
        print(f"HAR from {rep['creator'] or 'unknown'}, {rep['summary']['total']} request(s)"
              + (" (cut short)" if rep["truncated"] else "") + f", {rep['summary']['with_body']} with a body")
        print("  by type: " + ", ".join(f"{k} {v}" for k, v in sorted(rep["summary"]["by_type"].items())))
        for e in rep["entries"][:args.limit]:
            print(f"  {e['status']} {e['resource_type']:<10} {e['url'][:120]}")
        if args.out:
            result = {"url": (rep["pages"] or [None])[0], "source": "har", "summary": rep["summary"],
                      "entries": rep["entries"]}
            with open(args.out, "w", encoding="utf-8") as out:
                json.dump(result, out, indent=1)
            print(f"Wrote {args.out}")
    except HarError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    except OSError as e:
        print(f"Read failed: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
