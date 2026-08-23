#!/usr/bin/env python3
"""Department router: from a work phrase to the department, its gate and its tools.

Post is not one job, it is eleven jobs in a fixed order, and most damage comes
from doing one of them out of turn. This tool answers three questions:

  what department does this phrase belong to      route.py find "the phone screen slides"
  what has to be true before the next step runs   route.py gate colour
  what is the whole order                         route.py spine

Every department names three things: the engines in this skill, the SKILLS this
skill wraps rather than reimplements, and its reference pack. A department with
no engine of its own is not a gap; it means an existing skill already does that
work properly and post's job is to sequence it and prove it.

Usage:
  python route.py spine
  python route.py list
  python route.py find "supers are hard to read on the pale shot"
  python route.py show colour
  (add --json for structured output)
"""
from __future__ import annotations

import re
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _common as C  # noqa: E402

# The order IS the method. `step` is the position in the spine; `gate` is what
# must be proved before the next step is allowed to start.
DEPARTMENTS = {
    "ingest": {
        "step": 1,
        "name": "Ingest and spec",
        "answers": "What actually arrived, and what has to go out.",
        "gate": "True resolution, true frame rate (native or conformed), colour "
                "tags, codec, bit depth, chroma, audio layout, duration and the "
                "deliverable list, every one of them read off the file rather "
                "than off the filename or the email.",
        "engines": ["spec.py", "prove.py"],
        "wraps": [],
        "reference": "00_departments.md, 07_delivery.md",
        "keywords": ["ingest", "spec", "what is this file", "probe", "resolution",
                     "frame rate", "framerate", "fps", "codec", "bit depth",
                     "colour space", "color space", "colour tag", "color tag",
                     "metadata", "received", "arrived", "delivery spec",
                     "aspect", "duration", "container", "sample rate"],
    },
    "repair": {
        "step": 2,
        "name": "Picture repair",
        "answers": "Frames that stick, judder, teleport or step in colour.",
        "gate": "Held out frames score the rebuild, and all three of the scores "
                "are read, not the error alone. Frame rate, frame count, "
                "duration and constant timestamp spacing verified on the "
                "delivered file.",
        "engines": ["spec.py"],
        "wraps": ["colorgrade (track three)", "FRAME_REPAIR project"],
        "reference": "06_compositing.md, 08_failures.md",
        "keywords": ["stick", "sticks", "sticking", "stall", "stalls", "frozen",
                     "judder", "teleport", "lurch", "cadence", "duplicate frames",
                     "frame interpolate", "interpolation", "retime", "smooth motion",
                     "choppy", "stutter", "pulldown", "dropped frames"],
    },
    "resolution": {
        "step": 3,
        "name": "Resolution and restoration",
        "answers": "Softness, compression damage, and the delivery raster.",
        "gate": "The source's EFFECTIVE resolution measured before anything is "
                "enlarged, because a file that does not carry its own raster "
                "cannot be enlarged into one; the frame rate, frame count, "
                "duration, audio and colour tags identical on the result; and "
                "the result proved TEMPORALLY, because a stills upscaler run "
                "down a clip re-invents its detail every picture and no still "
                "frame shows it.",
        "engines": ["upres.py (routes, effres, route, temporal, verify, "
                    "superscale)", "spec.py"],
        "wraps": ["upscale (stills only)", "davinci-resolve (Super Scale)",
                  "colorgrade (track three)"],
        "reference": "10_resolution.md",
        "keywords": ["upscale", "upscaling", "upres", "enlarge", "enlarged",
                     "blow up", "blown up", "resolution", "4k", "uhd", "8k",
                     "2k", "hd", "1080", "720", "soft", "softness", "sharpen",
                     "sharper", "restore", "restoration", "denoise", "noisy",
                     "compression", "compressed", "artefacts", "artifacts",
                     "blocky", "macroblocking", "banding", "mushy", "boil",
                     "boiling", "crawl", "crawling", "worms", "super scale",
                     "superscale", "esrgan", "seedvr", "magnific", "downscale",
                     "downscaled", "bigger", "small", "low res", "lowres",
                     "pixelated", "pixellated", "make it sharper", "boils",
                     "crawls", "shimmer", "shimmering", "swimming", "wormy",
                     "invented detail", "fake detail", "plasticky", "mush"],
    },
    "conform": {
        "step": 4,
        "name": "Conform",
        "answers": "The cut, the timeline, handles, timecode.",
        "gate": "Frame count and duration agree with the edit, every join is "
                "accounted for, and the timecode arithmetic is done in the "
                "delivery's own rate and drop frame convention.",
        "engines": ["conform.py"],
        "wraps": ["davinci-resolve", "video-editing"],
        "reference": "01_conform.md",
        "keywords": ["conform", "edl", "aaf", "fcpxml", "otio", "xml", "timeline",
                     "timecode", "drop frame", "handles", "shot list", "cut list",
                     "reel", "online", "offline", "relink", "assembly", "trim",
                     "in point", "out point", "edit"],
    },
    "comp": {
        "step": 5,
        "name": "Compositing and inserts",
        "answers": "Screen comps, clean plates, repaints, object removal, inserts.",
        "gate": "The plate's cadence is measured BEFORE anything is tracked; the "
                "motion model is chosen by cross validation, not assumed; the "
                "track is certified by a second route that shares no assumption "
                "with the first; every occluded edge is declared UNMEASURED and "
                "any rigid fill is proved on held out frames; and every check on "
                "the result is anchored in the PLATE, never in the model the "
                "build used. A keyed matte cannot audit the track that put "
                "content inside it.",
        "engines": ["comp.py (cadence, track, quad, aspect, key, despill, "
                    "triangulate, warp, insert, grain, holdout, verify)",
                    "prove.py"],
        "wraps": ["colorgrade (track two)", "image-editing", "gimp", "inkscape",
                  "background-removal", "blender", "img2threejs"],
        "reference": "06_compositing.md",
        "keywords": ["comp", "composite", "compositing", "screen", "monitor",
                     "phone", "insert", "track", "tracking", "matte", "key",
                     "green screen", "greenscreen", "chroma key", "roto",
                     "clean plate", "remove", "repaint", "paint out", "patch",
                     "replace the screen", "wall logo", "overlay", "alpha",
                     "premultiplied", "premultiply", "plate", "warp", "homography",
                     "despill", "spill", "light wrap", "grain", "regrain",
                     "corner pin", "cadence", "judder", "aspect", "anisotropy",
                     "vanishing point", "horizon", "occlusion", "occluded",
                     "hull", "curved screen", "bezel", "triangulation",
                     "clean plate pass", "garbage matte", "choke", "edge",
                     "bleed", "bleeding", "fringe", "fringing", "dark spots",
                     "dark spot", "dark edge", "notch", "halo", "matte line",
                     "sliding", "slipping", "floating", "sticker", "pasted on",
                     "doesn't sit", "does not sit", "wrong shape", "stretched",
                     "squashed", "circle", "round", "red and blue", "channels"],
    },
    "colour": {
        "step": 6,
        "name": "Colour",
        "answers": "Balance, shot to shot match, the look, one LUT per shot.",
        "gate": "dE2000 consistency measured before and after, and the list of "
                "corrections that hit a safety cap declared rather than hidden.",
        "engines": ["supers.py contrast (Lab maths only)"],
        "wraps": ["colorgrade (tracks one, two and four)", "color-palette"],
        "reference": "02_colour.md",
        "keywords": ["grade", "grading", "colour", "color", "colour grade",
                     "look", "lut", "cube", "balance", "white balance", "match",
                     "consistency", "aces", "ocio", "rec709", "log", "contrast",
                     "saturation", "lift gamma gain", "shot match", "dctl"],
    },
    "supers": {
        "step": 7,
        "name": "Supers and titles",
        "answers": "Type on picture: supers, lower thirds, end boards, CTAs.",
        "gate": "Safe area respected, and the ink separated from its ACTUAL "
                "ground in CIE Lab, per block, measured at 1:1 on the graded "
                "picture the audience will see.",
        "engines": ["supers.py"],
        "wraps": ["remotion", "logo-animate", "font-tools", "ocr",
                  "algorithmic-art"],
        "reference": "03_supers.md",
        "keywords": ["super", "supers", "title", "titles", "lower third",
                     "end board", "endboard", "cta", "caption card", "type",
                     "typography", "font", "legible", "legibility", "safe area",
                     "safe title", "text on screen", "kinetic", "credits",
                     "logo", "endcard", "straps", "strapline"],
    },
    "subs": {
        "step": 8,
        "name": "Subtitles and captions",
        "answers": "Timed text: sidecars, burn ins, localisation, access captions.",
        "gate": "Reading speed, line count, line length, minimum and maximum "
                "duration, gap between events, and no collision with a super.",
        "engines": ["subs.py"],
        "wraps": ["translate", "voice", "ocr"],
        "reference": "04_subtitles.md",
        "keywords": ["subtitle", "subtitles", "srt", "vtt", "webvtt", "ttml",
                     "itt", "sdh", "ccap", "closed caption", "captions",
                     "timed text", "cps", "reading speed", "burn in", "burnt in",
                     "localisation", "localization", "translate the film",
                     "forced narrative", "dubbing card"],
    },
    "sound": {
        "step": 9,
        "name": "Sound",
        "answers": "Mix, stems, and the loudness the platform will measure.",
        "gate": "Integrated loudness, true peak and loudness range measured "
                "against the named profile, on the FINAL cut, because every "
                "edit moves the integrated number.",
        "engines": ["audio.py"],
        "wraps": ["audio-editing", "audio-analysis", "stems", "sound-design",
                  "text-to-speech", "midi-to-audio"],
        "reference": "05_sound.md",
        "keywords": ["sound", "audio", "mix", "loudness", "lufs", "lkfs",
                     "true peak", "dbtp", "r128", "ebu", "normalise", "normalize",
                     "stems", "voice over", "vo", "music", "sfx", "dialogue",
                     "channel layout", "5.1", "stereo", "mute", "level"],
    },
    "master": {
        "step": 10,
        "name": "Master",
        "answers": "Build, splice, tag, and prove the file that ships.",
        "gate": "The null version encode is byte identical to the delivered "
                "predecessor, colour tags are uniform across every join, and "
                "the changed frame list matches what the planner PREDICTED.",
        "engines": ["prove.py", "spec.py"],
        "wraps": ["video-editing", "davinci-resolve", "upscale"],
        "reference": "07_delivery.md",
        "keywords": ["master", "render", "encode", "splice", "concat", "join",
                     "prores", "dnxhd", "h264", "h265", "mxf", "imf", "bake",
                     "output", "final file", "export", "mov", "deliverable file",
                     "framemd5", "hash", "checksum", "byte identical"],
    },
    "deliver": {
        "step": 11,
        "name": "Deliverables",
        "answers": "Everything that leaves the building, and its paperwork.",
        "gate": "Every item on the delivery list present and proved, every "
                "sidecar named to the house convention, and a viewing copy "
                "labelled as a viewing copy.",
        "engines": ["deliver.py", "spec.py", "audio.py"],
        "wraps": ["compress", "cloud-sync", "presentations", "email"],
        "reference": "07_delivery.md",
        "keywords": ["deliver", "delivery", "deliverable", "textless", "sidecar",
                     "package", "hand off", "handoff", "send", "upload",
                     "viewing copy", "proxy", "review copy", "spec sheet",
                     "as-11", "dpp", "imf", "naming convention", "qc"],
    },
    "versions": {
        "step": 12,
        "name": "Versions and archive",
        "answers": "What was delivered, what changed, and what can be restored.",
        "gate": "Survivors verified against their record BEFORE anything is "
                "condemned, condemned files hashed on the way out, and every "
                "restore path proved to exist.",
        "engines": ["archive.py", "prove.py"],
        "wraps": ["compress", "cloud-sync"],
        "reference": "07_delivery.md, 08_failures.md",
        "keywords": ["version", "versions", "revision", "v2", "v3", "archive",
                     "backup", "delete", "clear", "clean up", "disk space",
                     "roll back", "rollback", "restore", "supersede", "old master",
                     "hard link", "hardlink", "overwrite", "undo"],
    },
}

