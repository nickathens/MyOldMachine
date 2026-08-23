#!/usr/bin/env python3
"""The dated standards picture, with a freshness check.

A delivery spec is a moving target and the most expensive mistake in post is
applying last year's number. This tool is DATED SCAFFOLDING, not authority:
every domain carries the date it was last verified against the primary
document, and the primary document is named. Read it, then confirm before you
bake a number into a master.

Two kinds of number live here and they are not equal:

  a STANDARD'S CONSTANT may default   -23.0 LUFS, -1 dBTP, 93 and 90 per cent
  a PROJECT'S FACT may never default  this film's rate, raster, tags, targets

Anything tagged [VERIFY] came from memory or a secondary source and must be
confirmed in the primary text before it is used on paid work.

Usage:
  python standards.py list
  python standards.py show loudness
  python standards.py show --all
  python standards.py freshness
  python standards.py const safe.action
  (add --json for structured output)
"""
from __future__ import annotations

import datetime as dt
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _common as C  # noqa: E402

# Days after verification beyond which a domain is called stale. A quarter is
# generous for a picture that moves as fast as platform delivery specs.
STALE_DAYS = 120

DOMAINS = {
    "loudness": {
        "title": "Loudness: EBU R128 and the platform targets",
        "as_of": "2026-08-23",
        "verified": True,
        "state": (
            "EBU R128 is at version 5, Geneva, November 2023. Programme "
            "Loudness normalises to a Target Level of -23.0 LUFS. The often "
            "quoted plus or minus 1.0 LU is NOT a general tolerance: R128 "
            "allows it only 'where attaining the Target Level is not "
            "achievable practically (for example, live programmes)'. For a "
            "file based deliverable the target is -23.0, and the only other "
            "tolerance in the document is plus or minus 0.2 LU for "
            "measurement error in a QC workflow. True Peak shall not exceed "
            "-1 dBTP in production (linear audio), measurement tolerance plus "
            "or minus 0.3 dB, and distribution systems with data reduction may "
            "set a LOWER ceiling (see EBU Tech 3344). Measurement is the level "
            "gated method of ITU-R BS.1770 equation 7, relative gate -10 LU, "
            "over the signal IN ITS ENTIRETY, explicitly without emphasis on "
            "speech. LUFS and LKFS are the same unit. Four supplements: s1 "
            "short form (adverts, promos), s2 streaming, s3 radio, s4 "
            "cinematic content."
        ),
        "for_the_cutting_room": (
            "R128 is ungated by dialogue and the big VOD platforms are not, so "
            "the two numbers are not comparable and converting between them by "
            "arithmetic is wrong. Measure the final cut: every trim moves the "
            "integrated number. Normalise LAST."
        ),
        "constants": {
            "r128.target_i": -23.0,
            "r128.tol_live": 1.0,
            "r128.tol_qc": 0.2,
            "r128.max_tp": -1.0,
            "r128.gate_rel": -10.0,
        },
        "sources": ["EBU R 128-2023 v5, tech.ebu.ch/publications/r128, "
                    "clauses g to v, read in full 2026-08-23",
                    "ITU-R BS.1770 eq.(7)", "EBU Tech 3341, 3342, 3344"],
    },
    "loudness-platforms": {
        "title": "Loudness: what the platforms actually measure",
        "as_of": "2026-08-23",
        "verified": True,
        "state": (
            "Netflix specifies -27 LKFS with a tolerance of plus or minus 2 "
            "LU, DIALOG GATED, true peak not above -2 dBTP, measured over the "
            "full programme per ITU-R BS.1770-1. This applies to original "
            "language mixes, dubs and audio description alike. US broadcast is "
            "-24 LKFS under ATSC A/85. Music streaming clusters near -14 LUFS "
            "and is a different world with different rules. [VERIFY] each "
            "platform at the moment of delivery: these move."
        ),
        "for_the_cutting_room": (
            "Ask which target and which GATE before mixing, not after. A mix "
            "delivered at -23 ungated is not a -27 dialog gated mix minus 4."
        ),
        "constants": {
            "netflix.target_i": -27.0,
            "netflix.tol": 2.0,
            "netflix.max_tp": -2.0,
            "atsc_a85.target_i": -24.0,
        },
        "sources": ["Netflix Sound Mix Specifications and Best Practices v1.3, "
                    "partnerhelp.netflixstudios.com, fetched 2026-08-23",
                    "ATSC A/85 [VERIFY]"],
    },
    "safe-areas": {
        "title": "Safe areas: EBU R95",
        "as_of": "2026-08-23",
        "verified": True,
        "state": (
            "EBU R95, Revision 1, Geneva, July 2016. Two areas: the Action "
            "Safe Area at an inset of 3.5 per cent of picture width and height "
            "per edge, and the Graphics Safe Area at 5 per cent per edge. That "
            "is 93 per cent and 90 per cent of the full raster, concentric. On "
            "1920x1080: action safe 1786x1004 (3.5 per cent of 1920 is 67 "
            "pixels, a figure the document's own errata confirms), graphics "
            "safe 1728x972. On 3840x2160: 3571x2008 and 3456x1944. R95 also "
            "carries per raster FIRST and LAST safe line and pixel numbers for "
            "576i, 720p, 1080i, 1080p, 2160p and 4320p, and those live in the "
            "figures as images, so read them out of the PDF for a broadcast "
            "deliverable rather than trusting the percentage rounding."
        ),
        "for_the_cutting_room": (
            "Safe area is necessary and never sufficient. A super inside the "
            "graphics safe area can still be unreadable; that is a separate "
            "measurement, and it is the one clients notice. See "
            "reference/03_supers.md."
        ),
        "constants": {"safe.action": 0.93, "safe.title": 0.90,
                      "safe.action_inset": 0.035, "safe.title_inset": 0.05},
        "sources": ["EBU R 95 rev 1, July 2016, "
                    "tech.ebu.ch/files/live/sites/tech/files/shared/r/r095-2016_2.pdf, "
                    "read 2026-08-23", "SMPTE ST 2046-1 [VERIFY]"],
    },
    "subtitles": {
        "title": "Timed text: formats and the reading speed numbers",
        "as_of": "2026-08-23",
        "verified": True,
        "state": (
            "IMSC 1.2 (TTML Profiles for Internet Media Subtitles and "
            "Captions) is a W3C Recommendation of 4 August 2020, defining a "
            "Text Profile and an Image Profile which a document cannot satisfy "
            "at once. Whether a later text profile revision has advanced is "
            "[VERIFY] at w3.org before quoting a version. Netflix's English "
            "style guide gives 42 characters per line, a maximum of two lines, "
            "20 characters per second for adult programmes and 17 for "
            "children's. Those numbers live in the PER LANGUAGE guides, not in "
            "the General Requirements page, so cite the language guide. The "
            "General Requirements carry the durations: minimum five sixths of "
            "a second, maximum 7 seconds, two lines, centre justified top or "
            "bottom. Greek and other languages have their own character and "
            "CPS numbers and they are not the English ones."
        ),
        "for_the_cutting_room": (
            "State the CPS counting rule alongside any CPS number: whether "
            "spaces count, and whether the line break counts. Two tools "
            "disagreeing on that alone shift a reading speed by several per "
            "cent and fail a file that passes."
        ),
        "constants": {
            "netflix_en.chars_per_line": 42,
            "netflix_en.max_lines": 2,
            "netflix_en.cps_adult": 20,
            "netflix_en.cps_children": 17,
            "netflix.min_duration_s": 5.0 / 6.0,
            "netflix.max_duration_s": 7.0,
        },
        "sources": ["W3C TTML IMSC 1.2 Recommendation, 4 Aug 2020, "
                    "w3.org/TR/ttml-imsc1.2/, fetched 2026-08-23",
                    "Netflix English Timed Text Style Guide, fetched 2026-08-23",
                    "Netflix Timed Text General Requirements [VERIFY per language]"],
    },
    "colour": {
        "title": "Colour management: how a file declares itself",
        "as_of": "2026-08-23",
        "verified": False,
        "state": (
            "A delivery declares four things and they are separate: primaries, "
            "transfer, matrix and range. Rec.709 HD is primaries bt709, "
            "transfer bt709 (the display side of which is BT.1886), matrix "
            "bt709, range tv (limited, 16 to 235 at 8 bit). UHD SDR keeps "
            "bt709 primaries only if the deliverable says so; HDR10 is bt2020 "
            "primaries, smpte2084 transfer, bt2020nc matrix, with MaxCLL and "
            "MaxFALL as separate metadata; HLG is arib-std-b67. ACES 2.0 "
            "support landed in OpenColorIO 2.4.2 and OCIO 2.5 ships built in "
            "ACES 2.0 Studio and CG configs needing no external LUT files. "
            "[VERIFY] the OCIO version numbers at opencolorio.readthedocs.io."
        ),
        "for_the_cutting_room": (
            "An UNTAGGED file is a delivery fault, not a neutral one: the "
            "player decides, and it will not always decide the way the "
            "colourist did. ProRes written from a PNG sequence with no "
            "-colorspace comes out untagged; spliced into a tagged film with "
            "-c copy it changes the master's colour metadata partway through "
            "and every frame hash still matches."
        ),
        "constants": {},
        "sources": ["ITU-R BT.709-6", "ITU-R BT.1886", "ITU-R BT.2100",
                    "SMPTE ST 2084", "opencolorio.readthedocs.io [VERIFY]"],
    },
    "delivery": {
        "title": "Masters, containers and the broadcast packages",
        "as_of": "2026-08-23",
        "verified": False,
        "state": (
            "IMF is SMPTE ST 2067; Application 2E is the common one for "
            "finished programmes, and IMF Application DPP is RDD 59, which "
            "includes a ProRes variant with HLG and an audio description "
            "control track. UK broadcast has taken AS-11 UK DPP (MXF OP1a) "
            "since 1 October 2014. Advertising in the UK goes through Clearcast "
            "and Adstream style routes with their own specs. ProRes 422 HQ and "
            "4444 remain the working master currencies outside broadcast, "
            "DNxHR the Avid side. [VERIFY] every one of these against the "
            "client's own delivery document, which always wins."
        ),
        "for_the_cutting_room": (
            "The delivery document is a project fact. Never infer it from the "
            "codec of the file you were sent."
        ),
        "constants": {},
        "sources": ["SMPTE ST 2067 family [VERIFY]", "SMPTE RDD 59 [VERIFY]",
                    "thedpp.com/specs [VERIFY]"],
    },
    "interchange": {
        "title": "Timeline interchange: EDL, AAF, FCPXML, OTIO",
        "as_of": "2026-08-23",
        "verified": True,
        "state": (
            "OpenTimelineIO 0.18.1 is current on PyPI, Apache 2.0, Python "
            "above 3.9, and its licence is clean for paid client work. The AAF "
            "adapter is a SEPARATE repository from core and has to be "
            "installed on purpose. CMX 3600 EDL remains the lowest common "
            "denominator and carries no effects, no multi layer video and no "
            "colour. FCPXML and AAF carry more and agree less."
        ),
        "for_the_cutting_room": (
            "An EDL is a claim about a timeline, not the timeline. Check its "
            "record times are contiguous and that its total matches the cut "
            "you were given, before relinking anything."
        ),
        "constants": {},
        "sources": ["pypi.org/pypi/OpenTimelineIO/json, read 2026-08-23",
                    "CMX 3600 EDL conventions [VERIFY]"],
    },
    "rates": {
        "title": "Frame rates, drop frame and conform arithmetic",
        "as_of": "2026-08-23",
        "verified": True,
        "state": (
            "The NTSC family rates are exact ratios: 24000/1001, 30000/1001, "
            "60000/1001. 23.976 and 29.97 are ROUNDINGS and a timecode built "
            "on the rounding drifts. Drop frame is a LABELLING scheme, not a "
            "dropped picture: at 30000/1001 it skips the labels :00 and :01 at "
            "the top of every minute except every tenth minute, so a day of "
            "drop frame timecode is 2589408 frames and one hour of it is "
            "107892. Non drop at the same rate runs 3.6 seconds slow per hour "
            "against the clock. 25 and 24 have no drop frame."
        ),
        "for_the_cutting_room": (
            "A generated plate is often a higher rate conformed down by "
            "DROPPING pictures, which makes the whole scene lurch on a fixed "
            "beat. That lurch is real motion the comp must follow, not noise "
            "to smooth away."
        ),
        "constants": {"df.frames_per_day_2997": 2589408,
                      "df.frames_per_hour_2997": 107892},
        "sources": ["SMPTE ST 12-1 timecode [VERIFY]",
                    "arithmetic self tested in selftest.py"],
    },
}


