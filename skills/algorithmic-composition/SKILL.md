# Algorithmic Composition

Generate music programmatically. `scripts/compose.py` writes MIDI for five
things; anything beyond them is written as code with the libraries below.

## compose.py

```bash
C=skills/algorithmic-composition/scripts/compose.py

python $C melody   --root D --scale dorian --bars 16 -o melody.mid
python $C chords   --root C --progression jazz_251 --bars 8 -o chords.mid
python $C arpeggio --root A --chord minor7 --bars 4 -o arp.mid
python $C drums    --style electronic --bars 8 --tempo 128 -o drums.mid
python $C full     --root E --progression sad --style rock --bars 8 --seed 7 -o song.mid
```

Options (an unknown name is an error, it no longer falls back quietly):

- `--root`: C, F#, Bb and so on
- `--scale` (melody): major, minor, dorian, phrygian, lydian, mixolydian, locrian, harmonic_minor, melodic_minor, pentatonic_major, pentatonic_minor, blues, whole_tone, chromatic
- `--progression` (chords, full): pop (I V vi IV), jazz_251 (ii V I), jazz_1625 (I vi ii V), blues (12 bar), classical (I IV V I), ambient (I iii vi IV), sad (i VI III VII), epic (i VII VI VII)
- `--chord` (arpeggio): major, minor, major7, minor7, dim; `--pattern` up, down, updown
- `--style` (drums, full): basic, rock, electronic, jazz
- `--bars`, `--tempo` (BPM), `--seed` (same seed, same notes), `-o`

How it behaves:

- Chords: one chord per bar, the progression repeating; a progression the
  bars do not complete (blues over 8 bars) gets a note saying so. Minor key
  progressions (sad, epic) take their roots from the natural minor scale, so
  sad in C is Cm, Ab, Eb, Bb.
- Melody: notes start on beats, stepwise mostly, within the scale. It does
  not follow the chords.
- Arpeggio: eighth notes in the chosen direction.
- full: chords (octave 3), melody (octave 5) and drums in one file, stamped
  with the tempo.

## Beyond the script

**music21** (analysis, counterpoint, voice leading, MusicXML), **mingus**
(theory helpers), **scamp** (expressive playback, notation), **pretty_midi**
and **mido** (MIDI). The skill installs mingus and pretty_midi; pip install
the others when a job needs them. Counterpoint, style pastiche or analysis of
an existing MIDI file means writing code with these; for key and chord
analysis the music-theory skill already has it.

## Output

MIDI files. Render to audio with the midi-to-audio skill (FluidSynth).
