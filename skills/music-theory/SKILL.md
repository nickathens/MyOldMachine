# Music Theory Analysis

Analyze musical content: detect chords, keys, intervals, and generate notation.

## Usage

```bash
# Analyze a MIDI file for key and chords
python skills/music-theory/scripts/theory.py analyze input.mid

# Detect key signature
python skills/music-theory/scripts/theory.py key input.mid

# Extract chord progression
python skills/music-theory/scripts/theory.py chords input.mid

# Identify interval between two notes
python skills/music-theory/scripts/theory.py interval C4 G4

# Get notes in a chord
python skills/music-theory/scripts/theory.py chord-notes "Cmaj7"

# Get notes in a scale
python skills/music-theory/scripts/theory.py scale C major

# Transpose chord progression
python skills/music-theory/scripts/theory.py transpose-chords "Cmaj Fmaj Gmaj" --semitones 5
```

## Chord Names

Supported chord formats:
- Major: C, Cmaj, CM
- Minor: Cm, Cmin
- Seventh: C7, Cmaj7, Cm7
- Diminished: Cdim, Cdim7
- Augmented: Caug, C+
- Suspended: Csus2, Csus4

## Scale Types

major, minor, harmonic-minor, melodic-minor, dorian, phrygian, lydian, mixolydian, locrian, chromatic, whole-tone, pentatonic-major, pentatonic-minor, blues

## Notes

- Uses music21 library for analysis
- Key detection uses Krumhansl-Schmuckler algorithm
- Chord detection samples at regular intervals
- Works with MIDI and MusicXML files
