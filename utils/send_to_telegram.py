#!/usr/bin/env python3
"""
Send files or messages to Telegram users via the bot.
Usage:
    python send_to_telegram.py --user USER_ID --message "text"
    python send_to_telegram.py --user USER_ID --photo /path/to/image.png
    python send_to_telegram.py --user USER_ID --video /path/to/video.mp4
    python send_to_telegram.py --user USER_ID --document /path/to/file.pdf
    python send_to_telegram.py --user USER_ID --photo /path/to/img.png --caption "Description"
"""

import argparse
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

BOT_DIR = Path(__file__).parent.parent
load_dotenv(BOT_DIR / ".env")

STANDARD_API = "https://api.telegram.org"


def get_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    return token


def get_api_base() -> str:
    custom = os.environ.get("TELEGRAM_API_BASE", "")
    if custom:
        try:
            httpx.get(f"{custom}/", timeout=2)
            return custom
        except Exception:
            pass
    return STANDARD_API


def api_url(token: str, method: str) -> str:
    return f"{get_api_base()}/bot{token}/{method}"


def send_message(token, chat_id, text):
    r = httpx.post(api_url(token, "sendMessage"), data={"chat_id": chat_id, "text": text})
    return r.json().get("ok", False)


def send_file(token, chat_id, method, field, path, caption=None):
    with open(path, "rb") as f:
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        r = httpx.post(api_url(token, method), data=data, files={field: f}, timeout=300)
    return r.json().get("ok", False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", type=int, required=True)
    parser.add_argument("--message", type=str)
    parser.add_argument("--photo", type=str)
    parser.add_argument("--video", type=str)
    parser.add_argument("--document", type=str)
    parser.add_argument("--caption", type=str)
    args = parser.parse_args()

    token = get_token()

    if args.message:
        print(f"Message sent: {send_message(token, args.user, args.message)}")
    if args.photo:
        print(f"Photo sent: {send_file(token, args.user, 'sendPhoto', 'photo', args.photo, args.caption)}")
    if args.video:
        print(f"Video sent: {send_file(token, args.user, 'sendVideo', 'video', args.video, args.caption)}")
    if args.document:
        print(f"Document sent: {send_file(token, args.user, 'sendDocument', 'document', args.document, args.caption)}")

    if not any([args.message, args.photo, args.video, args.document]):
        print("Error: No content specified. Use --message, --photo, --video, or --document")
        sys.exit(1)


if __name__ == "__main__":
    main()
