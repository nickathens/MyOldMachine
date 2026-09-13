"""The Mini App's engine and usage endpoints.

These are the first endpoints a non-admin may WRITE through, so the two
things that matter are: the write lands on the caller's own preferences and
nowhere else, and the cross-user usage view stays behind the admin check.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

import miniapp.server as srv  # noqa: E402
from core import engines, usage, users  # noqa: E402
from fastapi import HTTPException  # noqa: E402


class _FakeRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


def _user(uid: str, role: str = "user") -> dict:
    return {"_id": uid, "_profile": {"role": role, "name": f"user{uid}",
                                     "display_name": f"User {uid}"}}


class _EngineCase(unittest.TestCase):
    """The fixture both endpoint classes need, and no tests of its own:
    inheriting from a class that HAS tests re-runs all of them."""

    def setUp(self):
        login = patch("core.engines._login_status", return_value=(True, ""))
        login.start()
        self.addCleanup(login.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="mom-mini-engine-"))
        self._saved_users = users.USERS_DATA_DIR
        users.USERS_DATA_DIR = self.tmp
        engines.probe_cache_clear()
        self._probe = patch("core.engines._cli_version_text",
                            return_value="codex-cli 0.154.0")
        self._probe.start()
        self.addCleanup(self._probe.stop)

    def tearDown(self):
        users.USERS_DATA_DIR = self._saved_users
        engines.probe_cache_clear()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class EngineEndpointTests(_EngineCase):
    def test_the_payload_carries_every_engine_and_the_bot_default(self):
        payload = srv.get_engine(user=_user("7"))
        self.assertEqual(payload["picked"], "")
        self.assertEqual(len(payload["engines"]), len(engines.ENGINES))
        self.assertIn("bot_default_model", payload)
        for row in payload["engines"]:
            self.assertIn("accent", row)
            self.assertIn("available", row)

    def test_a_write_stores_against_the_caller_not_the_body(self):
        request = _FakeRequest({"engine": "astra", "user": "999",
                                "_id": "999", "telegram_id": 999})
        payload = asyncio.run(srv.set_engine(request, user=_user("7")))
        self.assertEqual(payload["picked"], "astra")
        self.assertEqual(engines.user_engine_id(7), "astra")
        self.assertEqual(engines.user_engine_id(999), "")

    def test_an_empty_engine_clears_the_caller_back_to_the_default(self):
        engines.set_user_engine(7, "astra")
        payload = asyncio.run(
            srv.set_engine(_FakeRequest({"engine": ""}), user=_user("7")))
        self.assertEqual(payload["picked"], "")

    def test_an_unknown_engine_is_a_400_and_stores_nothing(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(srv.set_engine(_FakeRequest({"engine": "nope"}),
                                       user=_user("7")))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(engines.user_engine_id(7), "")

    def test_an_unavailable_engine_is_a_400_with_the_reason(self):
        engines.probe_cache_clear()
        with patch("core.engines._cli_version_text", return_value=None):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(srv.set_engine(_FakeRequest({"engine": "astra"}),
                                           user=_user("7")))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("not installed", caught.exception.detail)

    def test_a_non_admin_may_pick_without_being_refused(self):
        # The whole point: /api/provider, /api/model and /api/effort are
        # admin-only because they rewrite .env for everybody. This one is not.
        payload = asyncio.run(
            srv.set_engine(_FakeRequest({"engine": "opus"}), user=_user("7")))
        self.assertEqual(payload["picked"], "opus")


class AdminPickerTests(_EngineCase):
    """An admin sets provider, model and effort; the picker is not theirs.

    Two settings that disagree is the fault being locked out here: the
    picker sat below the model and effort sections and quietly beat them.
    """

    def test_the_payload_tells_the_front_end_to_hide_the_picker(self):
        payload = srv.get_engine(user=_user("7", "admin"))
        self.assertTrue(payload["admin"])
        self.assertEqual(payload["effective"], "")

    def test_an_ordinary_user_still_gets_the_picker(self):
        payload = srv.get_engine(user=_user("7"))
        self.assertFalse(payload["admin"])

    def test_a_write_from_an_admin_is_a_400_and_stores_nothing(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(srv.set_engine(_FakeRequest({"engine": "astra"}),
                                       user=_user("7", "admin")))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(engines.user_engine_id(7), "")


class UsageEndpointTests(unittest.TestCase):
    def setUp(self):
        login = patch("core.engines._login_status", return_value=(True, ""))
        login.start()
        self.addCleanup(login.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="mom-mini-usage-"))
        self._saved_users = users.USERS_DATA_DIR
        self._saved_limits = usage.CLAUDE_LIMITS_FILE
        users.USERS_DATA_DIR = self.tmp
        usage.CLAUDE_LIMITS_FILE = self.tmp / "limits.json"
        usage.codex_cache_clear()
        self._meter = patch("core.usage._codex_rpc_rate_limits", return_value=None)
        self._meter.start()
        self.addCleanup(self._meter.stop)
        usage.record_turn(7, provider="claude-cli", model="claude-opus-5",
                          engine="opus", input_tokens=10, output_tokens=5)
        usage.record_turn(8, provider="codex", model="gpt-6-astra",
                          engine="astra", input_tokens=20, output_tokens=7)

    def tearDown(self):
        users.USERS_DATA_DIR = self._saved_users
        usage.CLAUDE_LIMITS_FILE = self._saved_limits
        usage.codex_cache_clear()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_user_sees_only_their_own_consumption(self):
        payload = srv.get_usage(days=7, user=_user("7"))
        self.assertEqual(payload["you"]["turns"], 1)
        self.assertEqual(payload["you"]["by_model"]["claude-opus-5"]["turns"], 1)
        self.assertIsNone(payload["everyone"])

    def test_an_admin_sees_the_breakdown_by_person(self):
        payload = srv.get_usage(days=7, user=_user("7", role="admin"))
        self.assertIsNotNone(payload["everyone"])
        self.assertEqual({row["id"] for row in payload["everyone"]}, {"7", "8"})

    def test_a_meter_that_cannot_be_read_says_so(self):
        payload = srv.get_usage(days=7, user=_user("7"))
        self.assertTrue(payload["meters"]["codex"]["unavailable"])
        self.assertTrue(payload["meters"]["claude"]["unavailable"])
        self.assertTrue(payload["meters"]["claude"]["reason"])

    def test_the_day_window_is_clamped_to_something_sane(self):
        self.assertEqual(srv.get_usage(days=0, user=_user("7"))["days"], 1)
        self.assertEqual(srv.get_usage(days=9999, user=_user("7"))["days"], 90)


if __name__ == "__main__":
    unittest.main()
