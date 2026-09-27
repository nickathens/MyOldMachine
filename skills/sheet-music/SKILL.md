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

# Loose playing: tidy to eighths; already exact MIDI: no tidy
python skills/sheet-music/scripts/midi2sheet.py input.mid --grid 8
python skills/sheet-music/scripts/midi2sheet.py input.mid --grid 0

# Force the key signature (sharps, negative for flats, :1 for minor); detected otherwise
python skills/sheet-music/scripts/midi2sheet.py input.mid --key -3:1
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

- Uses LilyPond (midi2ly, then lilypond) for engraving
- Played MIDI, and everything audio-to-midi makes, sits a little off the grid. Handed to midi2ly raw, a steady scale engraved as notes tied to 128ths with slivers of rest between them (seen 2026-09-27). So the timing is tidied first: onsets snap to 1/16 notes (`--grid`), and a note runs on to the next onset when the gap is shorter than one grid step. A steady played scale then engraves as plain quarters
- The tidy cannot fix tempo drift (a take that speeds up or slows down against the written tempo); such a take still comes out with odd durations
- The key signature is detected with music21 unless `--key` is given, so accidentals are written the key's way
- Complex polyphonic MIDI may require manual cleanup
- Multi-track MIDI will show all parts; a PNG longer than a page is reported page by page
