#!/usr/bin/env python3
"""Offline tests for every piece of arithmetic in this skill.

No video, no ffmpeg, no network. It must end "0 failures" before any number out
of these engines is worth anything.

  python selftest.py                 the maths and the parsers
  python selftest.py --with-media    also builds a clip and tests the ffmpeg paths

The rule this file exists to serve: a check that has never been run against a
file already known to be correct is not evidence. Every threshold below either
has a reference value behind it, or is a round trip that cannot be satisfied by
a broken implementation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _colour as COL  # noqa: E402
import _common as C  # noqa: E402
import _ttf  # noqa: E402
import archive as ARC  # noqa: E402
import conform as CONF  # noqa: E402
import deliver as DEL  # noqa: E402
import prove as PROVE  # noqa: E402
import route as ROUTE  # noqa: E402
import spec as SPEC  # noqa: E402
import standards as STD  # noqa: E402
import subs as SUBS  # noqa: E402
import supers as SUP  # noqa: E402

_ran = _fail = 0


def check(label, ok, detail=""):
    global _ran, _fail
    _ran += 1
    if ok:
        print(f"  ok   {label}" + (f"  ({detail})" if detail else ""))
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"  ({detail})" if detail else ""))


def section(name):
    print(f"\n{name}")


def a_font():
    """Any TrueType face on this machine, for the metrics tests."""
    for path in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                 "/System/Library/Fonts/Supplemental/Arial.ttf",
                 "/System/Library/Fonts/Helvetica.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(path):
            return path
    return None


# ---------------------------------------------------------------- rates


def test_rates():
    section("Frame rates are ratios, never floats")
    check("23.976 snaps to 24000/1001", str(C.rate("23.976")) == "24000/1001")
    check("29.97 snaps to 30000/1001", str(C.rate("29.97")) == "30000/1001")
    check("25 stays exact", str(C.rate("25")) == "25")
    check("24000/1001 passes through", str(C.rate("24000/1001")) == "24000/1001")
    check("30 is not snapped to 30000/1001", str(C.rate("30")) == "30")
    check("a float rate and its ratio compare equal",
          C.rate("23.98") == C.rate("24000/1001"))


def test_pixels():
    section("Pixel formats")
    for fmt, depth in (("yuv420p", 8), ("yuv422p10le", 10), ("p010le", 10),
                       ("yuv444p12le", 12), ("rgb48le", 16), ("gray", 8)):
        check(f"{fmt} reads as {depth} bit", C.pix_depth(fmt) == depth,
              f"got {C.pix_depth(fmt)}")
    for fmt, chroma in (("yuv420p", "420"), ("yuv422p10le", "422"),
                        ("yuva444p10le", "444"), ("rgb48le", "rgb"),
                        ("p010le", "420")):
        check(f"{fmt} chroma is {chroma}", C.pix_chroma(fmt) == chroma,
              f"got {C.pix_chroma(fmt)}")
    check("yuva444p10le carries alpha", C.pix_alpha("yuva444p10le"))
    check("yuv422p10le does not", not C.pix_alpha("yuv422p10le"))


def test_provenance():
    section("A number carries its decode path")
    a = C.Measurement(1.0, "a.mov", "ffmpeg:rgb48le")
    b = C.Measurement(1.0, "b.mov", "ffmpeg:rgb48le")
    c = C.Measurement(1.0, "c.mov", "opencv:bgr")
    check("same path compares", C.require_same_path(a, b))
    try:
        C.require_same_path(a, c)
        check("different paths refuse to compare", False)
    except ValueError:
        check("different paths refuse to compare", True)


# ---------------------------------------------------------------- colour


def test_colour():
    section("Colour difference")
    # The published CIEDE2000 reference rows. The colorgrade skill on this
    # machine is checked against the same table, so two independent
    # implementations here agree with one external source.
    cases = [
        ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
        ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
        ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
        ((50.0000, -1.3802, -84.2814), (50.0000, 0.0000, -82.7485), 1.0000),
        ((50.0000, -1.1848, -84.8006), (50.0000, 0.0000, -82.7485), 1.0000),
        ((50.0000, -0.9009, -85.5211), (50.0000, 0.0000, -82.7485), 1.0000),
        ((50.0000, 0.0000, 0.0000), (50.0000, -1.0000, 2.0000), 2.3669),
        ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0009), 7.1792),
        ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
        ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.2630),
        ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
        ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
    ]
    worst = max(abs(COL.delta_e_2000(a, b) - want) for a, b, want in cases)
    check("CIEDE2000 matches the reference table", worst < 1e-3,
          f"worst deviation {worst:.6f}")
    check("dE of a colour with itself is zero",
          COL.delta_e_2000((50, 10, -20), (50, 10, -20)) == 0.0)
    check("dE is symmetric",
          abs(COL.delta_e_2000((60, -34, 36), (60.5, -34, 39))
              - COL.delta_e_2000((60.5, -34, 39), (60, -34, 36))) < 1e-12)
    white = COL.hex_to_lab("#FFFFFF")
    check("white is L 100, a and b at zero",
          abs(white[0] - 100) < 0.01 and abs(white[1]) < 0.01 and abs(white[2]) < 0.01,
          f"{[round(v, 3) for v in white]}")
    black = COL.hex_to_lab("#000000")
    check("black is L 0", abs(black[0]) < 1e-9)
    # The demonstration the supers audit rests on.
    pale = "#E8E4DC"
    de_white = COL.delta_e_2000(COL.hex_to_lab("#FFFFFF"), COL.hex_to_lab(pale))
    de_turq = COL.delta_e_2000(COL.hex_to_lab("#27E2CC"), COL.hex_to_lab(pale))
    r_white = COL.contrast_ratio(COL.hex_to_rgb("#FFFFFF"), COL.hex_to_rgb(pale))
    r_turq = COL.contrast_ratio(COL.hex_to_rgb("#27E2CC"), COL.hex_to_rgb(pale))
    check("luma cannot separate two inks on a pale ground",
          abs(r_white - r_turq) < 0.05,
          f"contrast ratios {r_white:.2f} and {r_turq:.2f}")
    check("Lab can", de_turq > 3 * de_white,
          f"dE {de_white:.1f} against {de_turq:.1f}")


# ---------------------------------------------------------------- timecode


def test_timecode():
    section("Timecode and drop frame")
    rate, drop = CONF.parse_rate("29.97df")
    check("29.97df parses as 30000/1001 drop frame",
          str(rate) == "30000/1001" and drop)
    check("00:00:59:29 plus one frame is 00:01:00;02",
          CONF.frames_to_tc(CONF.tc_to_frames("00:00:59:29", rate, drop) + 1,
                            rate, drop) == "00:01:00;02")
    check("one hour of drop frame is 107892 frames",
          CONF.tc_to_frames("01:00:00;00", rate, drop) == 107892)
    check("frame 17982 is ten minutes",
          CONF.frames_to_tc(17982, rate, drop) == "00:10:00;00")
    check("a day of drop frame is 2589408 frames",
          CONF.tc_to_frames("23:59:59;29", rate, drop) + 1 == 2589408)
    bad = sum(1 for f in range(0, 108000)
              if CONF.tc_to_frames(CONF.frames_to_tc(f, rate, drop), rate, drop) != f)
    check("drop frame round trips over a whole hour", bad == 0,
          f"{bad} mismatches")
    r60, d60 = CONF.parse_rate("60000/1001df")
    check("59.94 drop frame hour is exactly twice the 29.97 one",
          CONF.tc_to_frames("01:00:00;00", r60, d60) == 2 * 107892)
    rnd, dnd = CONF.parse_rate("29.97")
    check("non drop hour is 108000 frames",
          CONF.tc_to_frames("01:00:00:00", rnd, dnd) == 108000)
    check("and runs 3.6 seconds long against the clock",
          abs(CONF.frames_to_seconds(108000, rnd) - 3603.6) < 0.01,
          f"{CONF.frames_to_seconds(108000, rnd):.3f}s")
    r25, d25 = CONF.parse_rate("25")
    bad25 = sum(1 for f in range(0, 90000)
                if CONF.tc_to_frames(CONF.frames_to_tc(f, r25, d25), r25, d25) != f)
    check("25 fps round trips over an hour", bad25 == 0, f"{bad25} mismatches")
    try:
        CONF.parse_rate("24df")
        check("24 fps refuses a drop frame flag", False)
    except ValueError:
        check("24 fps refuses a drop frame flag", True)
    try:
        CONF.tc_to_frames("00:01:00;00", rate, drop)
        check("an illegal drop frame label is refused", False)
    except ValueError:
        check("an illegal drop frame label is refused", True)


def test_handles():
    section("Handles, with exclusive out points")
    rate, drop = CONF.parse_rate("24")
    h = CONF.handles("01:00:04:12", "01:00:09:00", 12, rate, drop)
    check("cut length is exclusive of the out frame", h["cut_frames"] == 108,
          f"{h['cut_frames']}")
    check("handles add twice the handle", h["pull_frames"] == 108 + 24,
          f"{h['pull_frames']}")
    h2 = CONF.handles("01:00:00:06", "01:00:01:00", 12, rate, drop,
                      source_first="01:00:00:00")
    check("a head handle running off the source is clamped and reported",
          h2["pull_in"] == "01:00:00:00" and h2["faults"])


def test_edl():
    section("EDL")
    text = ("TITLE: TEST\nFCM: NON-DROP FRAME\n"
            "001  REEL01   V     C        01:00:04:12 01:00:09:00 "
            "01:00:00:00 01:00:04:12\n"
            "* FROM CLIP NAME: A.mov\n"
            "002  REEL02   V     C        02:14:00:00 02:14:03:00 "
            "01:00:04:12 01:00:07:12\n"
            "003  REEL03   V     C        03:00:00:00 03:00:05:00 "
            "01:00:08:00 01:00:13:00\n")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "cut.edl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        doc = CONF.read_edl(path, "24")
        check("three events read", doc["count"] == 3, str(doc["count"]))
        check("the title is read", doc["title"] == "TEST")
        check("FCM non drop frame is read", doc["fcm_drop_frame"] is False)
        check("comments attach to their event",
              doc["events"][0]["comments"] == ["* FROM CLIP NAME: A.mov"])
        rate, drop = CONF.parse_rate("24")
        res = CONF.check_edl(doc, rate, drop)
        check("the planted 12 frame gap is found",
              any("gap" in f for row in res["rows"] for f in row["faults"]))
        check("event lengths agree between source and record",
              all(row["src_frames"] == row["rec_frames"] for row in res["rows"]))


# ---------------------------------------------------------------- subtitles


def test_subs():
    section("Subtitles")
    srt = ("1\n00:00:01,000 --> 00:00:03,000\nLine one\nLine two\n\n"
           "2\n00:00:03,020 --> 00:00:03,400\n"
           "A single line that is far too long to read in that amount of time.\n\n"
           "3\n00:00:03,300 --> 00:00:12,000\nOne\nTwo\nThree\nFour\n")
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "a.srt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(srt)
        doc = SUBS.read(p)
        check("three events read", doc["count"] == 3)
        check("times parse", abs(doc["events"][0]["end"] - 3.0) < 1e-9)
        check("multi line text is kept as lines",
              doc["events"][0]["lines"] == ["Line one", "Line two"])

        # Round trip through every format this writes.
        for fmt in ("vtt", "ttml", "srt"):
            out = os.path.join(tmp, f"b.{fmt}")
            SUBS.write(doc["events"], out, fmt)
            back = SUBS.read(out)
            same = (back["count"] == doc["count"]
                    and all(abs(x["start"] - y["start"]) < 0.002
                            and abs(x["end"] - y["end"]) < 0.002
                            and x["lines"] == y["lines"]
                            for x, y in zip(back["events"], doc["events"])))
            check(f"round trip through {fmt} preserves times and text", same)

        res = SUBS.check(doc, {"max_cps": 17, "max_chars_per_line": 37,
                               "max_lines": 2, "min_duration_s": 1.0,
                               "max_duration_s": 7.0, "min_gap_s": 0.0833})
        faults = " ".join(f for row in res["rows"] for f in row["faults"])
        for expect in ("characters per second", "under the 1.0s minimum",
                       "over the 7.0s maximum", "lines against a maximum",
                       "characters against 37", "overlaps"):
            check(f"the checker sees '{expect}'", expect in faults)
        check("all three events are flagged", res["failing"] == 3)

        clean = [{"start": 0.0, "end": 2.0, "lines": ["Short line"], "index": 1,
                  "style": None}]
        ok = SUBS.check({"file": p, "format": "srt", "events": clean}, {})
        check("a clean event passes", ok["failing"] == 0)

        shifted = SUBS.shift(SUBS.read(p), 1.5)
        check("shift moves every event",
              abs(shifted["events"][0]["start"] - 2.5) < 1e-9)
        neg = SUBS.shift(SUBS.read(p), -10.0)
        check("shift clamps at zero rather than going negative",
              neg["events"][0]["start"] == 0.0)
        scaled = SUBS.retime(SUBS.read(p), 25, 24, "scale")
        check("retime by scale stretches by 25/24",
              abs(scaled["events"][0]["end"] - 3.0 * 25 / 24) < 1e-9)
        kept = SUBS.retime(SUBS.read(p), 25, 24, "keep")
        check("retime by keep moves nothing",
              abs(kept["events"][0]["end"] - 3.0) < 1e-9)


# ---------------------------------------------------------------- supers


def test_supers():
    section("Supers geometry")
    font = a_font()
    if not font:
        check("a TrueType face is available to test against", False,
              "no system font found")
        return
    face = _ttf.Font(font)
    check("units per em read", face.units_per_em in (1000, 2048, 4096),
          str(face.units_per_em))
    check("ascender is above the baseline", face.ascender > 0)
    check("descender is below it", face.descender < 0)
    m = face.measure("HH", 100)
    m1 = face.measure("H", 100)
    check("two glyphs advance twice one glyph",
          abs(m["advance"] - 2 * m1["advance"]) < 1e-9)
    check("ink is inside the advance",
          m["ink_left"] >= -1 and m["ink_right"] <= m["advance"] + 1)
    check("a space adds advance and no ink",
          face.measure("H H", 100)["advance"] > m1["advance"] * 2)
    scaled = face.measure("HELLO", 200)
    half = face.measure("HELLO", 100)
    check("measurement scales linearly with size",
          abs(scaled["advance"] - 2 * half["advance"]) < 1e-9)

    spec = {"raster": [3840, 2160], "font": font, "size": 102, "pitch": 122.5,
            "align": "flush_left",
            "anchor": {"em_box_bottom_y": 1992, "ink_right_x": 3634},
            "ink": ["#FFFFFF", "#27E2CC"],
            "blocks": [{"id": "b1", "lines": ["FIRST LINE", "A LONGER SECOND LINE"],
                        "in": 0, "out": 48}]}
    laid = SUP.plan(spec)
    block = laid["blocks"][0]
    last = block["lines"][-1]
    check("the last line's em box bottom lands on the anchor",
          abs((last["em_top"] + 102) - 1992) < 1e-6,
          f"{last['em_top'] + 102}")
    widest = max(ln["ink_box"][0] + ln["ink_box"][2] for ln in block["lines"])
    check("the widest line's ink right edge lands on the anchor",
          abs(widest - 3634) < 1e-6, f"{widest}")
    check("lines are one pitch apart",
          abs((block["lines"][1]["em_top"] - block["lines"][0]["em_top"]) - 122.5) < 1e-9)
    check("flush left means one origin for the block",
          block["lines"][0]["origin_x"] == block["lines"][1]["origin_x"])
    check("inks alternate down the block",
          block["lines"][0]["ink"] == "#FFFFFF"
          and block["lines"][1]["ink"] == "#27E2CC")

    centred = dict(spec, align="centre",
                   anchor={"em_box_bottom_y": 1992, "centre_x": 1920})
    cblock = SUP.plan(centred)["blocks"][0]
    for line in cblock["lines"]:
        mid = line["ink_box"][0] + line["ink_box"][2] / 2.0
        check(f"centred line '{line['line'][:12]}' is centred on 1920",
              abs(mid - 1920) < 1e-6, f"{mid:.3f}")

    try:
        SUP.plan(dict(spec, anchor={}))
        check("a missing vertical anchor is refused", False)
    except ValueError:
        check("a missing vertical anchor is refused", True)

    section("Safe areas")
    s = SUP.safe_check([0, 0, 1920, 1080], 1920, 1080)
    check("a full frame box is outside both safe areas",
          not s["action"]["inside"] and not s["title"]["inside"])
    check("action safe on 1920x1080 is 1786x1004",
          abs(s["action"]["area"][2] - 1785.6) < 0.5
          and abs(s["action"]["area"][3] - 1004.4) < 0.5,
          f"{s['action']['area'][2]}x{s['action']['area'][3]}")
    check("title safe on 1920x1080 is 1728x972",
          abs(s["title"]["area"][2] - 1728) < 0.001
          and abs(s["title"]["area"][3] - 972) < 0.001)
    inside = SUP.safe_check([200, 200, 1400, 600], 1920, 1080)
    check("a central box is inside both",
          inside["action"]["inside"] and inside["title"]["inside"])
    edge = SUP.safe_check([96, 54, 1728, 972], 1920, 1080)
    check("a box exactly on title safe counts as inside",
          edge["title"]["inside"])

    section("Contrast")
    c = SUP.contrast("#FFFFFF", "#E8E4DC")
    check("white on a pale ground is called too close",
          c["delta_e_2000"] < SUP.DE_FLOOR and "TOO CLOSE" in c["verdict"])
    c2 = SUP.contrast("#FFFFFF", "#141414")
    check("white on a dark ground is separated",
          c2["delta_e_2000"] > SUP.DE_FLOOR)
    d = SUP.shadow_dose(102, "soft")
    check("a shadow dose scales with the type",
          abs(d["offset_y_px"] - 102 * 0.04) < 1e-9
          and abs(SUP.shadow_dose(204, "soft")["blur_px"] - 2 * d["blur_px"]) < 1e-9)


# ---------------------------------------------------------------- prove


def test_prove():
    section("Proof arithmetic")
    e = PROVE.expect([1, 2, 3], [1, 2, 3])
    check("an exact match passes", e["pass"])
    e2 = PROVE.expect([1, 2, 3], [0, 1, 2])
    check("a constant shift FAILS rather than being absorbed", not e2["pass"])
    check("and the shift is named", e2["constant_shift_detected"] == -1,
          str(e2["constant_shift_detected"]))
    e3 = PROVE.expect([1, 2, 3], [0, 1, 2], offset=-1)
    check("the confirmed offset then passes", e3["pass"])
    e4 = PROVE.expect([1, 2, 3], [1, 2, 9])
    check("a real difference is not mistaken for a shift",
          not e4["pass"] and e4["constant_shift_detected"] is None)
    check("frame runs summarise as ranges",
          PROVE._summarise([1, 2, 3, 7, 8]).startswith("1-3, 7-8"))
    check("an empty list says none", PROVE._summarise([]) == "none")

    with tempfile.TemporaryDirectory() as tmp:
        old = os.path.join(tmp, "old")
        new = os.path.join(tmp, "new")
        os.makedirs(old)
        os.makedirs(new)
        for i in range(1, 6):
            for root in (old, new):
                with open(os.path.join(root, f"ov_{i:03d}.png"), "w") as fh:
                    fh.write("same")
        with open(os.path.join(new, "ov_003.png"), "w") as fh:
            fh.write("different")
        os.remove(os.path.join(old, "ov_005.png"))
        with open(os.path.join(new, "notes.txt"), "w") as fh:
            fh.write("no frame number here")
        pred = PROVE.predict_changes(old, new)
        check("a changed layer predicts its frame", 3 in pred["predicted_frames"])
        check("a layer present in only one build predicts its frame",
              5 in pred["predicted_frames"])
        check("unchanged layers predict nothing",
              pred["predicted_frames"] == [3, 5], str(pred["predicted_frames"]))
        check("a changed file with no number is reported separately",
              pred["unnumbered_changes"] == ["notes.txt"])
        check("the filename numbering base is reported",
              pred["filename_numbering_base"] == 1)

        rows = PROVE.sha_files([os.path.join(new, "ov_001.png")])
        ledger = os.path.join(tmp, "L.json")
        PROVE.write_ledger(rows, ledger)
        check("a fresh ledger verifies",
              PROVE.verify_ledger(ledger)["failures"] == 0)
        with open(os.path.join(new, "ov_001.png"), "w") as fh:
            fh.write("tampered")
        check("a changed file fails its ledger",
              PROVE.verify_ledger(ledger)["failures"] == 1)


# ---------------------------------------------------------------- the rest


def test_spec():
    section("Spec, profiles and the gate")
    names = SPEC.list_profiles()
    check("profiles are on the shelf", len(names) >= 3, ", ".join(names))
    for name in names:
        p = SPEC.load_profile(name)
        for key in ("slug", "name", "picture", "audio", "safe", "items"):
            check(f"{name} has {key}", key in p)
        check(f"{name} declares all four colour fields",
              all(p["picture"].get(k) for k in
                  ("primaries", "transfer", "matrix", "range")))
        check(f"{name} names a loudness target and its gate",
              p["audio"]["loudness"].get("target_i") is not None
              and p["audio"]["loudness"].get("gate"))
        check(f"{name} is tagged for verification", p.get("verify") is True)
    try:
        SPEC.load_profile("no_such_profile")
        check("an unknown profile is refused", False)
    except FileNotFoundError:
        check("an unknown profile is refused", True)

    fake = {"video": {"width": 1280, "height": 720, "bit_depth_declared": 10,
                      "codec": "prores", "fps_avg": "24", "transfer": None}}
    rows = SPEC.claims("CLIENT_FILM_4K_1080p_ProRes_10bit_v3.mov", fake)
    verdicts = {r["field"]: r["verdict"] for r in rows}
    check("a 4K claim on a 1280 wide file is contradicted",
          verdicts.get("width") == "CONTRADICTED")
    check("a 1080p claim on a 720 line file is contradicted",
          verdicts.get("height") == "CONTRADICTED")
    check("a true codec claim agrees", verdicts.get("codec") == "agrees")
    check("a true depth claim agrees", verdicts.get("bit_depth") == "agrees")
    check("nothing is claimed by a name that claims nothing",
          SPEC.claims("FILM.mov", fake) == [])


def test_route_and_standards():
    section("Routing and standards")
    hits = ROUTE.find("the phone screen slides against the bezel")
    check("a comp phrase routes to comp", hits and hits[0]["slug"] == "comp")
    check("a loudness phrase routes to sound",
          ROUTE.find("the mix is too quiet, what LUFS")[0]["slug"] == "sound")
    check("a subtitle phrase routes to subs",
          ROUTE.find("the subtitles are too fast")[0]["slug"] == "subs")
    check("an unmatched phrase returns nothing rather than guessing",
          ROUTE.find("make it more beautiful") == [])
    order = [d["step"] for d in ROUTE.spine()]
    check("the spine is a strict order", order == sorted(order)
          and len(set(order)) == len(order))
    check("every department names a gate",
          all(d["gate"].strip() for d in ROUTE.spine()))

    check("the R128 target is -23.0",
          STD.constant("r128.target_i")["value"] == -23.0)
    check("the R128 true peak ceiling is -1.0",
          STD.constant("r128.max_tp")["value"] == -1.0)
    check("action safe is 93 per cent",
          STD.constant("safe.action")["value"] == 0.93)
    check("title safe is 90 per cent",
          STD.constant("safe.title")["value"] == 0.90)
    check("a day of drop frame is recorded as 2589408",
          STD.constant("df.frames_per_day_2997")["value"] == 2589408)
    try:
        STD.constant("nope.nope")
        check("an unknown constant is refused", False)
    except ValueError:
        check("an unknown constant is refused", True)
    fresh = STD.freshness()
    check("every domain carries a verification date",
          all(r["as_of"] for r in fresh))
    check("the safe area figures agree between standards.py and supers.py",
          STD.constant("safe.action")["value"]
          == SUP.safe_check([0, 0, 10, 10], 100, 100)["action"]["fraction"])


def test_deliver_and_archive():
    section("Delivery precheck")
    res = DEL.check("tvc", ["master", "viewing_copy"])
    missing = {m["slug"] for m in res["missing"]}
    check("a missing textless is struck", "textless" in missing)
    check("a missing hash ledger is struck", "hash_ledger" in missing)
    check("a conditional item is an open question, not a gap",
          any(q["slug"] == "audio_stems" for q in res["open_questions"]))
    conf = DEL.check("tvc", ["master"], ["audio_stems"])
    check("a confirmed conditional item becomes required",
          any(m["slug"] == "audio_stems" for m in conf["missing"]))
    check("every delivery type lists only known items",
          all(slug in DEL.ITEMS for t in DEL.TYPES.values() for slug in t["items"]))
    try:
        DEL.check("tvc", ["not_an_item"])
        check("an unknown item is refused", False)
    except ValueError:
        check("an unknown item is refused", True)

    section("Archive gates")
    with tempfile.TemporaryDirectory() as tmp:
        live = os.path.join(tmp, "live")
        old = os.path.join(tmp, "old")
        os.makedirs(live)
        os.makedirs(old)
        keep = os.path.join(live, "KEEP.mov")
        condemned = os.path.join(old, "OLD.mov")
        for p, text in ((keep, "keep"), (condemned, "old")):
            with open(p, "w") as fh:
                fh.write(text)
        ledger = os.path.join(live, "SHA256.json")
        PROVE.write_ledger(PROVE.sha_files([keep]), ledger)

        r = ARC.sweep(ledger, [condemned])
        check("no restore path means no deletion", not r["pass"])
        check("nothing was deleted on a failing sweep",
              os.path.exists(condemned) and not r["executed"])
        r2 = ARC.sweep(ledger, [condemned], {"OLD.mov": keep})
        check("a proved restore path passes every gate", r2["pass"], r2["verdict"])
        check("the condemned were hashed before anything happened",
              r2["condemned"][0].get("sha256"))

        with open(keep, "w") as fh:
            fh.write("tampered")
        r3 = ARC.sweep(ledger, [condemned], {"OLD.mov": keep})
        check("a survivor that does not match its record stops the sweep",
              not r3["pass"])

        # The folder-name trap: a newer ledger naming the old file as a dependency.
        with open(keep, "w") as fh:
            fh.write("keep")
        dep = os.path.join(live, "DEPS_SHA256.json")
        with open(dep, "w") as fh:
            json.dump({"entries": [{"path": condemned, "sha256": "x"}]}, fh)
        r4 = ARC.sweep(ledger, [condemned], {"OLD.mov": keep})
        check("a file named by another version's records is protected",
              not r4["pass"] and r4["referenced_elsewhere"])


# ---------------------------------------------------------------- media


def test_media():
    section("The ffmpeg paths (--with-media)")
    if not (C.__dict__ and _which("ffmpeg") and _which("ffprobe")):
        check("ffmpeg and ffprobe are on PATH", False)
        return
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "clip.mov")
        run = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "testsrc2=size=320x180:rate=24:duration=1",
             "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
             "-colorspace", "bt709", "-color_range", "tv", clip],
            capture_output=True)
        if run.returncode != 0:
            check("a test clip could be built", False,
                  run.stderr.decode()[:200])
            return
        check("a test clip could be built", True)
        info = SPEC.probe(clip)
        check("the raster is read", info["video"]["width"] == 320)
        check("the rate is read as an exact ratio",
              str(C.rate(info["video"]["fps_avg"])) == "24")
        check("24 frames are counted", info["video"]["frames"] == 24)
        check("missing colour tags are flagged",
              any("UNTAGGED" in f for f in info["flags"]))
        depth = SPEC.measured_depth(clip, 2)
        check("the measured depth is read in the file's own pixel format",
              depth["measured_in_pix_fmt"] == "yuv422p10le")
        # testsrc2 is generated at 8 bit (lavfi hands out yuv420p), so the clip
        # above IS 8 bit content in a 10 bit container: 91 per cent of its luma
        # sits on the 4x lattice. Asking the detector to call that "not
        # promoted" asserted something false, and it could only ever pass where
        # the encode path happened to dither enough samples off the lattice --
        # a check passing for the wrong reason, on the one instrument whose
        # whole job is to catch promotion. It is the POSITIVE control instead,
        # and the negative one needs content that genuinely carries the depth:
        # a blur run AT 10 bit fills the lattice honestly.
        lat = (depth["lattice"] or [{}])[0].get("fraction_on_lattice")
        check("8 bit content in a 10 bit container is called promoted",
              depth["effective_bit_depth"] == 8,
              f"{depth['distinct_codes']} distinct codes, {lat} on the 4x lattice")
        native = os.path.join(tmp, "native10.mov")
        built = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             "testsrc2=size=320x180:rate=24:duration=1,format=yuv422p10le,"
             "gblur=sigma=0.7", "-c:v", "prores_ks", "-profile:v", "3",
             "-pix_fmt", "yuv422p10le", "-colorspace", "bt709",
             "-color_range", "tv", native], capture_output=True)
        if built.returncode != 0:
            check("a genuine 10 bit clip could be built", False,
                  built.stderr.decode()[:200])
        else:
            d10 = SPEC.measured_depth(native, 2)
            check("a genuine 10 bit clip is not called promoted",
                  d10["effective_bit_depth"] == 10,
                  f"{d10['distinct_codes']} distinct codes, "
                  f"{(d10['lattice'] or [{}])[0].get('fraction_on_lattice')} "
                  "on the 4x lattice")
        tl = PROVE.timeline(clip)
        check("an unspliced clip has a uniform timeline", tl["uniform"])
        check("every ProRes frame is a keyframe", tl["all_keyframes"])
        s = PROVE.seek_for_frame(clip, 5)
        check("a seek lands strictly inside the frame it names",
              s["frame_start_s"] < s["seek_seconds"] < s["frame_end_s"])
        f = PROVE.framemd5(clip, use_cache=False)
        check("per frame hashes come back", f["count"] == 24)
        d = PROVE.diff_frames(clip, clip, use_cache=False)
        check("a file diffed against itself has no changed frames",
              d["changed_count"] == 0 and d["identical_count"] == 24)
        floor = PROVE.generation_floor(clip, clip, 3)
        check("and its generation floor against itself is zero",
              floor["max_abs_diff"] == 0)
        _media_honest_enlargement(tmp)


def _media_honest_enlargement(tmp):
    """An honest enlargement of a 4:2:0 source must not be called a fake.

    The whole department turns on this one: a lossless Lanczos enlargement
    invents nothing, and before the luma fix it was struck at 7.1 dB on real
    graded footage for "adding what was not there". The cause was the chroma
    path, not the picture. This builds the same shape of file and requires the
    fidelity check to keep its hands off it.
    """
    import upres as U
    small = os.path.join(tmp, "small.mp4")
    big = os.path.join(tmp, "big.mp4")
    for cmd in (
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "testsrc2=size=640x360:rate=24:duration=0.5", "-c:v", "libx264",
         "-qp", "0", "-pix_fmt", "yuv420p", small],
        ["ffmpeg", "-y", "-v", "error", "-i", small, "-vf",
         "scale=1280:720:flags=lanczos", "-c:v", "libx264", "-qp", "0",
         "-pix_fmt", "yuv420p", big]):
        if subprocess.run(cmd, capture_output=True).returncode != 0:
            check("an honest enlargement could be built", False)
            return
    try:
        res = U.verify(small, big, frames=4)
    except Exception as exc:                                # pragma: no cover
        check("an honest enlargement can be verified", False, str(exc))
        return
    d = res["checks"]["downscale_back"]
    check("an honest 4:2:0 enlargement is not struck for inventing detail",
          d["verdict"] == "PASS",
          f"{d['verdict']}, luma deficit {d['deficit_db']} dB, "
          f"RGB deficit {d['rgb_deficit_db']} dB")
    check("and the RGB reading is the one that would have condemned it",
          d["rgb_deficit_db"] is not None
          and d["rgb_deficit_db"] > (d["deficit_db"] or 0.0),
          f"RGB {d['rgb_deficit_db']} against luma {d['deficit_db']}")


# ---------------------------------------------------------------- resolution


def _natural(w, h, seed=7, alpha=1.0):
    """1/f^alpha noise: the spectrum real pictures actually have."""
    import numpy as np
    rng = np.random.default_rng(seed)
    F = np.fft.fft2(rng.normal(size=(h, w)))
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.fftfreq(w)[None, :]
    r = np.sqrt(fy ** 2 + fx ** 2)
    r[0, 0] = 1e-6
    g = np.real(np.fft.ifft2(F / (r ** alpha)))
    return ((g - g.min()) / (g.max() - g.min())).astype(np.float32)


def test_resolution_spectrum():
    section("resolution: does a file carry the raster it claims")
    if not _comp_available():
        print("  SKIP needs numpy, OpenCV and SciPy (see the compositing note above)")
        return
    import cv2
    import _res as R

    RA = (1920, 1080)
    g = _natural(*RA)

    # Ground truth: a frame built with detail to Nyquist must read CARRIES, and
    # that must not depend on which power law it was built with.
    for alpha in (0.8, 1.0, 1.2):
        r = R.effective_resolution(_natural(*RA, alpha=alpha), RA)
        check(f"a native 1/f^{alpha} frame carries its raster",
              r["verdict"] == "CARRIES" and r["knee"] >= 0.85,
              f"knee {r['knee']}")

    def up(f, interp=cv2.INTER_LANCZOS4, src=None):
        s = g if src is None else src
        small = R.resize(s, (int(RA[0] / f), int(RA[1] / f)))
        return R.resize(small, RA, interp)

    # An enlargement is caught, and its source raster is named, across three
    # different resamplers and four factors.
    for f, interp, name, want in ((2, cv2.INTER_LANCZOS4, "Lanczos 2x", "960x540"),
                                  (2, cv2.INTER_CUBIC, "bicubic 2x", "960x540"),
                                  (1.5, cv2.INTER_LANCZOS4, "Lanczos 1.5x", "1280x720"),
                                  (3, cv2.INTER_LANCZOS4, "Lanczos 3x", "640x360"),
                                  (4, cv2.INTER_LANCZOS4, "Lanczos 4x", "480x270")):
        r = R.effective_resolution(up(f, interp), RA)
        check(f"{name} reads SHORT and names its source",
              r["verdict"] == "SHORT" and want in (r["consistent_with"] or ""),
              f"knee {r['knee']}, {r['consistent_with']}")

    # A good lens is not an enlargement. This is the case the first calibration
    # got wrong, by fitting the law inside the roll off it was meant to detect.
    r = R.effective_resolution(cv2.GaussianBlur(g, (0, 0), 0.5), RA)
    check("a frame through a good lens still reads CARRIES",
          r["verdict"] == "CARRIES", f"knee {r['knee']}, curvature {r['curvature_db']}")

    # The two refusals.
    import numpy as np
    r = R.effective_resolution(np.full((1080, 1920), 0.5, np.float32), RA)
    check("a flat card refuses to be measured", r["verdict"] == "UNDETERMINED")
    r = R.effective_resolution(cv2.GaussianBlur(g, (0, 0), 25.0), RA)
    check("a frame blurred past measurability refuses too",
          r["verdict"] == "UNDETERMINED",
          f"curvature {r['curvature_db']} against a limit of {R.MAX_CURV_DB}")

    # The case the spectrum alone gets wrong, and the exact fact that fixes it.
    nn = up(2, cv2.INTER_NEAREST)
    lat = R.block_structure(nn)
    check("a pixel doubled frame shows an exact replication lattice",
          lat["step"] == 2 and lat["ratio"] < 0.02, f"parity ratio {lat['ratio']}")
    check("and a Lanczos enlargement does not",
          R.block_structure(up(2))["ratio"] > 0.5,
          f"parity ratio {R.block_structure(up(2))['ratio']}")
    r = R.effective_resolution(nn, RA)
    check("so pixel doubling is caught despite reading to Nyquist",
          r["verdict"] == "SHORT" and r["effective_raster"] == "960x540",
          r["consistent_with"] or "")


def _moving(n=7, W=960, H=540, dx=7, dy=3, seed=11):
    """A textured world panned by an exact integer step, so flow has ground truth."""
    import cv2
    import numpy as np
    rng = np.random.default_rng(seed)
    big = _natural(2000, 1200, seed=seed)
    for _ in range(60):
        c = (int(rng.integers(0, 2000)), int(rng.integers(0, 1200)))
        cv2.circle(big, c, int(rng.integers(8, 45)), float(rng.uniform(0, 1)), -1)
    big = np.clip(big, 0, 1).astype(np.float32)
    seq = [np.ascontiguousarray(np.repeat(
        big[40 + dy * t:40 + dy * t + H, 60 + dx * t:60 + dx * t + W][..., None],
        3, 2).astype(np.float32)) for t in range(n)]
    return big, seq


def test_resolution_time():
    section("resolution: does an enlargement boil")
    if not _comp_available():
        print("  SKIP needs numpy, OpenCV and SciPy (see the compositing note above)")
        return
    import cv2
    import numpy as np
    import _res as R

    big, seq = _moving()
    H, W = seq[0].shape[:2]

    # The sign convention, against a flow that is KNOWN rather than solved. This
    # is the check that found the bug: applied backwards the error is 0.082,
    # which is indistinguishable from two unrelated frames, so every candidate
    # boils equally and nothing can be ranked.
    f = R.flow_dis(seq[0], seq[1])
    w, inside = R.warp_by_flow(seq[0], f)
    right = float(np.abs(w - seq[1])[inside].mean())
    w2, inside2 = R.warp_by_flow(seq[0], -f)
    wrong = float(np.abs(w2 - seq[1])[inside2].mean())
    check("the flow pulls frame t onto frame t+1, not the other way",
          right < 0.01 and wrong > 20 * right,
          f"{right:.5f} the right way, {wrong:.5f} reversed")
    check("and it recovers the exact translation it was given",
          abs(float(np.median(f[..., 0])) - 7) < 0.1
          and abs(float(np.median(f[..., 1])) - 3) < 0.1,
          f"({np.median(f[..., 0]):+.2f},{np.median(f[..., 1]):+.2f}) against (+7,+3)")

    # The statistic, on a control and on two candidates that differ ONLY in
    # whether their added detail travels with the picture.
    base = R.warping_error(list(zip(seq[:-1], seq[1:])))
    flows = base["flows"]
    inc_c = R.detail_incoherence(seq, flows)
    check("a control's own detail is coherent under its own flow",
          inc_c < 0.10, f"{inc_c:.4f} against {R.INCOHERENCE_INDEPENDENT} for independent")

    amp = 0.012
    boil = [np.clip(s + cv2.GaussianBlur(
        np.random.default_rng(100 + t).normal(0, amp, s.shape[:2]).astype(np.float32),
        (0, 0), 0.8)[..., None], 0, 1) for t, s in enumerate(seq)]
    world = cv2.GaussianBlur(
        np.random.default_rng(5).normal(0, 1, big.shape).astype(np.float32), (0, 0), 0.8)
    coherent = [np.clip(s + (world[40 + 3 * t:40 + 3 * t + H,
                                  60 + 7 * t:60 + 7 * t + W] * amp)[..., None], 0, 1)
                for t, s in enumerate(seq)]

    tb = R.temporal_stability(seq, boil)
    tc = R.temporal_stability(seq, coherent)
    check("detail re-invented every frame is caught",
          tb["verdict"] in ("MARGINAL", "BOILS"),
          f"excess {tb['excess_incoherence']}")
    check("the SAME amplitude added coherently is not",
          tc["verdict"] == "STABLE", f"excess {tc['excess_incoherence']}")
    check("and the two are separated by an order of magnitude",
          tb["excess_incoherence"] > 10 * max(tc["excess_incoherence"], 1e-4),
          f"{tb['excess_incoherence']} against {tc['excess_incoherence']}")

    # The bounded scale is the whole reason the verdict runs on excess: two
    # independent fields must land on 1/sqrt(2) whatever their amplitude.
    rng = np.random.default_rng(3)
    noise = [rng.normal(0, 0.05, seq[0].shape).astype(np.float32) for _ in range(3)]
    ind = R.detail_incoherence(noise, flows[:2])
    check("two unrelated detail fields read as fully independent",
          abs(ind - R.INCOHERENCE_INDEPENDENT) < 0.06,
          f"{ind:.4f} against {R.INCOHERENCE_INDEPENDENT}")


def test_resolution_engine():
    section("resolution: the engine's own arithmetic")
    import upres as U
    # Super Scale enumerations, read out of the manual that ships in the app.
    r = U.superscale(2)
    check("plain 2x is a single argument call",
          r["clip_property"]["call"] == "clip.SetClipProperty('Super Scale', 2)")
    r = U.superscale(2, 0.5, 0.2)
    check("2x Enhanced passes exactly four arguments",
          r["clip_property"]["call"] ==
          "clip.SetClipProperty('Super Scale', 2, 0.5, 0.2)")
    # Measured 2026-08-23 on a live Studio licence: a STRING is refused with a
    # bare False, which is also what the free edition returns. Failure 41.
    trap = r["project_setting"].get("type_trap", "")
    check("the project setting states its integer-only type trap",
          "INTEGER" in trap and "returns False" in trap)
    for bad in (lambda: U.superscale(3, 0.5, 0.2), lambda: U.superscale(2, 1.5)):
        try:
            bad()
            check("an impossible Super Scale setting is refused", False)
        except ValueError:
            check("an impossible Super Scale setting is refused", True)
    check("every route names its licence and its stage",
          all(v.get("licence") and v.get("stage") for v in U.ROUTES.values()))
    if _comp_available():
        check("the stable limit sits below the boiling limit",
              _res_consts()[0] < _res_consts()[1])

    # The downscale back check, and the one input that has no ceiling. A same
    # raster job is a stage 3 restore, which is a first class use of this
    # department: the control IS the source there, so every control reading is
    # infinite and the mean of no finite readings is a nan. A nan printed as a
    # number and, because every comparison against a nan is False, it also
    # switched the strike off in silence.
    d = U.downscale_back(57.05, 61.20, same_raster=False)
    check("a candidate inside the resampler's own loss passes",
          d["verdict"] == "PASS" and d["deficit_db"] == 4.15,
          f"deficit {d['deficit_db']} dB")
    d = U.downscale_back(40.0, 61.20, same_raster=False)
    check("and one well below that ceiling strikes",
          d["verdict"] == "STRIKE", f"deficit {d['deficit_db']} dB")
    d = U.downscale_back(47.05, None, same_raster=True)
    check("a same raster job is UNPROVEN, never a pass",
          d["verdict"] == "UNPROVEN" and d["deficit_db"] is None)
    check("and it says out loud that there is no ceiling",
          "no ceiling" in d["note"] and "UNPROVEN" in d["note"])
    check("nothing in that line prints as a nan",
          "nan" not in U._downscale_line(d).lower(),
          U._downscale_line(d))
    check("the boundary is the limit itself, not one side of it",
          U.downscale_back(55.2, 55.2 + U.DEFICIT_STRIKE_DB,
                           same_raster=False)["verdict"] == "PASS")

    # The other input with nothing to read against: two files that disagree
    # about the clock. Frame N of one is a different moment of the world from
    # frame N of the other, so a 24 dB deficit there is not evidence about the
    # enlargement at all. Reported as a strike it would be a true verdict with a
    # false cause, and the cause is what gets acted on: it sends someone back to
    # re-run an upscale that was never wrong.
    d = U.downscale_back(30.0, 54.0, same_raster=False, same_clock=False)
    check("a wrong clock makes the reading UNPROVEN, not a strike",
          d["verdict"] == "UNPROVEN" and d["deficit_db"] is None)
    check("and the printed line names the clock as the reason",
          "clocks disagree" in U._downscale_line(d)
          and "nan" not in U._downscale_line(d).lower(),
          U._downscale_line(d))
    d = U.downscale_back(None, 54.0, same_raster=False)
    check("an exact reduction is necessary, and said to be not sufficient",
          d["verdict"] == "PASS" and "not sufficient" in d["note"]
          and "nearest neighbour" in d["note"])
    d = U.downscale_back(54.0, 54.5, same_raster=False, rgb_deficit_db=9.9)
    check("the RGB deficit rides along as evidence and never gates",
          d["verdict"] == "PASS" and d["rgb_deficit_db"] == 9.9
          and d["measured_on"] == "luma")

    # Why that reading is taken on luma. A 4:2:0 source carries its chroma at
    # half raster, and every real enlarger resamples in YUV while this control
    # resamples in RGB, so the two reconstruct that chroma differently. On a real
    # 1080p job that difference alone read 7.07 dB, which is more than the strike
    # above, and it condemned a lossless Lanczos enlargement for inventing detail
    # it had never touched; the same file reads 0.76 dB on luma. Here is the
    # mechanism with the luma held exactly, by construction: R and B moved
    # against each other in the Rec.709 ratio cancel in luma and cannot cancel in
    # RGB.
    if not _comp_available():
        print("  SKIP the luma demonstration needs numpy "
              "(see the compositing note above)")
        return
    import numpy as np
    import _res as R
    base = _natural(64, 36, seed=3)
    src = np.dstack([base, base, base]).astype(np.float32)
    delta = (_natural(64, 36, seed=9) - 0.5) * 0.05
    cand = src.copy()
    cand[..., 0] += delta
    cand[..., 2] -= delta * (0.2126 / 0.0722)
    check("a chroma only difference cancels in luma down to float32's own floor",
          R.psnr(R.gray(cand), R.gray(src)) > 120.0,
          f"{R.psnr(R.gray(cand), R.gray(src)):.0f} dB")
    check("and the same difference is plain in RGB",
          R.psnr(cand, src) < 40.0, f"{R.psnr(cand, src):.1f} dB")


def _res_consts():
    import _res as R
    return R.STABLE_EXCESS, R.BOILING_EXCESS


# ---------------------------------------------------------------- compositing


def _comp_available():
    """The compositing engines live in this skill's own environment."""
    try:
        import numpy  # noqa: F401
        import cv2  # noqa: F401
        import scipy  # noqa: F401
    except ImportError:
        return False
    return True


