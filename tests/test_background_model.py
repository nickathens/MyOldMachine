#!/usr/bin/env python3
"""Background compaction must pin a cheap model instead of billing at the
CLI's default premium model.

Run: python3 -m unittest tests.test_background_model  (from repo root)

Both compaction spawn sites (the semaphore runner in bot.py and the thread
pool fallback in core/session.py) used to spawn bare `claude -p`, silently
taking the CLI's default model for a summarization task. They now pass
--model from get_background_model() (BACKGROUND_MODEL env, default Haiku).
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import DEFAULT_BACKGROUND_MODEL, get_background_model  # noqa: E402


class TestGetBackgroundModel(unittest.TestCase):
    def test_default_is_haiku(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BACKGROUND_MODEL", None)
            self.assertEqual(get_background_model(), DEFAULT_BACKGROUND_MODEL)
        self.assertIn("haiku", DEFAULT_BACKGROUND_MODEL)

    def test_env_override(self):
        with patch.dict(os.environ, {"BACKGROUND_MODEL": "claude-sonnet-4-6"}):
            self.assertEqual(get_background_model(), "claude-sonnet-4-6")


class TestSpawnSitesPinModel(unittest.TestCase):
    """Source-level pins: both `claude -p` compaction spawns carry --model.

    Spawning real subprocesses in a unit test is not viable, so these assert
    against the registration source the same way test_edited_message_filter
    pins the handler filters.
    """

    def test_bot_semaphore_compaction_passes_model(self):
        source = (ROOT / "bot.py").read_text()
        spawn = re.search(
            r"create_subprocess_exec\(\s*\"claude\", \"-p\",[^\n]*", source,
        )
        self.assertIsNotNone(spawn, "semaphore compaction spawn not found")
        self.assertIn("get_background_model()", spawn.group(0))

    def test_session_thread_compaction_passes_model(self):
        source = (ROOT / "core" / "session.py").read_text()
        spawn = re.search(r"\[\"claude\", \"-p\",[^\]]*\]", source)
        self.assertIsNotNone(spawn, "thread pool compaction spawn not found")
        self.assertIn("get_background_model()", spawn.group(0))

    def test_no_bare_claude_p_spawns_remain(self):
        """No compaction path may spawn `claude -p` without a model flag."""
        for rel in ("bot.py", "core/session.py"):
            source = (ROOT / rel).read_text()
            for match in re.finditer(r"\"claude\", \"-p\"[^\n]*", source):
                self.assertIn(
                    "--model", match.group(0),
                    f"bare claude -p spawn in {rel}: {match.group(0)!r}",
                )


if __name__ == "__main__":
    unittest.main()