def freshness(today=None):
    """Age of each domain's verification, and whether it has gone stale."""
    today = today or dt.date.today()
    rows = []
    for slug, d in DOMAINS.items():
        as_of = dt.date.fromisoformat(d["as_of"])
        age = (today - as_of).days
        rows.append({"domain": slug, "as_of": d["as_of"], "age_days": age,
                     "verified_against_primary": d["verified"],
                     "stale": age > STALE_DAYS})
    return rows


def constant(key):
    """Look up one standard constant by dotted name, with its domain."""
    for slug, d in DOMAINS.items():
        if key in d["constants"]:
            return {"key": key, "value": d["constants"][key], "domain": slug,
                    "as_of": d["as_of"], "sources": d["sources"]}
    known = sorted(k for d in DOMAINS.values() for k in d["constants"])
    raise ValueError(f"Unknown constant {key}. Known: {', '.join(known)}")


def _print_domain(slug, d):
    mark = ("read in the primary text" if d["verified"]
            else f"{C.VERIFY} secondary source only, confirm before use")
    print(f"{d['title']}  [{slug}]")
    print(f"  Checked: {d['as_of']} ({mark})")
    print(f"  State: {d['state']}")
    print(f"  In the cutting room: {d['for_the_cutting_room']}")
    if d["constants"]:
        print("  Constants: " + ", ".join(f"{k}={v}" for k, v in d["constants"].items()))
    print(f"  Sources: {'; '.join(d['sources'])}")


