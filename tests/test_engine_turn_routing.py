"""A picked engine has to reach the turn: the argv, the stream, the routing.

The picker is only worth having if the turn it describes is the turn that
runs. Four places that could quietly disagree with the button:

1. **The effort on the command line.** An engine instance must use its OWN
   level, not the install-wide LLM_EFFORT, and it must still be clamped: a
   level a model does not accept is silently ignored by both CLIs.

2. **The flag that carries the meter.** --include-partial-messages is what
   makes Claude Code report what is left of the subscription. It is probed,
   because an unknown flag on an older build fails every turn, and the
   partial messages it brings are skipped without being parsed.

3. **The turn's own counts.** They come off the CLI's result event, cache
   reads kept separate from fresh input, and the cost recorded as list price.

4. **Which provider object answers, and which one /stop reaches.** Once a
   user picks an engine, their subprocess belongs to that engine's provider
   and the install's own provider knows nothing about it.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

from core import engines, usage, users  # noqa: E402
from core.llm import (  # noqa: E402
    ClaudeCLIProvider,
    CodexCLIProvider,
    PARTIAL_MESSAGES_FLAG,
    _claude_usage_fields,
    _turn_effort,
)


class _SpawnRefused(RuntimeError):
    pass


class TurnEffortTests(unittest.TestCase):
    def test_no_override_reads_the_installs_own_setting(self):
        provider = ClaudeCLIProvider("claude-opus-5")
        with patch("core.config.get_llm_effort", return_value="high") as cfg:
            self.assertEqual(_turn_effort(provider), "high")
        cfg.assert_called_once()

    def test_an_override_wins_over_the_installs_setting(self):
        provider = ClaudeCLIProvider("claude-opus-5")
        provider.effort_override = "max"
        with patch("core.config.get_llm_effort", return_value="low") as cfg:
            self.assertEqual(_turn_effort(provider), "max")
        cfg.assert_not_called()

    def test_an_override_the_model_refuses_is_stepped_down(self):
        # ultra is Astra's sixth level; the claude binary warns on stderr and
        # runs at its default, which is a silent downgrade.
        provider = ClaudeCLIProvider("claude-opus-5")
        provider.effort_override = "ultra"
        self.assertEqual(_turn_effort(provider), "max")

    def test_an_override_on_a_model_with_no_known_levels_sends_nothing(self):
        provider = CodexCLIProvider("gpt-9-imaginary")
        provider.effort_override = "max"
        self.assertEqual(_turn_effort(provider), "")

    def test_astra_keeps_extra_high(self):
        provider = CodexCLIProvider("gpt-6-astra")
        provider.effort_override = "xhigh"
        self.assertEqual(_turn_effort(provider), "xhigh")


class ClaudeArgvTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.captured: list[str] = []

        async def fake_spawn(*cmd, **kwargs):
            self.captured.extend(cmd)
            raise _SpawnRefused("no real subprocess in tests")

        self._spawn = patch("asyncio.create_subprocess_exec", new=fake_spawn)
        self._spawn.start()
        self.addCleanup(self._spawn.stop)

    async def _run(self, provider):
        await provider.complete("sys", [], user_id=None)

    async def test_the_engine_effort_reaches_the_command_line(self):
        provider = ClaudeCLIProvider("claude-opus-5")
        provider.effort_override = "max"
        with patch("core.llm._claude_supports_partial_messages", return_value=True):
            await self._run(provider)
        self.assertEqual(self.captured[self.captured.index("--effort") + 1], "max")

    async def test_the_partial_messages_flag_is_added_when_supported(self):
        with patch("core.llm._claude_supports_partial_messages", return_value=True):
            await self._run(ClaudeCLIProvider("claude-opus-5"))
        self.assertIn(PARTIAL_MESSAGES_FLAG, self.captured)
        self.assertEqual(self.captured[-1], "-")

    async def test_an_older_cli_never_sees_the_flag(self):
        with patch("core.llm._claude_supports_partial_messages", return_value=False):
            await self._run(ClaudeCLIProvider("claude-opus-5"))
        self.assertNotIn(PARTIAL_MESSAGES_FLAG, self.captured)

    async def test_the_probe_reads_the_help_text(self):
        from core import llm
        llm._codex_probe_cache_clear()
        self.addCleanup(llm._codex_probe_cache_clear)

        class Result:
            returncode = 0
            stdout = f"  {PARTIAL_MESSAGES_FLAG}  Stream partial messages\n"
            stderr = ""

        with patch("core.llm.subprocess.run", return_value=Result()):
            self.assertTrue(llm._claude_supports_partial_messages("claude"))
        llm._codex_probe_cache_clear()

        class Older:
            returncode = 0
            stdout = "  --verbose  Be loud\n"
            stderr = ""

        with patch("core.llm.subprocess.run", return_value=Older()):
            self.assertFalse(llm._claude_supports_partial_messages("claude"))

    async def test_a_probe_that_cannot_run_answers_no(self):
        from core import llm
        llm._codex_probe_cache_clear()
        self.addCleanup(llm._codex_probe_cache_clear)
        with patch("core.llm.subprocess.run", side_effect=OSError("boom")):
            self.assertFalse(llm._claude_supports_partial_messages("claude"))


class _FakeStream:
    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""

    async def read(self):
        return b""


class _FakeStdin:
    def write(self, _data):
        return None

    async def drain(self):
        return None

    def close(self):
        return None

    async def wait_closed(self):
        return None


class _FakeProcess:
    def __init__(self, lines):
        self.stdout = _FakeStream(lines)
        self.stderr = _FakeStream([])
        self.stdin = _FakeStdin()
        self.returncode = 0

    async def wait(self):
        return 0


class ClaudeStreamTests(unittest.IsolatedAsyncioTestCase):
    """What the parse takes out of a real stream-json turn."""

    STREAM = [
        b'{"type":"system","subtype":"init","session_id":"s"}\n',
        b'{"type":"stream_event","event":{"type":"content_block_delta"}}\n',
        b'{"type":"rate_limit_event","rate_limit_info":{"status":"allowed",'
        b'"unifiedWindows":{"five_hour":{"utilization":0.05,"resetsAt":1789296000},'
        b'"seven_day":{"utilization":0.09,"resetsAt":1789570800}}}}\n',
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}\n',
        b'{"duration_ms":10,"type":"result","subtype":"success","is_error":false,'
        b'"result":"ok","total_cost_usd":0.0172,'
        b'"usage":{"input_tokens":10,"output_tokens":39,'
        b'"cache_read_input_tokens":13607,"cache_creation_input_tokens":7368}}\n',
    ]

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.tmp = Path(tempfile.mkdtemp(prefix="mom-stream-"))
        self._saved = usage.CLAUDE_LIMITS_FILE
        self._saved_dir = usage.USAGE_DIR
        usage.USAGE_DIR = self.tmp
        usage.CLAUDE_LIMITS_FILE = self.tmp / "claude_rate_limits.json"

        async def fake_spawn(*_cmd, **_kwargs):
            return _FakeProcess(list(self.STREAM))

        self._spawn = patch("asyncio.create_subprocess_exec", new=fake_spawn)
        self._spawn.start()
        self.addCleanup(self._spawn.stop)
        self._probe = patch("core.llm._claude_supports_partial_messages",
                            return_value=True)
        self._probe.start()
        self.addCleanup(self._probe.stop)

    def tearDown(self):
        usage.USAGE_DIR = self._saved_dir
        usage.CLAUDE_LIMITS_FILE = self._saved
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def _run(self):
        return await ClaudeCLIProvider("claude-opus-5").complete(
            "sys", [], user_id=None)

    async def test_the_answer_survives_the_partial_messages(self):
        response = await self._run()
        self.assertEqual(response.text, "ok")
        self.assertIsNone(response.error)

    async def test_the_turns_counts_come_back_on_the_response(self):
        response = await self._run()
        self.assertEqual(response.input_tokens, 10)
        self.assertEqual(response.output_tokens, 39)
        self.assertEqual(response.cache_read_tokens, 13607)
        self.assertEqual(response.cache_creation_tokens, 7368)
        self.assertAlmostEqual(response.list_cost_usd, 0.0172)

    async def test_the_subscription_reading_is_stored_once_the_turn_ends(self):
        await self._run()
        stored = json.loads(usage.CLAUDE_LIMITS_FILE.read_text())
        self.assertEqual(
            stored["info"]["unifiedWindows"]["five_hour"]["utilization"], 0.05)
        meter = usage.claude_meter()
        self.assertEqual(meter["windows"][0]["used_percent"], 5.0)

    async def test_a_turn_with_no_rate_limit_event_stores_nothing(self):
        self.STREAM = [ln for ln in self.STREAM if b"rate_limit_event" not in ln]
        await self._run()
        self.assertFalse(usage.CLAUDE_LIMITS_FILE.exists())


class UsageFieldTests(unittest.TestCase):
    def test_missing_counts_read_as_zero_not_as_an_error(self):
        self.assertEqual(
            _claude_usage_fields({}, 0.0),
            {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
             "cache_creation_tokens": 0, "list_cost_usd": 0.0})

    def test_a_junk_usage_block_is_not_fatal(self):
        self.assertEqual(_claude_usage_fields("nonsense", 0)["input_tokens"], 0)
        self.assertEqual(
            _claude_usage_fields({"input_tokens": "many"}, 0)["input_tokens"], 0)

    def test_cache_reads_stay_out_of_fresh_input(self):
        fields = _claude_usage_fields(
            {"input_tokens": 10, "cache_read_input_tokens": 13607}, 0)
        self.assertEqual(fields["input_tokens"], 10)
        self.assertEqual(fields["cache_read_tokens"], 13607)


class ProviderRoutingTests(unittest.TestCase):
    """bot.py: whose provider answers, and who gets asked to stop."""

    def setUp(self):
        login = patch("core.engines._login_status", return_value=(True, ""))
        login.start()
        self.addCleanup(login.stop)
        import bot as botmod
        self.bot = botmod
        self.tmp = Path(tempfile.mkdtemp(prefix="mom-routing-"))
        self._saved_users = users.USERS_DATA_DIR
        users.USERS_DATA_DIR = self.tmp
        self._saved_global = botmod._llm_provider
        self._saved_cache = dict(botmod._engine_providers)
        botmod._engine_providers.clear()
        botmod._llm_provider = MagicMock(name="install-provider")
        engines.probe_cache_clear()
        self._probe = patch("core.engines._cli_version_text",
                            return_value="codex-cli 0.154.0")
        self._probe.start()
        self.addCleanup(self._probe.stop)

    def tearDown(self):
        users.USERS_DATA_DIR = self._saved_users
        self.bot._llm_provider = self._saved_global
        self.bot._engine_providers.clear()
        self.bot._engine_providers.update(self._saved_cache)
        engines.probe_cache_clear()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_user_with_no_pick_gets_the_installs_provider(self):
        provider, engine = self.bot._provider_for_user(7)
        self.assertIs(provider, self.bot._llm_provider)
        self.assertIsNone(engine)

    def test_a_picked_engine_builds_its_own_provider_with_its_own_effort(self):
        engines.set_user_engine(7, "astra")
        built = MagicMock(name="astra-provider")
        with patch.object(self.bot, "_build_llm_provider",
                          return_value=built) as build:
            provider, engine = self.bot._provider_for_user(7)
        self.assertIs(provider, built)
        self.assertEqual(engine["id"], "astra")
        build.assert_called_once_with("codex", "gpt-6-astra", "",
                                      track_spec=False, effort="xhigh")

    def test_the_engine_provider_is_built_once_and_reused(self):
        engines.set_user_engine(7, "astra")
        with patch.object(self.bot, "_build_llm_provider",
                          return_value=MagicMock()) as build:
            first, _ = self.bot._provider_for_user(7)
            second, _ = self.bot._provider_for_user(7)
        self.assertIs(first, second)
        self.assertEqual(build.call_count, 1)

    def test_two_users_on_the_same_engine_share_one_provider(self):
        engines.set_user_engine(7, "astra")
        engines.set_user_engine(8, "astra")
        with patch.object(self.bot, "_build_llm_provider",
                          return_value=MagicMock()) as build:
            self.bot._provider_for_user(7)
            self.bot._provider_for_user(8)
        self.assertEqual(build.call_count, 1)

    def test_a_provider_that_will_not_build_falls_back_to_the_install(self):
        engines.set_user_engine(7, "astra")
        with patch.object(self.bot, "_build_llm_provider",
                          side_effect=RuntimeError("no codex here")):
            provider, engine = self.bot._provider_for_user(7)
        self.assertIs(provider, self.bot._llm_provider)
        self.assertIsNone(engine)

    def test_live_providers_covers_the_install_and_every_engine(self):
        engines.set_user_engine(7, "astra")
        built = MagicMock(name="astra-provider")
        with patch.object(self.bot, "_build_llm_provider", return_value=built):
            self.bot._provider_for_user(7)
        live = self.bot._live_providers()
        self.assertIn(self.bot._llm_provider, live)
        self.assertIn(built, live)
        self.assertEqual(len(live), 2)

    def test_a_turn_is_booked_against_the_engine_that_ran_it(self):
        provider = MagicMock()
        provider.provider_name = "codex"
        provider.model = "gpt-6-astra"
        provider.effort_override = "xhigh"
        response = MagicMock(input_tokens=100, output_tokens=20,
                             cache_read_tokens=5, cache_creation_tokens=1,
                             list_cost_usd=0.0, error=None, usage_reported=True, cost_reported=False, completed=True)
        self.bot._record_turn_usage(7, provider, {"id": "astra"}, response)
        summary = usage.summarise(7, 7)
        self.assertEqual(summary["turns"], 1)
        self.assertEqual(summary["failed_turns"], 0)
        rows = usage._read_rows(usage.ledger_path(7))
        self.assertEqual(rows[0]["engine"], "astra")
        self.assertEqual(rows[0]["effort"], "xhigh")

    def test_a_failed_turn_is_booked_as_failed(self):
        provider = MagicMock()
        provider.provider_name = "claude-cli"
        provider.model = "claude-opus-5"
        provider.effort_override = "max"
        response = MagicMock(input_tokens=0, output_tokens=0,
                             cache_read_tokens=0, cache_creation_tokens=0,
                             list_cost_usd=0.0, error="OOM killed", usage_reported=False, cost_reported=False, completed=False)
        self.bot._record_turn_usage(7, provider, None, response)
        self.assertEqual(usage.summarise(7, 7)["failed_turns"], 1)

    def test_status_names_the_engine_the_user_is_actually_on(self):
        engines.set_user_engine(7, "astra")
        line = self.bot._engine_status_line(7)
        self.assertIn("Astra", line)
        self.assertIn("gpt-6-astra", line)
        self.assertIn("xhigh", line)

    def test_status_falls_back_to_the_install_pair_for_everybody_else(self):
        with patch("bot.get_llm_provider", return_value="ollama"), \
                patch("bot.get_llm_model", return_value="llama3.1:8b"):
            line = self.bot._engine_status_line(7)
        self.assertEqual(line, "Provider: ollama / llama3.1:8b")

    def test_recording_never_takes_the_turn_down_with_it(self):
        with patch("core.usage.record_turn", side_effect=OSError("disk full")):
            self.bot._record_turn_usage(7, MagicMock(), None, MagicMock())


if __name__ == "__main__":
    unittest.main()
