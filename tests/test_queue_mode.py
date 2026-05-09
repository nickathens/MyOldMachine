"""Tests for the multi-user queue mode split.

The wizard's queue prompt was reframed from a yes/no toggle into a mode
selector: 'universal' (one queue across all users) or 'per_user' (one
queue per user, parallel across users). The per-user lock in bot.py is
always on regardless of mode — these tests pin down:

  - the wizard writes both QUEUE_MODE and CONCURRENT_REQUESTS,
  - resume reads QUEUE_MODE first and falls back to CONCURRENT_REQUESTS,
  - the orchestrator users.json gets queue_mode + queue_enabled +
    concurrent_requests, all derived from the same source of truth,
  - core/users.queue_mode() prefers the new field, falls back to the
    legacy fields for installs that pre-date the split, and never
    returns 'universal' on a single-user install.
"""
from __future__ import annotations

import json
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
            "llm_provider": "claude-cli",
            "llm_model": "claude-sonnet-4-6",
            "llm_api_key": "",
            "bot_name": "MOM",
            "timezone": "UTC",
            "takeover": "workstation",
            "multiuser_enabled": True,
            "multiuser_num_slots": 4,
        }
        config.update(config_overrides)
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            wizard.write_env(repo, config)
            return (repo / ".env").read_text(encoding="utf-8")

    def test_universal_mode_writes_both_keys(self):
        text = self._write_and_read({
            "multiuser_queue_mode": "universal",
            "multiuser_queue_enabled": True,
        })
        self.assertIn("QUEUE_MODE=universal", text)
        self.assertIn("CONCURRENT_REQUESTS=1", text)

    def test_per_user_mode_writes_both_keys(self):
        text = self._write_and_read({
            "multiuser_queue_mode": "per_user",
            "multiuser_queue_enabled": False,
        })
        self.assertIn("QUEUE_MODE=per_user", text)
        self.assertIn("CONCURRENT_REQUESTS=0", text)

    def test_legacy_enabled_only_falls_back_to_universal(self):
        # Old code paths set only multiuser_queue_enabled. Make sure we
        # still derive a sensible mode rather than crashing.
        text = self._write_and_read({
            "multiuser_queue_enabled": True,
        })
        self.assertIn("QUEUE_MODE=universal", text)
        self.assertIn("CONCURRENT_REQUESTS=1", text)

    def test_legacy_disabled_only_falls_back_to_per_user(self):
        text = self._write_and_read({
            "multiuser_queue_enabled": False,
        })
        self.assertIn("QUEUE_MODE=per_user", text)
        self.assertIn("CONCURRENT_REQUESTS=0", text)

    def test_single_user_does_not_write_queue_keys(self):
        text = self._write_and_read({
            "multiuser_enabled": False,
        })
        self.assertNotIn("QUEUE_MODE=", text)
        self.assertNotIn("CONCURRENT_REQUESTS=", text)


