#!/usr/bin/env python3
"""Subtitles and captions: read, check, retime, convert, and place.

Nothing in this toolkit could read a subtitle file before this, so the whole
department is new. It is also the cheapest department to get wrong, because
every fault is invisible in the edit and obvious to the audience.

  read      any of SRT, WebVTT, TTML or iTT into one shape
  check     reading speed, line rules, durations, gaps, overlaps
  shift     move every event by an offset
  retime    change frame rate, either by SCALING time or by KEEPING it
  convert   write out in another format
  collide   hold the subtitles against the supers, in time and in place
  burn      the ffmpeg command to burn in, with the geometry stated

The counting rule is stated with every reading speed, because two tools that
disagree only about whether spaces count will disagree about whether a file
passes. Default here: every character counts except the line break.

Usage:
  python subs.py read FILM.srt
  python subs.py check FILM.srt --profile broadcast_hd_r128
  python subs.py check FILM.srt --max-cps 17 --max-chars 37 --fps 25
  python subs.py shift FILM.srt --by -1.5 --out SHIFTED.srt
  python subs.py retime FILM.srt --from 25 --to 24 --mode scale --out OUT.srt
  python subs.py convert FILM.srt --to vtt --out FILM.vtt
  python subs.py collide FILM.srt --supers SUPERS.json --fps 25
  python subs.py burn FILM.srt --video FILM.mov --raster 1920x1080
  (add --json for structured output)
"""
from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _common as C  # noqa: E402

# House defaults, used only when neither a profile nor a flag names a number.
# These are conservative and they are NOT a standard: see standards.py subtitles.
DEFAULTS = {"max_lines": 2, "max_chars_per_line": 42, "max_cps": 17,
            "min_duration_s": 1.0, "max_duration_s": 7.0, "min_gap_s": 0.0833}

_SRT_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
_ARROW = re.compile(r"-->")


# ---------------------------------------------------------------- time


def parse_time(text):
    """hh:mm:ss,mmm or hh:mm:ss.mmm or mm:ss.mmm to seconds."""
    text = text.strip()
    m = _SRT_TIME.search(text)
    if m:
        h, mnt, s, ms = m.groups()
        return int(h) * 3600 + int(mnt) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0
    m = re.match(r"^(\d{1,2}):(\d{2})[.,](\d{1,3})$", text)
    if m:
        mnt, s, ms = m.groups()
        return int(mnt) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0
    m = re.match(r"^(\d+(?:\.\d+)?)s?$", text)
    if m:
        return float(m.group(1))
    raise ValueError(f"Cannot read a time from {text!r}")


