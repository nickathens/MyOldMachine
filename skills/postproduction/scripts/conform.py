#!/usr/bin/env python3
"""Conform: timecode, drop frame, EDLs, handles and rate changes.

Nothing here decodes a picture. It is the arithmetic the cut depends on, and it
is arithmetic that fails silently: a drop frame timecode read as non drop puts
an hour long programme 3.6 seconds out and every frame number after it points at
the wrong picture.

Two things worth saying plainly, because they cause most of the damage:

  DROP FRAME DROPS NO PICTURES. It skips LABELS, two per minute at 30000/1001
  and four at 60000/1001, except on every tenth minute, so that the label keeps
  up with the wall clock. Every picture is still there.

  23.976 AND 29.97 ARE NOT RATES. They are roundings of 24000/1001 and
  30000/1001. Timecode built on the rounding drifts, and it drifts slowly enough
  to reach delivery.

Commands:
  tc         convert between timecode and frames, add, subtract, compare
  rates      what a rate is, exactly, and what its drop frame rules are
  edl        read a CMX 3600 EDL, and check it against itself
  handles    the pull with handles, and where it runs off the source
  duration   frames, seconds and timecode for a length at a rate

Usage:
  python conform.py tc to-frames 01:00:00:00 --fps 29.97df
  python conform.py tc from-frames 107892 --fps 29.97df
  python conform.py tc add 00:59:59:29 --frames 1 --fps 29.97df
  python conform.py tc diff 01:00:00:00 01:00:10:00 --fps 25
  python conform.py rates
  python conform.py edl read CUT.edl
  python conform.py edl check CUT.edl
  python conform.py handles --in 01:00:04:12 --out 01:00:09:00 --handle 12 --fps 24
  (add --json for structured output)
"""
from __future__ import annotations

import os
import re
import sys
from fractions import Fraction

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _common as C  # noqa: E402

# Rates that carry a drop frame variant, with how many labels are skipped per
# minute. Only the 1001 family has one: 24, 25, 30 and 50 exact have nothing to
# correct, and 24000/1001 has no standard drop frame scheme at all.
DROP_RULES = {
    Fraction(30000, 1001): {"nominal": 30, "drop": 2},
    Fraction(60000, 1001): {"nominal": 60, "drop": 4},
}

_TC = re.compile(r"^(\d{1,3}):([0-5]\d):([0-5]\d)([:;.])(\d{1,3})$")


def parse_rate(text):
    """'25', '29.97df', '24000/1001', '23.98nd' into (Fraction, drop_frame)."""
    s = str(text).strip().lower().replace(" ", "")
    drop = False
    if s.endswith("ndf"):
        s = s[:-3]
    elif s.endswith("nd"):
        s = s[:-2]
    elif s.endswith("df"):
        drop, s = True, s[:-2]
    rate = C.rate(s)
    if drop and rate not in DROP_RULES:
        raise ValueError(
            f"{C.rate_str(rate)} has no drop frame scheme. Drop frame exists to "
            "keep a LABEL in step with the clock, and only the 1001 family at 30 "
            "and 60 needs it. There is no standard drop frame at 24000/1001."
        )
    return rate, drop


