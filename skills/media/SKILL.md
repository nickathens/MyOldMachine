# Media Capture

Screenshots and video recording of web pages.

## Screenshot

Capture a screenshot of any URL.

```bash
python ~/claude-telegram-bot/skills/media/scripts/screenshot.py <url> <output.png>
```

**Examples:**
```bash
# Basic screenshot
python ~/claude-telegram-bot/skills/media/scripts/screenshot.py https://example.com /tmp/shot.png

# Full page capture
python ~/claude-telegram-bot/skills/media/scripts/screenshot.py https://example.com /tmp/shot.png --full-page

# Custom viewport
python ~/claude-telegram-bot/skills/media/scripts/screenshot.py https://example.com /tmp/shot.png --width 1920 --height 1080
```

**Options:**
- `--full-page` - Capture entire scrollable page
- `--width N` - Viewport width (default: 1280)
- `--height N` - Viewport height (default: 720)
- `--wait N` - Wait N ms after page load (default: 1000)

## Video Recording

Record a video of a webpage (captures animations, transitions).

```bash
python ~/claude-telegram-bot/skills/media/scripts/record_video.py <url> <output.webm>
```

**Examples:**
```bash
# Record 5 seconds
python ~/claude-telegram-bot/skills/media/scripts/record_video.py https://nick-athens.com /tmp/demo.webm

# Record 10 seconds at 4K
python ~/claude-telegram-bot/skills/media/scripts/record_video.py https://nick-athens.com /tmp/demo.webm --duration 10 --width 3840 --height 2160
```

**Options:**
- `--duration N` - Recording duration in seconds (default: 5)
- `--width N` - Viewport width (default: 1920)
- `--height N` - Viewport height (default: 1080)

## Sending to User

After capturing, send to the user:

```bash
python ~/claude-telegram-bot/utils/send_to_telegram.py --user USER_ID --photo /tmp/shot.png
python ~/claude-telegram-bot/utils/send_to_telegram.py --user USER_ID --video /tmp/demo.webm
```
