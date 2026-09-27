#!/usr/bin/env python3
"""
Algorithmic composition engine using mingus, pretty_midi, and music theory
"""
import argparse
import random
import uuid
import pretty_midi

# Scale patterns (intervals from root)
SCALE_PATTERNS = {
    'major': [0, 2, 4, 5, 7, 9, 11],
    'minor': [0, 2, 3, 5, 7, 8, 10],
    'dorian': [0, 2, 3, 5, 7, 9, 10],
    'phrygian': [0, 1, 3, 5, 7, 8, 10],
    'lydian': [0, 2, 4, 6, 7, 9, 11],
    'mixolydian': [0, 2, 4, 5, 7, 9, 10],
    'locrian': [0, 1, 3, 5, 6, 8, 10],
    'harmonic_minor': [0, 2, 3, 5, 7, 8, 11],
    'melodic_minor': [0, 2, 3, 5, 7, 9, 11],
    'pentatonic_major': [0, 2, 4, 7, 9],
    'pentatonic_minor': [0, 3, 5, 7, 10],
    'blues': [0, 3, 5, 6, 7, 10],
    'whole_tone': [0, 2, 4, 6, 8, 10],
    'chromatic': list(range(12)),
}

# Common chord progressions (Roman numerals as scale degrees)
PROGRESSIONS = {
    'pop': ['I', 'V', 'vi', 'IV'],
    'jazz_251': ['ii', 'V', 'I'],
    'jazz_1625': ['I', 'vi', 'ii', 'V'],
    'blues': ['I', 'I', 'I', 'I', 'IV', 'IV', 'I', 'I', 'V', 'IV', 'I', 'V'],
    'classical': ['I', 'IV', 'V', 'I'],
    'sad': ['i', 'VI', 'III', 'VII'],
    'epic': ['i', 'VII', 'VI', 'VII'],
    'ambient': ['I', 'iii', 'vi', 'IV'],
}

NOTE_TO_MIDI = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
CHORD_TYPES = ('major', 'minor', 'major7', 'minor7', 'dim')
DRUM_STYLES = ('basic', 'rock', 'electronic', 'jazz')


def root_note(text):
    """argparse type for a root: a letter with an optional # or b."""
    if len(text) in (1, 2) and text[0].upper() in NOTE_TO_MIDI and text[1:] in ('', '#', 'b'):
        return text[0].upper() + text[1:]
    raise argparse.ArgumentTypeError(f"not a note name: {text!r} (C, F#, Bb ...)")


def note_to_midi_number(note_name, octave=4):
    """Convert note name to MIDI number"""
    base = note_name[0].upper()
    modifier = 0
    if len(note_name) > 1:
        if note_name[1] == '#':
            modifier = 1
        elif note_name[1] == 'b':
            modifier = -1
    return NOTE_TO_MIDI[base] + modifier + (octave + 1) * 12

def get_scale_notes(root, scale_type, octave=4):
    """Get MIDI notes for a scale"""
    root_midi = note_to_midi_number(root, octave)
    pattern = SCALE_PATTERNS.get(scale_type, SCALE_PATTERNS['major'])
    return [root_midi + interval for interval in pattern]

def generate_melody(root='C', scale_type='major', bars=8, octave=4,
                   note_density=0.7, step_probability=0.7, tempo=120):
    """Generate a melody using the given scale.

    All durations are in BEATS and converted to seconds with the one tempo
    given (audit F22, 2026-09-06: chords and melody each assumed their own
    clock, and four bars of chords ran to 15.6 s instead of 8).
    """
    scale_notes = get_scale_notes(root, scale_type, octave)
    extended_scale = scale_notes + [n + 12 for n in scale_notes[:4]]  # Extend up

    midi = pretty_midi.PrettyMIDI(initial_tempo=tempo)
    instrument = pretty_midi.Instrument(program=0)  # Piano
    spb = 60.0 / tempo  # seconds per beat

    current_beat = 0.0
    beats_per_bar = 4
    total_beats = bars * beats_per_bar
    current_note_idx = len(scale_notes) // 2  # Start in middle

    for beat in range(total_beats):
        if random.random() < note_density:
            # Decide step or leap
            if random.random() < step_probability:
                step = random.choice([-1, 1])
            else:
                step = random.choice([-3, -2, 2, 3])

            current_note_idx = max(0, min(len(extended_scale) - 1,
                                         current_note_idx + step))

            pitch = extended_scale[current_note_idx]
            duration = random.choice([0.25, 0.5, 0.5, 1.0])
            velocity = random.randint(70, 100)

            note = pretty_midi.Note(
                velocity=velocity,
                pitch=pitch,
                start=current_beat * spb,
                end=(current_beat + duration * 0.9) * spb
            )
            instrument.notes.append(note)

        current_beat += 1.0  # one step per beat

    midi.instruments.append(instrument)
    return midi

