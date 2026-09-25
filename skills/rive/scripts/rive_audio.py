#!/usr/bin/env python3
"""Turn a piece of music into per-frame curves that drive a Rive scene.

Rive can play sound but cannot listen to it: a script gets an AudioSource to
play and nothing to analyse, and a headless capture is silent anyway. So a
music visual is built the other way round: measure the track here, one value
per video frame, and feed the values in as data (rive_render.py
--data-curve), then lay the track under the finished video.

Curves (each 0..1, normalised to the track's 99th percentile so one spike
does not flatten the rest):

  level          overall loudness (RMS), with attack/release smoothing
  peak           the loudest sample in each frame
  low, mid, high three bands (60-250 Hz, 250-2000 Hz, 2000-8000 Hz), or
                 your own with --bands
  onset          rises in level: kicks, hits, entries (half-wave rectified
                 level difference, normalised)

Standard library plus ffmpeg only, so it runs the same on the Linux side.

  rive_audio.py track.wav -o curves.json --fps 25
  rive_audio.py track.mp3 -o curves.json --fps 30 --start 12 --duration 20 --bands 40-120,120-500,500-4000,4000-12000
  rive_audio.py track.wav -o curves.json --fps 25 --log-bands 12      (b1..b12 for a 12-bar spectrum)
  rive_render.py viz -o viz.mp4 --fps 25 --duration 20 --data-curve level=curves.json:level@0:100 \\
      --data-curve bass=curves.json:low --audio track.wav --audio-offset 12
"""

from __future__ import annotations

import argparse
import array
import json
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rivelib as L  # noqa: E402

RATE = 48000
DEFAULT_BANDS = [("low", 60, 250), ("mid", 250, 2000), ("high", 2000, 8000)]


def decode(path: str, start: float, duration: float | None, filters: str | None = None) -> array.array:
    cmd = [L.ffmpeg_bin(), "-v", "error", "-nostdin"]
    if start:
        cmd += ["-ss", f"{start:.6f}"]
    cmd += ["-i", str(path)]
    if duration:
        cmd += ["-t", f"{duration:.6f}"]
    if filters:
        cmd += ["-af", filters]
    cmd += ["-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise L.RiveError(f"ffmpeg could not decode {path}: {result.stderr.decode(errors='replace')[-300:]}")
    samples = array.array("f")
    samples.frombytes(result.stdout[: len(result.stdout) // 4 * 4])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def per_frame(samples: array.array, fps: float, frames: int) -> tuple[list[float], list[float]]:
    """RMS and peak for each video frame's slice of samples."""
    rms, peak = [], []
    per = RATE / fps
    for k in range(frames):
        a, b = int(round(k * per)), int(round((k + 1) * per))
        chunk = samples[a:b]
        if not chunk:
            rms.append(0.0)
            peak.append(0.0)
            continue
        rms.append(math.sqrt(sum(x * x for x in chunk) / len(chunk)))
        peak.append(max(abs(min(chunk)), abs(max(chunk))))
    return rms, peak


def smooth(values: list[float], fps: float, attack: float, release: float) -> list[float]:
    """One-pole envelope follower: rises in `attack` seconds, falls in `release`."""
    if not values:
        return []
    up = 1.0 - math.exp(-1.0 / max(attack * fps, 1e-6))
    down = 1.0 - math.exp(-1.0 / max(release * fps, 1e-6))
    out, env = [], values[0]
    for v in values:
        env += (v - env) * (up if v > env else down)
        out.append(env)
    return out


def normalise(values: list[float], percentile: float = 0.99) -> list[float]:
    if not values:
        return []
    ranked = sorted(values)
    top = ranked[min(len(ranked) - 1, int(percentile * (len(ranked) - 1)))] or max(ranked) or 1.0
    return [round(min(1.0, v / top), 5) for v in values]


def onsets(level: list[float]) -> list[float]:
    diff = [0.0] + [max(0.0, b - a) for a, b in zip(level, level[1:])]
    return normalise(diff, 0.995)


def log_bands(count: int, lo: float = 40.0, hi: float = 12000.0) -> list[tuple[str, float, float]]:
    """count bands spaced evenly in pitch, named b1..bN (a spectrum for N bars)."""
    if count < 1:
        raise L.RiveError("--log-bands needs at least one band")
    edges = [lo * (hi / lo) ** (i / count) for i in range(count + 1)]
    return [(f"b{i + 1}", round(edges[i], 1), round(edges[i + 1], 1)) for i in range(count)]


def parse_bands(text: str | None) -> list[tuple[str, float, float]]:
    if not text:
        return DEFAULT_BANDS
    bands = []
    for i, part in enumerate(text.split(",")):
        lo, hi = (float(v) for v in part.split("-", 1))
        if not 0 < lo < hi:
            raise L.RiveError(f"band {part!r} must be LOW-HIGH in Hz")
        bands.append((f"band{i + 1}", lo, hi))
    return bands


def analyse(path: str, fps: float, start: float = 0.0, duration: float | None = None,
            bands: list | None = None, attack: float = 0.01, release: float = 0.25) -> dict:
    full = decode(path, start, duration)
    if not full:
        raise L.RiveError(f"{path} decoded to no samples")
    seconds = len(full) / RATE
    frames = int(math.floor(seconds * fps + 1e-9))
    rms, peak = per_frame(full, fps, frames)
    level = smooth(rms, fps, attack, release)
    curves = {"level": normalise(level), "peak": normalise(peak), "onset": onsets(rms)}
    for name, lo, hi in bands or DEFAULT_BANDS:
        filtered = decode(path, start, duration, f"highpass=f={lo}:poles=2,lowpass=f={hi}:poles=2")
        band_rms, _ = per_frame(filtered, fps, frames)
        curves[name] = normalise(smooth(band_rms, fps, attack, release))
    return {"fps": fps, "frames": frames, "seconds": round(seconds, 4), "source": str(Path(path).resolve()),
            "start": start, "interpolation": "linear", "curves": curves,
            "bands": {name: [lo, hi] for name, lo, hi in (bands or DEFAULT_BANDS)},
            "note": "values are 0..1 per video frame; map with @LO:HI in rive_render.py --data-curve"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--fps", type=float, default=30.0, help="the video frame rate the curves are for")
    ap.add_argument("--start", type=float, default=0.0, help="seconds into the track")
    ap.add_argument("--duration", type=float, help="seconds to analyse (default: to the end)")
    ap.add_argument("--bands", help="comma list of LOW-HIGH Hz bands (default 60-250,250-2000,2000-8000)")
    ap.add_argument("--log-bands", type=int, help="N pitch-spaced bands b1..bN from 40 Hz to 12 kHz, "
                                                  "added to the default low/mid/high (a spectrum for N bars)")
    ap.add_argument("--attack", type=float, default=0.01, help="seconds for the envelope to rise")
    ap.add_argument("--release", type=float, default=0.25, help="seconds for the envelope to fall")
    args = ap.parse_args(argv)
    try:
        bands = parse_bands(args.bands)
        if args.log_bands:
            bands = bands + log_bands(args.log_bands)
        doc = analyse(args.audio, args.fps, args.start, args.duration, bands, args.attack, args.release)
    except L.RiveError as exc:
        print(f"rive_audio: {exc}", file=sys.stderr)
        return 1
    Path(args.output).write_text(json.dumps(doc) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: {doc['frames']} frames at {doc['fps']:g} fps, curves "
          + ", ".join(sorted(doc["curves"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
