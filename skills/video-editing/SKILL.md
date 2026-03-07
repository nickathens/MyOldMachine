# Video Editing

Edit videos: cut, merge, add audio, text overlays, resize, extract frames.

## Script

`scripts/video.py` in this skill directory. Run with `python <path-to-script> <command> [args]`.

## Commands

```bash
# Cut a segment (start and end in seconds)
python scripts/video.py cut input.mp4 output.mp4 --start 10 --end 30

# Merge/concatenate videos
python scripts/video.py merge video1.mp4 video2.mp4 -o combined.mp4

# Add audio track (replace or mix)
python scripts/video.py audio input.mp4 output.mp4 --audio music.mp3
python scripts/video.py audio input.mp4 output.mp4 --audio music.mp3 --mix

# Remove audio
python scripts/video.py audio input.mp4 output.mp4 --remove

# Add text overlay
python scripts/video.py text input.mp4 output.mp4 --text "Hello" --position bottom

# Resize video
python scripts/video.py resize input.mp4 output.mp4 --width 1280 --height 720

# Extract frames as images
python scripts/video.py frames input.mp4 ./frames/ --fps 1

# Get video info
python scripts/video.py info input.mp4

# Extract audio from video
python scripts/video.py extract-audio input.mp4 output.mp3

# Create GIF from video
python scripts/video.py gif input.mp4 output.gif --start 0 --duration 5 --fps 10
```

## Text Positions

top, bottom, center, top-left, top-right, bottom-left, bottom-right

## Notes

- Uses moviepy with ffmpeg backend
- Supports mp4, webm, avi, mov, mkv
- Text overlays require ImageMagick for fancy fonts
- GIF creation can produce large files