def fmt_time(seconds, sep=","):
    if seconds < 0:
        seconds = 0.0
    ms_total = int(round(seconds * 1000))
    h, rem = divmod(ms_total, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


# ---------------------------------------------------------------- read


def read(path):
    """Read any supported format into one shape."""
    ext = os.path.splitext(path)[1].lower()
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    if ext in (".ttml", ".itt", ".xml", ".dfxp"):
        events = _read_ttml(text)
        fmt = "ttml"
    elif ext == ".vtt" or text.lstrip().startswith("WEBVTT"):
        events = _read_vtt(text)
        fmt = "vtt"
    else:
        events = _read_srt(text)
        fmt = "srt"
    for i, e in enumerate(events):
        e["index"] = i + 1
    out_of_order = [e["index"] for a, e in zip(events, events[1:])
                    if e["start"] < a["start"]]
    return {"file": os.path.abspath(path), "format": fmt,
            "events": events, "count": len(events),
            "out_of_order": out_of_order,
            "note": ("This file contains no subtitle events. An empty sidecar "
                     "passes every rule check and delivers nothing."
                     if not events else
                     f"Events {out_of_order} start before the event above them. "
                     "The gap and overlap checks below read the file in its own "
                     "order, so sort it before believing them."
                     if out_of_order else "")}


def _read_srt(text):
    events = []
    for chunk in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [ln for ln in chunk.splitlines() if ln.strip() != ""]
        if not lines:
            continue
        if not _ARROW.search(lines[0]) and lines[0].strip().isdigit():
            lines = lines[1:]
        if not lines or not _ARROW.search(lines[0]):
            continue
        left, right = _ARROW.split(lines[0], 1)
        events.append({"start": parse_time(left), "end": parse_time(right),
                       "lines": [ln.rstrip() for ln in lines[1:]], "style": None})
    return events


def _read_vtt(text):
    events = []
    body = re.sub(r"^WEBVTT.*?(\r?\n\r?\n)", "", text, count=1, flags=re.S)
    for chunk in re.split(r"\r?\n\s*\r?\n", body.strip()):
        lines = [ln for ln in chunk.splitlines() if ln.strip() != ""]
        if not lines:
            continue
        if lines and lines[0].upper().startswith(("NOTE", "STYLE", "REGION")):
            continue
        if not _ARROW.search(lines[0]) and len(lines) > 1:
            lines = lines[1:]
        if not lines or not _ARROW.search(lines[0]):
            continue
        head = lines[0]
        settings = head.split("-->")[1].strip().split(" ", 1)
        left = head.split("-->")[0]
        right = settings[0]
        style = settings[1] if len(settings) > 1 else None
        events.append({"start": parse_time(left), "end": parse_time(right),
                       "lines": [ln.rstrip() for ln in lines[1:]], "style": style})
    return events


def _read_ttml(text):
    root = ET.fromstring(text)
    ns = {"tt": "http://www.w3.org/ns/ttml"}
    events = []
    for p in root.iterfind(".//tt:body//tt:p", ns):
        begin, end = p.get("begin"), p.get("end")
        if not begin:
            continue
        lines, current = [], []
        for node in p.iter():
            tag = node.tag.split("}")[-1]
            if tag == "br":
                lines.append("".join(current))
                current = []
                if node.tail:
                    current.append(node.tail)
                continue
            if node is p:
                if node.text:
                    current.append(node.text)
                continue
            if node.text:
                current.append(node.text)
            if node.tail:
                current.append(node.tail)
        lines.append("".join(current))
        events.append({"start": parse_time(begin),
                       "end": parse_time(end) if end else parse_time(begin) + 2.0,
                       "lines": [ln.strip() for ln in lines if ln.strip() != ""],
                       "style": p.get("region")})
    return events


# ---------------------------------------------------------------- write


def write(events, path, fmt=None):
    fmt = fmt or os.path.splitext(path)[1].lstrip(".").lower() or "srt"
    if fmt == "srt":
        out = _write_srt(events)
    elif fmt in ("vtt", "webvtt"):
        out = _write_vtt(events)
    elif fmt in ("ttml", "itt", "dfxp", "xml"):
        out = _write_ttml(events)
    else:
        raise ValueError(f"Cannot write {fmt}. Try srt, vtt or ttml.")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out)
    return path


def _write_srt(events):
    parts = []
    for i, e in enumerate(events, 1):
        parts.append(f"{i}\n{fmt_time(e['start'])} --> {fmt_time(e['end'])}\n"
                     + "\n".join(e["lines"]) + "\n")
    return "\n".join(parts)


def _write_vtt(events):
    parts = ["WEBVTT\n"]
    for e in events:
        head = f"{fmt_time(e['start'], '.')} --> {fmt_time(e['end'], '.')}"
        if e.get("style"):
            head += " " + e["style"]
        parts.append(head + "\n" + "\n".join(e["lines"]) + "\n")
    return "\n".join(parts)


def _write_ttml(events):
    def esc(s):
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    rows = []
    for e in events:
        body = "<br/>".join(esc(ln) for ln in e["lines"])
        rows.append(f'      <p begin="{fmt_time(e["start"], ".")}" '
                    f'end="{fmt_time(e["end"], ".")}">{body}</p>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<tt xmlns="http://www.w3.org/ns/ttml" xml:lang="">\n'
            '  <body>\n    <div>\n'
            + "\n".join(rows)
            + '\n    </div>\n  </body>\n</tt>\n')


# ---------------------------------------------------------------- check


def count_chars(lines, count_spaces=True):
    text = "".join(lines) if count_spaces else "".join(lines).replace(" ", "")
    return len(text)


