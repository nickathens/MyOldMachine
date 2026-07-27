#!/usr/bin/env python3
"""A failed turn must not enter conversation history.

Run: python3 -m unittest tests.test_failed_turn_history  (from repo root)

During the 2026-07-20 auth outage every failed turn was stored as a normal
assistant message. The model then read "Failed to authenticate. API Error:
401 ..." back as its own prior speech on every following turn, so the
context stayed poisoned even after the credential was repaired.

call_llm records the user in bot._failed_turns when the provider reports an
error; _save_and_send skips conversation persistence for that turn and
clears the flag. The append-only exchange log is still written, so the
failure stays visible for debugging without entering the context window.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep test logging out of the production bot.log

import bot as botmod  # noqa: E402


class FailedTurnHistoryTests(unittest.TestCase):
    def setUp(self):
        botmod._failed_turns.discard(7)
        botmod._conversation_cache.pop(7, None)
        self.session = MagicMock()
        self.session.get_current_topic.return_value = None
        self.session.load_conversation.return_value = []
        self.session.should_compact.return_value = (False, "")

    def tearDown(self):
        botmod._failed_turns.discard(7)
        botmod._conversation_cache.pop(7, None)

    def test_successful_turn_is_stored(self):
        with patch.object(botmod, "log_exchange"):
            botmod._save_and_send(7, "hello", "hi there", session=self.session)
        self.session.save_conversation.assert_called_once()
        stored = self.session.save_conversation.call_args[0][0]
        self.assertEqual(stored[-1], {"role": "assistant", "content": "hi there"})

    def test_failed_turn_is_not_stored(self):
        botmod._failed_turns.add(7)
        with patch.object(botmod, "log_exchange"):
            botmod._save_and_send(
                7, "hello", "I can't reach Claude right now", session=self.session)
        self.session.save_conversation.assert_not_called()

    def test_failed_turn_is_still_logged_for_debugging(self):
        botmod._failed_turns.add(7)
        with patch.object(botmod, "log_exchange") as log_exchange:
            botmod._save_and_send(7, "hello", "failure text", session=self.session)
        log_exchange.assert_called_once()

    def test_flag_is_cleared_so_the_next_turn_stores_normally(self):
        botmod._failed_turns.add(7)
        with patch.object(botmod, "log_exchange"):
            botmod._save_and_send(7, "hello", "failure text", session=self.session)
        self.assertNotIn(7, botmod._failed_turns)

        with patch.object(botmod, "log_exchange"):
            botmod._save_and_send(7, "again", "real answer", session=self.session)
        self.session.save_conversation.assert_called_once()

    def test_failed_turn_drops_the_conversation_cache(self):
        # The normal path pops this; the early return must too, or a later
        # turn reuses a stale in-memory history.
        botmod._failed_turns.add(7)
        botmod._conversation_cache[7] = [{"role": "user", "content": "stale"}]
        with patch.object(botmod, "log_exchange"):
            botmod._save_and_send(7, "hello", "failure text", session=self.session)
        self.assertNotIn(7, botmod._conversation_cache)

    def test_one_users_failure_does_not_suppress_another(self):
        botmod._failed_turns.add(7)
        other = MagicMock()
        other.get_current_topic.return_value = None
        other.load_conversation.return_value = []
        other.should_compact.return_value = (False, "")
        with patch.object(botmod, "log_exchange"):
            botmod._save_and_send(8, "hello", "hi there", session=other)
        other.save_conversation.assert_called_once()
        self.assertIn(7, botmod._failed_turns)  # still pending for user 7


if __name__ == "__main__":
    unittest.main()