def _project(aspect, f, rx, ry, dist, W=1920, H=1080):
    """A rectangle of known aspect, projected by a known pinhole camera."""
    import numpy as np
    w, h = aspect / 2.0, 0.5
    P = np.array([[-w, -h, 0], [w, -h, 0], [w, h, 0], [-w, h, 0]], float)
    cx, sx = np.cos(rx), np.sin(rx)
    cy, sy = np.cos(ry), np.sin(ry)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Pc = P @ (Ry @ Rx).T + np.array([0, 0, dist])
    return np.stack([f * Pc[:, 0] / Pc[:, 2] + W / 2,
                     f * Pc[:, 1] / Pc[:, 2] + H / 2], 1)


def test_comp_geometry():
    section("compositing: geometry")
    if not _comp_available():
        print("  SKIP the compositing engines need numpy, OpenCV and SciPy in "
              "this skill's own\n       environment. Create it and re-run this "
              "file with it:\n"
              "         python3 -m venv ~/.venvs/post\n"
              "         ~/.venvs/post/bin/pip install pillow numpy "
              "opencv-python-headless scipy\n"
              "         ~/.venvs/post/bin/python selftest.py")
        return
    import numpy as np
    import _geom as G

    # Zhang and He: from four corners of a KNOWN rectangle, both the aspect and
    # the focal length come back. Ground truth, not a tolerance pulled from air.
    c = _project(16 / 9, 1400, 0.35, 0.45, 4.0)
    r = G.rectangle_aspect(c, raster=(1920, 1080))
    check("rectangle aspect recovered from one perspective view",
          r["verdict"] == "DETERMINED" and abs(r["aspect"] - 16 / 9) < 1e-3,
          f"{r['aspect']:.4f} against 1.7778")
    check("and the focal length with it",
          abs(r["focal_px"] - 1400) < 1.0, f"{r['focal_px']:.1f} against 1400")

    # The degeneracy that turns up on a long lens, and the one on a level shot.
    c = _project(16 / 9, 9000, 0.02, 0.03, 60.0)
    r = G.rectangle_aspect(c, raster=(1920, 1080))
    check("a near affine view says UNDETERMINED rather than guessing",
          r["verdict"] == "UNDETERMINED",
          f"nearest vanishing point {min(r['vp_width_distance_pictures'], r['vp_height_distance_pictures']):.0f} pictures away")
    c = _project(16 / 9, 1400, 0.0, 0.55, 3.0)
    r = G.rectangle_aspect(c, raster=(1920, 1080))
    check("one vanishing point at infinity says FOCAL_FREE",
          r["verdict"] == "FOCAL_FREE")
    r = G.rectangle_aspect(c, raster=(1920, 1080), focal_px=1400)
    check("and given the lens, the aspect comes back exactly",
          abs(r["aspect"] - 16 / 9) < 1e-3, f"{r['aspect']:.4f}")

    # R is an invariant of the outline: the whole reason it is what content is
    # laid out at, and the aspect is not.
    dst = _project(16 / 9, 1400, 0.35, 0.45, 4.0)
    unit = [[0, 0], [1, 0], [1, 1], [0, 1]]
    Rs, resid = [], []
    for forced in (1.30, 1.60, 1.78, 2.20, 2.60):
        Hf = G.h_from_quad([[0, 0], [forced, 0], [forced, 1], [0, 1]], dst)
        resid.append(float(np.abs(G.apply_h(
            Hf, [[0, 0], [forced, 0], [forced, 1], [0, 1]]) - dst).max()))
        Rs.append(G.anisotropy(G.h_from_quad(unit, dst))["R"])
    check("R does not move across a wide band of forced aspects",
          max(Rs) - min(Rs) < 1e-6, f"R = {Rs[0]:.4f} for every one")
    check("and every one of those solves fits the boundary",
          max(resid) < 1e-3, f"worst boundary residual {max(resid):.1e} px")

    # A fronto parallel rectangle: R must simply BE its aspect.
    for a in (1.0, 16 / 9, 2.35):
        d = [[0, 0], [800 * a, 0], [800 * a, 800], [0, 800]]
        got = G.anisotropy(G.h_from_quad(unit, d))["R"]
        check(f"R equals the aspect on a flat on panel of {a:.4f}",
              abs(got - a) < 1e-6, f"{got:.6f}")

    # Four LINES determine a homography exactly, which is what a partly occluded
    # edge actually gives you.
    src = np.array(unit, float)
    ol = [G.line_through(src[i], src[(i + 1) % 4]) for i in range(4)]
    il = [G.line_through(dst[i], dst[(i + 1) % 4]) for i in range(4)]
    err = float(np.abs(G.apply_h(G.h_from_lines(ol, il), src) - dst).max())
    check("a homography from four lines equals one from four points",
          err < 1e-6, f"{err:.1e} px")

    # The rigid anchor, and the hold out that certifies it.
    rng = np.random.default_rng(7)
    shape = np.array([[0, 0], [1, 0], [1, 0.5], [0, 0.5]]) * 300 + [100, 100]
    warps, quads = [], []
    for k in range(20):
        ang, sc = 0.02 * k, 1 + 0.01 * k
        warps.append(np.array([[sc * np.cos(ang), -sc * np.sin(ang), 3 * k],
                               [sc * np.sin(ang), sc * np.cos(ang), 1.5 * k],
                               [0, 0, 1]]))
        quads.append(G.apply_h(warps[-1], shape) + rng.normal(0, 0.4, (4, 2)))
    anch = G.anchored_shape(quads, warps)
    raw = float(np.linalg.norm(
        np.stack(quads) - np.stack([G.apply_h(W, shape) for W in warps]),
        axis=2).mean())
    anc = float(np.linalg.norm(
        np.stack([G.apply_h(W, anch["shape"]) for W in warps]) -
        np.stack([G.apply_h(W, shape) for W in warps]), axis=2).mean())
    check("the anchored shape beats the raw per frame detections",
          anc < raw / 2, f"{raw:.3f} px raw against {anc:.3f} px anchored")
    ho = G.holdout_shape(quads, warps)
    check("and leaving each frame out predicts it to under a pixel",
          ho["verdict"] == "MEASURED" and ho["worst_px"] < 1.5,
          f"worst {ho['worst_px']:.3f} px over {ho['n_folds']} folds")

    # The horizon ratio: same real size in two frames without registering them.
    a = G.horizon_insert(1080, 500, 900, 1.6, 1.8)
    b = G.horizon_insert(1080, 500, 700, 1.6, 1.8)
    check("the horizon ratio scales with the drop and nothing else",
          abs(a["object_px"] - 450.0) < 1e-6 and
          abs(a["object_px"] / b["object_px"] - 2.0) < 1e-9,
          f"{a['object_px']:.1f} px and {b['object_px']:.1f} px")


