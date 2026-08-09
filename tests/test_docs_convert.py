"""Tests for the docs skill's backend router (skills/docs/scripts/convert.py).

Run: python3 -m unittest tests.test_docs_convert  (from repo root)

The skill used to be one backend, markitdown, for everything. It is now two,
routed per format, and the routing is the whole value: measured over a real
195 document corpus, anydoc is 28x faster on spreadsheets and emits none of
markitdown's NaN / "Unnamed: N" filler, while on pdf it failed to parse 15 of
107 files and dropped the fi ligature ("Please find below" -> "Please nd
below") on most of the rest.

So the contract these tests pin, in order of what it would cost to lose it:

  1. pdf NEVER reaches anydoc. Not by extension, and not by content either: a
     pdf arriving under an office extension is still a pdf. Losing this is
     silent -- the text still converts, it is just quietly corrupted.
  2. Spreadsheets and word processor formats DO reach anydoc, and the output
     really is free of the filler. This is the positive half: a test that only
     checked "nothing broke" would pass with the router reverted to
     markitdown-for-everything, which is exactly the change it must catch.
  3. anydoc declining a file falls back to markitdown, and says so. One of the
     72 workbooks in that corpus is malformed enough that anydoc refuses it;
     without the
     fallback the skill would simply fail on files it used to read.
  4. A forced backend does NOT fall back, so `--backend anydoc` measures
     anydoc rather than quietly reporting markitdown's work.
  5. Faults that are not ConvertError propagate. Swallowing a missing file or
     an OS error into a fallback would hide real problems.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "docs" / "scripts" / "convert.py"

_spec = importlib.util.spec_from_file_location("docs_convert", SCRIPT)
convert_mod = importlib.util.module_from_spec(_spec)
sys.modules["docs_convert"] = convert_mod
_spec.loader.exec_module(convert_mod)

try:
    import anydoc  # noqa: F401

    HAVE_ANYDOC = True
except ImportError:
    HAVE_ANYDOC = False

try:
    import markitdown  # noqa: F401

    HAVE_MARKITDOWN = True
except ImportError:
    HAVE_MARKITDOWN = False

try:
    import openpyxl

    HAVE_OPENPYXL = True
except ImportError:
    HAVE_OPENPYXL = False

PDF_BYTES = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


class RoutingTable(unittest.TestCase):
    """Which backend a file goes to, decided before any conversion runs.

    anydoc's presence is forced on here so these test the routing rule itself
    and not this machine's package list. Without that, a runner with no anydoc
    installed (CI is one) would see every case degrade to markitdown and the
    whole class would pass while asserting nothing.
    """

    def setUp(self):
        patcher = mock.patch.object(convert_mod, "_have_anydoc", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pdf_never_routes_to_anydoc(self):
        self.assertEqual(convert_mod.pick_backend(Path("/tmp/x.pdf")), "markitdown")
        self.assertEqual(convert_mod.pick_backend(Path("/tmp/X.PDF")), "markitdown")
        self.assertNotIn(".pdf", convert_mod.ANYDOC_FIRST)

    def test_office_and_csv_route_to_anydoc(self):
        for ext in (".docx", ".doc", ".odt", ".rtf", ".epub", ".pptx", ".odp", ".xlsx", ".ods", ".csv"):
            with self.subTest(ext=ext):
                self.assertEqual(convert_mod.pick_backend(Path("/tmp/x" + ext)), "anydoc")

    def test_extension_case_is_ignored(self):
        self.assertEqual(convert_mod.pick_backend(Path("/tmp/BOOKS.XLSX")), "anydoc")

    def test_unknown_and_markitdown_only_formats_go_to_markitdown(self):
        for ext in (".html", ".htm", ".png", ".jpg", ".mp3", ".zip", ".txt", ".weirdext", ""):
            with self.subTest(ext=ext):
                self.assertEqual(convert_mod.pick_backend(Path("/tmp/x" + ext)), "markitdown")

    @unittest.skipUnless(HAVE_ANYDOC, "anydoc not installed")
    def test_every_routed_extension_is_one_anydoc_actually_supports(self):
        """Guards against anydoc dropping a format: the list would go stale silently."""
        import anydoc

        for ext in convert_mod.ANYDOC_FIRST:
            with self.subTest(ext=ext):
                self.assertIsNotNone(
                    anydoc.format_from_extension(ext.lstrip(".")),
                    f"{ext} is routed to anydoc but anydoc no longer claims it",
                )

    @unittest.skipUnless(HAVE_ANYDOC, "anydoc not installed")
    def test_pdf_is_excluded_deliberately_not_because_anydoc_lacks_it(self):
        """anydoc reads pdf. It is excluded on quality, so pin that it still can."""
        import anydoc

        self.assertIsNotNone(anydoc.format_from_extension("pdf"))


class ContentSniffing(unittest.TestCase):
    """An office extension over pdf bytes is still pdf."""

    @unittest.skipUnless(HAVE_ANYDOC, "anydoc not installed")
    def test_pdf_bytes_under_office_extension_go_to_markitdown(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "invoice.docx"
            p.write_bytes(PDF_BYTES)
            self.assertEqual(convert_mod.pick_backend(p), "markitdown")

    @unittest.skipUnless(HAVE_ANYDOC, "anydoc not installed")
    def test_genuine_office_bytes_still_go_to_anydoc(self):
        """The sniff must not swallow the normal case: only pdf diverts."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "books.xlsx"
            p.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
            self.assertEqual(convert_mod.pick_backend(p), "anydoc")

    def test_sniff_on_an_unreadable_file_does_not_raise(self):
        self.assertFalse(convert_mod._looks_like_pdf(Path("/tmp/definitely-absent-9f3a.docx")))


