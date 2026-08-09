# Document Conversion

Convert documents to Markdown. Two backends, routed per format by
`scripts/convert.py`, because measurement said neither wins everywhere.

## Use this

```bash
python3 skills/docs/scripts/convert.py FILE
python3 skills/docs/scripts/convert.py FILE -o out.md
python3 skills/docs/scripts/convert.py FILE --backend markitdown
```

Markdown goes to stdout, so it pipes cleanly. The backend that produced it is
announced on stderr. Exit code 1 with a reason on failure.

## Which backend, and why

| formats | backend | why |
|---|---|---|
| .doc .docx .odt .rtf .epub .pptx .odp .xlsx .ods .csv | anydoc | faster, and no filler tokens |
| .pdf | markitdown | anydoc mangles pdf text |
| .html .htm images audio .zip | markitdown | anydoc does not read them |

Measured over a real 195 document corpus on Linux: 107 pdf, 72 xlsx, 9 csv,
6 docx, 1 legacy doc.

* **Spreadsheets.** anydoc took 521 ms for all 72 workbooks, markitdown took
  14656 ms. markitdown routes spreadsheets through pandas, so empty cells
  become `NaN`, unlabelled columns become `Unnamed: 3`, and integers become
  floats. That was 52334 filler tokens across the corpus, none of which anydoc
  emits. Sheet headings are identical on multi sheet workbooks. anydoc omits
  the heading on a single sheet workbook, which is the one thing it loses.
* **Legacy .doc.** markitdown cannot read binary Word at all. anydoc can.
* **Word documents.** anydoc 32 ms against 982 ms, and markitdown inlines
  base64 image data into the text. anydoc split one word across a field
  boundary in one of six files, so it is not free either.
* **PDF.** anydoc failed to parse 15 of 107 files that markitdown read fine,
  and drops the fi ligature on most of the rest, so "Please find below" comes
  out "Please nd below". It also orphans currency symbols into a junk line at
  the end. anydoc does reconstruct pdf tables better, so `--backend anydoc` is
  worth trying by hand on a table heavy pdf, with the text checked afterwards.

Do not trust that table on a different machine or a different document set.
Re-measure:

```bash
python3 skills/docs/scripts/compare_backends.py YOUR_FILE ...
```

## Fallback

Anything anydoc declines falls back to markitdown automatically and says so on
stderr. One workbook in that corpus is malformed enough that anydoc refuses it
and markitdown reads it. A forced `--backend anydoc` does not fall back, so it
stays a real measurement of anydoc.

A pdf arriving under an office extension is detected by content and sent to
markitdown anyway.

An install where anydoc never landed degrades to markitdown for everything,
which is what this skill did before, rather than raising.

## Python usage

```python
from pathlib import Path
import sys

sys.path.insert(0, "skills/docs/scripts")
from convert import convert

text, backend_used = convert(Path("ledger.xlsx"))
```

## Notes

* Scanned PDFs still need OCR first. Neither backend does OCR. Use the `ocr`
  skill or `ocrmypdf`, then convert.
* anydoc is `firecrawl-anydoc` on PyPI, MIT, Rust with a Python binding and no
  Python dependencies of its own. Published wheels cover linux and macOS on
  both x86_64 and arm64, plus Windows x86_64, on Python 3.10 and up. There is
  no anydoc command line tool, which is why this skill has a script rather
  than a bare command.
* markitdown stays installed. It is not a legacy path, it owns pdf, html,
  images, audio and archives.
