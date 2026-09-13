"""Regression checks across machine selection, storage and provider routing."""
import asyncio
import os
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["MOM_TEST"] = "1"

import bot
from core import engines, llm, users
import miniapp.server as server


class MachineEngineReviewTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.env = self.root / ".env"
        self.env.write_text("LLM_PROVIDER=codex\nLLM_MODEL=gpt-6-astra\n"
                            "LLM_EFFORT=max\nLLM_API_KEY=test-key\nOTHER=keep\n")
        self.stack.enter_context(patch.object(server, "ENV_FILE", self.env))
        self.stack.enter_context(patch.object(users, "USERS_DATA_DIR", self.root / "users"))
        self.stack.enter_context(patch("core.engines._probe", return_value=(True, "")))
        self.stack.enter_context(patch("miniapp.server._bot_status", return_value={"active": True}))
        self.stack.enter_context(patch.dict(os.environ, {
            "LLM_PROVIDER": "codex", "LLM_MODEL": "gpt-6-astra", "LLM_API_KEY": "test-key"}))
        engines.probe_cache_clear()
        self.addCleanup(engines.probe_cache_clear)

    def test_machine_claude_selection_builds_subscription_provider_with_api_key(self):
        payload = server._set_machine_engine("sonnet")
        provider = llm.create_provider(payload["provider"], payload["model"], "test-key")
        self.assertIsInstance(provider, llm.ClaudeCLIProvider)

    def test_api_and_cli_rows_for_same_model_have_distinct_ids(self):
        self.env.write_text("LLM_PROVIDER=claude-api\nLLM_MODEL=claude-opus-5\n")
        payload = server._machine_engine_payload()
        ids = [r["id"] for r in payload["engines"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotEqual(payload["effective"], "claude-opus-5")

    def test_legacy_claude_with_key_is_displayed_as_api_not_subscription(self):
        self.env.write_text("LLM_PROVIDER=claude\nLLM_MODEL=claude-opus-5\nLLM_API_KEY=test-key\n")
        rows = server._machine_engine_payload()["engines"]
        current = next(row for row in rows if row["current"])
        self.assertIsInstance(llm.create_provider(current["provider"], current["model"], "test-key"),
                              llm.ClaudeAPIProvider)
        self.assertFalse(next(row for row in rows if row["id"] == "claude-opus-5")["current"])

    def test_selecting_current_api_row_is_a_noop(self):
        self.env.write_text("LLM_PROVIDER=gemini\nLLM_MODEL=gemini-custom\n")
        current = server._machine_engine_payload()["effective"]
        before = self.env.read_bytes()
        with patch("miniapp.server.atomic_env_write") as write:
            result = server._set_machine_engine(current)
        write.assert_not_called()
        self.assertEqual(result["effective"], current)
        self.assertEqual(self.env.read_bytes(), before)

    def test_every_published_file_contains_the_complete_new_pair(self):
        published = []
        original = server.atomic_env_write

        def publish(path, content):
            original(path, content)
            published.append((server._read_env_var("LLM_PROVIDER"),
                              server._read_env_var("LLM_MODEL")))

        with patch("miniapp.server.atomic_env_write", side_effect=publish):
            server._set_machine_engine("sonnet")
        self.assertTrue(published)
        self.assertTrue(all(pair == ("claude-cli", "claude-sonnet-5") for pair in published), published)
        self.assertEqual(server._read_env_var("LLM_EFFORT"), "max")
        self.assertEqual(server._read_env_var("OTHER"), "keep")
        self.assertEqual(self.env.stat().st_mode & 0o777, 0o600)

    def test_failed_file_publish_preserves_original_settings(self):
        before = self.env.read_bytes()
        with patch("miniapp.server.atomic_env_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                server._set_machine_engine("sonnet")
        self.assertEqual(self.env.read_bytes(), before)

    def test_failed_provider_construction_does_not_save_new_settings(self):
        update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
        with (patch("bot._build_llm_provider", side_effect=RuntimeError("cannot build")),
              patch("bot._write_machine_llm") as write):
            asyncio.run(bot._admin_engine_switch(update, 7, "sonnet"))
        write.assert_not_called()
        self.assertIn("Failed", update.message.reply_text.call_args.args[0])

    def test_subscription_selection_has_provider_and_model_controls(self):
        payload = server._set_machine_engine("sonnet")
        status = asyncio.run(server.get_status(user={"_profile": {"role": "admin"}}))
        self.assertIn(payload["provider"], {r["id"] for r in status["available_providers"]})
        self.assertIn("claude-sonnet-5", {r["id"] for r in status["available_models"]})

    def test_machine_note_does_not_claim_to_override_ordinary_opus_default(self):
        with patch("core.config.is_admin", return_value=False):
            actual = engines.resolve_engine(7, default_provider="codex")
        self.assertEqual(actual["id"], "opus")
        self.assertNotIn("applies to every user who has not picked", engines.MACHINE_PICKER_NOTE)

    def test_failed_read_never_replaces_existing_configuration(self):
        before = self.env.read_bytes()
        with (patch.object(Path, "read_text", side_effect=PermissionError("unreadable")),
              patch("miniapp.server.atomic_env_write") as write):
            with self.assertRaises(PermissionError):
                server._write_env_var("LLM_MODEL", "gpt-5.5")
        write.assert_not_called()
        self.assertEqual(self.env.read_bytes(), before)

    def test_invalid_second_value_does_not_publish_first_value(self):
        before = self.env.read_bytes()
        with self.assertRaises(ValueError):
            server._write_env_vars({"LLM_PROVIDER": "codex", "LLM_MODEL": "bad\nOTHER=lost"})
        self.assertEqual(self.env.read_bytes(), before)

    def test_concurrent_panel_writes_preserve_pair_and_effort(self):
        entered = threading.Event()
        release = threading.Event()
        second_started = threading.Event()
        writes = []
        errors = []
        original = server.atomic_env_write

        def publish(path, content):
            writes.append(content)
            if len(writes) == 1:
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test release missing")
            original(path, content)

        def switch():
            try:
                server._write_env_vars({"LLM_PROVIDER": "claude-cli", "LLM_MODEL": "claude-sonnet-5"})
            except Exception as exc:
                errors.append(exc)

        def effort():
            second_started.set()
            try:
                server._write_env_var("LLM_EFFORT", "high")
            except Exception as exc:
                errors.append(exc)

        with patch("miniapp.server.atomic_env_write", side_effect=publish):
            first = threading.Thread(target=switch)
            second = threading.Thread(target=effort)
            first.start()
            try:
                self.assertTrue(entered.wait(2))
                second.start()
                self.assertTrue(second_started.wait(2))
                time.sleep(0.03)
            finally:
                release.set()
                first.join(2)
                if second.ident:
                    second.join(2)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(server._current_pair(), ("claude-cli", "claude-sonnet-5"))
        self.assertEqual(server._read_env_var("LLM_EFFORT"), "high")

    def test_failed_save_keeps_running_provider_and_spec(self):
        old = object()
        spec = ("codex", "gpt-6-astra", "test-key")
        update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
        with (patch.object(bot, "_llm_provider", old),
              patch.object(bot, "_llm_provider_spec", spec),
              patch("bot._build_llm_provider", return_value=object()) as build,
              patch("bot._write_machine_llm", side_effect=OSError("disk full"))):
            asyncio.run(bot._admin_engine_switch(update, 7, "sonnet"))
            self.assertIs(bot._llm_provider, old)
            self.assertEqual(bot._llm_provider_spec, spec)
        self.assertFalse(build.call_args.kwargs["track_spec"])
        self.assertIn("Could not save", update.message.reply_text.call_args.args[0])

    def test_pair_read_never_combines_two_file_versions(self):
        old = "LLM_PROVIDER=codex\nLLM_MODEL=gpt-6-astra\n"
        new = "LLM_PROVIDER=claude-cli\nLLM_MODEL=claude-sonnet-5\n"
        with patch.object(Path, "read_text", side_effect=[old, new]):
            pair = server._current_pair()
        self.assertIn(pair, [("codex", "gpt-6-astra"), ("claude-cli", "claude-sonnet-5")])
