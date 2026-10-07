# Spreadsheet

Create, read, edit, and export Excel/ODS spreadsheets. Full formula support, charts, PDF export.

## Tools Available

### 1. LibreOffice UNO (primary)

Full Excel compatibility with formula evaluation, charts, and format conversion. Run it with `/usr/bin/python3`, not the bot's venv. On Linux that is the Python `python3-uno` serves, and the command talks to LibreOffice over a pipe. On macOS no Python outside LibreOffice can import `uno` (the one inside LibreOffice.app is signed so that only LibreOffice may start it), so the command runs inside LibreOffice as a macro; any Python 3 starts it.

Every command starts a private LibreOffice of its own, on a fresh profile in a new temporary folder, and closes that copy, and only that copy, when it ends. It never closes a LibreOffice it did not start: not a window someone has open, not another user's command.

**Helper script:** `skills/spreadsheet/scripts/excel_lo.py`

Run from the MyOldMachine repo root:

```bash
# File info (sheets, dimensions)
/usr/bin/python3 skills/spreadsheet/scripts/excel_lo.py info /path/to/file.xlsx

# Read a sheet (outputs JSON; date cells come back as ISO text, 2026-10-07 or 2026-10-07 14:05:00)
/usr/bin/python3 skills/spreadsheet/scripts/excel_lo.py read /path/to/file.xlsx --sheet "Sheet1"
/usr/bin/python3 skills/spreadsheet/scripts/excel_lo.py read /path/to/file.xlsx --sheet "Sheet1" --range A1:E10

# Write a cell value
/usr/bin/python3 skills/spreadsheet/scripts/excel_lo.py write /path/to/file.xlsx --sheet "Sheet1" --cell A1 --value "text"
/usr/bin/python3 skills/spreadsheet/scripts/excel_lo.py write /path/to/file.xlsx --sheet "Sheet1" --cell B4 --value "2.500" --decimal comma

# Insert rows from JSON
/usr/bin/python3 skills/spreadsheet/scripts/excel_lo.py add-rows /path/to/file.xlsx --sheet "Sheet1" --after 5 --data /tmp/rows.json --decimal comma

# Set a formula
/usr/bin/python3 skills/spreadsheet/scripts/excel_lo.py formula /path/to/file.xlsx --sheet "Sheet1" --cell C1 --formula "=SUM(A1:B1)"

# Add a new sheet
/usr/bin/python3 skills/spreadsheet/scripts/excel_lo.py add-sheet /path/to/file.xlsx --name "NewSheet"

# Export to another format (xlsx, xls, csv, pdf, ods)
/usr/bin/python3 skills/spreadsheet/scripts/excel_lo.py save-as /path/to/file.xlsx --output /tmp/output.pdf --format pdf

# CSV holds one sheet: the active one, or the one --sheet names (the reply says which, and how many there were)
/usr/bin/python3 skills/spreadsheet/scripts/excel_lo.py save-as /path/to/file.xlsx --output /tmp/totals.csv --sheet "Totals"

# Recalculate all formulas
/usr/bin/python3 skills/spreadsheet/scripts/excel_lo.py eval-formulas /path/to/file.xlsx

# Close a LibreOffice this tool left running for you (only after a command was killed)
/usr/bin/python3 skills/spreadsheet/scripts/excel_lo.py stop
```

### 2. openpyxl (Python library)

For programmatic spreadsheet creation when you need fine control over formatting, conditional formatting, or building from scratch. Available in the venv.

```python
import openpyxl

# Create new workbook
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Data"
ws['A1'] = "Name"
ws['B1'] = "Value"
wb.save("/tmp/output.xlsx")

# Read existing
wb = openpyxl.load_workbook("/path/to/file.xlsx")
ws = wb["Sheet1"]
for row in ws.iter_rows(values_only=True):
    print(row)
```

### 3. xlsxwriter (Python library)

Better for creating new spreadsheets with charts. Available in the venv.

```python
import xlsxwriter

wb = xlsxwriter.Workbook("/tmp/chart.xlsx")
ws = wb.add_worksheet()

# Write data
data = [10, 20, 30, 40, 50]
for i, val in enumerate(data):
    ws.write(i, 0, val)

# Create chart
chart = wb.add_chart({'type': 'bar'})
chart.add_series({'values': '=Sheet1!$A$1:$A$5'})
ws.insert_chart('C1', chart)

wb.close()
```

### 4. pandas (read/transform/export)

For data manipulation, analysis, and quick CSV/Excel conversions. Available in the venv.

```python
import pandas as pd

# Read Excel
df = pd.read_excel("/path/to/file.xlsx", sheet_name="Sheet1")

# Transform
summary = df.groupby("category").sum()

# Write back
df.to_excel("/tmp/output.xlsx", index=False)
```

### 5. seaborn / matplotlib (visualization)

For data visualization and chart generation. Available in the venv.

```python
import seaborn as sns
import matplotlib.pyplot as plt
import pandas as pd

df = pd.read_excel("/path/to/file.xlsx")
sns.barplot(data=df, x="category", y="value")
plt.savefig("/tmp/chart.png", dpi=150, bbox_inches='tight')
plt.close()
```

## When to Use What

| Task | Tool |
|------|------|
| Read/edit existing Excel with formulas | LibreOffice UNO |
| Export to PDF | LibreOffice UNO |
| Create new spreadsheet from scratch | openpyxl or xlsxwriter |
| Spreadsheet with charts | xlsxwriter or LibreOffice |
| Data analysis/transformation | pandas |
| Data visualization | seaborn/matplotlib |
| Evaluate/recalculate formulas | LibreOffice UNO |

## Important Notes

- **Numbers in text form must be unambiguous.** `write` and `add-rows` turn a text value into a number only when it can mean one thing. `2.500` is two thousand five hundred in Greek and two and a half in English, so it is refused (nothing is written) until you pass `--decimal comma` (Greek amounts) or `--decimal dot`. IDs with leading zeros (`0012`), `nan`, `1e5`, `12%` and `€1.234,56` stay text. The JSON reply lists every cell stored as text. JSON numbers (`2500`, `2.5`) are always stored as numbers; `--as-text` keeps everything exactly as typed.
- LibreOffice UNO runs under `/usr/bin/python3` (system Python), NOT the venv Python
- openpyxl, xlsxwriter, pandas, seaborn are all in the bot's venv (installed by `deps.json`)
- LibreOffice starts and stops by itself with every command: no manual management. Never close LibreOffice by name (`pkill soffice`, `killall soffice`): one OS account hosts every user here, so that closes other people's windows and commands. `stop` closes only a copy this tool left behind for you, and only once the command that started it is gone.
- Commands that only read (`info`, `read`, `save-as`) open the file read-only, so they work while someone has it open and leave no lock behind. Commands that write refuse a file someone else has open ("is open in another LibreOffice (...)"), rather than race their save: ask them to close it. A lock left by a killed copy of this tool is recognised and cleared by itself.
- Supported formats: xlsx, xls, ods, csv, pdf
