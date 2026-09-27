#!/usr/bin/env python3
"""
MIDI manipulation tool using mido.
"""

import argparse
import json
import sys

import mido


def cmd_info(args):
    """Get MIDI file info."""
    mid = mido.MidiFile(args.input)

    # Count notes and find pitch range
    note_count = 0
    min_note = 127
    max_note = 0
    tracks_info = []

    for i, track in enumerate(mid.tracks):
        track_notes = 0
        track_name = None
        for msg in track:
            if msg.type == 'track_name':
                track_name = msg.name
            if msg.type == 'note_on' and msg.velocity > 0:
                track_notes += 1
                note_count += 1
                min_note = min(min_note, msg.note)
                max_note = max(max_note, msg.note)
        tracks_info.append({
            "track": i,
            "name": track_name,
            "notes": track_notes,
            "events": len(track)
        })

    # The opening tempo is the earliest set_tempo in time across all tracks
    # (the old loop's break only left the inner loop, so a later track's
    # tempo won); a tempo map with changes is reported as such
    changes = _tempo_map(mid)
    tempo = changes[0][1] if changes and changes[0][0] == 0 else DEFAULT_TEMPO
    bpm = round(mido.tempo2bpm(tempo), 1)

    def note_name(n):
        names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        return f"{names[n % 12]}{n // 12 - 1}"

    info = {
        "type": mid.type,
        "ticks_per_beat": mid.ticks_per_beat,
        "duration_seconds": round(mid.length, 2),
        "tempo_bpm": bpm,
        "tempo_changes": max(0, len(changes) - 1),
        "tempo_range_bpm": [round(mido.tempo2bpm(max(t for _, t in changes)), 1),
                            round(mido.tempo2bpm(min(t for _, t in changes)), 1)] if len(changes) > 1 else None,
        "total_notes": note_count,
        "pitch_range": f"{note_name(min_note)} - {note_name(max_note)}" if note_count else "N/A",
        "tracks": tracks_info
    }
    print(json.dumps(info, indent=2))


DRUM_CHANNEL = 9  # General MIDI channel 10: its note numbers are drum sounds, not pitches
MAJOR_KEYS = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B']
MINOR_KEYS = ['Cm', 'C#m', 'Dm', 'Ebm', 'Em', 'Fm', 'F#m', 'Gm', 'G#m', 'Am', 'Bbm', 'Bm']
PITCH_CLASS = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def _transpose_key(key, semitones):
    """A key signature name moved by semitones, spelt with the fewest accidentals."""
    minor = key.endswith('m')
    root = key[:-1] if minor else key
    pc = PITCH_CLASS[root[0]] + root.count('#') - root.count('b')
    return (MINOR_KEYS if minor else MAJOR_KEYS)[(pc + semitones) % 12]


def cmd_transpose(args):
    """Transpose all notes by semitones.

    Drums stay put: on channel 10 a note number is an instrument, and moving
    it turned a kick, snare and hi-hat into three toms. A note pushed out of
    0..127 used to stay at its old pitch among the transposed ones; it is
    dropped now and counted. The key signature follows the notes.
    """
    mid = mido.MidiFile(args.input)
    dropped = 0
    for i, track in enumerate(mid.tracks):
        events = []
        for t, msg in _to_absolute(track):
            if msg.type in ('note_on', 'note_off') and (args.include_drums or msg.channel != DRUM_CHANNEL):
                new_note = msg.note + args.semitones
                if not 0 <= new_note <= 127:
                    dropped += msg.type == 'note_on' and msg.velocity > 0
                    continue
                msg = msg.copy(note=new_note)
            elif msg.type == 'key_signature' and args.semitones % 12:
                msg = msg.copy(key=_transpose_key(msg.key, args.semitones))
            events.append((t, msg))
        mid.tracks[i] = _from_absolute(events)

    mid.save(args.output)
    print(f"Transposed by {args.semitones:+d} semitones -> {args.output}")
    if dropped:
        print(f"  {dropped} note(s) fell outside the MIDI range 0..127 and were dropped")


