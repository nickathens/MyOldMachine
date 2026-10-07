#!/usr/bin/env python3
"""
Audio editing operations using pydub, with loudness work done by ffmpeg.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from pydub import AudioSegment

# Lossy exports used ffmpeg's default (128k MP3): a cut of a 320k MP3 came
# back 128k (Linux bot review 2026-09-27)
DEFAULT_BITRATE = {'mp3': '320k', 'm4a': '256k', 'aac': '256k', 'mp4': '256k',
                   'ogg': '256k', 'opus': '192k'}


def _float_pcm(path) -> bool:
    """True for a WAV that holds float samples (pcm_f32le, a usual DAW export)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                        "stream=codec_name", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return r.stdout.strip().startswith("pcm_f")


def load_audio(path: str) -> AudioSegment:
    """Load audio file, auto-detecting format."""
    suffix = Path(path).suffix.lower().lstrip('.')
    format_map = {'mp3': 'mp3', 'wav': 'wav', 'flac': 'flac',
                  'ogg': 'ogg', 'm4a': 'mp4', 'aac': 'aac'}
    fmt = format_map.get(suffix, suffix)
    if fmt == 'wav' and _float_pcm(path):
        # pydub reads a WAV's samples as integers whatever its format tag
        # says: a float WAV came back as distortion (a -24 dBFS sine at 8x
        # the RMS). Decode to 32 bit integer PCM first; values past full
        # scale, which only float can hold, are clipped there.
        with tempfile.TemporaryDirectory() as tmp:
            pcm = os.path.join(tmp, "pcm.wav")
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-map", "0:a:0",
                                "-c:a", "pcm_s32le", pcm], capture_output=True, text=True)
            if r.returncode != 0:
                fail(f"cannot decode {path}: {r.stderr.strip()[-300:]}")
            return AudioSegment.from_file(pcm, format="wav")
    return AudioSegment.from_file(path, format=fmt)


def save_audio(audio: AudioSegment, path: str, bitrate: str = None):
    """Save audio file, inferring format from extension."""
    suffix = Path(path).suffix.lower().lstrip('.')
    # Extension to (ffmpeg muxer, codec). "m4a" and "aac" are not muxer names
    # and failed every export (audit F23, 2026-09-06).
    export_map = {'m4a': ('ipod', 'aac'), 'aac': ('adts', 'aac'),
                  'mp4': ('mp4', 'aac'), 'ogg': ('ogg', 'libvorbis'),
                  'opus': ('opus', 'libopus')}
    fmt, codec = export_map.get(suffix, (suffix, None))
    params = {}
    if codec:
        params['codec'] = codec
    if suffix in DEFAULT_BITRATE:
        params['bitrate'] = bitrate or DEFAULT_BITRATE[suffix]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    audio.export(path, format=fmt, **params)
    print(f"Saved: {path}")


def fail(message):
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def ffprobe_stream(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-print_format", "json",
                          "-show_streams", "-show_format", str(path)], capture_output=True, text=True)
    if out.returncode != 0:
        fail(f"cannot read {path}: {out.stderr.strip()[-300:]}")
    data = json.loads(out.stdout)
    if not data.get("streams"):
        fail(f"{path} has no audio stream")
    return data["streams"][0], data.get("format", {})


def measure_loudness(path, extra_filter=""):
    """EBU R128 figures from ffmpeg's loudnorm analysis pass."""
    af = (extra_filter + "," if extra_filter else "") + "loudnorm=print_format=json"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-af", af, "-f", "null", "-"],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
    except ValueError:
        fail(f"loudness analysis failed: {r.stderr.strip()[-300:]}")


def ffmpeg_codec_args(output, stream):
    """Codec for the output's extension; PCM keeps more than 16 bits when the source had them."""
    ext = Path(output).suffix.lower().lstrip('.')
    deep = int(stream.get("bits_per_raw_sample") or stream.get("bits_per_sample") or 16) > 16 \
        or stream.get("sample_fmt", "").startswith(("s32", "flt", "dbl"))
    if ext == "wav":
        return ["-c:a", "pcm_s24le" if deep else "pcm_s16le"]
    if ext == "flac":
        return ["-c:a", "flac", "-sample_fmt", "s32" if deep else "s16"]
    if ext == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", DEFAULT_BITRATE["mp3"]]
    if ext in ("m4a", "mp4"):
        return ["-c:a", "aac", "-b:a", DEFAULT_BITRATE["m4a"]]
    if ext == "aac":
        return ["-c:a", "aac", "-b:a", DEFAULT_BITRATE["aac"], "-f", "adts"]
    if ext == "ogg":
        return ["-c:a", "libvorbis", "-b:a", DEFAULT_BITRATE["ogg"]]
    if ext == "opus":
        return ["-c:a", "libopus", "-b:a", DEFAULT_BITRATE["opus"]]
    fail(f"unsupported output format .{ext}")


