#!/usr/bin/env python3
"""The verifier: prove what a file is, and prove what a revision changed.

This is the tool that was rewritten from scratch in job folder after job
folder, and it is the reason this skill exists. Its rules are not style:

  A hash proves the PIXELS and says nothing about how the file DECLARES them.
  A changed frame list is only a proof when it was PREDICTED before the render.
  A threshold of zero measures the codec, not the film: read every difference
  against the frame's own generation floor.
  A spliced master's timeline is broken at its joins, so never seek by
  arithmetic; read the file's own packet timestamps, and then PROVE the seek
  landed, because packet order is not display order and the decoder's own
  behaviour has changed under us.
  A new check must pass on an already approved file before it is believed.

Commands:
  sha        hash files into a ledger
  verify     re-hash a ledger and report drift          (survivors first)
  frames     per frame hashes, cached on path+size+mtime
  diff       which frames differ between two files
  predict    derive the predicted changed frames from the LAYER files
  expect     hold a found set against a predicted set
  timeline   packet timestamps, joins, and whether the timeline is uniform
  seek       the -ss for frame N, tried against the file until it lands
  length     did the picture survive the mux, or did the sound cut it
  packets    packet size sequence, and prove a cut without decoding
  tags       walk the colour metadata across the whole file
  floor      the generation floor: what a re-encode costs where nothing changed

Usage:
  python prove.py sha OUT/*.mov --ledger OUT/SHA256.json
  python prove.py verify OUT/SHA256.json
  python prove.py diff v8.mov v9.mov
  python prove.py predict build_v8/overlays build_v9/overlays
  python prove.py expect --predicted 233,234,235 --found found.json
  python prove.py timeline MASTER.mov
  python prove.py seek MASTER.mov --frame 795
  python prove.py length v9.mov --against v8.mov
  python prove.py packets MASTER.mov --compare SRC.mov --at 96
  python prove.py tags MASTER.mov
  python prove.py floor delivered.mov revised.mov --frame 795 --box 0,0,400,200
  (add --json for structured output)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from fractions import Fraction

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _common as C  # noqa: E402


# ---------------------------------------------------------------- ledger


def sha_files(paths):
    return [{"path": os.path.abspath(p), "sha256": C.sha256_file(p),
             "size": os.path.getsize(p)} for p in paths]


def write_ledger(rows, out):
    payload = {"tool": "postproduction/prove.py", "entries": rows}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return out


def verify_ledger(path):
    """Re-hash everything a ledger claims. Survivors are verified FIRST.

    When told to clear old masters and stay able to roll back, this runs before
    a single byte is deleted: if what you are keeping is not what you think it
    is, what you are deleting is not redundant, it is the last copy.
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rows = []
    for e in data.get("entries", []):
        p = e["path"]
        if not os.path.exists(p):
            rows.append(dict(e, state="MISSING", now=None))
            continue
        now = C.sha256_file(p)
        rows.append(dict(e, state="ok" if now == e["sha256"] else "CHANGED",
                         now=now))
    bad = [r for r in rows if r["state"] != "ok"]
    return {"ledger": os.path.abspath(path), "entries": rows,
            "failures": len(bad),
            "verdict": "every file matches its record" if not bad
                       else f"{len(bad)} file(s) do not match. Stop."}


# ---------------------------------------------------------------- frames


def framemd5(path, use_cache=True, stream="v:0"):
    """Per frame hashes. Cached, because the expensive check is the one skipped.

    Three framemd5 walks over 5 GB of ProRes is twelve minutes, and that cost
    is exactly what tempts a person to patch a check and skip the re-run.
    """
    C.need("ffmpeg")
    key = C.stat_key(path)
    import hashlib
    cache_id = hashlib.sha256(
        json.dumps([key, stream], sort_keys=True).encode()).hexdigest()[:32]
    cache_path = os.path.join(C.cache_dir(), f"framemd5_{cache_id}.json")
    if use_cache and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            return json.load(fh)
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
           "-map", f"0:{stream}", "-f", "framemd5", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("framemd5 failed: " + proc.stderr.strip()[:300])
    hashes = []
    for line in proc.stdout.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 6:
            hashes.append(parts[-1])
    payload = {"file": os.path.abspath(path), "stream": stream,
               "count": len(hashes), "hashes": hashes, "key": key,
               "decode_path": "ffmpeg:framemd5"}
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return payload


def diff_frames(a, b, use_cache=True):
    """Which frames differ, and how many are bit identical."""
    fa, fb = framemd5(a, use_cache), framemd5(b, use_cache)
    n = min(fa["count"], fb["count"])
    changed = [i for i in range(n) if fa["hashes"][i] != fb["hashes"][i]]
    return {
        "a": fa["file"], "b": fb["file"],
        "a_frames": fa["count"], "b_frames": fb["count"],
        "compared": n,
        "changed": changed, "changed_count": len(changed),
        "identical_count": n - len(changed),
        "length_mismatch": fa["count"] != fb["count"],
        "note": "A frame count that matches proves nothing on its own: a splice "
                "one frame early and one frame late both keep the count.",
    }


# ---------------------------------------------------------------- predict


_FRAME_NUM = re.compile(r"(\d{2,})(?=\.[A-Za-z0-9]+$)")