def generate_chord_progression(root='C', prog_type='pop', bars=4, octave=3, tempo=120):
    """Generate a chord progression, one chord per bar, the progression repeating.

    A minor key progression (tonic written i) takes its roots from the natural
    minor scale: reading them off the major scale made "sad" in C come out
    Cm, A, E, B instead of Cm, Ab, Eb, Bb. Spreading the progression over the
    bars instead of one chord a bar put a ii V I over 8 bars on beats 0,
    10.67 and 21.33, off every bar line (Linux bot review 2026-09-27).
    """
    progression = PROGRESSIONS[prog_type]

    midi = pretty_midi.PrettyMIDI(initial_tempo=tempo)
    instrument = pretty_midi.Instrument(program=0)  # Piano
    spb = 60.0 / tempo

    root_midi = note_to_midi_number(root, octave)
    minor_key = progression[0].startswith('i')
    major_scale = SCALE_PATTERNS['minor'] if minor_key else SCALE_PATTERNS['major']

    # Map roman numerals to scale degrees and chord types
    numeral_to_degree = {
        'I': 0, 'II': 1, 'III': 2, 'IV': 3, 'V': 4, 'VI': 5, 'VII': 6,
        'i': 0, 'ii': 1, 'iii': 2, 'iv': 3, 'v': 4, 'vi': 5, 'vii': 6
    }

    current_beat = 0.0
    beats_per_chord = 4

    for bar in range(bars):
        numeral = progression[bar % len(progression)]
        degree = numeral_to_degree.get(numeral.replace('7', ''), 0)
        chord_root = root_midi + major_scale[degree % 7]

        # Determine chord quality
        if numeral.islower() or numeral[0].islower():
            # Minor chord
            chord_notes = [chord_root, chord_root + 3, chord_root + 7]
        else:
            # Major chord
            chord_notes = [chord_root, chord_root + 4, chord_root + 7]

        # Add 7th if specified
        if '7' in numeral:
            chord_notes.append(chord_root + 10)

        for pitch in chord_notes:
            note = pretty_midi.Note(
                velocity=80,
                pitch=pitch,
                start=current_beat * spb,
                end=(current_beat + beats_per_chord * 0.9) * spb
            )
            instrument.notes.append(note)

        current_beat += beats_per_chord

    midi.instruments.append(instrument)
    return midi

def generate_arpeggio(root='C', chord_type='major', bars=4, octave=4,
                     pattern='up', tempo=120):
    """Generate an arpeggio pattern"""
    midi = pretty_midi.PrettyMIDI(initial_tempo=tempo)
    instrument = pretty_midi.Instrument(program=0)

    root_midi = note_to_midi_number(root, octave)

    if chord_type == 'major':
        intervals = [0, 4, 7, 12]
    elif chord_type == 'minor':
        intervals = [0, 3, 7, 12]
    elif chord_type == 'major7':
        intervals = [0, 4, 7, 11]
    elif chord_type == 'minor7':
        intervals = [0, 3, 7, 10]
    elif chord_type == 'dim':
        intervals = [0, 3, 6, 9]
    else:
        intervals = [0, 4, 7, 12]

    chord_notes = [root_midi + i for i in intervals]

    if pattern == 'down':
        chord_notes = list(reversed(chord_notes))
    elif pattern == 'updown':
        chord_notes = chord_notes + list(reversed(chord_notes[1:-1]))

    current_time = 0
    note_duration = 60 / tempo / 2  # 8th notes

    total_notes = bars * 8
    for i in range(total_notes):
        pitch = chord_notes[i % len(chord_notes)]
        note = pretty_midi.Note(
            velocity=random.randint(70, 90),
            pitch=pitch,
            start=current_time,
            end=current_time + note_duration * 0.8
        )
        instrument.notes.append(note)
        current_time += note_duration

    midi.instruments.append(instrument)
    return midi

