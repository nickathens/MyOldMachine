#!/usr/bin/env python3
"""Ingest: read a file's TRUE spec, and hold it against a delivery profile.

The first gate in the spine, and the cheapest one to skip. Filenames lie, review
links lie, and the email that came with the drive lies; only the file is honest.
On one real job every review file was named for a resolution it did not have and
the whole set was 720p, which nobody in the chain had raised.

  probe     what the file actually is
  claims    what its NAME claims, held against what it is
  depth     what bit depth it actually CARRIES, not what it declares
  check     the file against a named delivery profile, mismatch by mismatch
  gate      the delivery facts that must be named before anything renders
  profiles  the profiles on the shelf, and a blank template for a new job

Usage:
  python spec.py probe FILM.mov
  python spec.py claims "CLIENT_FILM_4K_ProRes_v3.mov"
  python spec.py depth FILM.mov --frames 5
  python spec.py check FILM.mov --profile broadcast_hd_r128
  python spec.py gate --profile broadcast_hd_r128
  python spec.py profiles
  python spec.py profile --template > myjob.json
  (add --json for structured output)
"""
from __future__ import annotations

import array
import json
import math
import os
import re
import subprocess
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _common as C  # noqa: E402

# Total samples per pixel for a frame, by chroma family. Used only to slice a
# raw buffer into frames; when it does not divide the buffer, the byte count wins.
_SAMPLES_PER_PIXEL = {"420": 1.5, "422": 2.0, "444": 3.0, "440": 2.0,
                      "411": 1.5, "410": 1.25, "gray": 1.0, "rgb": 3.0}

PROFILE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "profiles")

# The facts a render depends on. A standard's constant may default; a project's
# fact may not. These are all project facts.
GATE_FACTS = [
    ("frame size", "the full raster, in pixels, before any crop"),
    ("frame rate", "as a RATIO and whether it is native or conformed"),
    ("scan", "progressive, or which field is first"),
    ("codec and container", "what the master is written as"),
    ("bit depth", "8, 10, 12 or 16, and never lower than the source"),
    ("chroma", "420, 422, 444 or RGB"),
    ("colour", "primaries, transfer, matrix and range, all four"),
    ("audio layout", "channel count, order and sample rate"),
    ("loudness target", "the number AND the gate it is measured with"),
    ("safe area convention", "which action and title areas apply"),
    ("aspect and crop", "any letterbox, pillarbox or centre extraction"),
    ("deliverable list", "master, textless, versions, sidecars, viewing copies"),
]

_TEMPLATE = {
    "slug": "CHANGE_ME", "name": "", "as_of": "", "source": "", "verify": True,
    "picture": {"width": None, "height": None, "fps": None, "scan": None,
                "codec": [], "bit_depth": None, "chroma": None,
                "primaries": None, "transfer": None, "matrix": None,
                "range": None},
    "audio": {"codec": [], "sample_rate": None, "channels": None,
              "layout": None,
              "loudness": {"target_i": None, "tol_i": None, "max_tp": None,
                           "max_lra": None, "gate": None}},
    "safe": {"action": 0.93, "title": 0.90},
    "subtitles": {"max_lines": None, "max_chars_per_line": None,
                  "max_cps": None, "min_duration_s": None,
                  "max_duration_s": None, "min_gap_s": None, "formats": []},
    "items": [],
}


# ---------------------------------------------------------------- profiles


def load_profile(slug):
    path = slug if slug.endswith(".json") else os.path.join(PROFILE_DIR, f"{slug}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No profile {slug}. On the shelf: {', '.join(list_profiles())}. "
            "A real job supplies its own from the client's delivery document: "
            "spec.py profile --template > myjob.json"
        )
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def list_profiles():
    if not os.path.isdir(PROFILE_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(PROFILE_DIR) if f.endswith(".json"))


# ---------------------------------------------------------------- probe