DEFAULT_TEMPO = 500000  # microseconds per beat, 120 BPM, the MIDI default when no event is present


MAX_TEMPO = 16777215  # microseconds per beat that fit a set_tempo event (3.58 BPM)


def cmd_tempo(args):
    """Change tempo of MIDI file.

    --bpm sets the OPENING tempo and scales every later change in proportion:
    setting each event to the same value flattened a ritardando (120 then 60
    became 90 and 90).
    """
    if (args.bpm is None) == (args.scale is None):
        _fail("give one of --bpm or --scale")
    if (args.bpm if args.bpm is not None else args.scale) <= 0:
        _fail("--bpm and --scale must be above 0")
    mid = mido.MidiFile(args.input)
    changes = _tempo_map(mid)
    opening = changes[0][1] if changes and changes[0][0] == 0 else DEFAULT_TEMPO
    factor = args.scale if args.scale is not None else args.bpm / mido.tempo2bpm(opening)

    def scaled(tempo):
        new = round(tempo / factor)
        if not 1 <= new <= MAX_TEMPO:
            _fail(f"a tempo of {mido.tempo2bpm(tempo) * factor:.2f} BPM is outside what MIDI can store "
                  f"(3.58 BPM and up)")
        return new

    for track in mid.tracks:
        for i, msg in enumerate(track):
            if msg.type == 'set_tempo':
                track[i] = msg.copy(tempo=scaled(msg.tempo))

    # No tempo event at the start: the file opens at the implicit 120 BPM
    # default, so that default is written scaled (audit F21, 2026-09-06: a
    # --scale on such a file changed nothing).
    if not changes or changes[0][0] > 0:
        mid.tracks[0].insert(0, mido.MetaMessage('set_tempo', tempo=scaled(DEFAULT_TEMPO), time=0))

    mid.save(args.output)
    kept = f" ({len(changes) - 1} tempo change(s) kept in proportion)" if len(changes) > 1 else ""
    if args.bpm is not None:
        print(f"Set tempo to {args.bpm} BPM{kept} -> {args.output}")
    else:
        print(f"Scaled tempo by {args.scale}x{kept} -> {args.output}")


def cmd_merge(args):
    """Merge multiple MIDI files."""
    if len(args.files) < 2:
        print("Error: Need at least 2 files", file=sys.stderr)
        sys.exit(1)

    base = mido.MidiFile(args.files[0])
    if base.type == 2 or any(mido.MidiFile(f).type == 2 for f in args.files[1:]):
        _fail("type 2 files hold independent sequences and cannot be layered")
    # A type 0 file holds exactly one track; layering makes it type 1 (saving
    # two type 0 files merged used to die with "type 0 file must have
    # exactly 1 track")
    base.type = 1

    for path in args.files[1:]:
        other = mido.MidiFile(path)
        if any(m.type == 'set_tempo' for tr in other.tracks for m in tr):
            print(f"note: {path} has its own tempo events; players apply them on the shared timeline",
                  file=sys.stderr)
        # Delta times are in the OTHER file's ticks per beat. Appended as-is
        # into a file with a different resolution they play at the wrong
        # speed (audit F21, 2026-09-06: 960 tpb notes landed at double beat
        # positions in a 480 tpb file). Rescale on absolute ticks so rounding
        # cannot drift along the track.
        for track in other.tracks:
            if other.ticks_per_beat != base.ticks_per_beat:
                track = _rescale_track(track, other.ticks_per_beat, base.ticks_per_beat)
            base.tracks.append(track)

    base.save(args.output)
    print(f"Merged {len(args.files)} files -> {args.output}")