_WORD = re.compile(r"[a-z0-9]+")


def _fold(text):
    """Lowercase, strip punctuation, so hurried typing still routes."""
    return " ".join(_WORD.findall(text.lower()))


def find(query):
    """Route a work phrase to departments, scored by keyword overlap.

    Longer keywords score higher: 'green screen' matching is worth more than
    'screen' matching, because the specific phrase carries more of the intent.
    """
    q = _fold(query)
    if not q.strip():
        raise ValueError("Give a phrase to route.")
    hits = []
    for slug, d in DEPARTMENTS.items():
        matched, score = [], 0
        for kw in d["keywords"]:
            folded = _fold(kw)
            if folded and re.search(rf"\b{re.escape(folded)}\b", q):
                matched.append(kw)
                score += len(folded.split())
        if score:
            hits.append({"slug": slug, "step": d["step"], "name": d["name"],
                         "score": score, "matched": matched,
                         "gate": d["gate"], "engines": d["engines"],
                         "wraps": d["wraps"]})
    hits.sort(key=lambda h: (-h["score"], h["step"]))
    return hits


def spine():
    """The order, with each step's gate."""
    return [dict(slug=s, **{k: v for k, v in d.items() if k != "keywords"})
            for s, d in sorted(DEPARTMENTS.items(), key=lambda kv: kv[1]["step"])]