def probe(path):
    """Everything the file says about itself, plus the flags that matter."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"No such file: {path}")
    data = C.ffprobe_json(path, ["-show_format", "-show_streams"])
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    vids = [s for s in streams if s.get("codec_type") == "video"]
    auds = [s for s in streams if s.get("codec_type") == "audio"]
    others = [s for s in streams if s.get("codec_type") not in ("video", "audio")]

    out = {
        "file": os.path.abspath(path),
        "size_bytes": int(fmt.get("size", 0) or 0),
        "container": fmt.get("format_name", ""),
        "duration_s": float(fmt.get("duration", 0) or 0) or None,
        "video": None, "audio": [], "other_streams": [], "flags": [],
    }
    flags = out["flags"]

    for s in others:
        out["other_streams"].append({"index": s.get("index"),
                                     "type": s.get("codec_type"),
                                     "codec": s.get("codec_name")})

    if not vids:
        flags.append("No video stream: this is an audio or data file.")
    else:
        if len(vids) > 1:
            flags.append(f"{len(vids)} video streams. Only the first is read here; "
                         "a second one is often an attached cover image.")
        v = vids[0]
        pix = v.get("pix_fmt")
        r_fps = C.rate(v.get("r_frame_rate", "0/1")) if v.get("r_frame_rate", "0/0") != "0/0" else None
        a_fps = C.rate(v.get("avg_frame_rate", "0/1")) if v.get("avg_frame_rate", "0/0") != "0/0" else None
        frames = v.get("nb_frames")
        frames = int(frames) if frames and str(frames).isdigit() else None
        if frames is None:
            frames = _count_packets(path)
        prim, trc, mtx = (v.get("color_primaries"), v.get("color_transfer"),
                          v.get("color_space"))
        rng = v.get("color_range")
        sar = v.get("sample_aspect_ratio") or "1:1"
        out["video"] = {
            "codec": v.get("codec_name"), "profile": v.get("profile"),
            "width": v.get("width"), "height": v.get("height"),
            "coded_width": v.get("coded_width"), "coded_height": v.get("coded_height"),
            "sample_aspect_ratio": sar,
            "display_aspect_ratio": v.get("display_aspect_ratio"),
            "pix_fmt": pix,
            "bit_depth_declared": C.pix_depth(pix),
            "chroma": C.pix_chroma(pix), "alpha": C.pix_alpha(pix),
            "fps_r": str(r_fps) if r_fps else None,
            "fps_avg": str(a_fps) if a_fps else None,
            "frames": frames,
            "time_base": v.get("time_base"),
            "start_time": v.get("start_time"),
            "field_order": v.get("field_order", "unknown"),
            "primaries": prim, "transfer": trc, "matrix": mtx, "range": rng,
            "colour_tagged": bool(prim and trc and mtx),
            "duration_s": float(v.get("duration", 0) or 0) or out["duration_s"],
        }
        vi = out["video"]
        if not vi["colour_tagged"]:
            missing = [n for n, x in (("primaries", prim), ("transfer", trc),
                                      ("matrix", mtx)) if not x]
            flags.append(
                "UNTAGGED COLOUR: the file does not declare "
                + ", ".join(missing)
                + ". A player is entitled to guess, and it will not always guess "
                  "the way the colourist did. This is a delivery fault, not a "
                  "neutral state."
            )
        if not rng:
            flags.append("Colour RANGE not declared (tv or pc). Full range read as "
                         "limited crushes blacks and clips whites.")
        if r_fps and a_fps and r_fps != a_fps:
            flags.append(
                f"Frame rate disagrees with itself: container rate {C.rate_str(r_fps)}, "
                f"average rate {C.rate_str(a_fps)}. The file may be a higher rate "
                "conformed down by dropping or repeating pictures. Run "
                "prove.py timeline before treating either as the truth."
            )
        if sar not in ("1:1", "0:1", None):
            flags.append(f"Pixels are not square (SAR {sar}). Every geometry number "
                         "below is in STORED pixels, not display pixels.")
        if vi["field_order"] not in ("progressive", "unknown", None):
            flags.append(f"Interlaced ({vi['field_order']}). Field order is a project "
                         "fact and getting it backwards is invisible on a still.")
        if vi["alpha"]:
            flags.append("Alpha channel present. Check it is premultiplied the way the "
                         "compositor expects before resampling or blurring it.")
        if frames and vi["duration_s"] and a_fps:
            implied = frames / float(a_fps)
            if abs(implied - vi["duration_s"]) > 1.5 / float(a_fps):
                flags.append(
                    f"Frame count and duration disagree: {frames} frames at "
                    f"{C.rate_str(a_fps)} is {implied:.3f}s but the stream says "
                    f"{vi['duration_s']:.3f}s. On a spliced master this is the "
                    "signature of joins that left the timeline non uniform."
                )

    for a in auds:
        depth = a.get("bits_per_raw_sample") or a.get("bits_per_sample")
        out["audio"].append({
            "index": a.get("index"), "codec": a.get("codec_name"),
            "sample_rate": int(a.get("sample_rate", 0) or 0) or None,
            "channels": a.get("channels"),
            "layout": a.get("channel_layout"),
            "sample_fmt": a.get("sample_fmt"),
            "bit_depth": int(depth) if depth and str(depth).isdigit() else None,
            "duration_s": float(a.get("duration", 0) or 0) or None,
        })
    if not auds:
        flags.append("No audio stream. If the delivery expects one, this is silent, "
                     "not mute: some players show an error rather than play it.")
    elif len(auds) > 1:
        flags.append(f"{len(auds)} audio streams. Confirm which is the mix and which "
                     "are stems or alternates, and in what order the delivery wants them.")
    return out


def _count_packets(path):
    """Frame count by counting packets, which does not decode."""
    try:
        data = C.ffprobe_json(path, ["-select_streams", "v:0", "-count_packets",
                                     "-show_entries", "stream=nb_read_packets"])
        streams = data.get("streams") or []
        if streams and streams[0].get("nb_read_packets"):
            return int(streams[0]["nb_read_packets"])
    except (RuntimeError, ValueError, KeyError):
        pass
    return None


# ---------------------------------------------------------------- claims


# Tokens a filename uses to claim something, and what each one claims.
_CLAIM_PATTERNS = [
    (r"\b(4k|uhd)\b", "width", 3840),
    (r"\b(2160p?|2160)\b", "height", 2160),
    (r"\b(1080p?|1080i?|fhd)\b", "height", 1080),
    (r"\b(720p?|hd720)\b", "height", 720),
    (r"\b(576[ip]?)\b", "height", 576),
    (r"\b(8k)\b", "width", 7680),
    (r"\b(\d{2,3}(?:\.\d+)?)\s*fps\b", "fps", None),
    (r"\b(23\.?976|2398)\b", "fps", "24000/1001"),
    (r"\b(2997|29\.?97)\b", "fps", "30000/1001"),
    (r"\b(24p|25p|30p|50p|60p)\b", "fps", None),
    (r"\b(10\s*bit|10bit)\b", "bit_depth", 10),
    (r"\b(12\s*bit|12bit)\b", "bit_depth", 12),
    (r"\b(8\s*bit|8bit)\b", "bit_depth", 8),
    (r"\b(prores|dnxh[dr]|h ?26[45]|hevc|avc)\b", "codec", None),
    (r"\b(hdr10?|hlg|pq|rec2020|bt2020)\b", "hdr", None),
    (r"\b(textless|txtless|clean)\b", "textless", True),
]


def claims(name, measured=None):
    """What the NAME claims, and where the file contradicts it."""
    # Underscores and dots are word characters to a regex, so a token buried in
    # CLIENT_FILM_4K_v3.mov never matches a \b boundary. Split on them first.
    base = re.sub(r"[_\-.]+", " ", os.path.basename(name).lower())
    found = []
    for pattern, field, value in _CLAIM_PATTERNS:
        m = re.search(pattern, base)
        if not m:
            continue
        token = m.group(1)
        claimed = value
        if field == "fps" and value is None:
            claimed = token.rstrip("p")
        if field == "codec":
            claimed = token.replace(" ", "")
        found.append({"token": token, "field": field, "claims": claimed})

    rows = []
    for f in found:
        row = dict(f, measured=None, verdict="unchecked")
        if measured and measured.get("video"):
            v = measured["video"]
            got = {"width": v["width"], "height": v["height"],
                   "bit_depth": v["bit_depth_declared"], "codec": v["codec"],
                   "fps": v["fps_avg"], "hdr": v["transfer"],
                   "textless": None}.get(f["field"])
            row["measured"] = got
            if got is None:
                row["verdict"] = "not measurable from the file"
            elif f["field"] == "fps":
                try:
                    row["verdict"] = ("agrees" if C.rate(f["claims"]) == C.rate(got)
                                      else "CONTRADICTED")
                except (ValueError, ZeroDivisionError):
                    row["verdict"] = "unparsed"
            elif f["field"] in ("codec", "hdr"):
                row["verdict"] = ("agrees" if str(f["claims"]).lower() in str(got).lower()
                                  else "CONTRADICTED")
            elif f["field"] == "width" and f["claims"] == 3840:
                row["verdict"] = "agrees" if got >= 3840 else "CONTRADICTED"
            else:
                row["verdict"] = "agrees" if f["claims"] == got else "CONTRADICTED"
        rows.append(row)
    return rows


# ---------------------------------------------------------------- depth


def measured_depth(path, frames=3, start=0):
    """What bit depth the picture actually CARRIES, not what it declares.

    The pixel format is a claim. Content that began at 8 bit and was promoted
    into a 10 bit container is still 8 bit content, and every tag says 10.

    Two measurements, because one of them dies under a lossy codec:

    1. DISTINCT CODES. Promotion is injective, so a lossless 10 bit file made
       from 8 bit content still carries at most 256 distinct values. Decisive
       when it holds, and it only holds on a lossless path.
    2. LATTICE FRACTION. Promoting 8 bit to 10 bit multiplies by 4, so every
       sample lands on a multiple of 4. A lossy re-encode moves samples off
       that lattice but not far: measured on this machine, 2026-08-23, an 8 bit
       clip promoted and written as ProRes HQ kept 95.2 per cent of its luma on
       the multiple-of-4 lattice, while native 10 bit ProRes sat at 31.5 per
       cent against a 25 per cent chance level. That separation is what the
       verdict below reads.

    The samples must be read in the file's OWN pixel format. Asking ffmpeg for
    a wider one runs the scaler, which range converts and dithers: the same 10
    bit file then reports 27946 distinct codes instead of 805. Measured, not
    assumed, and it is the same family of mistake as comparing two numbers that
    came through different decode paths.
    """
    C.need("ffmpeg")
    info = probe(path)
    v = info.get("video")
    if not v:
        raise ValueError("No video stream to measure.")
    w, h, pix = v["width"], v["height"], v["pix_fmt"]
    if not (w and h and pix):
        raise ValueError("Frame size or pixel format unknown; cannot measure depth.")
    declared = v["bit_depth_declared"] or 8
    bps = 1 if declared <= 8 else 2
    cmd = ["ffmpeg", "-v", "error", "-nostdin",
           "-i", str(path), "-vf", f"select='gte(n\\,{start})'",
           "-frames:v", str(frames), "-fps_mode", "passthrough",
           "-pix_fmt", pix, "-f", "rawvideo", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg could not decode a frame: "
                           + proc.stderr.decode("utf-8", "replace")[:300])
    raw = proc.stdout
    if not raw:
        raise RuntimeError("Decoded no data. Is --start past the end?")
    plane_bytes = w * h * bps
    frame_bytes = int(plane_bytes * _SAMPLES_PER_PIXEL.get(C.pix_chroma(pix), 3))
    if frame_bytes <= 0 or len(raw) % frame_bytes:
        frame_bytes = len(raw) // max(1, frames) or len(raw)
    got_frames = max(1, len(raw) // frame_bytes)
    packed = C.pix_chroma(pix) in ("rgb", None)

    union, per_frame, planes = set(), [], []
    for i in range(got_frames):
        chunk = raw[i * frame_bytes:(i + 1) * frame_bytes]
        sample_bytes = chunk if packed else chunk[:plane_bytes]
        if bps == 2:
            arr = array.array("H")
            arr.frombytes(sample_bytes[:len(sample_bytes) // 2 * 2])
            if sys.byteorder == "big":
                arr.byteswap()
            seen = set(arr)
        else:
            seen = set(sample_bytes)
        per_frame.append({"frame": start + i, "distinct": len(seen)})
        union |= seen
        planes.append(sample_bytes)

    n = len(union)
    gcd = 0
    for x in union:
        gcd = math.gcd(gcd, x)

    lattice = []
    for candidate in (8, 10, 12):
        if candidate >= declared:
            continue
        step = 1 << (declared - candidate)
        if step > 256:
            continue
        frac = _lattice_fraction(planes, step, bps)
        lattice.append({"candidate_bit_depth": candidate, "step": step,
                        "fraction_on_lattice": round(frac, 4),
                        "chance_level": round(1.0 / step, 4)})

    effective, verdict = declared, "consistent with the declared depth"
    for bits in (8, 10, 12, 16):
        if n <= (1 << bits):
            distinct_fits = bits
            break
    if distinct_fits < declared:
        effective = distinct_fits
        verdict = (f"carries only {n} distinct codes, which fits in "
                   f"{distinct_fits} bit against a declared {declared}. On a "
                   f"lossless path that is decisive.")
    else:
        for row in lattice:
            if row["fraction_on_lattice"] >= 0.90:
                effective = row["candidate_bit_depth"]
                verdict = (f"{row['fraction_on_lattice'] * 100:.1f} per cent of "
                           f"samples sit on the {row['step']}x lattice against a "
                           f"{row['chance_level'] * 100:.0f} per cent chance "
                           f"level. This is {row['candidate_bit_depth']} bit "
                           f"content in a {declared} bit container, with one "
                           f"lossy generation on top.")
                break

    return {"file": os.path.abspath(path), "declared_bit_depth": declared,
            "measured_in_pix_fmt": pix,
            "plane": "all channels" if packed else "luma",
            "frames_measured": got_frames, "first_frame": start,
            "distinct_codes": n, "per_frame": per_frame,
            "gcd_of_codes": gcd, "lattice": lattice,
            "effective_bit_depth": effective, "verdict": verdict,
            "decode_path": f"ffmpeg:{pix} (no scaler, no dither)",
            "caveat": "A flat or graded-to-flat frame carries few codes "
                      "honestly, and a heavy re-encode fills the alphabet. "
                      "Measure several frames with real texture, and read the "
                      "lattice fraction rather than the distinct count on "
                      "anything that has been through a lossy codec."}


def _lattice_fraction(planes, step, bps):
    """Fraction of samples divisible by step, counted at C speed.

    For any step up to 256 the low byte alone decides divisibility, so the
    whole test is a byte translation and a count. A per sample Python loop over
    a UHD frame is tens of seconds; this is milliseconds.
    """
    table = bytes(1 if (b % step == 0) else 0 for b in range(256))
    hits = total = 0
    for plane in planes:
        low = plane[0::2] if bps == 2 else plane
        hits += low.translate(table).count(1)
        total += len(low)
    return hits / total if total else 0.0


# ---------------------------------------------------------------- check


def check(path, profile):
    """Hold a file against a delivery profile, field by field."""
    info = probe(path)
    rows = []

    def add(field, want, got, ok, severity="strike", note=""):
        if want in (None, [], ""):
            rows.append({"field": field, "want": "not specified", "got": got,
                         "verdict": "ASK", "severity": "query",
                         "note": note or "The profile does not name this. It is a "
                                         "project fact: ask before rendering."})
        elif got in (None, ""):
            rows.append({"field": field, "want": want, "got": "unknown",
                         "verdict": "UNKNOWN", "severity": severity,
                         "note": note or "Not readable from the file."})
        else:
            rows.append({"field": field, "want": want, "got": got,
                         "verdict": "ok" if ok else "MISMATCH",
                         "severity": "" if ok else severity, "note": note})

    p_pic = profile.get("picture", {})
    v = info.get("video") or {}
    add("width", p_pic.get("width"), v.get("width"),
        p_pic.get("width") == v.get("width"))
    add("height", p_pic.get("height"), v.get("height"),
        p_pic.get("height") == v.get("height"))

    want_fps = p_pic.get("fps")
    got_fps = v.get("fps_avg")
    if want_fps and got_fps:
        same = C.rate(want_fps) == C.rate(got_fps)
        add("frame rate", C.rate_str(C.rate(want_fps)), C.rate_str(C.rate(got_fps)), same,
            note="" if same else "A rate change is never silent: it moves every "
                                 "timecode, every subtitle and the conform.")
    else:
        add("frame rate", want_fps, got_fps, False)

    want_codec = p_pic.get("codec") or []
    got_codec = v.get("codec")
    add("codec", want_codec, got_codec,
        bool(got_codec) and got_codec in want_codec)

    want_depth = p_pic.get("bit_depth")
    got_depth = v.get("bit_depth_declared")
    if want_depth and got_depth:
        if got_depth == want_depth:
            add("bit depth", want_depth, got_depth, True)
        elif got_depth < want_depth:
            add("bit depth", want_depth, got_depth, False,
                note="Lower than the delivery. Never deliver less depth than the "
                     "spec, and never less than the source.")
        else:
            add("bit depth", want_depth, got_depth, False, severity="query",
                note="Higher than the delivery asks. Usually harmless, but "
                     "confirm the client can ingest it.")
    else:
        add("bit depth", want_depth, got_depth, False)

    add("chroma", p_pic.get("chroma"), v.get("chroma"),
        p_pic.get("chroma") == v.get("chroma"))
    for field, key in (("colour primaries", "primaries"),
                       ("colour transfer", "transfer"),
                       ("colour matrix", "matrix"),
                       ("colour range", "range")):
        want, got = p_pic.get(key), v.get(key)
        add(field, want, got, want == got,
            note="" if want == got else "Colour metadata is part of the "
                                        "deliverable. A hash proves the pixels "
                                        "and says nothing about how the file "
                                        "declares them.")
    want_scan = p_pic.get("scan")
    got_scan = v.get("field_order")
    if got_scan == "unknown":
        got_scan = None
    add("scan", want_scan, got_scan,
        (want_scan == got_scan) or (want_scan == "progressive" and got_scan is None),
        severity="query",
        note="ffprobe reports unknown field order on many progressive files."
             if got_scan is None else "")

    p_aud = profile.get("audio", {})
    a = (info.get("audio") or [{}])[0]
    add("audio codec", p_aud.get("codec") or [], a.get("codec"),
        bool(a.get("codec")) and a.get("codec") in (p_aud.get("codec") or []))
    add("sample rate", p_aud.get("sample_rate"), a.get("sample_rate"),
        p_aud.get("sample_rate") == a.get("sample_rate"))
    add("channels", p_aud.get("channels"), a.get("channels"),
        p_aud.get("channels") == a.get("channels"))
    add("channel layout", p_aud.get("layout"), a.get("layout"),
        p_aud.get("layout") == a.get("layout"), severity="query")

    loud = (p_aud.get("loudness") or {})
    rows.append({"field": "loudness", "want": f"{loud.get('target_i')} LUFS "
                                              f"+/-{loud.get('tol_i')} LU, "
                                              f"TP {loud.get('max_tp')} dBTP, "
                                              f"gate {loud.get('gate')}",
                 "got": "not measured here", "verdict": "DEFERRED",
                 "severity": "query",
                 "note": "Measure it with audio.py check; it needs a decode pass."})

    strikes = [r for r in rows if r["verdict"] in ("MISMATCH", "UNKNOWN")
               and r["severity"] == "strike"]
    queries = [r for r in rows if r["severity"] == "query"
               and r["verdict"] in ("MISMATCH", "UNKNOWN", "ASK", "DEFERRED")]
    return {"file": info["file"], "profile": profile.get("slug"),
            "profile_verify": profile.get("verify", False),
            "profile_source": profile.get("source", ""),
            "rows": rows, "strikes": len(strikes), "queries": len(queries),
            "flags": info["flags"],
            "verdict": "would be struck" if strikes else
                       ("open questions" if queries else "matches the profile")}


# ---------------------------------------------------------------- cli


def _print_probe(info):
    v = info["video"]
    print(f"{os.path.basename(info['file'])}")
    print(f"  container {info['container']}, {info['size_bytes'] / 1e9:.2f} GB")
    if v:
        depth = v["bit_depth_declared"]
        print(f"  picture   {v['width']}x{v['height']} {v['codec']} "
              f"({v['profile'] or 'no profile'}), {v['pix_fmt']}, "
              f"{depth} bit declared, chroma {v['chroma']}"
              + (", alpha" if v["alpha"] else ""))
        rate = C.rate_str(C.rate(v["fps_avg"])) if v["fps_avg"] else "?"
        frames = v["frames"] if v["frames"] is not None else "?"
        dur = f"{v['duration_s']:.3f}s" if v["duration_s"] else "duration unknown"
        print(f"  rate      {rate}, {frames} frames, {dur}")
        tag = (f"{v['primaries']}/{v['transfer']}/{v['matrix']}, range {v['range']}"
               if v["colour_tagged"] else "UNTAGGED")
        print(f"  colour    {tag}")
    for a in info["audio"]:
        print(f"  audio     {a['codec']} {a['sample_rate']} Hz, {a['channels']} ch "
              f"({a['layout']}), {a['sample_fmt']}")
    for s in info["other_streams"]:
        print(f"  other     stream {s['index']}: {s['type']} {s['codec']}")
    if info["flags"]:
        print("\n  Flags:")
        for f in info["flags"]:
            print(f"   - {f}")


def _print_depth(r):
    print(f"{os.path.basename(r['file'])}: declares {r['declared_bit_depth']} bit, "
          f"carries {r['effective_bit_depth']} bit.")
    print(f"  {r['verdict']}")
    print(f"  distinct codes {r['distinct_codes']} (gcd {r['gcd_of_codes']}), "
          f"read as {r['plane']} in {r['measured_in_pix_fmt']} over "
          f"{r['frames_measured']} frames")
    print("  per frame: " + ", ".join(f"f{p['frame']}={p['distinct']}"
                                      for p in r["per_frame"]))
    for row in r["lattice"]:
        print(f"  lattice {row['step']}x (source {row['candidate_bit_depth']} bit): "
              f"{row['fraction_on_lattice'] * 100:.1f} per cent of samples, "
              f"chance level {row['chance_level'] * 100:.0f} per cent")
    print(f"  {r['caveat']}")


def _print_check(res):
    print(f"{os.path.basename(res['file'])} against profile {res['profile']}")
    if res["profile_verify"]:
        print(f"  {C.VERIFY} this profile is scaffolding. {res['profile_source']}")
    print()
    width = max(len(r["field"]) for r in res["rows"])
    for r in res["rows"]:
        mark = {"ok": "  ok", "MISMATCH": "MISS", "UNKNOWN": "  ??",
                "ASK": " ask", "DEFERRED": "defr"}.get(r["verdict"], "    ")
        print(f"  [{mark}] {r['field']:<{width}}  want {r['want']}   got {r['got']}")
        if r["note"]:
            print(f"          {r['note']}")
    print(f"\n  {res['strikes']} would be struck, {res['queries']} open question(s): "
          f"{res['verdict']}")
    for f in res["flags"]:
        print(f"   - {f}")


def main(argv=None):
    ap = C.parser_for(__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("probe", help="What the file actually is")
    pr.add_argument("file")
    C.add_json(pr)

    cl = sub.add_parser("claims", help="What the filename claims, held against the file")
    cl.add_argument("file")
    C.add_json(cl)

    dp = sub.add_parser("depth", help="Bit depth measured, not declared")
    dp.add_argument("file")
    dp.add_argument("--frames", type=int, default=3)
    dp.add_argument("--start", type=int, default=0)
    C.add_json(dp)

    ck = sub.add_parser("check", help="File against a delivery profile")
    ck.add_argument("file")
    ck.add_argument("--profile", required=True)
    C.add_json(ck)

    ga = sub.add_parser("gate", help="The facts that must be named before a render")
    ga.add_argument("--profile")
    C.add_json(ga)

    pf = sub.add_parser("profiles", help="Profiles on the shelf")
    C.add_json(pf)

    po = sub.add_parser("profile", help="One profile, or a blank template")
    po.add_argument("slug", nargs="?")
    po.add_argument("--template", action="store_true")
    C.add_json(po)

    args = ap.parse_args(argv)

    if args.cmd == "probe":
        info = probe(args.file)
        return C.emit(info, args.json, _print_probe)
    if args.cmd == "claims":
        measured = probe(args.file) if os.path.exists(args.file) else None
        rows = claims(args.file, measured)
        if args.json:
            return C.emit({"file": args.file, "measured": bool(measured),
                           "claims": rows}, True)
        if not rows:
            print("The filename claims nothing measurable. That is better than a "
                  "filename that claims something false.")
            return 0
        print(f"Claims in the name {os.path.basename(args.file)}:")
        for r in rows:
            print(f"  '{r['token']}' claims {r['field']}={r['claims']}; "
                  f"file says {r['measured']}: {r['verdict']}")
        bad = [r for r in rows if r["verdict"] == "CONTRADICTED"]
        if bad:
            print(f"\n  {len(bad)} contradiction(s). Go by the file. Names travel "
                  "further than files and get edited on the way.")
        elif measured is None:
            print("\n  File not on disk, so nothing was checked. A claim unchecked "
                  "is a claim.")
        return 0
    if args.cmd == "depth":
        res = measured_depth(args.file, args.frames, args.start)
        return C.emit(res, args.json, _print_depth)
    if args.cmd == "check":
        res = check(args.file, load_profile(args.profile))
        return C.emit(res, args.json, _print_check)
    if args.cmd == "gate":
        prof = load_profile(args.profile) if args.profile else None
        rows = []
        for fact, why in GATE_FACTS:
            known = None
            if prof:
                known = _profile_knows(prof, fact)
            rows.append({"fact": fact, "why": why,
                         "state": "named" if known else "UNKNOWN"})
        if args.json:
            return C.emit({"profile": args.profile, "facts": rows}, True)
        print("Before anything renders, these have to be named. A standard's "
              "constant may default; a project's fact may not.\n")
        for r in rows:
            mark = "named" if r["state"] == "named" else "  ASK"
            print(f"  [{mark}] {r['fact']}: {r['why']}")
        unknown = [r["fact"] for r in rows if r["state"] != "named"]
        if unknown:
            print("\n  Ask for these in ONE list, and stop until they come back:")
            for f in unknown:
                print(f"   - {f}")
        return 0
    if args.cmd == "profiles":
        names = list_profiles()
        if args.json:
            return C.emit(names, True)
        print("Profiles on the shelf (all scaffolding; a job supplies its own):")
        for n in names:
            p = load_profile(n)
            print(f"  {n:<24} {p.get('name', '')}")
        print("\n  New one: python spec.py profile --template > myjob.json")
        return 0
    if args.cmd == "profile":
        if args.template:
            print(json.dumps(_TEMPLATE, indent=2))
            return 0
        if not args.slug:
            raise ValueError("Give a profile slug, or --template.")
        return C.emit(load_profile(args.slug), True)
    return 0


def _profile_knows(prof, fact):
    """Does the profile actually name this delivery fact?"""
    pic, aud = prof.get("picture", {}), prof.get("audio", {})
    loud = aud.get("loudness", {})
    table = {
        "frame size": pic.get("width") and pic.get("height"),
        "frame rate": pic.get("fps"),
        "scan": pic.get("scan"),
        "codec and container": pic.get("codec"),
        "bit depth": pic.get("bit_depth"),
        "chroma": pic.get("chroma"),
        "colour": pic.get("primaries") and pic.get("transfer")
                  and pic.get("matrix") and pic.get("range"),
        "audio layout": aud.get("channels") and aud.get("sample_rate"),
        "loudness target": loud.get("target_i") is not None and loud.get("gate"),
        "safe area convention": prof.get("safe", {}).get("action"),
        "aspect and crop": pic.get("width") and pic.get("height"),
        "deliverable list": prof.get("items"),
    }
    return bool(table.get(fact))


if __name__ == "__main__":
    sys.exit(C.main_guard(main))
