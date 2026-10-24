#!/usr/bin/env python3
"""
Name the front-end libraries inside shipped JavaScript, even when it is minified.

Usage:
  python libscan.py --dir ./chunks
  python libscan.py --files app.js vendor.js
  python libscan.py --capture net.json        # the scripts a capture_net.py run recorded
  python libscan.py --url https://site.com    # static: the page's own <script> files only
  ... [--out result.json] [--emit-claims] [--all]

Why not grep for the package name: a minifier throws names away first. A scan
for "framer-motion" reports nothing on a site that ships Framer Motion (it did,
on example.com). What survives is text the library itself relies on at
runtime: error messages, attribute and class names it writes into the page,
property names on its own objects. Every marker below was checked by
proof/libscan/prove.py against real minified builds of each library (esbuild,
webpack in production mode, and the library's own published file, old and new
major versions), and against the builds of every other library and two controls
with no library at all. A marker that is missing from its own library's builds,
or that shows up anywhere else, fails that proof. proof/libscan/result.json
holds the last run.

Verdict per library:
  found    two or more of its markers matched (each cited by file and byte offset)
  trace    exactly one matched: a lead to check by hand, not a finding
  absent   none matched in the files scanned. This says only that THESE files
           lack it. A chunk the page loads later (after a click, a route change,
           or deep in a scroll) was not scanned unless you captured it. For the
           widest net, capture with capture_net.py while using the page, then
           pass --capture.

Static only: files are read as text and nothing is executed, so this is safe on
any input. Downloaded files are untrusted: keep them in their own folder.
"""
import argparse
import hashlib
import json
import os
import re
import stat
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webfetch  # noqa: E402

MAX_FILE_BYTES = 20_000_000
MAX_TOTAL_BYTES = 300_000_000
MAX_FILES = 5000
NEED = 2  # markers needed for "found"
SEP = " | "  # joins a claim's markers; no marker may contain it (a test guards this)