def cmd_cut(args):
    """Cut a segment from audio."""
    audio = load_audio(args.input)
    start_ms = int(args.start * 1000)
    end_ms = int(args.end * 1000) if args.end is not None else len(audio)
    if not 0 <= start_ms < min(end_ms, len(audio)):
        fail(f"--start {args.start} and --end {args.end} do not make a span inside {len(audio) / 1000:.2f} s")

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
    """Adjust volume by dB, saying so when the gain would clip."""
    audio = load_audio(args.input)
    peak_after = audio.max_dBFS + args.db
    if peak_after > 0:
        print(f"warning: the peak goes to {peak_after:+.1f} dBFS, so {peak_after:.1f} dB of it clips. "
              f"For a louder file without clipping use: normalize --target LUFS", file=sys.stderr)
    adjusted = audio + args.db
    save_audio(adjusted, args.output)
    print(f"Adjusted volume by {args.db:+.1f} dB")


def cmd_normalize(args):
    """Normalize loudness to --target LUFS (EBU R128, two pass), peaks kept under --true-peak.

    It used to add (target - RMS dBFS) to the samples: the result was not
    LUFS (a "-14" landed at -11.55 LUFS) and on material with transients the
    gain pushed peaks over full scale (920 clipped samples on a drum loop).
    """
    stream, _ = ffprobe_stream(args.input)
    first = measure_loudness(args.input)
    if first["input_i"] in ("-inf", "inf") or float(first["input_i"]) < -70:
        fail("the file is silent; there is no loudness to normalize")
    gain = args.target - float(first["input_i"])
    if float(first["input_tp"]) + gain <= args.true_peak:
        # the peaks allow a plain gain: exact, and nothing else changes
        af, limited = f"volume={gain:.2f}dB", False
    else:
        # the peaks would pass the ceiling, so loudnorm limits them. Its LRA
        # target follows the source: at the default 11 it would also compress
        # the dynamics of wide range material (film, classical)
        lra = min(50.0, max(11.0, float(first["input_lra"]) + 0.1))
        af = (f"loudnorm=I={args.target}:TP={args.true_peak}:LRA={lra}:"
              f"measured_I={first['input_i']}:measured_TP={first['input_tp']}:measured_LRA={first['input_lra']}:"
              f"measured_thresh={first['input_thresh']}:offset={first['target_offset']}:linear=true")
        limited = True
    rate = stream.get("sample_rate") or "48000"
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    # loudnorm works at 192 kHz inside and hands that rate on unless told
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(args.input), "-map", "0:a:0",
                        "-af", af, "-ar", rate, *ffmpeg_codec_args(args.output, stream), str(args.output)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not Path(args.output).exists():
        fail(f"ffmpeg failed: {r.stderr.strip()[-500:]}")
    after = measure_loudness(args.output)
    print(f"Saved: {args.output}")
    print(f"Normalized: {float(first['input_i']):.1f} LUFS -> {float(after['input_i']):.1f} LUFS "
          f"(target {args.target}), true peak {float(after['input_tp']):.1f} dBTP"
          + (f"; the peaks were limited to stay under {args.true_peak} dBTP" if limited else f"; plain gain {gain:+.1f} dB"))
    short = args.target - float(after["input_i"])
    if short > 1:
        print(f"note: {short:.1f} LU short of the target; getting there would take heavier limiting than "
              f"loudnorm allows at {args.true_peak} dBTP (raise --true-peak, or accept the level)")


def cmd_convert(args):
    """Convert audio format."""
    audio = load_audio(args.input)
    save_audio(audio, args.output, bitrate=args.bitrate)


def cmd_info(args):
    """Get audio file info, with peak and EBU R128 loudness."""
    audio = load_audio(args.input)
    stream, fmt = ffprobe_stream(args.input)
    loud = measure_loudness(args.input)

    def number(value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return round(v, 2) if math.isfinite(v) else None

    info = {
        "duration_seconds": len(audio) / 1000,
        "duration_formatted": f"{len(audio)//60000}:{(len(audio)//1000)%60:02d}",
        "codec": stream.get("codec_name"),
        "bit_rate": int(stream.get("bit_rate") or fmt.get("bit_rate") or 0) or None,
        "channels": audio.channels,
        "sample_rate": audio.frame_rate,
        "sample_width_bits": audio.sample_width * 8,
        "dBFS": round(audio.dBFS, 2),
        "peak_dBFS": round(audio.max_dBFS, 2),
        "loudness_LUFS": number(loud.get("input_i")),
        "true_peak_dBTP": number(loud.get("input_tp")),
        "loudness_range_LU": number(loud.get("input_lra")),
    }
    print(json.dumps(info, indent=2, ensure_ascii=False))


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
    p = subparsers.add_parser("normalize", help="Normalize loudness (EBU R128)")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--target", type=float, default=-14, help="Integrated loudness in LUFS (default -14)")
    p.add_argument("--true-peak", type=float, default=-1.0, help="True peak ceiling in dBTP (default -1)")
    p.set_defaults(func=cmd_normalize)

    # Convert
    p = subparsers.add_parser("convert", help="Convert format")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--bitrate", help="Bitrate for lossy formats (default 320k mp3, 256k aac/ogg, 192k opus)")
    p.set_defaults(func=cmd_convert)

    # Info
    p = subparsers.add_parser("info", help="Get file info")
    p.add_argument("input", help="Input file")
    p.set_defaults(func=cmd_info)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
