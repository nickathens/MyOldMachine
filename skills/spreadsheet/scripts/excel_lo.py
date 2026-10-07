#!/usr/bin/env python3
"""
Excel helper using LibreOffice UNO bridge.

Must be run with /usr/bin/python3 (system Python) since UNO bindings
are only available there, not in the venv.

Usage:
    /usr/bin/python3 excel_lo.py <command> <file> [options]

Commands:
    info <file>
        Show sheet names and dimensions.

    read <file> --sheet <name> [--range A1:E10]
        Read cells from a sheet. Outputs JSON.

    write <file> --sheet <name> --cell A1 --value "text" [--decimal comma|dot] [--as-text]
        Write a value to a cell.

    add-rows <file> --sheet <name> --after <row> --data <json_file> [--decimal comma|dot] [--as-text]
        Insert rows from a JSON file after the specified row.
        JSON format: [["val1", "val2", ...], ...]

    Text becomes a number only when the number is unambiguous. '2.500' is
    two thousand five hundred in Greek and two and a half in English, so it
    is refused until --decimal says which ('comma' = Greek, 'dot' = English).
    IDs with leading zeros ('0012'), 'nan', '1e5' and anything with a
    currency sign or percent stay text.

    formula <file> --sheet <name> --cell A1 --formula "=SUM(B1:B10)"
        Set a formula in a cell.

    add-sheet <file> --name <sheet_name> [--after <existing_sheet>]
        Add a new sheet.

    save-as <file> --output <output_file> [--format xlsx|pdf|csv]
        Save/export to another format.

    eval-formulas <file>
        Force recalculation of all formulas and save.

The script manages the LibreOffice process automatically (starts/stops as needed).
"""

import argparse
import datetime
import json
import math
import os
import re
import subprocess
import sys
import time


LO_PORT = 2002
LO_TIMEOUT = 10  # seconds to wait for LO to start


def start_libreoffice():
    """Start LibreOffice in headless mode with UNO listener."""
    # Check if already running
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"soffice.*accept.*{LO_PORT}"],
            capture_output=True, text=True
        )
        if result.stdout.strip():
            return  # Already running
    except Exception:
        pass

    # Start fresh
    subprocess.Popen(
        ["soffice", "--headless", "--norestore", "--nologo",
         f"--accept=socket,host=localhost,port={LO_PORT};urp;"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True
    )

    # Wait for it to be ready
    import uno
    for i in range(LO_TIMEOUT):
        time.sleep(1)
        try:
            localContext = uno.getComponentContext()
            resolver = localContext.ServiceManager.createInstanceWithContext(
                "com.sun.star.bridge.UnoUrlResolver", localContext)
            resolver.resolve(
                f"uno:socket,host=localhost,port={LO_PORT};urp;StarOffice.ComponentContext")
            return  # Connected
        except Exception:
            continue
    raise RuntimeError("Failed to start LibreOffice within timeout")


def stop_libreoffice():
    """Stop the LibreOffice process."""
    subprocess.run(["pkill", "-f", "soffice.bin"], capture_output=True)
    time.sleep(1)


def get_desktop():
    """Get the LibreOffice Desktop object via UNO."""
    import uno
    localContext = uno.getComponentContext()
    resolver = localContext.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", localContext)
    ctx = resolver.resolve(
        f"uno:socket,host=localhost,port={LO_PORT};urp;StarOffice.ComponentContext")
    smgr = ctx.ServiceManager
    return smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)


def open_document(desktop, filepath):
    """Open a document and return it."""
    from urllib.parse import quote
    abspath = os.path.abspath(filepath)
    url = "file://" + quote(abspath, safe="/:@")
    doc = desktop.loadComponentFromURL(url, "_blank", 0, ())
    if not doc:
        raise RuntimeError(f"Failed to open: {filepath}")
    return doc


def col_letter_to_index(letter):
    """Convert column letter(s) to 0-based index. A=0, B=1, Z=25, AA=26."""
    result = 0
    for char in letter.upper():
        result = result * 26 + (ord(char) - ord('A') + 1)
    return result - 1


def parse_cell_ref(ref):
    """Parse a cell reference like 'A1' into (col_index, row_index)."""
    match = re.match(r'^([A-Za-z]+)(\d+)$', ref)
    if not match:
        raise ValueError(f"Invalid cell reference: {ref}")
    col = col_letter_to_index(match.group(1))
    row = int(match.group(2)) - 1  # 0-based
    return col, row