def test_comp_pixels():
    section("compositing: pixels")
    if not _comp_available():
        return
    import numpy as np
    import _pix as P

    x = np.linspace(0, 1, 4096, dtype=np.float32)
    for tf in ("srgb", "bt1886", "gamma22"):
        err = float(np.abs(P.to_display(P.to_linear(x, tf), tf) - x).max())
        check(f"the {tf} transfer round trips", err < 1e-5, f"{err:.1e}")

    # Blending in code values instead of in light. The size of the error is the
    # point: it is not a subtlety, it is more than half the luminance.
    teal = np.array([[[0.0, 0.6, 0.6]]], np.float32)
    red = np.array([[[0.9, 0.0, 0.0]]], np.float32)
    half = np.array([[0.5]], np.float32)
    wrong = P.over(teal, half, red)
    right = P.to_display(P.over(P.to_linear(teal, "srgb"), half,
                                P.to_linear(red, "srgb")), "srgb")
    lw = float(P.linear_luma(P.to_linear(wrong, "srgb"))[0, 0])
    lr = float(P.linear_luma(P.to_linear(right, "srgb"))[0, 0])
    check("mixing code values darkens the result badly", lw < 0.6 * lr,
          f"{100 * (1 - lw / lr):.0f}% darker than the real mix")

    # Premultiplying before decoding, which is the same mistake wearing a hat.
    col = np.array([[[0.8, 0.2, 0.05]]], np.float32)
    al = np.array([[0.35]], np.float32)
    img = P.Image(col, "srgb", "synthetic", "test", alpha=al)
    good = img.as_linear()
    good.to_premultiplied()
    bad = P.to_linear(P.premultiply(col, al), "srgb")
    ratio = float((bad / np.maximum(good.rgb, 1e-9))[0, 0, 0])
    check("premultiplying before decoding is wrong, and by a lot",
          ratio < 0.5, f"the red channel comes out {ratio:.2f} of the truth")

    # The four notch. Reproduced, not asserted.
    art = np.zeros((128, 128, 3), np.float32)
    art[..., 0], art[..., 1], art[..., 2] = 0.9, 0.85, 0.1
    a = np.ones((128, 128), np.float32)
    pad = 24
    rgb_p = np.pad(art, ((pad, pad), (pad, pad), (0, 0)))
    a_p = np.pad(a, ((pad, pad), (pad, pad)))
    cn, _ = P.blur_naive(rgb_p, a_p, 3.0)
    cg, _ = P.blur_rgba(rgb_p, a_p, 3.0)
    ref = float(P.linear_luma(art[64:65, 64:65])[0, 0])
    mid = pad + 64
    pts = {"top": (pad + 1, mid), "bottom": (pad + 126, mid),
           "left": (mid, pad + 1), "right": (mid, pad + 126)}
    naive = [float(P.linear_luma(cn[y:y + 1, x:x + 1])[0, 0]) / ref
             for y, x in pts.values()]
    fixed = [float(P.linear_luma(cg[y:y + 1, x:x + 1])[0, 0]) / ref
             for y, x in pts.values()]
    corner = float(P.linear_luma(cn[pad + 8:pad + 9, pad + 8:pad + 9])[0, 0]) / ref
    check("a straight blur notches all four canvas contacts",
          max(naive) < 0.8, f"{min(naive):.3f} of the mark's own value")
    check("and leaves the middle of the artwork alone", corner > 0.95,
          f"{corner:.3f} eight pixels in from a corner")
    check("premultiplied and padded, all four are clean",
          min(fixed) > 0.99, f"{min(fixed):.3f}")