class AnydocAbsent(unittest.TestCase):
    """An install where anydoc never landed must still convert documents."""

    def test_office_formats_degrade_to_markitdown(self):
        with mock.patch.object(convert_mod, "_have_anydoc", return_value=False):
            for ext in (".xlsx", ".docx", ".csv", ".doc"):
                with self.subTest(ext=ext):
                    self.assertEqual(convert_mod.pick_backend(Path("/tmp/x" + ext)), "markitdown")

    def test_convert_uses_markitdown_when_anydoc_is_absent(self):
        with mock.patch.object(convert_mod, "_have_anydoc", return_value=False), mock.patch.object(
            convert_mod, "_via_anydoc", side_effect=AssertionError("must not run")
        ), mock.patch.object(convert_mod, "_via_markitdown", return_value="ok"):
            text, used = convert_mod.convert(Path(__file__))
        self.assertEqual((text, used), ("ok", "markitdown"))


class FallbackBehaviour(unittest.TestCase):
    """anydoc declining a file must degrade to markitdown, loudly."""

    def setUp(self):
        self.path = Path(__file__)  # a real file, so the is_file() gate passes

    @unittest.skipUnless(HAVE_ANYDOC, "anydoc not installed")
    def test_convert_error_falls_back_and_is_announced(self):
        import anydoc

        with mock.patch.object(convert_mod, "pick_backend", return_value="anydoc"), mock.patch.object(
            convert_mod, "_via_anydoc", side_effect=anydoc.MalformedError("broken")
        ), mock.patch.object(convert_mod, "_via_markitdown", return_value="rescued"):
            err = io.StringIO()
            with redirect_stderr(err):
                text, used = convert_mod.convert(self.path)
        self.assertEqual(text, "rescued")
        self.assertIn("markitdown", used)
        self.assertIn("declined", err.getvalue())

    @unittest.skipUnless(HAVE_ANYDOC, "anydoc not installed")
    def test_non_convert_error_propagates(self):
        with mock.patch.object(convert_mod, "pick_backend", return_value="anydoc"), mock.patch.object(
            convert_mod, "_via_anydoc", side_effect=OSError("disk went away")
        ), mock.patch.object(convert_mod, "_via_markitdown", return_value="rescued"):
            with self.assertRaises(OSError):
                convert_mod.convert(self.path)

    @unittest.skipUnless(HAVE_ANYDOC, "anydoc not installed")
    def test_forced_anydoc_does_not_fall_back(self):
        import anydoc

        with mock.patch.object(
            convert_mod, "_via_anydoc", side_effect=anydoc.MalformedError("broken")
        ), mock.patch.object(convert_mod, "_via_markitdown", return_value="rescued"):
            with self.assertRaises(anydoc.ConvertError):
                convert_mod.convert(self.path, backend="anydoc")

    def test_forced_markitdown_never_calls_anydoc(self):
        with mock.patch.object(convert_mod, "_via_anydoc", side_effect=AssertionError("must not run")), mock.patch.object(
            convert_mod, "_via_markitdown", return_value="ok"
        ):
            text, used = convert_mod.convert(self.path, backend="markitdown")
        self.assertEqual((text, used), ("ok", "markitdown"))

    def test_missing_file_raises_before_any_backend_runs(self):
        with mock.patch.object(convert_mod, "_via_anydoc", side_effect=AssertionError("must not run")), mock.patch.object(
            convert_mod, "_via_markitdown", side_effect=AssertionError("must not run")
        ):
            with self.assertRaises(FileNotFoundError):
                convert_mod.convert(Path("/tmp/absent-2b7c.docx"))

    def test_a_directory_is_not_a_document(self):
        with self.assertRaises(FileNotFoundError):
            convert_mod.convert(ROOT)


