#!/usr/bin/env python3
"""
Translation - Translate text between languages using Google Translate (free).

Usage:
    python translate.py "Hello world"                    # Auto-detect to English
    python translate.py "Hello world" --to el            # English to Greek
    python translate.py "Γεια σου κόσμε" --from el --to en  # Greek to English
    python translate.py --languages                       # List language codes
"""

import argparse
import sys

from deep_translator import GoogleTranslator

COMMON_LANGUAGES = {
    "en": "English", "el": "Greek", "es": "Spanish", "fr": "French",
    "de": "German", "it": "Italian", "pt": "Portuguese", "ru": "Russian",
    "zh-CN": "Chinese (Simplified)", "ja": "Japanese", "ko": "Korean",
    "ar": "Arabic", "tr": "Turkish", "nl": "Dutch", "pl": "Polish",
    "sv": "Swedish", "da": "Danish", "no": "Norwegian", "fi": "Finnish",
    "cs": "Czech", "hu": "Hungarian", "ro": "Romanian", "bg": "Bulgarian",
    "uk": "Ukrainian", "he": "Hebrew", "th": "Thai", "vi": "Vietnamese",
    "id": "Indonesian", "ms": "Malay", "hi": "Hindi",
}


def translate_text(text, source="auto", target="en"):
    try:
        translator = GoogleTranslator(source=source, target=target)
        result = translator.translate(text)
        return {"original": text, "translated": result, "source_lang": source, "target_lang": target}
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Translate text")
    parser.add_argument("text", nargs="?", help="Text to translate")
    parser.add_argument("--from", dest="source", default="auto")
    parser.add_argument("--to", dest="target", default="en")
    parser.add_argument("--languages", action="store_true")
    args = parser.parse_args()

    if args.languages:
        print("Common Language Codes:\n")
        for code, name in sorted(COMMON_LANGUAGES.items(), key=lambda x: x[1]):
            print(f"  {code:8} {name}")
        return 0

    if not args.text:
        parser.print_help()
        return 1

    result = translate_text(args.text, args.source, args.target)
    if "error" in result:
        print(f"Error: {result['error']}")
        return 1

    source_name = COMMON_LANGUAGES.get(result['source_lang'], result['source_lang'])
    target_name = COMMON_LANGUAGES.get(result['target_lang'], result['target_lang'])
    print(f"From: {source_name}")
    print(f"To: {target_name}\n")
    print(f"Original: {result['original']}")
    print(f"Translated: {result['translated']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
