#!/usr/bin/env python3
"""
Blender Video Sequence Editor (VSE) script
Run with: blender --background --python video_edit.py -- [args]
"""
import bpy
import sys
import os
import argparse
import uuid

# Blender version
BLENDER_VERSION = bpy.app.version


def setup_scene(width=1920, height=1080, fps=30, duration_frames=None):
    """Initialize scene for video editing"""
    scene = bpy.context.scene
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.fps = fps
    scene.render.resolution_percentage = 100

    if duration_frames:
        scene.frame_start = 1
        scene.frame_end = duration_frames

    # Enable sequencer
    scene.sequence_editor_create()
    return scene


def clear_sequencer():
    """Clear all strips from sequencer"""
    scene = bpy.context.scene
    if scene.sequence_editor:
        for strip in scene.sequence_editor.strips_all:
            scene.sequence_editor.strips.remove(strip)


def get_video_info(filepath):
    """Get video duration and resolution by adding and checking strip"""
    scene = bpy.context.scene
    if not scene.sequence_editor:
        scene.sequence_editor_create()

    # Temporarily add strip to get info
    strip = scene.sequence_editor.strips.new_movie(
        name="temp",
        filepath=filepath,
        channel=1,
        frame_start=1
    )

    info = {
        'frames': strip.frame_final_duration,
        'fps': scene.render.fps,
        'duration': strip.frame_final_duration / scene.render.fps
    }

    # Get resolution from strip elements
    if strip.elements:
        info['width'] = strip.elements[0].orig_width
        info['height'] = strip.elements[0].orig_height

    scene.sequence_editor.strips.remove(strip)
    return info


# Blender 5.x names the sequencer collection `strips` (`sequences` was
# removed after 4.x, confirmed on 5.2.1, audit F25 2026-09-06). Every call
# below uses `strips`; on an older Blender, alias it once so the same code
# runs there too.
def _ensure_strips_alias():
    se_type = getattr(bpy.types, "SequenceEditor", None)
    if se_type is not None and not hasattr(se_type, "strips") and hasattr(se_type, "sequences"):
        se_type.strips = property(lambda self: self.sequences)
        se_type.strips_all = property(lambda self: self.sequences_all)


_ensure_strips_alias()


def add_video(filepath, channel=1, frame_start=1, trim_start=None, trim_end=None):
    """Add video strip to sequencer"""
    scene = bpy.context.scene
    seq = scene.sequence_editor

    strip = seq.strips.new_movie(
        name=os.path.basename(filepath),
        filepath=filepath,
        channel=channel,
        frame_start=frame_start
    )

    # Apply trims
    if trim_start:
        strip.frame_offset_start = int(trim_start * scene.render.fps)
    if trim_end:
        original_duration = strip.frame_final_duration
        trim_frames = int(trim_end * scene.render.fps)
        if trim_frames < original_duration:
            strip.frame_offset_end = original_duration - trim_frames

    return strip


def add_audio(filepath, channel=2, frame_start=1):
    """Add audio strip to sequencer"""
    scene = bpy.context.scene
    seq = scene.sequence_editor

    strip = seq.strips.new_sound(
        name=os.path.basename(filepath),
        filepath=filepath,
        channel=channel,
        frame_start=frame_start
    )
    return strip


def add_text(text, channel=3, frame_start=1, frame_end=None,
             position='center', font_size=60, color=(1, 1, 1, 1)):
    """Add text overlay strip"""
    scene = bpy.context.scene
    seq = scene.sequence_editor

    if frame_end is None:
        frame_end = scene.frame_end

    strip = seq.strips.new_effect(
        name="Text",
        type='TEXT',
        channel=channel,
        frame_start=frame_start,
        frame_end=frame_end
    )

    strip.text = text
    strip.font_size = font_size
    strip.color = color[:4] if len(color) >= 4 else (*color, 1)

    # Position
    positions = {
        'center': (0.5, 0.5),
        'top': (0.5, 0.85),
        'bottom': (0.5, 0.15),
        'top-left': (0.15, 0.85),
        'top-right': (0.85, 0.85),
        'bottom-left': (0.15, 0.15),
        'bottom-right': (0.85, 0.15),
    }
    pos = positions.get(position, (0.5, 0.5))
    strip.location = pos

    # Center alignment
    strip.align_x = 'CENTER'
    strip.align_y = 'CENTER'

    return strip


