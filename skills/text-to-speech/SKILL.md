# Text-to-Speech

Generate spoken audio from text.

## Tools

- **espeak-ng** - Fast, lightweight TTS (cross-platform)
- **say** - macOS built-in TTS

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

# Convert to MP3 (requires ffmpeg)
ffmpeg -i /tmp/speech.wav /tmp/speech.mp3
ffmpeg -i /tmp/speech.aiff /tmp/speech.mp3
```

## Examples

"Convert this text to speech"
"Generate voiceover for this script"
"Read this text aloud"
"Create audio narration"

## Notes

- espeak-ng: Instant, works offline, robotic voice
- macOS say: Better quality on Mac, multiple voices available
- Convert WAV/AIFF to MP3 with ffmpeg for smaller files
