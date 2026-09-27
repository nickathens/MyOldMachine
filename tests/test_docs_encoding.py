"""docs convert.py fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('charset_normalizer', 'markitdown') if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "docs" / "scripts" / "convert.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=120)


class Encoding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_windows_greek_csv_keeps_its_letters(self):
        src = self.d / "greek.csv"
        src.write_bytes("Όνομα,Ποσό\nΓιώργος,120\n".encode("cp1253"))
        r = run(src)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Γιώργος", r.stdout, "cp1253 came back as Latin-1 mojibake")
        self.assertIn("Ποσό", r.stdout)

    def test_western_accents_are_not_mistaken_for_greek(self):
        src = self.d / "fr.csv"
        src.write_bytes("Café,Prix\nCrème,5\n".encode("cp1252"))
        r = run(src)
        self.assertIn("Café", r.stdout)
        self.assertIn("Crème", r.stdout)

    def test_english_headers_with_greek_values(self):
        src = self.d / "mixed.csv"
        src.write_bytes("Name,City\nJohn,Athens\nΓιώργος,Θεσσαλονίκη\n".encode("cp1253"))
        self.assertIn("Θεσσαλονίκη", run(src).stdout)

    def test_the_markitdown_route_reads_windows_greek_too(self):
        # review of #187: without anydoc (the Mac mini has none) every csv goes
        # to markitdown, which read this as Cyrillic, "Гйюсгпт" for "Γιώργος"
        src = self.d / "greek.csv"
        src.write_bytes("Όνομα,Ποσό\nΓιώργος,120\n".encode("cp1253"))
        r = run(src, "--backend", "markitdown")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Γιώργος", r.stdout)
        fr = self.d / "fr.csv"
        fr.write_bytes("Café,Prix\nCrème,5\n".encode("cp1252"))
        self.assertIn("Crème", run(fr, "--backend", "markitdown").stdout)

    def test_an_empty_conversion_says_so(self):
        # markitdown reads a pdf only with its pdf extra installed
        missing = [m for m in ("pdfminer", "pdfplumber") if _ilu.find_spec(m) is None]
        if missing:
            self.skipTest("markitdown's pdf support needs " + ", ".join(missing))
        from PIL import Image
        pdf = self.d / "scan.pdf"
        Image.new("RGB", (300, 400), "white").save(pdf)
        r = run(pdf)
        self.assertIn("no text found", r.stderr)

    def test_output_folder_is_created(self):
        src = self.d / "a.csv"
        src.write_text("a,b\n1,2\n", encoding="utf-8")
        out = self.d / "sub" / "out.md"
        r = run(src, "-o", out)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