def check(doc, rules, fps=None, count_spaces=True):
    """Every event against the rules, and the file against itself."""
    rules = {**DEFAULTS, **{k: v for k, v in (rules or {}).items() if v is not None}}
    events = doc["events"]
    rows = []
    for i, e in enumerate(events):
        dur = e["end"] - e["start"]
        chars = count_chars(e["lines"], count_spaces)
        cps = chars / dur if dur > 0 else float("inf")
        faults = []
        if dur <= 0:
            faults.append(("zero or negative duration", "strike"))
        if rules.get("min_duration_s") and dur < rules["min_duration_s"] - 1e-6:
            faults.append((f"{dur:.3f}s is under the {rules['min_duration_s']}s "
                           "minimum: it flashes", "strike"))
        if rules.get("max_duration_s") and dur > rules["max_duration_s"] + 1e-6:
            faults.append((f"{dur:.3f}s is over the {rules['max_duration_s']}s "
                           "maximum: it sits after the line has gone", "strike"))
        if rules.get("max_lines") and len(e["lines"]) > rules["max_lines"]:
            faults.append((f"{len(e['lines'])} lines against a maximum of "
                           f"{rules['max_lines']}", "strike"))
        if rules.get("max_chars_per_line"):
            for line in e["lines"]:
                if len(line) > rules["max_chars_per_line"]:
                    faults.append((f"line of {len(line)} characters against "
                                   f"{rules['max_chars_per_line']}: '{line[:24]}...'",
                                   "strike"))
        if rules.get("max_cps") and cps > rules["max_cps"] + 1e-9:
            faults.append((f"{cps:.1f} characters per second against a limit of "
                           f"{rules['max_cps']}: too fast to read", "strike"))
        if i + 1 < len(events):
            gap = events[i + 1]["start"] - e["end"]
            if gap < 0:
                faults.append((f"overlaps the next event by {-gap:.3f}s", "strike"))
            elif rules.get("min_gap_s") and gap < rules["min_gap_s"] - 1e-6:
                faults.append((f"gap of {gap:.3f}s to the next event is under "
                               f"{rules['min_gap_s']:.4f}s: they read as one flicker",
                               "strike"))
        rows.append({"index": e["index"], "start": e["start"], "end": e["end"],
                     "duration": round(dur, 3), "chars": chars,
                     "cps": round(cps, 2) if dur > 0 else None,
                     "lines": e["lines"],
                     "faults": [f[0] for f in faults],
                     "worst": "strike" if faults else "ok"})
    if fps:
        rate = float(C.rate(fps))
        for row, e in zip(rows, events):
            for key in ("start", "end"):
                exact = e[key] * rate
                if abs(exact - round(exact)) > 0.02:
                    row.setdefault("faults", []).append(
                        f"{key} {e[key]:.3f}s is not on a frame boundary at "
                        f"{C.rate_str(C.rate(fps))} ({exact:.2f} frames)")
                    row["worst"] = "query" if row["worst"] == "ok" else row["worst"]
    disorder = doc.get("out_of_order") or []
    for row in rows:
        if row["index"] in disorder:
            row["faults"].insert(0, "starts BEFORE the event above it: this file "
                                    "is out of order, so every gap and overlap "
                                    "result here is measuring the wrong pair")
    bad = [r for r in rows if r["faults"]]
    if not events:
        return {"file": doc["file"], "format": doc["format"], "events": 0,
                "rules": rules, "counting_rule": "nothing to count",
                "fps_checked": None, "rows": [], "failing": 0,
                "verdict": "THIS FILE HAS NO EVENTS. An empty sidecar passes "
                           "every rule and delivers nothing.",
                "note": "Check it was written from the right source."}
    return {"file": doc["file"], "format": doc["format"], "events": len(events),
            "out_of_order": disorder,
            "rules": rules, "counting_rule":
                ("every character counts except the line break"
                 if count_spaces else "spaces do not count"),
            "fps_checked": str(C.rate(fps)) if fps else None,
            "rows": rows, "failing": len(bad),
            "verdict": ("every event is inside the rules" if not bad
                        else f"{len(bad)} of {len(events)} events break a rule"),
            "note": "Reading speed limits belong to the delivery, and the big "
                    "platforms keep theirs in the PER LANGUAGE guide, not in the "
                    "general one. Cite the language guide."}


# ---------------------------------------------------------------- transforms


def shift(doc, by):
    for e in doc["events"]:
        e["start"] = max(0.0, e["start"] + by)
        e["end"] = max(0.0, e["end"] + by)
    return doc


def retime(doc, src_fps, dst_fps, mode="scale"):
    """Change rate. Two modes, and they answer different questions.

    scale  the picture was RE-TIMED, so the film is now a different length and
           every subtitle time scales with it. 25 to 24 makes it longer.
    keep   the picture kept its running time (a conform by re-labelling, or a
           rate change with a matching speed change), so the times do not move
           and only the frame boundaries do.
    """
    a, b = float(C.rate(src_fps)), float(C.rate(dst_fps))
    if mode == "keep":
        factor = 1.0
    elif mode == "scale":
        factor = a / b
    else:
        raise ValueError("mode is scale or keep")
    for e in doc["events"]:
        e["start"] *= factor
        e["end"] *= factor
    doc["retime"] = {"from": str(C.rate(src_fps)), "to": str(C.rate(dst_fps)),
                     "mode": mode, "factor": factor}
    return doc