def add_color_strip(color=(0, 0, 0, 1), channel=1, frame_start=1, frame_end=None):
    """Add solid color strip"""
    scene = bpy.context.scene
    seq = scene.sequence_editor

    if frame_end is None:
        frame_end = scene.frame_end

    strip = seq.strips.new_effect(
        name="Color",
        type='COLOR',
        channel=channel,
        frame_start=frame_start,
        frame_end=frame_end
    )
    strip.color = color[:3]
    return strip


def add_transition(strip1, strip2, transition_type='CROSS', duration_frames=30):
    """Add transition between two strips"""
    scene = bpy.context.scene
    seq = scene.sequence_editor

    # Adjust strips to overlap
    overlap_start = strip2.frame_final_start
    strip1.frame_final_end = overlap_start + duration_frames

    transition_types = {
        'dissolve': 'CROSS',
        'cross': 'CROSS',
        'wipe': 'WIPE',
        'gamma_cross': 'GAMMA_CROSS',
    }

    effect_type = transition_types.get(transition_type.lower(), 'CROSS')

    transition = seq.strips.new_effect(
        name="Transition",
        type=effect_type,
        channel=max(strip1.channel, strip2.channel) + 1,
        frame_start=overlap_start,
        frame_end=overlap_start + duration_frames,
        seq1=strip1,
        seq2=strip2
    )

    return transition


def add_fade(strip, fade_type='both', duration=1.0):
    """Add fade in/out to strip"""
    scene = bpy.context.scene
    fps = scene.render.fps
    fade_frames = int(duration * fps)

    # Use strip's blend_alpha for fading
    strip.blend_type = 'ALPHA_OVER'

    # Fade in
    if fade_type in ('in', 'both'):
        strip.blend_alpha = 0
        strip.keyframe_insert(data_path='blend_alpha', frame=strip.frame_final_start)
        strip.blend_alpha = 1
        strip.keyframe_insert(data_path='blend_alpha', frame=strip.frame_final_start + fade_frames)

    # Fade out
    if fade_type in ('out', 'both'):
        strip.blend_alpha = 1
        strip.keyframe_insert(data_path='blend_alpha', frame=strip.frame_final_end - fade_frames)
        strip.blend_alpha = 0
        strip.keyframe_insert(data_path='blend_alpha', frame=strip.frame_final_end)


def apply_speed(strip, speed_factor):
    """Apply speed change to strip"""
    scene = bpy.context.scene
    seq = scene.sequence_editor

    speed = seq.strips.new_effect(
        name="Speed",
        type='SPEED',
        channel=strip.channel + 1,
        frame_start=strip.frame_final_start,
        seq1=strip
    )

    speed.speed_factor = speed_factor
    speed.use_frame_interpolate = True

    return speed


def apply_color_correction(strip, brightness=0, contrast=1, saturation=1, gamma=1):
    """Apply color correction modifier to strip"""
    mod = strip.modifiers.new(name="ColorCorrect", type='COLOR_BALANCE')

    # Blender uses lift/gamma/gain model
    # We'll approximate brightness/contrast

    # Brightness via lift
    if brightness != 0:
        lift_value = 1.0 + brightness * 0.5
        mod.color_balance.lift = (lift_value, lift_value, lift_value)

    # Contrast via gain
    if contrast != 1:
        mod.color_balance.gain = (contrast, contrast, contrast)

    # Saturation via modifier
    if saturation != 1:
        sat_mod = strip.modifiers.new(name="Saturation", type='HUE_CORRECT')
        sat_mod.curve_mapping.curves[1].points[0].location = (0, saturation)

    return mod


def apply_effect(strip, effect_type, **kwargs):
    """Apply visual effect to strip"""
    scene = bpy.context.scene
    seq = scene.sequence_editor

    effect_map = {
        'blur': 'GAUSSIAN_BLUR',
        'glow': 'GLOW',
        'transform': 'TRANSFORM',
    }

    if effect_type.lower() not in effect_map:
        print(f"Unknown effect: {effect_type}")
        return None

    effect = seq.strips.new_effect(
        name=effect_type,
        type=effect_map[effect_type.lower()],
        channel=strip.channel + 1,
        frame_start=strip.frame_final_start,
        frame_end=strip.frame_final_end,
        seq1=strip
    )

    # Configure effect
    if effect_type.lower() == 'blur':
        effect.size_x = kwargs.get('amount', 10)
        effect.size_y = kwargs.get('amount', 10)
    elif effect_type.lower() == 'glow':
        effect.threshold = kwargs.get('threshold', 0.5)
        effect.blur_radius = kwargs.get('radius', 5)

    return effect


