# Text-to-Speech

Generate spoken audio from text. Part of the Voice Mode pipeline (voice in, voice out).

## Tools

- **espeak-ng** - Fast, lightweight TTS (cross-platform, installed by default)
- **say** - macOS built-in TTS (higher quality on Mac)
- **piper-tts** - Neural TTS with natural-sounding voices (optional, pip install)

## Commands

```bash
# espeak-ng (Linux/macOS)
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
piper --model en_US-lessac-high --output_file /tmp/speech.wav < /tmp/text.txt
piper --model el_GR-rapunzelina-medium --output_file /tmp/speech.wav < /tmp/text.txt

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
4. Converts response to speech via espeak-ng (or Piper if available)
5. Sends both text and voice reply

This is handled automatically by bot.py. No manual intervention needed.

## Output Formats

- `.wav` - Raw audio (default)
- `.mp3` - Compressed audio (via ffmpeg)
- `.ogg` - OGG Opus (Telegram voice messages, via ffmpeg)

## Examples

"Convert this text to speech"
"Generate voiceover for this script"
"Read this text aloud"
"Send me a voice message saying..."
"Create audio narration"

## Notes

- espeak-ng: Instant, works offline, robotic voice
- macOS say: Better quality on Mac, multiple voices available
- Piper: Best quality, neural voices, ~300MB per voice model
- Convert WAV/AIFF to MP3/OGG with ffmpeg for smaller files