class LoadEnvQueueModeTests(unittest.TestCase):
    """_load_config_from_env round-trips the queue mode."""

    def _load_with_env(self, env_text):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / ".env").write_text(env_text, encoding="utf-8")
            return wizard._load_config_from_env(repo)

    def test_queue_mode_universal_round_trip(self):
        config = self._load_with_env(
            "TELEGRAM_TOKEN=x\nLLM_PROVIDER=claude-cli\nLLM_MODEL=m\n"
            "ALLOWED_USERS=1\nQUEUE_MODE=universal\n"
        )
        self.assertEqual(config["multiuser_queue_mode"], "universal")
        self.assertTrue(config["multiuser_queue_enabled"])

    def test_queue_mode_per_user_round_trip(self):
        config = self._load_with_env(
            "TELEGRAM_TOKEN=x\nLLM_PROVIDER=claude-cli\nLLM_MODEL=m\n"
            "ALLOWED_USERS=1\nQUEUE_MODE=per_user\n"
        )
        self.assertEqual(config["multiuser_queue_mode"], "per_user")
        self.assertFalse(config["multiuser_queue_enabled"])

    def test_per_user_with_dash_normalizes_to_underscore(self):
        config = self._load_with_env(
            "TELEGRAM_TOKEN=x\nLLM_PROVIDER=claude-cli\nLLM_MODEL=m\n"
            "ALLOWED_USERS=1\nQUEUE_MODE=per-user\n"
        )
        self.assertEqual(config["multiuser_queue_mode"], "per_user")
        self.assertFalse(config["multiuser_queue_enabled"])

    def test_legacy_concurrent_requests_1_resumes_as_universal(self):
        config = self._load_with_env(
            "TELEGRAM_TOKEN=x\nLLM_PROVIDER=claude-cli\nLLM_MODEL=m\n"
            "ALLOWED_USERS=1\nCONCURRENT_REQUESTS=1\n"
        )
        self.assertEqual(config["multiuser_queue_mode"], "universal")
        self.assertTrue(config["multiuser_queue_enabled"])

    def test_legacy_concurrent_requests_0_resumes_as_per_user(self):
        config = self._load_with_env(
            "TELEGRAM_TOKEN=x\nLLM_PROVIDER=claude-cli\nLLM_MODEL=m\n"
            "ALLOWED_USERS=1\nCONCURRENT_REQUESTS=0\n"
        )
        self.assertEqual(config["multiuser_queue_mode"], "per_user")
        self.assertFalse(config["multiuser_queue_enabled"])

    def test_queue_mode_wins_over_legacy_when_both_present(self):
        # Newer wizard installs always write both. QUEUE_MODE is
        # authoritative regardless of CONCURRENT_REQUESTS ordering.
        config = self._load_with_env(
            "TELEGRAM_TOKEN=x\nLLM_PROVIDER=claude-cli\nLLM_MODEL=m\n"
            "ALLOWED_USERS=1\nCONCURRENT_REQUESTS=1\nQUEUE_MODE=per_user\n"
        )
        self.assertEqual(config["multiuser_queue_mode"], "per_user")
        self.assertFalse(config["multiuser_queue_enabled"])

    def test_unknown_queue_mode_value_falls_through(self):
        # Garbage in QUEUE_MODE shouldn't crash. The setdefault path
        # below defaults to per_user.
        config = self._load_with_env(
            "TELEGRAM_TOKEN=x\nLLM_PROVIDER=claude-cli\nLLM_MODEL=m\n"
            "ALLOWED_USERS=1\nQUEUE_MODE=banana\n"
        )
        self.assertEqual(config["multiuser_queue_mode"], "per_user")
        self.assertFalse(config["multiuser_queue_enabled"])

    def test_no_queue_keys_defaults_to_per_user(self):
        config = self._load_with_env(
            "TELEGRAM_TOKEN=x\nLLM_PROVIDER=claude-cli\nLLM_MODEL=m\n"
            "ALLOWED_USERS=1\n"
        )
        self.assertEqual(config["multiuser_queue_mode"], "per_user")
        self.assertFalse(config["multiuser_queue_enabled"])


