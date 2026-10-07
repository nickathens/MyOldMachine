#!/usr/bin/env python3
"""
Blender 5.x Video Sequence Editor (VSE) script.

Run with:
    blender --background --python video_edit.py -- [args]

Rewritten 2026-09-27 (Linux bot review), for Blender 5 and newer only: older
Blender is refused with the reason. The previous version never ran on
Blender 5.2: it requested video output the pre 5.0 way
(file_format 'FFMPEG' without media_type 'VIDEO'), called new_effect with the
removed frame_end/seq1/seq2 arguments, and Blender exits 0 on an uncaught
error, so every call "succeeded" with no file. It also never added sound (all
outputs silent), rendered every clip at the scene's default rate instead of
its own (a 25 fps clip drifted against its audio), and a cut left the first
seconds black. What this version does, each proved by a render:

- every clip keeps its own frame rate (read with ffprobe) and its sound;
- cuts start at frame 1 and keep audio in sync;
- any failure prints the reason and exits 1.

Linux bot sweep 2026-10-07: a cut with a speed change rendered one black frame (the
retiming keys Blender adds at a cut's ends pinned the clip); --width/--height
and a joined clip of another size were cropped, not scaled; a relative
--output failed; and Blender's 4 GB frame cache took a 60 second 1080p edit
to 4.9 GB, 0.96 GB with the cache at 256 MB and the same frames.
"""
import argparse
import json
import os
import subprocess
import sys
import traceback
import uuid

import bpy


# ---------------------------------------------------------------- probing


def probe(path):
    """Size, exact frame rate, audio presence and duration, from ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError(f"{path} has no video stream")
    rate = video.get("r_frame_rate") or video.get("avg_frame_rate") or "25/1"
    num, den = (int(x) for x in rate.split("/"))
    if not num or not den:
        num, den = 25, 1
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps_num": num,
        "fps_den": den,
        "fps": num / den,
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
        "duration": float(data.get("format", {}).get("duration") or 0.0),
    }


# ---------------------------------------------------------------- scene


def setup_scene(info, width=None, height=None):
    """Resolution and the clip's exact frame rate (30000/1001 is 30 at base 1.001)."""
    scene = bpy.context.scene
    scene.render.resolution_x = width or info["width"]
    scene.render.resolution_y = height or info["height"]
    scene.render.resolution_percentage = 100
    fps_int = max(1, round(info["fps"]))
    scene.render.fps = fps_int
    scene.render.fps_base = fps_int / info["fps"]
    scene.frame_start = 1
    # Blender's default view transform (AgX) tone maps the whole edit: the
    # test clip's background went 63,94,127 -> 57,95,127 and white text
    # peaked at 220. Standard passes the footage through unchanged.
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    # A render reads each frame once, so the cache only costs memory: at
    # Blender's default 4096 MB a 60 second 1080p edit peaked at 4.9 GB,
    # at 256 MB at 0.96 GB with identical frames and no loss of speed.
    # (Preferences are not saved by a --background run.)
    bpy.context.preferences.system.memory_cache_limit = 256
    scene.sequence_editor_create()
    for strip in list(scene.sequence_editor.strips_all):
        scene.sequence_editor.strips.remove(strip)
    return scene


def seconds_to_frames(seconds, info):
    return int(round(seconds * info["fps"]))


def add_clip(path, info, frame_start=1, channel=1):
    """The movie strip and, when the file has audio, its sound strip."""
    ed = bpy.context.scene.sequence_editor
    name = os.path.basename(path)
    # FIT scales the picture into the frame: the API's default, ORIGINAL,
    # cropped the middle out of a clip larger than the frame (--width and
    # --height, or a bigger clip in a join)
    movie = ed.strips.new_movie(name=name, filepath=path, channel=channel, frame_start=frame_start,
                                fit_method='FIT')
    sound = None
    if info["has_audio"]:
        sound = ed.strips.new_sound(name=name + " audio", filepath=path,
                                    channel=channel + 1, frame_start=frame_start)
    return movie, sound


