#!/usr/bin/env python3
"""
Render MIDI to audio using FluidSynth.
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Default soundfont location (installed with fluid-soundfont-gm)
DEFAULT_SOUNDFONT = "/usr/share/sounds/sf2/FluidR3_GM.sf2"


def render_midi(midi_path: str, output_path: str, soundfont: str = None, gain: float = 1.0) -> bool:
    """Render MIDI to WAV using FluidSynth."""
    sf = soundfont or DEFAULT_SOUNDFONT

    if not Path(sf).exists():
        print(f"Error: Soundfont not found: {sf}", file=sys.stderr)
        return False

    cmd = [
        "fluidsynth",
        "-ni",           # No interactive mode
        "-g", str(gain), # Gain
        "-F", output_path,  # Output file
        sf,              # Soundfont
        midi_path        # Input MIDI
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error: {result.stderr}", file=sys.stderr)
        return False

    return True


def convert_to_mp3(wav_path: str, mp3_path: str, bitrate: str = "192k") -> bool:
    """Convert WAV to MP3 using ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-i", wav_path,
        "-b:a", bitrate,
        mp3_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Render MIDI to audio")
    parser.add_argument("input", help="Input MIDI file")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--format", "-f", choices=["wav", "mp3"], default="wav", help="Output format")
    parser.add_argument("--soundfont", "-sf", help="Custom soundfont path")
    parser.add_argument("--gain", "-g", type=float, default=1.0, help="Volume gain (default: 1.0)")
    parser.add_argument("--bitrate", "-b", default="192k", help="MP3 bitrate (default: 192k)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        output_path = str(input_path.with_suffix(f".{args.format}"))

    # Render to WAV first
    if args.format == "mp3":
        wav_path = str(input_path.with_suffix(".tmp.wav"))
    else:
        wav_path = output_path

    print(f"Rendering {input_path.name}...")
    if not render_midi(args.input, wav_path, args.soundfont, args.gain):
        sys.exit(1)

    # Convert to MP3 if needed
    if args.format == "mp3":
        print("Converting to MP3...")
        if not convert_to_mp3(wav_path, output_path, args.bitrate):
            print("Error: MP3 conversion failed", file=sys.stderr)
            sys.exit(1)
        # Clean up temp WAV
        Path(wav_path).unlink()

    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