def parse_range(range_str):
    """Parse a range like 'A1:E10' into ((col1, row1), (col2, row2))."""
    parts = range_str.split(':')
    if len(parts) != 2:
        raise ValueError(f"Invalid range: {range_str}")
    return parse_cell_ref(parts[0]), parse_cell_ref(parts[1])


class AmbiguousNumber(ValueError):
    """A value that reads as two different numbers depending on the locale."""


# ASCII digits only: Python's \d also matches Arabic-Indic and other digits.
_INT = re.compile(r"[+-]?[0-9]+")
_ONE_MARK = re.compile(r"([+-]?)([0-9]+)([.,])([0-9]+)")
_GROUPED = {
    # thousands mark, decimal mark: 1.234.567,89 and 1,234,567.89
    "comma": re.compile(r"([+-]?)([1-9][0-9]{0,2}(?:\.[0-9]{3})+)(?:,([0-9]+))?"),
    "dot": re.compile(r"([+-]?)([1-9][0-9]{0,2}(?:,[0-9]{3})+)(?:\.([0-9]+))?"),
}
_DECIMAL_HINT = (
    "pass --decimal comma for Greek numbers (2.500 = two thousand five "
    "hundred), --decimal dot for English ones (2.500 = two and a half), or "
    "--as-text to keep them exactly as typed"
)


def _number_from(sign, whole, frac):
    return float(f"{sign}{whole}.{frac}" if frac else f"{sign}{whole}")


def parse_number(text, decimal=None):
    """The number a cell text stands for, or None when it should stay text.

    ``decimal`` is 'comma' (Greek: 1.234,56), 'dot' (English: 1,234.56) or
    None. With None, a value whose meaning depends on the locale ('2.500',
    '1,234') raises AmbiguousNumber instead of guessing: the old guess read
    every Greek thousands amount as a small decimal.
    """
    t = text.strip()
    if not t:
        return None
    if _INT.fullmatch(t):
        digits = t.lstrip("+-")
        if len(digits) > 1 and digits.startswith("0"):
            return None  # an ID or invoice number, not a quantity
        return float(t)

    m = _ONE_MARK.fullmatch(t)
    if m:
        sign, whole, mark, frac = m.groups()
        could_be_thousands = (len(frac) == 3 and len(whole) <= 3
                              and not whole.startswith("0"))
        if decimal is None:
            if could_be_thousands:
                raise AmbiguousNumber(f"'{t}' could be {whole}{frac} or {whole}.{frac}")
            return _number_from(sign, whole, frac)
        decimal_mark = "," if decimal == "comma" else "."
        if mark == decimal_mark:
            return _number_from(sign, whole, frac)
        if could_be_thousands:
            return _number_from(sign, whole + frac, "")
        return None

    for style, rx in _GROUPED.items():
        if decimal is not None and style != decimal:
            continue
        g = rx.fullmatch(t)
        if g:
            sign, grouped, frac = g.groups()
            return _number_from(sign, re.sub(r"[.,]", "", grouped), frac or "")
    return None


def _cell_name(col, row):
    """0-based (col, row) to an A1 name."""
    letters = ""
    col += 1
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return f"{letters}{row + 1}"


def _num(val):
    """Whole-number floats become ints; NaN and inf pass through untouched.

    Guards the bare ``val == int(val)`` comparison, which raises ValueError on
    NaN and OverflowError on inf, so one such cell no longer ends the whole read.
    """
    if math.isfinite(val) and val == int(val):
        return int(val)
    return val


def serial_to_iso(value, null_date, has_date, has_time):
    """A spreadsheet date serial as ISO text, or None to keep the number.

    ``null_date`` is the document's day zero as (year, month, day): LibreOffice
    and Excel both use 1899-12-30 unless a file says otherwise. Rounded to the
    second, because 14:05 is stored as 0.58680555... A time-only cell of a day
    or more is a duration ([HH]:MM), which a clock time would misstate, so it
    stays a number.
    """
    if not (has_date or has_time) or not math.isfinite(value):
        return None
    if has_time and not has_date and not 0 <= value < 1:
        return None
    try:
        moment = (datetime.datetime(*null_date)
                  + datetime.timedelta(seconds=round(value * 86400)))
    except (OverflowError, ValueError):
        return None
    if has_date and not has_time:
        return moment.date().isoformat()
    if has_time and not has_date:
        return moment.time().isoformat()
    return moment.isoformat(sep=" ")