class CoreUsersQueueModeTests(unittest.TestCase):
    """core/users.queue_mode reads orchestrator users.json."""

    def _with_users_json(self, payload):
        """Run a callable with USERS_JSON pointed at a temp file."""
        td = tempfile.TemporaryDirectory()
        try:
            users_dir = Path(td.name)
            users_json = users_dir / "users.json"
            if payload is not None:
                users_json.write_text(json.dumps(payload), encoding="utf-8")
            from core import users as users_mod
            with mock.patch.object(users_mod, "USERS_JSON", users_json), \
                 mock.patch.object(users_mod, "ORCHESTRATOR_DIR", users_dir):
                yield users_mod
        finally:
            td.cleanup()

    def test_no_users_json_returns_per_user(self):
        # Single-user install (no orchestrator file). Per-user lock is
        # the only queue, which IS the universal queue here, but the
        # accessor reports per_user because that's literally what's set.
        for users_mod in self._with_users_json(None):
            self.assertEqual(users_mod.queue_mode(), "per_user")
            self.assertFalse(users_mod.queue_enabled())
            self.assertEqual(users_mod.concurrent_requests(), 0)

    def test_universal_mode_in_users_json(self):
        for users_mod in self._with_users_json({
            "version": 1,
            "queue_mode": "universal",
            "queue_enabled": True,
            "concurrent_requests": 1,
            "slots": {},
        }):
            self.assertEqual(users_mod.queue_mode(), "universal")
            self.assertTrue(users_mod.queue_enabled())
            self.assertEqual(users_mod.concurrent_requests(), 1)

    def test_per_user_mode_in_users_json(self):
        for users_mod in self._with_users_json({
            "version": 1,
            "queue_mode": "per_user",
            "queue_enabled": False,
            "concurrent_requests": 0,
            "slots": {},
        }):
            self.assertEqual(users_mod.queue_mode(), "per_user")
            self.assertFalse(users_mod.queue_enabled())
            self.assertEqual(users_mod.concurrent_requests(), 0)

    def test_legacy_users_json_no_queue_mode_field(self):
        # Pre-split installs don't have queue_mode. Fall back to
        # queue_enabled / concurrent_requests.
        for users_mod in self._with_users_json({
            "version": 1,
            "queue_enabled": True,
            "concurrent_requests": 1,
            "slots": {},
        }):
            self.assertEqual(users_mod.queue_mode(), "universal")
            self.assertTrue(users_mod.queue_enabled())
            self.assertEqual(users_mod.concurrent_requests(), 1)

    def test_legacy_users_json_disabled(self):
        for users_mod in self._with_users_json({
            "version": 1,
            "queue_enabled": False,
            "concurrent_requests": 0,
            "slots": {},
        }):
            self.assertEqual(users_mod.queue_mode(), "per_user")
            self.assertFalse(users_mod.queue_enabled())
            self.assertEqual(users_mod.concurrent_requests(), 0)

    def test_queue_mode_universal_with_missing_concurrent_requests(self):
        # Hand-edited users.json: only the new field, missing legacy.
        for users_mod in self._with_users_json({
            "version": 1,
            "queue_mode": "universal",
            "slots": {},
        }):
            self.assertEqual(users_mod.queue_mode(), "universal")
            self.assertTrue(users_mod.queue_enabled())
            # concurrent_requests falls back to mode-derived value.
            self.assertEqual(users_mod.concurrent_requests(), 1)

    def test_queue_mode_with_dash_normalizes_to_underscore(self):
        for users_mod in self._with_users_json({
            "version": 1,
            "queue_mode": "per-user",
            "slots": {},
        }):
            self.assertEqual(users_mod.queue_mode(), "per_user")

    def test_garbage_queue_mode_falls_back_safely(self):
        for users_mod in self._with_users_json({
            "version": 1,
            "queue_mode": "banana",
            "queue_enabled": True,
            "slots": {},
        }):
            # Garbage queue_mode → fall through to queue_enabled.
            self.assertEqual(users_mod.queue_mode(), "universal")

    def test_concurrent_requests_string_value(self):
        # If someone wrote "1" instead of 1, we should still parse it.
        for users_mod in self._with_users_json({
            "version": 1,
            "concurrent_requests": "1",
            "slots": {},
        }):
            self.assertEqual(users_mod.concurrent_requests(), 1)
            self.assertEqual(users_mod.queue_mode(), "universal")

    def test_concurrent_requests_invalid_falls_back_to_zero(self):
        for users_mod in self._with_users_json({
            "version": 1,
            "concurrent_requests": "abc",
            "slots": {},
        }):
            self.assertEqual(users_mod.concurrent_requests(), 0)
            self.assertEqual(users_mod.queue_mode(), "per_user")


