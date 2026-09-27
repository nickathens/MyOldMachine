#!/usr/bin/env python3
"""
Translation - Translate text between languages using Google Translate (free).

Usage:
    python translate.py "Hello world"                    # Auto-detect to English
    python translate.py "Hello world" --to el            # English to Greek
    python translate.py "Γεια σου κόσμε" --from el --to en  # Greek to English
    python translate.py --languages                       # List language codes

Language codes: en (English), el (Greek), es (Spanish), fr (French), de (German), etc.
"""

import argparse
import re
import sys
import time
from pathlib import Path

from deep_translator import GoogleTranslator


# Common language codes
COMMON_LANGUAGES = {
    "en": "English",
    "el": "Greek",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "zh-CN": "Chinese (Simplified)",
    "ja": "Japanese",
    "ko": "Korean",
    "ar": "Arabic",
    "tr": "Turkish",
    "nl": "Dutch",
    "pl": "Polish",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "cs": "Czech",
    "hu": "Hungarian",
    "ro": "Romanian",
    "bg": "Bulgarian",
    "uk": "Ukrainian",
    "he": "Hebrew",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "ms": "Malay",
    "hi": "Hindi",
}


GOOGLE_LIMIT = 4500      # deep-translator refuses 5000 and up
MYMEMORY_LIMIT = 450     # and 500 and up for MyMemory

# Scripts that name the language well enough for the MyMemory fallback, which
# has no auto-detection
SCRIPTS = [("el", "\u0370", "\u03ff"), ("el", "\u1f00", "\u1fff"), ("ru", "\u0400", "\u04ff"),
           ("ar", "\u0600", "\u06ff"), ("he", "\u0590", "\u05ff"), ("ko", "\uac00", "\ud7af"),
           ("ja", "\u3040", "\u30ff"), ("zh-CN", "\u4e00", "\u9fff")]


def guess_by_script(text: str):
    letters = [ch for ch in text if ch.isalpha()]
    for code, lo, hi in SCRIPTS:
        if letters and sum(lo <= ch <= hi for ch in letters) / len(letters) > 0.5:
            return code
    return None


def pieces(text: str, limit: int):
    """Split text into pieces under limit characters, at line, then sentence, then word breaks.

    Google refuses 5000 characters and more, and the error printed the whole
    text back (Linux bot review 2026-09-27). Returns [(piece, joiner_after)].
    """
    out = []
    for line in text.split("\n"):
        if len(line) < limit:
            out.append((line, "\n"))
            continue
        chunk = ""
        for sentence in re.split(r"(?<=[.!?;\u037e\u00b7])\s+", line):
            while len(sentence) >= limit:            # a sentence longer than the limit: by words
                cut = sentence.rfind(" ", 0, limit - 1)
                hard = cut <= 0                       # one "word" longer than the limit (a URL)
                cut = limit - 1 if hard else cut
                if chunk:
                    out.append((chunk, " "))
                    chunk = ""
                # a hard cut rejoins with nothing: a space there split the word (audit pass 1)
                out.append((sentence[:cut], "" if hard else " "))
                sentence = sentence[cut:].lstrip()
            if chunk and len(chunk) + 1 + len(sentence) >= limit:
                out.append((chunk, " "))
                chunk = sentence
            else:
                chunk = f"{chunk} {sentence}".strip()
        out.append((chunk, "\n"))
    # pack short lines together so a letter is not one request per line
    packed, current, joiner = [], "", "\n"
    for piece, after in out:
        if current and len(current) + 1 + len(piece) < limit and joiner == "\n":
            current += "\n" + piece
        else:
            if current or packed:
                packed.append((current, joiner))
            current = piece
        joiner = after
    packed.append((current, ""))
    return packed


def _run(translate_one, text: str, limit: int) -> str:
    result = []
    for piece, joiner in pieces(text, limit):
        result.append((translate_one(piece) if piece.strip() else piece) + joiner)
        time.sleep(0.25)                          # Google's limit is 5 requests a second
    return "".join(result).rstrip("\n")


