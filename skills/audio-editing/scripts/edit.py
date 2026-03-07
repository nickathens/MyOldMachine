#!/usr/bin/env python3
"""
Audio editing operations using pydub.
"""

import argparse
import json
import sys
from pathlib import Path

from pydub import AudioSegment


def load_audio(path: str) -> AudioSegment:
    """Load audio file, auto-detecting format."""
    suffix = Path(path).suffix.lower().lstrip('.')
    format_map = {'mp3': 'mp3', 'wav': 'wav', 'flac': 'flac',
                  'ogg': 'ogg', 'm4a': 'mp4', 'aac': 'aac'}
    fmt = format_map.get(suffix, suffix)
    return AudioSegment.from_file(path, format=fmt)


def save_audio(audio: AudioSegment, path: str, bitrate: str = None):
    """Save audio file, inferring format from extension."""
    suffix = Path(path).suffix.lower().lstrip('.')
    params = {}
    if bitrate and suffix == 'mp3':
        params['bitrate'] = bitrate
    audio.export(path, format=suffix, **params)
    print(f"Saved: {path}")


def cmd_cut(args):
    """Cut a segment from audio."""
    audio = load_audio(args.input)
    start_ms = int(args.start * 1000)
    end_ms = int(args.end * 1000) if args.end else len(audio)

    segment = audio[start_ms:end_ms]
    save_audio(segment, args.output)
    print(f"Cut: {args.start}s to {end_ms/1000}s ({len(segment)/1000:.1f}s)")


def cmd_merge(args):
    """Merge multiple audio files."""
    files = args.files
    if len(files) < 2:
        print("Error: Need at least 2 files to merge", file=sys.stderr)
        sys.exit(1)

    result = load_audio(files[0])
    for f in files[1:]:
        next_audio = load_audio(f)
        if args.crossfade:
            result = result.append(next_audio, crossfade=args.crossfade)
        else:
            result = result + next_audio

    save_audio(result, args.output)
    print(f"Merged {len(files)} files ({len(result)/1000:.1f}s total)")


def cmd_fade(args):
    """Add fade in/out to audio."""
    audio = load_audio(args.input)

    if args.fade_in:
        audio = audio.fade_in(args.fade_in)
    if args.fade_out:
        audio = audio.fade_out(args.fade_out)

    save_audio(audio, args.output)
    print(f"Applied fade in={args.fade_in}ms, out={args.fade_out}ms")


def cmd_volume(args):
    """Adjust volume by dB."""
    audio = load_audio(args.input)
    adjusted = audio + args.db
    save_audio(adjusted, args.output)
    print(f"Adjusted volume by {args.db:+.1f} dB")


def cmd_normalize(args):
    """Normalize audio to target dBFS."""
    audio = load_audio(args.input)
    change = args.target - audio.dBFS
    normalized = audio + change
    save_audio(normalized, args.output)
    print(f"Normalized: {audio.dBFS:.1f} dBFS -> {args.target} dBFS (change: {change:+.1f} dB)")


def cmd_convert(args):
    """Convert audio format."""
    audio = load_audio(args.input)
    save_audio(audio, args.output, bitrate=args.bitrate)


def cmd_info(args):
    """Get audio file info."""
    audio = load_audio(args.input)
    info = {
        "duration_seconds": len(audio) / 1000,
        "duration_formatted": f"{len(audio)//60000}:{(len(audio)//1000)%60:02d}",
        "channels": audio.channels,
        "sample_rate": audio.frame_rate,
        "sample_width_bits": audio.sample_width * 8,
        "dBFS": round(audio.dBFS, 2),
    }
    print(json.dumps(info, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Audio editing tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Cut
    p = subparsers.add_parser("cut", help="Cut a segment")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--start", type=float, default=0, help="Start time (seconds)")
    p.add_argument("--end", type=float, help="End time (seconds)")
    p.set_defaults(func=cmd_cut)

    # Merge
    p = subparsers.add_parser("merge", help="Merge files")
    p.add_argument("files", nargs="+", help="Files to merge")
    p.add_argument("-o", "--output", required=True, help="Output file")
    p.add_argument("--crossfade", type=int, help="Crossfade duration (ms)")
    p.set_defaults(func=cmd_merge)

    # Fade
    p = subparsers.add_parser("fade", help="Add fade in/out")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--fade-in", type=int, default=0, help="Fade in (ms)")
    p.add_argument("--fade-out", type=int, default=0, help="Fade out (ms)")
    p.set_defaults(func=cmd_fade)

    # Volume
    p = subparsers.add_parser("volume", help="Adjust volume")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--db", type=float, required=True, help="Volume change in dB")
    p.set_defaults(func=cmd_volume)

    # Normalize
    p = subparsers.add_parser("normalize", help="Normalize loudness")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--target", type=float, default=-14, help="Target dBFS")
    p.set_defaults(func=cmd_normalize)

    # Convert
    p = subparsers.add_parser("convert", help="Convert format")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--bitrate", default="192k", help="Bitrate for mp3")
    p.set_defaults(func=cmd_convert)

    # Info
    p = subparsers.add_parser("info", help="Get file info")
    p.add_argument("input", help="Input file")
    p.set_defaults(func=cmd_info)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
