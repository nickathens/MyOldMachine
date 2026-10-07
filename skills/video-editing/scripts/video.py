#!/usr/bin/env python3
"""
Video editing with ffmpeg directly.

Rewritten in the Linux bot's 2026-09-27 full review. The moviepy version sent every
frame through an RGB pipe and back: a plain cut measured 25.95 dB against its
source with luma 1.36 levels dark, where ffmpeg re-encoding the same span at
the same quality measures 46.0 dB with no shift. It also dropped the colour
tags and wrote MP3 audio into MP4. Here the picture never leaves YUV, a
re-encode is one x264 pass at CRF 18 carrying the source's colour tags and
bit depth, audio edits copy the picture untouched, and a failed ffmpeg run is
an error, not a message.
"""

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CRF = 18

# Codecs each container takes without re-encoding
VIDEO_COPY = {
    '.mp4': {'h264', 'hevc', 'av1', 'mpeg4'}, '.m4v': {'h264', 'hevc', 'mpeg4'},
    '.mov': {'h264', 'hevc', 'prores', 'mpeg4', 'mjpeg', 'dnxhd'},
    '.webm': {'vp8', 'vp9', 'av1'}, '.avi': {'h264', 'mpeg4', 'mjpeg'},
}
AUDIO_COPY = {
    '.mp4': {'aac', 'mp3', 'ac3', 'eac3', 'alac', 'opus'}, '.m4v': {'aac', 'mp3', 'ac3'},
    '.mov': {'aac', 'mp3', 'ac3', 'eac3', 'alac', 'pcm_s16le', 'pcm_s24le'},
    '.webm': {'opus', 'vorbis'}, '.avi': {'mp3', 'ac3', 'pcm_s16le'},
}
AUDIO_ENCODE = {
    '.mp3': ['-c:a', 'libmp3lame', '-q:a', '2'], '.m4a': ['-c:a', 'aac', '-b:a', '256k'],
    '.aac': ['-c:a', 'aac', '-b:a', '256k'], '.wav': ['-c:a', 'pcm_s16le'],
    '.flac': ['-c:a', 'flac'], '.ogg': ['-c:a', 'libvorbis', '-q:a', '6'],
    '.opus': ['-c:a', 'libopus', '-b:a', '160k'],
}
POSITIONS = {
    'top': ('(w-text_w)/2', 'M'),
    'bottom': ('(w-text_w)/2', 'h-text_h-M'),
    'center': ('(w-text_w)/2', '(h-text_h)/2'),
    'top-left': ('M', 'M'),
    'top-right': ('w-text_w-M', 'M'),
    'bottom-left': ('M', 'h-text_h-M'),
    'bottom-right': ('w-text_w-M', 'h-text_h-M'),
}


def fail(message):
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def probe(path):
    """Streams and duration of a media file, through ffprobe."""
    if not Path(path).is_file():
        fail(f"no such file: {path}")
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                              "-show_format", str(path)], capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        fail("ffprobe is not installed")
    if out.returncode != 0:
        fail(f"cannot read {path}: {out.stderr.strip()[-300:]}")
    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or (video or {}).get("duration") or 0)
    return {"video": video, "audio": audio, "duration": duration, "format": data.get("format", {})}


def frame_rate(video):
    """The stream's frame rate as ffmpeg writes it (30000/1001 stays exact)."""
    for key in ("avg_frame_rate", "r_frame_rate"):
        rate = (video or {}).get(key, "0/0")
        num, _, den = rate.partition("/")
        if num not in ("", "0") and den not in ("", "0"):
            return rate
    return "30"


def rotation(video):
    for side in (video or {}).get("side_data_list", []):
        if "rotation" in side:
            return int(side["rotation"])
    return int((video or {}).get("tags", {}).get("rotate", 0) or 0)


def colour_args(video):
    """The source's colour tags, so the output still says what its pixels mean."""
    out = []
    for key, option in (("color_primaries", "-color_primaries"), ("color_transfer", "-color_trc"),
                        ("color_space", "-colorspace"), ("color_range", "-color_range")):
        value = (video or {}).get(key)
        if value and value not in ("unknown", "reserved", "unspecified"):
            out += [option, value]
    return out