def predict_changes(old_dir, new_dir, pattern=None):
    """Derive the predicted changed frames from the LAYER files themselves.

    The strongest claim about a revision is 'these frames changed and no
    others', and it only holds when the prediction comes from the build rather
    than from someone's memory of what they touched. Hash every layer file in
    both builds; the predicted set is exactly the frames where a layer differs,
    or exists in one build and not the other.

    A hand typed list only works while the person writing it remembers
    everything they touched, and that is precisely what fails on version five.
    """
    def index(root):
        out = {}
        for dirpath, _dirs, files in os.walk(root):
            for f in sorted(files):
                if f.startswith("."):
                    continue
                if pattern and not re.search(pattern, f):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, f), root)
                out[rel] = os.path.join(dirpath, f)
        return out

    old, new = index(old_dir), index(new_dir)
    changed_files, frames = [], set()
    for rel in sorted(set(old) | set(new)):
        in_old, in_new = rel in old, rel in new
        if in_old and in_new:
            if os.path.getsize(old[rel]) == os.path.getsize(new[rel]) and \
               C.sha256_file(old[rel]) == C.sha256_file(new[rel]):
                continue
            why = "differs"
        else:
            why = "only in new" if in_new else "only in old"
        m = _FRAME_NUM.search(os.path.basename(rel))
        frame = int(m.group(1)) if m else None
        changed_files.append({"file": rel, "why": why, "frame": frame})
        if frame is not None:
            frames.add(frame)
    all_numbers = sorted(
        int(m.group(1))
        for rel in set(old) | set(new)
        for m in [_FRAME_NUM.search(os.path.basename(rel))] if m)
    return {"old_dir": os.path.abspath(old_dir), "new_dir": os.path.abspath(new_dir),
            "changed_files": changed_files,
            "predicted_frames": sorted(frames),
            "predicted_count": len(frames),
            "filename_numbering_base": all_numbers[0] if all_numbers else None,
            "unnumbered_changes": [c["file"] for c in changed_files
                                   if c["frame"] is None],
            "note": "Frame numbers are read from the trailing digits of each "
                    "file name. Any changed file with no number is listed "
                    "separately: it changes something, and this cannot say which "
                    "frames. These are FILE numbers: if the layer set starts at "
                    "1 and the film's frames start at 0, hand expect an "
                    "--offset of -1, having confirmed that is the mapping."}


def expect(predicted, found, offset=0):
    """Hold a found changed-frame set against the predicted one.

    Layer files are usually numbered from 1 and film frames from 0, so a
    perfectly correct build reports a total mismatch until somebody notices the
    base. This refuses to guess: when the two sets agree under a constant
    shift, it says so and FAILS, so the mapping is confirmed on purpose with
    --offset rather than absorbed silently. An off by one that a tool corrects
    for you is an off by one you will meet again in the render.
    """
    p = {x + offset for x in predicted}
    f = set(found)
    missing, extra = sorted(p - f), sorted(f - p)
    shift = None
    if missing and extra and len(p) == len(f):
        candidates = {b - a for a, b in zip(sorted(p), sorted(f))}
        if len(candidates) == 1:
            shift = candidates.pop()
    verdict = (f"{len(p)} predicted, {len(f)} found, exact match"
               if not missing and not extra else
               f"{len(missing)} predicted frame(s) did not change and "
               f"{len(extra)} frame(s) changed that were not predicted. "
               "Both accounts have to agree before this passes.")
    if shift:
        verdict += (f" The two sets ARE identical after a constant shift of "
                    f"{shift:+d}, which is the signature of one side numbering "
                    f"from a different base. Confirm the mapping and re-run "
                    f"with --offset {offset + shift}; do not assume it.")
    return {"predicted_count": len(p), "found_count": len(f), "offset": offset,
            "predicted_not_found": missing, "found_not_predicted": extra,
            "constant_shift_detected": shift,
            "pass": not missing and not extra,
            "verdict": verdict}


# ---------------------------------------------------------------- packets


def packets(path, stream="v:0"):
    """Packet timestamps, durations, sizes and keyframe flags."""
    data = C.ffprobe_json(path, ["-select_streams", stream, "-show_packets",
                                 "-show_entries",
                                 "packet=pts,dts,duration,size,flags"])
    pk = data.get("packets", [])
    stream_info = C.ffprobe_json(path, ["-select_streams", stream,
                                        "-show_entries", "stream=time_base"])
    tb = (stream_info.get("streams") or [{}])[0].get("time_base", "1/1")
    num, den = tb.split("/")
    return {"file": os.path.abspath(path), "time_base": Fraction(int(num), int(den)),
            "packets": pk}


def timeline(path, stream="v:0"):
    """Is this file's timeline uniform, and where are its joins?

    A master built by splicing parts together does not have a uniform timeline.
    Measured on a real delivery: the last frame of each stream copied piece came
    out one tick long and every packet after each join sat 511 ticks early,
    while all the pictures were present. Two splices died on that before it was
    found, and only a frame hash battery caught the second one.
    """
    info = packets(path, stream)
    pk, tb = info["packets"], info["time_base"]
    if not pk:
        raise RuntimeError("No packets read. Wrong stream, or an empty file.")
    durs = [int(p["duration"]) for p in pk if p.get("duration") not in (None, "N/A")]
    modal = max(set(durs), key=durs.count) if durs else None
    odd = [{"index": i, "duration_ticks": int(p["duration"]),
            "pts": int(p["pts"]) if p.get("pts") not in (None, "N/A") else None}
           for i, p in enumerate(pk)
           if p.get("duration") not in (None, "N/A")
           and int(p["duration"]) != modal]
    ptss = [int(p["pts"]) for p in pk if p.get("pts") not in (None, "N/A")]
    gaps = []
    for i in range(len(ptss) - 1):
        step = ptss[i + 1] - ptss[i]
        if modal and step != modal:
            gaps.append({"between": [i, i + 1], "step_ticks": step,
                         "expected": modal})
    span = (ptss[-1] - ptss[0] + (modal or 0)) * tb if ptss else 0
    keyframes = sum(1 for p in pk if "K" in (p.get("flags") or ""))
    return {
        "file": info["file"], "packets": len(pk),
        "time_base": str(tb), "modal_duration_ticks": modal,
        "modal_rate": str(1 / (modal * tb)) if modal else None,
        "odd_duration_packets": odd[:50], "odd_duration_count": len(odd),
        "non_uniform_steps": gaps[:50], "non_uniform_count": len(gaps),
        "all_keyframes": keyframes == len(pk),
        "keyframes": keyframes,
        "picture_span_s": float(span),
        "uniform": not odd and not gaps,
        "verdict": ("uniform: plain frame arithmetic is safe on this file"
                    if not odd and not gaps else
                    "NOT uniform. This file has joins. Do not seek by "
                    "arithmetic and do not trust select=eq(n,N): ffmpeg "
                    "reconfigures its filter graph at a join and the select "
                    "filter's own counter restarts there. Use 'prove.py seek'."),
    }