def test_comp_mattes():
    section("compositing: mattes")
    if not _comp_available():
        return
    import numpy as np
    import _matte as M
    import _pix as P

    def mk(rgb):
        return P.Image(rgb, "linear", "synthetic", "test:in-memory")

    H, W = 240, 320
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.hypot(yy - 120, xx - 150)
    alpha = np.clip((70 - r) / 6.0, 0, 1).astype(np.float32)
    fg = np.zeros((H, W, 3), np.float32)
    fg[..., 0], fg[..., 1], fg[..., 2] = 0.55, 0.28, 0.22
    green_panel = (np.abs(yy - 100) < 18) & (np.abs(xx - 150) < 28)
    fg[green_panel] = [0.10, 0.62, 0.12]
    bk1 = np.zeros((H, W, 3), np.float32)
    bk1[..., 1] = 0.62 + 0.10 * (xx / W)
    bk1[..., 0], bk1[..., 2] = 0.05, 0.09
    bk2 = bk1 * 0.45
    cf1 = fg * alpha[..., None] + bk1 * (1 - alpha[..., None])
    cf2 = fg * alpha[..., None] + bk2 * (1 - alpha[..., None])

    # Triangulation is EXACT, which is the whole reason it is worth having.
    t = M.triangulate(mk(cf1), mk(cf2), mk(bk1), mk(bk2))
    check("triangulation recovers alpha exactly from two backings",
          float(np.abs(t["alpha"] - alpha).max()) < 1e-5,
          f"worst {float(np.abs(t['alpha'] - alpha).max()):.1e}")
    inside = alpha > 0.9
    colour_err = float(np.abs(
        P.unpremultiply(t["foreground"], np.maximum(t["alpha"], 1e-3)) -
        fg)[inside].max())
    check("and the foreground colour with it", colour_err < 1e-5,
          f"worst {colour_err:.1e}")

    core = alpha > 0.98
    bg = alpha < 0.02
    kd = M.key_difference(mk(cf1), screen="green")
    check("the difference key clears the backing",
          float(kd["alpha"][bg].mean()) < 0.01,
          f"alpha {float(kd['alpha'][bg].mean()):.4f} on the backing")
    check("and it EATS a green foreground, as the assumption says it must",
          float(kd["alpha"][green_panel].mean()) < 0.1,
          f"alpha {float(kd['alpha'][green_panel].mean()):.4f} on a green jacket")

    v = M.violation_report(cf1, alpha, screen="green")
    check("the violation map finds that loss before anyone looks at the matte",
          v["violating_px"] >= int(green_panel.sum()) * 0.9,
          f"{v['violating_px']} px flagged, the panel is {int(green_panel.sum())}")

    ku = M.key_union(mk(cf1), screen="green")
    check("the union recovers what the difference key ate",
          float(ku["alpha"][green_panel].mean()) > 0.9,
          f"{float(ku['alpha'][green_panel].mean()):.3f} against "
          f"{float(ku['alpha_difference_key'][green_panel].mean()):.3f} alone")
    check("and the two keys disagree exactly where the trouble is",
          ku["union"]["verdict"] == "MEASURED" and
          ku["union"]["disagreement_px"] > 0,
          f"{ku['union']['disagreement_px']} px")
    check("the union still clears the backing",
          float(ku["alpha"][bg].mean()) < 0.02,
          f"alpha {float(ku['alpha'][bg].mean()):.4f}")
    check("and holds the real foreground solid",
          float(ku["alpha"][core].mean()) > 0.95,
          f"alpha {float(ku['alpha'][core].mean()):.4f}")

    # Despill: the cost is measured. A limit_min despill on a yellow rotates it
    # toward orange, which is what a client sees and calls "the badge went off".
    yellow = np.zeros((40, 40, 3), np.float32)
    yellow[..., 0], yellow[..., 1], yellow[..., 2] = 0.75, 0.70, 0.08
    solid = np.ones((40, 40), np.float32)
    d = M.despill(yellow, screen="green", form="limit_min", alpha=solid)
    check("despill reports the hue rotation it causes",
          d["hue_rotated_px"] > 0 and d["hue_rotation_worst_deg"] < -10,
          f"{d['hue_rotation_worst_deg']:.1f} degrees on a yellow")
    check("and the light it removes", d["foreground_luma_change_mean"] < -0.01,
          f"{d['foreground_luma_change_mean']:+.4f}")
    d2 = M.despill(yellow, screen="green", form="limit_min", alpha=solid,
                   preserve_luma=True)
    check("preserve_luma puts the light back exactly",
          abs(d2["foreground_luma_change_mean"]) < 1e-5,
          f"{d2['foreground_luma_change_mean']:+.6f}")


