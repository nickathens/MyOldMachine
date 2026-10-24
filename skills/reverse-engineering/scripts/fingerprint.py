#!/usr/bin/env python3
"""
Fingerprint a web page's stack from ground-truth evidence.

Usage:
  python fingerprint.py --url https://site.com [--out detections.json]
  python fingerprint.py --html rendered.html          # analyse captured HTML
  python fingerprint.py --url https://site.com --emit-claims   # dossier claims

What it does: fetches the page STATICALLY (no JavaScript is executed, so this is
safe to point at an untrusted target -- we only read bytes) and matches a table
of signatures against the response headers and the HTML/JS body. Each hit
records the LITERAL string that matched and a certainty label:

  definitional -- the marker only exists for this technology (e.g. __NEXT_DATA__).
  strong       -- strongly associated, rare false positives.
  weak         -- suggestive; treat the technology as a hypothesis, not a fact.

For a JavaScript-rendered page, capture the post-render DOM with the browser
skill first and feed it in:

  B="python skills/browser/scripts/browser.py"
  $B goto https://site.com && $B extract --format html > /tmp/rendered.html
  python fingerprint.py --html /tmp/rendered.html

Only requests + stdlib. The detection is ground truth (the matched string);
mapping a weak marker to a product name is the analyst's hypothesis.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webfetch  # noqa: E402

MAX_BODY_BYTES = 5_000_000  # read at most this much of the page, so a huge or endless one can't blow memory

# A script or style address: "//", then at most 300 address characters before the library's
# name. Inline images and fonts are long runs of address characters full of "//" and, read
# case-blind, of short names like "gsap": unbounded, each "//" scanned to the end of the run
# (minutes on a big blob) and random image data read as GSAP. So the run is bounded, and a
# short name must start a path segment or a file name.
URL = r"(?:https?:)?//[^\s\"'<>)]{0,300}?"
REST = r"[^\s\"'<>)]{0,300}"

# Each signature: name, category, where (body|headers), certainty, a list of
# patterns (first match wins and supplies the literal), optional version regex
# whose group 1 is the version string. Patterns are matched case-insensitively.
SIGNATURES = [
    # --- JavaScript frameworks (mostly definitional markers) ---
    {"name": "Next.js", "category": "framework", "where": "body", "certainty": "definitional",
     "patterns": [r"__NEXT_DATA__", r"/_next/static/"], "version": r"next@([0-9]+\.[0-9]+\.[0-9]+)"},
    {"name": "Next.js", "category": "framework", "where": "headers", "certainty": "definitional",
     "patterns": [r"x-powered-by:\s*Next\.js"]},
    {"name": "Nuxt", "category": "framework", "where": "body", "certainty": "definitional",
     "patterns": [r"window\.__NUXT__", r"/_nuxt/", r'id="__nuxt"']},
    {"name": "Gatsby", "category": "framework", "where": "body", "certainty": "definitional",
     "patterns": [r"___gatsby", r"/page-data/"]},
    {"name": "Remix", "category": "framework", "where": "body", "certainty": "definitional",
     "patterns": [r"__remixContext", r"__remixManifest"]},
    {"name": "Astro", "category": "framework", "where": "body", "certainty": "definitional",
     "patterns": [r"astro-island", r"astro-root", r"/_astro/"]},
    {"name": "SvelteKit", "category": "framework", "where": "body", "certainty": "definitional",
     "patterns": [r"/_app/immutable/"]},
    {"name": "Svelte", "category": "framework", "where": "body", "certainty": "strong",
     "patterns": [r'class="[^"]*\bsvelte-[0-9a-z]{6}\b']},
    {"name": "Angular", "category": "framework", "where": "body", "certainty": "definitional",
     "patterns": [r"ng-version=\"[0-9.]+\"", r"_nghost-", r"_ngcontent-"],
     "version": r"ng-version=\"([0-9]+\.[0-9]+\.[0-9]+)\""},
    {"name": "Vue", "category": "framework", "where": "body", "certainty": "strong",
     "patterns": [r"data-v-app", r"__VUE__", r"\bdata-v-[0-9a-f]{8}\b"]},
    {"name": "React", "category": "framework", "where": "body", "certainty": "strong",
     "patterns": [r"data-reactroot", r"__REACT_DEVTOOLS_GLOBAL_HOOK__", URL + r"react-dom" + REST]},

    # --- scroll / animation (the reel stack) ---
    {"name": "GSAP", "category": "animation", "where": "body", "certainty": "strong",
     "patterns": [URL + r"[/@.]gsap\b" + REST, r"\bTweenMax\b",
                  r"\bgsap\.(?:registerPlugin|timeline|to|from|set)\b"],
     "version": r"gsap@([0-9]+\.[0-9]+(?:\.[0-9]+)?)"},
    {"name": "GSAP ScrollTrigger", "category": "animation", "where": "body", "certainty": "strong",
     "patterns": [r"\bScrollTrigger\b"]},
    {"name": "Lenis (smooth scroll)", "category": "animation", "where": "body", "certainty": "strong",
     "patterns": [URL + r"[/@.]lenis\b" + REST, r"@studio-freight/lenis", r"\bnew Lenis\b"],
     "version": r"lenis@([0-9]+\.[0-9]+\.[0-9]+)"},
    {"name": "Locomotive Scroll", "category": "animation", "where": "body", "certainty": "strong",
     "patterns": [r"locomotive-scroll", r"data-scroll-container", r"data-scroll-section"]},
    {"name": "Three.js", "category": "animation", "where": "body", "certainty": "strong",
     "patterns": [URL + r"three(?:\.module)?(?:\.min)?\.js", URL + r"three@" + REST, r"\bTHREE\."],
     "version": r"three@([0-9]+\.[0-9]+(?:\.[0-9]+)?)|three/r?([0-9]{3})"},
    # Not the Framer CDN's host name: no Motion build carries it, and the Framer entry below covers it.
    {"name": "Framer Motion", "category": "animation", "where": "body", "certainty": "strong",
     "patterns": [r"framer-motion"]},
    {"name": "Framer (site builder)", "category": "cms", "where": "body", "certainty": "strong",
     "patterns": [r"framerusercontent\.com", r'content="Framer']},
    {"name": "Barba.js (page transitions)", "category": "animation", "where": "body", "certainty": "weak",
     "patterns": [r"\bbarba\b", r"data-barba"]},
    {"name": "Swiper", "category": "animation", "where": "body", "certainty": "weak",
     "patterns": [r"swiper-(?:container|wrapper|slide)", URL + r"[/@.]swiper\b" + REST]},

    # --- bundlers ---
    {"name": "webpack", "category": "bundler", "where": "body", "certainty": "strong",
     "patterns": [r"webpackJsonp", r"webpackChunk"]},
    # Bounded like URL: an unbounded [^>]+ ran to the next '>' from every opener, 14 min on a hostile page.
    {"name": "Vite", "category": "bundler", "where": "body", "certainty": "strong",
     "patterns": [r"/@vite/client", r'<script type="module"[^>]{1,300}/assets/']},

    # --- libraries ---
    {"name": "jQuery", "category": "library", "where": "body", "certainty": "strong",
     "patterns": [URL + r"jquery[^\s\"'<>)]{0,300}?\.js", r"\bjQuery\b"],
     "version": r"jquery[.-]([0-9]+\.[0-9]+\.[0-9]+)"},

    # --- hosting / CDN (headers) ---
    {"name": "Cloudflare", "category": "hosting", "where": "headers", "certainty": "definitional",
     "patterns": [r"server:\s*cloudflare", r"cf-ray:\s*\S+"]},
    {"name": "Vercel", "category": "hosting", "where": "headers", "certainty": "definitional",
     "patterns": [r"x-vercel-id:\s*\S+", r"server:\s*Vercel"]},
    {"name": "Netlify", "category": "hosting", "where": "headers", "certainty": "definitional",
     "patterns": [r"x-nf-request-id:\s*\S+", r"server:\s*Netlify"]},
    {"name": "GitHub Pages", "category": "hosting", "where": "headers", "certainty": "strong",
     "patterns": [r"server:\s*GitHub\.com"]},
    {"name": "Fastly", "category": "hosting", "where": "headers", "certainty": "weak",
     "patterns": [r"x-served-by:\s*\S*fastly", r"x-fastly"]},

    # --- CMS / commerce / site builders ---
    {"name": "WordPress", "category": "cms", "where": "body", "certainty": "strong",
     "patterns": [r"/wp-content/", r"/wp-json", r"/wp-includes/"]},
    {"name": "Shopify", "category": "cms", "where": "body", "certainty": "definitional",
     "patterns": [r"cdn\.shopify\.com", r"Shopify\.theme"]},
    {"name": "Webflow", "category": "cms", "where": "body", "certainty": "strong",
     "patterns": [r"data-wf-page", r'content="Webflow', r"\.w-(?:container|row|col)\b"]},
    {"name": "Squarespace", "category": "cms", "where": "body", "certainty": "definitional",
     "patterns": [r"static1\.squarespace\.com", r"Static\.SQUARESPACE_CONTEXT"]},
    {"name": "Wix", "category": "cms", "where": "body", "certainty": "strong",
     "patterns": [r"static\.wixstatic\.com", r"X-Wix-"]},

    # --- analytics ---
    {"name": "Google Tag / Analytics", "category": "analytics", "where": "body", "certainty": "strong",
     "patterns": [r"googletagmanager\.com/gtag", r"www\.google-analytics\.com", r"gtag\("]},
]


def fetch(url, timeout, verify):
    # Streamed, only the first MAX_BODY_BYTES read, and redirects followed without reading their bodies.
    final_url, status, headers, raw, encoding = webfetch.get(url, timeout, verify, MAX_BODY_BYTES)
    header_blob = "\n".join(f"{k}: {v}" for k, v in headers.items())
    try:
        body = raw[:MAX_BODY_BYTES].decode(encoding or "utf-8", errors="replace")
    except (LookupError, UnicodeError):
        # A charset the server made up, or a codec that is not text (zlib, base64, hex, rot13):
        # one header would otherwise switch fingerprinting off.
        body = raw[:MAX_BODY_BYTES].decode("utf-8", errors="replace")
    return final_url, status, header_blob, body


def scan(body, header_blob):
    haystacks = {"body": body or "", "headers": header_blob or ""}
    detections = []
    for sig in SIGNATURES:
        hay = haystacks.get(sig["where"], "")
        if not hay:
            continue
        literal = None
        for pat in sig["patterns"]:
            m = re.search(pat, hay, re.IGNORECASE)
            if m:
                literal = m.group(0)[:200]
                break
        if literal is None:
            continue
        version = None
        if sig.get("version"):
            vm = re.search(sig["version"], hay, re.IGNORECASE)
            if vm:
                version = next((g for g in vm.groups() if g), None)
        detections.append({
            "name": sig["name"],
            "category": sig["category"],
            "certainty": sig["certainty"],
            "where": "response headers" if sig["where"] == "headers" else "HTML/JS body",
            "literal": literal,
            "version": version,
        })
    # Two separate signatures can carry the same name (a body marker and a
    # header marker for the same product). Keep the first, note nothing twice.
    seen = set()
    deduped = []
    for d in detections:
        if d["name"] in seen:
            continue
        seen.add(d["name"])
        deduped.append(d)
    return deduped


def slug(name):
    return "gt-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def to_claims(detections):
    claims = []
    for d in detections:
        ver = f" {d['version']}" if d.get("version") else ""
        if d["certainty"] == "definitional":
            statement = f"{d['name']}{ver} is present ({d['literal']!r} is definitional for it)."
        else:
            statement = f"A marker associated with {d['name']}{ver} is present ({d['certainty']} signal)."
        claims.append({
            "kind": "ground_truth",
            "id": slug(d["name"]),
            "statement": statement,
            "evidence": {"tool": "fingerprint.py", "locator": d["where"], "literal": d["literal"]},
        })
    return claims


def main():
    ap = argparse.ArgumentParser(description="Fingerprint a web page's stack.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="Fetch and analyse this URL (static, no JS executed).")
    src.add_argument("--html", help="Analyse an already-captured HTML file.")
    src.add_argument("--js", help="Analyse a single JS/CSS file.")
    ap.add_argument("--out", help="Write JSON here instead of stdout.")
    ap.add_argument("--emit-claims", action="store_true",
                    help="Emit dossier ground_truth claims instead of raw detections.")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--insecure", action="store_true", help="Skip TLS verification.")
    args = ap.parse_args()

    target = args.url or args.html or args.js
    fetched_url, status = target, None
    header_blob = ""
    if args.url:
        try:
            fetched_url, status, header_blob, body = fetch(args.url, args.timeout, not args.insecure)
        except Exception as e:  # noqa: BLE001 - network/TLS errors are expected, report them
            print(f"Fetch failed: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        path = args.html or args.js
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                body = fh.read(MAX_BODY_BYTES)
        except Exception as e:  # noqa: BLE001
            print(f"Read failed: {e}", file=sys.stderr)
            sys.exit(1)

    detections = scan(body, header_blob)

    if args.emit_claims:
        result = to_claims(detections)
    else:
        result = {
            "target": target,
            "fetched_url": fetched_url,
            "status": status,
            "detections": detections,
        }

    text = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Wrote {args.out} ({len(detections)} detection(s))")
    else:
        print(text)


if __name__ == "__main__":
    main()
