"""Telegram Mini App initData HMAC-SHA256 validation.

Implements Telegram's documented validation for WebApp init_data:
  https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

Returns the parsed user dict on success, or None on any failure (bad HMAC,
expired auth_date, missing or malformed user payload).
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, unquote


def validate_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int = 3600,
) -> dict | None:
    """Validate Telegram WebApp initData and return the parsed user dict.

    Args:
        init_data: The raw `initData` query string from `window.Telegram.WebApp`.
        bot_token: The bot's Telegram token, used as the HMAC secret seed.
        max_age_seconds: Reject auth_date older than this (default 1h).
            Telegram mints fresh initData every time the Mini App opens, so a
            short window costs nothing in UX and shrinks the replay value of a
            leaked initData string.

    Returns:
        A dict with the user fields (id, first_name, ...) on success.
        None if the HMAC is invalid, the payload is expired, or anything is
        missing or malformed.
    """
    parsed = parse_qs(init_data)

    received_hash = parsed.get("hash", [None])[0]
    if not received_hash:
        return None

    data_pairs = []
    for chunk in init_data.split("&"):
        key, _, value = chunk.partition("=")
        if key != "hash":
            data_pairs.append(f"{key}={unquote(value)}")
    data_pairs.sort()
    data_check_string = "\n".join(data_pairs)

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    auth_date_str = parsed.get("auth_date", ["0"])[0]
    try:
        auth_date = int(auth_date_str)
    except ValueError:
        return None

    if time.time() - auth_date > max_age_seconds:
        return None

    user_str = parsed.get("user", [None])[0]
    if not user_str:
        return None

    try:
        return json.loads(user_str)
    except (json.JSONDecodeError, TypeError):
        return None
