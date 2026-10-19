# Video Watch

Give the assistant the ability to watch any video. Downloads with yt-dlp, extracts auto-scaled frames with ffmpeg, pulls a timestamped transcript (native captions first, Whisper API or local CLI fallback), prints frame paths. Read each frame path to see the images, combine with the transcript, answer the user.

It also breaks a reference video down: every shot with its camera move and look, the music's tempo and the moments it builds and hits (see Breakdown, below).

## When to use

- User pastes a video URL (YouTube, Vimeo, X, TikTok, Twitch clip, most yt-dlp-supported sites) and asks about it
- User points at a local video file (`.mp4`, `.mov`, `.mkv`, `.webm`, etc.)
- User asks you to analyze a video, summarize a video, or describe what happens in a video
- User sends a reference and asks how it is built (pacing, cutting, camera, colour, the music under it) or wants something to "feel like this": use the breakdown

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

## Breakdown: how a reference video is built

When the question is how a video is made rather than what it says ("make it feel like this", "what is the pacing of this ad", "how does this trailer cut to the music"), run the breakdown. It measures; the watch report above describes. For a full read of a reference, run both.

```bash
~/.venvs/watch/bin/python skills/watch/scripts/breakdown.py "<url-or-file>" [--start T] [--end T] [--out-dir DIR]
```

It runs in its own Python environment, never in the bot's: the bot's `.venv` is off limits, the same rule colorgrade and postproduction follow. Set it up once with `python3 -m venv ~/.venvs/watch && ~/.venvs/watch/bin/pip install scenedetect librosa` (PySceneDetect brings OpenCV and numpy; about 520 MB on the Mac mini, 680 MB measured on Linux). Run from any other Python, it names whatever is missing and the line that sets up or uses that environment, and stops. It prints a markdown report and leaves in its working folder:

- `sheet_1fps.jpg`: up to 64 frames spread across the stretch, 8 to a row. Read it first.
- `sheet_scenes.jpg`: the middle frame of each shot (every Nth shot past 48), labelled with its time in seconds from the first frame analysed. When that frame is not at 0:00 (a range, or a picture that starts after the sound), the report's sheet line says where it is in the source.
- `report.json`: every number, every beat time included.
- `proxy.mp4` (the analysis copy, at most 640 px wide) and `audio.wav` (the loudness normalised mono mix the music was measured on).

What it measures:

- **Shots:** every cut, with start and length, and the mean, shortest and longest shot.
- **Camera, per shot:** push in, pull out, move left, right, up or down, roll/orbit, or static, with a pace (still, slow, medium, fast), from optical flow between frames sampled inside the shot.
- **Look, per shot:** brightness, contrast, the share of the frame near black, and the four main colours.
- **Music:** tempo, the beats, the mean shot length in beats, and up to three moments where it builds and hits.
- **Flash frames:** frames much brighter than the frames on both sides of them.
- **The picture inside the frame:** letterbox bars, and in an upright frame the app bars of a phone screen recording, are found and cropped away before anything is measured. A phone video stored on its side with a rotation flag is read, and reported, upright.
- **Picture and sound on one clock, the file's:** a sound track that starts after the picture (screen recordings, some downloads), or a picture that starts after the sound, keeps its place, so a cut, beat or hit at 0:12 in the report is at 0:12 in a player.

`--start` and `--end` pick a section. One run reads at most 15 minutes, so a longer video needs a section. Every time in the report stays a time in the source, and a shot the range cuts into is marked and left out of the mean, shortest and longest.

`--out-dir` must be a new or empty folder, or one an earlier breakdown made: a run deletes and rewrites `audio.wav`, `report.json` and its other files there, so it refuses a folder holding anything else.

What to trust, measured on four 1080p ads and fifteen excerpts of film scores and songs (2026-10-18):

