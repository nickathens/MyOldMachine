#!/usr/bin/env python3
"""System clipboard read/write via pyperclip.

Linux requires DISPLAY (xclip). On macOS uses pbcopy/pbpaste. On Windows uses
the WinAPI bindings inside pyperclip.
"""
from __future__ import annotations

import argparse
import os
import sys

import pyperclip

MAX_BYTES = 50_000


def cmd_read(args: argparse.Namespace) -> int:
    text = pyperclip.paste()
    if args.truncate and len(text.encode("utf-8")) > MAX_BYTES:
        text = text.encode("utf-8")[:MAX_BYTES].decode("utf-8", "ignore")
        text += "\n[truncated]"
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    if args.text == "-":
        text = sys.stdin.read()
    else:
        text = args.text
    if len(text.encode("utf-8")) > MAX_BYTES:
        print(
            f"Refusing to copy: input is {len(text.encode('utf-8'))} bytes, "
            f"limit is {MAX_BYTES}.",
            file=sys.stderr,
        )
        return 2
    pyperclip.copy(text)
    print(f"Copied {len(text)} chars to clipboard.")
    return 0


def cmd_clear(_args: argparse.Namespace) -> int:
    pyperclip.copy("")
    print("Cleared clipboard.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="System clipboard read/write.")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("read", help="Print clipboard contents to stdout.")
    r.add_argument(
        "--truncate",
        action="store_true",
        help=f"Truncate output at {MAX_BYTES} bytes.",
    )
    r.set_defaults(func=cmd_read)

    w = sub.add_parser("write", help="Copy text to clipboard.")
    w.add_argument("text", help="Text to copy. Use '-' to read from stdin.")
    w.set_defaults(func=cmd_write)

    c = sub.add_parser("clear", help="Clear the clipboard.")
    c.set_defaults(func=cmd_clear)
    return p


def main() -> int:
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":0"
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except pyperclip.PyperclipException as exc:
        print(f"Clipboard error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
