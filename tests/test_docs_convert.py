"""Tests for the docs skill's backend router (skills/docs/scripts/convert.py).

Run: python3 -m unittest tests.test_docs_convert  (from repo root)

markitdown owns every format. Exactly four are routed to anydoc instead, and
each one is there because markitdown measurably loses data on it, measured
over a real 195 document corpus. The router started out with ten, six of which
were speed or a guess copied from anydoc's format table, and one of those six
(docx) was routed to a backend that corrupts words at field boundaries.

So the contract these tests pin, in order of what it would cost to lose it:

  1. pdf NEVER reaches anydoc. Not by extension, and not by content either: a
     pdf arriving under a routed extension is still a pdf. Losing this is
     silent, the text still converts, it is just quietly corrupted.
  2. The routing table is exactly the four measured formats. Widening it back
     is the regression, and it is silent too: docx through anydoc still
     produces plausible text with one word broken.
  3. The four that ARE routed really do lose data on markitdown, driven with
     real fixtures rather than asserted. A test that only checked "conversion
     succeeded" would pass with the router reverted to markitdown for
     everything, which is exactly the change it must catch.
  4. anydoc declining a file falls back to markitdown, and says so.
  5. anydoc that is installed but does not LOAD also falls back. It is a
     compiled Rust extension, so a findable module that raises on import is
     the realistic failure, and asking importlib whether it is findable does
     not catch it.
  6. A forced backend does NOT fall back, so `--backend anydoc` measures
     anydoc rather than quietly reporting markitdown's work.
  7. Faults that are not ConvertError propagate. Swallowing a missing file or
     an OS error into a fallback would hide real problems.
"""

from __future__ import annotations

import importlib
import importlib.util
import io
import sys
import tempfile
import unittest
import zipfile
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

# The four formats the router sends to anydoc, restated here rather than
# imported, so widening the module's own set fails a test instead of being
# audited against itself.
MEASURED_ANYDOC_FORMATS = {".doc", ".ods", ".xlsx", ".csv"}

# Routed in the first version and removed: docx on measured corruption, the
# rest because no file of that type existed in the corpus to measure.
UNROUTED_OFFICE_FORMATS = (".docx", ".odt", ".rtf", ".epub", ".pptx", ".odp")

PDF_BYTES = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)

# A csv whose first line is a comment. This is the shape that costs markitdown
# 3298 of 3824 cells on the real file this was reduced from: pandas takes the
# comment as the header, infers one column, and drops the rest.
COMMENT_HEADER_CSV = (
    "# Data source: https://example.invalid\n"
    "Date,Client,Amount\n"
    "2026-01-14,NORTHWIND,2560\n"
    "2026-02-01,ACME,1800\n"
)

ODS_CONTENT_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    ' xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"'
    ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">'
    "<office:body><office:spreadsheet>"
    '<table:table table:name="Ledger">'
    "<table:table-row>"
    "<table:table-cell><text:p>NORTHWIND</text:p></table:table-cell>"
    "<table:table-cell><text:p>2560</text:p></table:table-cell>"
    "</table:table-row>"
    "</table:table></office:spreadsheet></office:body></office:document-content>"
)

DOCX_DOCUMENT_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p><w:r><w:t>Please contact NORTHWIND about invoice 2560.</w:t></w:r></w:p></w:body>"
    "</w:document>"
)
DOCX_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1"'
    ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
    ' Target="word/document.xml"/></Relationships>'
)
DOCX_CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/></Types>'
)


