#!/usr/bin/env python3
"""Unit tests for bot._locked_call_llm.

Run: python3 -m unittest tests.test_scheduler_llm_lock  (from repo root)

Live Telegram handlers serialize per user via _get_user_lock before calling
call_llm (_process_single, _process_media_group). Scheduled agent jobs used
to be handed bare call_llm, so a job firing mid-conversation ran concurrently
with the user's live turn and the two calls raced on the same session
history. _locked_call_llm wraps call_llm in the same per-user lock; these
tests prove the serialization, the passthrough, and the wiring.
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep test logging out of the production bot.log

import bot as botmod  # noqa: E402


class LockedCallLLMTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_call_llm = botmod.call_llm
        # Fresh lock table so tests do not inherit lock state across cases.
        self._orig_locks = botmod._user_locks
        botmod._user_locks = {}

    def tearDown(self):
        botmod.call_llm = self._orig_call_llm
        botmod._user_locks = self._orig_locks

    async def test_waits_for_held_user_lock(self):
        """A scheduled job for user N waits while N's lock is held."""
        order = []

        async def fake_call_llm(user_id, message, chat=None, images=None):
            order.append("llm")
            return "ok"

        botmod.call_llm = fake_call_llm
        lock = botmod._get_user_lock(7)
        await lock.acquire()  # simulate a live Telegram turn in flight
        task = asyncio.create_task(botmod._locked_call_llm(7, "scheduled"))
        await asyncio.sleep(0.05)
        self.assertEqual(order, [], "job ran while the user lock was held")
        order.append("released")
        lock.release()
        result = await asyncio.wait_for(task, timeout=2)
        self.assertEqual(order, ["released", "llm"])
        self.assertEqual(result, "ok")

    async def test_releases_lock_after_completion(self):
        async def fake_call_llm(user_id, message, chat=None, images=None):
            return "done"

        botmod.call_llm = fake_call_llm
        result = await botmod._locked_call_llm(7, "x")
        self.assertEqual(result, "done")
        self.assertFalse(botmod._get_user_lock(7).locked())

    async def test_releases_lock_on_exception(self):
        async def fake_call_llm(user_id, message, chat=None, images=None):
            raise RuntimeError("boom")

        botmod.call_llm = fake_call_llm
        with self.assertRaises(RuntimeError):
            await botmod._locked_call_llm(7, "x")
        self.assertFalse(botmod._get_user_lock(7).locked())

    async def test_different_users_do_not_block_each_other(self):
        started = []
        release = asyncio.Event()

        async def fake_call_llm(user_id, message, chat=None, images=None):
            started.append(user_id)
            await release.wait()
            return "ok"

        botmod.call_llm = fake_call_llm
        t1 = asyncio.create_task(botmod._locked_call_llm(1, "a"))
        t2 = asyncio.create_task(botmod._locked_call_llm(2, "b"))
        await asyncio.sleep(0.05)
        self.assertEqual(sorted(started), [1, 2], "users must not serialize")
        release.set()
        await asyncio.gather(t1, t2)

    async def test_passes_args_through(self):
        seen = {}

        async def fake_call_llm(user_id, message, chat=None, images=None):
            seen.update(user_id=user_id, message=message,
                        chat=chat, images=images)
            return "ok"

        botmod.call_llm = fake_call_llm
        await botmod._locked_call_llm(9, "msg", chat="CHAT", images=["i"])
        self.assertEqual(seen, {"user_id": 9, "message": "msg",
                                "chat": "CHAT", "images": ["i"]})

    def test_scheduler_is_wired_to_locked_wrapper(self):
        """Regression pin: post_init hands the scheduler the locked wrapper.

        post_init is a closure inside main(), unreachable without booting the
        app, so the wiring is asserted at source level.
        """
        src = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn("scheduler.set_claude_handler(_locked_call_llm)", src)
        self.assertNotIn("scheduler.set_claude_handler(call_llm)", src)


if __name__ == "__main__":
    unittest.main()