def main(argv=None):
    ap = C.parser_for(__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser("list", help="Domains, one line each")
    C.add_json(ls)

    sh = sub.add_parser("show", help="One domain, or all of them")
    sh.add_argument("domain", nargs="?")
    sh.add_argument("--all", action="store_true")
    C.add_json(sh)

    fr = sub.add_parser("freshness", help="How old each verification is")
    C.add_json(fr)

    co = sub.add_parser("const", help="One constant by dotted key")
    co.add_argument("key")
    C.add_json(co)

    args = ap.parse_args(argv)

    if args.cmd == "list":
        if args.json:
            return C.emit({k: v["title"] for k, v in DOMAINS.items()}, True)
        print("Standards domains. Dated scaffolding, not authority.\n")
        for slug, d in DOMAINS.items():
            how = "primary text" if d["verified"] else f"secondary {C.VERIFY}"
            print(f"  {slug:<20} {d['title']}  (checked {d['as_of']}, {how})")
    elif args.cmd == "show":
        if args.all:
            for slug, d in DOMAINS.items():
                if args.json:
                    continue
                _print_domain(slug, d)
                print()
            if args.json:
                return C.emit(DOMAINS, True)
        else:
            if not args.domain:
                raise ValueError("Give a domain or --all. Available: "
                                 + ", ".join(DOMAINS))
            d = DOMAINS.get(args.domain)
            if d is None:
                raise ValueError(f"Unknown domain {args.domain}. Available: "
                                 + ", ".join(DOMAINS))
            return C.emit(dict(slug=args.domain, **d), args.json,
                          lambda _: _print_domain(args.domain, d))
    elif args.cmd == "freshness":
        rows = freshness()
        if args.json:
            return C.emit(rows, True)
        print(f"Verification age (stale beyond {STALE_DAYS} days):")
        for r in rows:
            flag = "STALE, re-verify before use" if r["stale"] else "in date"
            prim = "" if r["verified_against_primary"] else f", {C.VERIFY} secondary only"
            print(f"  {r['domain']:<20} {r['as_of']} ({r['age_days']} days): {flag}{prim}")
    elif args.cmd == "const":
        got = constant(args.key)
        return C.emit(got, args.json,
                      lambda g: print(f"{g['key']} = {g['value']}\n"
                                      f"  from {g['domain']}, verified {g['as_of']}\n"
                                      f"  sources: {'; '.join(g['sources'])}"))
    return 0


if __name__ == "__main__":
    sys.exit(C.main_guard(main))
