# Sheet Music

Convert MIDI files to sheet music (PDF or PNG) using LilyPond.

## Usage

```bash
# Convert MIDI to PDF sheet music
python skills/sheet-music/scripts/midi2sheet.py input.mid

# Output as PNG image
python skills/sheet-music/scripts/midi2sheet.py input.mid --png

# Add a title
python skills/sheet-music/scripts/midi2sheet.py input.mid --title "My Composition"

# Specify output path
python skills/sheet-music/scripts/midi2sheet.py input.mid --output sheet.pdf
```

## Workflow

Combine with audio-to-midi for full audio to sheet music pipeline:

```bash
# Step 1: Transcribe audio to MIDI
python skills/audio-to-midi/scripts/audio2midi.py recording.mp3

# Step 2: Convert MIDI to sheet music
python skills/sheet-music/scripts/midi2sheet.py recording_basic_pitch.mid
```

## Notes

- Uses LilyPond for professional music engraving
- Output is publication-quality
- Complex polyphonic MIDI may require manual cleanup
- Multi-track MIDI will show all parts