- **Cuts:** on a 54 s ad, 24 of 25 cuts were real, and a second detector (ffmpeg's scene filter) agreed on all 24; its only other hit was the single black frame that ends the spot. The 25th was a large graphic disc wiping across one shot. A shot under 15 frames, like that last black frame, merges into its neighbour, and a slow dissolve can go unseen.
- **Camera:** of 8 shots checked by eye, 6 matched the picture (push ins, pans, still shots), 1 looked right but was hard to confirm from stills, and 1 read animated graphics as camera moves. It reads how the whole picture moves, so moving graphics (an end card of tumbling 3D discs) read as camera moves.
- **Tempo:** on click tracks at known tempos it reads within 0.1 bpm, and on the four ads it matched essentia, a second, independent beat tracker, within 1.3%. It can still land on half, double or one and a half times the felt tempo, and music with no steady pulse still gets a number. Treat a tempo on rubato or orchestral music as unverified, and do not take a second tracker's agreement as proof: on clicks with no steady beat (random, or speeding up the whole time) the two trackers' beats lined up as well as essentia's own bar for a good read on six clips of seven and their tempos matched on two, and on one film score their tempos matched (153 bpm) while their beats lined up no better than chance. The report gives the beat count and where the beats run, and a tracker that lost the beat shows as "no reliable tempo".
- **Picture and sound shapes:** a phone video stored on its side with a rotation flag, a vertical 1080x1920 ad, 60 fps, a sound track that starts 0.75 s or 1 s after the picture, a picture that starts 0.25 s or 0.5 s after the sound (until the picture's own start was read, its cuts and flashes came out early by the gap where ffmpeg leaves that gap empty, as on the Mac; ffmpeg 6.1 and 7.0 fill it with copies of the first frame, so the analysis copy is now cut at that frame and reads the same on both; at 0.5 s and 120 bpm the gap is exactly one beat, so the first check could not see it), a sound track shorter or longer than the picture, a 0.3 s clip, and a path with spaces and accents all read correctly. One still landscape shot whose motion sits in a band across the middle was taken for a phone screen recording and cropped to the band until the app bar search was limited to upright frames; in an upright frame that mistake is still possible, and the report's Picture line says when a crop was made.
- **Flash frames:** a cut into a bright card, or a dissolve out of one, is not a flash. ReelMimic's own rule counted both, and they were its only two hits on the 54 s ad. A flash shorter than about 4 frames at 24 fps can fall between samples.
- **Library versions:** the tests pass on OpenCV 4.13 with librosa 0.11 and on what a fresh install brings today, OpenCV 5.0 with librosa 1.0.
- **Speed and size** (a 4 core 2015 i5 on Linux): 23 s for a 54 s 1080p ad (about two cores, 370 MB of memory); 4 min 15 s for the 15 minute maximum of 1080p footage with 297 shots (1.1 GB of memory; the working folder peaks at 614 MB while the music is measured, a float copy of the sound included, and keeps 210 MB).

Credit: the measuring functions are ReelMimic's (https://github.com/edenfunf/reelmimic, MIT, commit 9fb7a07c), copied verbatim with the licence in the script's header, and `tests/test_watch_breakdown.py` pins that block so it cannot drift by hand. The download, the range, the analysis copy, the flash rule, the sharper tempo and the report are this skill's.

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

1. **Native captions (free, preferred).** yt-dlp pulls manual or auto-generated subtitles when the source has them, chosen by the video's own language: a manual track in that language first, then the original speech recognition track (`el-orig` on a Greek video), then English. An automatic English track on a video in another language is YouTube's machine translation and is used last, labeled as such. The transcript line says which kind it read (for example `captions: automatic, original language (el-orig)`). A track that the default client refuses (HTTP 429) is fetched again through the `android_vr` client.
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
- **YouTube quality degrades** (missing formats, "formats may be missing" warnings, low-res only) → a stale yt-dlp is the first suspect; YouTube breaks old extractors within months. Update yt-dlp before debugging anything else. Current yt-dlp also needs a JavaScript runtime (node or deno) plus the `yt-dlp-ejs` package for full YouTube extraction — without them it silently falls back to degraded formats
- **Whisper request fails** → invalid key, rate limit, or 25 MB upload limit. Try `--whisper openai` if Groq failed, or `--whisper local`
- **Local whisper hangs/OOMs** → the CPU run is memory-capped in a systemd scope (default 6G via `WHISPER_MEM_MAX`), so a heavy model dies alone instead of taking down the bot. If a model gets killed at the cap, drop to a smaller one: `WATCH_LOCAL_WHISPER_MODEL=tiny python …/watch.py …`. Where `systemd-run` is unavailable (macOS, non-systemd Linux), models heavier than `medium` are refused on CPU, so use a smaller model or set an API key.

## What this skill does

- Runs yt-dlp locally to download the video and pull native captions
- Runs ffmpeg/ffprobe locally to extract frames as JPEGs and (when needed) mono 16kHz audio
- Sends extracted audio (not the video) to Groq or OpenAI Whisper API when needed; OR runs the local `whisper` CLI fully offline
- Writes downloaded video, frames, audio, transcript to a tmp working dir
- Reads/creates `~/.config/watch/.env` (mode 0600) for API keys
- For the breakdown, runs PySceneDetect, OpenCV, librosa and ffmpeg locally and writes its sheets, report and analysis copies to its working dir; nothing is uploaded

## What this skill does NOT do

- Does not upload the video itself — only extracted audio to an API, and only when captions missing AND an API backend is selected
- Does not upload anything when using `--whisper local` — transcription runs entirely on this machine
- Does not access platform accounts (no login, no cookies, no posting)
- Does not log API keys to stdout, stderr, or output files
- Does not persist anything outside the working dir and `~/.config/watch/.env` (the breakdown's libraries are installed once, in `~/.venvs/watch`)

Source: https://github.com/bradautomates/claude-video (MIT, by bradautomates)

The breakdown's measuring functions in `breakdown.py` are from https://github.com/edenfunf/reelmimic (MIT, ReelMimic contributors), commit 9fb7a07c.
