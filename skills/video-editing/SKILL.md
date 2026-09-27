# Video Editing

Edit videos: cut, merge, add audio, text overlays, resize, extract frames.

## Script

`scripts/video.py` in this skill directory. Run with `python <path-to-script> <command> [args]`.

## Commands

```bash
# Cut a segment (start and end in seconds); frame accurate, one CRF 18 re-encode
python scripts/video.py cut input.mp4 output.mp4 --start 10 --end 30
# Lossless and instant, but it starts on the keyframe at or before --start
python scripts/video.py cut input.mp4 output.mp4 --start 10 --end 30 --copy

# Merge/concatenate videos
python scripts/video.py merge video1.mp4 video2.mp4 -o combined.mp4

# Add audio track (replace or mix)
python scripts/video.py audio input.mp4 output.mp4 --audio music.mp3
python scripts/video.py audio input.mp4 output.mp4 --audio music.mp3 --mix

# Remove audio
python scripts/video.py audio input.mp4 output.mp4 --remove

# Add text overlay (5% margin; \n breaks a line; --outline for legibility; --font takes a .ttf/.otf path)
python scripts/video.py text input.mp4 output.mp4 --text "Hello" --position bottom --outline

# Resize video
python scripts/video.py resize input.mp4 output.mp4 --width 1280 --height 720

# Extract frames as images (frame_0000.png on; a rerun first removes the
# frame_NNNN.png files already there and leaves every other file alone)
python scripts/video.py frames input.mp4 ./frames/ --fps 1

# Get video info
python scripts/video.py info input.mp4

# Extract audio from video (.mp3 .m4a .aac .wav .flac .ogg .opus; copied when the codec already fits)
python scripts/video.py extract-audio input.mp4 output.mp3

# Create GIF from video
python scripts/video.py gif input.mp4 output.gif --start 0 --duration 5 --fps 10
```

## Text Positions

top, bottom, center, top-left, top-right, bottom-left, bottom-right

## Examples

"Cut 10-30 seconds from this video" + video
"Combine these videos" + multiple videos
"Add this music to the video" + video + audio
"Remove audio from this video" + video
"Add 'Chapter 1' text at the bottom" + video
"Resize to 720p" + video
"Extract frames at 1fps" + video
"Make a gif from first 5 seconds" + video

## Notes

- Runs ffmpeg directly. It used moviepy until 2026-09-27, whose RGB pipe regraded every edit: a plain cut measured 25.95 dB against its source with luma 1.36 levels dark (ffmpeg at the same quality: 46 dB, no shift), dropped the colour tags and put MP3 audio in MP4
- A re-encode is libx264 CRF 18 (10-bit stays 10-bit) carrying the source's colour tags, with AAC audio; `.webm` gets VP9 and Opus. Audio edits and text keep the picture or the sound by stream copy where the container allows
- Merge fits every clip to the first one's size (letterboxed, not stretched) and frame rate; a clip without sound gets silence, so later clips stay in step
- `resize` keeps sizes even (4:2:0 needs it); give one side to keep the aspect ratio
- `info` reports the displayed size (phone rotation applied), codecs, pixel format and colour tags
- GIFs get a palette built for the clip (palettegen and paletteuse)
- Any ffmpeg failure prints `Error:` and exits 1
