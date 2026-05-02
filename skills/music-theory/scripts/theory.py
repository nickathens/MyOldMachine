#!/usr/bin/env python3
"""
Music theory analysis using music21.
"""

import argparse
import json
import sys


def cmd_analyze(args):
    """Full analysis of a music file."""
    from music21 import converter

    score = converter.parse(args.input)

    # Key analysis
    key = score.analyze('key')

    # Get all notes
    notes = list(score.flatten().notes)

    result = {
        "key": str(key),
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
        "key": str(key),
        "tonic": key.tonic.name,
        "mode": key.mode,
        "confidence": round(key.correlationCoefficient, 3),
        "relative": str(key.relative) if key.mode == 'major' else str(key.parallel),
    }

    print(json.dumps(result, indent=2))


def cmd_chords(args):
    """Extract chord progression."""
    from music21 import converter, harmony

    score = converter.parse(args.input)

    # Try to get existing chord symbols
    chords = list(score.flatten().getElementsByClass(harmony.ChordSymbol))

    if chords:
        progression = [str(c.figure) for c in chords]
    else:
        # Analyze chords at regular intervals
        from music21 import chord as m21chord

        chordified = score.chordify()
        progression = []
        for c in chordified.flatten().getElementsByClass(m21chord.Chord):
            try:
                # Get common name if possible
                name = c.pitchedCommonName
                if name and name != 'empty':
                    progression.append(name)
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
        "from": str(p1),
        "to": str(p2),
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
        ch = harmony.ChordSymbol(args.chord)
        notes = [p.nameWithOctave for p in ch.pitches]
        simple = [p.name for p in ch.pitches]

        result = {
            "chord": args.chord,
            "root": ch.root().name,
            "bass": ch.bass().name,
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

    # Custom scales defined by intervals from root
    custom_scales = {
        'pentatonic-major': [0, 2, 4, 7, 9],      # C D E G A
        'pentatonic-minor': [0, 3, 5, 7, 10],     # C Eb F G Bb
        'blues': [0, 3, 5, 6, 7, 10],             # C Eb F F# G Bb
    }

    scale_type = args.scale_type.lower()
    tonic = pitch.Pitch(args.root)

    if scale_type in scale_classes:
        sc = scale_classes[scale_type](tonic)
        notes = [p.name for p in sc.getPitches(args.root + '3', args.root + '4')]
    elif scale_type in custom_scales:
        intervals = custom_scales[scale_type]
        base_midi = tonic.midi
        notes = []
        for i in intervals:
            p = pitch.Pitch(midi=base_midi + i)
            notes.append(p.name)
        notes.append(tonic.name)  # Add octave
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
    """Transpose a chord progression."""
    from music21 import harmony, interval

    chords = args.progression.split()
    transposed = []

    intv = interval.Interval(args.semitones)

    for ch_name in chords:
        try:
            ch = harmony.ChordSymbol(ch_name)
            ch.transpose(intv, inPlace=True)
            transposed.append(ch.figure)
        except Exception:
            transposed.append(f"[{ch_name}?]")

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