def cmd_extract(args):
    """Extract a specific track."""
    mid = mido.MidiFile(args.input)

    if not 0 <= args.track < len(mid.tracks):
        print(f"Error: Track {args.track} not found (file has {len(mid.tracks)} tracks)", file=sys.stderr)
        sys.exit(1)

    new_mid = mido.MidiFile(type=0, ticks_per_beat=mid.ticks_per_beat)
    track = mid.tracks[args.track]
    # A type 1 file keeps tempo (and time signature) on the conductor track.
    # Extracting another track alone dropped them, so the result played at
    # 120 BPM whatever the piece was (audit F21, 2026-09-06). Carry the
    # conductor's tempo map over, at its absolute times, unless the track
    # already has its own.
    if args.track != 0 and not any(m.type == 'set_tempo' for m in track):
        track = _merge_conductor(mid.tracks[0], track)
    new_mid.tracks.append(track)

    new_mid.save(args.output)
    print(f"Extracted track {args.track} -> {args.output}")


def cmd_notes(args):
    """List all notes in the file."""
    mid = mido.MidiFile(args.input)

    names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    changes = _tempo_map(mid)

    notes = []

    for track_idx, track in enumerate(mid.tracks):
        for tick, msg in _to_absolute(track):
            if msg.type == 'note_on' and msg.velocity > 0:
                notes.append({
                    "track": track_idx,
                    "time": tick,
                    "seconds": round(_seconds(tick, changes, mid.ticks_per_beat), 3),
                    "channel": msg.channel + 1,
                    "note": msg.note,
                    "name": f"{names[msg.note % 12]}{msg.note // 12 - 1}",
                    "velocity": msg.velocity
                })

    notes.sort(key=lambda x: (x['time'], x['note']))

    # JSON is for analysis, so it carries every note unless asked otherwise
    # (it used to stop at 100 without a word); the table stops at 50
    limit = args.limit if args.limit is not None else (0 if args.json else 50)
    shown = notes[:limit] if limit > 0 else notes
    if args.json:
        print(json.dumps(shown, indent=2))
    else:
        print(f"{'Tick':>8} {'Seconds':>8} {'Track':>5} {'Ch':>3} {'Note':>6} {'Vel':>4}")
        print("-" * 40)
        for n in shown:
            print(f"{n['time']:>8} {n['seconds']:>8.3f} {n['track']:>5} {n['channel']:>3} {n['name']:>6} {n['velocity']:>4}")
    if len(shown) < len(notes):
        print(f"... and {len(notes) - len(shown)} more notes (--limit 0 shows all)", file=sys.stderr if args.json else sys.stdout)


def _fail(message):
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def _tempo_map(mid):
    """[(abs_tick, tempo)] across all tracks, in time order."""
    return sorted((t, m.tempo) for tr in mid.tracks for t, m in _to_absolute(tr) if m.type == 'set_tempo')


def _seconds(tick, changes, tpb):
    """Seconds at an absolute tick under the tempo map (120 BPM before the first event)."""
    seconds, last, tempo = 0.0, 0, DEFAULT_TEMPO
    for t, new in changes:
        if t >= tick:
            break
        seconds += mido.tick2second(t - last, tpb, tempo)
        last, tempo = t, new
    return seconds + mido.tick2second(tick - last, tpb, tempo)


def _to_absolute(track):
    """[(abs_tick, msg)] for a track's delta-time messages."""
    out, t = [], 0
    for msg in track:
        t += msg.time
        out.append((t, msg))
    return out


def _from_absolute(events):
    """A MidiTrack from [(abs_tick, msg)], sorted by time (stable), end_of_track last."""
    events = sorted(events, key=lambda e: (e[0], e[1].type == 'end_of_track'))
    track = mido.MidiTrack()
    prev = 0
    for t, msg in events:
        track.append(msg.copy(time=t - prev))
        prev = t
    return track


def _rescale_track(track, src_tpb, dst_tpb):
    return _from_absolute([(round(t * dst_tpb / src_tpb), msg) for t, msg in _to_absolute(track)])


def _merge_conductor(conductor, track):
    meta = [(t, m) for t, m in _to_absolute(conductor)
            if m.type in ('set_tempo', 'time_signature', 'key_signature')]
    body = [(t, m) for t, m in _to_absolute(track) if m.type != 'end_of_track']
    end = [(t, m) for t, m in _to_absolute(track) if m.type == 'end_of_track']
    return _from_absolute(meta + body + end)


