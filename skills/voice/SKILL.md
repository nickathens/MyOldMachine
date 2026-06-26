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

**Model:** Whisper medium (multilingual, runs on CPU). Auto-detects language. Supports English, Greek, and 90+ other languages. Override with `--model tiny|base|small|medium`.

**Note:** First run downloads the model (~1.5GB). On CPU the model loads in fp32 and peaks around 4.8GB resident. Transcription takes 10-30 seconds per audio clip depending on length.

**Memory safety (load-bearing):** The whisper run is placed inside a memory-capped systemd user scope (`systemd-run --user --scope`, ceiling set by `WHISPER_MEM_MAX`, default 6G) whose cgroup lives outside the bot service, so a heavy model that balloons is killed alone and can never exhaust system RAM and OOM the machine. Where `systemd-run` is unavailable (macOS, non-systemd Linux), models heavier than `medium` are refused rather than risking the box. This complements the skill_hooks RAM gate (`min_ram_gb` in deps.json), which blocks the skill before launch but cannot bound a process that grows after it starts.
