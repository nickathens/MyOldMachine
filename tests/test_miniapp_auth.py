"""Unit tests for miniapp.auth — Telegram initData HMAC validation."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from miniapp.auth import validate_init_data  # noqa: E402


def _sign(bot_token: str, fields: dict) -> str:
    """Build a valid Telegram initData query string for `fields`.

    Mirrors the documented algorithm exactly so we can produce test fixtures
    without depending on the bot framework: sort pairs alphabetically, join
    with newlines, HMAC-SHA256 with key derived from 'WebAppData' + token.
    """
    data_pairs = []
    for k in sorted(fields.keys()):
        data_pairs.append(f"{k}={fields[k]}")
    data_check_string = "\n".join(data_pairs)
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    parts = [f"{k}={quote(str(v), safe='')}" for k, v in fields.items()]
    parts.append(f"hash={signature}")
    return "&".join(parts)


class TestValidateInitData(unittest.TestCase):
    TOKEN = "123456:fake-bot-token-for-tests"

    def setUp(self) -> None:
        self.user = {"id": 8044898180, "first_name": "Nick", "language_code": "en"}
        self.now = int(time.time())

    def test_valid_signature_returns_user_dict(self) -> None:
        init_data = _sign(self.TOKEN, {
            "auth_date": self.now,
            "user": json.dumps(self.user, separators=(",", ":")),
        })
        result = validate_init_data(init_data, self.TOKEN)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], 8044898180)
        self.assertEqual(result["first_name"], "Nick")

    def test_tampered_user_field_rejected(self) -> None:
        init_data = _sign(self.TOKEN, {
            "auth_date": self.now,
            "user": json.dumps(self.user, separators=(",", ":")),
        })
        # Mutate the user payload after signing → HMAC must fail.
        tampered = init_data.replace(
            quote(json.dumps(self.user, separators=(",", ":")), safe=""),
            quote(json.dumps({"id": 999}, separators=(",", ":")), safe=""),
        )
        self.assertIsNone(validate_init_data(tampered, self.TOKEN))

    def test_wrong_token_rejected(self) -> None:
        init_data = _sign(self.TOKEN, {
            "auth_date": self.now,
            "user": json.dumps(self.user, separators=(",", ":")),
        })
        self.assertIsNone(validate_init_data(init_data, "wrong-token"))

    def test_expired_auth_date_rejected(self) -> None:
        old = self.now - (86400 + 100)
        init_data = _sign(self.TOKEN, {
            "auth_date": old,
            "user": json.dumps(self.user, separators=(",", ":")),
        })
        self.assertIsNone(validate_init_data(init_data, self.TOKEN))

    def test_missing_hash_rejected(self) -> None:
        self.assertIsNone(validate_init_data("auth_date=123&user=%7B%7D", self.TOKEN))

    def test_missing_user_rejected(self) -> None:
        # Signature is valid but user field is absent.
        fields = {"auth_date": self.now}
        data_pairs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
        secret = hmac.new(b"WebAppData", self.TOKEN.encode(), hashlib.sha256).digest()
        sig = hmac.new(secret, data_pairs.encode(), hashlib.sha256).hexdigest()
        init_data = f"auth_date={self.now}&hash={sig}"
        self.assertIsNone(validate_init_data(init_data, self.TOKEN))


if __name__ == "__main__":
    unittest.main()
