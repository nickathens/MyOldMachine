#!/usr/bin/env python3
"""
Render MIDI to audio using FluidSynth.

Rendered in 32-bit float, then peak-normalised to -1 dBFS and written in the
format the output name asks for. Linux bot review 2026-09-27: at the old default
gain of 1.0 (five times FluidSynth's own 0.2) six-note chords hit full scale
and clipped (31 samples), and "--output song.mp3" without "--format mp3"
wrote WAV bytes under the .mp3 name.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

# Default soundfont location (installed with fluid-soundfont-gm)
DEFAULT_SOUNDFONT = "/usr/share/sounds/sf2/FluidR3_GM.sf2"
FORMATS = ("wav", "mp3", "flac", "ogg", "m4a")


def render_midi(midi_path: str, output_path: str, soundfont: str = None, gain: float = 0.5) -> bool:
    """Render MIDI to a float WAV using FluidSynth (float cannot clip)."""
    sf = soundfont or DEFAULT_SOUNDFONT

    if not Path(sf).exists():
        print(f"Error: Soundfont not found: {sf}", file=sys.stderr)
        return False

    cmd = [
        "fluidsynth",
        "-ni",           # No interactive mode
        "-g", str(gain), # Gain
        "-O", "float",   # 32-bit float samples: nothing clips before the peak is set
        "-T", "wav",
        "-r", "44100",
        "-F", output_path,  # Output file
        sf,              # Soundfont
        midi_path        # Input MIDI
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0 or not Path(output_path).exists():
        print(f"Error: {result.stderr.strip() or 'FluidSynth wrote no audio'}", file=sys.stderr)
        return False

    return True


def finish(float_wav: str, output_path: str, peak_db: float = -1.0, bitrate: str = "192k",
           normalize: bool = True) -> str:
    """Normalise the float render and write it in the output's format with ffmpeg."""
    import soundfile

    audio, _ = soundfile.read(float_wav, dtype="float32")
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak == 0.0:
        raise SystemExit("Error: the render is silent (no notes, or the soundfont has no matching instruments)")
    scale = 10 ** (peak_db / 20) / peak if normalize else min(1.0, 1.0 / peak)
    ext = Path(output_path).suffix.lower().lstrip(".")
    codec = {"wav": ["-c:a", "pcm_s24le"], "flac": ["-c:a", "flac", "-sample_fmt", "s32"],
             "mp3": ["-c:a", "libmp3lame", "-b:a", bitrate], "ogg": ["-c:a", "libvorbis", "-b:a", bitrate],
             "m4a": ["-c:a", "aac", "-b:a", bitrate]}[ext]
    result = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", float_wav, "-af", f"volume={scale:.8f}",
                             *codec, output_path], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"Error: ffmpeg could not write {output_path}: {result.stderr.strip()[-300:]}")
    return f"peak {20 * np.log10(peak * scale):.1f} dBFS" + ("" if normalize else " (not normalised)")


def main():
    parser = argparse.ArgumentParser(description="Render MIDI to audio")
    parser.add_argument("input", help="Input MIDI file")
    parser.add_argument("--output", "-o", help="Output file path; its extension sets the format")
    parser.add_argument("--format", "-f", choices=FORMATS, help="Output format when --output is not given (default: wav)")
    parser.add_argument("--soundfont", "-sf", help="Custom soundfont path")
    parser.add_argument("--gain", "-g", type=float, default=0.5, help="FluidSynth gain before normalising (default: 0.5)")
    parser.add_argument("--peak", type=float, default=-1.0, help="Peak level in dBFS after normalising (default: -1)")
    parser.add_argument("--no-normalize", action="store_true", help="Keep FluidSynth's level (only pulled down if it would clip)")
    parser.add_argument("--bitrate", "-b", default="192k", help="Bitrate for mp3/ogg/m4a (default: 192k)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    # The format follows the output name; --format only names a default output
    if args.output:
        output_path = args.output
        ext = Path(output_path).suffix.lower().lstrip(".")
        if ext not in FORMATS:
            sys.exit(f"Error: {output_path}: use one of .{', .'.join(FORMATS)}")
        if args.format and args.format != ext:
            sys.exit(f"Error: --format {args.format} but the output is .{ext}")
    else:
        output_path = str(input_path.with_suffix(f".{args.format or 'wav'}"))
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"Rendering {input_path.name}...")
    with tempfile.TemporaryDirectory(prefix="midi_render_") as tmp:
        float_wav = str(Path(tmp) / "render.wav")
        if not render_midi(args.input, float_wav, args.soundfont, args.gain):
            sys.exit(1)
        level = finish(float_wav, output_path, args.peak, args.bitrate, not args.no_normalize)

    print(f"Saved: {output_path} ({level})")


if __name__ == "__main__":
    main()
