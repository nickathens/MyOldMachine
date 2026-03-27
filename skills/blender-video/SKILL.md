# Blender Video Editing Skill

Video editing, color grading, and compositing via Blender's Video Sequence Editor (VSE).

## Capabilities

- **Cuts & Trims**: Cut, split, trim video clips
- **Transitions**: Cross dissolve, wipe, fade in/out
- **Text Overlays**: Titles, lower thirds, captions
- **Color Grading**: Curves, color balance, LUTs, exposure
- **Speed Control**: Slow motion, time remapping
- **Effects**: Blur, glow, vignette, film grain
- **Audio**: Mix, fade, sync audio tracks
- **Export**: MP4 (H.264), WebM, ProRes, image sequences

## Usage

```bash
# Basic cut
blender --background --python scripts/video_edit.py -- --input video.mp4 --cut 10-30 --output cut.mp4

# Add text overlay
blender --background --python scripts/video_edit.py -- --input video.mp4 --text "Title" --position top --output titled.mp4

# Color grade with LUT
blender --background --python scripts/video_edit.py -- --input video.mp4 --lut cinematic.cube --output graded.mp4

# Concatenate videos
blender --background --python scripts/video_edit.py -- --concat video1.mp4 video2.mp4 --output combined.mp4

# Speed change (0.5 = half speed, 2.0 = double)
blender --background --python scripts/video_edit.py -- --input video.mp4 --speed 0.5 --output slowmo.mp4

# Fade in/out
blender --background --python scripts/video_edit.py -- --input video.mp4 --fade-in 1.0 --fade-out 1.0 --output faded.mp4

# Cross dissolve transition between clips
blender --background --python scripts/video_edit.py -- --concat video1.mp4 video2.mp4 --transition dissolve --duration 1.0 --output merged.mp4
```

## Script Location

`scripts/video_edit.py` - Main video editing script

## Color Grading Options

- `--brightness` - Adjust brightness (-1.0 to 1.0)
- `--contrast` - Adjust contrast (0.0 to 2.0)
- `--saturation` - Adjust saturation (0.0 to 2.0)
- `--gamma` - Adjust gamma (0.1 to 3.0)
- `--lut` - Apply LUT file (.cube format)
- `--tint` - Apply color tint (hex color)

## Examples

"Cut the first 30 seconds from this video"
"Add a title 'Chapter 1' at the start"
"Make this video black and white"
"Slow this down to half speed"
"Add a cinematic fade in and out"
"Concatenate these clips with dissolve transitions"
"Color grade this to look more cinematic"

## Notes

- Uses Blender VSE (Video Sequence Editor)
- Requires Blender 2.8+ (older versions like 2.79 have a different VSE API and will not work)
- Shares the Blender dependency with the blender 3D skill — no extra install needed
- Large videos may take time to process
- Supports most video formats via ffmpeg
- On old distros (e.g., Ubuntu 18.04), the system Blender may be too old — install from snap or blender.org
