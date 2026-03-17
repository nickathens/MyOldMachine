# Algorithmic Composition

Generate music programmatically using Python libraries.

## Usage

```bash
# Generate a melody
python skills/algorithmic-composition/scripts/compose.py melody --root C --scale major --bars 8 -o melody.mid

# Generate chord progression
python skills/algorithmic-composition/scripts/compose.py chords --root C --progression pop --bars 4 -o chords.mid

# Generate arpeggio
python skills/algorithmic-composition/scripts/compose.py arpeggio --root C --chord major --bars 4 --tempo 120 -o arp.mid

# Generate drum pattern
python skills/algorithmic-composition/scripts/compose.py drums --bars 4 --style jazz --tempo 120 -o drums.mid

# Full arrangement (chords + melody + drums)
python skills/algorithmic-composition/scripts/compose.py full --root C --scale major --progression pop --bars 8 -o full.mid
```

## Available Scales

major, minor, dorian, phrygian, lydian, mixolydian, locrian, harmonic_minor, melodic_minor, pentatonic_major, pentatonic_minor, blues, whole_tone, chromatic

## Available Progressions

pop (I-V-vi-IV), jazz_251 (ii-V-I), jazz_1625 (I-vi-ii-V), blues (12-bar), classical (I-IV-V-I), sad (i-VI-III-VII), epic (i-VII-VI-VII), ambient (I-iii-vi-IV)

## Drum Styles

basic, rock, electronic, jazz

## Output

- MIDI files (can convert to audio via midi-to-audio skill)
- Can analyze existing MIDI for key, tempo, structure