def _date_text(cell, doc):
    """ISO text for a cell formatted as a date, time or both; None otherwise."""
    if doc is None:
        return None
    try:
        from com.sun.star.util.NumberFormat import DATE, TIME
        kind = doc.getNumberFormats().getByKey(cell.NumberFormat).Type
        null = doc.NullDate
    except Exception:
        return None
    return serial_to_iso(cell.getValue(), (null.Year, null.Month, null.Day),
                         bool(kind & DATE), bool(kind & TIME))


def get_cell_value(cell, doc=None):
    """Get the display value of a cell.

    A cell formatted as a date comes back as ISO text ("2026-10-07"), not the
    serial number it is stored as: read on an invoice or expenses sheet, 46302
    is not a date anyone can check (Linux bot sweep 2026-10-07). ``doc``
    supplies the number formats and the document's day zero.
    """
    from com.sun.star.table.CellContentType import EMPTY, VALUE, TEXT, FORMULA
    ctype = cell.getType()
    if ctype == EMPTY:
        return ""
    elif ctype == VALUE:
        dated = _date_text(cell, doc)
        return dated if dated is not None else _num(cell.getValue())
    elif ctype == TEXT:
        return cell.getString()
    elif ctype == FORMULA:
        # A numeric result in a date format reads as ISO text; its display
        # string ("10/08/26") is a locale's guess at month and day order.
        try:
            from com.sun.star.sheet.FormulaResult import VALUE as NUMERIC_RESULT
            numeric = cell.FormulaResultType2 == NUMERIC_RESULT
        except Exception:
            numeric = False
        if numeric:
            dated = _date_text(cell, doc)
            if dated is not None:
                return dated
        # Computed value; return the string form if the result is non-numeric text.
        s = cell.getString()
        if s and not s.replace('.', '').replace(',', '').replace('-', '').isdigit():
            return s
        return _num(cell.getValue())
    return cell.getString()


def cmd_info(args):
    """Show info about an Excel file."""
    start_libreoffice()
    desktop = get_desktop()
    doc = open_document(desktop, args.file)
    try:
        sheets = doc.getSheets()
        result = {"file": args.file, "sheets": []}
        for i in range(sheets.getCount()):
            sheet = sheets.getByIndex(i)
            name = sheet.getName()

            # Find used range
            cursor = sheet.createCursor()
            cursor.gotoStartOfUsedArea(False)
            cursor.gotoEndOfUsedArea(True)
            rows = cursor.getRangeAddress().EndRow + 1
            cols = cursor.getRangeAddress().EndColumn + 1

            result["sheets"].append({
                "name": name,
                "rows": rows,
                "columns": cols
            })
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        doc.close(True)


def cmd_read(args):
    """Read cells from a sheet."""
    start_libreoffice()
    desktop = get_desktop()
    doc = open_document(desktop, args.file)
    try:
        sheets = doc.getSheets()
        sheet = sheets.getByName(args.sheet)

        if args.range:
            (c1, r1), (c2, r2) = parse_range(args.range)
        else:
            cursor = sheet.createCursor()
            cursor.gotoStartOfUsedArea(False)
            cursor.gotoEndOfUsedArea(True)
            addr = cursor.getRangeAddress()
            c1, r1 = addr.StartColumn, addr.StartRow
            c2, r2 = addr.EndColumn, addr.EndRow

        data = []
        for row in range(r1, r2 + 1):
            row_data = []
            for col in range(c1, c2 + 1):
                cell = sheet.getCellByPosition(col, row)
                row_data.append(get_cell_value(cell, doc))
            data.append(row_data)

        print(json.dumps(data, ensure_ascii=False, indent=2))
    finally:
        doc.close(True)