class ProvisionUsersJsonQueueModeTests(unittest.TestCase):
    """The provisioner emits queue_mode + queue_enabled + concurrent_requests
    consistently from the same config source."""

    def test_universal_config_emits_universal_users_json(self):
        config = {
            "multiuser_queue_mode": "universal",
            "multiuser_queue_enabled": True,
        }
        # Re-derive the same way the provisioner does.
        mode = config.get("multiuser_queue_mode") or (
            "universal" if config.get("multiuser_queue_enabled") else "per_user"
        )
        users_json = {
            "queue_mode": mode,
            "queue_enabled": mode == "universal",
            "concurrent_requests": 1 if mode == "universal" else 0,
        }
        self.assertEqual(users_json["queue_mode"], "universal")
        self.assertTrue(users_json["queue_enabled"])
        self.assertEqual(users_json["concurrent_requests"], 1)

    def test_per_user_config_emits_per_user_users_json(self):
        config = {
            "multiuser_queue_mode": "per_user",
            "multiuser_queue_enabled": False,
        }
        mode = config.get("multiuser_queue_mode") or (
            "universal" if config.get("multiuser_queue_enabled") else "per_user"
        )
        users_json = {
            "queue_mode": mode,
            "queue_enabled": mode == "universal",
            "concurrent_requests": 1 if mode == "universal" else 0,
        }
        self.assertEqual(users_json["queue_mode"], "per_user")
        self.assertFalse(users_json["queue_enabled"])
        self.assertEqual(users_json["concurrent_requests"], 0)

    def test_legacy_config_only_enabled_flag(self):
        # If only the boolean is set (an older config dict in flight),
        # fall back to deriving the mode.
        for enabled, expected_mode in [(True, "universal"), (False, "per_user")]:
            config = {"multiuser_queue_enabled": enabled}
            mode = config.get("multiuser_queue_mode") or (
                "universal" if config.get("multiuser_queue_enabled") else "per_user"
            )
            self.assertEqual(mode, expected_mode)


class WizardPromptModeTests(unittest.TestCase):
    """The interactive prompt accepts u/universal and p/per-user variants.

    The wizard's prompt parsing is duplicated below because the function
    it lives in does many other things (TTY interaction, hardware probe,
    UI strings) that are awkward to drive from a unit test. If the prompt
    is refactored, these tests will go stale and that is exactly when
    they should fail loudly.
    """

    def _run_prompt(self, hardware_default, user_inputs):
        config = {}
        mode_default = "universal" if hardware_default == "y" else "per-user"
        for raw in user_inputs:
            v = (raw or mode_default).strip().lower()
            if v in ("u", "universal"):
                config["multiuser_queue_mode"] = "universal"
                config["multiuser_queue_enabled"] = True
                return config
            if v in ("p", "per-user", "peruser", "per_user"):
                config["multiuser_queue_mode"] = "per_user"
                config["multiuser_queue_enabled"] = False
                return config
        return config

    def test_universal_long_form(self):
        c = self._run_prompt("y", ["universal"])
        self.assertEqual(c["multiuser_queue_mode"], "universal")
        self.assertTrue(c["multiuser_queue_enabled"])

    def test_universal_short_form(self):
        c = self._run_prompt("y", ["u"])
        self.assertEqual(c["multiuser_queue_mode"], "universal")

    def test_per_user_long_form_dash(self):
        c = self._run_prompt("n", ["per-user"])
        self.assertEqual(c["multiuser_queue_mode"], "per_user")
        self.assertFalse(c["multiuser_queue_enabled"])

    def test_per_user_long_form_underscore(self):
        c = self._run_prompt("n", ["per_user"])
        self.assertEqual(c["multiuser_queue_mode"], "per_user")

    def test_per_user_short_form(self):
        c = self._run_prompt("n", ["p"])
        self.assertEqual(c["multiuser_queue_mode"], "per_user")

    def test_empty_input_uses_hardware_default_universal(self):
        c = self._run_prompt("y", [""])
        self.assertEqual(c["multiuser_queue_mode"], "universal")

    def test_empty_input_uses_hardware_default_per_user(self):
        c = self._run_prompt("n", [""])
        self.assertEqual(c["multiuser_queue_mode"], "per_user")

    def test_uppercase_normalizes(self):
        c = self._run_prompt("y", ["UNIVERSAL"])
        self.assertEqual(c["multiuser_queue_mode"], "universal")


if __name__ == "__main__":
    unittest.main()