def collide(doc, supers_plan, fps):
    """Do any subtitles share time and space with a super?

    A subtitle that lands on a super is the single most common note from a
    client viewing, and it is entirely predictable before anyone watches.
    """
    rate = float(C.rate(fps))
    hits = []
    for block in supers_plan.get("blocks", []):
        b_in, b_out = block.get("in"), block.get("out")
        if b_in is None or b_out is None:
            continue
        t0, t1 = b_in / rate, b_out / rate
        box = block["ink_box"]
        for e in doc["events"]:
            if e["end"] <= t0 or e["start"] >= t1:
                continue
            hits.append({"event": e["index"], "lines": e["lines"],
                         "event_time": [round(e["start"], 3), round(e["end"], 3)],
                         "block": block["id"],
                         "block_time": [round(t0, 3), round(t1, 3)],
                         "block_ink_box": [round(v, 1) for v in box],
                         "overlap_s": round(min(e["end"], t1) - max(e["start"], t0), 3)})
    return {"collisions": hits, "count": len(hits),
            "verdict": ("no subtitle shares time with a super" if not hits else
                        f"{len(hits)} subtitle(s) are on screen while a super is. "
                        "Move the subtitle, move the super, or place the subtitle "
                        "at the top for that span, and say which you did."),
            "note": "This compares TIME. Whether they also share space depends "
                    "on where the burn in or the player puts the subtitle, which "
                    "is why burn prints the geometry it will use."}


def burn_command(sub_path, video, raster, margin_v=None, fontsize=None,
                 safe=0.90, out=None):
    """The ffmpeg burn in, with its geometry stated rather than defaulted."""
    rw, rh = raster
    title_inset = rh * (1 - safe) / 2.0
    margin_v = margin_v if margin_v is not None else int(round(title_inset))
    fontsize = fontsize or int(round(rh * 0.042))
    out = out or "BURNED.mov"
    style = (f"FontSize={fontsize},MarginV={margin_v},Alignment=2,"
             f"BorderStyle=1,Outline=2,Shadow=0")
    return {
        "command": (f"ffmpeg -i {video} -vf \"subtitles={sub_path}:"
                    f"force_style='{style}'\" -c:a copy {out}"),
        "raster": [rw, rh], "font_size_px": fontsize,
        "margin_v_px": margin_v, "title_safe_inset_px": round(title_inset, 1),
        "note": "MarginV is set to the title safe inset so the bottom line "
                "cannot fall outside it. Burning in is destructive: keep a "
                "textless master, and never burn into the only copy.",
    }


# ---------------------------------------------------------------- cli


def _rules_from_args(args):
    rules = {}
    if getattr(args, "profile", None):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import spec as SPEC
        rules = dict(SPEC.load_profile(args.profile).get("subtitles") or {})
    for key, attr in (("max_cps", "max_cps"), ("max_chars_per_line", "max_chars"),
                      ("max_lines", "max_lines"),
                      ("min_duration_s", "min_duration"),
                      ("max_duration_s", "max_duration"),
                      ("min_gap_s", "min_gap")):
        val = getattr(args, attr, None)
        if val is not None:
            rules[key] = val
    return rules


