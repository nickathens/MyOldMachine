"""sprite-gen fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "sprite-gen" / "scripts" / "sprite.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=60)


class Sprites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_frames_keep_their_numeric_order(self):
        for i in range(1, 13):        # frame_1 ... frame_12, no leading zeros
            Image.new("RGBA", (8, 8), (i * 20, 0, 0, 255)).save(self.d / f"frame_{i}.png")
        run("sheet", *sorted(self.d.glob("frame_*.png")), "--cols", "12", "-o", self.d / "sheet.png")
        sheet = Image.open(self.d / "sheet.png")
        reds = [sheet.getpixel((x * 8 + 4, 4))[0] for x in range(12)]
        self.assertEqual(reds, [i * 20 for i in range(1, 13)], "frame_10 landed after frame_1")

    def test_split_names_frames_by_their_cell(self):
        sheet = Image.new("RGBA", (24, 8), (0, 0, 0, 0))
        sheet.paste((255, 0, 0, 255), (0, 0, 8, 8))       # cell 0
        sheet.paste((0, 0, 255, 255), (16, 0, 24, 8))     # cell 2; cell 1 empty
        sheet.save(self.d / "s.png")
        run("split", self.d / "s.png", "--cols", "3", "--rows", "1", "-o", self.d / "out")
        self.assertEqual(sorted(p.name for p in (self.d / "out").iterdir()), ["frame_0000.png", "frame_0002.png"])


if __name__ == "__main__":
    unittest.main()
