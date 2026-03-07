# Document Conversion

Convert documents to Markdown using markitdown.

## Convert Files

```bash
# Convert a file to markdown
markitdown document.docx > output.md
markitdown document.pdf > output.md
markitdown document.pptx > output.md
markitdown document.xlsx > output.md

# Read the output directly
markitdown document.pdf
```

## Supported Formats

- **Office**: .docx, .xlsx, .pptx
- **PDF**: .pdf (text extraction)
- **HTML**: .html, .htm
- **Images**: .jpg, .png (with EXIF/metadata)
- **Audio**: .mp3, .wav (metadata)
- **Archives**: .zip (lists contents)

## Python Usage

```python
from markitdown import MarkItDown

md = MarkItDown()
result = md.convert("/path/to/document.docx")
print(result.text_content)
```

## Examples

"Convert this Word doc to markdown"
"Read this PDF"
"Extract text from this document"

## Notes

- PDFs with images only need OCR first (use ocrmypdf)
- Large files may take a moment to process
- Output is clean Markdown suitable for reading or further processing