# Proven by proof/libscan/prove.py; add a marker only after the proof passes with it.
LIBRARIES = [
    {"id": "react", "name": "React (react-dom)", "category": "framework",
     "markers": ["__reactInternalSnapshotBeforeUpdate", "_reactRootContainer", "unstable_scheduleCallback",
                 "UNSAFE_componentWillMount", "Minified React error #", "react.suspense_list"]},
    {"id": "vue", "name": "Vue 3", "category": "framework",
     "markers": ["__VUE_DEVTOOLS_GLOBAL_HOOK__", "__v_isRef", "__v_isReactive", "__vueParentComponent",
                 "__VUE_OPTIONS_API__", "__isKeepAlive"]},
    {"id": "framer-motion", "name": "Motion (framer-motion)", "category": "animation",
     "markers": ["unresolvedKeyframes", "onKeyframesResolved", "isResolvedFromDuration", "resolveNoneKeyframes",
                 "mirroredGenerator", "framerAppearId", "scrapeMotionValuesFromProps"]},
    {"id": "gsap", "name": "GSAP", "category": "animation",
     "markers": ["Missing plugin? gsap.registerPlugin()",
                 "onComplete,onUpdate,onStart,onRepeat,onReverseComplete,onInterrupt",
                 "to,from,fromTo,delayedCall,set,killTweensOf", "data-svg-origin",
                 "perspective,force3D,transformOrigin,svgOrigin"]},
    {"id": "gsap-scrolltrigger", "name": "GSAP ScrollTrigger", "category": "scroll",
     "markers": ["gsap-marker-", "pin-spacer", "scroller-start", "scroller-end", "_gsapAllow", "_isScrollT"]},
    {"id": "lenis", "name": "Lenis (smooth scroll)", "category": "scroll",
     "markers": ["data-lenis-prevent", "lenisVersion", "lenis-smooth", "lenis-stopped", "wheelMultiplier",
                 "syncTouchLerp"]},
    {"id": "locomotive-scroll", "name": "Locomotive Scroll", "category": "scroll",
     # v4 and v5 share little code: the first two are in both, then v4's, then v5's.
     "markers": ["is-inview", "setOutOfView", "has-scroll-smooth", "has-scroll-init", "c-scrollbar",
                 "data-scroll-section-id", "data-scroll-orientation", "data-scroll-to-href",
                 "_subscribeScrollElements"]},
    {"id": "three", "name": "Three.js", "category": "3d",
     "markers": ["__THREE__", "#define gl_FragColor pc_fragColor", "clipping_planes_pars_fragment",
                 "__webglMultisampledFramebuffer", "DEFAULT_MATRIX_WORLD_AUTO_UPDATE"]},
    {"id": "pixi.js", "name": "PixiJS", "category": "2d",
     "markers": ["_createBundleAssetId", "_defaultBundleIdentifierOptions", "_videoFrameRequestCallbackHandle",
                 "_isConnectedToTicker", "PixiJS"]},
    {"id": "swiper", "name": "Swiper", "category": "carousel",
     "markers": ["swiper-wrapper", "swiper-slide-active", "data-swiper-slide-index", "swiper-no-swiping",
                 "--swiper-centered-offset-before"]},
    {"id": "embla-carousel", "name": "Embla Carousel", "category": "carousel",
     "markers": ["scrollSnapList", "internalEngine", "optionsMediaQueries", "scrollContainLimit",
                 "slideSizesWithGaps", "snapsContained"]},
    {"id": "animejs", "name": "anime.js", "category": "animation",
     # v3 then v4: the rewrite shares almost nothing with the old code.
     "markers": ["suspendWhenDocumentHidden", "easeOutElastic(1, .5)", "isPathTargetInsideSVG", "setDashoffset",
                 "_absoluteStartTime", "_currentIteration", "_renderTransforms", "_hasFromValue"]},
    {"id": "lottie-web", "name": "Lottie (lottie-web)", "category": "animation",
     "markers": ["__[ANIMATIONDATA]__", "data-animation-path", "_createBaseContainerElements",
                 "BLACK_FLAG_CODE_POINT", "bodymovin"]},
    {"id": "hls.js", "name": "hls.js", "category": "video",
     "markers": ["MANIFEST_INCOMPATIBLE_CODECS_ERROR", "Trying to nudge playhead over buffer-hole",
                 "transmuxer-interface flush error", "hlsManifestParsed", "hlsMediaAttached"]},
    {"id": "video.js", "name": "Video.js", "category": "video",
     # 7 and 8, then 10 (@videojs/html and @videojs/react, a new codebase).
     "markers": ["Plugin must be sub-classed; not directly instantiated.", "vjs-tech", "vjs-big-play-button",
                 "vjs-control-bar", "@videojs/set-user-title", "@videojs/media-title", "[vjs-store]",
                 "@videojs/media/registered"]},
    {"id": "plyr", "name": "Plyr", "category": "video",
     "markers": ["plyr__preview-thumb__image-container", "plyr--fullscreen-enabled", '[data-plyr="fullscreen"]',
                 "plyr__controls"]},
    {"id": "barba", "name": "Barba.js", "category": "transitions",
     "markers": ["[@barba/core] No Barba container found", "[@barba/core] No Barba wrapper found", "x-barba",
                 "data-barba", "Transition error [page]"]},
    {"id": "jquery", "name": "jQuery", "category": "library",
     "markers": ["Thenable self-resolution", "_queueHooks", "_evalUrl",
                 "Boolean Number String Function Array Date RegExp Object Error Symbol", "X-Requested-With"]},
]


def scan_text(text):
    """Scan one text. Returns {lib_id: {"status", "markers": [{"marker", "offset"}]}}.
    The offset is the character index of the first occurrence."""
    out = {}
    for lib in LIBRARIES:
        hits = []
        for m in lib["markers"]:
            i = text.find(m)
            if i >= 0:
                hits.append({"marker": m, "offset": i})
        out[lib["id"]] = {"status": verdict(len(hits)), "markers": hits}
    return out


def verdict(n):
    return "found" if n >= NEED else ("trace" if n == 1 else "absent")


def scan_sources(sources):
    """sources: list of (label, text). Markers are pooled across all files, since
    one library's code is often split over several chunks; each hit keeps the
    file and offset where it was seen first."""
    pooled = {lib["id"]: {} for lib in LIBRARIES}
    for label, text in sources:
        for lib_id, res in scan_text(text).items():
            for h in res["markers"]:
                pooled[lib_id].setdefault(h["marker"], {"marker": h["marker"], "file": label, "offset": h["offset"],
                                                        "files": []})["files"].append(label)
    results = []
    for lib in LIBRARIES:
        hits = list(pooled[lib["id"]].values())
        results.append({"id": lib["id"], "name": lib["name"], "category": lib["category"],
                        "status": verdict(len(hits)), "matched": len(hits), "of": len(lib["markers"]),
                        "markers": hits})
    return results


# ---------- inputs ----------

