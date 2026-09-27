"""color-palette fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('PIL', 'colorthief', 'numpy') if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import importlib.util
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "color-palette" / "scripts" / "palette.py"


def load():
    spec = importlib.util.spec_from_file_location("palette_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Palette(unittest.TestCase):
    def test_the_untouched_colour_comes_back_unchanged(self):
        p = load()
        self.assertEqual(p.analogous("#3498db")[1], "#3498db")
        self.assertEqual(p.complementary("#808080"), "#808080")
        self.assertEqual(p.complementary("#010203"), "#030201")

    def test_short_hex_is_read(self):
        self.assertEqual(load().hex_to_rgb("#fff"), (255, 255, 255))

    def test_extracted_colours_are_the_real_ones(self):
        p = load()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "flat.png"
            im = Image.new("RGB", (200, 100))
            for i, colour in enumerate([(200, 30, 30), (30, 200, 30), (30, 30, 200), (240, 240, 240), (10, 10, 10)]):
                im.paste(colour, (i * 40, 0, i * 40 + 40, 100))
            im.save(path)
            got = {tuple(c["rgb"]) for c in p.extract_palette(str(path), 5)}
        self.assertIn((30, 200, 30), got, got)
        self.assertIn((200, 30, 30), got, got)


if __name__ == "__main__":
    unittest.main()