def nominal(rate):
    """The integer rate the LABELS count in: 30 for 30000/1001, 25 for 25."""
    if rate in DROP_RULES:
        return DROP_RULES[rate]["nominal"]
    f = Fraction(rate)
    ceil = -(-f.numerator // f.denominator)
    return int(ceil)


def tc_to_frames(text, rate, drop):
    """Timecode label to a frame number, counting from zero."""
    m = _TC.match(str(text).strip())
    if not m:
        raise ValueError(f"Not a timecode: {text}. Use hh:mm:ss:ff, or hh:mm:ss;ff "
                         "for drop frame.")
    h, mi, s, sep, f = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4), int(m.group(5))
    if sep in (";", ".") and not drop:
        drop = True
        if rate not in DROP_RULES:
            raise ValueError(f"{text} is written as drop frame but "
                             f"{C.rate_str(rate)} has no drop frame scheme.")
    nom = nominal(rate)
    if f >= nom:
        raise ValueError(f"Frame field {f} is not valid at {nom} labels per second.")
    total = ((h * 60 + mi) * 60 + s) * nom + f
    if drop:
        d = DROP_RULES[rate]["drop"]
        if mi % 10 and s == 0 and f < d:
            raise ValueError(
                f"{text} is not a legal drop frame timecode: the labels :0 to "
                f":{d - 1} at the top of every minute except every tenth are the "
                "ones that get skipped.")
        minutes = h * 60 + mi
        total -= d * (minutes - minutes // 10)
    return total


def frames_to_tc(frames, rate, drop):
    """Frame number to a timecode label."""
    if frames < 0:
        raise ValueError("Negative frame numbers have no timecode.")
    nom = nominal(rate)
    if drop:
        d = DROP_RULES[rate]["drop"]
        frames_per_10min = nom * 60 * 10 - d * 9
        frames_per_min = nom * 60 - d
        tens = frames // frames_per_10min
        rem = frames % frames_per_10min
        if rem < frames_per_min + d:
            adjusted = frames + d * 9 * tens
        else:
            adjusted = (frames + d * 9 * tens
                        + d * ((rem - d) // frames_per_min))
        sep = ";"
    else:
        adjusted = frames
        sep = ":"
    f = adjusted % nom
    total_s = adjusted // nom
    s = total_s % 60
    mi = (total_s // 60) % 60
    h = total_s // 3600
    return f"{h:02d}:{mi:02d}:{s:02d}{sep}{f:02d}"


def frames_to_seconds(frames, rate):
    """Real elapsed time, which is NOT the timecode label divided by anything."""
    return float(Fraction(frames) / Fraction(rate))


# ---------------------------------------------------------------- edl


_EDL_EVENT = re.compile(
    r"^(?P<num>\d+)\s+(?P<reel>\S+)\s+(?P<track>\S+)\s+(?P<kind>\S+)\s+"
    r"(?:(?P<dur>\d+)\s+)?"
    r"(?P<src_in>[\d:;.]+)\s+(?P<src_out>[\d:;.]+)\s+"
    r"(?P<rec_in>[\d:;.]+)\s+(?P<rec_out>[\d:;.]+)\s*$")


def read_edl(path, rate=None, drop=None):
    """Read a CMX 3600 EDL into events.

    An EDL is a CLAIM about a timeline, not the timeline. It carries no effects,
    no multi layer video and no colour, and its frame rate is not in the file:
    the FCM line says only whether it is drop frame. The rate is a project fact
    and has to be supplied.
    """
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    title = None
    fcm_drop = None
    events, comments = [], []
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.upper().startswith("TITLE:"):
            title = line.split(":", 1)[1].strip()
            continue
        if line.upper().startswith("FCM:"):
            fcm_drop = "NON" not in line.upper()
            continue
        if line.startswith("*") or line.upper().startswith(("AUD", "SPLIT")):
            # A comment before the first event belongs to the header; after one,
            # it belongs to that event, and that is where the clip name lives.
            if events:
                events[-1]["comments"].append(line.strip())
            else:
                comments.append(line.strip())
            continue
        m = _EDL_EVENT.match(line.strip())
        if m:
            events.append({"event": int(m.group("num")), "reel": m.group("reel"),
                           "track": m.group("track"), "kind": m.group("kind"),
                           "src_in": m.group("src_in"), "src_out": m.group("src_out"),
                           "rec_in": m.group("rec_in"), "rec_out": m.group("rec_out"),
                           "comments": []})
        elif events:
            events[-1]["comments"].append(line.strip())
    if drop is None:
        drop = bool(fcm_drop)
    return {"file": os.path.abspath(path), "title": title,
            "fcm_drop_frame": fcm_drop, "events": events,
            "count": len(events), "header_comments": comments,
            "rate_supplied": str(C.rate(rate)) if rate else None,
            "note": "The rate is not in a CMX 3600 EDL. FCM says drop frame or "
                    "not, and nothing says 24 against 25. Supply it with --fps "
                    "and confirm it against the cut."}


def check_edl(doc, rate, drop):
    """Is the record timeline contiguous, and does every event have a length?"""
    rows, prev_out = [], None
    total = 0
    for e in doc["events"]:
        try:
            si = tc_to_frames(e["src_in"], rate, drop)
            so = tc_to_frames(e["src_out"], rate, drop)
            ri = tc_to_frames(e["rec_in"], rate, drop)
            ro = tc_to_frames(e["rec_out"], rate, drop)
        except ValueError as exc:
            rows.append({"event": e["event"], "faults": [str(exc)]})
            continue
        faults = []
        src_len, rec_len = so - si, ro - ri
        if src_len <= 0:
            faults.append(f"source out is not after source in ({src_len} frames)")
        if rec_len <= 0:
            faults.append(f"record out is not after record in ({rec_len} frames)")
        if src_len != rec_len and src_len > 0 and rec_len > 0:
            faults.append(f"source is {src_len} frames and record is {rec_len}: "
                          "a speed change, a fit to fill, or an error")
        if prev_out is not None:
            gap = ri - prev_out
            if gap > 0:
                faults.append(f"{gap} frame gap in the record timeline before this "
                              "event: black, or a missing event")
            elif gap < 0:
                faults.append(f"overlaps the previous event by {-gap} frames")
        prev_out = max(prev_out or 0, ro)
        total = max(total, ro)
        rows.append({"event": e["event"], "reel": e["reel"], "kind": e["kind"],
                     "src_frames": src_len, "rec_frames": rec_len,
                     "rec_in_frame": ri, "rec_out_frame": ro, "faults": faults})
    bad = [r for r in rows if r["faults"]]
    first = doc["events"][0]["rec_in"] if doc["events"] else None
    return {"file": doc["file"], "events": len(rows),
            "rate": str(C.rate(rate)), "drop_frame": drop,
            "record_start": first,
            "record_end_frame": total,
            "record_length_frames": total - (tc_to_frames(first, rate, drop)
                                             if first else 0),
            "record_length_tc": frames_to_tc(total, rate, drop),
            "rows": rows, "failing": len(bad),
            "verdict": ("the record timeline is contiguous and every event has a "
                        "length" if not bad else
                        f"{len(bad)} event(s) have something wrong"),
            "note": "This checks the EDL against ITSELF. It cannot tell you the "
                    "rate is right, and a whole EDL read at the wrong rate is "
                    "self consistent and completely wrong."}


def handles(tc_in, tc_out, handle, rate, drop, source_first=None, source_last=None):
    """The pull with handles, and whether it runs off the end of the source."""
    fin = tc_to_frames(tc_in, rate, drop)
    fout = tc_to_frames(tc_out, rate, drop)
    if fout <= fin:
        raise ValueError("The out point is not after the in point.")
    want_in, want_out = fin - handle, fout + handle
    faults = []
    if source_first is not None:
        first = tc_to_frames(source_first, rate, drop)
        if want_in < first:
            faults.append(f"the head handle runs {first - want_in} frames before "
                          "the start of the source")
            want_in = first
    elif want_in < 0:
        faults.append(f"the head handle runs {-want_in} frames before zero")
        want_in = 0
    if source_last is not None:
        last = tc_to_frames(source_last, rate, drop)
        if want_out > last:
            faults.append(f"the tail handle runs {want_out - last} frames past the "
                          "end of the source")
            want_out = last
    return {"cut_in": tc_in, "cut_out": tc_out, "handle_frames": handle,
            "cut_frames": fout - fin,
            "pull_in": frames_to_tc(want_in, rate, drop),
            "pull_out": frames_to_tc(want_out, rate, drop),
            "pull_frames": want_out - want_in,
            "pull_seconds": round(frames_to_seconds(want_out - want_in, rate), 4),
            "faults": faults,
            "note": "Out points here are EXCLUSIVE, the CMX convention: the out "
                    "frame is the first frame NOT used. A tool that treats them "
                    "as inclusive is one frame long on every event, which is the "
                    "single most common conform error there is."}


# ---------------------------------------------------------------- cli


def main(argv=None):
    ap = C.parser_for(__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    tc = sub.add_parser("tc", help="Timecode arithmetic")
    tcsub = tc.add_subparsers(dest="tccmd", required=True)
    for name, args_ in (("to-frames", ["tc"]), ("from-frames", ["frames"]),
                        ("add", ["tc"]), ("diff", ["a", "b"])):
        p = tcsub.add_parser(name)
        for a in args_:
            p.add_argument(a)
        p.add_argument("--fps", required=True)
        if name == "add":
            p.add_argument("--frames", type=int, required=True)
        C.add_json(p)

    ra = sub.add_parser("rates", help="What each rate is, exactly")
    C.add_json(ra)

    ed = sub.add_parser("edl", help="Read or check a CMX 3600 EDL")
    edsub = ed.add_subparsers(dest="edlcmd", required=True)
    er = edsub.add_parser("read")
    er.add_argument("file")
    er.add_argument("--fps")
    C.add_json(er)
    ec = edsub.add_parser("check")
    ec.add_argument("file")
    ec.add_argument("--fps", required=True)
    C.add_json(ec)

    ha = sub.add_parser("handles", help="The pull with handles")
    ha.add_argument("--in", dest="tin", required=True)
    ha.add_argument("--out", dest="tout", required=True)
    ha.add_argument("--handle", type=int, required=True)
    ha.add_argument("--fps", required=True)
    ha.add_argument("--source-first")
    ha.add_argument("--source-last")
    C.add_json(ha)

    du = sub.add_parser("duration", help="Frames, seconds and timecode")
    du.add_argument("--frames", type=int)
    du.add_argument("--seconds", type=float)
    du.add_argument("--fps", required=True)
    C.add_json(du)

    args = ap.parse_args(argv)

    if args.cmd == "tc":
        rate, drop = parse_rate(args.fps)
        if args.tccmd == "to-frames":
            n = tc_to_frames(args.tc, rate, drop)
            res = {"timecode": args.tc, "frames": n, "rate": str(rate),
                   "drop_frame": drop,
                   "elapsed_seconds": round(frames_to_seconds(n, rate), 6)}
            return C.emit(res, args.json, lambda r: print(
                f"  {r['timecode']} at {C.rate_str(C.rate(r['rate']))}"
                f"{' drop frame' if r['drop_frame'] else ''} is frame {r['frames']}, "
                f"{r['elapsed_seconds']}s of real time"))
        if args.tccmd == "from-frames":
            n = int(args.frames)
            res = {"frames": n, "timecode": frames_to_tc(n, rate, drop),
                   "rate": str(rate), "drop_frame": drop,
                   "elapsed_seconds": round(frames_to_seconds(n, rate), 6)}
            return C.emit(res, args.json, lambda r: print(
                f"  frame {r['frames']} is {r['timecode']}, "
                f"{r['elapsed_seconds']}s of real time"))
        if args.tccmd == "add":
            n = tc_to_frames(args.tc, rate, drop) + args.frames
            res = {"from": args.tc, "frames_added": args.frames,
                   "timecode": frames_to_tc(n, rate, drop), "frame": n}
            return C.emit(res, args.json, lambda r: print(
                f"  {r['from']} {r['frames_added']:+d} frames is {r['timecode']}"))
        if args.tccmd == "diff":
            a = tc_to_frames(args.a, rate, drop)
            b = tc_to_frames(args.b, rate, drop)
            res = {"a": args.a, "b": args.b, "frames": b - a,
                   "seconds": round(frames_to_seconds(b - a, rate), 6),
                   "duration_tc": frames_to_tc(abs(b - a), rate, drop)}
            return C.emit(res, args.json, lambda r: print(
                f"  {r['b']} minus {r['a']} is {r['frames']} frames, "
                f"{r['seconds']}s, {r['duration_tc']}"))
    if args.cmd == "rates":
        rows = []
        for text in ("24", "24000/1001", "25", "30000/1001", "30", "50",
                     "60000/1001", "60"):
            r = C.rate(text)
            rows.append({"rate": str(r), "exact": C.rate_str(r),
                         "float": round(float(r), 5),
                         "labels_per_second": nominal(r),
                         "drop_frame": r in DROP_RULES,
                         "labels_dropped_per_minute":
                             DROP_RULES.get(r, {}).get("drop", 0)})
        return C.emit(rows, args.json, lambda rs: [
            print(f"  {r['exact']:<22} {r['labels_per_second']} labels/s, "
                  + ("drop frame available, "
                     f"{r['labels_dropped_per_minute']} labels skipped per minute "
                     "except every tenth" if r["drop_frame"] else "no drop frame"))
            for r in rs])
    if args.cmd == "edl":
        if args.edlcmd == "read":
            doc = read_edl(args.file, args.fps)
            return C.emit(doc, args.json, lambda d: (
                print(f"{d['title'] or os.path.basename(d['file'])}: {d['count']} "
                      f"events, FCM drop frame {d['fcm_drop_frame']}"),
                [print(f"  {e['event']:>4} {e['reel']:<12} {e['kind']:<6} "
                       f"src {e['src_in']} {e['src_out']}  rec {e['rec_in']} "
                       f"{e['rec_out']}") for e in d["events"][:30]],
                print(f"  {d['note']}")))
        rate, drop = parse_rate(args.fps)
        doc = read_edl(args.file, args.fps, drop)
        res = check_edl(doc, rate, drop)
        return C.emit(res, args.json, lambda r: (
            print(f"{os.path.basename(r['file'])}: {r['events']} events at "
                  f"{r['rate']}{' drop frame' if r['drop_frame'] else ''}"),
            print(f"  record runs {r['record_start']} to {r['record_length_tc']}, "
                  f"{r['record_length_frames']} frames"),
            [print(f"  event {row['event']}: " + "; ".join(row["faults"]))
             for row in r["rows"] if row["faults"]],
            print(f"\n  {r['verdict']}"), print(f"  {r['note']}")))
    if args.cmd == "handles":
        rate, drop = parse_rate(args.fps)
        res = handles(args.tin, args.tout, args.handle, rate, drop,
                      args.source_first, args.source_last)
        return C.emit(res, args.json, lambda r: (
            print(f"  cut {r['cut_in']} to {r['cut_out']} is {r['cut_frames']} frames"),
            print(f"  pull {r['pull_in']} to {r['pull_out']} is {r['pull_frames']} "
                  f"frames ({r['pull_seconds']}s)"),
            [print(f"  WARNING: {f}") for f in r["faults"]],
            print(f"  {r['note']}")))
    if args.cmd == "duration":
        rate, drop = parse_rate(args.fps)
        if args.frames is None and args.seconds is None:
            raise ValueError("Give --frames or --seconds.")
        n = (args.frames if args.frames is not None
             else int(round(args.seconds * float(rate))))
        res = {"frames": n, "seconds": round(frames_to_seconds(n, rate), 6),
               "timecode": frames_to_tc(n, rate, drop), "rate": str(rate),
               "drop_frame": drop}
        return C.emit(res, args.json, lambda r: print(
            f"  {r['frames']} frames at {C.rate_str(C.rate(r['rate']))} is "
            f"{r['seconds']}s, timecode {r['timecode']}"))
    return 0


if __name__ == "__main__":
    sys.exit(C.main_guard(main))