def video_encode(output, video):
    ext = Path(output).suffix.lower()
    if ext == ".webm":
        return ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-row-mt", "1"] + colour_args(video)
    # Bit depth from the pixel format's suffix (yuv420p10le, p010le, gray12be).
    # A bare substring test also matched nv12, an 8-bit format, and wrote
    # 10-bit H.264 that browsers, Telegram and iPhones cannot play (audit
    # pass 1 of the 2026-09-27 review).
    deep = bool(re.search(r"(?:10|12|14|16)(?:le|be)$", (video or {}).get("pix_fmt", "")))
    args = ["-c:v", "libx264", "-crf", str(CRF), "-preset", "medium",
            "-pix_fmt", "yuv420p10le" if deep else "yuv420p"] + colour_args(video)
    if ext in (".mp4", ".mov", ".m4v"):
        args += ["-movflags", "+faststart"]
    return args


def audio_encode(output):
    ext = Path(output).suffix.lower()
    if ext == ".webm":
        return ["-c:a", "libopus", "-b:a", "160k"]
    if ext == ".avi":
        return ["-c:a", "libmp3lame", "-q:a", "2"]
    return ["-c:a", "aac", "-b:a", "192k"]


def video_copy_or_encode(output, video):
    ext = Path(output).suffix.lower()
    if ext in (".mkv",) or (video or {}).get("codec_name") in VIDEO_COPY.get(ext, set()):
        return ["-c:v", "copy"]
    return video_encode(output, video)


def audio_copy_or_encode(output, audio):
    ext = Path(output).suffix.lower()
    if ext in (".mkv",) or (audio or {}).get("codec_name") in AUDIO_COPY.get(ext, set()):
        return ["-c:a", "copy"]
    return audio_encode(output)


def run(args, output, single_file=True):
    """ffmpeg with args then output; any failure, or an empty output, is an error."""
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", *map(str, args), str(output)],
                           capture_output=True, text=True)
    except FileNotFoundError:
        fail("ffmpeg is not installed")
    written = not single_file or (Path(output).exists() and Path(output).stat().st_size > 0)
    if r.returncode != 0 or not written:
        fail(f"ffmpeg failed: {r.stderr.strip()[-1200:] or 'no output written'}")