def cmd_quantize(args):
    """Quantize note timing to grid."""
    if args.grid <= 0:
        _fail("--grid must be a positive division (4 = quarter, 8 = eighth)")
    mid = mido.MidiFile(args.input)
    # The grid is 1/args.grid of a whole note: --grid 4 is a quarter note
    # (one beat), --grid 8 an eighth. Quantise ABSOLUTE positions, not the
    # delta between events: rounding each delta moved a note at tick 230 to
    # 240 instead of 0 and let the error walk down the track (audit F21).
    grid = max(1, round(mid.ticks_per_beat * 4 / args.grid))

    # Only ONSETS snap. A note's release moves by the same amount as its
    # onset so it keeps its length: snapping both ends collapsed any note
    # shorter than half a grid step to zero length, silent (eight eighth
    # notes on a quarter grid lost four of them: review of #156).
    for i, track in enumerate(mid.tracks):
        events = []
        open_shift = {}  # (channel, note) -> [shift, ...] for sounding notes
        for t, msg in _to_absolute(track):
            is_on = msg.type == 'note_on' and msg.velocity > 0
            is_off = msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0)
            if is_on:
                shift = round(t / grid) * grid - t
                open_shift.setdefault((msg.channel, msg.note), []).append(shift)
                t += shift
            elif is_off:
                stack = open_shift.get((msg.channel, msg.note))
                if stack:
                    t += stack.pop(0)
                else:  # a release with no onset in this track: snap it alone
                    t = round(t / grid) * grid
            events.append((t, msg))
        mid.tracks[i] = _from_absolute(events)

    mid.save(args.output)
    print(f"Quantized to 1/{args.grid} notes -> {args.output}")


def main():
    parser = argparse.ArgumentParser(description="MIDI editing tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Info
    p = subparsers.add_parser("info", help="Get MIDI info")
    p.add_argument("input", help="Input MIDI file")
    p.set_defaults(func=cmd_info)

    # Transpose
    p = subparsers.add_parser("transpose", help="Transpose notes")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--semitones", "-s", type=int, required=True, help="Semitones (+/-)")
    p.add_argument("--include-drums", action="store_true",
                   help="Also move channel 10 (General MIDI drums, where a note number is an instrument)")
    p.set_defaults(func=cmd_transpose)

    # Tempo
    p = subparsers.add_parser("tempo", help="Change tempo")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--bpm", type=float, help="Set the opening BPM; later tempo changes keep their proportion")
    p.add_argument("--scale", type=float, help="Scale tempo (0.5 = half speed)")
    p.set_defaults(func=cmd_tempo)

    # Merge
    p = subparsers.add_parser("merge", help="Merge MIDI files")
    p.add_argument("files", nargs="+", help="Files to merge")
    p.add_argument("-o", "--output", required=True, help="Output file")
    p.set_defaults(func=cmd_merge)

    # Extract
    p = subparsers.add_parser("extract", help="Extract track")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--track", "-t", type=int, required=True, help="Track number")
    p.set_defaults(func=cmd_extract)

    # Notes
    p = subparsers.add_parser("notes", help="List notes")
    p.add_argument("input", help="Input file")
    p.add_argument("--json", action="store_true", help="Output as JSON (every note unless --limit)")
    p.add_argument("--limit", type=int, help="Notes to show (default 50 for the table, all for JSON; 0 = all)")
    p.set_defaults(func=cmd_notes)

    # Quantize
    p = subparsers.add_parser("quantize", help="Quantize timing")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--grid", "-g", type=int, default=4, help="Grid division (4=quarter, 8=eighth, 16=sixteenth)")
    p.set_defaults(func=cmd_quantize)

    args = parser.parse_args()
    try:
        args.func(args)
    except (OSError, EOFError, ValueError) as exc:
        _fail(str(exc) or f"{type(exc).__name__}: not a readable MIDI file")


if __name__ == "__main__":
    main()
