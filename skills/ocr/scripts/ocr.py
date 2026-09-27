#!/usr/bin/env python3
"""
OCR using Tesseract - extract text from images and PDFs.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# tesseract's Greek model writes the micro sign (U+00B5) for mu, so
# "αριθμός" came out "αριθµός" and a search for the real word missed it
MICRO_IN_GREEK = re.compile(r"(?<=[\u0370-\u03ff\u1f00-\u1fff])\u00b5|\u00b5(?=[\u0370-\u03ff\u1f00-\u1fff])")


def greek_mu(text):
    return MICRO_IN_GREEK.sub("\u03bc", text)


def installed_langs():
    r = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True)
    return {line.strip() for line in r.stdout.splitlines()[1:] if line.strip()}


def default_lang():
    """English plus Greek when the Greek pack is there: Greek read with eng
    alone comes out as Latin garbage ("TiWOASyLO apLOudc")."""
    return "eng+ell" if "ell" in installed_langs() else "eng"


def upright(image_path, tmpdir):
    """The image as it is meant to be seen: tesseract ignores the EXIF
    orientation tag, so a phone photo taken sideways was read sideways and
    came back as garbage (Linux bot review 2026-09-27)."""
    try:
        from PIL import Image, ImageOps
        with Image.open(image_path) as im:
            if int(im.getexif().get(0x0112, 1) or 1) == 1:
                return image_path
            fixed = ImageOps.exif_transpose(im)
            out = os.path.join(tmpdir, "upright.png")
            fixed.save(out)
            return out
    except Exception:
        return image_path


def ocr_image(image_path, lang="eng"):
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            ["tesseract", upright(image_path, tmp), "stdout", "-l", lang],
            capture_output=True, text=True
        )
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "text": ""}
    return {"text": greek_mu(result.stdout.strip()), "error": None}


def ocr_image_with_data(image_path, lang="eng"):
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            ["tesseract", upright(image_path, tmp), "stdout", "-l", lang, "tsv"],
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
            # tesseract writes conf as a decimal ("96.500000"); -1 marks a
            # non-word row. isdigit() read every decimal as 0 (audit F26).
            try:
                conf = float(parts[10])
            except ValueError:
                conf = 0.0
            words.append({
                "text": greek_mu(parts[11]),
                "confidence": round(conf, 1) if conf >= 0 else 0.0,
            })
            text_parts.append(greek_mu(parts[11]))

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

        # A page error used to be dropped, so a bad --lang gave an empty
        # result with exit 0
        all_text = []
        errors = []
        page_files = sorted(Path(tmpdir).glob("page-*.png"))
        for i, page_file in enumerate(page_files):
            result = ocr_image(str(page_file), lang)
            if result["error"]:
                errors.append(f"page {i+1}: {result['error']}")
            elif result["text"]:
                all_text.append(f"--- Page {i+1} ---\n{result['text']}")

        if page_files and len(errors) == len(page_files):
            return {"error": errors[0], "text": ""}
        for err in errors:
            print(f"warning: {err}", file=sys.stderr)
        return {"text": "\n\n".join(all_text), "pages": len(page_files), "error": None}


def main():
    parser = argparse.ArgumentParser(description="OCR - extract text from images")
    parser.add_argument("input", help="Input image or PDF file")
    parser.add_argument("--output", "-o", help="Output text file")
    parser.add_argument("--lang", "-l", help="Language(s), e.g. eng, ell, eng+ell (default: eng+ell when the Greek pack is installed)")
    parser.add_argument("--json", action="store_true", help="Output as JSON with confidence")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    args.lang = args.lang or default_lang()
    missing = [code for code in args.lang.split("+") if code not in installed_langs()]
    if missing:
        print(f"Error: no tesseract language pack for {', '.join(missing)} "
              f"(installed: {', '.join(sorted(installed_langs()))}; Greek is 'ell', not 'gre')", file=sys.stderr)
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
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Saved to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
