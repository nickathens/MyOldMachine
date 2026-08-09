# Document Conversion

Convert documents to Markdown with markitdown, except for four formats where
markitdown measurably loses data and `scripts/convert.py` routes to anydoc
instead.

## Use this

```bash
python3 skills/docs/scripts/convert.py FILE
python3 skills/docs/scripts/convert.py FILE -o out.md
python3 skills/docs/scripts/convert.py FILE --backend markitdown
```

Markdown goes to stdout, so it pipes cleanly. The backend that produced it is
announced on stderr. Exit code 1 with a reason on failure.

## Which backend, and why

| format | backend | what markitdown loses |
|---|---|---|
| .ods | anydoc | cannot open it at all |
| .doc | anydoc | cannot open legacy binary Word at all |
| .csv | anydoc | drops columns when it misreads the header |
| .xlsx | anydoc | fills empty cells with NaN, columns with "Unnamed: N" |
| everything else | markitdown | nothing |

Four formats, each with a measured reason. Measured over 195 real documents on
Linux: 107 pdf, 72 xlsx, 9 csv, 6 docx, 1 legacy doc.

* **.ods.** `UnsupportedFormatException`, no converter even attempts it.
  anydoc reads the same workbook in full.
* **.doc.** markitdown cannot read binary Word at all.
* **.csv.** markitdown reads csv through pandas, which infers a header row.
  Given a real 11 column file whose first line is a comment, it inferred one
  column and dropped **3298 of 3824 cells**. anydoc dropped none. On a clean
  csv the two agree to within one character, so this is a tail risk rather
  than a constant cost, and the tail is silent data loss.
* **.xlsx.** Same pandas path, so empty cells become `NaN`, unlabelled columns
  become `Unnamed: 3`, integers become floats. 183 to 756 filler tokens per
  accounting workbook, none of which anydoc emits. No cell content is lost by
  either side. anydoc omits the sheet heading on a single sheet workbook,
  which is the one thing it loses.

## What is deliberately NOT routed

Three of these were routed in the first version of this skill and should not
have been. Kept written down so the guess does not come back:

* **.pdf.** anydoc failed to parse 15 of 107 files markitdown read fine, and
  drops the fi ligature on most of the rest, so "Please find below" comes out
  "Please nd below". It does reconstruct pdf tables better, so `--backend
  anydoc` is worth trying by hand on a table heavy pdf, with the text checked
  afterwards.
* **.docx.** anydoc corrupts text at field boundaries: in a real invoice
  template "contact" came out as "ntact". markitdown's only cost is a short
  `![](data:image/png;base64...)` placeholder per image, truncated, not the
  payload. Corruption beats cosmetics. anydoc is 30x faster on docx and that
  is not worth a wrong word in an invoice.
* **.odt .rtf .epub .pptx .odp.** Never measured. There was not one such file
  in the corpus. They were routed because anydoc's own format table lists
  them, which is exactly the habit that put pdf on the wrong backend.

Do not trust the table above on a different machine or a different document
set, and measure any format before adding it:

```bash
python3 skills/docs/scripts/compare_backends.py YOUR_FILE ...
```

## Fallback

Anything anydoc declines falls back to markitdown automatically and says so on
stderr. One workbook in that corpus is malformed enough that anydoc refuses it
and markitdown reads it. A forced `--backend anydoc` does not fall back, so it
stays a real measurement of anydoc.

A pdf arriving under a routed extension is detected by content and sent to
markitdown anyway.

An install where anydoc never landed degrades to markitdown for everything,
which is what this skill did before, rather than raising. So does an anydoc
that is installed but does not load: it is a compiled Rust extension, so a
broken shared object is the realistic failure, and the check performs the real
import rather than asking whether the module is findable, because those are
different questions.

## Python usage

```python
import sys
sys.path.insert(0, "skills/docs/scripts")
from convert import convert

text, backend_used = convert(Path("ledger.xlsx"))
```

## Notes

* Scanned PDFs still need OCR first. Neither backend does OCR. Use the `ocr`
  skill or `ocrmypdf`, then convert.
* anydoc is `firecrawl-anydoc` on PyPI, MIT, Rust with a Python binding and no
  Python dependencies of its own. There is no anydoc command line tool, which
  is why this skill has a script rather than a bare command.
* markitdown owns everything else: pdf, docx, html, images, audio, archives.
  It is the default, not the legacy path.