def test_comp_outlines():
    section("compositing: outlines and occlusion")
    if not _comp_available():
        return
    import numpy as np
    import _geom as G

    quad = np.array([[120, 90], [520, 70], [540, 400], [110, 380]], float)
    flat = G.fill_poly_subpixel((480, 640), quad)
    r = G.ring_from_mask(flat)
    check("a flat panel's outline is convex, so a hull is a no op",
          r["hull"]["deep_points"] == 0,
          f"{r['hull']['max_px']:.1f} px over 0 points")

    # A panel concave toward the camera: exactly one edge bows INTO the shape.
    ring = []
    centre = quad.mean(axis=0)
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        ts = np.linspace(0, 1, 90, endpoint=False)
        base = a[None, :] + (b - a)[None, :] * ts[:, None]
        if i == 2:
            n = np.array([-(b - a)[1], (b - a)[0]])
            n = n / np.linalg.norm(n)
            if n @ (centre - (a + b) / 2) < 0:
                n = -n
            base = base + (22.0 * np.sin(np.pi * ts))[:, None] * n[None, :]
        ring.append(base)
    curved = G.fill_poly_subpixel((480, 640), np.vstack(ring))
    r = G.ring_from_mask(curved, order=2)
    check("a curved panel's outline is NOT, and the hull cost is the bow",
          abs(r["hull"]["max_px"] - 22.0) < 3.0 and
          r["hull"]["deep_fraction"] > 0.05,
          f"a hull would move it {r['hull']['max_px']:.1f} px, over "
          f"{100 * r['hull']['deep_fraction']:.0f}% of the outline")
    check("and the bowed edge is named", 2 in r["bowed_edges"],
          f"bowed edges {r['bowed_edges']}")

    # The distinction that costs a version: a bite out of the MIDDLE of an edge
    # against an occluder that removes a CORNER.
    import cv2

    def verdicts(mask):
        rr = G.ring_from_mask(mask)
        a = (mask > 0).astype(np.float32)
        out = []
        for i in range(4):
            p0, p1 = rr["corners"][i], rr["corners"][(i + 1) % 4]
            pts, _, params, attempted = G.subpixel_edge_samples(a, p0, p1)
            length = float(np.linalg.norm(p1 - p0))
            out.append(G.edge_verdict(pts, length, params=params,
                                      attempted=attempted,
                                      expected_gap_px=0.08 * length))
        return rr, out

    bitten = flat.copy()
    mid = (quad[1] + quad[2]) / 2
    cv2.circle(bitten, (int(mid[0]), int(mid[1])), 26, 0, -1)
    rb, vb = verdicts(bitten)
    corner_ok = all("end" not in (vb[i - 1]["ends_unmeasured"] or []) and
                    "start" not in (vb[i]["ends_unmeasured"] or [])
                    for i in range(4))
    check("a bite out of the MIDDLE of an edge leaves every corner measured",
          corner_ok, "the bridge lies along the edge the bite came out of")

    cornered = flat.copy()
    cv2.circle(cornered, (int(quad[1][0]), int(quad[1][1])), 34, 0, -1)
    rc, vc = verdicts(cornered)
    lost = [i for i in range(4)
            if "end" in (vc[i - 1]["ends_unmeasured"] or []) or
            "start" in (vc[i]["ends_unmeasured"] or [])]
    check("an occluder that removes a CORNER leaves that corner UNMEASURED",
          lost == [1], f"corners lost: {lost}")

    # The int32 truncation that drags a whole shape toward the origin.
    tiny = np.array([[10.5, 10.5], [40.5, 10.5], [40.5, 40.5], [10.5, 40.5]])
    sub = G.fill_poly_subpixel((64, 64), tiny)
    trunc = np.zeros((64, 64), np.uint8)
    cv2.fillPoly(trunc, [tiny.astype(np.int32).reshape(-1, 1, 2)], 255)
    ys, xs = np.nonzero(sub > 127)
    yt, xt = np.nonzero(trunc > 127)
    check("filling at a sixteenth of a pixel does not drag the shape",
          abs((xs.mean() - xt.mean())) > 0.2,
          f"a truncated cast moves the centroid {xs.mean() - xt.mean():+.2f} px")