def retime(strips, speed):
    """Play each whole strip at `speed`: Blender 5 retiming, a key at each
    end and the end key moved. The SPEED effect is locked to its input's
    length there (right_handle is read only), and retiming also works on the
    sound strip, with pitch kept. Done before any cut: on a cut strip
    Blender adds keys at the cut's ends too, the end key cannot pass them,
    and the clip collapsed to one black frame."""
    for strip in strips:
        if strip is None:
            continue
        length = strip.right_handle - strip.left_handle
        keys = strip.retiming_keys
        keys.add(timeline_frame=strip.left_handle)
        keys[-1].timeline_frame = strip.left_handle + max(1, int(round(length / speed)))
        if strip.type == "SOUND":
            strip.pitch_correction = True


def trim(strips, start_frames, end_frames, place_at):
    """Keep source frames [start, end) of each strip and move them to start at
    `place_at`. Handles are set before the move, as Blender measures them from
    the strip's own content start."""
    for strip in strips:
        if strip is None:
            continue
        origin = strip.left_handle
        end = min(origin + end_frames, strip.right_handle)
        strip.left_handle = origin + start_frames
        strip.right_handle = max(end, strip.left_handle + 1)
        _move(strip, strip.left_handle - place_at)


def _move(strip, frames):
    """Shift a strip `frames` earlier. content_start is the 5.x name;
    frame_start still works and is due to go in Blender 6.0."""
    if hasattr(strip, "content_start"):
        strip.content_start = strip.content_start - frames
    else:
        strip.frame_start = strip.frame_start - frames


def clip_end(strips):
    return max(s.right_handle for s in strips if s is not None)


def fade(strips, frames_in, frames_out):
    """Fade to and from black (picture) and silence (sound) with keyframes."""
    for strip in strips:
        if strip is None:
            continue
        prop = "volume" if strip.type == "SOUND" else "blend_alpha"
        full = getattr(strip, prop)
        first, last = strip.left_handle, strip.right_handle
        if frames_in:
            setattr(strip, prop, 0.0)
            strip.keyframe_insert(data_path=prop, frame=first)
            setattr(strip, prop, full)
            strip.keyframe_insert(data_path=prop, frame=first + frames_in)
        if frames_out:
            setattr(strip, prop, full)
            strip.keyframe_insert(data_path=prop, frame=last - frames_out)
            setattr(strip, prop, 0.0)
            strip.keyframe_insert(data_path=prop, frame=last)


def color(movie, brightness=0.0, contrast=1.0, saturation=1.0):
    """Brightness -1..1 (0 unchanged), contrast 0..2 and saturation 0..2 (1
    unchanged), through the strip's own saturation and a Bright/Contrast
    modifier. The old version wrote saturation into ONE point of a hue curve,
    so --bw only greyed the reds."""
    movie.color_saturation = max(0.0, saturation)
    if brightness or contrast != 1.0:
        mod = movie.modifiers.new(name="BrightContrast", type='BRIGHT_CONTRAST')
        mod.bright = max(-100.0, min(100.0, brightness * 100.0))
        mod.contrast = max(-100.0, min(100.0, (contrast - 1.0) * 100.0))


POSITIONS = {
    'center': (0.5, 0.5), 'top': (0.5, 0.85), 'bottom': (0.5, 0.15),
    'top-left': (0.15, 0.85), 'top-right': (0.85, 0.85),
    'bottom-left': (0.15, 0.15), 'bottom-right': (0.85, 0.15),
}


def add_text(text, frame_start, length, position='center', font_size=60, channel=5):
    ed = bpy.context.scene.sequence_editor
    strip = ed.strips.new_effect(name="Text", type='TEXT', channel=channel,
                                 frame_start=frame_start, length=length)
    strip.text = text
    strip.font_size = font_size
    strip.color = (1.0, 1.0, 1.0, 1.0)
    strip.location = POSITIONS.get(position, POSITIONS['center'])
    strip.anchor_x = 'CENTER'
    strip.anchor_y = 'CENTER'
    strip.alignment_x = 'CENTER'
    strip.blend_type = 'ALPHA_OVER'
    return strip


# ---------------------------------------------------------------- render