@unittest.skipUnless(HAVE_ANYDOC and HAVE_MARKITDOWN and HAVE_OPENPYXL, "needs anydoc, markitdown and openpyxl")
class RealConversion(unittest.TestCase):
    """The positive half: routing must actually change the output.

    A test that only asserted "conversion succeeded" would pass with the router
    reverted to markitdown for everything. These assert the difference.
    """

    @classmethod
    def setUpClass(cls):
        import tempfile

        cls._td = tempfile.TemporaryDirectory()
        cls.xlsx = Path(cls._td.name) / "ledger.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Ledger"
        ws.append(["Date", "Client", "Amount"])
        ws.append(["2026-01-14", "MODIANO", 2560])
        ws.append(["Subtotal", None, None])
        ws.append([None, None, 2560])
        wb.save(cls.xlsx)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_markitdown_really_does_emit_the_filler(self):
        """Without this, the assertion below could pass for the wrong reason."""
        text, used = convert_mod.convert(self.xlsx, backend="markitdown")
        self.assertEqual(used, "markitdown")
        self.assertIn("NaN", text)
        self.assertIn("2560.0", text)

    def test_routed_spreadsheet_is_clean_and_came_from_anydoc(self):
        text, used = convert_mod.convert(self.xlsx)
        self.assertEqual(used, "anydoc")
        self.assertNotIn("NaN", text)
        self.assertNotIn("Unnamed:", text)
        self.assertNotIn("2560.0", text)
        self.assertIn("MODIANO", text)
        self.assertIn("2560", text)

    def test_no_cell_content_is_lost_against_markitdown(self):
        a, _ = convert_mod.convert(self.xlsx)
        m, _ = convert_mod.convert(self.xlsx, backend="markitdown")
        for token in ("Date", "Client", "Amount", "2026-01-14", "MODIANO", "Subtotal"):
            with self.subTest(token=token):
                self.assertIn(token, m)
                self.assertIn(token, a)

    def test_single_sheet_heading_is_the_known_cost(self):
        """Documented difference, pinned so it is never mistaken for a new bug."""
        a, _ = convert_mod.convert(self.xlsx)
        m, _ = convert_mod.convert(self.xlsx, backend="markitdown")
        self.assertIn("## Ledger", m)
        self.assertNotIn("## Ledger", a)

    def test_csv_round_trips_through_anydoc(self):
        p = Path(self._td.name) / "cards.csv"
        p.write_text("player,team,rating\nGiannis,MIL,97\n", encoding="utf-8")
        text, used = convert_mod.convert(p)
        self.assertEqual(used, "anydoc")
        self.assertIn("Giannis", text)
        self.assertIn("97", text)

    def test_pdf_is_converted_by_markitdown_end_to_end(self):
        import reportlab.pdfgen.canvas as canvas_mod

        p = Path(self._td.name) / "note.pdf"
        c = canvas_mod.Canvas(str(p))
        c.drawString(72, 720, "Please find below a cost-breakdown")
        c.save()
        text, used = convert_mod.convert(p)
        self.assertEqual(used, "markitdown")
        self.assertIn("find below", text)


