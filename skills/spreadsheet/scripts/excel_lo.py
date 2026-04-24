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

    write <file> --sheet <name> --cell A1 --value "text"
        Write a value to a cell.

    add-rows <file> --sheet <name> --after <row> --data <json_file>
        Insert rows from a JSON file after the specified row.
        JSON format: [["val1", "val2", ...], ...]

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
import json
import os
import subprocess
import sys
import time
import re


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


def get_cell_value(cell):
    """Get the display value of a cell."""
    from com.sun.star.table.CellContentType import EMPTY, VALUE, TEXT, FORMULA
    ctype = cell.getType()
    if ctype == EMPTY:
        return ""
    elif ctype == VALUE:
        val = cell.getValue()
        # Return int if it's a whole number
        if val == int(val):
            return int(val)
        return val
    elif ctype == TEXT:
        return cell.getString()
    elif ctype == FORMULA:
        # Return the computed value
        val = cell.getValue()
        s = cell.getString()
        if s and not s.replace('.', '').replace(',', '').replace('-', '').isdigit():
            return s
        if val == int(val):
            return int(val)
        return val
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
                row_data.append(get_cell_value(cell))
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

        # Try to set as number, fall back to string
        try:
            val = float(args.value.replace(',', '.'))
            cell.setValue(val)
        except ValueError:
            cell.setString(args.value)

        doc.store()
        print(json.dumps({"status": "ok", "cell": args.cell, "value": args.value}))
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

        with open(args.data, 'r') as f:
            rows_data = json.load(f)

        insert_row = args.after  # 1-based row number
        num_rows = len(rows_data)

        # Insert empty rows
        sheet.getRows().insertByIndex(insert_row, num_rows)

        # Fill in data
        for i, row_data in enumerate(rows_data):
            for j, val in enumerate(row_data):
                cell = sheet.getCellByPosition(j, insert_row + i)
                if val is None or val == "":
                    continue
                try:
                    num_val = float(str(val).replace(',', '.'))
                    cell.setValue(num_val)
                except (ValueError, TypeError):
                    cell.setString(str(val))

        doc.store()
        print(json.dumps({
            "status": "ok",
            "rows_added": num_rows,
            "after_row": insert_row
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
        computed = get_cell_value(cell)
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

    # add-rows
    p = subparsers.add_parser("add-rows")
    p.add_argument("file")
    p.add_argument("--sheet", required=True)
    p.add_argument("--after", type=int, required=True, help="1-based row number")
    p.add_argument("--data", required=True, help="Path to JSON file with row data")

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
