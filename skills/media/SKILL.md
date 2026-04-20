# Media Capture

Screenshots and video recording of web pages and the desktop.

## Screenshot

Capture a screenshot of any URL.

```bash
python skills/media/scripts/screenshot.py <url> <output.png>
```

**Examples:**
```bash
# Basic screenshot
python skills/media/scripts/screenshot.py https://example.com /tmp/shot.png

# Full page capture
python skills/media/scripts/screenshot.py https://example.com /tmp/shot.png --full-page

# Custom viewport
python skills/media/scripts/screenshot.py https://example.com /tmp/shot.png --width 1920 --height 1080
```

**Options:**
- `--full-page` - Capture entire scrollable page
- `--width N` - Viewport width (default: 1280)
- `--height N` - Viewport height (default: 720)
- `--wait N` - Wait N ms after page load (default: 1000)

## Video Recording (Linux only)

Records true constant-framerate H.264 MP4 by launching a headless Xvfb
virtual display, rendering the page in Chromium on that display, and capturing
with `ffmpeg x11grab`. This is the documented fallback method and works on any
Linux laptop, with or without a GPU.

Do not use Playwright `record_video_dir` or per-frame screenshot loops: both
produce unwatchable output.

### Record a webpage

```bash
python skills/media/scripts/record_video.py <url> <output.mp4>
```

**Examples:**
```bash
# Record 5 seconds of a webpage at 1920x1080
python skills/media/scripts/record_video.py https://example.com /tmp/demo.mp4

# Record 10 seconds at 1280x720, 30fps
python skills/media/scripts/record_video.py https://example.com /tmp/demo.mp4 \
    --duration 10 --width 1280 --height 720 --fps 30

# Record with background audio muxed in
python skills/media/scripts/record_video.py https://example.com /tmp/demo.mp4 \
    --duration 8 --audio /path/to/music.mp3
```

### Record the existing display (no browser)

For capturing apps already running on the user's display:

```bash
python skills/media/scripts/record_video.py --screen /tmp/desktop.mp4 --duration 10
```

Requires `DISPLAY` to be set in the environment. Geometry is auto-detected via
`xdpyinfo`; `--width`/`--height` are used as a fallback.

**Options:**
- `--duration N` - Recording duration in seconds (default: 5)
- `--width N` - Browser window width (default: 1920)
- `--height N` - Browser window height (default: 1080)
- `--fps N` - Frame rate (default: 30)
- `--audio FILE` - Optional audio file to mux into the video
- `--screen` - Record `$DISPLAY` instead of launching a browser on Xvfb

**Output:** Always H.264 MP4. If you pass a non-`.mp4` extension, it is
silently changed with a notice on stderr.

**Requirements:**
- Linux (macOS is not currently supported: the script exits with an error)
- `ffmpeg`
- `xvfb` and `xdotool` (for URL recording)
- Playwright Chromium (installed via `playwright install chromium`) or a
  system Chromium/Chrome binary on `PATH`

**Concurrency:** only one recording can run at a time. An advisory lock at
`/tmp/claude_video_recording.lock` prevents parallel runs from stomping on
each other and from spawning Chromium pileups.

## Sending to User

After capturing, send to the user:

```bash
python utils/send_to_telegram.py --user USER_ID --photo /tmp/shot.png
python utils/send_to_telegram.py --user USER_ID --video /tmp/demo.mp4
```
