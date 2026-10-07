#!/usr/bin/env python3
"""
Music theory analysis using music21.
"""

import argparse
import json
import re
import sys


def _name(text) -> str:
    """music21 writes flats as '-' (E-, B-): print them the way people read them."""
    return str(text).replace("-", "b")


# A chord symbol split into root, the text after it, and an optional bass.
CHORD_RE = re.compile(r"^([A-G](?:##|#|bb|b|--|-)?)(.*?)(?:/([A-G](?:##|#|bb|b|--|-)?))?$")

# The diatonic interval for each semitone count, so a transposition moves
# every root by the same number of letters (C F G up 3 is Eb Ab Bb).
SEMITONE_INTERVALS = ["P1", "m2", "M2", "m3", "M3", "P4", "A4", "P5", "m6", "M6", "m7", "M7"]


def _m21_note(name: str) -> str:
    """'Bb' -> 'B-': music21 reads a flat root only as '-'."""
    return name[0] + name[1:].replace("b", "-")


def _m21_chord(symbol: str) -> str:
    """A chord symbol as music21 parses it: 'Bbmaj7' read as B with a 'bmaj7'
    quality and failed, so flats in the root and the bass become '-'."""
    m = CHORD_RE.match(symbol.strip())
    if not m:
        return symbol
    root, rest, bass = m.groups()
    return _m21_note(root) + rest + (f"/{_m21_note(bass)}" if bass else "")


def cmd_analyze(args):
    """Full analysis of a music file."""
    from music21 import converter

    score = converter.parse(args.input)

    # Key analysis
    key = score.analyze('key')

    # Get all notes
    notes = list(score.flatten().notes)

    result = {
        "key": _name(key),
        "mode": key.mode,
        "correlation": round(key.correlationCoefficient, 3),
        "total_notes": len(notes),
        "duration_quarters": float(score.duration.quarterLength),
    }

    print(json.dumps(result, indent=2))


def cmd_key(args):
    """Detect key signature."""
    from music21 import converter

    score = converter.parse(args.input)
    key = score.analyze('key')

    result = {
        "key": _name(key),
        "tonic": _name(key.tonic.name),
        "mode": key.mode,
        "confidence": round(key.correlationCoefficient, 3),
        # key.relative for both modes: for a minor key the old code printed
        # key.parallel, so A minor's "relative" read A major, not C major.
        "relative": _name(key.relative),
        "parallel": _name(key.parallel),
    }

    print(json.dumps(result, indent=2))


def cmd_chords(args):
    """Extract chord progression."""
    from music21 import converter, harmony

    score = converter.parse(args.input)

    # Try to get existing chord symbols
    chords = list(score.flatten().getElementsByClass(harmony.ChordSymbol))

    if chords:
        progression = [_name(c.figure) for c in chords]
    else:
        # Analyze chords at regular intervals
        from music21 import chord as m21chord

        chordified = score.chordify()
        progression = []
        for c in chordified.flatten().getElementsByClass(m21chord.Chord):
            try:
                # Root and common name, joined here: pitchedCommonName joins
                # them with a dash ("C-major triad"), and the flat-sign
                # helper turned that dash into a flat ("Cbmajor triad")
                name = c.commonName
                if name and name != 'empty' and c.root() is not None:
                    progression.append(f"{_name(c.root().name)} {name}")
            except Exception:
                pass

    # Deduplicate consecutive
    deduped = []
    for c in progression:
        if not deduped or c != deduped[-1]:
            deduped.append(c)

    result = {
        "chords": deduped[:30],  # Limit output
        "count": len(deduped)
    }

    print(json.dumps(result, indent=2))


def cmd_interval(args):
    """Calculate interval between two notes."""
    from music21 import pitch, interval

    p1 = pitch.Pitch(args.note1)
    p2 = pitch.Pitch(args.note2)
    intv = interval.Interval(noteStart=p1, noteEnd=p2)

    result = {
        "from": _name(p1),
        "to": _name(p2),
        "name": intv.name,
        "simple_name": intv.simpleName,
        "semitones": intv.semitones,
        "direction": "up" if intv.semitones >= 0 else "down"
    }

    print(json.dumps(result, indent=2))


def cmd_chord_notes(args):
    """Get notes in a chord."""
    from music21 import harmony

    try:
        ch = harmony.ChordSymbol(_m21_chord(args.chord))
        notes = [_name(p.nameWithOctave) for p in ch.pitches]
        simple = [_name(p.name) for p in ch.pitches]

        result = {
            "chord": args.chord,
            "root": _name(ch.root().name),
            "bass": _name(ch.bass().name),
            "quality": ch.quality,
            "notes": simple,
            "notes_with_octave": notes
        }

        print(json.dumps(result, indent=2))

    except Exception as e:
        print(f"Error parsing chord '{args.chord}': {e}", file=sys.stderr)
        sys.exit(1)