def _probe_raster(path, stream="v:0"):
    """Width and height, for building a small identity raster."""
    d = C.ffprobe_json(path, ["-select_streams", stream, "-show_entries",
                              "stream=width,height", "-of", "json"])
    st = (d.get("streams") or [{}])[0]
    return int(st.get("width") or 0), int(st.get("height") or 0)


def _walk_digests(path, upto, probe_w=96, stream="v:0"):
    """Digests of frames 0..upto, walked from the HEAD with no seek at all.

    This is the only thing on the machine entitled to say which picture frame N
    is, because it never asks the decoder to jump. The raster is tiny and the
    digest is of the scaled bytes: this answers "is this the same picture", not
    "what level is it", so it is an identity instrument and never a colour one.
    """
    import hashlib
    W, H = _probe_raster(path, stream)
    if not W or not H:
        raise RuntimeError("Could not read the picture size.")
    w = min(probe_w, W)
    h = max(2, int(round(H * w / W)) // 2 * 2)
    per = w * h * 3
    C.need("ffmpeg")
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
           "-vf", f"scale={w}:{h}:flags=bilinear",
           "-frames:v", str(upto + 1),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    # stderr is DEVNULL, not PIPE, and that is load bearing. This function
    # never reads stderr, and it closes stdout and then blocks in wait(). A pipe
    # nobody drains stops ffmpeg the moment the 64 KB kernel buffer fills, so a
    # file that emits more than that in decode warnings deadlocks here with no
    # timeout to break it. There is nothing to lose: the messages were discarded.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            bufsize=per * 4)
    out = []
    try:
        while len(out) <= upto:
            buf = proc.stdout.read(per)
            if not buf or len(buf) < per:
                break
            out.append(hashlib.sha256(buf).hexdigest())
    finally:
        proc.stdout.close()
        proc.wait()
    return out, (w, h)


def _digest_at_seek(path, seconds, probe_wh, stream="v:0"):
    """The digest of whatever ONE frame a seek to `seconds` actually returns."""
    import hashlib
    w, h = probe_wh
    per = w * h * 3
    C.need("ffmpeg")
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{seconds:.9f}",
           "-i", str(path), "-vf", f"scale={w}:{h}:flags=bilinear",
           "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    buf = subprocess.run(cmd, capture_output=True).stdout[:per]
    if len(buf) < per:
        return None
    return hashlib.sha256(buf).hexdigest()


def length(path, expect_frames=None, against=None, stream="v:0"):
    """Did the picture survive the mux, or did the sound decide its length?

    The fault this exists for, measured on a real delivery 2026-08-28. A supers
    renderer muxed finished picture against the master's PCM with `-shortest`.
    The audio was 51.578688 s; 1238 frames at 24 fps is 51.583333 s. Four
    milliseconds. `-shortest` cut the PICTURE to the sound and the master went
    out at 1237 frames, one frame shorter than the film the client already had.

    Nothing downstream saw it. Every frame check in that build compared index to
    index, and a film that is one frame short passes all of them on the 1237
    frames it does have. A hash of it is a confident record of a truncated film.

    So the count is asserted BEFORE anything else runs, either against a number
    you name or against the film this one is a revision of. A four millisecond
    picture and sound mismatch is normal and harmless; letting it decide the
    picture length is not. Mux with no length flag and pad the audio if a
    container really needs them equal. Never trim the picture.
    """
    def read(p):
        d = C.ffprobe_json(p, ["-show_entries",
                               "stream=codec_type,nb_frames,r_frame_rate,duration,"
                               "sample_rate,channels:format=duration",
                               "-of", "json"])
        v = a = None
        for st in d.get("streams") or []:
            if st.get("codec_type") == "video" and v is None:
                v = st
            if st.get("codec_type") == "audio" and a is None:
                a = st
        n = (v or {}).get("nb_frames")
        if n and str(n).isdigit():
            n = int(n)
        elif v:
            # Not every container carries nb_frames. Counting this file's own
            # video packets is the fallback, and it is the same number the
            # timeline command works from.
            n = len(packets(p, stream)["packets"])
        else:
            n = None
        r = C.rate((v or {}).get("r_frame_rate", "0/1")) if v else None
        return {
            "file": os.path.abspath(p),
            "frames": n,
            "rate": str(r) if r else None,
            "picture_s": float(n / r) if n and r else None,
            "audio_s": float((a or {}).get("duration") or 0) or None,
            "container_s": float((d.get("format") or {}).get("duration") or 0) or None,
        }

    me = read(path)
    out = dict(me)
    strikes, notes = [], []
    if me["frames"] is None:
        raise RuntimeError("No video stream, so there is no picture length to prove.")

    if me["audio_s"] and me["picture_s"]:
        d = me["picture_s"] - me["audio_s"]
        out["picture_minus_audio_s"] = round(d, 6)
        out["picture_minus_audio_frames"] = round(d * float(C.rate(me["rate"])), 4)
        # The signature: the picture ends where the sound does, to well inside a
        # frame, AND the sound was the shorter of the two. That is what -shortest
        # leaves behind.
        one_frame = 1.0 / float(C.rate(me["rate"]))
        if d < -one_frame * 0.02:
            notes.append(
                f"The picture ENDS BEFORE THE SOUND, by {-d * 1000:.1f} ms "
                f"({-d / one_frame:.2f} of a frame). That is the wrong way round "
                "for a deliverable and it is what -shortest leaves behind: the "
                "sound decided the picture length. Measured on the real fault, "
                "the picture came back 0.90 of a frame short of its own sound "
                "where a correct mux of the same two streams sat 0.10 of a frame "
                "long. It is not proof on its own, so hold the frame count "
                "against the film this replaces.")

    if against:
        ref = read(against)
        out["against"] = ref
        if ref["frames"] is not None and ref["frames"] != me["frames"]:
            strikes.append(
                f"FRAME COUNT CHANGED: {me['frames']} against {ref['frames']} in "
                f"{os.path.basename(against)}, a difference of "
                f"{me['frames'] - ref['frames']:+d} frame(s). If this revision "
                "was not meant to change the length, the film is truncated and "
                "every index to index check will still pass on it.")
        if ref["rate"] and me["rate"] and ref["rate"] != me["rate"]:
            strikes.append(f"RATE CHANGED: {me['rate']} against {ref['rate']}.")

    if expect_frames is not None and me["frames"] != expect_frames:
        strikes.append(
            f"FRAME COUNT IS NOT WHAT WAS ASKED FOR: {me['frames']} against "
            f"{expect_frames}, a difference of {me['frames'] - expect_frames:+d}.")

    out["strikes"] = strikes
    out["notes"] = notes
    out["verdict"] = (
        "; ".join(strikes) if strikes else
        (f"{me['frames']} frames at {me['rate']} fps. "
         + ("Length agrees with everything it was held against."
            if (against or expect_frames is not None) else
            "NOTHING WAS COMPARED. A frame count on its own proves nothing: "
            "pass --frames or --against so this can fail.")))
    out["ok"] = not strikes
    return out


def seek_for_frame(path, frame, stream="v:0", verify=True, probe_w=96):
    """The -ss that really lands on frame N, CALIBRATED against this file.

    Two faults, both measured on this machine on 2026-08-31, ffmpeg 9.0.1:

    ONE. Packet order is not display order. `ffprobe -show_packets` hands them
    over in decode order, so on any long GOP file with B frames the Nth packet
    is not the Nth picture. Indexing that list by frame number was reading a
    different frame's timestamp entirely. Proved on a 48 frame h264 file whose
    packet order and display order disagreed, where the old code asked for
    frame 12 and computed a seek that landed on frame 14. Sorting the
    timestamps fixes it, and `pts_in_packet_order` below reports when it
    mattered.

    TWO. The midpoint rule is a measurement of a decoder, not a rule. Seeking
    to a time strictly inside frame N now returns frame N+1, on ProRes and on
    h264 alike, at every frame tested, and on the LAST frame it returns nothing
    at all because the midpoint lies past the end of the picture. Every still
    grabbed with the old recipe is one frame late, and the frame count is
    correct both times, so nothing downstream noticed.

    So the seek is no longer derived and asserted. Candidates are generated,
    each one is TRIED, and the one whose picture matches frame N walked from the
    head of the file is the answer. `verified` says whether that happened.
    Pass `verify=False` only when the walk is too expensive to afford, and read
    the result as UNPROVEN when you do.
    """
    info = packets(path, stream)
    pk, tb = info["packets"], info["time_base"]
    raw = [int(p["pts"]) for p in pk if p.get("pts") not in (None, "N/A")]
    if not raw:
        raise RuntimeError("No packet timestamps. Wrong stream, or an empty file.")
    ptss = sorted(raw)
    reordered = raw != ptss
    if frame < 0 or frame >= len(ptss):
        raise ValueError(f"Frame {frame} is outside this file's {len(ptss)} packets.")
    durs = [int(p["duration"]) for p in pk if p.get("duration") not in (None, "N/A")]
    modal = max(set(durs), key=durs.count) if durs else 1
    this = ptss[frame]
    nxt = ptss[frame + 1] if frame + 1 < len(ptss) else this + modal
    span = nxt - this

    # Ordered by what was measured to work, not by what reads well. The frame's
    # own start is first because that is what lands on this decoder today; the
    # old midpoint is kept as a candidate so a decoder that goes back to the old
    # behaviour is still served, and so the result can say which one it was.
    cands = [
        ("frame start", float(this * tb)),
        ("a quarter frame before the start", float(max(0, this - span / 4) * tb)),
        ("midpoint of the frame (the pre 2026-08-31 rule)", float((this + nxt) / 2 * tb)),
        ("midpoint of the frame before", float((ptss[max(0, frame - 1)] + this) / 2 * tb)),
    ]

    out = {
        "file": info["file"], "frame": frame,
        "pts_ticks": this, "next_pts_ticks": nxt,
        "time_base": str(tb),
        "frame_start_s": float(this * tb),
        "frame_end_s": float(nxt * tb),
        "pts_in_packet_order": not reordered,
        "frames_in_file": len(ptss),
    }
    if reordered:
        out["packet_order_warning"] = (
            "Packet order is NOT display order on this file, so any tool that "
            "indexes -show_packets output by frame number is reading the wrong "
            "picture's timestamp. These are sorted.")

    if not verify:
        name, sec = cands[0]
        out.update({
            "seek_seconds": sec, "landed_on": name, "verified": False,
            "verdict": "UNPROVEN: verification was switched off. This seek was "
                       "derived, not measured, and a derived seek has been "
                       "wrong on this machine.",
        })
    else:
        truth, wh = _walk_digests(path, min(frame + 1, len(ptss) - 1), probe_w, stream)
        if len(truth) <= frame:
            raise RuntimeError(
                f"Walked only {len(truth)} frames from the head, so frame "
                f"{frame} could not be identified. The file is shorter than "
                "its packet count claims.")
        want = truth[frame]
        # The picture is the only evidence there is, so the LABEL is proved only
        # when this picture appears ONCE in everything walked. Neighbours are the
        # obvious case and they were all this used to test, but a repeat anywhere
        # else in the walk satisfies `got == want` exactly as well: a black head,
        # a flash, a plate that comes back. Measured on a 24 frame file whose
        # frame 12 repeats frame 0, the neighbours-only test read
        # "distinguishable", the verdict said "this seek returns frame 12", and
        # every correct candidate reported having matched frame 0.
        same_picture = [i for i, d in enumerate(truth) if d == want]
        ambiguous = len(same_picture) > 1
        tried = []
        hit = None
        for name, sec in cands:
            got = _digest_at_seek(path, sec, wh, stream)
            # Every index this picture matches, not the first. The first one is a
            # confident wrong answer whenever the picture repeats.
            landed = [i for i, d in enumerate(truth) if d == got] if got else []
            tried.append({"candidate": name, "seek_seconds": sec,
                          "returned_a_frame": got is not None,
                          "matches_frames": landed, "correct": got == want})
            if got == want and hit is None:
                hit = (name, sec)
        out["candidates_tried"] = tried
        out["picture_unique_in_walk"] = not ambiguous
        out["frames_sharing_this_picture"] = same_picture if ambiguous else []
        if hit:
            out.update({
                "seek_seconds": hit[1], "landed_on": hit[0], "verified": True,
                "verdict": (
                    f"Verified against a head walk: this seek returns frame "
                    f"{frame}." if not ambiguous else
                    "Verified against a head walk as a PICTURE, UNPROVEN as a "
                    "label: frames "
                    + ", ".join(str(i) for i in same_picture)
                    + f" are all the same picture in the {len(truth)} frames "
                    "walked, so content cannot say which of them came back. The "
                    "seek is right about what you get and unproven about its "
                    "number. Cut on packet indices if the number matters. Only "
                    "frames 0 to the target were walked, so a repeat later in "
                    "the file is outside what this can see either way."),
            })
        else:
            out.update({
                "seek_seconds": None, "landed_on": None, "verified": False,
                "verdict": (f"UNPROVEN: none of the {len(cands)} candidate seeks "
                            f"returned frame {frame}. Do not seek on this file. "
                            "Walk it from the head, or cut on packet indices "
                            "with -f segment -segment_frames."),
            })

    sec = out.get("seek_seconds")
    out["ffmpeg"] = (f"ffmpeg -ss {sec:.9f} -i {info['file']} -frames:v 1 "
                     "-c copy out.mov" if sec is not None else None)
    out["note"] = ("Every ProRes and DNx frame is a keyframe, so a copy starting "
                   "at this instant starts on frame N whatever the timeline "
                   "does. On a long GOP file -ss before -i is not frame exact, "
                   "which is why this is verified rather than derived.")
    return out


def prove_cut(piece, source, at, stream="v:0"):
    """Prove a cut without decoding: packet SIZES must match the source slice.

    A frame count does not prove a cut. One real splice came out with exactly
    the right number of frames and every frame one early.
    """
    a = [int(p["size"]) for p in packets(piece, stream)["packets"]]
    b = [int(p["size"]) for p in packets(source, stream)["packets"]]
    if at + len(a) > len(b):
        return {"pass": False,
                "verdict": f"The piece is {len(a)} packets and the source only has "
                           f"{len(b) - at} from index {at}."}
    window = b[at:at + len(a)]
    bad = [i for i, (x, y) in enumerate(zip(a, window)) if x != y]
    best, best_score = at, len(bad)
    if bad:
        for shift in range(max(0, at - 3), min(len(b) - len(a), at + 3) + 1):
            score = sum(1 for x, y in zip(a, b[shift:shift + len(a)]) if x != y)
            if score < best_score:
                best, best_score = shift, score
    return {"piece": os.path.abspath(piece), "source": os.path.abspath(source),
            "claimed_start": at, "piece_packets": len(a),
            "mismatches": len(bad), "first_mismatch": bad[0] if bad else None,
            "pass": not bad,
            "best_alignment": best, "best_alignment_mismatches": best_score,
            "verdict": ("packet sizes match the source slice exactly: the cut is "
                        "where it claims to be"
                        if not bad else
                        f"{len(bad)} packet size mismatches. The best alignment "
                        f"found is index {best} with {best_score} mismatches, so "
                        f"the cut is off by {best - at:+d} frame(s).")}


# ---------------------------------------------------------------- tags


def tag_walk(path, stream="v:0", every=1):
    """Walk the colour metadata across the file and report every change.

    ProRes written from a PNG sequence with no -colorspace comes out untagged.
    Spliced into a film tagged bt709 with -c copy, the delivered master changes
    its colour metadata partway through, at the join. Every frame hash matches,
    every picture check passes, and a colourist's tool is entitled to read those
    frames differently from the rest of the film.
    """
    data = C.ffprobe_json(path, ["-select_streams", stream, "-show_frames",
                                 "-show_entries",
                                 "frame=color_space,color_primaries,"
                                 "color_transfer,color_range"])
    frames = data.get("frames", [])
    runs = []
    for i, f in enumerate(frames):
        if every > 1 and i % every and i != len(frames) - 1:
            continue
        sig = (f.get("color_primaries") or "unset", f.get("color_transfer") or "unset",
               f.get("color_space") or "unset", f.get("color_range") or "unset")
        if runs and runs[-1]["signature"] == list(sig):
            runs[-1]["last_frame"] = i
            runs[-1]["frames"] += 1
        else:
            runs.append({"first_frame": i, "last_frame": i, "frames": 1,
                         "signature": list(sig)})
    unset = [r for r in runs if "unset" in r["signature"]]
    return {"file": os.path.abspath(path), "frames_read": len(frames),
            "runs": runs, "changes": len(runs) - 1,
            "uniform": len(runs) <= 1,
            "untagged_runs": len(unset),
            "verdict": (("one colour declaration for the whole file"
                         + (f", but {unset[0]['signature'].count('unset')} of the "
                            "four fields are UNSET: the file does not fully "
                            "declare its colour, and a player will guess"
                            if unset else ""))
                        if len(runs) <= 1 else
                        f"{len(runs)} different colour declarations. The file "
                        f"changes what it says about itself at frame(s) "
                        + ", ".join(str(r['first_frame']) for r in runs[1:])
                        + ". Tag every spliced segment with the tag the film "
                          "carries, matched to the file being joined, and "
                          "compare before concatenating as well as after."),
            "note": "Uniformity is the goal, but a NEW check demanding one tag "
                    "must be run against the last file the client accepted "
                    "first: an approved build may have carried two kinds since "
                    "its own splices, and then the check is wrong, not the film."}


# ---------------------------------------------------------------- floor


def _compare16(a, b, chunk=1 << 16):
    """Worst, mean and count of differing 16 bit samples between two buffers.

    Chunked, and identical chunks are skipped by a C level byte compare. On the
    normal case, where almost all of a frame is untouched, that turns a whole
    UHD frame from tens of seconds of Python into a few milliseconds, and the
    worst case is no slower than the plain loop.
    """
    import array
    worst = total = nonzero = 0
    n = len(a) // 2
    for off in range(0, len(a), chunk):
        ca, cb = a[off:off + chunk], b[off:off + chunk]
        if ca == cb:
            continue
        xa, xb = array.array("H"), array.array("H")
        xa.frombytes(ca[:len(ca) // 2 * 2])
        xb.frombytes(cb[:len(cb) // 2 * 2])
        if sys.byteorder == "big":
            xa.byteswap()
            xb.byteswap()
        for u, v in zip(xa, xb):
            d = u - v if u > v else v - u
            if d:
                nonzero += 1
                total += d
                if d > worst:
                    worst = d
    return worst, total, nonzero, n


def generation_floor(a, b, frame, box=None, stream="v:0"):
    """What a re-encode costs where nothing changed: the number to compare against.

    A verifier that demands the untouched regions be identical fails a correct
    master. When a tool re-encodes a whole SPAN to change part of a picture,
    every pixel in that span carries one codec generation. Read the difference
    as a MULTIPLE of this floor, never against zero.

    Both frames are pulled through the same decode path, seeking by this file's
    own packet timestamps, because two decode paths disagree by one to two code
    levels on YUV to RGB conversion alone.
    """
    C.need("ffmpeg")
    out = {}
    planes = []
    for path in (a, b):
        s = seek_for_frame(path, frame, stream)
        if not s.get("verified") or s.get("seek_seconds") is None:
            raise RuntimeError(
                f"Cannot reach frame {frame} of {path} by seeking: "
                + s["verdict"] + " A floor read off the wrong frame is a "
                "confident number about the wrong picture, so this refuses "
                "rather than returning one.")
        crop = []
        if box:
            x, y, w, h = box
            crop = ["-vf", f"crop={w}:{h}:{x}:{y}"]
        cmd = (["ffmpeg", "-v", "error", "-nostdin", "-ss",
                f"{s['seek_seconds']:.9f}", "-i", str(path), "-frames:v", "1"]
               + crop + ["-pix_fmt", "rgb48le", "-f", "rawvideo", "-"])
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0 or not proc.stdout:
            raise RuntimeError(f"Could not read frame {frame} from {path}: "
                               + proc.stderr.decode("utf-8", "replace")[:300])
        planes.append(proc.stdout)
        out.setdefault("seeks", []).append(s["seek_seconds"])
    if len(planes[0]) != len(planes[1]):
        raise ValueError("The two frames are different sizes. Nothing here can "
                         "compare them; fix the geometry first.")
    worst, total, nonzero, n = _compare16(planes[0], planes[1])
    return {"a": os.path.abspath(a), "b": os.path.abspath(b), "frame": frame,
            "box": list(box) if box else "whole frame",
            "samples": n, "changed_samples": nonzero,
            "max_abs_diff": worst, "mean_abs_diff": total / n if n else 0.0,
            "scale": "16 bit, 0 to 65535",
            "seek_seconds": out.get("seeks"),
            "decode_path": "ffmpeg:rgb48le via packet-timestamp seek",
            "verdict": ("bit identical in this region"
                        if worst == 0 else
                        f"worst {worst} of 65535, mean {total / n:.2f}. If this "
                        "region was NOT meant to change, this is the generation "
                        "floor: read every real signal as a multiple of it. If it "
                        "WAS meant to change, compare it with a floor measured "
                        "somewhere the change cannot reach."),
            "note": "A codec floor has a shape. ProRes quantises in slices 16 "
                    "rows tall, so new ink anywhere in a strip re-quantises the "
                    "rest of that strip and nothing outside it. Mask the slice "
                    "ROWS, not the bounding rectangle, and the floor often falls "
                    "to exactly zero."}


# ---------------------------------------------------------------- cli


def _ints(s):
    return [int(x) for x in re.findall(r"-?\d+", s or "")]


def main(argv=None):
    ap = C.parser_for(__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sh = sub.add_parser("sha", help="Hash files into a ledger")
    sh.add_argument("files", nargs="+")
    sh.add_argument("--ledger")
    C.add_json(sh)

    ve = sub.add_parser("verify", help="Re-hash a ledger")
    ve.add_argument("ledger")
    C.add_json(ve)

    fr = sub.add_parser("frames", help="Per frame hashes")
    fr.add_argument("file")
    fr.add_argument("--no-cache", action="store_true")
    C.add_json(fr)

    di = sub.add_parser("diff", help="Which frames differ")
    di.add_argument("a")
    di.add_argument("b")
    di.add_argument("--no-cache", action="store_true")
    C.add_json(di)

    pd = sub.add_parser("predict", help="Predicted changed frames from layer files")
    pd.add_argument("old_dir")
    pd.add_argument("new_dir")
    pd.add_argument("--pattern", help="Only consider files matching this regex")
    C.add_json(pd)

    ex = sub.add_parser("expect", help="Found against predicted")
    ex.add_argument("--predicted", required=True,
                    help="Comma list, or a predict --json file")
    ex.add_argument("--found", required=True,
                    help="Comma list, or a diff --json file")
    ex.add_argument("--offset", type=int, default=0,
                    help="Add this to every predicted number before comparing, "
                         "for layer files numbered from a different base")
    C.add_json(ex)

    tl = sub.add_parser("timeline", help="Joins and timeline uniformity")
    tl.add_argument("file")
    C.add_json(tl)

    ln = sub.add_parser("length", help="Frame count, and whether the sound cut it")
    ln.add_argument("file")
    ln.add_argument("--frames", type=int, help="The count this file must have")
    ln.add_argument("--against", help="The film this one is a revision of")
    C.add_json(ln)

    sk = sub.add_parser("seek", help="The correct -ss for a frame, verified")
    sk.add_argument("file")
    sk.add_argument("--frame", type=int, required=True)
    sk.add_argument("--no-verify", action="store_true",
                    help="Skip the head walk that proves the seek landed. The "
                         "answer then comes back UNPROVEN, because a derived "
                         "seek has been wrong on this machine.")
    C.add_json(sk)

    pk = sub.add_parser("packets", help="Packet sizes, and prove a cut")
    pk.add_argument("file")
    pk.add_argument("--compare", help="The source this piece was cut from")
    pk.add_argument("--at", type=int, help="Claimed start frame in the source")
    pk.add_argument("--limit", type=int, default=20)
    C.add_json(pk)

    tg = sub.add_parser("tags", help="Colour metadata across the whole file")
    tg.add_argument("file")
    tg.add_argument("--every", type=int, default=1)
    C.add_json(tg)

    fl = sub.add_parser("floor", help="The generation floor in a region")
    fl.add_argument("a")
    fl.add_argument("b")
    fl.add_argument("--frame", type=int, required=True)
    fl.add_argument("--box", help="x,y,w,h")
    C.add_json(fl)

    args = ap.parse_args(argv)

    if args.cmd == "sha":
        rows = sha_files(args.files)
        if args.ledger:
            write_ledger(rows, args.ledger)
        return C.emit({"entries": rows, "ledger": args.ledger}, args.json,
                      lambda r: [print(f"{e['sha256']}  {e['path']}")
                                 for e in r["entries"]])
    if args.cmd == "verify":
        res = verify_ledger(args.ledger)
        return C.emit(res, args.json, lambda r: (
            [print(f"  [{e['state']:<7}] {e['path']}") for e in r["entries"]],
            print(f"\n  {r['verdict']}")))
    if args.cmd == "frames":
        res = framemd5(args.file, not args.no_cache)
        return C.emit(res, args.json,
                      lambda r: print(f"{r['count']} frames hashed from "
                                      f"{os.path.basename(r['file'])}"))
    if args.cmd == "diff":
        res = diff_frames(args.a, args.b, not args.no_cache)
        return C.emit(res, args.json, lambda r: (
            print(f"{r['identical_count']} of {r['compared']} frames bit identical, "
                  f"{r['changed_count']} changed."),
            print(f"  changed: {_summarise(r['changed'])}") if r["changed"] else None,
            print("  LENGTH MISMATCH: the two files have different frame counts."
                  if r["length_mismatch"] else f"  {r['note']}")))
    if args.cmd == "predict":
        res = predict_changes(args.old_dir, args.new_dir, args.pattern)
        return C.emit(res, args.json, lambda r: (
            print(f"{len(r['changed_files'])} layer file(s) differ, predicting "
                  f"{r['predicted_count']} changed frame(s)."),
            print(f"  frames: {_summarise(r['predicted_frames'])} "
                  f"(file numbering starts at {r['filename_numbering_base']})"),
            [print(f"  unnumbered change: {f}") for f in r["unnumbered_changes"]]))
    if args.cmd == "expect":
        res = expect(_load_ints(args.predicted, "predicted_frames"),
                     _load_ints(args.found, "changed"), args.offset)
        return C.emit(res, args.json, lambda r: print(f"  {r['verdict']}"))
    if args.cmd == "timeline":
        res = timeline(args.file)
        return C.emit(res, args.json, lambda r: (
            print(f"{r['packets']} packets, time base {r['time_base']}, "
                  f"modal duration {r['modal_duration_ticks']} ticks "
                  f"({r['modal_rate']} fps)"),
            print(f"  {r['odd_duration_count']} packet(s) with an odd duration, "
                  f"{r['non_uniform_count']} non uniform step(s)"),
            print(f"  all frames are keyframes: {r['all_keyframes']}"),
            print(f"  picture span {r['picture_span_s']:.3f}s"),
            print(f"\n  {r['verdict']}")))
    if args.cmd == "length":
        res = length(args.file, args.frames, args.against)
        return C.emit(res, args.json, lambda r: (
            print(f"  {r['frames']} frames at {r['rate']} fps "
                  f"= {r['picture_s']:.6f}s of picture"),
            print(f"  sound {r['audio_s']:.6f}s" if r["audio_s"] else
                  "  no sound in this file"),
            print(f"  picture minus sound {r['picture_minus_audio_s']:+.6f}s "
                  f"({r['picture_minus_audio_frames']:+.4f} frames)"
                  if r.get("picture_minus_audio_s") is not None else ""),
            [print(f"  {n}") for n in r["notes"]],
            print(f"\n  {'OK  ' if r['ok'] else 'STRIKE  '}{r['verdict']}")))
    if args.cmd == "seek":
        res = seek_for_frame(args.file, args.frame, verify=not args.no_verify)

        def _show(r):
            print(f"Frame {r['frame']} of {r['frames_in_file']} runs "
                  f"{r['frame_start_s']:.6f}s to {r['frame_end_s']:.6f}s "
                  f"(pts {r['pts_ticks']}).")
            if not r["pts_in_packet_order"]:
                print(f"  {r['packet_order_warning']}")
            for t in r.get("candidates_tried", []):
                m = t["matches_frames"]
                landed = ("nothing" if not m else f"frame {m[0]}" if len(m) == 1
                          else "frames " + ", ".join(map(str, m))
                          + " (one picture, several frames)")
                print(f"  [{'x' if t['correct'] else ' '}] {t['candidate']}"
                      f"  ->  {landed}")
            if r["seek_seconds"] is not None:
                print(f"  seek to {r['seek_seconds']:.9f}   ({r['landed_on']})")
                print(f"  {r['ffmpeg']}")
            if r.get("picture_unique_in_walk") is False:
                print("  frames "
                      + ", ".join(map(str, r["frames_sharing_this_picture"]))
                      + " are the same picture, so the number is unproven")
            print(f"  {r['verdict']}")
            print(f"  {r['note']}")
        return C.emit(res, args.json, _show)
    if args.cmd == "packets":
        if args.compare is not None:
            if args.at is None:
                raise ValueError("--compare needs --at, the claimed start frame.")
            res = prove_cut(args.file, args.compare, args.at)
            return C.emit(res, args.json, lambda r: print(f"  {r['verdict']}"))
        info = packets(args.file)
        sizes = [int(p["size"]) for p in info["packets"]]
        res = {"file": info["file"], "count": len(sizes),
               "sizes": sizes[:args.limit]}
        return C.emit(res, args.json,
                      lambda r: print(f"{r['count']} packets. First "
                                      f"{len(r['sizes'])} sizes: "
                                      + ", ".join(map(str, r["sizes"]))))
    if args.cmd == "tags":
        res = tag_walk(args.file, every=args.every)
        return C.emit(res, args.json, lambda r: (
            [print(f"  frames {run['first_frame']}-{run['last_frame']}: "
                   f"primaries {run['signature'][0]}, transfer {run['signature'][1]}, "
                   f"matrix {run['signature'][2]}, range {run['signature'][3]}")
             for run in r["runs"]],
            print(f"\n  {r['verdict']}"),
            print(f"  {r['note']}")))
    if args.cmd == "floor":
        box = _ints(args.box) if args.box else None
        if box and len(box) != 4:
            raise ValueError("--box wants x,y,w,h")
        res = generation_floor(args.a, args.b, args.frame, box)
        return C.emit(res, args.json, lambda r: (
            print(f"Frame {r['frame']}, {r['box']}: "
                  f"{r['changed_samples']} of {r['samples']} samples differ"),
            print(f"  max {r['max_abs_diff']} of 65535, "
                  f"mean {r['mean_abs_diff']:.3f}"),
            print(f"\n  {r['verdict']}"),
            print(f"  {r['note']}")))
    return 0


def _summarise(nums, limit=12):
    """Print a frame list as ranges, because 233 numbers is not readable."""
    if not nums:
        return "none"
    runs, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        runs.append((start, prev))
        start = prev = n
    runs.append((start, prev))
    parts = [f"{a}" if a == b else f"{a}-{b}" for a, b in runs]
    if len(parts) > limit:
        return ", ".join(parts[:limit]) + f", ... ({len(runs)} runs, {len(nums)} frames)"
    return ", ".join(parts) + f"  ({len(nums)} frames)"


def _load_ints(spec, key):
    if os.path.exists(spec):
        with open(spec, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data.get(key) or data.get("predicted_frames") or data.get("changed") or []
        return data
    return _ints(spec)


if __name__ == "__main__":
    sys.exit(C.main_guard(main))