def test_comp_track():
    section("compositing: tracking")
    if not _comp_available():
        return
    import numpy as np
    import _geom as G
    import _track as T

    # Model selection on correspondences that genuinely NEED a homography.
    rng = np.random.default_rng(5)
    src = rng.uniform(0, 800, (400, 2))
    Htrue = np.array([[1.02, 0.03, 12.0], [-0.02, 0.98, -7.0],
                      [2.5e-4, 1.1e-4, 1.0]])
    dst = G.apply_h(Htrue, src) + rng.normal(0, 0.2, (400, 2))
    sel = T.model_select(src, dst)
    check("cross validation picks the model the motion actually needs",
          sel["chosen"] == "homography",
          "held out: " + ", ".join(
              f"{m} {v['heldout_median_px']:.2f}"
              for m, v in sel["models"].items() if v))

    # And it does NOT reach for a homography when affine is enough.
    Aff = np.array([[1.02, 0.03, 12.0], [-0.02, 0.98, -7.0], [0, 0, 1.0]])
    dst2 = G.apply_h(Aff, src) + rng.normal(0, 0.2, (400, 2))
    sel2 = T.model_select(src, dst2)
    check("and it does not reach for a richer one when a simpler fits",
          sel2["chosen"] in ("affine", "euclidean", "translation"),
          f"chose {sel2['chosen']}")

    # A region straddling two things that move differently is named as such.
    dst3 = dst2.copy()
    dst3[200:] = G.apply_h(np.array([[1, 0, -40.0], [0, 1, 25.0], [0, 0, 1]]),
                           src[200:])
    sel3 = T.model_select(src, dst3)
    check("two rigid bodies in one region are reported, not averaged",
          sel3.get("one_surface") == "NO",
          f"{100 * sel3['inlier_fraction']:.0f}% fit a single planar motion")

    # A collapsed solve is rejected from the warp alone, with no temporal model.
    corners = np.array([[0, 0], [100, 0], [100, 60], [0, 60]], float)
    check("a warp that mirrors the panel is refused",
          not T.warp_plausible(np.array([[-1, 0, 0], [0, 1, 0], [0, 0, 1]],
                                        float), corners, (640, 360))["ok"],
          "the winding flipped: that solve is looking at the back of it")
    check("and one that blows the area up by fifty times",
          not T.warp_plausible(np.diag([50.0, 50.0, 1.0]), corners,
                               (640, 360))["ok"])
    check("while a real move passes",
          T.warp_plausible(np.array([[1.02, 0.01, 5], [0.0, 1.01, -3],
                                     [0, 0, 1]]), corners, (640, 360))["ok"])

    # Smoothing: the two refusals.
    try:
        T.smooth([1, 2, 3, 4, 5], true_time=range(5), method="poly")
        ok = False
    except ValueError as exc:
        ok = "global polynomial" in str(exc)
    check("a global polynomial is refused outright", ok)
    try:
        T.smooth(np.arange(20.0), method="savgol")
        ok = False
    except ValueError as exc:
        ok = "true time" in str(exc)
    check("and smoothing without a true time vector is refused too", ok)

    # A settle is exactly what a low order fit deletes. Prove the local filter
    # keeps it and score the residual against the RAW values.
    n = 41
    t = np.arange(n, dtype=float)
    settle = 10.0 * np.exp(-t / 6.0) * np.cos(t / 2.0)
    noisy = settle + np.random.default_rng(2).normal(0, 0.15, n)
    sm = T.smooth(noisy, true_time=t, window=7, order=2)
    kept = float(np.abs(np.asarray(sm["values"]) - settle).max())
    check("a local filter against true time keeps a settle",
          kept < 1.2, f"worst {kept:.2f} against the true settle")
    check("and the residual is reported against the RAW values",
          "residual_vs_raw_px" in sm and sm["note"].startswith("the residual"))


def _which(tool):
    from shutil import which
    return which(tool)


def main(argv=None):
    with_media = "--with-media" in (argv or sys.argv[1:])
    print("postproduction selftest")
    test_rates()
    test_pixels()
    test_provenance()
    test_colour()
    test_timecode()
    test_handles()
    test_edl()
    test_subs()
    test_supers()
    test_prove()
    test_spec()
    test_route_and_standards()
    test_deliver_and_archive()
    test_comp_geometry()
    test_comp_pixels()
    test_comp_mattes()
    test_comp_outlines()
    test_comp_track()
    test_resolution_spectrum()
    test_resolution_time()
    test_resolution_engine()
    if with_media:
        test_media()
    print(f"\n{_ran} checks, {_fail} failures")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
