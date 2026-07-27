#!/usr/bin/env python3
"""Live .env reload: edits apply on the next turn, no restart.

Run: python3 -m unittest tests.test_env_hot_reload  (from repo root)

bot.py loads .env into os.environ exactly once at boot. Before this fix, any
later edit of the file was invisible to the running process: the Mini App
writes LLM_MODEL/LLM_PROVIDER/LLM_EFFORT from a separate uvicorn process (its
restart-hint told the user to /restart), and a direct edit of the file (ssh,
an editor, a wizard re-run) never landed at all -- the bot kept spending on
the old model while .env said otherwise. Now core.config re-reads .env when
its mtime changes, and call_llm() rebuilds the provider object when the
(provider, model, api_key) spec drifts from the object in memory.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep test logging out of the production bot.log

import core.config as config  # noqa: E402


class EnvReloadBase(unittest.TestCase):
    """Points core.config at a scratch .env and restores everything after."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env_file = Path(self._tmp.name) / ".env"
        self._saved_state = (
            config.ENV_FILE,
            config._env_file_mtime,
            dict(config._env_file_values),
        )
        self._saved_environ = os.environ.copy()
        config.ENV_FILE = self.env_file
        config._env_file_mtime = None
        config._env_file_values = {}
        self._mtime = 1_000_000_000  # deterministic, strictly increasing

    def tearDown(self):
        config.ENV_FILE, config._env_file_mtime, values = self._saved_state
        config._env_file_values = values
        os.environ.clear()
        os.environ.update(self._saved_environ)
        self._tmp.cleanup()

    def write_env(self, text: str):
        """Write the scratch .env with a strictly increasing mtime so the
        stat-based change check always trips, regardless of filesystem
        timestamp granularity."""
        self.env_file.write_text(text, encoding="utf-8")
        self._mtime += 1
        os.utime(self.env_file, (self._mtime, self._mtime))


class TestEnvFileHotReload(EnvReloadBase):
    def test_first_sight_is_baseline_only(self):
        """The first parse must never apply values: boot-time load_dotenv
        already decided precedence (pre-set process env wins over the file)."""
        os.environ.pop("LLM_MODEL", None)
        self.write_env("LLM_MODEL=from-file\n")
        self.assertEqual(config.get_llm_model(), "claude-sonnet-5")

    def test_edit_applies_without_restart(self):
        os.environ.pop("LLM_MODEL", None)
        self.write_env("LLM_MODEL=alpha\n")
        config.get_llm_model()  # baseline
        self.write_env("LLM_MODEL=beta\n")
        self.assertEqual(config.get_llm_model(), "beta")

    def test_new_key_in_file_applies(self):
        os.environ.pop("LLM_EFFORT", None)
        self.write_env("LLM_MODEL=alpha\n")
        config.get_llm_model()  # baseline
        self.write_env("LLM_MODEL=alpha\nLLM_EFFORT=low\n")
        self.assertEqual(config.get_llm_effort(), "low")

    def test_unchanged_key_keeps_process_env_shadow(self):
        """A key set in the process environment (systemd Environment=, a
        test) keeps shadowing an *unchanged* file entry, exactly like the
        boot-time load_dotenv(override=False) semantics."""
        self.write_env("LLM_MODEL=file-value\nOTHER_KEY=1\n")
        config.get_llm_model()  # baseline
        os.environ["LLM_MODEL"] = "process-shadow"
        self.write_env("LLM_MODEL=file-value\nOTHER_KEY=2\n")
        self.assertEqual(config.get_llm_model(), "process-shadow")

    def test_edited_key_wins_over_process_env(self):
        """Editing a key's file value is a deliberate act and wins."""
        self.write_env("LLM_MODEL=file-value\n")
        config.get_llm_model()  # baseline
        os.environ["LLM_MODEL"] = "process-shadow"
        self.write_env("LLM_MODEL=file-edited\n")
        self.assertEqual(config.get_llm_model(), "file-edited")

    def test_removed_key_falls_back_to_default(self):
        os.environ.pop("LLM_EFFORT", None)
        self.write_env("LLM_MODEL=alpha\n")
        config.get_llm_model()  # baseline
        self.write_env("LLM_MODEL=alpha\nLLM_EFFORT=low\n")
        self.assertEqual(config.get_llm_effort(), "low")
        self.write_env("LLM_MODEL=alpha\n")
        self.assertEqual(config.get_llm_effort(), "max")

    def test_removed_key_never_clobbers_foreign_env_value(self):
        """Removal only unsets os.environ when the live value is the one the
        file put there; an in-process override survives."""
        self.write_env("LLM_MODEL=alpha\nLLM_EFFORT=low\n")
        config.get_llm_model()  # baseline (nothing applied)
        os.environ["LLM_EFFORT"] = "high"
        self.write_env("LLM_MODEL=alpha\n")
        self.assertEqual(config.get_llm_effort(), "high")

    def test_missing_file_is_harmless(self):
        os.environ.pop("LLM_MODEL", None)
        self.assertEqual(config.get_llm_model(), "claude-sonnet-5")

    def test_int_getter_reloads_too(self):
        os.environ.pop("WEBHOOK_PORT", None)
        self.write_env("WEBHOOK_PORT=1111\n")
        config.get_webhook_port()  # baseline
        self.write_env("WEBHOOK_PORT=2222\n")
        self.assertEqual(config.get_webhook_port(), 2222)

    def test_list_getter_reloads_too(self):
        os.environ.pop("ALLOWED_USERS", None)
        self.write_env("WEBHOOK_PORT=1\n")
        config._env_list("ALLOWED_USERS")  # baseline
        self.write_env("WEBHOOK_PORT=1\nALLOWED_USERS=7,8\n")
        self.assertEqual(config._env_list("ALLOWED_USERS"), [7, 8])