def _print_dept(slug, d):
    print(f"{d['step']:>2}. {d['name']}  [{slug}]")
    print(f"    {d['answers']}")
    print(f"    Gate: {d['gate']}")
    if d["engines"]:
        print(f"    Engines: {', '.join(d['engines'])}")
    if d["wraps"]:
        print(f"    Wraps: {', '.join(d['wraps'])}")
    print(f"    Reference: {d['reference']}")


def main(argv=None):
    ap = C.parser_for(__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("spine", help="The whole order with its gates")
    C.add_json(sp)

    ls = sub.add_parser("list", help="Departments, one line each")
    C.add_json(ls)

    fi = sub.add_parser("find", help="Route a work phrase")
    fi.add_argument("query")
    C.add_json(fi)

    sh = sub.add_parser("show", help="One department in full")
    sh.add_argument("slug")
    C.add_json(sh)

    ga = sub.add_parser("gate", help="What must be proved before the next step")
    ga.add_argument("slug")
    C.add_json(ga)

    args = ap.parse_args(argv)

    if args.cmd == "spine":
        rows = spine()
        if args.json:
            return C.emit(rows, True)
        print("The order. Each step's gate must pass before the next one starts.\n")
        for r in rows:
            _print_dept(r["slug"], r)
            print()
        print("Repair before colour, always. Supers on the graded picture. "
              "Subtitles after supers. Loudness last.")
    elif args.cmd == "list":
        if args.json:
            return C.emit({s: d["name"] for s, d in DEPARTMENTS.items()}, True)
        for r in spine():
            print(f"{r['step']:>2}. {r['slug']:<9} {r['name']}: {r['answers']}")
    elif args.cmd == "find":
        hits = find(args.query)
        if args.json:
            return C.emit(hits, True)
        if not hits:
            print("No department matched. Run 'spine' for the order, or start at "
                  "ingest: nothing can be decided before the file is measured.")
            return 0
        print(f"Routing: {args.query}\n")
        for h in hits:
            print(f"  {h['step']:>2}. {h['name']} (score {h['score']}) "
                  f"matched {', '.join(h['matched'])}")
            print(f"      Gate: {h['gate']}")
            if h["engines"]:
                print(f"      Engines: {', '.join(h['engines'])}")
        first = hits[0]
        earlier = [d for d in spine() if d["step"] < first["step"]]
        if earlier:
            print(f"\n  Before this runs, {len(earlier)} step(s) come first: "
                  + ", ".join(d["slug"] for d in earlier)
                  + ". Confirm their gates have passed.")
    elif args.cmd in ("show", "gate"):
        d = DEPARTMENTS.get(args.slug)
        if d is None:
            raise ValueError(f"Unknown department {args.slug}. "
                             f"Available: {', '.join(DEPARTMENTS)}")
        if args.cmd == "gate":
            payload = {"slug": args.slug, "step": d["step"], "gate": d["gate"]}
            return C.emit(payload, args.json,
                          lambda p: print(f"{d['name']} gate:\n  {d['gate']}"))
        if args.json:
            return C.emit(dict(slug=args.slug, **d), True)
        _print_dept(args.slug, d)
    return 0


if __name__ == "__main__":
    sys.exit(C.main_guard(main))