def _mymemory_code(code: str) -> str:
    from deep_translator import MyMemoryTranslator
    codes = MyMemoryTranslator(source="en-GB", target="el-GR").get_supported_languages(as_dict=True).values()
    if code in codes:
        return code
    return next((c for c in codes if c.lower().startswith(code.lower() + "-")), code)


def translate_text(text: str, source: str = "auto", target: str = "en") -> dict:
    """Translate text: Google, or MyMemory when Google refuses this machine."""
    from deep_translator import MyMemoryTranslator
    from deep_translator.exceptions import RequestError, TooManyRequests

    try:
        google = GoogleTranslator(source=source, target=target)
        translated = _run(google.translate, text, GOOGLE_LIMIT)
        return {"original": text, "translated": translated, "source_lang": source,
                "target_lang": target, "engine": "Google Translate"}
    except (TooManyRequests, RequestError) as exc:
        google_error = str(exc)
    except Exception as exc:
        return {"error": f"Google Translate: {type(exc).__name__}: {str(exc)[:300]}"}

    # Google answers a machine with its "unusual traffic" page when it
    # decides the address sends too much (seen on the Linux bot, 2026-09-27); MyMemory still
    # works but needs the source language
    guessed = source if source != "auto" else guess_by_script(text)
    if not guessed:
        return {"error": "Google Translate is refusing this machine (" + google_error[:120] + "), and the "
                         "MyMemory fallback needs the source language: pass --from, e.g. --from fr"}
    try:
        memory = MyMemoryTranslator(source=_mymemory_code(guessed), target=_mymemory_code(target))
        translated = _run(memory.translate, text, MYMEMORY_LIMIT)
        # past its free daily quota MyMemory returns the warning AS the translation
        if "MYMEMORY WARNING" in translated.upper():
            raise RuntimeError(translated.strip()[:200])
    except Exception as exc:
        return {"error": f"Google refused this machine and MyMemory failed too: {type(exc).__name__}: {str(exc)[:200]}"}
    return {"original": text, "translated": translated, "target_lang": target,
            "source_lang": guessed if source != "auto" else f"{guessed} (by script)",
            "engine": "MyMemory (Google Translate refused this machine)"}


def list_languages():
    """List supported language codes."""
    print("Common Language Codes:")
    print("")
    for code, name in sorted(COMMON_LANGUAGES.items(), key=lambda x: x[1]):
        print(f"  {code:8} {name}")
    print("")
    print("For full list, see: https://cloud.google.com/translate/docs/languages")


def main():
    parser = argparse.ArgumentParser(description="Translate text")
    parser.add_argument("text", nargs="?", help="Text to translate ('-' reads stdin)")
    parser.add_argument("--file", help="Translate the contents of this text file")
    parser.add_argument("--from", dest="source", default="auto", help="Source language code (default: auto-detect)")
    parser.add_argument("--to", dest="target", default="en", help="Target language code (default: en)")
    parser.add_argument("--languages", action="store_true", help="List language codes")
    args = parser.parse_args()

    if args.languages:
        list_languages()
        return 0

    text = Path(args.file).read_text(encoding="utf-8") if args.file else \
        (sys.stdin.read() if args.text == "-" else args.text)
    if not text or not text.strip():
        parser.print_help()
        return 1

    result = translate_text(text, args.source, args.target)

    if "error" in result:
        print(f"Error: {result['error']}")
        return 1

    source = result["source_lang"]
    source_name = "auto-detected by the engine" if source == "auto" else COMMON_LANGUAGES.get(source, source)
    target_name = COMMON_LANGUAGES.get(result['target_lang'], result['target_lang'])

    print(f"From: {source_name}")
    print(f"To: {target_name}")
    print(f"Engine: {result['engine']}")
    print("")
    if len(text) <= 500:
        print(f"Original: {result['original']}")
    print(f"Translated: {result['translated']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
