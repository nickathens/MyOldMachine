# PDF Processing

Manipulate PDFs: merge, split, extract text, OCR.

## Extract Text (pypdf)

```python
from pypdf import PdfReader

reader = PdfReader("/path/to/file.pdf")
text = ""
for page in reader.pages:
    text += page.extract_text()
print(text)
```

Or as a one-liner:
```bash
python3 -c "from pypdf import PdfReader; r=PdfReader('/path/to/file.pdf'); print(''.join(p.extract_text() for p in r.pages))"
```

## Merge PDFs

```python
from pypdf import PdfWriter

writer = PdfWriter()
writer.append("/path/to/first.pdf")
writer.append("/path/to/second.pdf")
writer.write("/path/to/merged.pdf")
```

## Split PDF

```python
from pypdf import PdfReader, PdfWriter

reader = PdfReader("/path/to/file.pdf")
for i, page in enumerate(reader.pages):
    writer = PdfWriter()
    writer.add_page(page)
    writer.write(f"/tmp/page_{i+1}.pdf")
```

## OCR a PDF (make searchable)

```bash
# Basic OCR
ocrmypdf input.pdf output.pdf

# Force OCR even if text exists
ocrmypdf --force-ocr input.pdf output.pdf

# Specific language
ocrmypdf -l eng input.pdf output.pdf

# Skip text pages (only OCR image pages)
ocrmypdf --skip-text input.pdf output.pdf
```

## Get PDF Info

```python
from pypdf import PdfReader
reader = PdfReader("/path/to/file.pdf")
print(f"Pages: {len(reader.pages)}")
print(f"Metadata: {reader.metadata}")
```
