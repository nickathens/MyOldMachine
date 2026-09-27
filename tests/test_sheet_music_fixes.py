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


if __name__ == "__main__":
    unittest.main()
