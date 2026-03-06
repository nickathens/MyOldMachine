#!/usr/bin/env python3
"""
OCR using Tesseract - extract text from images and PDFs.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def ocr_image(image_path, lang="eng"):
    result = subprocess.run(
        ["tesseract", image_path, "stdout", "-l", lang],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "text": ""}
    return {"text": result.stdout.strip(), "error": None}


def ocr_image_with_data(image_path, lang="eng"):
    result = subprocess.run(
        ["tesseract", image_path, "stdout", "-l", lang, "tsv"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "words": [], "text": ""}

    lines = result.stdout.strip().split('\n')
    if len(lines) < 2:
        return {"words": [], "text": "", "error": None}

    words = []
    text_parts = []
    for line in lines[1:]:
        parts = line.split('\t')
        if len(parts) >= 12 and parts[11].strip():
            words.append({
                "text": parts[11],
                "confidence": int(parts[10]) if parts[10].isdigit() else 0,
            })
            text_parts.append(parts[11])

    avg_conf = sum(w["confidence"] for w in words) / len(words) if words else 0
    return {
        "text": " ".join(text_parts),
        "word_count": len(words),
        "average_confidence": round(avg_conf, 1),
        "error": None
    }


def ocr_pdf(pdf_path, lang="eng"):
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            ["pdftoppm", "-png", pdf_path, f"{tmpdir}/page"],
            capture_output=True
        )
        if result.returncode != 0:
            return {"error": "Failed to convert PDF to images", "text": ""}

        all_text = []
        page_files = sorted(Path(tmpdir).glob("page-*.png"))
        for i, page_file in enumerate(page_files):
            result = ocr_image(str(page_file), lang)
            if result["text"]:
                all_text.append(f"--- Page {i+1} ---\n{result['text']}")

        return {"text": "\n\n".join(all_text), "pages": len(page_files), "error": None}


def main():
    parser = argparse.ArgumentParser(description="OCR - extract text from images")
    parser.add_argument("input", help="Input image or PDF file")
    parser.add_argument("--output", "-o", help="Output text file")
    parser.add_argument("--lang", "-l", default="eng")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    if input_path.suffix.lower() == ".pdf":
        result = ocr_pdf(args.input, args.lang)
    elif args.json:
        result = ocr_image_with_data(args.input, args.lang)
    else:
        result = ocr_image(args.input, args.lang)

    if result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    output = json.dumps(result, indent=2) if args.json else result["text"]

    if args.output:
        Path(args.output).write_text(output)
        print(f"Saved to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
