# Spreadsheet

Create, read, edit, and export Excel/ODS spreadsheets. Full formula support, charts, PDF export.

## Tools Available

### 1. LibreOffice UNO (primary)

Full Excel compatibility with formula evaluation, charts, and format conversion. Must use system Python (`/usr/bin/python3`) because UNO bindings aren't in the venv.

**Helper script:** `/home/ntouri/claude-telegram-bot/utils/excel_lo.py`

```bash
# File info (sheets, dimensions)
/usr/bin/python3 ~/claude-telegram-bot/utils/excel_lo.py info /path/to/file.xlsx

# Read a sheet (outputs JSON)
/usr/bin/python3 ~/claude-telegram-bot/utils/excel_lo.py read /path/to/file.xlsx --sheet "Sheet1"
/usr/bin/python3 ~/claude-telegram-bot/utils/excel_lo.py read /path/to/file.xlsx --sheet "Sheet1" --range A1:E10

# Write a cell value
/usr/bin/python3 ~/claude-telegram-bot/utils/excel_lo.py write /path/to/file.xlsx --sheet "Sheet1" --cell A1 --value "text"

# Insert rows from JSON
/usr/bin/python3 ~/claude-telegram-bot/utils/excel_lo.py add-rows /path/to/file.xlsx --sheet "Sheet1" --after 5 --data /tmp/rows.json

# Set a formula
/usr/bin/python3 ~/claude-telegram-bot/utils/excel_lo.py formula /path/to/file.xlsx --sheet "Sheet1" --cell C1 --formula "=SUM(A1:B1)"

# Add a new sheet
/usr/bin/python3 ~/claude-telegram-bot/utils/excel_lo.py add-sheet /path/to/file.xlsx --name "NewSheet"

# Export to another format (xlsx, xls, csv, pdf, ods)
/usr/bin/python3 ~/claude-telegram-bot/utils/excel_lo.py save-as /path/to/file.xlsx --output /tmp/output.pdf --format pdf

# Recalculate all formulas
/usr/bin/python3 ~/claude-telegram-bot/utils/excel_lo.py eval-formulas /path/to/file.xlsx

# Stop LibreOffice process
/usr/bin/python3 ~/claude-telegram-bot/utils/excel_lo.py stop
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

- LibreOffice UNO requires `/usr/bin/python3` (system Python), NOT the venv Python
- openpyxl, xlsxwriter, pandas, seaborn are all in the venv (`~/.venvs/main`)
- LibreOffice starts/stops automatically per command -- no manual management needed
- Supported formats: xlsx, xls, ods, csv, pdf
