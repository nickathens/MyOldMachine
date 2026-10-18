#!/usr/bin/env python3
# ruff: noqa: E401, E701, E702, E731, E741
# (The verbatim ReelMimic block below keeps its own style: several imports or
# statements on one line, three assigned lambdas, a loop variable named l.)
"""Breakdown of a reference video: how it is built, not what it says.

    python breakdown.py <url-or-file> [--start T] [--end T] [--out-dir DIR]

Lists every shot with its start, length, camera move and speed, and how it
looks (brightness, contrast, share near black, four main colours). Measures
the music: tempo, beats, mean shot length in beats, and up to three moments
where it builds and hits. Finds flash frames and draws two contact sheets.
Prints a markdown report on stdout and writes report.json, the sheets and the
analysis copies into the working folder.

The measuring functions between the two rulers below are ReelMimic's
(https://github.com/edenfunf/reelmimic, file
.claude/skills/video-clone/scripts/analyze.py at commit
9fb7a07ca990e0215881182151974b48dc0a4d7b, sha256 867fb37a...c8b87a), copied
verbatim. Do not edit them in place: if one needs to change, pull it again
from upstream so the credit stays honest, and put local behaviour in the
adapter below the block. test_breakdown.py pins the block's text.

Left out of the copy, and what does the job here instead:
  fetch()            watch's download.py (the same 720p cap, plus the yt-dlp
                     settings watch already uses)
  make_proxy()       make_analysis_copy(): also cuts the range and scales to
                     at most 640 px wide
  shot_details()     the per shot work runs in this process through
                     _shot_details_part(), not in a pool of processes
  flashes()          flash_frames(): the same measurement, but a flash must
                     be brighter than the frames on both sides of it
  cache_key(), summary(), main() and the ~/.reelmimic cache: this skill
                     keeps nothing outside its working folder

ReelMimic's licence, as its terms require:

  MIT License

  Copyright (c) 2026 ReelMimic contributors

  Permission is hereby granted, free of charge, to any person obtaining a copy
  of this software and associated documentation files (the "Software"), to deal
  in the Software without restriction, including without limitation the rights
  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
  copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:

  The above copyright notice and this permission notice shall be included in all
  copies or substantial portions of the Software.

  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
  SOFTWARE.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from download import download  # noqa: E402
from frames import parse_time  # noqa: E402


# ===========================================================================
# Verbatim from ReelMimic analyze.py @ 9fb7a07c (MIT). Do not edit in place.
# ===========================================================================
def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def probe(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate,channels",
             "-of", "json", path])
    j = json.loads(r.stdout)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    fps = None
    if v and v.get("r_frame_rate") and v["r_frame_rate"] != "0/0":
        n, d = v["r_frame_rate"].split("/"); fps = round(float(n) / float(d), 3)
    return {"duration": float(j["format"]["duration"]), "width": v and v["width"], "height": v and v["height"], "fps": fps,
            "has_audio": a is not None, "channels": a and a.get("channels")}


def detect_crop(path, dur, w, h):
    """Most common cropdetect box over the whole video; None if the picture already fills the frame."""
    r = run(["ffmpeg", "-hide_banner", "-i", path, "-t", str(min(dur, 60)), "-vf", "fps=2,cropdetect=limit=24:round=2:reset=1",
             "-f", "null", "-"])
    boxes = collections.Counter(re.findall(r"crop=(\d+:\d+:\d+:\d+)", r.stderr))
    if not boxes: return None
    cw, ch, cx, cy = map(int, boxes.most_common(1)[0][0].split(":"))
    if cw * ch > .85 * w * h: return None
    return {"w": cw, "h": ch, "x": cx, "y": cy, "note": "picture is a strip inside a larger frame (screen recording / letterbox)"}


def ui_strip_crop(path, dur, w, h):
    """Phone screen recordings put the video between two busy UI bands that cropdetect can't see through (they aren't
    black). Find the band of rows whose content changes most over time: that's the playing video."""
    try:
        import numpy as np, cv2
    except ImportError:
        return None
    cap = cv2.VideoCapture(path); frames = []
    for k in range(12):
        cap.set(cv2.CAP_PROP_POS_MSEC, (k + .5) / 12 * dur * 1000)
        ok, f = cap.read()
        if ok: frames.append(cv2.cvtColor(cv2.resize(f, (w // 4, h // 4)), cv2.COLOR_BGR2GRAY).astype("float32"))
    cap.release()
    if len(frames) < 4: return None
    var = np.stack(frames).std(axis=0).mean(axis=1)            # temporal change per row
    rows = np.where(var > max(6.0, var.max() * .35))[0]
    if not len(rows): return None
    # the longest run of consecutive active rows
    runs, start = [], rows[0]
    for a, b in zip(rows, rows[1:]):
        if b != a + 1: runs.append((start, a)); start = b
    runs.append((start, rows[-1]))
    y0, y1 = max(runs, key=lambda r: r[1] - r[0])
    y0, y1 = int(y0 * 4), int((y1 + 1) * 4)
    if (y1 - y0) > .85 * h or (y1 - y0) < .15 * h: return None
    return {"w": w, "h": (y1 - y0) // 2 * 2, "x": 0, "y": y0, "note": "moving picture band between static UI (phone screen recording)"}


def shots(path):
    try:
        from scenedetect import detect, ContentDetector
    except ImportError:
        return []
    lst = detect(path, ContentDetector(threshold=22))
    sec = lambda x: x.seconds if hasattr(type(x), "seconds") else x.get_seconds()
    return [[round(sec(a), 3), round(sec(b), 3)] for a, b in lst]


def sheets(path, dur, cuts, out):
    n = int(min(dur, 64)); cols = 8; rows = max(1, -(-n // cols))
    run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vf", f"fps={n / dur:.5f},scale=300:-2,tile={cols}x{rows}", "-frames:v", "1",
         os.path.join(out, "sheet_1fps.jpg")])
    if cuts:
        try:
            import cv2, numpy as np
        except ImportError:
            return
        step = max(1, len(cuts) // 48); mids = [(a + b) / 2 for a, b in cuts][::step][:48]
        cap = cv2.VideoCapture(path); tiles = []
        for m in mids:
            cap.set(cv2.CAP_PROP_POS_MSEC, m * 1000); ok, f = cap.read()
            if ok:
                f = cv2.resize(f, (300, round(300 * f.shape[0] / f.shape[1])))
                cv2.putText(f, f"{m:.1f}s", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3); cv2.putText(f, f"{m:.1f}s", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
                tiles.append(f)
        cap.release()
        if tiles:
            h = tiles[0].shape[0]; tiles = [t if t.shape[0] == h else cv2.resize(t, (300, h)) for t in tiles]
            while len(tiles) % 8: tiles.append(np.zeros_like(tiles[0]))
            grid = np.vstack([np.hstack(tiles[r:r + 8]) for r in range(0, len(tiles), 8)])
            cv2.imwrite(os.path.join(out, "sheet_scenes.jpg"), grid, [cv2.IMWRITE_JPEG_QUALITY, 85])


def _shot_details_part(path, spans, offset):
    """Per shot: camera motion (optical flow → push/pull/pan/tilt/rotate/static + speed) and look (brightness, contrast,
    dark ratio, colourfulness, dominant colours). These numbers let the plan and the review match the reference shot by shot."""
    try:
        import numpy as np, cv2
    except ImportError:
        return []
    cap = cv2.VideoCapture(path); out = []
    for i, (a, b) in enumerate(spans, offset):
        n = max(3, min(10, int((b - a) * 5))); ts = [a + (b - a) * (k + .5) / n for k in range(n)]
        grays, looks = [], []
        for t in ts:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000); ok, f = cap.read()
            if not ok: continue
            sm = cv2.resize(f, (320, round(320 * f.shape[0] / f.shape[1])))
            grays.append(cv2.cvtColor(sm, cv2.COLOR_BGR2GRAY)); looks.append(sm)
        if len(grays) < 2: continue
        H_, W_ = grays[0].shape; yy, xx = np.mgrid[0:H_, 0:W_]; xc, yc = xx - W_ / 2, yy - H_ / 2
        tx = ty = zoom = rot = 0.0
        for g0, g1 in zip(grays, grays[1:]):
            fl = cv2.calcOpticalFlowFarneback(g0, g1, None, .5, 3, 21, 3, 5, 1.2, 0)
            u, v = fl[..., 0], fl[..., 1]
            # only textured pixels carry motion information (black stages and static UI bars would pull the median to 0)
            gx = cv2.Sobel(g0, cv2.CV_32F, 1, 0); gy = cv2.Sobel(g0, cv2.CV_32F, 0, 1)
            tex = np.hypot(gx, gy) > 40
            tex[: max(1, H_ // 25)] = False   # ignore a thin top strip (browser tab bars in screen recordings)
            if tex.sum() < 200: continue
            uu, vv, xx_, yy_ = u[tex], v[tex], xc[tex], yc[tex]
            tx += float(np.median(uu)); ty += float(np.median(vv))
            r2 = xx_ ** 2 + yy_ ** 2 + 1e-6
            zoom += float(np.median((uu * xx_ + vv * yy_) / r2)); rot += float(np.median((vv * xx_ - uu * yy_) / r2))
        span = ts[-1] - ts[0] or 1
        mv = {"pan_x": round(tx / W_ / span, 4), "tilt_y": round(ty / H_ / span, 4), "zoom": round(zoom / span, 4), "rotate": round(rot / span, 4)}
        kind = []
        if abs(mv["zoom"]) > 0.006: kind.append("push_in" if mv["zoom"] > 0 else "pull_out")
        if abs(mv["pan_x"]) > 0.008: kind.append("move_left" if mv["pan_x"] > 0 else "move_right")   # content moves right → camera moves left
        if abs(mv["tilt_y"]) > 0.008: kind.append("move_up" if mv["tilt_y"] > 0 else "move_down")
        if abs(mv["rotate"]) > 0.01: kind.append("roll/orbit")
        mv["kind"] = kind or ["static"]
        mv["speed"] = round(max(abs(mv["pan_x"]), abs(mv["tilt_y"]), abs(mv["zoom"]), abs(mv["rotate"])), 3)
        mv["pace"] = "still" if mv["speed"] < 0.006 else "slow" if mv["speed"] < 0.04 else "medium" if mv["speed"] < 0.12 else "fast"
        px = np.concatenate([l.reshape(-1, 3) for l in looks]).astype(np.float32)
        lum = (0.114 * px[:, 0] + 0.587 * px[:, 1] + 0.299 * px[:, 2]) / 255
        rg = px[:, 2] - px[:, 1]; yb = 0.5 * (px[:, 2] + px[:, 1]) - px[:, 0]
        colorful = float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)) / 255
        sample = px[:: max(1, len(px) // 4000)]
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
        _, lab, cen = cv2.kmeans(sample, 4, None, crit, 2, cv2.KMEANS_PP_CENTERS)
        order = np.argsort(-np.bincount(lab.ravel(), minlength=4))
        dom = ["#%02X%02X%02X" % (int(cen[k][2]), int(cen[k][1]), int(cen[k][0])) for k in order]
        out.append({"shot": i + 1, "start": round(a, 2), "end": round(b, 2), "camera": mv,
                    "look": {"brightness": round(float(lum.mean()), 3), "contrast": round(float(lum.std()), 3),
                             "dark_ratio": round(float((lum < 0.08).mean()), 3), "colourfulness": round(colorful, 3), "dominant": dom}})
    cap.release()
    return out


def audio(path, info, out):
    """Pick a phase-safe channel mix, normalise, then measure tempo, beats and the densest 30 s windows."""
    if not info["has_audio"]: return {"present": False}
    import numpy as np, librosa
    raw = os.path.join(out, "_stereo.wav")
    run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-ac", "2", "-ar", "22050", raw])
    y, sr = librosa.load(raw, sr=22050, mono=False)
    if y.ndim == 1: y = np.stack([y, y])
    db = lambda x: float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12))
    L, R, M = db(y[0]), db(y[1]), db((y[0] + y[1]) / 2)
    phase_inverted = M < max(L, R) - 10
    mix = "pan=mono|c0=c0" if phase_inverted else "pan=mono|c0=0.5*c0+0.5*c1"
    wav = os.path.join(out, "audio.wav")
    run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-af", f"{mix},loudnorm=I=-14:TP=-1.5", "-ar", "44100", wav])
    os.remove(raw)
    y, sr = librosa.load(wav, sr=22050, mono=True)
    level = db(y)
    if level < -45:
        return {"present": True, "silent": True, "levels_db": {"L": L, "R": R, "mono": M}, "phase_inverted": phase_inverted,
                "note": "no usable sound: ask the user to re-record with system audio on, or to supply the audio file"}
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    tempo = float(np.atleast_1d(tempo)[0])
    ibi = np.diff(beats); bpm = float(60 / np.median(ibi)) if len(ibi) else tempo
    on = librosa.onset.onset_strength(y=y, sr=sr); ot = librosa.times_like(on, sr=sr)
    win, dur = 30.0, len(y) / sr
    cands = []
    for i in range(0, len(beats), 4):
        s = beats[i]
        if s + win > dur: break
        cands.append((float(on[(ot >= s) & (ot < s + win)].mean()), float(s)))
    cands.sort(reverse=True)
    # noise floor: the quietest 10th percentile of 0.5 s windows
    hop = sr // 2; w = [db(y[i:i + hop]) for i in range(0, len(y) - hop, hop)]
    peaks = peak_candidates(y, sr, beats)
    return {"present": True, "silent": False, "levels_db": {"L": round(L, 1), "R": round(R, 1), "mono": round(M, 1)},
            "phase_inverted": bool(phase_inverted), "noise_floor_db": round(float(np.percentile(w, 10)), 1) if w else None,
            "bpm": round(bpm, 2), "beat_seconds": round(60 / bpm, 4), "first_beat": round(float(beats[0]), 3) if len(beats) else None,
            "beats": [round(float(b), 3) for b in beats],
            "best_30s_starts": [{"start": round(s, 2), "density": round(m, 3)} for m, s in cands[:5]],
            "peak_candidates": peaks,
            "quality_warning": ("mono only (phase-inverted stereo)" if phase_inverted else None)}


def peak_candidates(y, sr, beats, n=3):
    """Where the music builds and then hits: the moments a reference spends its biggest visual on. Three curves in
    0.25 s steps (loudness, onset strength, spectral fullness), each z-scored and summed, because mastered pop is
    loudness-flat and only gets fuller and busier at a chorus. A candidate is the sharpest rise from the 2 s before to
    the 1 s after, scored higher when a quieter stretch (the held breath) sits right before it; snapped to the nearest
    beat. Numbers only point: the director confirms by looking at the frames."""
    import numpy as np, librosa
    hop = sr // 4
    rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    bw = librosa.feature.spectral_bandwidth(y=y, sr=sr, hop_length=hop)[0]
    m = min(len(rms), len(onset), len(bw))
    if m < 24: return []
    z = lambda v: (v - v.mean()) / (v.std() + 1e-9)
    cur = z(20 * np.log10(rms[:m] + 1e-9)) + z(onset[:m]) + z(bw[:m])
    sm = np.convolve(cur, np.ones(4) / 4, mode="same")
    out = []
    for i in range(8, m - 4):
        before, after = sm[i - 8:i].mean(), sm[i:i + 4].mean()
        rise = after - before
        if rise < 0.8: continue
        dip = float(sm[i - 8:i].min())
        out.append((rise + 0.5 * max(0.0, before - dip) + 0.3 * after, i * 0.25, rise, after))
    out.sort(reverse=True)
    picked = []
    for score, t, rise, after in out:
        if len(beats): t = float(beats[np.argmin(np.abs(np.asarray(beats) - t))])
        if any(abs(t - p["t"]) < 6 for p in picked): continue
        picked.append({"t": round(t, 2), "rise": round(float(rise), 2), "level_after": round(float(after), 2),
                       "kind": "drop" if rise >= 2.5 else "lift"})
        if len(picked) >= n: break
    return sorted(picked, key=lambda p: p["t"])


# ===========================================================================
# Local adapter: watch's download, the range, a small analysis copy, the
# music checks and the report. Nothing below this line is ReelMimic's text.
# ===========================================================================
MADE_WITH = ("ReelMimic analyze.py @ 9fb7a07c "
             "(https://github.com/edenfunf/reelmimic, MIT), adapted for watch")
NEEDS = ("numpy", "cv2", "scenedetect", "librosa")
MAX_SECONDS = 15 * 60      # the longest stretch one run analyses
PROXY_WIDTH = 640          # nothing in the analysis reads finer than 320 px
# Everything a run writes into its folder, cleared first so a reused folder
# can never hand back an earlier run's sheet or report.
OUTPUTS = ("report.json", "sheet_1fps.jpg", "sheet_scenes.jpg", "proxy.mp4",
           "proxy_full.mp4", "audio.wav", "_stereo.wav", "sound.wav")
MARKER = ".breakdown"      # marks a folder this tool made, so it may clear OUTPUTS there


def log(msg: str) -> None:
    print(f"[breakdown] {msg}", file=sys.stderr)


def missing_modules(names=None) -> list[str]:
    """The analysis libraries this Python cannot import (default: NEEDS, read
    at call time so a caller or test can swap it)."""
    return [n for n in (NEEDS if names is None else names) if importlib.util.find_spec(n) is None]


def claim_folder(work: Path) -> None:
    """Make work ours, or refuse it. A run deletes and rewrites the OUTPUTS
    names in its folder (audio() writes audio.wav there whatever happens), so
    a project folder's own audio.wav or report.json must never be in reach: a
    folder that already holds anything is used only if an earlier run marked it."""
    work.mkdir(parents=True, exist_ok=True)
    marker = work / MARKER
    others = sorted(p.name for p in work.iterdir() if p.name != MARKER)
    if others and not marker.exists():
        raise SystemExit(f"{work} already holds files ({', '.join(others[:4])}"
                         f"{', ...' if len(others) > 4 else ''}); give --out-dir an empty or new folder")
    marker.touch()


def display_rotation(path: str) -> int:
    """The first video stream's display rotation in degrees, 0 when it has none."""
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream_side_data=rotation", "-of", "csv=p=0", path])
    m = re.search(r"-?\d+", r.stdout)
    return int(m.group()) if m else 0


def read_probe(path: str) -> dict:
    """probe(), with an unreadable file turned into a plain message, and the
    size the picture plays at. A phone stores an upright video as a landscape
    picture plus a 90 degree rotation; ffmpeg turns it upright when it
    decodes, so the analysis copy is upright and the report must say so too."""
    try:
        info = probe(path)
    except (KeyError, ValueError, TypeError, StopIteration) as exc:
        raise SystemExit(f"could not read {path} as a video ({type(exc).__name__}: {exc})")
    if info["width"] and abs(display_rotation(path)) % 180 == 90:
        info["width"], info["height"] = info["height"], info["width"]
    return info


def make_analysis_copy(src: str, dst: str, start: float | None = None,
                       end: float | None = None, crop: dict | None = None) -> str:
    """Video only, cut to the range, cropped when a box is given, at most
    PROXY_WIDTH wide, a keyframe every 12 frames so the per shot sampling
    seeks cheaply. On a 54 s 1080p ad this gave the same 26 shots, flash
    frames and music as reading the full size file, in half the time (28 s
    against 58 s); no shot's brightness moved by more than 0.002."""
    vf = [f"crop={crop['w']}:{crop['h']}:{crop['x']}:{crop['y']}"] if crop else []
    vf.append(f"scale='trunc(min({PROXY_WIDTH},iw)/2)*2':-2")
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-i", src, "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "16", "-g", "12", "-pix_fmt", "yuv420p", "-an", dst]
    r = run(cmd)
    if r.returncode or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        raise SystemExit(f"could not make the analysis copy: {r.stderr.strip()[-400:]}")
    return dst


def cut_audio(src: str, dst: str, start: float | None, end: float | None) -> str:
    """The sound of the range (or the whole file) on the picture's clock,
    every channel kept. A sound track can start after the picture (screen
    recordings, some downloads); written out as it is, its first sample lands
    at 0 and every beat and hit reads early by the gap. aresample's
    first_pts=0 fills that gap with silence instead. 32 bit float, because an
    AAC decode runs past full scale (1.058 on a 75 s ad) and 16 bit clipped
    it, which moved the build and hit scores."""
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    r = run(cmd + ["-i", src, "-vn", "-af", "aresample=async=1:first_pts=0", "-c:a", "pcm_f32le", dst])
    if r.returncode or not os.path.isfile(dst):
        raise RuntimeError(f"could not cut the sound: {r.stderr.strip()[-300:]}")
    return dst


def flash_frames(path: str) -> list[float]:
    """Frames much brighter than the frames on both sides: white flashes and
    impact frames. Adapted from ReelMimic's flashes(): the same 6 fps, 64x36
    luminance, brighter than 0.75 and a 0.3 jump. Its jump was measured from
    the darkest of the two frames before and the two after, so a cut into a
    bright card, or a dissolve out of one, read as a flash; on a 54 s ad both
    of its two hits were that. Here the jump must clear the brighter of
    the two sides, each side taken at its darkest frame. A flash shorter than
    one 6 fps sample (about 4 frames at 24 fps) can fall between samples."""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", "fps=6,scale=64:36,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8)
    if not len(a):
        return []
    lum = a[: len(a) // (64 * 36) * 64 * 36].reshape(-1, 64 * 36).mean(1) / 255
    out = []
    for i in range(2, len(lum) - 2):
        sides = max(lum[i - 2:i].min(), lum[i + 1:i + 3].min())
        if lum[i] > 0.75 and lum[i] - sides > 0.3:
            t = round(i / 6, 2)
            if not out or t - out[-1] > 1:
                out.append(t)
    return out


def refine_tempo(beats: list[float]) -> float | None:
    """Tempo from the mean of the beat intervals within 10% of the median.
    audio() reports 60 over the median interval, and every interval is a
    whole number of 512 sample hops at 22050 Hz (23.2 ms), so its figure moves
    in steps: 123.0, 129.2, 136.0 near 125 bpm. The mean of the normal
    intervals averages the hops out. None when there are too few beats."""
    import numpy as np
    if len(beats) < 8:
        return None
    ibi = np.diff(np.asarray(beats, dtype=float))
    med = float(np.median(ibi))
    keep = ibi[np.abs(ibi - med) <= 0.1 * med]
    if len(keep) < 4:
        return None
    return round(60.0 / float(keep.mean()), 1)


def music(path: str, info: dict, out: str, start: float | None = None,
          end: float | None = None) -> dict:
    """ReelMimic's audio() on the sound cut to the picture's clock, with its
    failures made readable and the tempo sharpened.

    audio() divides by zero when the tracker finds no beat at all (a held
    tone, a clip of a second or two); that comes back as no_beat. Any other
    failure comes back as an error string, so the picture analysis is never
    lost to the music. On success it adds tempo_bpm (refine_tempo)."""
    if not info.get("has_audio"):
        return {"present": False}
    cut = os.path.join(out, "sound.wav")
    try:
        au = audio(cut_audio(path, cut, start, end), info, out)
    except ZeroDivisionError:
        return {"present": True, "silent": False, "no_beat": True,
                "note": "the beat tracker found no beat at all"}
    except Exception as exc:
        return {"present": True, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if os.path.exists(cut):
            os.remove(cut)
    if au.get("silent"):
        return au
    au["tempo_bpm"] = refine_tempo(au.get("beats") or [])
    return au


def shift_times(rep: dict, offset: float) -> None:
    """Move every time in the report from range time to source time, so a
    shot at 0:12 of a range that starts at 1:00:00 reads 1:00:12."""
    if not offset:
        return

    def sh(v):
        return None if v is None else round(v + offset, 3)

    rep["shots"] = [[sh(a), sh(b)] for a, b in rep["shots"]]
    for d in rep["shot_details"]:
        d["start"], d["end"] = sh(d["start"]), sh(d["end"])
    rep["flashes"] = [sh(t) for t in rep["flashes"]]
    au = rep["audio"]
    if "first_beat" in au:
        au["first_beat"] = sh(au["first_beat"])
    if "beats" in au:
        au["beats"] = [sh(t) for t in au["beats"]]
    for w in au.get("best_30s_starts", []):
        w["start"] = sh(w["start"])
    for p in au.get("peak_candidates", []):
        p["t"] = sh(p["t"])


def analyse(video: str, work, start: float | None = None, end: float | None = None,
            source: str | None = None) -> dict:
    """Run the breakdown on a local file and write report.json into work."""
    work = Path(work)
    claim_folder(work)
    for name in OUTPUTS:
        (work / name).unlink(missing_ok=True)
    info = read_probe(video)
    dur = info["duration"]
    if not info["width"]:
        raise SystemExit("no picture in this file: a breakdown needs a video stream "
                         "(for music alone, the audio-analysis skill)")
    if start is not None and start >= dur:
        raise SystemExit(f"--start {start:.1f}s is past the end of the video ({dur:.1f}s)")
    s = start or 0.0
    e = min(end, dur) if end is not None else dur
    if e <= s:
        raise SystemExit("the range is empty: --end must be after --start")
    if e - s > MAX_SECONDS:
        raise SystemExit(
            f"{e - s:.0f}s is longer than one run takes ({MAX_SECONDS // 60} minutes): every "
            "frame and all the sound are read. Pick a section with --start and --end.")
    focused = start is not None or end is not None

    log("making the analysis copy")
    full = make_analysis_copy(video, str(work / "proxy_full.mp4"), start, end)
    finfo = read_probe(full)
    cdur, fw, fh = finfo["duration"], finfo["width"], finfo["height"]
    # ui_strip_crop looks for a phone screen recording, which is upright. On a
    # landscape frame it also took one still shot with its motion in a band
    # (a locked off wide shot of traffic) for one, and measured the band only.
    crop = detect_crop(full, cdur, fw, fh) or (ui_strip_crop(full, cdur, fw, fh) if fh > fw else None)
    vid = str(work / "proxy.mp4")
    if crop:
        make_analysis_copy(full, vid, crop=crop)
        os.remove(full)
    else:
        os.replace(full, vid)
    vinfo = read_probe(vid)

    log("finding the shots")
    cuts = shots(vid)
    one_shot = not cuts
    if one_shot:
        cuts = [[0.0, round(cdur, 3)]]
    log(f"{len(cuts)} shots; drawing the contact sheets")
    sheets(vid, cdur, cuts, str(work))
    log("reading camera and look, shot by shot")
    import cv2
    cv2.setRNGSeed(0)   # kmeans seeds the four colours; same input, same answer
    # ponytail: one process, so one core; ReelMimic's own pool takes up to 8.
    # A long reference with hundreds of shots takes longer; a pool of 2 is the
    # lift if that ever matters, kept off to spare a small machine's CPU.
    details = _shot_details_part(vid, cuts, 0)
    fl = flash_frames(vid)
    log("measuring the music")
    au = music(video, info, str(work), start, end)

    # A range that starts or ends inside a shot cuts that shot short; a 0.25 s
    # tail of a 3 s shot is not the edit's shortest shot. Edge shots cut by the
    # range stay in the list, marked, and out of the pacing figures.
    partial = []
    if focused and len(cuts) > 1:
        if s > 0.05:
            partial.append(1)
        if e < dur - 0.05:
            partial.append(len(cuts))
    whole = [b - a for i, (a, b) in enumerate(cuts, 1) if i not in partial]
    lens = whole or [b - a for a, b in cuts]
    pacing = {"count": len(cuts), "mean_shot_s": round(sum(lens) / len(lens), 2),
              "shortest_s": round(min(lens), 2), "longest_s": round(max(lens), 2)}
    if one_shot:
        pacing["note"] = "No cut found: the whole stretch is one shot"
    elif partial and not whole:
        pacing["note"] = "Every shot runs past the range, so these are lengths inside the range only"
    elif len(partial) == 1:
        pacing["note"] = (f"Shot {partial[0]} runs past the range, so it is left out of the "
                          "mean, shortest and longest")
    elif partial:
        pacing["note"] = (f"Shots {partial[0]} and {partial[1]} run past the range, so they are "
                          "left out of the mean, shortest and longest")
    if au.get("tempo_bpm"):
        pacing["mean_shot_beats"] = round(pacing["mean_shot_s"] * au["tempo_bpm"] / 60, 1)
    rep = {"made_with": MADE_WITH, "source": source or video, "video": info,
           "range": {"start": round(s, 3), "end": round(e, 3)} if focused else None,
           "analysed": {"file": "proxy.mp4", "width": vinfo["width"],
                        "height": vinfo["height"], "duration": round(cdur, 3)},
           "crop": crop, "shots": cuts, "partial_shots": partial, "pacing": pacing, "audio": au,
           "shot_details": details, "flashes": fl}
    if crop:
        fx, fy = info["width"] / fw, info["height"] / fh
        rep["crop_source_px"] = {"w": round(crop["w"] * fx), "h": round(crop["h"] * fy),
                                 "x": round(crop["x"] * fx), "y": round(crop["y"] * fy)}
    if details:
        n = len(details)
        rep["look_summary"] = {
            "low_key_shots": round(sum(1 for x in details if x["look"]["dark_ratio"] > 0.5) / n, 2),
            "moving_camera_shots": round(sum(1 for x in details if x["camera"]["kind"] != ["static"]) / n, 2),
            "mean_brightness": round(sum(x["look"]["brightness"] for x in details) / n, 3),
            "camera_kinds": sorted({k for x in details for k in x["camera"]["kind"]})}
    shift_times(rep, s)
    with open(work / "report.json", "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    return rep


def ts(sec: float) -> str:
    """Seconds as m:ss.ss (h:mm:ss.ss past the hour)."""
    cs = int(round(max(0.0, float(sec)) * 100))
    m, cs = divmod(cs, 6000)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{cs / 100:05.2f}" if h else f"{m}:{cs / 100:05.2f}"


def render(rep: dict, work) -> str:
    """The markdown report printed on stdout."""
    work = Path(work)
    v, au, p = rep["video"], rep["audio"], rep["pacing"]
    L = ["# watch: breakdown", "", f"- **Source:** {rep['source']}"]
    fps = f", {v['fps']:g} fps" if v.get("fps") else ""
    L.append(f"- **Length:** {ts(v['duration'])} ({v['duration']:.1f}s), {v['width']}x{v['height']}{fps}")
    if rep.get("range"):
        r = rep["range"]
        L.append(f"- **Range:** {ts(r['start'])} to {ts(r['end'])} ({r['end'] - r['start']:.1f}s). "
                 "Every time below is a time in the source.")
    if rep.get("crop"):
        c = rep.get("crop_source_px") or rep["crop"]
        L.append(f"- **Picture:** {c['w']}x{c['h']} at {c['x']},{c['y']} inside the frame "
                 f"({rep['crop']['note']}); measured inside that box only")
    shots_line = (f"- **Shots:** {p['count']}, mean {p['mean_shot_s']:.2f}s "
                  f"(shortest {p['shortest_s']:.2f}s, longest {p['longest_s']:.2f}s)")
    if p.get("mean_shot_beats"):
        shots_line += f"; {p['mean_shot_beats']} beats a shot"
    if p.get("note"):
        shots_line += f". {p['note']}"
    L.append(shots_line)
    ls = rep.get("look_summary")
    if ls:
        det = rep["shot_details"]
        n = len(det)
        moving = sum(1 for d in det if d["camera"]["kind"] != ["static"])
        low_key = sum(1 for d in det if d["look"]["dark_ratio"] > 0.5)
        kinds = ", ".join(k.replace("_", " ") for k in ls["camera_kinds"] if k != "static") or "none"
        L.append(f"- **Camera:** moving in {moving} of {n} shots; moves seen: {kinds}")
        L.append(f"- **Look:** mean brightness {ls['mean_brightness']:.2f}; "
                 f"low key (over half the frame near black) in {low_key} of {n} shots")
    L.append(f"- **Music:** {music_line(au)}")
    if au.get("peak_candidates"):
        hits = ", ".join(f"{ts(x['t'])} {x['kind']}" for x in au["peak_candidates"])
        L.append(f"- **Builds and hits:** {hits}")
    L.append("- **Flash frames:** " + (", ".join(ts(t) for t in rep["flashes"]) or "none"))
    sheet_lines = []
    if (work / "sheet_1fps.jpg").exists():
        sheet_lines.append(f"  - `{work / 'sheet_1fps.jpg'}`: up to 64 frames spread evenly "
                           "across the stretch, 8 to a row, read left to right")
    if (work / "sheet_scenes.jpg").exists():
        step = max(1, p["count"] // 48)
        which = "each shot" if step == 1 else f"every {step}th shot (up to 48 tiles)"
        rel = " from the start of the range" if rep.get("range") else ""
        sheet_lines.append(f"  - `{work / 'sheet_scenes.jpg'}`: the middle frame of {which}, "
                           f"labelled with its time in seconds{rel}")
    if sheet_lines:
        L += ["- **Contact sheets** (read these before quoting any number):"] + sheet_lines

    L += ["", "## Shots", ""]
    by_shot = {d["shot"]: d for d in rep["shot_details"]}
    tempo = au.get("tempo_bpm")
    head = "| # | start | length |" + (" beats |" if tempo else "") + \
        " camera | pace | bright | contrast | dark | colours |"
    L += [head, "|" + "---|" * (head.count("|") - 1)]
    cut_short = set(rep.get("partial_shots") or [])
    for i, (a, b) in enumerate(rep["shots"], 1):
        row = f"| {i} | {ts(a)} | {b - a:.2f}s{' (cut by the range)' if i in cut_short else ''} |"
        if tempo:
            row += f" {(b - a) * tempo / 60:.1f} |"
        d = by_shot.get(i)
        if d:
            cam, lk = d["camera"], d["look"]
            row += (f" {', '.join(k.replace('_', ' ') for k in cam['kind'])} | {cam['pace']} |"
                    f" {lk['brightness']:.2f} | {lk['contrast']:.2f} | {lk['dark_ratio']:.2f} |"
                    f" {' '.join(lk['dominant'])} |")
        else:
            row += " not read | | | | | |"
        L.append(row)

    L += ["", "## Reading it", "",
          "- Camera comes from optical flow between frames sampled inside each shot: push in and "
          "pull out are zooms or dollies, move left, right, up and down are pans, tilts or tracks, "
          "roll/orbit is rotation, static means nothing passed the threshold. It reads how the "
          "whole picture moves, so animated graphics or a subject filling the frame can read as a "
          "camera move. Handheld shake can read as a slow move, and a move faster than the "
          "sampling can be missed.",
          "- Look: brightness runs 0 to 1, contrast is the spread of brightness, dark is the share "
          "of pixels near black, colours are the four main colours, most common first.",
          "- A shot shorter than 15 frames merges into its neighbour, so very fast cutting reads "
          "as fewer, longer shots. A large graphic sweeping across the frame can read as a cut, "
          "and a slow dissolve can go unseen.",
          "- Tempo comes from a beat tracker. It can read half, double or one and a half times "
          "the felt tempo, and on music with no steady pulse (rubato, ambient, much orchestral "
          "scoring) it still returns a number, which then means nothing. Builds and hits are "
          "where loudness, attack and fullness rise together; check them on the frames.",
          "- The numbers point, the frames confirm.",
          "", "---",
          f"_Work dir: `{work}` (report.json holds every number). Delete it when done._"]
    return "\n".join(L)


def music_line(au: dict) -> str:
    if not au.get("present"):
        return "none (no sound track)"
    if au.get("error"):
        return f"could not be measured ({au['error']})"
    if au.get("silent"):
        return "silent: no usable sound in the track"
    if au.get("no_beat"):
        return "no beat found: the beat tracker found no pulse at all"
    beats = au.get("beats") or []
    span = f"between {ts(beats[0])} and {ts(beats[-1])}" if beats else ""
    if not au.get("tempo_bpm"):
        return (f"no reliable tempo: the beat tracker found only {len(beats)} beats {span}").rstrip()
    line = f"{au['tempo_bpm']:g} bpm, from {len(beats)} beats {span}"
    if au.get("phase_inverted"):
        line += "; the stereo channels cancel when summed, so one channel was measured"
    return line


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="breakdown",
        description="How a reference video is built: every shot, its camera and look, and the music.")
    ap.add_argument("source", help="Video URL or local file path")
    ap.add_argument("--start", default=None, help="Range start (SS, MM:SS or HH:MM:SS)")
    ap.add_argument("--end", default=None, help="Range end (SS, MM:SS or HH:MM:SS)")
    ap.add_argument("--out-dir", default=None, help="Working folder (default: a new temp folder)")
    a = ap.parse_args(argv)
    missing = missing_modules()
    if missing:
        raise SystemExit(f"missing Python modules: {', '.join(missing)}. Install them for this "
                         f"Python: {sys.executable} -m pip install scenedetect librosa "
                         "(PySceneDetect brings OpenCV and numpy)")
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise SystemExit("ffmpeg and ffprobe are required")
    start, end = parse_time(a.start), parse_time(a.end)
    if start is not None and start < 0:
        raise SystemExit("--start must be non-negative")
    if end is not None and end <= (start or 0.0):
        raise SystemExit("--end must be greater than --start (and than 0)")
    work = Path(a.out_dir).expanduser().resolve() if a.out_dir else Path(tempfile.mkdtemp(prefix="watch-breakdown-"))
    claim_folder(work)
    log(f"working dir: {work}")
    dl = download(a.source, work / "download")
    rep = analyse(dl["video_path"], work, start, end, source=a.source)
    print(render(rep, work))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