def cmd_write(args):
    """Write a value to a cell."""
    start_libreoffice()
    desktop = get_desktop()
    doc = open_document(desktop, args.file)
    try:
        sheets = doc.getSheets()
        sheet = sheets.getByName(args.sheet)
        col, row = parse_cell_ref(args.cell)
        cell = sheet.getCellByPosition(col, row)

        # Store as text when asked (preserves leading zeros, IDs, invoice
        # numbers); otherwise a number only when the number is unambiguous.
        num = None
        if not getattr(args, "as_text", False):
            try:
                num = parse_number(args.value, getattr(args, "decimal", None))
            except AmbiguousNumber as exc:
                raise AmbiguousNumber(
                    f"{args.cell}: {exc}. Nothing was written; {_DECIMAL_HINT}.") from None
        if num is None:
            cell.setString(args.value)
        else:
            cell.setValue(num)

        doc.store()
        print(json.dumps({"status": "ok", "cell": args.cell, "value": args.value,
                          "stored_as": "text" if num is None else "number"},
                         ensure_ascii=False))
    finally:
        doc.close(True)


def cmd_add_rows(args):
    """Insert rows from a JSON file."""
    start_libreoffice()
    desktop = get_desktop()
    doc = open_document(desktop, args.file)
    try:
        sheets = doc.getSheets()
        sheet = sheets.getByName(args.sheet)

        with open(args.data, 'r', encoding='utf-8') as f:
            rows_data = json.load(f)

        insert_row = args.after  # 1-based row number
        num_rows = len(rows_data)
        as_text = getattr(args, "as_text", False)
        decimal = getattr(args, "decimal", None)

        # Decide every cell before touching the sheet, so an ambiguous value
        # anywhere leaves the file exactly as it was.
        plan, ambiguous, as_text_cells = [], [], []
        for i, row_data in enumerate(rows_data):
            for j, val in enumerate(row_data):
                if val is None or val == "":
                    continue
                name = _cell_name(j, insert_row + i)
                if isinstance(val, bool) or as_text:
                    num = None
                elif isinstance(val, (int, float)):
                    num = val if math.isfinite(val) else None
                else:
                    try:
                        num = parse_number(str(val), decimal)
                    except AmbiguousNumber:
                        ambiguous.append(f"{name} '{val}'")
                        continue
                if num is None:
                    as_text_cells.append(name)
                plan.append((j, i, num, val))
        if ambiguous:
            shown = ", ".join(ambiguous[:10])
            more = f" and {len(ambiguous) - 10} more" if len(ambiguous) > 10 else ""
            raise AmbiguousNumber(
                f"ambiguous numbers at {shown}{more}. Nothing was inserted; {_DECIMAL_HINT}.")

        # Insert empty rows
        sheet.getRows().insertByIndex(insert_row, num_rows)

        # Fill in data
        for j, i, num, val in plan:
            cell = sheet.getCellByPosition(j, insert_row + i)
            if num is None:
                cell.setString(str(val))
            else:
                cell.setValue(num)

        doc.store()
        print(json.dumps({
            "status": "ok",
            "rows_added": num_rows,
            "after_row": insert_row,
            "stored_as_text": as_text_cells,
        }))
    finally:
        doc.close(True)


def cmd_formula(args):
    """Set a formula in a cell."""
    start_libreoffice()
    desktop = get_desktop()
    doc = open_document(desktop, args.file)
    try:
        sheets = doc.getSheets()
        sheet = sheets.getByName(args.sheet)
        col, row = parse_cell_ref(args.cell)
        cell = sheet.getCellByPosition(col, row)
        cell.setFormula(args.formula)
        doc.store()

        # Read back computed value
        computed = get_cell_value(cell, doc)
        print(json.dumps({
            "status": "ok",
            "cell": args.cell,
            "formula": args.formula,
            "computed_value": computed
        }, ensure_ascii=False))
    finally:
        doc.close(True)


def cmd_add_sheet(args):
    """Add a new sheet."""
    start_libreoffice()
    desktop = get_desktop()
    doc = open_document(desktop, args.file)
    try:
        sheets = doc.getSheets()
        if args.after:
            # Find position of the reference sheet
            for i in range(sheets.getCount()):
                if sheets.getByIndex(i).getName() == args.after:
                    sheets.insertNewByName(args.name, i + 1)
                    break
            else:
                sheets.insertNewByName(args.name, sheets.getCount())
        else:
            sheets.insertNewByName(args.name, sheets.getCount())
        doc.store()
        print(json.dumps({"status": "ok", "sheet": args.name}))
    finally:
        doc.close(True)