def read_local(paths):
    """Regular files only, each read no further than the cap: a pipe named x.js in a downloaded or
    extracted folder blocked the read for ever, and a link to /dev/zero read until memory ran out."""
    sources, meta, total = [], [], 0
    for p in paths:
        try:
            fh = os.fdopen(os.open(p, os.O_RDONLY | os.O_NONBLOCK), "rb")   # a pipe opens without waiting
        except OSError as e:
            print(f"skip {p}: {e}", file=sys.stderr)
            continue
        with fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):
                print(f"skip {p}: not a regular file", file=sys.stderr)
                continue
            if st.st_size > MAX_FILE_BYTES:
                print(f"skip {p}: {st.st_size} bytes is over the {MAX_FILE_BYTES} cap", file=sys.stderr)
                continue
            if total + st.st_size > MAX_TOTAL_BYTES:
                print(f"stop: total over {MAX_TOTAL_BYTES} bytes", file=sys.stderr)
                break
            data = fh.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:              # it grew after its size was read
            print(f"skip {p}: over the {MAX_FILE_BYTES} byte cap", file=sys.stderr)
            continue
        total += len(data)
        sources.append((p, data.decode("utf-8", errors="replace")))
        meta.append({"source": p, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return sources, meta


def walk_dir(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".git")]
        for f in sorted(filenames):
            if f.endswith((".js", ".mjs", ".cjs")):
                found.append(os.path.join(dirpath, f))
                if len(found) >= MAX_FILES:
                    print(f"stop: more than {MAX_FILES} files under {root}", file=sys.stderr)
                    return found
    return found


def fetch_all(urls, timeout, verify):
    sources, meta, total = [], [], 0
    for u in urls[:MAX_FILES]:
        try:
            data = webfetch.get(u, timeout, verify, MAX_FILE_BYTES, check=True)[3]
        except Exception as e:  # noqa: BLE001 - one bad script must not stop the scan; report it
            print(f"skip {u}: {e}", file=sys.stderr)
            meta.append({"source": u, "error": str(e)[:160]})
            continue
        if len(data) > MAX_FILE_BYTES:
            print(f"skip {u}: over the {MAX_FILE_BYTES} byte cap", file=sys.stderr)
            continue
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            print(f"stop: total over {MAX_TOTAL_BYTES} bytes", file=sys.stderr)
            break
        sources.append((u, data.decode("utf-8", errors="replace")))
        meta.append({"source": u, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return sources, meta


def fetch_page(url, timeout, verify):
    """(html, final url): at most MAX_FILE_BYTES of the page, read as a stream so a huge or
    endless page cannot fill memory, its redirects followed unread. Only its script tags are wanted."""
    final_url, _, _, data, _ = webfetch.get(url, timeout, verify, MAX_FILE_BYTES, check=True)
    return data[:MAX_FILE_BYTES].decode("utf-8", errors="replace"), final_url


SRC = re.compile(r"\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
REL_PRELOAD = re.compile(r"\brel\s*=\s*[\"']?(?:modulepreload|preload)\b", re.I)
HREF = re.compile(r"\bhref\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s\"'>]+))", re.I)
AS_SCRIPT = re.compile(r"\bas\s*=\s*[\"']?script\b", re.I)
ASCII_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}   # str.lower() can change the length


def _tag(low, name, pos):
    """(start of its attributes, its '>') for the next <name ...> tag at or after pos, or None.
    A tag ends at its first '>' and every search moves on from there, so the read is linear.
    Regexes that ran from each opener to a far '>' or '</script>' read to the end of the page
    from every unclosed opener: 160 KB of '<script ' took 57 s, hours at the 20 MB cap."""
    opener = "<" + name
    while True:
        i = low.find(opener, pos)
        if i == -1:
            return None
        j = i + len(opener)
        if j < len(low) and (low[j].isspace() or low[j] in "/>"):
            k = low.find(">", j)
            return None if k == -1 else (j, k)
        pos = j                                       # <scripts>, <link-x>: another tag


def page_scripts(html, base):
    low = html.translate(ASCII_LOWER)   # same length as html, so a position in one is a position in the other
    urls, inline, pos = [], [], 0
    while (t := _tag(low, "script", pos)):
        j, k = t
        m = SRC.search(html, j, k)
        if m:
            urls.append(urllib.parse.urljoin(base, m.group(1)))
            pos = k + 1
            continue
        close = low.find("</script", k + 1)
        if close == -1:
            break
        inline.append(html[k + 1:close])
        pos = close + len("</script")
    pos = 0
    while (t := _tag(low, "link", pos)):
        tag = html[t[0]:t[1]]
        if REL_PRELOAD.search(tag):
            h = HREF.search(tag)
            href = h and next(g for g in h.groups() if g)
            if href and (href.split("?")[0].endswith((".js", ".mjs")) or AS_SCRIPT.search(tag)):
                urls.append(urllib.parse.urljoin(base, href))
        pos = t[1] + 1
    inline = "\n;\n".join(inline)
    seen, unique = set(), []
    for u in urls:
        if u not in seen and u.startswith(("http://", "https://")):
            seen.add(u)
            unique.append(u)
    return unique, inline


def capture_scripts(path):
    with open(path, "r", encoding="utf-8") as fh:
        cap = json.load(fh)
    urls = [e["url"] for e in cap.get("entries", []) if e.get("resource_type") == "script"
            and (e.get("status") or 0) < 400 and e.get("url", "").startswith(("http://", "https://"))]
    return list(dict.fromkeys(urls))


# ---------- output ----------

def to_claims(results, meta):
    files = [m for m in meta if "bytes" in m]
    kb = sum(m["bytes"] for m in files) // 1024
    claims = []
    for r in results:
        cid = "gt-lib-" + re.sub(r"[^a-z0-9]+", "-", r["id"]).strip("-")
        if r["status"] == "absent":
            claims.append({"kind": "ground_truth", "id": cid,
                           "statement": f"No {r['name']} marker is present in the {len(files)} scanned "
                                        f"JavaScript file(s) ({kb} KB).",
                           "evidence": {"tool": "libscan.py", "locator": f"{len(files)} files, {kb} KB",
                                        "literal": f"0 of {r['of']} markers"}})
            continue
        first = r["markers"][0]
        name = os.path.basename(urllib.parse.urlparse(first["file"]).path) or first["file"]
        more = f" (+{len(r['markers']) - 1} more)" if len(r["markers"]) > 1 else ""
        word = "markers are" if r["matched"] != 1 else "marker is"
        claims.append({"kind": "ground_truth", "id": cid,
                       "statement": f"{r['matched']} of {r['of']} {r['name']} {word} present in the shipped "
                                    f"JavaScript.",
                       "evidence": {"tool": "libscan.py", "locator": f"{name} @ char {first['offset']}{more}",
                                    "literal": SEP.join(h["marker"] for h in r["markers"])}})
    return claims


def main():
    ap = argparse.ArgumentParser(description="Name the libraries inside shipped JavaScript (static, minify-proof).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--files", nargs="+", help="Local JavaScript files.")
    src.add_argument("--dir", help="A folder of JavaScript files (searched recursively).")
    src.add_argument("--capture", help="A capture_net.py JSON; its script URLs are fetched and scanned.")
    src.add_argument("--url", help="A page; its <script> and modulepreload files are fetched (no JS runs).")
    ap.add_argument("--all", action="store_true", help="List absent libraries too.")
    ap.add_argument("--emit-claims", action="store_true", help="Print dossier ground_truth claims.")
    ap.add_argument("--out", help="Write the full JSON result here.")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--insecure", action="store_true", help="Skip TLS verification.")
    args = ap.parse_args()

    if args.files or args.dir:
        paths = args.files or walk_dir(args.dir)
        sources, meta = read_local(paths)
    elif args.capture:
        sources, meta = fetch_all(capture_scripts(args.capture), args.timeout, not args.insecure)
    else:
        try:
            html, final_url = fetch_page(args.url, args.timeout, not args.insecure)
        except Exception as e:  # noqa: BLE001
            print(f"Fetch failed: {e}", file=sys.stderr)
            sys.exit(1)
        urls, inline = page_scripts(html, final_url)
        sources, meta = fetch_all(urls, args.timeout, not args.insecure)
        if inline.strip():
            sources.append(("(inline scripts)", inline))
            meta.append({"source": "(inline scripts)", "bytes": len(inline.encode("utf-8")),
                         "sha256": hashlib.sha256(inline.encode("utf-8")).hexdigest()})

    if not sources:
        print("No JavaScript to scan.", file=sys.stderr)
        sys.exit(1)
    results = scan_sources(sources)
    if args.emit_claims:
        print(json.dumps(to_claims([r for r in results if args.all or r["status"] != "absent"], meta), indent=2))
    else:
        files = [m for m in meta if "bytes" in m]
        print(f"Scanned {len(files)} file(s), {sum(m['bytes'] for m in files) // 1024} KB")
        for r in results:
            if r["status"] == "absent" and not args.all:
                continue
            where = ""
            if r["markers"]:
                h = r["markers"][0]
                where = f"  first: {os.path.basename(urllib.parse.urlparse(h['file']).path) or h['file']} @ {h['offset']}"
            print(f"  {r['status']:6} {r['name']:28} {r['matched']}/{r['of']}{where}")
        print("absent = not in these files; lazily loaded chunks were scanned only if captured.")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"inputs": meta, "libraries": results}, fh, indent=1)
        print(f"Wrote {args.out}", file=sys.stderr if args.emit_claims else sys.stdout)


if __name__ == "__main__":
    main()