def write_minimal_ods(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        z.writestr("content.xml", ODS_CONTENT_XML)
    return path


def write_minimal_docx(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", DOCX_CONTENT_TYPES_XML)
        z.writestr("_rels/.rels", DOCX_RELS_XML)
        z.writestr("word/document.xml", DOCX_DOCUMENT_XML)
    return path


class RoutingTable(unittest.TestCase):
    """Which backend a file goes to, decided before any conversion runs.

    anydoc's presence is forced on here so these test the routing rule itself
    and not the runner's package list. Without that, a runner with no anydoc
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

    def test_the_routing_table_is_exactly_the_four_measured_formats(self):
        """Widening it back is the regression this whole module exists to catch."""
        self.assertEqual(set(convert_mod.ANYDOC_FIRST), MEASURED_ANYDOC_FORMATS)

    def test_the_four_measured_formats_route_to_anydoc(self):
        for ext in sorted(MEASURED_ANYDOC_FORMATS):
            with self.subTest(ext=ext):
                self.assertEqual(convert_mod.pick_backend(Path("/tmp/x" + ext)), "anydoc")

    def test_formats_removed_from_the_router_go_to_markitdown(self):
        """docx on measured corruption, the other five because nothing measured them.

        This is the positive half of the cut. Without it, quietly putting docx
        back would break nothing visible: anydoc still returns plausible text,
        with one word broken at a field boundary.
        """
        for ext in UNROUTED_OFFICE_FORMATS:
            with self.subTest(ext=ext):
                self.assertEqual(convert_mod.pick_backend(Path("/tmp/x" + ext)), "markitdown")
                self.assertNotIn(ext, convert_mod.ANYDOC_FIRST)

    def test_extension_case_is_ignored(self):
        self.assertEqual(convert_mod.pick_backend(Path("/tmp/BOOKS.XLSX")), "anydoc")
        self.assertEqual(convert_mod.pick_backend(Path("/tmp/LEDGER.ODS")), "anydoc")

    def test_unknown_and_markitdown_only_formats_go_to_markitdown(self):
        for ext in (".html", ".htm", ".png", ".jpg", ".mp3", ".zip", ".txt", ".weirdext", ""):
            with self.subTest(ext=ext):
                self.assertEqual(convert_mod.pick_backend(Path("/tmp/x" + ext)), "markitdown")


@unittest.skipUnless(HAVE_ANYDOC, "anydoc not installed")
class RoutedFormatsAreDeliberate(unittest.TestCase):
    """Each exclusion must be a judgement, not a capability gap gone stale."""

    def test_every_routed_extension_is_one_anydoc_actually_supports(self):
        """Guards against anydoc dropping a format: the list would go stale silently."""
        import anydoc

        for ext in convert_mod.ANYDOC_FIRST:
            with self.subTest(ext=ext):
                self.assertIsNotNone(
                    anydoc.format_from_extension(ext.lstrip(".")),
                    f"{ext} is routed to anydoc but anydoc no longer claims it",
                )

    def test_pdf_and_docx_are_excluded_on_quality_not_capability(self):
        """anydoc reads both. They are excluded because it reads them badly."""
        import anydoc

        for fmt in ("pdf", "docx"):
            with self.subTest(fmt=fmt):
                self.assertIsNotNone(anydoc.format_from_extension(fmt))


class ContentSniffing(unittest.TestCase):
    """A routed extension over pdf bytes is still pdf."""

    @unittest.skipUnless(HAVE_ANYDOC, "anydoc not installed")
    def test_pdf_bytes_under_a_routed_extension_go_to_markitdown(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "invoice.xlsx"
            p.write_bytes(PDF_BYTES)
            self.assertEqual(convert_mod.pick_backend(p), "markitdown")

    @unittest.skipUnless(HAVE_ANYDOC, "anydoc not installed")
    def test_genuine_office_bytes_still_go_to_anydoc(self):
        """The sniff must not swallow the normal case: only pdf diverts."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "books.xlsx"
            p.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
            self.assertEqual(convert_mod.pick_backend(p), "anydoc")

    def test_sniff_on_an_unreadable_file_does_not_raise(self):
        self.assertFalse(convert_mod._looks_like_pdf(Path("/tmp/definitely-absent-9f3a.xlsx")))


class AnydocAbsent(unittest.TestCase):
    """An install where anydoc never landed must still convert documents."""

    def test_routed_formats_degrade_to_markitdown(self):
        with mock.patch.object(convert_mod, "_have_anydoc", return_value=False):
            for ext in sorted(MEASURED_ANYDOC_FORMATS):
                with self.subTest(ext=ext):
                    self.assertEqual(convert_mod.pick_backend(Path("/tmp/x" + ext)), "markitdown")

    def test_convert_uses_markitdown_when_anydoc_is_absent(self):
        with mock.patch.object(convert_mod, "_have_anydoc", return_value=False), mock.patch.object(
            convert_mod, "_via_anydoc", side_effect=AssertionError("must not run")
        ), mock.patch.object(convert_mod, "_via_markitdown", return_value="ok"):
            text, used = convert_mod.convert(Path(__file__))
        self.assertEqual((text, used), ("ok", "markitdown"))


class BrokenAnydocDegrades(unittest.TestCase):
    """anydoc that is findable but does not load must degrade, not explode.

    This is the shape a compiled Rust extension fails in: the module sits
    right there on sys.path, so importlib.util.find_spec() answers yes, and
    importing it raises because the shared object will not link. The router
    used to ask find_spec, so it routed to a backend it could not call and
    every office document failed instead of falling back.

    Reproduced with a package that raises on execution rather than described,
    and the first test asserts find_spec still says yes, which is what makes
    this a test of the fix rather than of the platform.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        pkg = Path(self._td.name) / "anydoc"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "raise ImportError('libanydoc.so: cannot open shared object file')\n",
            encoding="utf-8",
        )
        saved = sys.modules.pop("anydoc", None)
        # addCleanup is LIFO, so these run bottom to top: drop the broken
        # module, take the shim off the path, put the real one back.
        if saved is not None:
            self.addCleanup(sys.modules.__setitem__, "anydoc", saved)
        self.addCleanup(sys.path.remove, self._td.name)
        self.addCleanup(sys.modules.pop, "anydoc", None)
        sys.path.insert(0, self._td.name)
        importlib.invalidate_caches()

    def test_the_module_is_findable_and_still_will_not_import(self):
        """The trap itself. If this stops holding, the rest proves nothing."""
        self.assertIsNotNone(importlib.util.find_spec("anydoc"))
        with self.assertRaises(ImportError):
            importlib.import_module("anydoc")

    def test_have_anydoc_reports_false(self):
        self.assertFalse(convert_mod._have_anydoc())
        self.assertIsNone(convert_mod._anydoc())

    def test_a_routed_file_still_converts_through_markitdown(self):
        p = Path(self._td.name) / "ledger.xlsx"
        p.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
        with mock.patch.object(convert_mod, "_via_markitdown", return_value="rescued"):
            text, used = convert_mod.convert(p)
        self.assertEqual((text, used), ("rescued", "markitdown"))

    def test_the_sniff_does_not_raise_either(self):
        p = Path(self._td.name) / "sniffme.xlsx"
        p.write_bytes(PDF_BYTES)
        self.assertFalse(convert_mod._looks_like_pdf(p))

    def test_convert_falls_back_if_anydoc_dies_after_the_routing_decision(self):
        """The defensive branch: pick_backend said anydoc, then it went away.

        Unreachable through pick_backend once _have_anydoc does a real import,
        which is exactly why it needs driving directly. Otherwise deleting it
        would survive every test and only surface as an AttributeError on a
        machine whose install broke mid run.
        """
        p = Path(self._td.name) / "ledger.xlsx"
        p.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
        with mock.patch.object(convert_mod, "_have_anydoc", return_value=True), mock.patch.object(
            convert_mod, "_anydoc", return_value=None
        ), mock.patch.object(convert_mod, "_looks_like_pdf", return_value=False), mock.patch.object(
            convert_mod, "_via_markitdown", return_value="rescued"
        ):
            err = io.StringIO()
            with redirect_stderr(err):
                text, used = convert_mod.convert(p)
        self.assertEqual(text, "rescued")
        self.assertIn("unusable", used)
        self.assertIn("unusable", err.getvalue())


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
                convert_mod.convert(Path("/tmp/absent-2b7c.xlsx"))

    def test_a_directory_is_not_a_document(self):
        with self.assertRaises(FileNotFoundError):
            convert_mod.convert(ROOT)


@unittest.skipUnless(HAVE_ANYDOC and HAVE_MARKITDOWN and HAVE_OPENPYXL, "needs anydoc, markitdown and openpyxl")
class RealConversion(unittest.TestCase):
    """The positive half: each routed format must really lose data on markitdown.

    A test that only asserted "conversion succeeded" would pass with the router
    reverted to markitdown for everything. Each case here drives markitdown
    first as a control, so the assertion about anydoc cannot pass for the wrong
    reason.
    """

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._td.name)
        cls.xlsx = cls.tmp / "ledger.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Ledger"
        ws.append(["Date", "Client", "Amount"])
        ws.append(["2026-01-14", "NORTHWIND", 2560])
        ws.append(["Subtotal", None, None])
        ws.append([None, None, 2560])
        wb.save(cls.xlsx)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    # --- xlsx: the filler ------------------------------------------------

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
        self.assertIn("NORTHWIND", text)
        self.assertIn("2560", text)

    def test_no_cell_content_is_lost_against_markitdown(self):
        a, _ = convert_mod.convert(self.xlsx)
        m, _ = convert_mod.convert(self.xlsx, backend="markitdown")
        for token in ("Date", "Client", "Amount", "2026-01-14", "NORTHWIND", "Subtotal"):
            with self.subTest(token=token):
                self.assertIn(token, m)
                self.assertIn(token, a)

    def test_single_sheet_heading_is_the_known_cost(self):
        """Documented difference, pinned so it is never mistaken for a new bug."""
        a, _ = convert_mod.convert(self.xlsx)
        m, _ = convert_mod.convert(self.xlsx, backend="markitdown")
        self.assertIn("## Ledger", m)
        self.assertNotIn("## Ledger", a)

    # --- csv: the dropped columns ----------------------------------------

    def test_markitdown_drops_csv_columns_when_it_misreads_the_header(self):
        """The actual reason csv is routed. Filler tokens were never it.

        Reduced from a real 11 column file where this cost 3298 of 3824 cells.
        """
        p = self.tmp / "with_comment.csv"
        p.write_text(COMMENT_HEADER_CSV, encoding="utf-8")
        m, used = convert_mod.convert(p, backend="markitdown")
        self.assertEqual(used, "markitdown")
        for token in ("Client", "Amount", "NORTHWIND", "ACME", "2560", "1800"):
            with self.subTest(token=token):
                self.assertNotIn(token, m)

    def test_routed_csv_keeps_every_cell(self):
        p = self.tmp / "with_comment.csv"
        p.write_text(COMMENT_HEADER_CSV, encoding="utf-8")
        a, used = convert_mod.convert(p)
        self.assertEqual(used, "anydoc")
        for token in ("Date", "Client", "Amount", "NORTHWIND", "ACME", "2560", "1800"):
            with self.subTest(token=token):
                self.assertIn(token, a)

    def test_a_clean_csv_is_not_where_the_difference_lives(self):
        """Honest bound on the claim: routing csv buys the tail, not the mean."""
        p = self.tmp / "clean.csv"
        p.write_text("player,team,rating\nGiannis,MIL,97\n", encoding="utf-8")
        a, used = convert_mod.convert(p)
        m, _ = convert_mod.convert(p, backend="markitdown")
        self.assertEqual(used, "anydoc")
        for token in ("Giannis", "MIL", "97"):
            with self.subTest(token=token):
                self.assertIn(token, a)
                self.assertIn(token, m)

    # --- ods: markitdown cannot open it at all ---------------------------

    def test_markitdown_cannot_open_ods_at_all(self):
        from markitdown import MarkItDown

        p = write_minimal_ods(self.tmp / "ledger.ods")
        with self.assertRaises(Exception) as ctx:
            MarkItDown().convert(str(p))
        self.assertIn("Unsupported", type(ctx.exception).__name__)

    def test_routed_ods_is_read_by_anydoc(self):
        p = write_minimal_ods(self.tmp / "ledger.ods")
        text, used = convert_mod.convert(p)
        self.assertEqual(used, "anydoc")
        self.assertIn("NORTHWIND", text)
        self.assertIn("2560", text)

    # --- the cut has to land somewhere that works ------------------------

    def test_docx_now_goes_to_markitdown_and_converts(self):
        """The cut is only safe if markitdown really reads docx."""
        p = write_minimal_docx(self.tmp / "letter.docx")
        text, used = convert_mod.convert(p)
        self.assertEqual(used, "markitdown")
        self.assertIn("contact", text)
        self.assertIn("NORTHWIND", text)

    def test_pdf_is_converted_by_markitdown_end_to_end(self):
        import reportlab.pdfgen.canvas as canvas_mod

        p = self.tmp / "note.pdf"
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
                rc = convert_mod.main(["/tmp/absent-77.xlsx"])
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


if __name__ == "__main__":
    unittest.main()
