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


class BigPhotos(unittest.TestCase):
    """Linux bot sweep 2026-10-07: ColorThief was given the full image at
    quality=1, which walks every pixel through Python tuples: a 48 MP photo
    peaked at 3.8 GB and took 76 s as a child of the bot (a 108 MP phone
    photo would kill the bot). It now gets the same 400 px copy the
    refinement step already used."""

    def test_colorthief_never_sees_the_full_image(self):
        from unittest import mock
        p = load()
        seen = []
        real = p.ColorThief

        class Spy(real):
            def __init__(self, file):
                super().__init__(file)
                seen.append(self.image.size)

        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "photo.png")
            img = Image.new("RGB", (2400, 1800), (30, 200, 30))
            img.paste((200, 30, 30), (0, 0, 1200, 1800))
            img.save(path)
            with mock.patch.object(p, "ColorThief", Spy):
                colours = p.extract_palette(str(path), 2)
        self.assertEqual(len(seen), 1)
        self.assertLessEqual(max(seen[0]), 400)
        self.assertEqual({c["hex"] for c in colours}, {"#1ec81e", "#c81e1e"})


if __name__ == "__main__":
    unittest.main()
