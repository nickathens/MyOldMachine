"""Methodology-report ritual is shed from all but the newest assistant turn.

Every non-trivial delivery ends with a "## Methodology Report" block (the
coding skill mandates it). Old reports are pure bulk in the provider prompt:
the production bot measured ~37K chars of a ~170K-char prompt spent
re-reading them (2026-08-11). The stripper collapses each block to one line;
build_messages applies it to every assistant turn except the newest, so the
freshest report stays discussable while the pile can never rebuild.

Storage is never touched: stored history keeps full text, only the message
list handed to the provider sheds it. The integration tests here drive the
real session store and the real build_messages.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

import bot as botmod  # noqa: E402
import core.users as users_mod  # noqa: E402
from core.conversation_format import (  # noqa: E402
    METHODOLOGY_TRIM_NOTE,
    strip_methodology_reports,
)

_UID_COUNTER = iter(range(920_000_000, 920_000_999))

REPORT = (
    "## Methodology Report\n"
    "- **Research:** checked the API docs\n"
    "- **Plan:** three steps\n"
    "- **Lint:** clean\n"
    "- **Tests:** 12/12 green\n"
    "  wrapped continuation of the tests bullet\n"
    "- **Audit passes:** 3 -- Findings: none\n"
)


class StripUnitTests(unittest.TestCase):
    def test_report_collapses_to_one_line(self):
        text = f"Delivery done.\n\n{REPORT}"
        out = strip_methodology_reports(text)
        self.assertIn("Delivery done.", out)
        self.assertIn(METHODOLOGY_TRIM_NOTE, out)
        self.assertNotIn("**Research:**", out)
        self.assertNotIn("Audit passes", out)

    def test_prose_after_report_survives(self):
        text = f"Done.\n\n{REPORT}\nSend /restart and it goes live."
        out = strip_methodology_reports(text)
        self.assertIn("Send /restart and it goes live.", out)

    def test_unrelated_list_after_blank_line_survives(self):
        text = (f"{REPORT}\n"
                "Next steps:\n"
                "- keep this bullet\n"
                "- and this one\n")
        out = strip_methodology_reports(text)
        self.assertIn("- keep this bullet", out)
        self.assertNotIn("**Plan:**", out)

    def test_mention_in_prose_is_not_a_block(self):
        text = "I always end with a Methodology Report so you can audit me."
        self.assertEqual(strip_methodology_reports(text), text)

    def test_fenced_template_quote_is_untouched(self):
        text = ("Here is the template:\n"
                "```\n"
                "## Methodology Report\n"
                "- **Research:** ...\n"
                "```\n"
                "Use it on every delivery.")
        self.assertEqual(strip_methodology_reports(text), text)

    def test_heading_level_and_case_tolerant(self):
        text = "### methodology report\n- **Lint:** clean\n"
        out = strip_methodology_reports(text)
        self.assertIn(METHODOLOGY_TRIM_NOTE, out)
        self.assertNotIn("Lint", out)

    def test_multiple_reports_all_collapse(self):
        text = f"First.\n{REPORT}\nMiddle prose.\n{REPORT}"
        out = strip_methodology_reports(text)
        self.assertEqual(out.count(METHODOLOGY_TRIM_NOTE), 2)
        self.assertIn("Middle prose.", out)

    def test_idempotent(self):
        text = f"Done.\n{REPORT}"
        once = strip_methodology_reports(text)
        self.assertEqual(strip_methodology_reports(once), once)

    def test_non_string_passes_through(self):
        payload = {"kind": "voice"}
        self.assertIs(strip_methodology_reports(payload), payload)

    def test_text_without_report_is_unchanged(self):
        text = "A plain reply.\n- with a list\n- of bullets"
        self.assertEqual(strip_methodology_reports(text), text)


class BuildMessagesIntegrationTests(unittest.TestCase):
    """Drive the real session store and the real build_messages."""

    def setUp(self):
        self.uid = next(_UID_COUNTER)
        self._tmp = TemporaryDirectory()
        self.users_dir = Path(self._tmp.name) / "users"
        self.users_dir.mkdir(parents=True)
        for p in (
            patch.object(botmod, "USERS_DIR", self.users_dir),
            patch.object(users_mod, "USERS_DATA_DIR", self.users_dir),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_only_newest_assistant_turn_keeps_its_report(self):
        # The first user turn QUOTES a report block: user turns must never be
        # stripped, so it has to arrive verbatim (a strip-everything mutation
        # would otherwise be invisible, since ordinary user text is a no-op
        # for the stripper).
        history = [
            {"role": "user", "content": f"why does my message say\n{REPORT}"},
            {"role": "assistant", "content": f"Built it.\n{REPORT}"},
            {"role": "user", "content": "now fix the bug"},
            {"role": "assistant", "content": f"Fixed it.\n{REPORT}"},
            {"role": "user", "content": "thanks"},
        ]
        session = botmod.get_session(self.uid)
        session.save_conversation(history)

        messages = botmod.build_messages(self.uid, "one more thing")

        assistant = [m for m in messages if m.role == "assistant"]
        self.assertEqual(len(assistant), 2)
        # Older assistant turn: report collapsed, verdict note present.
        self.assertIn(METHODOLOGY_TRIM_NOTE, assistant[0].content)
        self.assertNotIn("**Research:**", assistant[0].content)
        self.assertIn("Built it.", assistant[0].content)
        # Newest assistant turn: report intact.
        self.assertIn("**Research:**", assistant[1].content)
        # User turns and the new message are untouched — including a user
        # turn that quotes a full report block.
        users = [m for m in messages if m.role == "user"]
        self.assertEqual(users[-1].content, "one more thing")
        self.assertIn("**Research:**", users[0].content)
        self.assertNotIn(METHODOLOGY_TRIM_NOTE, users[0].content)

    def test_stored_history_keeps_full_text(self):
        history = [
            {"role": "assistant", "content": f"Old delivery.\n{REPORT}"},
            {"role": "assistant", "content": "Newest reply."},
        ]
        session = botmod.get_session(self.uid)
        session.save_conversation(history)
        botmod.build_messages(self.uid, "hello")
        stored = botmod.get_session(self.uid).load_conversation()
        self.assertIn("**Research:**", stored[0]["content"])


if __name__ == "__main__":
    unittest.main()
