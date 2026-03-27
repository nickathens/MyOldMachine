# Voice Processing

Speech-to-text transcription using Whisper. Part of the Voice Mode pipeline (voice in, voice out).

## Voice Mode

When a user sends a voice message, the bot automatically:
1. Transcribes the voice via Whisper (multilingual: English, Greek, 90+ languages)
2. Sends the transcription to the LLM
3. LLM responds with text
4. Text is converted to speech via TTS (see `text-to-speech` skill)
5. Both text and voice reply are sent back

This is fully automatic in bot.py. No manual steps needed.

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
python skills/voice/scripts/transcribe.py /tmp/voice.ogg --language el
python skills/voice/scripts/transcribe.py /tmp/voice.ogg --language en

# Transcribe any audio format
python skills/voice/scripts/transcribe.py /path/to/audio.mp3
python skills/voice/scripts/transcribe.py /path/to/audio.wav
```

**Supported formats:** mp3, wav, ogg, m4a, flac, webm

**Model:** Whisper medium (multilingual, runs on CPU). Auto-detects language. Supports English, Greek, and 90+ other languages.

**Note:** First run downloads the model (~1.5GB). Transcription takes 10-30 seconds per audio clip depending on length.