def cmd_info(args):
    """Get video file info."""
    info = probe(args.input)
    video, audio = info["video"], info["audio"]
    if not video:
        fail(f"{args.input} has no video stream")
    width, height = video.get("width"), video.get("height")
    turn = rotation(video)
    if turn % 180:
        width, height = height, width
    seconds = info["duration"]
    num, _, den = frame_rate(video).partition("/")
    out = {
        "duration_seconds": round(seconds, 2),
        "duration_formatted": f"{int(seconds // 3600)}:{int(seconds % 3600 // 60):02d}:{int(seconds % 60):02d}",
        "fps": round(float(num) / float(den or 1), 3),
        "size": [width, height],
        "width": width,
        "height": height,
        "rotation": turn,
        "video_codec": video.get("codec_name"),
        "pix_fmt": video.get("pix_fmt"),
        "colour": {k: video.get(k) for k in ("color_primaries", "color_transfer", "color_space", "color_range")},
        "has_audio": audio is not None,
        "audio_codec": (audio or {}).get("codec_name"),
        "audio_sample_rate": int((audio or {}).get("sample_rate") or 0) or None,
        "audio_channels": (audio or {}).get("channels"),
        "bit_rate": int(info["format"].get("bit_rate") or 0) or None,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_cut(args):
    """Cut a segment: frame accurate re-encode, or --copy for a lossless keyframe cut."""
    info = probe(args.input)
    start = args.start or 0.0
    end = info["duration"] if args.end is None else min(args.end, info["duration"] or args.end)
    if not 0 <= start < end:
        fail(f"--start {start} and --end {end} do not make a span inside {info['duration']:.2f} s")
    if args.copy:
        run(["-ss", start, "-i", args.input, "-t", end - start, "-map", "0:v:0", "-map", "0:a:0?",
             "-c", "copy", "-avoid_negative_ts", "make_zero"], args.output)
    else:
        run(["-ss", start, "-i", args.input, "-t", end - start, "-map", "0:v:0", "-map", "0:a:0?",
             *video_encode(args.output, info["video"]), *audio_encode(args.output)], args.output)
    print(f"Cut: {start}s to {end}s ({end - start:.2f}s){' (stream copy)' if args.copy else ''} -> {args.output}")


def cmd_merge(args):
    """Concatenate videos, fitted to the first one's size and frame rate."""
    if len(args.files) < 2:
        fail("need at least 2 files")
    infos = [probe(f) for f in args.files]
    first = infos[0]["video"]
    if not first:
        fail(f"{args.files[0]} has no video stream")
    width, height = first["width"], first["height"]
    if rotation(first) % 180:
        width, height = height, width
    rate = frame_rate(first)
    pix = "yuv420p10le" if "10" in first.get("pix_fmt", "") else "yuv420p"
    inputs, graph = [], []
    for i, (path, info) in enumerate(zip(args.files, infos)):
        if not info["video"]:
            fail(f"{path} has no video stream")
        inputs += ["-i", path]
        length = info["duration"]
        graph.append(f"[{i}:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                     f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={rate},format={pix}[v{i}]")
        # every segment's sound lasts exactly as long as its picture, so the
        # next clip cannot start early or late; a silent clip gets silence
        if info["audio"]:
            graph.append(f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={length}[a{i}]")
        else:
            graph.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={length}[a{i}]")
    graph.append("".join(f"[v{i}][a{i}]" for i in range(len(args.files)))
                 + f"concat=n={len(args.files)}:v=1:a=1[v][a]")
    run([*inputs, "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
         *video_encode(args.output, first), *audio_encode(args.output)], args.output)
    total = sum(info["duration"] for info in infos)
    print(f"Merged {len(args.files)} videos ({total:.1f}s) at {width}x{height} -> {args.output}")


def cmd_audio(args):
    """Add, replace, mix or remove audio; the picture is copied, not re-encoded."""
    info = probe(args.input)
    if not info["video"]:
        fail(f"{args.input} has no video stream")
    vcodec = video_copy_or_encode(args.output, info["video"])
    if args.remove:
        run(["-i", args.input, "-map", "0:v:0", *vcodec, "-an"], args.output)
        print(f"Removed audio -> {args.output}")
        return
    if not args.audio:
        fail("specify --audio FILE or --remove")
    added = probe(args.audio)
    if not added["audio"]:
        fail(f"{args.audio} has no audio stream")
    length = ["-t", info["duration"]] if info["duration"] else []
    if args.mix and info["audio"]:
        # summed at full level like before, with a limiter instead of clipping
        run(["-i", args.input, "-i", args.audio, "-filter_complex",
             "[0:a:0][1:a:0]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.97[a]",
             "-map", "0:v:0", "-map", "[a]", *vcodec, *audio_encode(args.output), *length], args.output)
        print(f"Mixed audio -> {args.output}")
    else:
        run(["-i", args.input, "-i", args.audio, "-map", "0:v:0", "-map", "1:a:0", *vcodec,
             *audio_encode(args.output), *length], args.output)
        print(f"Replaced audio -> {args.output}")


FONT_CANDIDATES = (
    # Linux (Debian/Ubuntu, Fedora, Arch)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
)


def _default_font() -> str | None:
    """First font file that exists on this machine, or None."""
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            return path
    return None


def _ffmpeg_has_filter(name: str) -> bool:
    """Whether this ffmpeg build has the named filter. drawtext needs
    libfreetype, and Homebrew's ffmpeg 9.0.2 on the Mac mini is built without
    it: no drawtext at all (measured 2026-09-27, review of #187)."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(line.split()[1:2] == [name] for line in out.splitlines())


def _drawtext_has_text_align() -> bool:
    """drawtext's text_align (centred lines) arrived in ffmpeg 6.1; older
    builds, such as Ubuntu 22.04's 4.4, refuse the whole filter over it."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-h", "filter=drawtext"],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "text_align" in out


def _rgba(colour: str) -> tuple:
    """An ffmpeg colour (a name, #RRGGBB or 0xRRGGBB, optionally @alpha) as RGBA."""
    from PIL import ImageColor

    name, _, alpha = colour.partition("@")
    if name.lower().startswith("0x"):
        name = "#" + name[2:]
    try:
        rgba = ImageColor.getcolor(name, "RGBA")
        if alpha:
            rgba = rgba[:3] + (round(255 * min(max(float(alpha), 0.0), 1.0)),)
    except ValueError:
        fail(f"not a colour: {colour} (a name, #RRGGBB or 0xRRGGBB, optionally @0.5)")
    return rgba


def _text_png(text, font, size, colour, outline, path):
    """The text drawn once on transparency, for an ffmpeg without drawtext.
    Lines are centred as text_align=C centres them, and the outline is
    drawtext's borderw and bordercolor."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        fail("this ffmpeg has no drawtext filter, and drawing the text without it needs Pillow: "
             "pip install pillow")
    face = ImageFont.truetype(str(font), size)
    stroke = max(1, round(size / 20)) if outline else 0
    left, top, right, bottom = ImageDraw.Draw(Image.new("RGBA", (1, 1))).multiline_textbbox(
        (0, 0), text, font=face, align="center", stroke_width=stroke)
    # Pillow measures in fractions of a pixel; the canvas is whole pixels
    canvas = (max(math.ceil(right - left), 1), max(math.ceil(bottom - top), 1))
    image = Image.new("RGBA", canvas, (0, 0, 0, 0))
    ImageDraw.Draw(image).multiline_text((-left, -top), text, font=face, fill=_rgba(colour),
                                         align="center", stroke_width=stroke,
                                         stroke_fill=(0, 0, 0, 178))
    image.save(path)


def _overlay_format(video) -> str:
    """overlay's working format for this source: its own chroma and bit depth.
    format=auto is not that: with an RGBA overlay, ffmpeg 9.0.2 negotiated
    rgba for the MAIN input, the RGB round trip this script exists to avoid,
    and overlay's default, yuv420, cuts a 10-bit source to 8 bits (both read
    in ffmpeg -v verbose, 2026-09-27)."""
    pix = (video or {}).get("pix_fmt", "")
    chroma = "444" if "444" in pix else "422" if "422" in pix else "420"
    deep = bool(re.search(r"(?:10|12|14|16)(?:le|be)$", pix))
    return f"yuv{chroma}" + ("p10" if deep else "")


def _overlay_position(expr: str) -> str:
    """A POSITIONS expression for overlay, where the text's size is the
    overlay's own w and h and the frame's is W and H."""
    return re.sub(r"\b(text_)?([wh])\b",
                  lambda m: m.group(2) if m.group(1) else m.group(2).upper(), expr)


def cmd_text(args):
    """Burn a text overlay in with drawtext, or with Pillow and overlay where
    this ffmpeg has no drawtext."""
    info = probe(args.input)
    # MoviePy-era audit F24 (2026-09-06): no font ships with the tools, so
    # take the first one this OS actually has unless --font names one.
    font = args.font or _default_font()
    if not font:
        fail("no usable font found; pass one with --font (a .ttf or .otf path)")
    font = Path(font)
    if not font.is_file():
        fail(f"font file not found: {font} (--font takes a .ttf or .otf path)")
    height = (info["video"] or {}).get("height") or 1080
    margin = round(0.05 * height)
    x, y = (expr.replace("M", str(margin)) for expr in POSITIONS[args.position])
    # font and text go in by staged file names: no escaping of colons,
    # quotes or percent signs in either can break the filter
    with tempfile.TemporaryDirectory(prefix="video_text_") as tmp:
        text = args.text.replace("\\n", "\n")
        drawtext = _ffmpeg_has_filter("drawtext")
        if drawtext:
            staged_font = Path(tmp) / f"font{font.suffix.lower()}"
            staged_font.symlink_to(font.resolve())
            text_file = Path(tmp) / "text.txt"
            text_file.write_text(text, encoding="utf-8")
            draw = (f"drawtext=fontfile={staged_font}:textfile={text_file}:expansion=none:"
                    f"fontsize={args.fontsize}:fontcolor={args.color}:x={x}:y={y}")
            if _drawtext_has_text_align():
                draw += ":text_align=C"
            if args.outline:
                draw += f":borderw={max(1, round(args.fontsize / 20))}:bordercolor=black@0.7"
            picture = ["-i", args.input, "-map", "0:v:0", "-vf", draw]
        else:
            # Pillow draws the text once and overlay lays it on every frame,
            # working in the source's own format, so the picture never leaves
            # YUV or its bit depth and overlay changes only the text's pixels.
            png = Path(tmp) / "text.png"
            _text_png(text, font, args.fontsize, args.color, args.outline, png)
            place = f"x={_overlay_position(x)}:y={_overlay_position(y)}"
            picture = ["-i", args.input, "-i", png, "-filter_complex",
                       f"[0:v:0][1:v:0]overlay={place}:format={_overlay_format(info['video'])}[v]",
                       "-map", "[v]"]
        run([*picture, "-map", "0:a:0?",
             *video_encode(args.output, info["video"]), *audio_copy_or_encode(args.output, info["audio"])],
            args.output)
    how = "" if drawtext else " (drawn with Pillow: this ffmpeg has no drawtext)"
    print(f"Added text '{args.text}' at {args.position}{how} -> {args.output}")


def cmd_resize(args):
    """Resize; a missing side keeps the aspect ratio, sizes are kept even for 4:2:0."""
    if not args.width and not args.height:
        fail("specify --width and/or --height")
    info = probe(args.input)
    width = args.width - args.width % 2 if args.width else -2
    height = args.height - args.height % 2 if args.height else -2
    run(["-i", args.input, "-map", "0:v:0", "-map", "0:a:0?",
         "-vf", f"scale={width}:{height}:flags=lanczos,setsar=1",
         *video_encode(args.output, info["video"]), *audio_copy_or_encode(args.output, info["audio"])],
        args.output)
    got = probe(args.output)["video"]
    print(f"Resized to {got['width']}x{got['height']} -> {args.output}")


def cmd_frames(args):
    """Extract frames as PNG images at --fps."""
    if args.fps <= 0:
        fail("--fps must be above 0")
    probe(args.input)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def ours():
        # only the names this command writes; a frame_hero.png of the user's stays
        return [p for p in out_dir.iterdir() if re.fullmatch(r"frame_\d{4,}\.png", p.name)]

    for old in ours():
        old.unlink()    # a shorter run would otherwise leave the last run's tail behind
    run(["-i", args.input, "-vf", f"fps={args.fps}", "-start_number", "0"], out_dir / "frame_%04d.png",
        single_file=False)
    count = len(ours())
    if not count:
        fail("no frames were written")
    print(f"Extracted {count} frames to {out_dir}")


def cmd_extract_audio(args):
    """Extract the audio track; copied when the target format already holds that codec."""
    info = probe(args.input)
    if not info["audio"]:
        fail("video has no audio track")
    ext = Path(args.output).suffix.lower()
    if ext not in AUDIO_ENCODE:
        fail(f"unsupported audio format {ext or '(none)'}: use {', '.join(sorted(AUDIO_ENCODE))}")
    same = {".m4a": "aac", ".aac": "aac", ".mp3": "mp3", ".flac": "flac", ".opus": "opus", ".ogg": "vorbis"}
    codec = ["-c:a", "copy"] if same.get(ext) == info["audio"].get("codec_name") else AUDIO_ENCODE[ext]
    run(["-i", args.input, "-map", "0:a:0", "-vn", *codec], args.output)
    print(f"Extracted audio{' (copied)' if codec[-1] == 'copy' else ''} -> {args.output}")


def cmd_gif(args):
    """Create a GIF with a palette built for the clip."""
    info = probe(args.input)
    start = args.start or 0.0
    if start >= info["duration"]:
        fail(f"--start {start} is past the end ({info['duration']:.2f} s)")
    duration = min(args.duration, info["duration"] - start)
    if args.fps <= 0 or duration <= 0:
        fail("--fps and --duration must be above 0")
    scale = f"scale={args.width}:-1:flags=lanczos," if args.width else ""
    graph = (f"fps={args.fps},{scale}split[a][b];[a]palettegen=stats_mode=diff[p];"
             f"[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
    run(["-ss", start, "-t", duration, "-i", args.input, "-filter_complex", graph, "-loop", "0"], args.output)
    print(f"Created GIF ({duration:.1f}s @ {args.fps}fps) -> {args.output}")


def main():
    if not shutil.which("ffmpeg"):
        fail("ffmpeg is not installed")
    parser = argparse.ArgumentParser(description="Video editing tool (ffmpeg)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("info", help="Get video info")
    p.add_argument("input", help="Input file")
    p.set_defaults(func=cmd_info)

    p = subparsers.add_parser("cut", help="Cut segment")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--start", type=float, help="Start time (seconds)")
    p.add_argument("--end", type=float, help="End time (seconds)")
    p.add_argument("--copy", action="store_true",
                   help="Lossless and instant stream copy; starts on the keyframe at or before --start")
    p.set_defaults(func=cmd_cut)

    p = subparsers.add_parser("merge", help="Concatenate videos")
    p.add_argument("files", nargs="+", help="Files to merge, in order")
    p.add_argument("-o", "--output", required=True, help="Output file")
    p.set_defaults(func=cmd_merge)

    p = subparsers.add_parser("audio", help="Modify audio")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--audio", help="Audio file to add")
    p.add_argument("--mix", action="store_true", help="Mix with the original instead of replacing it")
    p.add_argument("--remove", action="store_true", help="Remove audio")
    p.set_defaults(func=cmd_audio)

    p = subparsers.add_parser("text", help="Add text overlay")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--text", required=True, help="Text to add (\\n for a line break)")
    p.add_argument("--position", default="bottom", choices=list(POSITIONS), help="Position (5%% margin)")
    p.add_argument("--fontsize", type=int, default=50, help="Font size in pixels")
    p.add_argument("--color", default="white", help="Text colour (name or #RRGGBB)")
    p.add_argument("--font", help="Font FILE (.ttf/.otf); default: the first of FONT_CANDIDATES this machine has")
    p.add_argument("--outline", action="store_true", help="Thin dark outline for legibility")
    p.set_defaults(func=cmd_text)

    p = subparsers.add_parser("resize", help="Resize video")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--width", type=int, help="Width")
    p.add_argument("--height", type=int, help="Height")
    p.set_defaults(func=cmd_resize)

    p = subparsers.add_parser("frames", help="Extract frames")
    p.add_argument("input", help="Input file")
    p.add_argument("output_dir", help="Output directory")
    p.add_argument("--fps", type=float, default=1, help="Frames per second")
    p.set_defaults(func=cmd_frames)

    p = subparsers.add_parser("extract-audio", help="Extract audio")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output audio file (.mp3 .m4a .aac .wav .flac .ogg .opus)")
    p.set_defaults(func=cmd_extract_audio)

    p = subparsers.add_parser("gif", help="Create GIF")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output GIF")
    p.add_argument("--start", type=float, default=0, help="Start time")
    p.add_argument("--duration", type=float, default=5, help="Duration")
    p.add_argument("--fps", type=int, default=10, help="FPS")
    p.add_argument("--width", type=int, help="Width (keeps the ratio)")
    p.set_defaults(func=cmd_gif)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
