# Video Watch

Give the assistant the ability to watch any video. Downloads with yt-dlp, extracts auto-scaled frames with ffmpeg, pulls a timestamped transcript (native captions first, Whisper API or local CLI fallback), prints frame paths. Read each frame path to see the images, combine with the transcript, answer the user.

## When to use

- User pastes a video URL (YouTube, Vimeo, X, TikTok, Twitch clip, most yt-dlp-supported sites) and asks about it
- User points at a local video file (`.mp4`, `.mov`, `.mkv`, `.webm`, etc.)
- User asks you to analyze a video, summarize a video, or describe what happens in a video

## How to invoke

**Step 1 — parse user input.** Separate the video source (URL or path) from any question.

**Step 2 — run the watch script.**

```bash
python skills/watch/scripts/watch.py "<source>"
```

Optional flags:
- `--start T` / `--end T` — focus on a section. `SS`, `MM:SS`, or `HH:MM:SS`
- `--max-frames N` — cap frame count (default 80, hard max 100)
- `--resolution W` — frame width in px (default 512; bump to 1024 only if reading on-screen text)
- `--fps F` — override auto-fps (clamped to 2 fps max)
- `--out-dir DIR` — keep working files somewhere specific (default: tmp dir)
- `--whisper groq|openai|local` — force a Whisper backend (default: Groq → OpenAI → local CLI)
- `--no-whisper` — disable Whisper fallback (frames-only if no captions)

**Step 3 — Read every frame path the script lists.** The Read tool renders JPEGs as images. Read all frames in a single message (parallel calls) so you see them together. Frames are chronological with `t=MM:SS` timestamps.

**Step 4 — answer the user** using frames + transcript. Cite timestamps when answering specific questions.

**Step 5 — clean up.** If the user won't ask follow-ups, delete the working dir with `rm -rf <dir>`.

## Frame budgets

Token cost scales with frames. The script targets:
- ≤30s → ~30 frames
- 30s-1min → ~40 frames
- 1-3min → ~60 frames
- 3-10min → ~80 frames
- >10min → 100 frames sparsely (warning printed — use `--start`/`--end` instead)

## Focusing on a section (denser sampling)

Pass `--start` / `--end` when the user asks about a moment ("around 2:30", "the intro", "the last 30 seconds"). Focused mode budgets denser (still capped at 2 fps):
- ≤5s → 2 fps, up to 10 frames
- 5-15s → 2 fps, up to 30 frames
- 15-30s → ~2 fps, up to 60 frames
- 30-60s → ~1.3 fps, up to 80 frames
- 60-180s → ~0.6 fps, 100 frames capped

Always use focused mode for videos longer than 10 minutes when the question is about a specific part. Transcript is auto-filtered to the same range.

```bash
# Last 10 seconds of a 1 minute video
python skills/watch/scripts/watch.py video.mp4 --start 50 --end 60

# Zoom into 2:15 → 2:45 at 3 fps
python skills/watch/scripts/watch.py "$URL" --start 2:15 --end 2:45 --fps 3
```

## Transcription

Auto-detected priority: **captions → Groq → OpenAI → local CLI**.

1. **Native captions (free, preferred).** yt-dlp pulls manual or auto-generated subtitles when the source has them.
2. **Whisper API fallback.** If no captions and a key is set, the script extracts mono 16kHz mp3 (~480 kB/min) and uploads to whichever API has a key:
   - **Groq** (`whisper-large-v3`) — fastest, cheapest. Key at https://console.groq.com/keys
   - **OpenAI** (`whisper-1`) — fallback. Key at https://platform.openai.com/api-keys
3. **Local CLI fallback (offline, free).** If no API key is set but the `whisper` binary is on PATH, transcription runs locally. Defaults: model `base`, device `cpu`. Override with `WATCH_LOCAL_WHISPER_MODEL` and `WATCH_LOCAL_WHISPER_DEVICE` env vars. On CPU the run is placed inside a memory-capped systemd scope (`WHISPER_MEM_MAX`, default 6G) so a heavy model can't exhaust system RAM and OOM the machine.

Keys live in `~/.config/watch/.env` (mode 0600). The local CLI is installed automatically as part of this skill's pip deps (`openai-whisper`), so transcription works out of the box even with no API keys.

**Local CPU transcription is slow.** Roughly 5-10× real-time on a typical CPU with the `base` model — a 5-minute clip can take 30+ minutes. **Always pair `--whisper local` (or videos with no captions) with `--start`/`--end` to focus on the section you care about.** The script trims the audio before transcribing, so a focused range only transcribes that range.

`--device cuda` requires a PyTorch-supported GPU (compute capability 7.0+ on current PyTorch builds). Older GPUs will fail to initialize CUDA — stay on CPU.

## Token efficiency

- 80 frames at 512px wide ≈ 50-80k image tokens
- Transcript ≈ a few thousand tokens for a 10-minute video
- `--resolution 1024` quadruples image tokens per frame — only use when needed

If you already watched a video this session and the user asks a follow-up, **do not re-run the script** — answer from frames already in context.

## Failure modes

- **`setup.py --check` non-zero** → run `python skills/watch/scripts/setup.py` to scaffold `~/.config/watch/.env`
- **No transcript available** → captions missing AND (no Whisper backend OR all backends failed). Proceed frames-only and tell the user
- **Long video warning printed** → acknowledge it; offer to re-run focused on a specific section via `--start`/`--end`
- **Download fails** → yt-dlp's error goes to stderr. If it's login-required or region-locked, tell the user plainly; do not retry
- **Whisper request fails** → invalid key, rate limit, or 25 MB upload limit. Try `--whisper openai` if Groq failed, or `--whisper local`
- **Local whisper hangs/OOMs** → the CPU run is memory-capped in a systemd scope (default 6G via `WHISPER_MEM_MAX`), so a heavy model dies alone instead of taking down the bot. If a model gets killed at the cap, drop to a smaller one: `WATCH_LOCAL_WHISPER_MODEL=tiny python …/watch.py …`. Where `systemd-run` is unavailable (macOS, non-systemd Linux), models heavier than `medium` are refused on CPU, so use a smaller model or set an API key.

## What this skill does

- Runs yt-dlp locally to download the video and pull native captions
- Runs ffmpeg/ffprobe locally to extract frames as JPEGs and (when needed) mono 16kHz audio
- Sends extracted audio (not the video) to Groq or OpenAI Whisper API when needed; OR runs the local `whisper` CLI fully offline
- Writes downloaded video, frames, audio, transcript to a tmp working dir
- Reads/creates `~/.config/watch/.env` (mode 0600) for API keys

## What this skill does NOT do

- Does not upload the video itself — only extracted audio to an API, and only when captions missing AND an API backend is selected
- Does not upload anything when using `--whisper local` — transcription runs entirely on this machine
- Does not access platform accounts (no login, no cookies, no posting)
- Does not log API keys to stdout, stderr, or output files
- Does not persist anything outside the working dir and `~/.config/watch/.env`

Source: https://github.com/bradautomates/claude-video (MIT, by bradautomates)
