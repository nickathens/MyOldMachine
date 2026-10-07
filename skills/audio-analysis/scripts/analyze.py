#!/usr/bin/env python3
"""
Audio Analysis - BPM, key, waveform, spectrum analysis.

Usage:
    python analyze.py input.mp3              # Full analysis
    python analyze.py input.mp3 --bpm        # Just BPM
    python analyze.py input.mp3 --key        # Just key detection
    python analyze.py input.mp3 --waveform   # Generate waveform image
    python analyze.py input.mp3 --spectrum   # Generate spectrum image
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Each feature computes its own full-length STFT or CQT, so memory grows with
# the length: a 34 minute album peaked past 6 GB for the plain analysis
# (OOM-killed in a capped scope, 2026-10-07) and at 3 GB for a spectrogram.
# On Linux the run is a child of the bot's service, and when the kernel
# OOM-kills a process in a unit, systemd's default OOMPolicy=stop takes the
# whole unit down. So the script re-runs itself in its own memory-capped user
# scope (the voice skill's pattern); on Linux where no scope can be made, only
# files up to ten minutes run whole, and --start/--duration analyse a section
# of a longer one (Linux bot sweep 2026-10-07). macOS has no scope and no such
# policy; it runs as before.
MEM_MAX = os.environ.get("AUDIO_ANALYSIS_MEM_MAX", "6G")
UNPROTECTED_MAX_SECONDS = 10 * 60


KEY_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
# Krumhansl-Kessler probe-tone profiles (Krumhansl 1990), index 0 = tonic.
MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def estimate_key(chroma_mean) -> tuple[str, float]:
    """Krumhansl-Schmuckler: correlate the mean chroma with all 24 rotated key
    profiles and keep the best. Returns ("A" or "Am" style name, correlation).

    It replaced "strongest pitch class, then its relative minor if that is
    within 10 percent": a minor piece whose tonic dominated came out major
    (A minor read "A"), because only the relative minor of the loudest pitch
    class was ever considered (Linux bot review 2026-09-27).
    """
    import numpy as np
    chroma = np.asarray(chroma_mean, dtype=float)
    best = ("C", -2.0)
    for tonic in range(12):
        for profile, suffix in ((MAJOR_PROFILE, ""), (MINOR_PROFILE, "m")):
            r = float(np.corrcoef(chroma, np.roll(profile, tonic))[0, 1])
            if np.isfinite(r) and r > best[1]:
                best = (KEY_NAMES[tonic] + suffix, r)
    return best


def _scope_prefix():
    """systemd-run argv for a memory-capped user scope, or None when none can be made here."""
    systemd_run = shutil.which("systemd-run")
    if not systemd_run:
        return None
    prefix = [systemd_run, "--user", "--scope", "--quiet", "--collect",
              "-p", f"MemoryMax={MEM_MAX}", "-p", "MemorySwapMax=0", "--"]
    try:
        probe = subprocess.run(prefix + ["true"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return prefix if probe.returncode == 0 else None


def _isolate(argv):
    """Run this script again inside the capped scope and return its exit code;
    None when already inside one, or when no scope can be made here."""
    if os.environ.get("AUDIO_ANALYSIS_ISOLATED") == "1":
        return None
    prefix = _scope_prefix()
    if prefix is None:
        return None
    env = dict(os.environ, AUDIO_ANALYSIS_ISOLATED="1")
    proc = subprocess.run(prefix + [sys.executable, os.path.abspath(__file__), *argv], env=env)
    if proc.returncode < 0:
        print(f"Error: the analysis was killed by signal {-proc.returncode}, most likely by the "
              f"{MEM_MAX} memory cap: analyse a section with --start and --duration", file=sys.stderr)
        return 1
    return proc.returncode


def _length(path):
    try:
        import librosa
        return float(librosa.get_duration(path=str(path)))
    except Exception:
        return None


def analyze_audio(input_path: str, output_dir: str = None, offset: float = 0.0,
                  duration: float = None) -> dict:
    """Perform full audio analysis."""
    try:
        import librosa
        import numpy as np
    except ImportError:
        return {"error": "librosa not installed. Run: pip install librosa"}

    input_path = Path(input_path)
    if not input_path.exists():
        return {"error": f"File not found: {input_path}"}

    if output_dir:
        output_dir = Path(output_dir)
    else:
        output_dir = input_path.parent

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Load audio (a section when offset/duration are given)
        y, sr = librosa.load(str(input_path), sr=None, offset=offset, duration=duration)
        duration = librosa.get_duration(y=y, sr=sr)

        # BPM detection
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(tempo) if isinstance(tempo, (int, float, np.floating)) else float(tempo[0])

        # Key detection: chroma profile against the 24 key profiles
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        estimated_key, key_confidence = estimate_key(np.mean(chroma, axis=1))

        # Loudness (RMS)
        rms = librosa.feature.rms(y=y)
        avg_loudness = float(np.mean(rms))

        # Spectral centroid (brightness)
        spectral_centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
        avg_brightness = float(np.mean(spectral_centroid))

        return {
            "success": True,
            "file": str(input_path),
            "duration_seconds": round(duration, 2),
            "duration_formatted": f"{int(duration // 60)}:{int(duration % 60):02d}",
            "sample_rate": sr,
            "bpm": round(bpm, 1),
            "key": estimated_key,
            "key_confidence": round(key_confidence, 3),
            "avg_loudness_rms": round(avg_loudness, 4),
            "brightness_hz": round(avg_brightness, 1),
        }

    except Exception as e:
        return {"error": str(e)}


def _image_path(input_path: Path, output: str | None, kind: str) -> Path:
    """<input stem>.<kind>.png, beside the input or inside the --output folder.

    --output is documented as a folder, but it was used as the file name, so
    asking for both images wrote the spectrogram over the waveform, and an
    existing folder made savefig fail.
    """
    folder = Path(output) if output else input_path.parent
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{input_path.stem}.{kind}.png"


def generate_waveform(input_path: str, output_path: str = None, offset: float = 0.0,
                      duration: float = None) -> dict:
    """Generate waveform visualization."""
    try:
        import librosa
        import librosa.display
        import matplotlib.pyplot as plt
    except ImportError:
        return {"error": "Required packages not installed"}

    input_path = Path(input_path)
    if not input_path.exists():
        return {"error": f"File not found: {input_path}"}

    output_path = _image_path(input_path, output_path, "waveform")

    try:
        y, sr = librosa.load(str(input_path), sr=None, offset=offset, duration=duration)

        plt.figure(figsize=(14, 4))
        plt.subplot(1, 1, 1)
        librosa.display.waveshow(y, sr=sr, alpha=0.8)
        plt.title(f'Waveform: {input_path.name}')
        plt.xlabel('Time (s)')
        plt.ylabel('Amplitude')
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()

        return {"success": True, "output": str(output_path)}
    except Exception as e:
        return {"error": str(e)}


def generate_spectrum(input_path: str, output_path: str = None, offset: float = 0.0,
                      duration: float = None) -> dict:
    """Generate spectrogram visualization."""
    try:
        import librosa
        import librosa.display
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return {"error": "Required packages not installed"}

    input_path = Path(input_path)
    if not input_path.exists():
        return {"error": f"File not found: {input_path}"}

    output_path = _image_path(input_path, output_path, "spectrum")

    try:
        y, sr = librosa.load(str(input_path), sr=None, offset=offset, duration=duration)

        plt.figure(figsize=(14, 6))

        # Mel spectrogram
        S = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=128)
        S_dB = librosa.power_to_db(S, ref=np.max)

        librosa.display.specshow(S_dB, x_axis='time', y_axis='mel', sr=sr, fmax=8000)
        plt.colorbar(format='%+2.0f dB')
        plt.title(f'Mel Spectrogram: {input_path.name}')
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()

        return {"success": True, "output": str(output_path)}
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Analyze audio files")
    parser.add_argument("input", help="Input audio file")
    parser.add_argument("--bpm", action="store_true", help="Show only BPM")
    parser.add_argument("--key", action="store_true", help="Show only key")
    parser.add_argument("--waveform", action="store_true", help="Generate waveform image")
    parser.add_argument("--spectrum", action="store_true", help="Generate spectrogram image")
    parser.add_argument("--output", "-o", help="Output directory for images")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--start", type=float, default=0.0, help="Analyse from this second")
    parser.add_argument("--duration", type=float, default=None, help="Analyse this many seconds")
    args = parser.parse_args()

    rc = _isolate(sys.argv[1:])
    if rc is not None:
        return rc
    if (sys.platform.startswith("linux") and args.duration is None
            and os.environ.get("AUDIO_ANALYSIS_ISOLATED") != "1"):
        length = _length(args.input)
        if length is not None and length - args.start > UNPROTECTED_MAX_SECONDS:
            print(f"Error: no memory-capped scope can be made here, and {length / 60:.0f} minutes is too "
                  f"long to analyse unprotected; give --duration (at most {UNPROTECTED_MAX_SECONDS}) "
                  "and --start for a section", file=sys.stderr)
            return 1

    section = {"offset": args.start, "duration": args.duration}
    results = {}

    # Generate visualizations if requested
    if args.waveform:
        result = generate_waveform(args.input, args.output, **section)
        if "error" in result:
            print(f"Waveform error: {result['error']}")
        else:
            print(f"Waveform saved: {result['output']}")
            results["waveform"] = result["output"]

    if args.spectrum:
        result = generate_spectrum(args.input, args.output, **section)
        if "error" in result:
            print(f"Spectrum error: {result['error']}")
        else:
            print(f"Spectrum saved: {result['output']}")
            results["spectrum"] = result["output"]

    # Run analysis
    if not (args.waveform or args.spectrum) or args.bpm or args.key:
        analysis = analyze_audio(args.input, **section)

        if "error" in analysis:
            print(f"Error: {analysis['error']}")
            return 1

        if args.json:
            print(json.dumps(analysis, indent=2, ensure_ascii=False))
        elif args.bpm:
            print(f"BPM: {analysis['bpm']}")
        elif args.key:
            print(f"Key: {analysis['key']}")
        else:
            print(f"File: {analysis['file']}")
            print(f"Duration: {analysis['duration_formatted']} ({analysis['duration_seconds']}s)")
            print(f"Sample Rate: {analysis['sample_rate']} Hz")
            print(f"BPM: {analysis['bpm']}")
            print(f"Key: {analysis['key']}")
            print(f"Brightness: {analysis['brightness_hz']} Hz")

        results.update(analysis)

    return 0


if __name__ == "__main__":
    sys.exit(main())