class CommandLine(unittest.TestCase):
    """stdout stays pure markdown; the backend note goes to stderr."""

    def test_markdown_on_stdout_backend_on_stderr(self):
        with mock.patch.object(convert_mod, "convert", return_value=("# Title\n", "anydoc")):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = convert_mod.main([str(Path(__file__))])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), "# Title\n")
        self.assertIn("anydoc", err.getvalue())
        self.assertNotIn("anydoc", out.getvalue())

    def test_output_flag_writes_the_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.md"
            with mock.patch.object(convert_mod, "convert", return_value=("body", "anydoc")):
                err = io.StringIO()
                with redirect_stderr(err):
                    rc = convert_mod.main([str(Path(__file__)), "-o", str(dest)])
            self.assertEqual(rc, 0)
            self.assertEqual(dest.read_text(encoding="utf-8"), "body")
            self.assertIn(str(dest), err.getvalue())

    def test_failure_exits_nonzero_with_a_reason(self):
        with mock.patch.object(convert_mod, "convert", side_effect=FileNotFoundError("gone")):
            err = io.StringIO()
            with redirect_stderr(err):
                rc = convert_mod.main(["/tmp/absent-77.docx"])
        self.assertEqual(rc, 1)
        self.assertIn("FileNotFoundError", err.getvalue())

    def test_backend_flag_is_passed_through(self):
        with mock.patch.object(convert_mod, "convert", return_value=("x", "anydoc")) as conv:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                convert_mod.main([str(Path(__file__)), "--backend", "markitdown"])
        self.assertEqual(conv.call_args.args[1], "markitdown")

    def test_greek_survives_a_non_utf8_stdout(self):
        """Greek ledgers are the normal case, and stdout is not always utf-8.

        Python coerces a bare C locale to utf-8 by itself, so that case proves
        nothing. An ISO-8859 locale or a set PYTHONIOENCODING is NOT coerced,
        and writing Greek to that stream raises UnicodeEncodeError. Driven with
        a real latin-1 stream rather than a subprocess, so it needs no backend
        installed and runs on a bare CI runner too.
        """
        sink = io.BytesIO()
        stream = io.TextIOWrapper(sink, encoding="latin-1", newline="")
        self.assertEqual(stream.encoding, "latin-1", "fixture must start non-utf8")
        with mock.patch.object(convert_mod, "convert", return_value=("ΜΕΜΟΝΩΜΕΝΑ", "anydoc")), mock.patch.object(
            sys, "stdout", stream
        ), redirect_stderr(io.StringIO()):
            rc = convert_mod.main([str(Path(__file__))])
            stream.flush()
        self.assertEqual(rc, 0)
        self.assertEqual(sink.getvalue().decode("utf-8"), "ΜΕΜΟΝΩΜΕΝΑ")

    def test_an_unknown_backend_is_rejected(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                convert_mod.main([str(Path(__file__)), "--backend", "pandoc"])


class SkillTreeIsLinted(unittest.TestCase):
    """CI lints bot.py core/ utils/ install/ miniapp/ tests/ and never skills/.

    So a lint error in a skill script is invisible to a green pipeline. These
    two files are the skill's whole implementation; lint them from a test,
    which CI does run.
    """

    def test_ruff_is_clean_on_the_docs_scripts(self):
        import shutil
        import subprocess

        ruff = shutil.which("ruff")
        if not ruff:
            self.skipTest("ruff not installed")
        scripts = sorted((ROOT / "skills" / "docs" / "scripts").glob("*.py"))
        self.assertTrue(scripts, "no scripts found to lint")
        proc = subprocess.run(
            [ruff, "check", *[str(p) for p in scripts]],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
