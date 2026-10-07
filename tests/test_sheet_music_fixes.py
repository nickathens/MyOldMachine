"""sheet-music fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('mido', 'music21') if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import importlib.util
import random
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import mido

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "sheet-music" / "scripts" / "midi2sheet.py"


def load():
    spec = importlib.util.spec_from_file_location("midi2sheet_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def played_scale(path):
    """A steady scale played by a person: onsets and lengths a little off the grid."""
    rng = random.Random(2)
    mid = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    events = []
    for k, note in enumerate([60, 62, 64, 65, 67, 69, 71, 72]):
        start = max(0, k * 480 + rng.randint(-30, 30))
        events += [(start, 1, mido.Message("note_on", note=note, velocity=80)),
                   (start + 430 + rng.randint(-40, 30), 0, mido.Message("note_off", note=note, velocity=0))]
    last = 0
    for when, _, msg in sorted(events, key=lambda e: (e[0], e[1])):
        track.append(msg.copy(time=when - last))
        last = when
    mid.save(path)


@unittest.skipUnless(shutil.which("midi2ly") and shutil.which("lilypond"), "needs LilyPond")
class SheetMusic(unittest.TestCase):
    def test_a_played_line_engraves_as_plain_quarters(self):
        m = load()
        with tempfile.TemporaryDirectory() as d:
            src, tidy = Path(d) / "played.mid", Path(d) / "tidy.mid"
            played_scale(src)
            m.tidy_midi(str(src), str(tidy), 16)
            ly = m.midi_to_lilypond(str(tidy), 16)
        body = ly[ly.index("\\relative"):]
        self.assertNotRegex(body, r"\d\*\d+/\d+", "fractional durations: notes tied to 128ths")
        self.assertIsNone(re.search(r"\br\d", body), "slivers of rest between the notes")

    def test_a_title_with_quotes_renders(self):
        m = load()
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "played.mid"
            played_scale(src)
            ly = m.midi_to_lilypond(str(src))
            result = m.render_lilypond(ly, str(Path(d) / "out.pdf"), title='My "Scale"')
        self.assertIn("success", result, result)


def _notes(path):
    """(start, end, note) of every note in track 0, in ticks."""
    t, sounding, out = 0, {}, []
    for msg in mido.MidiFile(path).tracks[0]:
        t += msg.time
        if msg.type == "note_on" and msg.velocity:
            sounding[msg.note] = t
        elif msg.type in ("note_off", "note_on") and msg.note in sounding:
            out.append((sounding.pop(msg.note), t, msg.note))
    return sorted(out)


class HeldNotesStayHeld(unittest.TestCase):
    """Linux bot sweep 2026-10-07: tidy_midi ran every note on to the next
    onset when the gap was under a grid step, and an OVERLAP counts as a
    negative gap, so a bass note held under a moving melody was cut at the
    melody's next note (any piano part, and every audio-to-midi take)."""

    def _write(self, path, notes, tpb=480):
        mid = mido.MidiFile(ticks_per_beat=tpb)
        track = mido.MidiTrack()
        mid.tracks.append(track)
        events = sorted([(s, 1, n) for s, e, n in notes] + [(e, 0, n) for s, e, n in notes])
        last = 0
        for when, on, note in events:
            track.append(mido.Message("note_on" if on else "note_off", note=note, velocity=80 if on else 0,
                                      time=when - last))
            last = when
        mid.save(path)

    def test_a_bass_held_under_a_melody_keeps_its_length(self):
        sheet = load()
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d, "in.mid"), Path(d, "out.mid")
            bass = (0, 1920, 48)                                   # a whole note
            melody = [(i * 480, i * 480 + 470, 64 + i) for i in range(4)]   # quarters, released early
            self._write(src, [bass, *melody])
            sheet.tidy_midi(str(src), str(out), 16)
            got = _notes(out)
        self.assertIn((0, 1920, 48), got, got)
        # the small gaps in the melody still close up (the tidy's purpose)
        self.assertIn((0, 480, 64), got, got)

    def test_a_sloppy_release_just_past_the_next_note_is_still_trimmed(self):
        sheet = load()
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d, "in.mid"), Path(d, "out.mid")
            self._write(src, [(0, 500, 60), (480, 960, 62)])        # 20 ticks of overlap
            sheet.tidy_midi(str(src), str(out), 16)
            got = _notes(out)
        self.assertIn((0, 480, 60), got, got)


if __name__ == "__main__":
    unittest.main()