def render_video(output_path, format='mp4', quality='high'):
    """Render the sequence to video file"""
    scene = bpy.context.scene

    scene.render.filepath = output_path

    if format.lower() == 'mp4':
        scene.render.image_settings.file_format = 'FFMPEG'
        scene.render.ffmpeg.format = 'MPEG4'
        scene.render.ffmpeg.codec = 'H264'

        quality_settings = {
            'low': 'LOWEST',
            'medium': 'MEDIUM',
            'high': 'HIGH',
            'lossless': 'LOSSLESS'
        }
        scene.render.ffmpeg.constant_rate_factor = quality_settings.get(quality, 'HIGH')
        scene.render.ffmpeg.audio_codec = 'AAC'
        scene.render.ffmpeg.audio_bitrate = 192

    elif format.lower() == 'webm':
        scene.render.image_settings.file_format = 'FFMPEG'
        scene.render.ffmpeg.format = 'WEBM'
        scene.render.ffmpeg.codec = 'WEBM'

    elif format.lower() == 'prores':
        scene.render.image_settings.file_format = 'FFMPEG'
        scene.render.ffmpeg.format = 'QUICKTIME'
        scene.render.ffmpeg.codec = 'PRORES'

    # Render
    bpy.ops.render.render(animation=True)
    print(f"Rendered: {output_path}")


def concatenate_videos(filepaths, output_path, transition=None, transition_duration=1.0):
    """Concatenate multiple videos"""
    scene = bpy.context.scene

    # Get info from first video
    info = get_video_info(filepaths[0])
    setup_scene(
        width=info.get('width', 1920),
        height=info.get('height', 1080),
        fps=info.get('fps', 30)
    )
    clear_sequencer()

    current_frame = 1
    strips = []

    transition_frames = int(transition_duration * scene.render.fps) if transition else 0

    for i, filepath in enumerate(filepaths):
        strip = add_video(filepath, channel=1, frame_start=current_frame)
        strips.append(strip)

        if transition and i > 0:
            # Overlap with previous
            strip.frame_start = current_frame - transition_frames
            add_transition(strips[i-1], strip, transition, transition_frames)

        current_frame = strip.frame_final_end + 1
        if transition:
            current_frame -= transition_frames

    scene.frame_end = current_frame
    render_video(output_path)


def cut_video(input_path, output_path, start_time=None, end_time=None):
    """Cut a segment from video"""
    info = get_video_info(input_path)
    setup_scene(
        width=info.get('width', 1920),
        height=info.get('height', 1080),
        fps=info.get('fps', 30)
    )
    clear_sequencer()

    scene = bpy.context.scene
    fps = scene.render.fps

    strip = add_video(input_path, channel=1, frame_start=1)

    # Apply cuts
    if start_time:
        strip.frame_offset_start = int(start_time * fps)

    if end_time:
        total_frames = strip.frame_duration
        end_frame = int(end_time * fps)
        if end_frame < total_frames:
            strip.frame_offset_end = total_frames - end_frame

    # Adjust scene duration
    scene.frame_end = strip.frame_final_duration

    render_video(output_path)


def add_title_to_video(input_path, output_path, text, position='center',
                       duration=3.0, font_size=80):
    """Add title overlay to video"""
    info = get_video_info(input_path)
    setup_scene(
        width=info.get('width', 1920),
        height=info.get('height', 1080),
        fps=info.get('fps', 30)
    )
    clear_sequencer()

    scene = bpy.context.scene
    fps = scene.render.fps

    # Add video
    video = add_video(input_path, channel=1, frame_start=1)
    scene.frame_end = video.frame_final_end

    # Add text
    title_frames = int(duration * fps)
    text_strip = add_text(
        text=text,
        channel=2,
        frame_start=1,
        frame_end=title_frames,
        position=position,
        font_size=font_size
    )

    # Fade the text
    add_fade(text_strip, 'both', 0.5)

    render_video(output_path)