class TestProviderRebuildOnEnvChange(unittest.TestCase):
    """call_llm() rebuilds the provider object when the .env spec drifts.

    The provider objects bake self.model in at construction (the Claude CLI
    spawn passes --model self.model), so reloading os.environ alone is not
    enough -- the object must be rebuilt when the spec changes.
    """

    @classmethod
    def setUpClass(cls):
        import bot as botmod
        cls.bot = botmod

    def setUp(self):
        self._saved = (self.bot._llm_provider, self.bot._llm_provider_spec)
        self._saved_environ = os.environ.copy()
        os.environ.update({
            "LLM_PROVIDER": "claude",
            "LLM_MODEL": "model-old",
            "LLM_API_KEY": "",
        })
        self.old_provider = object()
        self.bot._llm_provider = self.old_provider
        self.bot._llm_provider_spec = ("claude", "model-old", "")

    def tearDown(self):
        self.bot._llm_provider, self.bot._llm_provider_spec = self._saved
        os.environ.clear()
        os.environ.update(self._saved_environ)

    def test_no_drift_no_rebuild(self):
        with patch.object(self.bot, "create_provider") as cp:
            self.bot._refresh_provider_if_env_changed()
            cp.assert_not_called()
        self.assertIs(self.bot._llm_provider, self.old_provider)

    def test_model_drift_rebuilds_provider(self):
        os.environ["LLM_MODEL"] = "model-new"
        fake = object()
        with patch.object(self.bot, "create_provider", return_value=fake) as cp:
            self.bot._refresh_provider_if_env_changed()
            cp.assert_called_once_with("claude", "model-new", "")
        self.assertIs(self.bot._llm_provider, fake)
        self.assertEqual(
            self.bot._llm_provider_spec, ("claude", "model-new", "")
        )

    def test_provider_drift_rebuilds_provider(self):
        os.environ["LLM_PROVIDER"] = "ollama"
        os.environ["LLM_MODEL"] = "llama3.1:8b"
        fake = object()
        with patch.object(self.bot, "create_provider", return_value=fake) as cp:
            self.bot._refresh_provider_if_env_changed()
            cp.assert_called_once()
            args, kwargs = cp.call_args
            self.assertEqual(args, ("ollama", "llama3.1:8b", ""))
            self.assertIn("base_url", kwargs)  # ollama keeps its base_url
        self.assertIs(self.bot._llm_provider, fake)

    def test_failed_rebuild_keeps_old_provider_and_retries(self):
        """A bad edit (unknown provider, typo) must not kill the bot: the
        old provider keeps serving, and the unchanged spec means the next
        turn retries the rebuild."""
        os.environ["LLM_MODEL"] = "model-new"
        with patch.object(
            self.bot, "create_provider", side_effect=ValueError("boom")
        ) as cp:
            self.bot._refresh_provider_if_env_changed()
            self.assertIs(self.bot._llm_provider, self.old_provider)
            self.assertEqual(
                self.bot._llm_provider_spec, ("claude", "model-old", "")
            )
            self.bot._refresh_provider_if_env_changed()
            self.assertEqual(cp.call_count, 2)

    def test_pre_startup_is_noop(self):
        self.bot._llm_provider = None
        with patch.object(self.bot, "create_provider") as cp:
            self.bot._refresh_provider_if_env_changed()
            cp.assert_not_called()

    def test_injected_provider_without_spec_is_left_alone(self):
        """A provider wired directly (tests patch.object the global, bespoke
        setups may too) without going through _build_llm_provider must not
        be swapped out from under its owner."""
        self.bot._llm_provider_spec = None
        os.environ["LLM_MODEL"] = "model-new"
        with patch.object(self.bot, "create_provider") as cp:
            self.bot._refresh_provider_if_env_changed()
            cp.assert_not_called()
        self.assertIs(self.bot._llm_provider, self.old_provider)


class TestSourcePins(unittest.TestCase):
    """Source-level pins, same style as test_background_model: the
    invariants that make the hot-reload complete stay enforced."""

    @classmethod
    def setUpClass(cls):
        cls.bot_src = (ROOT / "bot.py").read_text(encoding="utf-8")
        cls.config_src = (ROOT / "core" / "config.py").read_text(encoding="utf-8")

    def test_all_provider_creation_goes_through_builder(self):
        """create_provider() must have exactly one call site in bot.py
        (inside _build_llm_provider), so no path can build a provider
        without recording the spec that drift detection compares against."""
        self.assertEqual(self.bot_src.count("create_provider("), 1)

    def test_call_llm_refreshes_before_health_gate(self):
        m = re.search(
            r"async def call_llm\(.*?(?=\nasync def |\ndef )",
            self.bot_src,
            re.S,
        )
        self.assertIsNotNone(m, "call_llm not found in bot.py")
        body = m.group(0)
        refresh = body.find("_refresh_provider_if_env_changed()")
        health = body.find("last_health")
        self.assertNotEqual(refresh, -1, "call_llm does not refresh the provider")
        self.assertNotEqual(health, -1, "health gate not found in call_llm")
        self.assertLess(refresh, health, "refresh must run before the health gate")

    def test_config_getters_stat_the_file(self):
        for fn in ("def _env(", "def _env_int(", "def _env_list("):
            i = self.config_src.find(fn)
            self.assertNotEqual(i, -1, f"{fn} not found in core/config.py")
            body = self.config_src[i:i + 300]
            self.assertIn(
                "_reload_env_if_changed()", body,
                f"{fn} does not trigger the .env reload",
            )


if __name__ == "__main__":
    unittest.main()
