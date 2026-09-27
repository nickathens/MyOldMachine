"""translate.py splitting, Linux bot review 2026-09-27.

Google refuses 5000 characters and more (the error printed the whole text
back) and MyMemory 500, so long texts are split at line, sentence and word
breaks. The engines themselves are not called here: Google blocks this
machine and MyMemory has a daily quota.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "translate" / "scripts" / "translate.py"


def load():
    if "deep_translator" not in sys.modules:
        fake = types.ModuleType("deep_translator")
        fake.GoogleTranslator = object
        sys.modules["deep_translator"] = fake
    spec = importlib.util.spec_from_file_location("translate_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Pieces(unittest.TestCase):
    def test_every_piece_fits_and_nothing_is_lost(self):
        t = load()
        text = "\n".join(["Short line."] * 30 + ["Αυτή είναι μια πρόταση. " * 300] + ["Tail."])
        for limit in (450, 4500):
            parts = t.pieces(text, limit)
            self.assertTrue(all(len(piece) < limit for piece, _ in parts), limit)
            rebuilt = "".join(piece + joiner for piece, joiner in parts)
            self.assertEqual(rebuilt.split(), text.split())

    def test_a_word_longer_than_the_limit_comes_back_whole(self):
        # Audit pass 1: a hard cut inside a long URL was rejoined with a space.
        t = load()
        text = "see https://example.com/" + "x" * 700 + " now"
        parts = t.pieces(text, 500)
        self.assertTrue(all(len(piece) < 500 for piece, _ in parts))
        self.assertEqual("".join(piece + joiner for piece, joiner in parts), text)

    def test_script_guess(self):
        t = load()
        self.assertEqual(t.guess_by_script("Γεια σου κόσμε"), "el")
        self.assertEqual(t.guess_by_script("Привет мир"), "ru")
        self.assertIsNone(t.guess_by_script("Bonjour le monde"))


if __name__ == "__main__":
    unittest.main()
