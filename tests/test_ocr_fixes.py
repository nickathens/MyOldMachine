"""ocr fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "ocr" / "scripts" / "ocr.py"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def langs():
    r = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True)
    return r.stdout


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=300)


@unittest.skipUnless(shutil.which("tesseract") and "ell" in langs(), "needs tesseract with the Greek pack")
class Ocr(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp(prefix="ocr-"))
        font = ImageFont.truetype(FONT, 40)
        im = Image.new("RGB", (900, 200), "white")
        draw = ImageDraw.Draw(im)
        draw.text((20, 30), "Invoice number 4471", fill="black", font=font)
        draw.text((20, 110), "Τιμολόγιο αριθμός 4471", fill="black", font=font)
        im.save(cls.d / "upright.png")
        exif = Image.Exif()
        exif[0x0112] = 6
        im.rotate(90, expand=True).save(cls.d / "phone.jpg", exif=exif.tobytes(), quality=95)
        im.save(cls.d / "doc.pdf")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_a_sideways_phone_photo_is_read_upright(self):
        self.assertIn("Invoice number 4471", run(self.d / "phone.jpg").stdout)

    def test_greek_is_read_by_default_with_a_real_mu(self):
        out = run(self.d / "upright.png").stdout
        self.assertIn("Τιμολόγιο", out)
        self.assertIn("αριθμός", out, "micro sign in place of mu, or Greek read as Latin")

    def test_a_pdf_with_a_missing_language_fails_loudly(self):
        r = run(self.d / "doc.pdf", "--lang", "gre")
        self.assertEqual(r.returncode, 1, "empty output with exit 0")


if __name__ == "__main__":
    unittest.main()