def generate_drums(bars=4, style='basic', tempo=120):
    """Generate a drum pattern"""
    midi = pretty_midi.PrettyMIDI(initial_tempo=tempo)
    drums = pretty_midi.Instrument(program=0, is_drum=True)

    # GM drum mapping
    KICK = 36
    SNARE = 38
    HIHAT_CLOSED = 42
    HIHAT_OPEN = 46
    RIDE = 51

    beat_duration = 60 / tempo
    current_time = 0

    patterns = {
        'basic': {
            KICK: [1, 0, 0, 0, 1, 0, 0, 0],
            SNARE: [0, 0, 1, 0, 0, 0, 1, 0],
            HIHAT_CLOSED: [1, 1, 1, 1, 1, 1, 1, 1],
        },
        'rock': {
            KICK: [1, 0, 0, 1, 1, 0, 0, 0],
            SNARE: [0, 0, 1, 0, 0, 0, 1, 0],
            HIHAT_CLOSED: [1, 1, 1, 1, 1, 1, 1, 1],
        },
        'electronic': {
            KICK: [1, 0, 0, 0, 1, 0, 0, 0],
            SNARE: [0, 0, 1, 0, 0, 0, 1, 0],
            HIHAT_CLOSED: [1, 0, 1, 0, 1, 0, 1, 0],
            HIHAT_OPEN: [0, 0, 0, 1, 0, 0, 0, 1],
        },
        'jazz': {
            RIDE: [1, 0, 1, 1, 1, 0, 1, 1],
            KICK: [1, 0, 0, 0, 0, 1, 0, 0],
            SNARE: [0, 0, 0, 0, 1, 0, 0, 0],
        },
    }

    pattern = patterns.get(style, patterns['basic'])
    steps_per_bar = 8
    total_steps = bars * steps_per_bar
    step_duration = beat_duration / 2

    for step in range(total_steps):
        pattern_idx = step % steps_per_bar
        for drum, hits in pattern.items():
            if hits[pattern_idx]:
                velocity = random.randint(80, 110) if drum == KICK else random.randint(70, 100)
                note = pretty_midi.Note(
                    velocity=velocity,
                    pitch=drum,
                    start=current_time,
                    end=current_time + step_duration * 0.5
                )
                drums.notes.append(note)
        current_time += step_duration

    midi.instruments.append(drums)
    return midi

def combine_midi(midis, output_path, tempo=120):
    """Combine multiple MIDI objects into one file"""
    combined = pretty_midi.PrettyMIDI(initial_tempo=tempo)

    for i, m in enumerate(midis):
        for inst in m.instruments:
            # Offset program to avoid conflicts
            new_inst = pretty_midi.Instrument(
                program=inst.program if not inst.is_drum else 0,
                is_drum=inst.is_drum,
                name=inst.name or f'Track {i}'
            )
            new_inst.notes = inst.notes
            combined.instruments.append(new_inst)

    combined.write(output_path)
    print(f"Saved: {output_path}")
    return output_path

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Algorithmic composition')
    parser.add_argument('action', choices=['melody', 'chords', 'arpeggio', 'drums', 'full'])
    # Unknown names used to fall back silently: --progression jazz made the
    # pop progression and said nothing
    parser.add_argument('--root', default='C', type=root_note, help='Root note (C, F#, Bb ...)')
    parser.add_argument('--scale', default='major', choices=sorted(SCALE_PATTERNS), help='Scale for the melody')
    parser.add_argument('--chord', default='major', choices=CHORD_TYPES, help='Chord type for arpeggio')
    parser.add_argument('--pattern', default='up', choices=('up', 'down', 'updown'), help='Arpeggio direction')
    parser.add_argument('--progression', default='pop', choices=sorted(PROGRESSIONS),
                        help='Chord progression, one chord per bar, repeating')
    parser.add_argument('--style', default='basic', choices=DRUM_STYLES, help='Drum style')
    parser.add_argument('--bars', type=int, default=8)
    parser.add_argument('--tempo', type=int, default=120)
    parser.add_argument('--seed', type=int, help='Random seed, for a result you can reproduce')
    parser.add_argument('--output', '-o', default=f'/tmp/composition_{uuid.uuid4().hex[:8]}.mid')

    args = parser.parse_args()
    if args.bars < 1 or args.tempo < 1:
        parser.error('--bars and --tempo must be at least 1')
    if args.seed is not None:
        random.seed(args.seed)
    if args.action in ('chords', 'full') and args.bars % len(PROGRESSIONS[args.progression]):
        print(f"note: {args.bars} bars do not complete the {len(PROGRESSIONS[args.progression])} chord "
              f"'{args.progression}' progression; it stops part way through")

    if args.action == 'melody':
        midi = generate_melody(args.root, args.scale, args.bars, tempo=args.tempo)
        midi.write(args.output)

    elif args.action == 'chords':
        midi = generate_chord_progression(args.root, args.progression, args.bars, tempo=args.tempo)
        midi.write(args.output)

    elif args.action == 'arpeggio':
        midi = generate_arpeggio(args.root, args.chord, args.bars, pattern=args.pattern, tempo=args.tempo)
        midi.write(args.output)

    elif args.action == 'drums':
        midi = generate_drums(args.bars, args.style, args.tempo)
        midi.write(args.output)

    elif args.action == 'full':
        # Generate a full arrangement
        chord_midi = generate_chord_progression(args.root, args.progression, args.bars, tempo=args.tempo)
        melody = generate_melody(args.root, args.scale, args.bars, octave=5, tempo=args.tempo)
        drums = generate_drums(args.bars, args.style, args.tempo)
        # Stamp the file with the tempo the parts were written at. Without it
        # a `--tempo 90 full` laid its notes out at 90 and told the DAW 120,
        # so nothing landed on a bar line (found porting F22 to MOM #156).
        combine_midi([chord_midi, melody, drums], args.output, tempo=args.tempo)

    print(f"Generated: {args.output}")
