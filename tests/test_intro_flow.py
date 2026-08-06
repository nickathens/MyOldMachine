"""Tests for the per-user intro flow.

Covers:
- intro_shown / intro_done marker helpers in core.memory
- run_intro_reflection in utils.reflect (success, no-LLM, parse-error paths)
- Migration logic: existing model.md must mark both flags so prior users do
  not see the intro again on the next deployment.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep synthetic output out of the real reflection.log

from core.memory import MemoryManager  # noqa: E402
from utils import reflect  # noqa: E402


class IntroMarkerTests(unittest.TestCase):
    """The two filesystem markers must round-trip and stay independent."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mm = MemoryManager(Path(self._tmp.name))

    def test_new_user_has_no_markers(self):
        self.assertFalse(self.mm.intro_shown(123))
        self.assertFalse(self.mm.intro_done(123))

    def test_mark_and_check(self):
        self.mm.mark_intro_shown(456)
        self.assertTrue(self.mm.intro_shown(456))
        self.assertFalse(self.mm.intro_done(456))

        self.mm.mark_intro_done(456)
        self.assertTrue(self.mm.intro_done(456))

    def test_markers_are_per_user(self):
        self.mm.mark_intro_shown(1)
        self.mm.mark_intro_done(1)
        self.assertFalse(self.mm.intro_shown(2))
        self.assertFalse(self.mm.intro_done(2))

    def test_mark_is_idempotent(self):
        self.mm.mark_intro_shown(7)
        self.mm.mark_intro_shown(7)  # second call must not raise
        self.assertTrue(self.mm.intro_shown(7))


class IntroPromptBuilderTests(unittest.TestCase):
    """The intro reflection prompt must include the user's reply verbatim."""

    def test_prompt_includes_reply_and_intro_context(self):
        prompt = reflect._build_intro_prompt(
            user_name="Telegram-Display-Name",
            intro_message="Hi! I am Bot. What should I call you?",
            user_reply="Call me Nick. I make films and want help with audio.",
        )
        self.assertIn("Nick", prompt)
        self.assertIn("films", prompt)
        self.assertIn("Telegram-Display-Name", prompt)
        self.assertIn("---MODEL_START---", prompt)
        self.assertIn("---MODEL_END---", prompt)

    def test_prompt_truncates_huge_input(self):
        huge = "x" * 100_000
        prompt = reflect._build_intro_prompt("Name", huge, huge)
        # Reply and intro are each capped at 2000 chars in the builder
        self.assertLess(len(prompt), 12_000)


class IntroReflectionTests(unittest.TestCase):
    """run_intro_reflection writes a seed model on success and degrades cleanly."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mm = MemoryManager(Path(self._tmp.name))

    def _good_llm_output(self) -> str:
        return (
            "Some preamble.\n"
            "---MODEL_START---\n"
            "# Nick — Working Model\n\n"
            "Last updated: 2026-05-07\n\n"
            "## Identity\n"
            "- Name: Nick\n"
            "- Role: filmmaker\n\n"
            "## Preferences\n"
            "Direct, technical when needed.\n\n"
            "## Current State\n"
            "Wants help with audio editing.\n\n"
            "## Relationship\n"
            "- Trust level: New user\n"
            "- Expectations: To be established as we work together\n"
            "---MODEL_END---\n"
            "Trailing junk."
        )

    def test_successful_reflection_writes_model(self):
        with patch.object(reflect, "_call_claude_cli",
                          return_value=self._good_llm_output()), \
             patch.object(reflect, "_call_api", return_value=""):
            result = reflect.run_intro_reflection(
                self.mm, 9999, "Nick",
                "Bot intro text.", "User reply text.",
            )
        self.assertEqual(result["status"], "updated")
        self.assertGreater(result["size"], 0)
        model = self.mm.get_model(9999)
        self.assertIn("Nick", model)
        self.assertIn("filmmaker", model)
        # Markers from the bot are not the reflection's responsibility, but the
        # model itself must be persisted.
        self.assertTrue(model.startswith("# Nick"))

    def test_no_llm_returns_status(self):
        with patch.object(reflect, "_call_claude_cli", return_value=""), \
             patch.object(reflect, "_call_api", return_value=""):
            result = reflect.run_intro_reflection(
                self.mm, 1234, "User",
                "intro", "reply",
            )
        self.assertEqual(result["status"], "no_llm")
        # Model must NOT be created if the LLM call failed
        self.assertEqual(self.mm.get_model(1234), "")

    def test_parse_error_when_no_markers(self):
        with patch.object(reflect, "_call_claude_cli",
                          return_value="just plain text, no markers"), \
             patch.object(reflect, "_call_api", return_value=""):
            result = reflect.run_intro_reflection(
                self.mm, 5678, "User",
                "intro", "reply",
            )
        self.assertEqual(result["status"], "parse_error")
        self.assertEqual(self.mm.get_model(5678), "")

    def test_falls_back_to_api_when_cli_unavailable(self):
        with patch.object(reflect, "_call_claude_cli", return_value=""), \
             patch.object(reflect, "_call_api",
                          return_value=self._good_llm_output()):
            result = reflect.run_intro_reflection(
                self.mm, 7777, "Nick",
                "intro", "reply",
            )
        self.assertEqual(result["status"], "updated")


class MigrationLogicTests(unittest.TestCase):
    """The startup migration must mark prior users as intro-complete.

    Re-implements the migration block from bot.post_init in isolation so the
    behavioural contract is testable without booting Telegram.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mm = MemoryManager(Path(self._tmp.name))

    @staticmethod
    def _migrate(mm: MemoryManager):
        for uid in mm.get_all_users():
            if mm.get_model(uid):
                if not mm.intro_shown(uid):
                    mm.mark_intro_shown(uid)
                if not mm.intro_done(uid):
                    mm.mark_intro_done(uid)

    def test_existing_user_with_model_gets_both_markers(self):
        self.mm.init_model(42, name="ExistingUser")
        self.assertFalse(self.mm.intro_shown(42))

        self._migrate(self.mm)

        self.assertTrue(self.mm.intro_shown(42))
        self.assertTrue(self.mm.intro_done(42))

    def test_user_without_model_is_not_migrated(self):
        # Touch a user dir without a model file
        self.mm._user_dir(99)
        self._migrate(self.mm)
        self.assertFalse(self.mm.intro_shown(99))
        self.assertFalse(self.mm.intro_done(99))

    def test_migration_is_idempotent(self):
        self.mm.init_model(7, name="User")
        self._migrate(self.mm)
        self._migrate(self.mm)  # second run must not raise or change state
        self.assertTrue(self.mm.intro_shown(7))
        self.assertTrue(self.mm.intro_done(7))

    def test_migration_preserves_existing_done_marker(self):
        self.mm.init_model(11, name="User")
        self.mm.mark_intro_shown(11)
        self.mm.mark_intro_done(11)
        self._migrate(self.mm)
        self.assertTrue(self.mm.intro_shown(11))
        self.assertTrue(self.mm.intro_done(11))


if __name__ == "__main__":
    unittest.main()
