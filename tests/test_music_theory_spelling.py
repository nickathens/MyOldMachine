"""music-theory fixes from the Linux bot review 2026-09-27: the relative of a minor
key, flat spelling, pentatonic/blues spelling, flat chord names, and
consistent transposition."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

THEORY = Path(__file__).resolve().parent.parent / "skills" / "music-theory" / "scripts" / "theory.py"

try:
    import music21  # noqa: F401
    HAVE_M21 = True
except ImportError:
    HAVE_M21 = False


def run(*args):
    out = subprocess.run([sys.executable, str(THEORY), *args], capture_output=True, text=True,
                         timeout=120)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


@unittest.skipUnless(HAVE_M21, "music21 not installed")
class Spelling(unittest.TestCase):
    def test_minor_key_relative_is_the_relative_major(self):
        from music21 import note, stream
        d = Path(tempfile.mkdtemp(prefix="m21-"))
        self.addCleanup(shutil.rmtree, d, True)
        s = stream.Stream()
        for p in ["A3", "C4", "E4", "A4", "D4", "F4", "A4", "E4", "G#4", "B4", "E4", "A3", "C4", "E4"]:
            s.append(note.Note(p, quarterLength=1))
        s.write("midi", fp=str(d / "am.mid"))
        key = run("key", str(d / "am.mid"))
        self.assertEqual(key["relative"].lower(), "c major")
        self.assertEqual(key["parallel"].lower(), "a major")

    def test_scales_are_spelled_from_the_tonic(self):
        self.assertEqual(run("scale", "Eb", "pentatonic-minor")["notes"],
                         ["Eb", "Gb", "Ab", "Bb", "Db", "Eb"])
        self.assertEqual(run("scale", "C", "blues")["notes"], ["C", "Eb", "F", "Gb", "G", "Bb", "C"])
        self.assertEqual(run("scale", "F", "major")["notes"], ["F", "G", "A", "Bb", "C", "D", "E", "F"])

    def test_flat_chord_names_parse(self):
        self.assertEqual(run("chord-notes", "Bbmaj7")["notes"], ["Bb", "D", "F", "A"])
        self.assertEqual(run("chord-notes", "C/Bb")["bass"], "Bb")

    def test_transposition_keeps_letter_distance_and_suffixes(self):
        self.assertEqual(run("transpose-chords", "C F G", "-s", "3")["transposed"], "Eb Ab Bb")
        self.assertEqual(run("transpose-chords", "Am7b5 D7/F# Gm", "-s", "1")["transposed"],
                         "Bbm7b5 Eb7/G Abm")


if __name__ == "__main__":
    unittest.main()
