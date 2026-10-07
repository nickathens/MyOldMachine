# Blender Video Editing Skill

Video editing via Blender's Video Sequence Editor (VSE): cuts, titles, fades, colour, speed, and joining clips with dissolves, rendered on the CPU.

For plain ffmpeg jobs (trim, merge, overlay, convert) the `video-editing` skill is lighter. Use this one when you want the VSE's own transitions and retiming.

## What it does (each proved by a render on Blender 5.2.2, 2026-09-27)

- **Cut** a range; the output starts on the first kept frame, sound in sync
- **Title** text over the picture, fading in and out
- **Fade** in from black and out to black; the sound fades with it
- **Colour**: brightness, contrast, saturation, black and white
- **Speed**: faster or slower through Blender's retiming; the sound follows at the same pitch
- **Join** clips, with a dissolve or wipe and a sound crossfade, or butted
- **Export**: MP4 (H.264 + AAC), WebM (VP9 + Opus), ProRes (QuickTime + PCM)

Every clip keeps its own frame rate (read with ffprobe, 29.97 stays 29.97) and its sound. Colours pass through unchanged: the scene uses Blender's Standard view transform, because the default (AgX) tone maps the whole edit.

## Usage

```bash
V=skills/blender-video/scripts/video_edit.py

# Cut (keep 10 s to 30 s; "10-" and "-30" work too)
blender --background --python $V -- --input video.mp4 --cut 10-30 --output cut.mp4

# Title overlay
blender --background --python $V -- --input video.mp4 --text "Title" --position top --output titled.mp4

# Colour (brightness -1..1, contrast 0..2, saturation 0..2)
blender --background --python $V -- --input video.mp4 --brightness 0.1 --contrast 1.2 --saturation 1.1 --output graded.mp4
blender --background --python $V -- --input video.mp4 --bw --output bw.mp4

# Speed (0.5 = half speed, 2.0 = double)
blender --background --python $V -- --input video.mp4 --speed 0.5 --output slowmo.mp4

# Fades
blender --background --python $V -- --input video.mp4 --fade-in 1.0 --fade-out 1.0 --output faded.mp4

# Join, with a one second dissolve
blender --background --python $V -- --concat a.mp4 b.mp4 --transition dissolve --transition-duration 1.0 --output joined.mp4
```

Options combine on a single input (cut plus speed plus title plus colour plus fades in one render). `--format webm|prores`, `--quality low|medium|high|lossless`, `--width`/`--height` override the output size: the picture is scaled to fit, with bars when the shape differs. `--concat` takes only the join options (transition, size, format, quality) and refuses the others rather than drop them: join first, then edit the joined file. With no `--output` the file goes to /tmp with the format's own extension (.mp4, .webm, .mov).

## Notes

- Needs Blender 5 or newer: the script is written on the 5.x API and refuses an older Blender with the reason. Distribution packages can be older (Ubuntu 24.04's apt blender is 4.0); use the snap, brew's cask or the blender.org build.
- Exit code 0 means the file was written; any failure prints the reason and exits 1. Blender itself exits 0 after an uncaught script error, which is how the old version of this script "succeeded" on every call without writing anything.
- Joined clips run at the first clip's frame rate; a clip with a different rate is warned about, because it will play at the wrong speed. Convert it first (video-editing skill).
- Shares the Blender dependency with the blender 3D skill, no extra install needed
- Rendering runs on the CPU in the background mode used here, so expect several seconds per second of 1080p.
- Memory: the script caps Blender's frame cache at 256 MB (a render reads each frame once). At Blender's default 4 GB a 60 second 1080p edit peaked at 4.9 GB; capped, under 1 GB, with the same frames (2026-10-07).
