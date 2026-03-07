#!/usr/bin/env python3
"""
Video editing operations using moviepy.
"""

import argparse
import json
import os
import sys
from pathlib import Path


def cmd_info(args):
    """Get video file info."""
    from moviepy import VideoFileClip

    clip = VideoFileClip(args.input)

    info = {
        "duration_seconds": round(clip.duration, 2),
        "duration_formatted": f"{int(clip.duration//60)}:{int(clip.duration%60):02d}",
        "fps": clip.fps,
        "size": list(clip.size),
        "width": clip.size[0],
        "height": clip.size[1],
        "has_audio": clip.audio is not None,
    }

    clip.close()
    print(json.dumps(info, indent=2))


def cmd_cut(args):
    """Cut a segment from video."""
    from moviepy import VideoFileClip

    clip = VideoFileClip(args.input)
    start = args.start or 0
    end = args.end or clip.duration

    subclip = clip.subclipped(start, end)
    subclip.write_videofile(args.output, logger=None)

    print(f"Cut: {start}s to {end}s ({end-start:.1f}s) -> {args.output}")
    clip.close()


def cmd_merge(args):
    """Concatenate multiple videos."""
    from moviepy import VideoFileClip, concatenate_videoclips

    if len(args.files) < 2:
        print("Error: Need at least 2 files", file=sys.stderr)
        sys.exit(1)

    clips = [VideoFileClip(f) for f in args.files]
    final = concatenate_videoclips(clips, method="compose")
    final.write_videofile(args.output, logger=None)

    print(f"Merged {len(args.files)} videos ({final.duration:.1f}s) -> {args.output}")
    for c in clips:
        c.close()


def cmd_audio(args):
    """Add, replace, or remove audio."""
    from moviepy import VideoFileClip, AudioFileClip, CompositeAudioClip

    clip = VideoFileClip(args.input)

    if args.remove:
        final = clip.without_audio()
        print(f"Removed audio -> {args.output}")
    elif args.audio:
        audio = AudioFileClip(args.audio)
        # Loop or trim audio to match video
        if audio.duration < clip.duration:
            # Could loop, for now just use as-is
            pass
        else:
            audio = audio.subclipped(0, clip.duration)

        if args.mix and clip.audio:
            # Mix original and new audio
            mixed = CompositeAudioClip([clip.audio, audio])
            final = clip.with_audio(mixed)
            print(f"Mixed audio -> {args.output}")
        else:
            final = clip.with_audio(audio)
            print(f"Replaced audio -> {args.output}")
    else:
        print("Error: Specify --audio FILE or --remove", file=sys.stderr)
        sys.exit(1)

    final.write_videofile(args.output, logger=None)
    clip.close()


def cmd_text(args):
    """Add text overlay."""
    from moviepy import VideoFileClip, TextClip, CompositeVideoClip

    clip = VideoFileClip(args.input)

    # Position mapping
    positions = {
        'top': ('center', 'top'),
        'bottom': ('center', 'bottom'),
        'center': ('center', 'center'),
        'top-left': ('left', 'top'),
        'top-right': ('right', 'top'),
        'bottom-left': ('left', 'bottom'),
        'bottom-right': ('right', 'bottom'),
    }

    pos = positions.get(args.position, ('center', 'bottom'))

    txt = TextClip(
        text=args.text,
        font_size=args.fontsize,
        color=args.color,
        font=args.font or 'DejaVu-Sans',
    )
    txt = txt.with_position(pos).with_duration(clip.duration)

    final = CompositeVideoClip([clip, txt])
    final.write_videofile(args.output, logger=None)

    print(f"Added text '{args.text}' at {args.position} -> {args.output}")
    clip.close()


def cmd_resize(args):
    """Resize video."""
    from moviepy import VideoFileClip

    clip = VideoFileClip(args.input)

    if args.width and args.height:
        resized = clip.resized(newsize=(args.width, args.height))
    elif args.width:
        resized = clip.resized(width=args.width)
    elif args.height:
        resized = clip.resized(height=args.height)
    else:
        print("Error: Specify --width and/or --height", file=sys.stderr)
        sys.exit(1)

    resized.write_videofile(args.output, logger=None)
    print(f"Resized to {resized.size[0]}x{resized.size[1]} -> {args.output}")
    clip.close()