if __name__ == '__main__':
    # Parse args after --
    argv = sys.argv
    if '--' in argv:
        argv = argv[argv.index('--') + 1:]
    else:
        argv = []

    parser = argparse.ArgumentParser(description='Blender VSE Video Editor')

    # Input/Output
    parser.add_argument('--input', '-i', help='Input video file')
    parser.add_argument('--output', '-o', default=f'/tmp/output_{uuid.uuid4().hex[:8]}.mp4', help='Output file')
    parser.add_argument('--concat', nargs='+', help='Concatenate multiple videos')

    # Cutting
    parser.add_argument('--cut', help='Cut range in seconds (e.g., "10-30" or "10-")')
    parser.add_argument('--start', type=float, help='Start time in seconds')
    parser.add_argument('--end', type=float, help='End time in seconds')

    # Text
    parser.add_argument('--text', help='Add text overlay')
    parser.add_argument('--position', default='center',
                       choices=['center', 'top', 'bottom', 'top-left', 'top-right', 'bottom-left', 'bottom-right'])
    parser.add_argument('--font-size', type=int, default=60)
    parser.add_argument('--text-duration', type=float, default=3.0)

    # Transitions
    parser.add_argument('--transition', choices=['dissolve', 'wipe', 'cross'])
    parser.add_argument('--transition-duration', type=float, default=1.0)

    # Effects
    parser.add_argument('--fade-in', type=float, help='Fade in duration')
    parser.add_argument('--fade-out', type=float, help='Fade out duration')
    parser.add_argument('--speed', type=float, help='Speed factor (0.5=slow, 2=fast)')

    # Color
    parser.add_argument('--brightness', type=float, default=0)
    parser.add_argument('--contrast', type=float, default=1)
    parser.add_argument('--saturation', type=float, default=1)
    parser.add_argument('--bw', action='store_true', help='Convert to black & white')

    # Output settings
    parser.add_argument('--format', default='mp4', choices=['mp4', 'webm', 'prores'])
    parser.add_argument('--quality', default='high', choices=['low', 'medium', 'high', 'lossless'])
    parser.add_argument('--width', type=int)
    parser.add_argument('--height', type=int)
    parser.add_argument('--fps', type=int)

    args = parser.parse_args(argv)

    print(f"Blender {'.'.join(map(str, BLENDER_VERSION))} VSE")

    # Handle concatenation
    if args.concat:
        print(f"Concatenating {len(args.concat)} videos...")
        concatenate_videos(
            args.concat,
            args.output,
            transition=args.transition,
            transition_duration=args.transition_duration
        )
        sys.exit(0)

    # Handle cut operation
    if args.cut:
        parts = args.cut.split('-')
        start = float(parts[0]) if parts[0] else None
        end = float(parts[1]) if len(parts) > 1 and parts[1] else None

        print(f"Cutting {args.input}: {start}s - {end}s")
        cut_video(args.input, args.output, start, end)
        sys.exit(0)

    # Handle text overlay
    if args.text and args.input:
        print(f"Adding text '{args.text}' to {args.input}")
        add_title_to_video(
            args.input,
            args.output,
            args.text,
            position=args.position,
            duration=args.text_duration,
            font_size=args.font_size
        )
        sys.exit(0)

    # General video processing
    if args.input:
        info = get_video_info(args.input)
        width = args.width or info.get('width', 1920)
        height = args.height or info.get('height', 1080)
        fps = args.fps or info.get('fps', 30)

        setup_scene(width, height, fps)
        clear_sequencer()

        scene = bpy.context.scene

        # Add video
        strip = add_video(
            args.input,
            channel=1,
            frame_start=1,
            trim_start=args.start,
            trim_end=args.end
        )

        scene.frame_end = strip.frame_final_end

        # Apply effects
        if args.speed:
            apply_speed(strip, args.speed)
            scene.frame_end = int(scene.frame_end / args.speed)

        if args.fade_in or args.fade_out:
            if args.fade_in and args.fade_out:
                add_fade(strip, 'both', args.fade_in)
            elif args.fade_in:
                add_fade(strip, 'in', args.fade_in)
            elif args.fade_out:
                add_fade(strip, 'out', args.fade_out)

        # Color adjustments
        if args.bw:
            args.saturation = 0

        if args.brightness != 0 or args.contrast != 1 or args.saturation != 1:
            apply_color_correction(
                strip,
                brightness=args.brightness,
                contrast=args.contrast,
                saturation=args.saturation
            )

        print(f"Processing {args.input} -> {args.output}")
        render_video(args.output, args.format, args.quality)
