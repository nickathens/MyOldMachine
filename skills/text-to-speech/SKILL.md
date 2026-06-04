# Text-to-Speech

Generate spoken audio from text. Part of the Voice Mode pipeline (voice in, voice out).

## Choosing a voice: narration vs. quick read

Match the engine to the job:

- **Finished narration** — voiceovers, audiobooks, treatment reads, "send me a voice
  message saying...", anything meant to feel like a polished read. If a high-quality
  neural voice is configured on this machine (a voice clone such as Chatterbox, or a
  good Piper model), prefer it over the built-in robotic engines. The specifics of any
  such setup — which voice, reference clips, render command, language routing, post-
  processing — are configured per machine and per user, **not** hardcoded in this shared
  skill. Before falling back to a robotic engine, check the user's private skill overlay
  / `skill_settings.json` note or a local config for a configured narration voice.
- **Quick read** — placeholders, fast drafts, low-latency replies. Use the built-in
  engines (`espeak-ng`, macOS `say`). They are instant and always available.

If no high-quality voice is configured on the machine, use the built-in engines below.

## Tools

- **espeak-ng** — Fast, lightweight TTS (cross-platform, installed by default)
- **say** — macOS built-in TTS (higher quality on Mac)
- **piper-tts** — Neural TTS with natural-sounding voices (optional, `pip install`)
- **Voice-clone engines** (e.g. Chatterbox) — Highest quality; clone a voice from a
  short reference clip. Heavier to set up; configured per machine, not shipped here.

## Commands

```bash
# espeak-ng (Linux/macOS) — instant, robotic
espeak-ng "Hello world" -w /tmp/speech.wav
espeak-ng -v en-us "Hello world" -w /tmp/speech.wav

# List espeak voices
espeak-ng --voices

# macOS 'say' command
say "Hello world" -o /tmp/speech.aiff
say -v Samantha "Hello world" -o /tmp/speech.aiff

# List macOS voices
say -v '?'

# Piper TTS (if installed: pip install piper-tts)
piper --model <voice-model> --output_file /tmp/speech.wav < /tmp/text.txt

# Convert to MP3 (requires ffmpeg)
ffmpeg -i /tmp/speech.wav /tmp/speech.mp3
ffmpeg -i /tmp/speech.aiff /tmp/speech.mp3

# Convert to OGG Opus (for Telegram voice messages)
ffmpeg -i /tmp/speech.wav -c:a libopus /tmp/speech.ogg
```

## Voice Mode (Automatic)

When a user sends a voice message, the bot automatically:
1. Transcribes the voice via Whisper (see `voice` skill)
2. Sends transcription to the LLM
3. Gets text response
4. Converts the response to speech for a fast reply
5. Sends both text and voice reply

The automatic voice-reply path prioritises latency, so it uses a built-in engine even
when a high-quality narration voice is configured. The narration guidance above applies
to **explicit** TTS / voiceover / narration requests, not this fast auto-reply path.

## Output Formats

- `.wav` — Raw audio (default)
- `.mp3` — Compressed audio (via ffmpeg)
- `.ogg` — OGG Opus (Telegram voice messages, via ffmpeg)

## Examples

"Convert this text to speech"
"Generate voiceover for this script"
"Read this text aloud"
"Send me a voice message saying..."
"Create audio narration"

## Notes

- espeak-ng: Instant, works offline, robotic voice
- macOS say: Better quality on Mac, multiple voices available
- Piper: Natural neural voices, ~300MB per voice model
- Voice-clone engines (e.g. Chatterbox): highest quality, clone a voice from a short
  reference clip; heavier to set up, configured per machine
- Convert WAV/AIFF to MP3/OGG with ffmpeg for smaller files
