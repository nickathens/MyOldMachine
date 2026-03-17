#!/usr/bin/env python3
"""
Voice message transcription using Whisper medium multilingual model.
Usage: python transcribe.py <audio_file> [--language LANG]
"""
import sys
import whisper

if len(sys.argv) < 2:
    print("Usage: python transcribe.py <audio_file> [--language LANG]")
    sys.exit(1)

audio_path = sys.argv[1]

# Parse optional --language flag
language = None
if "--language" in sys.argv:
    idx = sys.argv.index("--language")
    if idx + 1 < len(sys.argv):
        language = sys.argv[idx + 1]

# Load multilingual model on CPU
model = whisper.load_model("medium", device="cpu")

transcribe_opts = {}
if language:
    transcribe_opts["language"] = language

result = model.transcribe(audio_path, **transcribe_opts)
print(result["text"].strip())
