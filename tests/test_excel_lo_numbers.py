"""excel_lo.py: a cell becomes a number only when the number is unambiguous.

Linux bot review 2026-09-27, S031. write and add-rows turned text into numbers with
float(v.replace(',', '.')): '2.500' (Greek two thousand five hundred) was
stored as 2.5, '0012' as 12, 'nan' as NaN, '1e5' as 100000. Money in invoice
and expense sheets was silently wrong.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "spreadsheet" / "scripts" / "excel_lo.py"
spec = importlib.util.spec_from_file_location("excel_lo_under_test", str(SCRIPT))
xl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xl)


class ParseNumber(unittest.TestCase):
    def test_plain_and_unambiguous_decimals(self):
        cases = {
            "12": 12.0, "-5": -5.0, "0": 0.0, "+7": 7.0,
            "2.5": 2.5, "2,5": 2.5, "0.500": 0.5, "0,500": 0.5,
            "3.14159": 3.14159, "1234.567": 1234.567, "10,25": 10.25,
            "1.234,56": 1234.56, "1,234.56": 1234.56,
            "1.234.567": 1234567.0, "1,234,567": 1234567.0,
            "-1.234,5": -1234.5, " 42 ": 42.0,
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(xl.parse_number(text), want)

    def test_text_stays_text(self):
        for text in ("0012", "007", "-007", "1e5", "nan", "inf", "Infinity",
                     "1_000", "12%", "€1.234,56", "1.2.3", "1,23,456",
                     "", "abc", "2026-09-27", "١٢", "1.23,4", "1,234.5.6",
                     "12.", ".5", "1 000"):
            with self.subTest(text=text):
                self.assertIsNone(xl.parse_number(text))

    def test_three_decimals_after_one_mark_is_ambiguous(self):
        for text in ("2.500", "1,234", "999.999", "-2.500"):
            with self.subTest(text=text):
                with self.assertRaises(xl.AmbiguousNumber):
                    xl.parse_number(text)

    def test_decimal_comma_reads_greek(self):
        cases = {"2.500": 2500.0, "1,234": 1.234, "2,5": 2.5,
                 "1.234,56": 1234.56, "12": 12.0}
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(xl.parse_number(text, decimal="comma"), want)
        # English grouping is not a Greek number: kept as typed.
        self.assertIsNone(xl.parse_number("1,234.56", decimal="comma"))
        self.assertIsNone(xl.parse_number("2.5", decimal="comma"))

    def test_decimal_dot_reads_english(self):
        cases = {"2.500": 2.5, "1,234": 1234.0, "2.5": 2.5,
                 "1,234.56": 1234.56}
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(xl.parse_number(text, decimal="dot"), want)
        self.assertIsNone(xl.parse_number("1.234,56", decimal="dot"))
        self.assertIsNone(xl.parse_number("2,5", decimal="dot"))


class _Cell:
    def __init__(self):
        self.value = None
        self.string = None

    def setValue(self, v):
        self.value = v

    def setString(self, s):
        self.string = s


class _Sheet:
    def __init__(self):
        self.cells = {}
        self.inserted = None

    def getCellByPosition(self, col, row):
        return self.cells.setdefault((col, row), _Cell())

    def getRows(self):
        sheet = self

        class _Rows:
            def insertByIndex(self, index, count):
                sheet.inserted = (index, count)

        return _Rows()


class _Doc:
    def __init__(self, sheet):
        self.sheet = sheet
        self.stored = False
        self.closed = False

    def getSheets(self):
        doc = self

        class _Sheets:
            def getByName(self, name):
                return doc.sheet

        return _Sheets()

    def store(self):
        self.stored = True

    def close(self, flag):
        self.closed = True


class Commands(unittest.TestCase):
    def _run(self, func, **kw):
        sheet = _Sheet()
        doc = _Doc(sheet)
        with mock.patch.object(xl, "start_libreoffice"), \
                mock.patch.object(xl, "get_desktop"), \
                mock.patch.object(xl, "open_document", return_value=doc), \
                mock.patch("builtins.print") as out:
            func(SimpleNamespace(file="f.xlsx", sheet="S", **kw))
        printed = json.loads(out.call_args[0][0]) if out.called else None
        return sheet, doc, printed

    def test_write_ambiguous_refuses_and_stores_nothing(self):
        sheet = _Sheet()
        doc = _Doc(sheet)
        with mock.patch.object(xl, "start_libreoffice"), \
                mock.patch.object(xl, "get_desktop"), \
                mock.patch.object(xl, "open_document", return_value=doc):
            with self.assertRaises(xl.AmbiguousNumber) as cm:
                xl.cmd_write(SimpleNamespace(file="f", sheet="S", cell="B2",
                                             value="2.500", as_text=False, decimal=None))
        self.assertFalse(doc.stored)
        self.assertIn("--decimal comma", str(cm.exception))

    def test_write_with_decimal_comma(self):
        sheet, doc, printed = self._run(xl.cmd_write, cell="B2", value="2.500",
                                        as_text=False, decimal="comma")
        self.assertEqual(sheet.cells[(1, 1)].value, 2500.0)
        self.assertTrue(doc.stored)

    def test_write_id_with_leading_zeros_is_text(self):
        sheet, doc, printed = self._run(xl.cmd_write, cell="A1", value="0012",
                                        as_text=False, decimal=None)
        self.assertEqual(sheet.cells[(0, 0)].string, "0012")
        self.assertIsNone(sheet.cells[(0, 0)].value)
        self.assertEqual(printed["stored_as"], "text")

    def test_add_rows_validates_every_cell_before_inserting(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump([["Rent", "450"], ["Studio", "2.500"]], fh)
        sheet = _Sheet()
        doc = _Doc(sheet)
        with mock.patch.object(xl, "start_libreoffice"), \
                mock.patch.object(xl, "get_desktop"), \
                mock.patch.object(xl, "open_document", return_value=doc):
            with self.assertRaises(xl.AmbiguousNumber) as cm:
                xl.cmd_add_rows(SimpleNamespace(file="f", sheet="S", after=3,
                                                data=fh.name, as_text=False, decimal=None))
        self.assertIsNone(sheet.inserted)
        self.assertFalse(doc.stored)
        self.assertIn("B5", str(cm.exception))

    def test_add_rows_json_numbers_and_text(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write('[["Invoice 0012", "0012", 2500, 2.5, true, NaN, "2.500"]]')
        sheet, doc, printed = self._run(xl.cmd_add_rows, after=1, data=fh.name,
                                        as_text=False, decimal="comma")
        row = 1
        self.assertEqual(sheet.inserted, (1, 1))
        self.assertEqual(sheet.cells[(0, row)].string, "Invoice 0012")
        self.assertEqual(sheet.cells[(1, row)].string, "0012")
        self.assertEqual(sheet.cells[(2, row)].value, 2500)
        self.assertEqual(sheet.cells[(3, row)].value, 2.5)
        self.assertEqual(sheet.cells[(4, row)].string, "True")
        self.assertEqual(sheet.cells[(5, row)].string, "nan")
        self.assertEqual(sheet.cells[(6, row)].value, 2500.0)
        self.assertEqual(printed["stored_as_text"], ["A2", "B2", "E2", "F2"])

    def test_add_rows_as_text(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump([["2.500", 7]], fh)
        sheet, doc, printed = self._run(xl.cmd_add_rows, after=0, data=fh.name,
                                        as_text=True, decimal=None)
        self.assertEqual(sheet.cells[(0, 0)].string, "2.500")
        self.assertEqual(sheet.cells[(1, 0)].string, "7")

    def test_cli_accepts_the_new_options(self):
        parser_args = ["excel_lo.py", "add-rows", "f.xlsx", "--sheet", "S", "--after", "1",
                       "--data", "d.json", "--decimal", "comma", "--as-text"]
        with mock.patch("sys.argv", parser_args), \
                mock.patch.object(xl, "cmd_add_rows") as fake, \
                mock.patch.object(xl, "stop_libreoffice"):
            xl.main()
        args = fake.call_args[0][0]
        self.assertEqual((args.decimal, args.as_text), ("comma", True))


if __name__ == "__main__":
    unittest.main()
