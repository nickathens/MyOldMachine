"""Tests for the queue scope (universal vs per-user).

The bot serves multiple Telegram users from a single OS user. Each
Telegram user always has their own per-user lock; the queue scope is
what happens when two Telegram users send messages at the same instant:

  universal — one LLM call at a time across the whole bot
  per_user  — each Telegram user can run a request in parallel

The wizard writes QUEUE_MODE (and CONCURRENT_REQUESTS for back-compat)
to .env. core/users.queue_mode() reads QUEUE_MODE from the env at
runtime and defaults to per_user.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install import wizard  # noqa: E402


class WriteEnvQueueModeTests(unittest.TestCase):
    """write_env emits the new QUEUE_MODE plus the legacy CONCURRENT_REQUESTS."""

    def _write_and_read(self, config_overrides):
        config = {
            "telegram_token": "x",
            "telegram_user_id": "111",
            "user_name": "test",
            "llm_provider": "claude",
            "llm_model": "claude-sonnet-4-6",
            "llm_api_key": "",
            "bot_name": "MOM",
            "timezone": "UTC",
            "takeover": "workstation",
        }
        config.update(config_overrides)
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            wizard.write_env(repo, config)
            return (repo / ".env").read_text(encoding="utf-8")

    def test_universal_writes_both_keys(self):
        text = self._write_and_read({"queue_mode": "universal"})
        self.assertIn("QUEUE_MODE=universal", text)
        self.assertIn("CONCURRENT_REQUESTS=1", text)

    def test_per_user_writes_both_keys(self):
        text = self._write_and_read({"queue_mode": "per_user"})
        self.assertIn("QUEUE_MODE=per_user", text)
        self.assertIn("CONCURRENT_REQUESTS=0", text)

    def test_missing_queue_mode_defaults_to_per_user(self):
        text = self._write_and_read({})
        self.assertIn("QUEUE_MODE=per_user", text)
        self.assertIn("CONCURRENT_REQUESTS=0", text)

    def test_unknown_queue_mode_defaults_to_per_user(self):
        text = self._write_and_read({"queue_mode": "banana"})
        self.assertIn("QUEUE_MODE=per_user", text)
        self.assertIn("CONCURRENT_REQUESTS=0", text)


class LoadEnvQueueModeTests(unittest.TestCase):
    """_load_config_from_env round-trips the queue mode."""

    def _load_with_env(self, env_text):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / ".env").write_text(env_text, encoding="utf-8")
            return wizard._load_config_from_env(repo)

    def test_queue_mode_universal_round_trip(self):
        config = self._load_with_env(
            "TELEGRAM_TOKEN=x\nLLM_PROVIDER=claude\nLLM_MODEL=m\n"
            "ALLOWED_USERS=1\nQUEUE_MODE=universal\n"
        )
        self.assertEqual(config["queue_mode"], "universal")

    def test_queue_mode_per_user_round_trip(self):
        config = self._load_with_env(
            "TELEGRAM_TOKEN=x\nLLM_PROVIDER=claude\nLLM_MODEL=m\n"
            "ALLOWED_USERS=1\nQUEUE_MODE=per_user\n"
        )
        self.assertEqual(config["queue_mode"], "per_user")

    def test_per_user_with_dash_normalizes_to_underscore(self):
        config = self._load_with_env(
            "TELEGRAM_TOKEN=x\nLLM_PROVIDER=claude\nLLM_MODEL=m\n"
            "ALLOWED_USERS=1\nQUEUE_MODE=per-user\n"
        )
        self.assertEqual(config["queue_mode"], "per_user")

    def test_legacy_concurrent_requests_1_resumes_as_universal(self):
        config = self._load_with_env(
            "TELEGRAM_TOKEN=x\nLLM_PROVIDER=claude\nLLM_MODEL=m\n"
            "ALLOWED_USERS=1\nCONCURRENT_REQUESTS=1\n"
        )
        self.assertEqual(config["queue_mode"], "universal")

    def test_legacy_concurrent_requests_0_resumes_as_per_user(self):
        config = self._load_with_env(
            "TELEGRAM_TOKEN=x\nLLM_PROVIDER=claude\nLLM_MODEL=m\n"
            "ALLOWED_USERS=1\nCONCURRENT_REQUESTS=0\n"
        )
        self.assertEqual(config["queue_mode"], "per_user")

    def test_queue_mode_wins_when_both_present(self):
        config = self._load_with_env(
            "TELEGRAM_TOKEN=x\nLLM_PROVIDER=claude\nLLM_MODEL=m\n"
            "ALLOWED_USERS=1\nCONCURRENT_REQUESTS=1\nQUEUE_MODE=per_user\n"
        )
        self.assertEqual(config["queue_mode"], "per_user")

    def test_no_queue_keys_defaults_to_per_user(self):
        config = self._load_with_env(
            "TELEGRAM_TOKEN=x\nLLM_PROVIDER=claude\nLLM_MODEL=m\n"
            "ALLOWED_USERS=1\n"
        )
        self.assertEqual(config["queue_mode"], "per_user")


class CoreUsersQueueModeTests(unittest.TestCase):
    """core/users.queue_mode reads QUEUE_MODE from the environment."""

    def _reload_users(self):
        # core.users caches USERS_JSON paths at import time; reloading
        # gives each test a clean module-level state.
        import core.users
        return importlib.reload(core.users)

    def test_no_env_defaults_to_per_user(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in ("QUEUE_MODE", "CONCURRENT_REQUESTS"):
                os.environ.pop(key, None)
            users = self._reload_users()
            self.assertEqual(users.queue_mode(), "per_user")
            self.assertFalse(users.queue_enabled())
            self.assertEqual(users.concurrent_requests(), 0)

    def test_universal_env_value(self):
        with mock.patch.dict(os.environ, {"QUEUE_MODE": "universal"}, clear=False):
            users = self._reload_users()
            self.assertEqual(users.queue_mode(), "universal")
            self.assertTrue(users.queue_enabled())
            self.assertEqual(users.concurrent_requests(), 1)

    def test_per_user_env_value(self):
        with mock.patch.dict(os.environ, {"QUEUE_MODE": "per_user"}, clear=False):
            users = self._reload_users()
            self.assertEqual(users.queue_mode(), "per_user")
            self.assertFalse(users.queue_enabled())

    def test_per_user_dash_normalizes(self):
        with mock.patch.dict(os.environ, {"QUEUE_MODE": "per-user"}, clear=False):
            users = self._reload_users()
            self.assertEqual(users.queue_mode(), "per_user")

    def test_unknown_value_falls_through_to_per_user(self):
        with mock.patch.dict(os.environ, {"QUEUE_MODE": "banana"}, clear=False):
            users = self._reload_users()
            self.assertEqual(users.queue_mode(), "per_user")

    def test_concurrent_requests_explicit_int(self):
        with mock.patch.dict(
            os.environ, {"QUEUE_MODE": "universal", "CONCURRENT_REQUESTS": "3"},
            clear=False,
        ):
            users = self._reload_users()
            self.assertEqual(users.concurrent_requests(), 3)

    def test_concurrent_requests_invalid_falls_back(self):
        with mock.patch.dict(
            os.environ, {"QUEUE_MODE": "universal", "CONCURRENT_REQUESTS": "abc"},
            clear=False,
        ):
            users = self._reload_users()
            self.assertEqual(users.concurrent_requests(), 1)

    def tearDown(self):
        # Restore the bot's environment after each test so other tests in
        # the same process see a clean baseline.
        for key in ("QUEUE_MODE", "CONCURRENT_REQUESTS"):
            os.environ.pop(key, None)
        self._reload_users()


if __name__ == "__main__":
    unittest.main()
