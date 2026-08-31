#!/usr/bin/env python3
"""Sound: measure the loudness the platform will measure, and normalise to it.

Loudness is normalised LAST, because every trim moves the integrated number,
and it is measured on the FINAL cut, not on the mix stem.

One limit stated up front, because it decides whether an answer here is worth
anything. This measures ITU-R BS.1770 level gated loudness, which is what EBU
R128 asks for: the whole signal, gated at -10 LU relative, with no emphasis on
speech. The large VOD platforms specify DIALOG GATED loudness instead, which is
a different measurement made by a different meter over the dialogue only. This
tool cannot produce a dialog gated number and will not pretend to: against a
dialog gated profile it reports what it measured, names the gap, and says the
number has to come from a dialogue gated meter.

  measure    integrated loudness, range, true peak
  check      against a profile's target, with the gate checked too
  normalise  two pass loudnorm to a target, by constant gain where possible
  layout     channel count, order and sample rate against the delivery
  clipping   samples on the ceiling, and the RUNS of them, which a true peak
             number cannot see

Usage:
  python audio.py measure FILM.mov
  python audio.py check FILM.mov --profile broadcast_hd_r128
  python audio.py normalise FILM.mov --profile broadcast_hd_r128 --out OUT.mov
  python audio.py layout FILM.mov --profile broadcast_hd_r128
  python audio.py clipping RETURNED_CUT.mov
  (add --json for structured output)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _common as C  # noqa: E402


def measure(path, stream="a:0"):
    """Integrated loudness, loudness range and true peak, via ffmpeg ebur128."""
    C.need("ffmpeg")
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path),
           "-map", f"0:{stream}", "-af", "ebur128=peak=true", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 and "Summary" not in proc.stderr:
        raise RuntimeError("ebur128 failed: " + proc.stderr.strip()[-400:])
    text = proc.stderr

    def grab(label, block=None):
        pattern = rf"{label}:\s*(-?\d+(?:\.\d+)?)"
        region = text
        if block:
            m = re.search(rf"{block}:(.*?)(?:\n\s*\n|$)", text, re.S)
            region = m.group(1) if m else text
        m = re.search(pattern, region)
        return float(m.group(1)) if m else None

    integrated = grab("I", "Integrated loudness")
    threshold = grab("Threshold", "Integrated loudness")
    lra = grab("LRA", "Loudness range")
    lra_low = grab("LRA low", "Loudness range")
    lra_high = grab("LRA high", "Loudness range")
    peak = grab("Peak", "True peak")
    return {"file": os.path.abspath(path), "stream": stream,
            "integrated_lufs": integrated, "threshold_lufs": threshold,
            "loudness_range_lu": lra, "lra_low_lufs": lra_low,
            "lra_high_lufs": lra_high, "true_peak_dbtp": peak,
            "gate": "bs1770",
            "measured_with": "ffmpeg ebur128, ITU-R BS.1770 level gated",
            "note": "Measured over the whole signal with no emphasis on speech, "
                    "which is what EBU R128 asks for and is NOT what a dialog "
                    "gated platform target means."}


def check(path, profile, stream="a:0"):
    """Hold the measurement against the profile, and check the GATE matches."""
    loud = (profile.get("audio") or {}).get("loudness") or {}
    got = measure(path, stream)
    want_gate = loud.get("gate")
    rows = []

    def add(field, want, got_value, ok, note=""):
        rows.append({"field": field, "want": want, "got": got_value,
                     "verdict": "ok" if ok else "MISMATCH", "note": note})

    if want_gate and want_gate != got["gate"]:
        rows.append({
            "field": "loudness gate", "want": want_gate, "got": got["gate"],
            "verdict": "CANNOT MEASURE",
            "note": "This profile is measured with a dialogue gated meter and "
                    "this tool is not one. The number below is the BS.1770 "
                    "gated loudness of the whole signal; it is not comparable "
                    "with the target and the difference is not a constant. Get "
                    "the dialog gated figure from the mix stage or a meter that "
                    "does it, and record which was used."})
    target = loud.get("target_i")
    tol = loud.get("tol_i")
    if target is not None and got["integrated_lufs"] is not None:
        delta = got["integrated_lufs"] - target
        inside = tol is None or abs(delta) <= tol + 1e-9
        add("integrated loudness", f"{target} LUFS +/-{tol}",
            f"{got['integrated_lufs']} LUFS", inside and want_gate == got["gate"],
            note=f"{delta:+.2f} LU from target"
                 + ("" if want_gate == got["gate"] else ", but see the gate above"))
    max_tp = loud.get("max_tp")
    if max_tp is not None and got["true_peak_dbtp"] is not None:
        add("true peak", f"not above {max_tp} dBTP",
            f"{got['true_peak_dbtp']} dBTP", got["true_peak_dbtp"] <= max_tp + 1e-9,
            note="R128 allows a measurement tolerance of 0.3 dB on a 20 kHz "
                 "limited signal, and a data reduced distribution path may set "
                 "a lower ceiling than production.")
    max_lra = loud.get("max_lra")
    if max_lra is not None and got["loudness_range_lu"] is not None:
        add("loudness range", f"not above {max_lra} LU",
            f"{got['loudness_range_lu']} LU",
            got["loudness_range_lu"] <= max_lra + 1e-9)
    bad = [r for r in rows if r["verdict"] != "ok"]
    return {"file": got["file"], "profile": profile.get("slug"),
            "measurement": got, "rows": rows, "failing": len(bad),
            "verdict": ("inside the profile" if not bad else
                        f"{len(bad)} item(s) do not meet the profile")}


def normalise(path, profile, out, stream="a:0", linear=True):
    """Two pass loudnorm to the profile's target.

    Pass one measures, pass two applies. Constant gain (linear) where the offset
    allows it, because a delivered film should be turned up or down, not
    reshaped. When loudnorm cannot stay linear it says so in its own output and
    that is reported rather than swallowed.

    Picture is stream copied. Sample rate and channel count are held at the
    source's, because loudnorm resamples internally and a silent rate change is
    a delivery fault.
    """
    C.need("ffmpeg")
    loud = (profile.get("audio") or {}).get("loudness") or {}
    target = loud.get("target_i")
    max_tp = loud.get("max_tp")
    if target is None:
        raise ValueError("The profile does not name a loudness target. That is a "
                         "project fact; ask for it.")
    if loud.get("gate") and loud["gate"] != "bs1770":
        raise ValueError(
            f"This profile wants a {loud['gate']} gated target. Normalising to it "
            "with a BS.1770 gated meter would land the film in the wrong place. "
            "Do this at the mix stage with a meter that gates on dialogue.")

    import spec as SPEC
    info = SPEC.probe(path)
    aud = (info.get("audio") or [{}])[0]
    rate = aud.get("sample_rate") or 48000
    lra = loud.get("max_lra") or 11.0

    first = ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path),
             "-map", f"0:{stream}",
             "-af", f"loudnorm=I={target}:TP={max_tp or -1.0}:LRA={lra}:"
                    "print_format=json",
             "-f", "null", "-"]
    proc = subprocess.run(first, capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\".*?\}", proc.stderr, re.S)
    if not m:
        raise RuntimeError("loudnorm's first pass produced no measurement: "
                           + proc.stderr.strip()[-400:])
    measured = json.loads(m.group(0))

    af = (f"loudnorm=I={target}:TP={max_tp or -1.0}:LRA={lra}"
          f":measured_I={measured['input_i']}"
          f":measured_TP={measured['input_tp']}"
          f":measured_LRA={measured['input_lra']}"
          f":measured_thresh={measured['input_thresh']}"
          f":offset={measured['target_offset']}"
          f":linear={'true' if linear else 'false'}:print_format=summary")
    second = ["ffmpeg", "-nostdin", "-hide_banner", "-y", "-i", str(path),
              "-map", "0", "-c", "copy", "-c:a", "pcm_s24le", "-ar", str(rate),
              "-af", af, str(out)]
    proc2 = subprocess.run(second, capture_output=True, text=True)
    if proc2.returncode != 0:
        raise RuntimeError("The normalising pass failed: "
                           + proc2.stderr.strip()[-500:])
    kind = re.search(r"Normalization Type:\s*(\w+)", proc2.stderr)
    kind = kind.group(1).lower() if kind else "unknown"
    stayed_linear = kind == "linear"
    after = measure(out, stream)
    return {"in": os.path.abspath(path), "out": os.path.abspath(out),
            "target_i": target, "max_tp": max_tp,
            "first_pass": measured, "after": after,
            "linear_requested": linear, "normalisation_type": kind,
            "stayed_linear": stayed_linear,
            "verdict": f"{after['integrated_lufs']} LUFS, true peak "
                       f"{after['true_peak_dbtp']} dBTP after normalising",
            "note": ("Constant gain applied: the mix was moved, not reshaped."
                     if stayed_linear else
                     f"loudnorm reports normalisation type '{kind}', not linear. "
                     "Dynamic mode reshapes the mix rather than moving it, which "
                     "on a delivered film is a mix decision and not a technical "
                     "one. Say so before delivering, or go back to the mix."),
            "warning": "Verify this file: measuring the OUTPUT is the only proof "
                       "the normalisation landed, and the picture must be "
                       "re-checked too because the container was rewritten."}


# The whole decode is never held in memory, and the block reader below is the
# reason, not a style choice.
#
# Measured on this Mac, 31 Aug 2026, on the code this replaces, which took
# ffmpeg's entire f32 decode back through `capture_output=True` and then copied
# it again into an `array`: peak RSS ran at a flat 14.2 bytes per SAMPLE.
#
#     10 min stereo    57.6 M samples   0.829 GB   2.08 s
#      5 min 5.1       86.4 M samples   1.233 GB   3.04 s
#     20 min stereo   115.2 M samples   1.637 GB   4.12 s
#
# Dead linear, so a 90 minute 5.1 master at 48 kHz is 1.56 G samples and about
# 22 GB of peak on a 24 GB machine: an out of memory kill, not a slow run.
# SKILL.md offers this as a master gate, so that is exactly the file it was
# written for. Streamed, the peak is one block and does not move with length.
#
# The CPU side is the smaller half here and the larger half elsewhere: this
# machine ran the per sample Python loop at 28 M samples/s, the same code on the
# review machine (Linux, 31 Aug 2026) at 7.9 M/s, which is 3.3 minutes for that
# master. So numpy is used where it is installed and a block at a time Python
# path where it is not. numpy is NOT imported at the top of this file on
# purpose: everything else here is standard library, and a hard import would
# change which interpreter can run the sound department at all.
CLIP_BLOCK_FRAMES = 1 << 20

try:  # optional, and the Python path below is a full replacement for it
    import numpy as _np
except Exception:  # pragma: no cover - exercised by the fallback check
    _np = None


def _clip_state(ch, ceiling, run):
    return {"ceiling": float(ceiling), "run": int(run), "frames": 0,
            "worst": 0.0,
            "ch": [{"hits": 0, "runs": 0, "longest": 0, "cur": 0}
                   for _ in range(ch)]}


def _clip_scan_np(buf, ch, st):
    """One block, vectorised. Runs carry across the block join."""
    a = _np.frombuffer(buf, dtype="<f4").reshape(-1, ch)
    if a.size == 0:
        return
    av = _np.abs(a)
    w = float(av.max())
    if w > st["worst"]:
        st["worst"] = w
    st["frames"] += int(a.shape[0])
    if w < st["ceiling"]:
        # Nothing here can be on the ceiling, so no run survives this block.
        for cs in st["ch"]:
            cs["cur"] = 0
        return
    run, n = st["run"], int(a.shape[0])
    hot = av >= st["ceiling"]
    pad = _np.zeros(n + 2, dtype=_np.int8)
    for c in range(ch):
        cs, m = st["ch"][c], hot[:, c]
        cs["hits"] += int(m.sum())
        pad[1:-1] = m
        d = _np.diff(pad)
        starts, ends = _np.flatnonzero(d == 1), _np.flatnonzero(d == -1)
        if starts.size == 0:
            cs["cur"] = 0
            continue
        lens = (ends - starts).astype(_np.int64)
        carry = cs["cur"]
        # A run beginning at sample 0 is the SAME run the last block ended on,
        # so its real length includes the carry -- and if it had already
        # reached `run` back there it was already counted, so it must not be
        # counted twice.
        joined = bool(starts[0] == 0)
        if joined:
            lens[0] += carry
        counted = int((lens >= run).sum())
        if joined and carry >= run and lens[0] >= run:
            counted -= 1
        cs["runs"] += counted
        cs["longest"] = max(cs["longest"], int(lens.max()))
        cs["cur"] = int(lens[-1]) if int(ends[-1]) == n else 0


def _clip_scan_py(buf, ch, st):
    """The same block with no numpy, and the same answer.

    max() and min() over an array.array are C loops; the per sample loop under
    them is not. A block with nothing near the ceiling is the common case even
    in a bad file, and skipping it there is what keeps this affordable.
    """
    import array
    a = array.array("f")
    a.frombytes(buf)
    if not len(a):
        return
    st["frames"] += len(a) // ch
    hi, lo = max(a), min(a)
    w = hi if hi > -lo else -lo
    if w > st["worst"]:
        st["worst"] = w
    if w < st["ceiling"]:
        for cs in st["ch"]:
            cs["cur"] = 0
        return
    ceiling, run = st["ceiling"], st["run"]
    for c in range(ch):
        cs = st["ch"][c]
        cur, hits, runs, longest = cs["cur"], cs["hits"], cs["runs"], cs["longest"]
        for i in range(c, len(a), ch):
            v = a[i]
            if (-v if v < 0 else v) >= ceiling:
                hits += 1
                cur += 1
                # `== run`, not `>= run`, and that is what carries the join for
                # free: a run already past `run` when the block ended never
                # equals it again, so it cannot be counted a second time.
                if cur == run:
                    runs += 1
                if cur > longest:
                    longest = cur
            else:
                cur = 0
        cs.update(hits=hits, runs=runs, longest=longest, cur=cur)


def clipping(path, stream="a:0", ceiling=0.995, run=2, block=CLIP_BLOCK_FRAMES,
             use_numpy=None):
    """Count samples on the ceiling, and the RUNS of them, which is the tell.

    A true peak number cannot answer this. A file driven into a limiter and
    flattened reads a perfectly compliant peak and is destroyed; a clean AAC
    decode overshoots 0 dBFS on a few hundred isolated samples and is fine. The
    two are told apart by whether the samples at the ceiling are CONSECUTIVE.

    Measured on a real returned cut, 2026-08-31: a client's re-export of our own
    mix, at a fitted gain of exactly 1.98981 (+5.98 dB), carried 65,611 samples
    on the ceiling in 26,076 consecutive pairs. Our own master, the same
    programme, carried 408 samples on the ceiling and no runs worth the name.
    Two orders of magnitude, on a measurement no picture check and no duration
    check can see, and both files pass a true peak gate.

    `ceiling` is a fraction of full scale, `run` the shortest run counted. The
    decode is 32 bit float so nothing is clipped by the instrument itself, and
    every channel is counted separately as well as together, because a fault
    that lives in one channel is invisible in a sum.

    The decode is STREAMED: see the note above CLIP_BLOCK_FRAMES for what the
    version that held it whole cost on a feature. `block` is the block size in
    sample frames and only exists so a check can drive the block join through
    the middle of a run; `use_numpy` likewise forces one arithmetic path or the
    other, and the two are required to agree exactly.
    """
    C.need("ffmpeg")
    idx = int(stream.split(":")[-1]) if ":" in stream else 0
    rows = layout(path)["streams"]
    if idx >= len(rows):
        raise ValueError(f"This file has {len(rows)} audio stream(s), not {idx + 1}.")
    ch = int(rows[idx].get("channels") or 1)
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
           "-map", f"0:{stream}", "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    st = _clip_state(ch, ceiling, run)
    np_path = (_np is not None) if use_numpy is None else bool(use_numpy)
    if np_path and _np is None:
        raise RuntimeError("numpy was asked for and is not installed here.")
    scan = _clip_scan_np if np_path else _clip_scan_py
    size = max(1, int(block)) * ch * 4
    with tempfile.TemporaryFile() as err:
        # stderr goes to a FILE, never a pipe. A pipe nobody drains stops
        # ffmpeg dead the moment the 64 KB kernel buffer fills, which is the
        # deadlock prove.py::_walk_digests was carrying; a file cannot fill,
        # and unlike DEVNULL it can still be read back for the error message.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
        rest = b""
        try:
            while True:
                buf = proc.stdout.read(size)
                if not buf:
                    break
                if rest:
                    buf, rest = rest + buf, b""
                keep = len(buf) // (ch * 4) * (ch * 4)
                if keep != len(buf):
                    buf, rest = buf[:keep], buf[keep:]
                if buf:
                    scan(buf, ch, st)
        finally:
            proc.stdout.close()
            rc = proc.wait()
        if rc != 0 or not st["frames"]:
            err.seek(0)
            raise RuntimeError("Could not decode the audio: "
                               + err.read().decode("utf-8", "replace")[:300])
    total, worst = st["frames"], st["worst"]
    per = [{"channel": c, "samples_at_ceiling": cs["hits"],
            "runs_of_%d_or_more" % run: cs["runs"],
            "longest_run": cs["longest"],
            "ppm_of_channel": round(1e6 * cs["hits"] / total, 2) if total else 0}
           for c, cs in enumerate(st["ch"])]
    hits = sum(x["samples_at_ceiling"] for x in per)
    runs = sum(x["runs_of_%d_or_more" % run] for x in per)
    longest = max([x["longest_run"] for x in per] or [0])
    ppm = 1e6 * hits / (total * ch) if total else 0
    import math
    dbfs = 20 * math.log10(worst) if worst > 0 else float("-inf")
    if runs == 0:
        verdict = ("No flat topping. %d sample(s) reached the ceiling and none "
                   "of them ran on. This is what a clean decode looks like."
                   % hits)
    elif longest >= 8 or ppm > 100:
        verdict = ("CLIPPED. %d samples on the ceiling in %d run(s), longest %d "
                   "samples. This is a signal that was pushed into the ceiling "
                   "and flattened there, not a decode overshoot. Do not carry "
                   "it into a master. If it came back from a client, fit it "
                   "against our own mix before assuming it is a new sound."
                   % (hits, runs, longest))
    else:
        verdict = ("%d samples on the ceiling in %d short run(s), longest %d. "
                   "Borderline: listen to it, and compare against the mix it "
                   "came from before deciding." % (hits, runs, longest))
    return {
        "file": os.path.abspath(path), "stream": stream,
        "channels": ch, "samples_per_channel": total,
        "ceiling_fraction": ceiling, "run_length_counted": run,
        "peak_sample": round(worst, 6), "peak_sample_dbfs": round(dbfs, 3),
        "samples_at_ceiling": hits, "runs": runs, "longest_run": longest,
        "ppm_of_all_samples": round(ppm, 2),
        "per_channel": per,
        "decode_path": "ffmpeg:f32le streamed in %d frame blocks, %s "
                       "(no clipping in the instrument)"
                       % (block, "numpy" if np_path else "python"),
        "verdict": verdict,
        "limit": "This counts flat topping. It does not say the sound is WRONG, "
                 "and it says nothing about the loudness: run 'measure' for "
                 "that. A quiet file can be clipped and a loud one clean.",
    }


def layout(path, profile=None):
    """Channel layout, order and sample rate against the delivery."""
    import spec as SPEC
    info = SPEC.probe(path)
    streams = info.get("audio") or []
    rows = []
    want = (profile or {}).get("audio") or {}
    for a in streams:
        row = dict(a)
        row["issues"] = []
        if want.get("sample_rate") and a.get("sample_rate") != want["sample_rate"]:
            row["issues"].append(f"sample rate {a.get('sample_rate')} against "
                                 f"{want['sample_rate']}")
        if want.get("channels") and a.get("channels") != want["channels"]:
            row["issues"].append(f"{a.get('channels')} channels against "
                                 f"{want['channels']}")
        if want.get("layout") and a.get("layout") != want["layout"]:
            row["issues"].append(f"layout {a.get('layout')} against {want['layout']}")
        rows.append(row)
    return {"file": info["file"], "streams": rows,
            "stream_count": len(rows),
            "note": "Channel ORDER inside a layout is a delivery fact that no "
                    "probe can confirm: a file can be tagged 5.1 with the centre "
                    "and the LFE swapped and every tool will agree with it. "
                    "Confirm the order against the mix's own routing sheet."}


def main(argv=None):
    ap = C.parser_for(__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    me = sub.add_parser("measure", help="Loudness, range and true peak")
    me.add_argument("file")
    me.add_argument("--stream", default="a:0")
    C.add_json(me)

    ck = sub.add_parser("check", help="Against a profile")
    ck.add_argument("file")
    ck.add_argument("--profile", required=True)
    ck.add_argument("--stream", default="a:0")
    C.add_json(ck)

    no = sub.add_parser("normalise", help="Two pass loudnorm to the target")
    no.add_argument("file")
    no.add_argument("--profile", required=True)
    no.add_argument("--out", required=True)
    no.add_argument("--stream", default="a:0")
    no.add_argument("--allow-dynamic", action="store_true",
                    help="Let loudnorm reshape the mix if constant gain cannot "
                         "reach the target")
    C.add_json(no)

    la = sub.add_parser("layout", help="Channels, order and rate")
    la.add_argument("file")
    la.add_argument("--profile")
    C.add_json(la)

    cl = sub.add_parser("clipping", help="Samples on the ceiling, and their runs")
    cl.add_argument("file")
    cl.add_argument("--stream", default="a:0")
    cl.add_argument("--ceiling", type=float, default=0.995,
                    help="Fraction of full scale counted as the ceiling")
    cl.add_argument("--run", type=int, default=2,
                    help="Shortest run of consecutive ceiling samples counted")
    C.add_json(cl)

    args = ap.parse_args(argv)
    import spec as SPEC

    if args.cmd == "measure":
        res = measure(args.file, args.stream)
        return C.emit(res, args.json, _print_measure)
    if args.cmd == "check":
        res = check(args.file, SPEC.load_profile(args.profile), args.stream)
        return C.emit(res, args.json, lambda r: (
            _print_measure(r["measurement"]),
            print(),
            [print(f"  [{row['verdict']}] {row['field']}: want {row['want']}, "
                   f"got {row['got']}" + (f"\n      {row['note']}" if row["note"] else ""))
             for row in r["rows"]],
            print(f"\n  {r['verdict']}")))
    if args.cmd == "normalise":
        res = normalise(args.file, SPEC.load_profile(args.profile), args.out,
                        args.stream, not args.allow_dynamic)
        return C.emit(res, args.json, lambda r: (
            print(f"  {r['verdict']}"), print(f"  {r['note']}"),
            print(f"  {r['warning']}")))
    if args.cmd == "layout":
        prof = SPEC.load_profile(args.profile) if args.profile else None
        res = layout(args.file, prof)
        return C.emit(res, args.json, lambda r: (
            [print(f"  stream {s['index']}: {s['codec']} {s['sample_rate']} Hz, "
                   f"{s['channels']} ch ({s['layout']})"
                   + ("" if not s["issues"] else "\n      " + "; ".join(s["issues"])))
             for s in r["streams"]],
            print(f"\n  {r['note']}")))
    if args.cmd == "clipping":
        res = clipping(args.file, args.stream, args.ceiling, args.run)
        return C.emit(res, args.json, lambda r: (
            print(f"  peak sample     {r['peak_sample']:.6f} "
                  f"({r['peak_sample_dbfs']:+.2f} dBFS)"),
            print(f"  on the ceiling  {r['samples_at_ceiling']} of "
                  f"{r['samples_per_channel'] * r['channels']} samples "
                  f"({r['ppm_of_all_samples']} ppm)"),
            print(f"  runs            {r['runs']}, longest {r['longest_run']} "
                  f"samples"),
            [print(f"    ch {c['channel']}: {c['samples_at_ceiling']} on the "
                   f"ceiling, longest run {c['longest_run']}")
             for c in r["per_channel"]],
            print(f"\n  {r['verdict']}"),
            print(f"  {r['limit']}")))
    return 0


def _print_measure(m):
    print(f"{os.path.basename(m['file'])} stream {m['stream']}")
    print(f"  integrated      {m['integrated_lufs']} LUFS "
          f"(gate threshold {m['threshold_lufs']})")
    print(f"  loudness range  {m['loudness_range_lu']} LU "
          f"({m['lra_low_lufs']} to {m['lra_high_lufs']})")
    print(f"  true peak       {m['true_peak_dbtp']} dBTP")
    print(f"  measured with   {m['measured_with']}")
    print(f"  {m['note']}")


if __name__ == "__main__":
    sys.exit(C.main_guard(main))