def render(output, fmt='mp4', quality='high'):
    scene = bpy.context.scene
    settings = scene.render.image_settings
    settings.media_type = 'VIDEO'      # Blender 5: FFMPEG is only offered after this
    settings.file_format = 'FFMPEG'
    ff = scene.render.ffmpeg
    if fmt == 'webm':
        ff.format, ff.codec, ff.audio_codec = 'WEBM', 'WEBM', 'OPUS'
    elif fmt == 'prores':
        ff.format, ff.codec, ff.audio_codec = 'QUICKTIME', 'PRORES', 'PCM'
    else:
        ff.format, ff.codec, ff.audio_codec = 'MPEG4', 'H264', 'AAC'
        ff.audio_bitrate = 192
    if fmt != 'prores':
        ff.constant_rate_factor = {'low': 'LOWEST', 'medium': 'MEDIUM', 'high': 'HIGH',
                                   'lossless': 'LOSSLESS'}[quality]
    ff.audio_channels = 'STEREO'
    ff.audio_mixrate = 48000
    scene.render.use_file_extension = False   # write exactly the path asked for
    # Blender cannot write a relative path with no .blend file to anchor it
    # ("Couldn't create directory for file cut.mp4"), the form the SKILL shows
    output = os.path.abspath(output)
    scene.render.filepath = output
    os.makedirs(os.path.dirname(output), exist_ok=True)
    bpy.ops.render.render(animation=True)
    if not os.path.isfile(output) or os.path.getsize(output) == 0:
        raise RuntimeError(f"Blender reported no error but wrote no file at {output}")
    print(f"Rendered: {output}")


# ---------------------------------------------------------------- operations