def main(argv=None):
    ap = C.parser_for(__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    rd = sub.add_parser("read", help="Read into one shape")
    rd.add_argument("file")
    C.add_json(rd)

    ck = sub.add_parser("check", help="Against the rules")
    ck.add_argument("file")
    ck.add_argument("--profile")
    ck.add_argument("--max-cps", type=float)
    ck.add_argument("--max-chars", type=int)
    ck.add_argument("--max-lines", type=int)
    ck.add_argument("--min-duration", type=float)
    ck.add_argument("--max-duration", type=float)
    ck.add_argument("--min-gap", type=float)
    ck.add_argument("--fps")
    ck.add_argument("--no-count-spaces", action="store_true")
    C.add_json(ck)

    sf = sub.add_parser("shift", help="Move every event")
    sf.add_argument("file")
    sf.add_argument("--by", type=float, required=True, help="Seconds, may be negative")
    sf.add_argument("--out", required=True)
    C.add_json(sf)

    rt = sub.add_parser("retime", help="Change frame rate")
    rt.add_argument("file")
    rt.add_argument("--from", dest="src", required=True)
    rt.add_argument("--to", dest="dst", required=True)
    rt.add_argument("--mode", default="scale", choices=["scale", "keep"])
    rt.add_argument("--out", required=True)
    C.add_json(rt)

    cv = sub.add_parser("convert", help="Write another format")
    cv.add_argument("file")
    cv.add_argument("--to", required=True)
    cv.add_argument("--out", required=True)
    C.add_json(cv)

    co = sub.add_parser("collide", help="Against the supers")
    co.add_argument("file")
    co.add_argument("--supers", required=True, help="A supers.py plan --json file, "
                                                    "or the spec it plans from")
    co.add_argument("--fps", required=True)
    C.add_json(co)

    bn = sub.add_parser("burn", help="The burn in command")
    bn.add_argument("file")
    bn.add_argument("--video", required=True)
    bn.add_argument("--raster", required=True)
    bn.add_argument("--margin-v", type=int)
    bn.add_argument("--font-size", type=int)
    bn.add_argument("--out")
    C.add_json(bn)

    args = ap.parse_args(argv)

    if args.cmd == "read":
        doc = read(args.file)
        return C.emit(doc, args.json, lambda d: (
            print(f"{os.path.basename(d['file'])}: {d['count']} events, "
                  f"read as {d['format']}"),
            print(f"  {d['note']}") if d["note"] else None,
            [print(f"  {e['index']:>4} {fmt_time(e['start'])} --> {fmt_time(e['end'])}"
                   f"  {' / '.join(e['lines'])}") for e in d["events"][:20]],
            print(f"  ... {d['count'] - 20} more") if d["count"] > 20 else None))
    if args.cmd == "check":
        doc = read(args.file)
        res = check(doc, _rules_from_args(args), args.fps, not args.no_count_spaces)
        return C.emit(res, args.json, _print_check)
    if args.cmd == "shift":
        doc = shift(read(args.file), args.by)
        write(doc["events"], args.out)
        return C.emit({"out": args.out, "events": doc["count"], "by": args.by},
                      args.json, lambda r: print(f"  {r['events']} events moved "
                                                 f"{r['by']:+}s into {r['out']}"))
    if args.cmd == "retime":
        doc = retime(read(args.file), args.src, args.dst, args.mode)
        write(doc["events"], args.out)
        return C.emit({"out": args.out, **doc["retime"]}, args.json,
                      lambda r: print(f"  {r['from']} to {r['to']} by {r['mode']}, "
                                      f"factor {r['factor']:.6f}, into {r['out']}"))
    if args.cmd == "convert":
        doc = read(args.file)
        write(doc["events"], args.out, args.to)
        return C.emit({"out": args.out, "events": doc["count"], "format": args.to},
                      args.json, lambda r: print(f"  {r['events']} events written "
                                                 f"as {r['format']} to {r['out']}"))
    if args.cmd == "collide":
        with open(args.supers, encoding="utf-8") as fh:
            data = json.load(fh)
        if "blocks" in data and data["blocks"] and "ink_box" not in data["blocks"][0]:
            import supers as SUP
            data = SUP.plan(data)
        res = collide(read(args.file), data, args.fps)
        return C.emit(res, args.json, lambda r: (
            [print(f"  event {h['event']} {h['event_time']} meets block "
                   f"{h['block']} {h['block_time']} for {h['overlap_s']}s: "
                   f"{' / '.join(h['lines'])}") for h in r["collisions"]],
            print(f"\n  {r['verdict']}"), print(f"  {r['note']}")))
    if args.cmd == "burn":
        rw, rh = (int(v) for v in re.split(r"[xX*]", args.raster))
        res = burn_command(args.file, args.video, (rw, rh), args.margin_v,
                           args.font_size, out=args.out)
        return C.emit(res, args.json, lambda r: (
            print(f"  {r['command']}"),
            print(f"\n  font {r['font_size_px']}px, MarginV {r['margin_v_px']}px, "
                  f"title safe inset {r['title_safe_inset_px']}px"),
            print(f"  {r['note']}")))
    return 0


def _print_check(res):
    print(f"{os.path.basename(res['file'])}: {res['events']} events, "
          f"read as {res['format']}")
    print(f"  rules: {res['rules']}")
    print(f"  counting: {res['counting_rule']}"
          + (f", frame boundaries at {res['fps_checked']}"
             if res["fps_checked"] else ""))
    print()
    for r in res["rows"]:
        if not r["faults"]:
            continue
        print(f"  event {r['index']} ({fmt_time(r['start'])}, {r['duration']}s, "
              f"{r['chars']} chars, {r['cps']} cps)")
        for f in r["faults"]:
            print(f"    - {f}")
    print(f"\n  {res['verdict']}")
    print(f"  {res['note']}")


if __name__ == "__main__":
    sys.exit(C.main_guard(main))
