# Text-to-Speech

Generate spoken audio from text. Part of the Voice Mode pipeline (voice in, voice out).

## DEFAULTS (machine-wide, applies to ALL users)

For any narration, voiceover, audiobook, treatment read, or "send me a voice message saying..." task, use **Chatterbox TTS** with the locked-in voice clones below. Do NOT suggest or fall back to espeak / macOS `say` / Piper for narration unless the user explicitly asks for a quick robotic read or the Chatterbox venv is unavailable.

### English narration — Attenborough clone

- Reference clip: `/Users/coocooai/MyOldMachine/data/chatterbox/refs/attenborough_v2_30s.wav`
- Render script: `/Users/coocooai/MyOldMachine/data/chatterbox/render.py`
- Venv: `/Users/coocooai/MyOldMachine/data/chatterbox/venv`

```bash
/Users/coocooai/MyOldMachine/data/chatterbox/venv/bin/python \
  /Users/coocooai/MyOldMachine/data/chatterbox/render.py \
  --text /path/to/script.txt \
  --ref /Users/coocooai/MyOldMachine/data/chatterbox/refs/attenborough_v2_30s.wav \
  --out /path/to/out.wav \
  --exaggeration 0.5 --cfg 0.28 --gap-ms 1100 --max-chars 120
```

### Greek narration — Tzoumas clone

- Reference clip: `/Users/coocooai/MyOldMachine/data/chatterbox/refs/tzoumas_30s.wav`
- Render script: `/Users/coocooai/MyOldMachine/data/chatterbox/render_mtl.py` (multilingual)
- Venv: same as above

```bash
/Users/coocooai/MyOldMachine/data/chatterbox/venv/bin/python \
  /Users/coocooai/MyOldMachine/data/chatterbox/render_mtl.py \
  --text /path/to/script.txt \
  --ref /Users/coocooai/MyOldMachine/data/chatterbox/refs/tzoumas_30s.wav \
  --out /path/to/out.wav \
  --lang el \
  --exaggeration 0.5 --cfg 0.28 --gap-ms 1100 --max-chars 120
```

### Post-process (mandatory for theatrical pacing)

After rendering, slow the audio down by 10% with ffmpeg before delivering:

```bash
ffmpeg -y -i out.wav -filter:a "atempo=0.90" out_slow.wav
ffmpeg -y -i out_slow.wav -b:a 192k out_slow.mp3
```

### Language routing

- Detect the script's language. If it's Greek (any Greek characters dominate), use Tzoumas + `render_mtl.py --lang el`.
- Otherwise default to Attenborough + `render.py`.
- For other languages, use `render_mtl.py` with the appropriate `--lang` code (`es`, `fr`, `de`, etc.) and pick whichever reference clip fits the timbre brief better — default Attenborough if unspecified.

### Why these defaults

- Locked in by the owner on 2026-05-27 after side-by-side comparison.
- Attenborough English was called "perfection" by the owner. Tzoumas Greek was approved as the Greek equivalent.
- Older voices (Piper Alan, Piper Rapunzelina, Kokoro) were deleted on the same date — do not try to install or use them.

### Hard rules

- **Never** modify the chatterbox venv at `/Users/coocooai/MyOldMachine/data/chatterbox/venv`. `setuptools` must stay `<81` in that venv for the perth watermarker to load. If the venv breaks, ask the user before reinstalling.
- **Never** suggest alternative English or Greek voices unless the user explicitly asks.
- First model download (`ChatterboxTTS.from_pretrained` / `ChatterboxMultilingualTTS.from_pretrained`) is a few GB. After that it's cached and runs fast on MPS (Apple Silicon GPU).

## Quick alternatives (only when explicitly requested)

When the user asks for a fast robotic read, or just a placeholder, these are fine:

```bash
# espeak-ng (Linux/macOS) — instant, robotic
espeak-ng "Hello world" -w /tmp/speech.wav

# macOS 'say' — better quality on Mac, system voices
say "Hello world" -o /tmp/speech.aiff
say -v Samantha "Hello world" -o /tmp/speech.aiff
say -v '?'   # list voices
```

## Output Formats

- `.wav` — raw audio (Chatterbox native output)
- `.mp3` — compressed audio via ffmpeg
- `.ogg` — OGG Opus for Telegram voice messages: `ffmpeg -i in.wav -c:a libopus out.ogg`

## Voice Mode (Automatic)

When a user sends a voice message, the bot automatically transcribes it via Whisper (see `voice` skill), gets a text response, and currently replies via espeak-ng for speed. The Chatterbox defaults above apply to any **explicit** TTS/narration/voiceover request, not the fast auto voice-reply path (which prioritises latency).

## Examples that trigger the Chatterbox defaults

- "Read this treatment aloud"
- "Create audio narration for this script"
- "Send me a voice message saying..."
- "Generate voiceover for this scene"
- "Narrate this in Greek"
- Anything where the output is meant to feel like a finished read.