def op_single(args):
    """One input: optional trim, speed, fades, colour, title."""
    info = probe(args.input)
    setup_scene(info, args.width, args.height)
    scene = bpy.context.scene
    movie, sound = add_clip(args.input, info)
    start = seconds_to_frames(args.start or 0.0, info)
    end = seconds_to_frames(args.end, info) if args.end is not None else movie.right_handle - 1
    if end <= start:
        if args.end is None:
            raise ValueError(f"--start {args.start}s is past the end of the clip ({info['duration']:.2f}s)")
        raise ValueError(f"the cut ends ({args.end}s) before it starts ({args.start}s)")
    speed = 1.0 if args.speed is None else args.speed
    if speed <= 0:
        raise ValueError(f"--speed must be above 0, not {args.speed}")
    if speed != 1.0:
        # the whole clip is retimed first, so the cut is made in retimed frames
        retime([movie, sound], speed)
        start = int(round(start / speed))
        end = max(int(round(end / speed)), start + 1)
    trim([movie, sound], start, end, place_at=1)
    last = clip_end([movie, sound]) - 1

    fade_in = seconds_to_frames(args.fade_in or 0.0, info)
    fade_out = seconds_to_frames(args.fade_out or 0.0, info)
    if fade_in or fade_out:
        fade([movie, sound], fade_in, fade_out)

    saturation = 0.0 if args.bw else args.saturation
    if args.brightness or args.contrast != 1.0 or saturation != 1.0:
        color(movie, args.brightness, args.contrast, saturation)

    if args.text:
        length = min(seconds_to_frames(args.text_duration, info), last)
        text = add_text(args.text, 1, length, args.position, args.font_size)
        fade([text], min(seconds_to_frames(0.5, info), length // 4),
             min(seconds_to_frames(0.5, info), length // 4))

    scene.frame_end = last
    render(args.output, args.format, args.quality)


def op_concat(args):
    infos = [probe(p) for p in args.concat]
    first = infos[0]
    for path, info in zip(args.concat[1:], infos[1:]):
        if abs(info["fps"] - first["fps"]) > 1e-6:
            print(f"Warning: {os.path.basename(path)} is {info['fps']:.3f} fps, the edit runs at "
                  f"{first['fps']:.3f}; it will play at the wrong speed")
    setup_scene(first, args.width, args.height)
    scene = bpy.context.scene
    overlap = seconds_to_frames(args.transition_duration, first) if args.transition else 0
    cursor = 1
    previous = None
    for i, (path, info) in enumerate(zip(args.concat, infos)):
        channel = 1 if i % 2 == 0 else 3          # alternate so overlaps do not collide
        start = cursor - (overlap if previous else 0)
        movie, sound = add_clip(path, info, frame_start=start, channel=channel)
        if previous and overlap:
            prev_movie, prev_sound = previous
            kind = 'WIPE' if args.transition == 'wipe' else 'CROSS'
            scene.sequence_editor.strips.new_effect(
                name=f"Transition {i}", type=kind, channel=5, frame_start=start,
                length=overlap, input1=prev_movie, input2=movie)
            # crossfade the sound under the picture transition
            for strip, fade_in, fade_out in ((prev_sound, 0, overlap), (sound, overlap, 0)):
                if strip is not None:
                    fade([strip], fade_in, fade_out)
        cursor = clip_end([movie, sound])
        previous = (movie, sound)
    scene.frame_end = cursor - 1
    render(args.output, args.format, args.quality)


def build_parser():
    p = argparse.ArgumentParser(description='Blender VSE video editor')
    p.add_argument('--input', '-i', help='Input video file')
    p.add_argument('--output', '-o', help='Output file (default /tmp/output_<id> with the format\'s extension)')
    p.add_argument('--concat', nargs='+', help='Concatenate these videos in order')
    p.add_argument('--cut', help='Keep a range in seconds: "10-30", "10-" or "-30"')
    p.add_argument('--start', type=float, help='Keep from this second')
    p.add_argument('--end', type=float, help='Keep up to this second')
    p.add_argument('--text', help='Title text')
    p.add_argument('--position', default='center', choices=sorted(POSITIONS))
    p.add_argument('--font-size', type=int, default=60)
    p.add_argument('--text-duration', type=float, default=3.0)
    p.add_argument('--transition', choices=['dissolve', 'wipe', 'cross'])
    p.add_argument('--transition-duration', type=float, default=1.0)
    p.add_argument('--fade-in', type=float, help='Fade in from black, seconds')
    p.add_argument('--fade-out', type=float, help='Fade out to black, seconds')
    p.add_argument('--speed', type=float, help='Speed factor (0.5 slower, 2 faster); audio follows at the same pitch')
    p.add_argument('--brightness', type=float, default=0.0, help='-1..1, 0 unchanged')
    p.add_argument('--contrast', type=float, default=1.0, help='0..2, 1 unchanged')
    p.add_argument('--saturation', type=float, default=1.0, help='0..2, 1 unchanged')
    p.add_argument('--bw', action='store_true', help='Black and white')
    p.add_argument('--format', default='mp4', choices=['mp4', 'webm', 'prores'])
    p.add_argument('--quality', default='high', choices=['low', 'medium', 'high', 'lossless'])
    p.add_argument('--width', type=int)
    p.add_argument('--height', type=int)
    return p


# The options op_single applies and op_concat does not: with --concat they
# were dropped without a word, so a join asked for with a title came back
# without one.
SINGLE_INPUT_ONLY = ('cut', 'start', 'end', 'text', 'fade_in', 'fade_out', 'speed', 'bw')
SINGLE_INPUT_DEFAULTS = {'brightness': 0.0, 'contrast': 1.0, 'saturation': 1.0}


def main(argv):
    args = build_parser().parse_args(argv)
    version = '.'.join(map(str, bpy.app.version))
    print(f"Blender {version} VSE")
    if bpy.app.version < (5, 0, 0):
        # The strips, handles, retiming and new_effect calls below are the 5.x
        # API; on 4.x they fail part way through. Say so up front instead.
        raise RuntimeError(
            f"this script needs Blender 5 or newer and this is Blender {version}. "
            "Distribution packages can be older (Ubuntu 24.04's apt blender is 4.0); "
            "install the blender.org build, the snap, or brew's cask.")
    if args.output is None:
        ext = {'mp4': 'mp4', 'webm': 'webm', 'prores': 'mov'}[args.format]
        args.output = f'/tmp/output_{uuid.uuid4().hex[:8]}.{ext}'
    if args.concat:
        if args.input:
            raise ValueError("give --input or --concat, not both")
        ignored = [name for name in SINGLE_INPUT_ONLY if getattr(args, name)]
        ignored += [name for name, value in SINGLE_INPUT_DEFAULTS.items() if getattr(args, name) != value]
        if ignored:
            names = ', '.join('--' + name.replace('_', '-') for name in ignored)
            raise ValueError(f"{names}: for a single --input only, not --concat. Join first, then "
                             "edit the joined file with --input")
    if args.cut:
        first, _, second = args.cut.partition('-')
        args.start = float(first) if first else None
        args.end = float(second) if second else None
    if args.concat:
        op_concat(args)
    elif args.input:
        op_single(args)
    else:
        raise ValueError("give --input or --concat")


if __name__ == '__main__':
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    try:
        main(argv)
    except SystemExit:
        raise
    except Exception:
        # Blender exits 0 on an uncaught error; the caller must see a failure.
        traceback.print_exc()
        sys.exit(1)
    sys.exit(0)
