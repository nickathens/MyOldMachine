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
sys.path.insert(0, str(BOT_DIR))
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
    return custom if custom else STANDARD_API


def api_url(token: str, method: str) -> str:
    return f"{get_api_base()}/bot{token}/{method}"


# Telegram refuses a caption over 1024 characters, and with it the whole file.
CAPTION_LIMIT = 1024


def _accepted_or_say_why(response: httpx.Response, what: str) -> bool:
    """True when Telegram accepted the call. Otherwise print its own reason.

    "Document sent: False" said nothing about why, so a turn could not tell a
    caption that was too long from a file over the size limit or a bad chat;
    and a reply that was not JSON (a proxy's error page) ended in a traceback
    (Linux bot sweep 2026-10-07)."""
    try:
        data = response.json()
    except ValueError:
        print(f"{what}: Telegram answered HTTP {response.status_code} with no JSON",
              file=sys.stderr)
        return False
    if data.get("ok"):
        return True
    print(f"{what} refused by Telegram: "
          f"{data.get('description') or f'HTTP {response.status_code}'}", file=sys.stderr)
    return False


def send_message(token, chat_id, text):
    # Without an explicit timeout httpx waits forever; a frozen Bot API server
    # would hang the calling process indefinitely. send_file already sets one;
    # send_message must too.
    r = httpx.post(
        api_url(token, "sendMessage"),
        data={"chat_id": chat_id, "text": text},
        timeout=30,
    )
    return _accepted_or_say_why(r, "Message")


def send_file(token, chat_id, method, field, path, caption=None):
    with open(path, "rb") as f:
        data = {"chat_id": chat_id}
        if caption:
            # A longer caption made Telegram refuse the file itself, so the
            # file never arrived; now the file goes and the cut is said.
            if len(caption) > CAPTION_LIMIT:
                print(f"Caption was {len(caption)} characters; Telegram takes {CAPTION_LIMIT}, "
                      "so it was cut. Send the rest as a message.", file=sys.stderr)
                caption = caption[:CAPTION_LIMIT - 1].rstrip() + "\u2026"
            data["caption"] = caption
        r = httpx.post(api_url(token, method), data=data, files={field: f}, timeout=300)
    return _accepted_or_say_why(r, field.capitalize())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", type=int, required=True)
    parser.add_argument("--message", type=str)
    parser.add_argument("--photo", type=str)
    parser.add_argument("--video", type=str)
    parser.add_argument("--document", type=str)
    parser.add_argument("--voice", type=str, help="OGG/Opus voice note")
    parser.add_argument("--caption", type=str)
    args = parser.parse_args()

    from utils.session_guard import enforce as _enforce_session_user
    _enforce_session_user(args.user)

    token = get_token()

    if not any([args.message, args.photo, args.video, args.document, args.voice]):
        print("Error: No content specified. Use --message, --photo, --video, --document, or --voice")
        sys.exit(1)

    all_ok = True
    if args.message:
        ok = send_message(token, args.user, args.message)
        all_ok &= bool(ok)
        print(f"Message sent: {ok}")
    for flag, method, field, label in (
        (args.photo, "sendPhoto", "photo", "Photo"),
        (args.video, "sendVideo", "video", "Video"),
        (args.document, "sendDocument", "document", "Document"),
        (args.voice, "sendVoice", "voice", "Voice"),
    ):
        if not flag:
            continue
        ok = send_file(token, args.user, method, field, flag, args.caption)
        all_ok &= bool(ok)
        print(f"{label} sent: {ok}")

    # Exit status carries the verdict. Callers that gate on the return code
    # (alert sweeps, scheduled deliverers, shell pipelines) used to read
    # "Document sent: False" with exit 0 as delivered (audit F06, 2026-09-06).
    if not all_ok:
        print("Error: Telegram did not accept every send", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
