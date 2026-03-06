# OCR (Optical Character Recognition)

Extract text from images, screenshots, and scanned PDFs using Tesseract.

## Usage

```bash
# Extract text from image
python $SKILL_DIR/scripts/ocr.py image.png

# Output to file
python $SKILL_DIR/scripts/ocr.py image.png --output text.txt

# Specify language (default: eng)
python $SKILL_DIR/scripts/ocr.py image.png --lang gre
python $SKILL_DIR/scripts/ocr.py image.png --lang eng+gre

# Get structured output (JSON with confidence scores)
python $SKILL_DIR/scripts/ocr.py image.png --json

# Process PDF (extracts text from each page image)
python $SKILL_DIR/scripts/ocr.py document.pdf
```

## Supported Languages

eng (English), gre (Greek), deu (German), fra (French), spa (Spanish), ita (Italian), and many more.

Install additional languages: `sudo apt install tesseract-ocr-<lang>` or `brew install tesseract-lang`

## Notes

- Works best with clear, high-contrast text
- Handwriting recognition is limited
- For scanned PDFs, converts each page to image first