def cmd_save_as(args):
    """Save/export to another format."""
    start_libreoffice()
    desktop = get_desktop()
    doc = open_document(desktop, args.file)
    try:
        output_path = os.path.abspath(args.output)
        from urllib.parse import quote
        url = "file://" + quote(output_path, safe="/:@")

        fmt = args.format or os.path.splitext(args.output)[1].lstrip('.')

        filter_map = {
            'xlsx': 'Calc MS Excel 2007 XML',
            'xls': 'MS Excel 97',
            'csv': 'Text - txt - csv (StarCalc)',
            'pdf': 'calc_pdf_Export',
            'ods': 'calc8',
        }

        filter_name = filter_map.get(fmt)
        if not filter_name:
            raise ValueError(f"Unsupported format: {fmt}")

        from com.sun.star.beans import PropertyValue
        props = []
        p = PropertyValue()
        p.Name = "FilterName"
        p.Value = filter_name
        props.append(p)

        if fmt == 'pdf':
            doc.storeToURL(url, tuple(props))
        else:
            doc.storeToURL(url, tuple(props))

        print(json.dumps({
            "status": "ok",
            "output": output_path,
            "format": fmt
        }))
    finally:
        doc.close(True)


def cmd_eval_formulas(args):
    """Force recalculation of all formulas and save."""
    start_libreoffice()
    desktop = get_desktop()
    doc = open_document(desktop, args.file)
    try:
        # Force recalculation
        doc.calculateAll()
        doc.store()
        print(json.dumps({"status": "ok", "message": "Formulas recalculated and saved"}))
    finally:
        doc.close(True)


def main():
    parser = argparse.ArgumentParser(description="Excel helper via LibreOffice UNO")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # info
    p = subparsers.add_parser("info")
    p.add_argument("file")

    # read
    p = subparsers.add_parser("read")
    p.add_argument("file")
    p.add_argument("--sheet", required=True)
    p.add_argument("--range", default=None)

    # write
    p = subparsers.add_parser("write")
    p.add_argument("file")
    p.add_argument("--sheet", required=True)
    p.add_argument("--cell", required=True)
    p.add_argument("--value", required=True)
    p.add_argument("--as-text", action="store_true",
                   help="Store as text, preserving leading zeros / IDs / invoice numbers")
    p.add_argument("--decimal", choices=["comma", "dot"], default=None,
                   help="Decimal mark of the value: comma (Greek, 2.500 = 2500) or dot (English)")

    # add-rows
    p = subparsers.add_parser("add-rows")
    p.add_argument("file")
    p.add_argument("--sheet", required=True)
    p.add_argument("--after", type=int, required=True, help="1-based row number")
    p.add_argument("--data", required=True, help="Path to JSON file with row data")
    p.add_argument("--as-text", action="store_true",
                   help="Store every value as text, exactly as given")
    p.add_argument("--decimal", choices=["comma", "dot"], default=None,
                   help="Decimal mark of text values: comma (Greek) or dot (English)")

    # formula
    p = subparsers.add_parser("formula")
    p.add_argument("file")
    p.add_argument("--sheet", required=True)
    p.add_argument("--cell", required=True)
    p.add_argument("--formula", required=True)

    # add-sheet
    p = subparsers.add_parser("add-sheet")
    p.add_argument("file")
    p.add_argument("--name", required=True)
    p.add_argument("--after", default=None)

    # save-as
    p = subparsers.add_parser("save-as")
    p.add_argument("file")
    p.add_argument("--output", required=True)
    p.add_argument("--format", default=None)

    # eval-formulas
    p = subparsers.add_parser("eval-formulas")
    p.add_argument("file")

    # stop (utility to stop LO)
    subparsers.add_parser("stop")

    args = parser.parse_args()

    if args.command == "stop":
        stop_libreoffice()
        print(json.dumps({"status": "ok", "message": "LibreOffice stopped"}))
        return

    try:
        cmd_func = {
            "info": cmd_info,
            "read": cmd_read,
            "write": cmd_write,
            "add-rows": cmd_add_rows,
            "formula": cmd_formula,
            "add-sheet": cmd_add_sheet,
            "save-as": cmd_save_as,
            "eval-formulas": cmd_eval_formulas,
        }[args.command]
        cmd_func(args)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)
    finally:
        stop_libreoffice()


if __name__ == "__main__":
    main()