def cmd_frames(args):
    """Extract frames as images."""
    from moviepy import VideoFileClip

    clip = VideoFileClip(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fps = args.fps or 1
    count = 0

    for t in range(0, int(clip.duration), int(1/fps) if fps < 1 else 1):
        for sub in range(int(fps) if fps >= 1 else 1):
            time = t + sub/fps if fps >= 1 else t
            if time >= clip.duration:
                break
            frame_path = output_dir / f"frame_{count:04d}.png"
            clip.save_frame(str(frame_path), t=time)
            count += 1

    print(f"Extracted {count} frames to {output_dir}")
    clip.close()


def cmd_extract_audio(args):
    """Extract audio track from video."""
    from moviepy import VideoFileClip

    clip = VideoFileClip(args.input)

    if not clip.audio:
        print("Error: Video has no audio track", file=sys.stderr)
        sys.exit(1)

    clip.audio.write_audiofile(args.output, logger=None)
    print(f"Extracted audio -> {args.output}")
    clip.close()


def cmd_gif(args):
    """Create GIF from video."""
    from moviepy import VideoFileClip

    clip = VideoFileClip(args.input)

    start = args.start or 0
    duration = args.duration or min(5, clip.duration)
    end = min(start + duration, clip.duration)

    subclip = clip.subclipped(start, end)

    if args.width:
        subclip = subclip.resized(width=args.width)

    subclip.write_gif(args.output, fps=args.fps, logger=None)
    print(f"Created GIF ({end-start:.1f}s @ {args.fps}fps) -> {args.output}")
    clip.close()


def main():
    parser = argparse.ArgumentParser(description="Video editing tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Info
    p = subparsers.add_parser("info", help="Get video info")
    p.add_argument("input", help="Input file")
    p.set_defaults(func=cmd_info)

    # Cut
    p = subparsers.add_parser("cut", help="Cut segment")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--start", type=float, help="Start time (seconds)")
    p.add_argument("--end", type=float, help="End time (seconds)")
    p.set_defaults(func=cmd_cut)

    # Merge
    p = subparsers.add_parser("merge", help="Concatenate videos")
    p.add_argument("files", nargs="+", help="Files to merge")
    p.add_argument("-o", "--output", required=True, help="Output file")
    p.set_defaults(func=cmd_merge)

    # Audio
    p = subparsers.add_parser("audio", help="Modify audio")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--audio", help="Audio file to add")
    p.add_argument("--mix", action="store_true", help="Mix with original")
    p.add_argument("--remove", action="store_true", help="Remove audio")
    p.set_defaults(func=cmd_audio)

    # Text
    p = subparsers.add_parser("text", help="Add text overlay")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--text", required=True, help="Text to add")
    p.add_argument("--position", default="bottom", help="Position")
    p.add_argument("--fontsize", type=int, default=50, help="Font size")
    p.add_argument("--color", default="white", help="Text color")
    p.add_argument("--font", help="Font name")
    p.set_defaults(func=cmd_text)

    # Resize
    p = subparsers.add_parser("resize", help="Resize video")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--width", type=int, help="Width")
    p.add_argument("--height", type=int, help="Height")
    p.set_defaults(func=cmd_resize)

    # Frames
    p = subparsers.add_parser("frames", help="Extract frames")
    p.add_argument("input", help="Input file")
    p.add_argument("output_dir", help="Output directory")
    p.add_argument("--fps", type=float, default=1, help="Frames per second")
    p.set_defaults(func=cmd_frames)

    # Extract audio
    p = subparsers.add_parser("extract-audio", help="Extract audio")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output audio file")
    p.set_defaults(func=cmd_extract_audio)

    # GIF
    p = subparsers.add_parser("gif", help="Create GIF")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output GIF")
    p.add_argument("--start", type=float, default=0, help="Start time")
    p.add_argument("--duration", type=float, default=5, help="Duration")
    p.add_argument("--fps", type=int, default=10, help="FPS")
    p.add_argument("--width", type=int, help="Width (maintains ratio)")
    p.set_defaults(func=cmd_gif)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
