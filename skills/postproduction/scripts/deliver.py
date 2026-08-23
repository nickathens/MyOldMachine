#!/usr/bin/env python3
"""The delivery precheck: everything wrong or unproven, in one list.

A film is the place where every department has to agree, and this is the single
list they agree against. Two halves, and the split of responsibility matters:

  check   THE CATALOGUE. You say what the package holds; it says what is
          missing, what is conditional and needs an explicit answer, and what a
          reviewer would strike. Deterministic, offline, no file needed.
  audit   THE FILE. It runs the picture spec, the colour tag walk and the
          loudness against a profile and returns one strike list. Anything it
          cannot measure is reported as UNPROVEN, never as passed.

The catalogue is SCAFFOLDING [VERIFY]. What a delivery requires is set by the
client's own delivery document, which always wins. A package that is complete by
this tool is not an approved delivery: it means the scaffolding is in place.

This tool never approves and never sends. Approval belongs to the client and the
send is a separate act that has to return success for that exact file.

Usage:
  python deliver.py list
  python deliver.py check --type tvc --have master,textless,viewing_copy
  python deliver.py check --type broadcast_programme --have master --conditions subtitle_sidecar
  python deliver.py audit FILM.mov --profile broadcast_hd_r128
  (add --json for structured output)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _common as C  # noqa: E402

VERIFY = C.VERIFY

ITEMS = {
    "master": {
        "name": "The master",
        "owner": "online or finishing",
        "always": True,
        "reviewer": "Raster, rate, codec, bit depth and all four colour "
                    "declarations against the delivery document. An untagged "
                    "master is struck even when it looks right.",
    },
    "textless": {
        "name": "Textless master (clean, no supers or titles)",
        "owner": "online",
        "always": True,
        "reviewer": "Same length and same grade as the master, with the type "
                    "absent rather than covered. Usually required for any "
                    "territory or language version later, so its absence is a "
                    "problem that surfaces months afterwards.",
    },
    "textless_partials": {
        "name": "Textless partials for the supered spans only",
        "owner": "online",
        "always": False,
        "condition": "When a full textless is not required but localisation is "
                     "expected " + VERIFY,
        "reviewer": "Each partial's in and out points must be quoted in the "
                    "master's own timecode.",
    },
    "audio_stems": {
        "name": "Audio stems (dialogue, music, effects)",
        "owner": "mix",
        "always": False,
        "condition": "When the delivery names them, which broadcast and VOD "
                     "usually do and a web cut usually does not",
        "reviewer": "Stems must sum to the mix. A stem set that does not sum is "
                    "the classic late discovery.",
    },
    "subtitle_sidecar": {
        "name": "Subtitle or caption sidecar",
        "owner": "post",
        "always": False,
        "condition": "When the delivery names timed text, in the format it names",
        "reviewer": "Format, language tag, frame rate assumption, reading speed "
                    "and line rules. Run subs.py check first.",
    },
    "audio_description": {
        "name": "Audio description track",
        "owner": "access services",
        "always": False,
        "condition": "When the platform or the broadcaster requires access "
                     "services " + VERIFY,
        "reviewer": "Present as its own track or file, correctly labelled.",
    },
    "viewing_copy": {
        "name": "Viewing copy, labelled as one",
        "owner": "post",
        "always": True,
        "reviewer": "Same frame size and same bit depth as the master, only "
                    "compressed. A downscaled preview is not a viewing copy of "
                    "this film; it is a different film, and it puts the work on "
                    "trial in a form that cannot show a fault or a fix.",
    },
    "spec_sheet": {
        "name": "Spec sheet: what this file actually is",
        "owner": "post",
        "always": True,
        "reviewer": "The measured spec, not the intended one. spec.py probe "
                    "writes it.",
    },
    "hash_ledger": {
        "name": "Hash ledger for every delivered file",
        "owner": "post",
        "always": True,
        "reviewer": "Recording the sha of every delivered file is what makes a "
                    "later rollback checkable instead of a claim. prove.py sha "
                    "writes it.",
    },
    "changed_frame_proof": {
        "name": "Changed frame proof against the previous version",
        "owner": "post",
        "always": False,
        "condition": "On any REVISION of a film already delivered",
        "reviewer": "The predicted set derived from the layers, the found set "
                    "from the file, and the two agreeing. A description of what "
                    "changed is not a proof.",
    },
    "null_version": {
        "name": "Null version encode, byte identical to the predecessor",
        "owner": "post",
        "always": False,
        "condition": "On any ADDITIVE revision (a shadow, an overlay, a graphic)",
        "reviewer": "Encode the version with the new thing switched OFF and "
                    "require sha equality with the delivered segment. It proves "
                    "the decode, the transform and the encoder all still "
                    "reproduce the delivered film.",
    },
    "conform_notes": {
        "name": "Conform notes: rate, drop frame, start timecode, handles",
        "owner": "editorial",
        "always": True,
        "reviewer": "A start timecode of 01:00:00:00 or 10:00:00:00 is a "
                    "convention, not a default. Say which, and say whether the "
                    "rate is native or conformed.",
    },
    "fonts_and_artwork": {
        "name": "Fonts, logos and artwork used, with their licences",
        "owner": "design",
        "always": False,
        "condition": "When the client will version the film themselves later",
        "reviewer": "A font licence that does not cover broadcast is found at "
                    "the worst possible moment.",
    },
    "archive_package": {
        "name": "Archive package: project, sources, build and notes",
        "owner": "post",
        "always": False,
        "condition": "When the job is being closed rather than iterated",
        "reviewer": "Enough to rebuild, plus the delivered artifacts "
                    "themselves, because a build script drifts and the "
                    "delivered file does not.",
    },
    "qc_report": {
        "name": "QC report",
        "owner": "post or a QC house",
        "always": False,
        "condition": "When the delivery names an automated or human QC pass",
        "reviewer": "A clean report from a checker that was never run against "
                    "an approved file is not evidence.",
    },
}

TYPES = {
    "tvc": {
        "name": "Commercial, online master to the agency or client",
        "items": ["master", "textless", "viewing_copy", "spec_sheet",
                  "hash_ledger", "conform_notes", "subtitle_sidecar",
                  "audio_stems", "changed_frame_proof", "null_version",
                  "fonts_and_artwork"],
    },
    "broadcast_programme": {
        "name": "Broadcast programme delivery",
        "items": ["master", "textless", "audio_stems", "subtitle_sidecar",
                  "audio_description", "spec_sheet", "hash_ledger",
                  "conform_notes", "qc_report", "viewing_copy"],
        "note": "Broadcasters publish their own delivery document and it "
                "overrides every line of this " + VERIFY,
    },
    "vod_episode": {
        "name": "VOD or streaming platform episode",
        "items": ["master", "textless", "audio_stems", "subtitle_sidecar",
                  "audio_description", "spec_sheet", "hash_ledger",
                  "qc_report", "conform_notes"],
        "note": "Platform specs move; confirm the current page before delivering "
                + VERIFY,
    },
    "social": {
        "name": "Social and web cutdowns",
        "items": ["master", "viewing_copy", "subtitle_sidecar", "spec_sheet"],
        "note": "Platforms re-encode whatever they are given, so the raster, the "
                "tags and the loudness are the only things under your control.",
    },
    "client_review": {
        "name": "Review copy for approval, not a delivery",
        "items": ["viewing_copy", "spec_sheet"],
        "note": "A review copy is still full raster and full bit depth, only "
                "compressed. It is labelled a viewing copy so nobody mistakes it "
                "for the master.",
    },
    "archive": {
        "name": "Closing a job",
        "items": ["master", "textless", "archive_package", "hash_ledger",
                  "spec_sheet", "conform_notes", "fonts_and_artwork"],
    },
}


def check(kind, have=(), conditions=()):
    """The package against the catalogue."""
    t = TYPES.get(kind)
    if t is None:
        raise ValueError(f"Unknown delivery type {kind}. Available: "
                         + ", ".join(TYPES))
    unknown = [s for s in list(have) + list(conditions) if s not in ITEMS]
    if unknown:
        raise ValueError(f"Unknown item(s): {', '.join(unknown)}. Run list.")
    have_set, cond_set = set(have), set(conditions)
    present, missing, open_questions = [], [], []
    for slug in t["items"]:
        item = ITEMS[slug]
        required = item["always"] or slug in cond_set
        if slug in have_set:
            present.append(slug)
        elif required:
            missing.append({"slug": slug, "name": item["name"],
                            "owner": item["owner"], "reviewer": item["reviewer"]})
        else:
            open_questions.append({"slug": slug, "name": item["name"],
                                   "condition": item.get("condition", "")})
    required_slugs = [s for s in t["items"]
                      if ITEMS[s]["always"] or s in cond_set]
    done = [s for s in present if s in required_slugs]
    return {"type": t["name"], "slug": kind,
            "present": present, "missing": missing,
            "open_questions": open_questions,
            "readiness": f"{len(done)}/{len(required_slugs)} required or "
                         "confirmed conditional items",
            "note": t.get("note", "") or
                    (f"Scaffolding {VERIFY} against the client's own delivery "
                     "document. Complete here does not mean approved."),
            "refusals": ["This tool never marks a delivery approved.",
                         "It never sends. A file is delivered only when the send "
                         "returns success for that exact path.",
                         "It never downscales anything."]}


def audit(path, profile_slug):
    """Run the machine checks that can be run, and name the ones that cannot."""
    import spec as SPEC
    profile = SPEC.load_profile(profile_slug)
    rows = []

    picture = SPEC.check(path, profile)
    for r in picture["rows"]:
        if r["verdict"] in ("MISMATCH", "UNKNOWN"):
            rows.append({"area": "picture", "item": r["field"],
                         "state": "STRIKE" if r["severity"] == "strike" else "ASK",
                         "detail": f"want {r['want']}, got {r['got']}. {r['note']}"})
        elif r["verdict"] in ("ASK", "DEFERRED"):
            rows.append({"area": "picture", "item": r["field"], "state": "ASK",
                         "detail": r["note"]})
    for flag in picture["flags"]:
        rows.append({"area": "picture", "item": "flag", "state": "ASK",
                     "detail": flag})

    try:
        import prove as PROVE
        tags = PROVE.tag_walk(path)
        if not tags["uniform"]:
            rows.append({"area": "colour", "item": "colour tags",
                         "state": "STRIKE", "detail": tags["verdict"]})
        elif tags["untagged_runs"]:
            rows.append({"area": "colour", "item": "colour tags",
                         "state": "STRIKE",
                         "detail": "The file does not fully declare its colour."})
    except (RuntimeError, ValueError) as exc:
        rows.append({"area": "colour", "item": "colour tags", "state": "UNPROVEN",
                     "detail": f"could not walk the tags: {exc}"})

    try:
        import audio as AUD
        loud = AUD.check(path, profile)
        for r in loud["rows"]:
            if r["verdict"] != "ok":
                rows.append({"area": "sound", "item": r["field"],
                             "state": "UNPROVEN" if r["verdict"] == "CANNOT MEASURE"
                                      else "STRIKE",
                             "detail": f"want {r['want']}, got {r['got']}. "
                                       f"{r['note']}"})
    except (RuntimeError, ValueError) as exc:
        rows.append({"area": "sound", "item": "loudness", "state": "UNPROVEN",
                     "detail": f"could not measure: {exc}"})

    rows.append({"area": "versions", "item": "changed frame proof",
                 "state": "UNPROVEN",
                 "detail": "Nothing here can know whether this is a revision. If "
                           "it is, derive the predicted frames from the layers "
                           "and require the delivered file to agree "
                           "(prove.py predict, then prove.py expect)."})

    strikes = [r for r in rows if r["state"] == "STRIKE"]
    unproven = [r for r in rows if r["state"] == "UNPROVEN"]
    asks = [r for r in rows if r["state"] == "ASK"]
    return {"file": os.path.abspath(path), "profile": profile_slug,
            "rows": rows, "strikes": len(strikes), "unproven": len(unproven),
            "questions": len(asks),
            "verdict": ("would be struck" if strikes else
                        "nothing struck, but see the unproven items"
                        if unproven else "clean against what could be measured"),
            "note": "UNPROVEN is not a pass. It is the list of things this tool "
                    "cannot measure and somebody still has to."}


def main(argv=None):
    ap = C.parser_for(__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser("list", help="Items and delivery types")
    C.add_json(ls)

    ck = sub.add_parser("check", help="The package against the catalogue")
    ck.add_argument("--type", required=True)
    ck.add_argument("--have", default="")
    ck.add_argument("--conditions", default="",
                    help="Conditional items you confirm this job needs")
    C.add_json(ck)

    au = sub.add_parser("audit", help="The FILE against a profile")
    au.add_argument("file")
    au.add_argument("--profile", required=True)
    C.add_json(au)

    args = ap.parse_args(argv)

    if args.cmd == "list":
        if args.json:
            return C.emit({"items": ITEMS, "types": TYPES}, True)
        print("Delivery types:")
        for slug, t in TYPES.items():
            print(f"  {slug:<22} {t['name']}")
        print("\nItems:")
        for slug, item in ITEMS.items():
            when = "always" if item["always"] else "conditional"
            print(f"  {slug:<22} {item['name']}  [{when}, {item['owner']}]")
        return 0
    if args.cmd == "check":
        have = [s for s in args.have.split(",") if s.strip()]
        cond = [s for s in args.conditions.split(",") if s.strip()]
        res = check(args.type, have, cond)
        return C.emit(res, args.json, _print_check)
    if args.cmd == "audit":
        res = audit(args.file, args.profile)
        return C.emit(res, args.json, _print_audit)
    return 0


def _print_check(r):
    print(f"{r['type']}\n")
    if r["present"]:
        print("  Present: " + ", ".join(r["present"]))
    if r["missing"]:
        print("\n  MISSING:")
        for m in r["missing"]:
            print(f"   - {m['name']}  ({m['owner']})")
            print(f"     {m['reviewer']}")
    if r["open_questions"]:
        print("\n  Conditional, answer these explicitly:")
        for q in r["open_questions"]:
            print(f"   - {q['name']}: {q['condition'] or 'does this job need it'}")
    print(f"\n  {r['readiness']}")
    print(f"  {r['note']}")
    for line in r["refusals"]:
        print(f"  {line}")


def _print_audit(r):
    print(f"{os.path.basename(r['file'])} against {r['profile']}\n")
    for row in r["rows"]:
        print(f"  [{row['state']:<8}] {row['area']}/{row['item']}")
        print(f"      {row['detail']}")
    print(f"\n  {r['strikes']} struck, {r['unproven']} unproven, "
          f"{r['questions']} question(s): {r['verdict']}")
    print(f"  {r['note']}")


if __name__ == "__main__":
    sys.exit(C.main_guard(main))