def cmd_scale(args):
    """Get notes in a scale."""
    from music21 import scale, pitch

    # Built-in scale classes
    scale_classes = {
        'major': scale.MajorScale,
        'minor': scale.MinorScale,
        'harmonic-minor': scale.HarmonicMinorScale,
        'melodic-minor': scale.MelodicMinorScale,
        'dorian': scale.DorianScale,
        'phrygian': scale.PhrygianScale,
        'lydian': scale.LydianScale,
        'mixolydian': scale.MixolydianScale,
        'locrian': scale.LocrianScale,
        'chromatic': scale.ChromaticScale,
        'whole-tone': scale.WholeToneScale,
    }

    # Custom scales as intervals from the root, so every note is spelled from
    # the tonic. They used to be built from MIDI numbers, which spell with
    # sharps only: E-flat minor pentatonic came out E- F# G# B- C#.
    custom_scales = {
        'pentatonic-major': ['P1', 'M2', 'M3', 'P5', 'M6'],        # C D E G A
        'pentatonic-minor': ['P1', 'm3', 'P4', 'P5', 'm7'],        # C Eb F G Bb
        'blues': ['P1', 'm3', 'P4', 'd5', 'P5', 'm7'],             # C Eb F Gb G Bb
    }

    scale_type = args.scale_type.lower()
    tonic = pitch.Pitch(args.root)

    if scale_type in scale_classes:
        sc = scale_classes[scale_type](tonic)
        notes = [_name(p.name) for p in sc.getPitches(args.root + '3', args.root + '4')]
    elif scale_type in custom_scales:
        notes = [_name(tonic.transpose(i).name) for i in custom_scales[scale_type]]
        notes.append(_name(tonic.name))  # Add octave
    else:
        print(f"Unknown scale type: {scale_type}", file=sys.stderr)
        all_scales = list(scale_classes.keys()) + list(custom_scales.keys())
        print(f"Available: {', '.join(all_scales)}", file=sys.stderr)
        sys.exit(1)

    result = {
        "scale": f"{args.root} {args.scale_type}",
        "notes": notes
    }

    print(json.dumps(result, indent=2))


def cmd_transpose_chords(args):
    """Transpose a chord progression.

    Only the root and the bass move, by one diatonic interval, and the rest
    of each symbol is kept as written. music21's ChordSymbol.transpose respelt
    roots one by one: C F G up 3 came back Eb G# Bb."""
    from music21 import pitch

    chords = args.progression.split()
    transposed = []

    semis = args.semitones
    name = SEMITONE_INTERVALS[abs(semis) % 12]
    step = name if semis >= 0 else "-" + name

    def move(note_name):
        return _name(pitch.Pitch(_m21_note(note_name)).transpose(step).name)

    for ch_name in chords:
        m = CHORD_RE.match(ch_name)
        if not m:
            transposed.append(f"[{ch_name}?]")
            continue
        root, rest, bass = m.groups()
        transposed.append(move(root) + rest + (f"/{move(bass)}" if bass else ""))

    result = {
        "original": args.progression,
        "transposed": ' '.join(transposed),
        "semitones": args.semitones
    }

    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Music theory analysis")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Analyze
    p = subparsers.add_parser("analyze", help="Full analysis")
    p.add_argument("input", help="Input file (MIDI, MusicXML)")
    p.set_defaults(func=cmd_analyze)

    # Key
    p = subparsers.add_parser("key", help="Detect key")
    p.add_argument("input", help="Input file")
    p.set_defaults(func=cmd_key)

    # Chords
    p = subparsers.add_parser("chords", help="Extract chords")
    p.add_argument("input", help="Input file")
    p.set_defaults(func=cmd_chords)

    # Interval
    p = subparsers.add_parser("interval", help="Calculate interval")
    p.add_argument("note1", help="First note (e.g., C4)")
    p.add_argument("note2", help="Second note (e.g., G4)")
    p.set_defaults(func=cmd_interval)

    # Chord notes
    p = subparsers.add_parser("chord-notes", help="Get chord notes")
    p.add_argument("chord", help="Chord name (e.g., Cmaj7)")
    p.set_defaults(func=cmd_chord_notes)

    # Scale
    p = subparsers.add_parser("scale", help="Get scale notes")
    p.add_argument("root", help="Root note (e.g., C)")
    p.add_argument("scale_type", help="Scale type (major, minor, etc.)")
    p.set_defaults(func=cmd_scale)

    # Transpose chords
    p = subparsers.add_parser("transpose-chords", help="Transpose chord progression")
    p.add_argument("progression", help="Chord progression (space-separated)")
    p.add_argument("--semitones", "-s", type=int, required=True, help="Semitones (+/-)")
    p.set_defaults(func=cmd_transpose_chords)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
