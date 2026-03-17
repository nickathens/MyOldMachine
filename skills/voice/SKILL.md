# Voice Processing

Speech-to-text transcription using Whisper.

## Transcribe Audio

Convert audio/voice messages to text.

```bash
python skills/voice/scripts/transcribe.py <audio_file>
```

**Examples:**
```bash
# Transcribe a voice message (auto-detects language)
python skills/voice/scripts/transcribe.py /tmp/voice.ogg

# Transcribe with a language hint
python skills/voice/scripts/transcribe.py /tmp/voice.ogg --language en
python skills/voice/scripts/transcribe.py /tmp/voice.ogg --language el

# Transcribe any audio format
python skills/voice/scripts/transcribe.py /path/to/audio.mp3
python skills/voice/scripts/transcribe.py /path/to/audio.wav
```

**Supported formats:** mp3, wav, ogg, m4a, flac, webm

**Model:** Whisper medium (multilingual, runs on CPU). Auto-detects language. Supports 90+ languages.

**Note:** First run downloads the model (~1.5GB). Transcription takes 10-30 seconds per clip depending on length.
