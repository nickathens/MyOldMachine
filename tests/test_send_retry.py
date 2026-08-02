#!/usr/bin/env python3
"""Unit tests for bot.send_chunks_with_retry.

Run: python3 -m unittest tests.test_send_retry  (from repo root)

The local Bot API server is briefly unreachable while it restarts (manual
/restart, health-monitor recovery, or a systemd/launchd restart). A send issued
in that window fails with NetworkError/TimedOut; before this helper the reply
chunk was logged and silently lost. The helper retries transient connectivity
failures so the reply survives a ~10s blip, while NOT retrying permanent
BadRequest errors (which subclass NetworkError in PTB and would otherwise be
retried in vain).
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep test logging out of the production bot.log

import bot as botmod  # noqa: E402
import core.users as users_mod  # noqa: E402
from bot import send_chunks_with_retry, split_message  # noqa: E402
from telegram.error import BadRequest, NetworkError, TimedOut  # noqa: E402


class SendRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Make retry backoff instant so tests do not actually sleep.
        self._orig_delay = botmod._SEND_RETRY_DELAY
        botmod._SEND_RETRY_DELAY = 0
        # A failing send now queues the reply under the user's data dir. Both
        # roots have to move: get_user_dir resolves through core.users, while
        # drain_outbox scans bot.USERS_DIR. Without this the failure tests
        # write fake replies into the real data/users/1/outbox, where the live
        # drain would try to deliver them to Telegram user 1.
        self._tmp = TemporaryDirectory()
        self._orig_users_data_dir = users_mod.USERS_DATA_DIR
        self._orig_users_dir = botmod.USERS_DIR
        users_mod.USERS_DATA_DIR = Path(self._tmp.name)
        botmod.USERS_DIR = Path(self._tmp.name)

    def tearDown(self):
        botmod._SEND_RETRY_DELAY = self._orig_delay
        users_mod.USERS_DATA_DIR = self._orig_users_data_dir
        botmod.USERS_DIR = self._orig_users_dir
        self._tmp.cleanup()

    async def test_success_on_first_try(self):
        sent = []

        async def send_fn(chunk):
            sent.append(chunk)

        await send_chunks_with_retry(send_fn, "hello", 1)
        self.assertEqual(sent, ["hello"])

    async def test_transient_then_success(self):
        n = {"count": 0}

        async def send_fn(chunk):
            n["count"] += 1
            if n["count"] < 3:
                raise NetworkError("local API restarting")

        await send_chunks_with_retry(send_fn, "hi", 1)
        self.assertEqual(n["count"], 3)  # 2 failures + 1 success

    async def test_persistent_network_error_gives_up_after_max_attempts(self):
        n = {"count": 0}

        async def send_fn(chunk):
            n["count"] += 1
            raise NetworkError("still down")

        await send_chunks_with_retry(send_fn, "hi", 1)
        self.assertEqual(n["count"], botmod._SEND_MAX_ATTEMPTS)

    async def test_timed_out_is_treated_as_transient(self):
        n = {"count": 0}

        async def send_fn(chunk):
            n["count"] += 1
            if n["count"] < 2:
                raise TimedOut()

        await send_chunks_with_retry(send_fn, "hi", 1)
        self.assertEqual(n["count"], 2)

    async def test_bad_request_is_not_retried(self):
        # BadRequest subclasses NetworkError in PTB, but is permanent.
        n = {"count": 0}

        async def send_fn(chunk):
            n["count"] += 1
            raise BadRequest("Message is too long")

        await send_chunks_with_retry(send_fn, "hi", 1)
        self.assertEqual(n["count"], 1)

    async def test_generic_exception_is_not_retried(self):
        n = {"count": 0}

        async def send_fn(chunk):
            n["count"] += 1
            raise ValueError("unexpected")

        await send_chunks_with_retry(send_fn, "hi", 1)
        self.assertEqual(n["count"], 1)

    async def test_all_chunks_delivered_for_long_text(self):
        text = ("A" * 3999 + "\n") * 3
        sent = []

        async def send_fn(chunk):
            sent.append(chunk)

        await send_chunks_with_retry(send_fn, text, 1)
        self.assertEqual(sent, split_message(text))
        self.assertGreaterEqual(len(sent), 2)


if __name__ == "__main__":
    unittest.main()
