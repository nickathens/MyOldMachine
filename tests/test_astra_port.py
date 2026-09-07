"""GPT-6 Astra on the Codex CLI provider: effort levels, and the traps.

Four things are locked here, each because it can fail silently rather than
loudly:

1. **The effort set is per model.** ``gpt-6-astra`` takes six levels including
   ``ultra``; ``gpt-5.5``, this repo's Codex default, takes only four and has
   no ``max`` at all. This repo's default effort has always been ``max``, so a
   single shared list would have started sending every default Codex install a
   level its own model does not accept.

2. **A level the model does not take is stepped down, never passed through.**
   ``claude --effort ultra`` is not an error: it warns on stderr and runs the
   turn at the CLI's default. An ``ultra`` left in .env by an Astra session
   would therefore downgrade every later Claude turn with nothing on screen.

3. **An unknown ``--disable`` name is a hard abort.** "Error: Unknown feature
   flag: <name>", no events, no turn, on every request. So the feature names
   are probed against the installed build, never assumed.

4. **A Codex build too old for the model fails every turn** with a message
   about ChatGPT accounts that reads like an account problem. Astra needs
   0.153.1 (openai/codex releases, read 2026-09-07), and the health check now
   says so instead.
"""
from __future__ import annotations

import ast
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep test logging out of the production bot.log

from core import config, model_efforts as me  # noqa: E402
from core.llm import (  # noqa: E402
    ClaudeCLIProvider,
    CodexCLIProvider,
    _codex_model_needs_newer_cli,
)
from core.model_efforts import parse_cli_version as _parse_codex_version  # noqa: E402
from install import wizard  # noqa: E402

ASTRA = "gpt-6-astra"


class EffortTableTests(unittest.TestCase):
    """The table itself, against OpenAI's and Anthropic's published sets."""

    def test_astra_carries_six_levels_including_ultra(self):
        self.assertEqual(
            me.efforts_for("codex", ASTRA),
            ("low", "medium", "high", "xhigh", "max", "ultra"),
        )

    def test_claude_has_five_and_no_ultra(self):
        levels = me.efforts_for("claude", "claude-sonnet-5")
        self.assertEqual(levels, ("low", "medium", "high", "xhigh", "max"))
        self.assertNotIn("ultra", levels)

    def test_gpt_5_5_has_no_max(self):
        # The concrete reason the table is per model: this repo's default
        # Codex model does not accept the default effort this repo sends.
        self.assertNotIn("max", me.efforts_for("codex", "gpt-5.5"))
        self.assertEqual(me.efforts_for("codex", "gpt-5.5"),
                         ("low", "medium", "high", "xhigh"))

    def test_every_level_has_a_label(self):
        # effort_options indexes EFFORT_LABELS; a missing one is a KeyError
        # in the picker's own endpoint.
        for level in me.EFFORT_ORDER:
            self.assertIn(level, me.EFFORT_LABELS)

    def test_effort_order_is_a_superset_of_every_set(self):
        for provider, model in (("claude", "claude-sonnet-5"),
                                ("codex", ASTRA), ("codex", "gpt-5.5")):
            with self.subTest(model=model):
                for level in me.efforts_for(provider, model):
                    self.assertIn(level, me.EFFORT_ORDER)

    def test_effort_order_has_no_duplicates(self):
        # clamp_effort walks it by index; a repeat would make the walk wrong.
        self.assertEqual(len(me.EFFORT_ORDER), len(set(me.EFFORT_ORDER)))

    def test_a_provider_with_no_knob_still_answers_the_old_way(self):
        """Regression, caught by the existing hot-reload suite.

        get_llm_effort used to be provider-blind. Making the empty answer
        apply to every unrecognised provider changed what it returns for an
        API provider that has LLM_EFFORT set — from the stored level to "".
        Nothing consumes it there, but the contract is public and three
        existing tests depend on it. Only a CODEX model whose levels are
        unknown gets the empty answer.
        """
        self.assertEqual(me.efforts_for("openai", "gpt-5.6"), me.CLAUDE_EFFORTS)
        self.assertEqual(me.clamp_effort("openai", "gpt-5.6", "high"), "high")
        self.assertEqual(me.clamp_effort("openai", "anything", None), "max")
        # ... and it is still not offered a row.
        self.assertFalse(me.supports_effort("openai", "gpt-5.6"))

    def test_unknown_codex_model_offers_nothing(self):
        # Not a guess: a Codex model whose levels have not been read gets an
        # empty set, and the caller sends no override at all.
        self.assertEqual(me.efforts_for("codex", "gpt-9-imaginary"), ())
        self.assertFalse(me.supports_effort("codex", "gpt-9-imaginary"))

    def test_api_providers_have_no_effort_row(self):
        for provider in ("openai", "gemini", "ollama", "openrouter", "deepseek"):
            with self.subTest(provider=provider):
                self.assertFalse(me.provider_supports_effort(provider))

    def test_cli_providers_and_their_aliases_have_one(self):
        for provider in ("claude", "claude-cli", "fcc", "codex", "codex-cli"):
            with self.subTest(provider=provider):
                self.assertTrue(me.provider_supports_effort(provider))


class ClampTests(unittest.TestCase):
    def test_ultra_steps_down_to_max_on_claude(self):
        self.assertEqual(
            me.clamp_effort("claude", "claude-sonnet-5", "ultra"), "max")

    def test_ultra_survives_on_astra(self):
        self.assertEqual(me.clamp_effort("codex", ASTRA, "ultra"), "ultra")

    def test_max_steps_down_to_xhigh_on_gpt_5_5(self):
        self.assertEqual(me.clamp_effort("codex", "gpt-5.5", "max"), "xhigh")

    def test_junk_falls_back_to_the_models_own_default(self):
        # Not to a shared "max": Astra's published default is medium.
        self.assertEqual(me.clamp_effort("codex", ASTRA, "nonsense"), "medium")
        self.assertEqual(me.clamp_effort("codex", "gpt-5.5", ""), "medium")
        self.assertEqual(
            me.clamp_effort("claude", "claude-sonnet-5", None), "max")

    def test_unknown_codex_model_clamps_to_nothing(self):
        self.assertEqual(me.clamp_effort("codex", "gpt-9-imaginary", "max"), "")

    def test_a_clamp_is_always_an_offered_level(self):
        # The property that matters: whatever comes back is on the row the
        # picker rendered, for every model in the table and every input the
        # picker could ever hold. Enumerated from the table rather than
        # hand-listed, so a model added later is covered without an edit.
        pairs = [("claude", "claude-sonnet-5"), ("claude", "claude-opus-5")]
        pairs += [("codex", m) for m in me._MODEL_EFFORTS]
        inputs = list(me.EFFORT_ORDER) + ["", None, "NONSENSE", "MAX"]
        for provider, model in pairs:
            allowed = me.efforts_for(provider, model)
            self.assertTrue(allowed, model)
            for value in inputs:
                with self.subTest(model=model, value=value):
                    self.assertIn(me.clamp_effort(provider, model, value),
                                  allowed)

    def test_every_model_default_is_one_of_its_own_levels(self):
        # The two tables are separate dicts. A model added to one and not the
        # other, or a typo, would make the fallback a level the model refuses
        # — and the fallback is exactly the path a junk value takes.
        self.assertEqual(set(me._MODEL_EFFORTS), set(me._MODEL_DEFAULT_EFFORT))
        for model, levels in me._MODEL_EFFORTS.items():
            with self.subTest(model=model):
                self.assertIn(me.default_effort_for("codex", model), levels)
        self.assertIn(me.default_effort_for("claude", "claude-sonnet-5"),
                      me.CLAUDE_EFFORTS)


class ConfigResolutionTests(unittest.TestCase):
    """get_llm_effort resolves the pair, then clamps."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mom-astra-env-"))
        self._saved_file = config.ENV_FILE
        # A path that does not exist: the reloader treats that as "nothing to
        # watch", so os.environ below is the only input.
        config.ENV_FILE = self.tmp / "no-such.env"
        self._saved = {k: os.environ.get(k)
                       for k in ("LLM_EFFORT", "LLM_PROVIDER", "LLM_MODEL")}

    def tearDown(self):
        config.ENV_FILE = self._saved_file
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _env(self, **kwargs):
        for key, value in kwargs.items():
            os.environ[key] = value

    def test_ultra_on_a_claude_turn_becomes_max(self):
        self._env(LLM_EFFORT="ultra", LLM_PROVIDER="claude",
                  LLM_MODEL="claude-sonnet-5")
        self.assertEqual(config.get_llm_effort(), "max")

    def test_ultra_on_an_astra_turn_survives(self):
        self._env(LLM_EFFORT="ultra", LLM_PROVIDER="codex", LLM_MODEL=ASTRA)
        self.assertEqual(config.get_llm_effort(), "ultra")

    def test_explicit_pair_beats_the_configured_one(self):
        # A provider instance constructed with an override must be able to ask
        # about ITS model, not whatever .env has moved on to.
        self._env(LLM_EFFORT="ultra", LLM_PROVIDER="codex", LLM_MODEL=ASTRA)
        self.assertEqual(
            config.get_llm_effort("claude", "claude-sonnet-5"), "max")

    def test_unset_effort_uses_the_models_default_not_max(self):
        os.environ.pop("LLM_EFFORT", None)
        self._env(LLM_PROVIDER="codex", LLM_MODEL=ASTRA)
        self.assertEqual(config.get_llm_effort(), "medium")

    def test_claude_default_is_unchanged(self):
        # The historical behaviour this repo has always had.
        os.environ.pop("LLM_EFFORT", None)
        self._env(LLM_PROVIDER="claude", LLM_MODEL="claude-sonnet-5")
        self.assertEqual(config.get_llm_effort(), "max")

    def test_an_api_provider_keeps_its_stored_level(self):
        # The exact pollution that failed test_env_hot_reload: a leftover
        # LLM_PROVIDER=openai in a developer's .env reaches os.environ, and
        # an empty answer here breaks a contract three older tests hold.
        self._env(LLM_EFFORT="high", LLM_PROVIDER="openai", LLM_MODEL="alpha")
        self.assertEqual(config.get_llm_effort(), "high")

    def test_uppercase_and_padding_are_accepted(self):
        self._env(LLM_EFFORT="  ULTRA  ", LLM_PROVIDER="codex", LLM_MODEL=ASTRA)
        self.assertEqual(config.get_llm_effort(), "ultra")


class _SpawnRefused(RuntimeError):
    """Raised in place of a real subprocess once argv has been captured."""


class CodexArgvTests(unittest.IsolatedAsyncioTestCase):
    """What actually reaches the command line."""

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.captured: list[str] = []

        self.kwargs: dict = {}

        async def fake_spawn(*cmd, **kwargs):
            self.captured.extend(cmd)
            self.kwargs.update(kwargs)
            raise _SpawnRefused("no real subprocess in tests")

        self._spawn = patch("asyncio.create_subprocess_exec", new=fake_spawn)
        self._spawn.start()
        self.addCleanup(self._spawn.stop)
        # The probe shells out to the installed codex, which CI does not have.
        # Patched so these tests measure the wiring, not the machine.
        self._features = patch("core.llm._codex_feature_names",
                               return_value=frozenset({"multi_agent",
                                                       "multi_agent_v2"}))
        self._features.start()
        self.addCleanup(self._features.stop)
        self._effort = patch("core.config.get_llm_effort", return_value="ultra")
        self._effort.start()
        self.addCleanup(self._effort.stop)

    async def _run(self, model=ASTRA):
        await CodexCLIProvider(model).complete("sys", [], user_id=None)

    async def test_the_effort_override_is_on_the_command_line(self):
        await self._run()
        self.assertIn("-c", self.captured)
        self.assertIn('model_reasoning_effort="ultra"', self.captured)

    async def test_the_key_is_effort_not_level(self):
        # `--strict-config` rejects model_reasoning_level by name and accepts
        # model_reasoning_effort. Getting this wrong is silent: an unknown
        # key parses fine and the turn runs at the model's default.
        await self._run()
        self.assertFalse([a for a in self.captured
                          if "model_reasoning_level" in a])

    async def test_no_override_when_the_model_has_no_known_levels(self):
        self._effort.stop()
        self.addCleanup(self._features.stop)  # idempotent
        with patch("core.config.get_llm_effort", return_value=""):
            await self._run("gpt-9-imaginary")
        self.assertNotIn("-c", self.captured)
        self.assertFalse([a for a in self.captured
                          if "model_reasoning_effort" in a])

    async def test_delegation_features_are_disabled(self):
        await self._run()
        self.assertEqual(self.captured.count("--disable"), 2)
        self.assertIn("multi_agent", self.captured)
        self.assertIn("multi_agent_v2", self.captured)

    async def test_a_name_this_build_does_not_know_is_never_passed(self):
        # The hard-abort guard: only names the installed build lists are sent.
        self._features.stop()
        with patch("core.llm._codex_feature_names",
                   return_value=frozenset({"multi_agent"})):
            await self._run()
        self.assertEqual(self.captured.count("--disable"), 1)
        self.assertIn("multi_agent", self.captured)
        self.assertNotIn("multi_agent_v2", self.captured)

    async def test_an_unreadable_probe_disables_nothing(self):
        # An older build with no `features` subcommand must still run turns.
        self._features.stop()
        with patch("core.llm._codex_feature_names", return_value=frozenset()):
            await self._run()
        self.assertNotIn("--disable", self.captured)

    async def test_stdin_marker_stays_last(self):
        # Everything is inserted before it; a flag after `-` is read as the
        # prompt source.
        await self._run()
        self.assertEqual(self.captured[-1], "-")

    async def test_the_model_still_reaches_the_command_line(self):
        await self._run()
        self.assertIn("-m", self.captured)
        self.assertIn(ASTRA, self.captured)

    async def test_the_flag_and_its_value_are_adjacent_and_ordered(self):
        await self._run()
        i = self.captured.index("-c")
        self.assertEqual(self.captured[i + 1], 'model_reasoning_effort="ultra"')

    async def test_no_anthropic_variable_reaches_the_codex_child(self):
        """core.tools' filter is a DENY list, not an allow list.

        Only secret-SHAPED names are removed: CLAUDE_CODE_OAUTH_TOKEN matches
        `.*_TOKEN$` and goes, CLAUDE_CONFIG_DIR and
        CLAUDE_CODE_MESSAGING_SOCKET do not and stay. A bot started from
        inside a Claude Code session would hand a second vendor's CLI the
        messaging socket of a live session.
        """
        planted = {
            "CLAUDE_CONFIG_DIR": "/tmp/leak",
            "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/leak.sock",
            "CLAUDECODE": "1",
            "CLAUDE_CODE_SESSION_ID": "abc",
            "ANTHROPIC_BASE_URL": "https://leak.example",
        }
        saved = {k: os.environ.get(k) for k in planted}
        os.environ.update(planted)
        try:
            await self._run()
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        env = self.kwargs.get("env") or {}
        # Positive control: an empty env would make the assertion below pass
        # for the wrong reason.
        self.assertIn("HOME", env)
        leaked = sorted(k for k in env
                        if k.startswith(("CLAUDE", "ANTHROPIC")))
        self.assertEqual(leaked, [])

    async def test_the_codex_key_still_reaches_the_child(self):
        # The strip must not take the provider's own auth with it.
        saved = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-probe"
        try:
            await self._run()
        finally:
            if saved is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = saved
        self.assertEqual((self.kwargs.get("env") or {}).get("OPENAI_API_KEY"),
                         "sk-probe")

    async def test_claude_provider_is_untouched_by_all_of_this(self):
        with patch("core.config.get_llm_effort", return_value="max"):
            await ClaudeCLIProvider("claude-sonnet-5").complete(
                "sys", [], user_id=None)
        self.assertIn("--effort", self.captured)
        self.assertIn("max", self.captured)
        self.assertNotIn("--disable", self.captured)

    async def test_a_stale_astra_ultra_never_reaches_the_claude_binary(self):
        """End to end, through the real config path.

        .env still says codex/astra/ultra — the Mini App wrote it, the bot
        was restarted onto a Claude model, or someone hand-edited it. The
        claude binary does not reject `--effort ultra`: it warns on stderr,
        which this bot surfaces only on a FAILED turn, and runs the turn at
        the default. So the guard has to be here, not in the CLI.
        """
        self._effort.stop()  # use the real get_llm_effort for this one
        saved_file = config.ENV_FILE
        saved = {k: os.environ.get(k)
                 for k in ("LLM_EFFORT", "LLM_PROVIDER", "LLM_MODEL")}
        config.ENV_FILE = Path(tempfile.gettempdir()) / "mom-astra-none.env"
        os.environ.update({"LLM_EFFORT": "ultra", "LLM_PROVIDER": "codex",
                           "LLM_MODEL": ASTRA})
        try:
            await ClaudeCLIProvider("claude-sonnet-5").complete(
                "sys", [], user_id=None)
        finally:
            config.ENV_FILE = saved_file
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            self._effort.start()
        i = self.captured.index("--effort")
        self.assertEqual(self.captured[i + 1], "max")


class _FakeStdout:
    def __init__(self, lines):
        self._lines = lines

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""


class _FakeStderr:
    async def read(self) -> bytes:
        return b""


class _FakeStdin:
    def write(self, _data):
        pass

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


class _FakeProcess:
    """Stand-in for the codex subprocess: returncode stays None while lines
    remain, so complete() drains the whole stream like a real one."""

    def __init__(self, lines):
        self._lines = list(lines)
        self.stdout = _FakeStdout(self._lines)
        self.stderr = _FakeStderr()
        self.stdin = _FakeStdin()

    @property
    def returncode(self):
        return None if self._lines else 0

    async def wait(self):
        return 0

    def kill(self):
        pass


class CodexTextBufferTests(unittest.IsolatedAsyncioTestCase):
    """The one text buffer in either provider that had no ceiling.

    The Claude branch caps all_text_blocks at 100KB, dropping oldest first,
    and both branches cap partial_text. agent_message_blocks did not, and a
    Codex turn can run for hours on a 4GB machine.
    """

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    async def _turn(self, blocks):
        import json
        lines = [json.dumps({"type": "item.completed",
                             "item": {"type": "agent_message", "text": b}}
                            ).encode() + b"\n" for b in blocks]
        lines.append(json.dumps({"type": "turn.completed",
                                 "usage": {}}).encode() + b"\n")
        provider = CodexCLIProvider(ASTRA)
        provider._cli_binary = "/usr/bin/env"
        proc = _FakeProcess(lines)

        async def fake_spawn(*cmd, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", new=fake_spawn), \
                patch("core.llm._codex_feature_names", return_value=frozenset()):
            return await provider.complete("sys", [], user_id=None)

    async def test_a_short_turn_is_returned_whole(self):
        # Positive control: the cap must not truncate an ordinary answer.
        response = await self._turn(["first block", "second block"])
        self.assertIn("first block", response.text)
        self.assertIn("second block", response.text)

    async def test_a_runaway_turn_is_capped(self):
        blocks = [f"{i:04d}" + "x" * 20000 for i in range(20)]  # ~400KB
        response = await self._turn(blocks)
        self.assertLess(len(response.text), 130000)

    async def test_the_cap_drops_the_oldest_and_keeps_the_newest(self):
        # Which end survives matters: the answer is at the end of the turn.
        blocks = [f"{i:04d}" + "x" * 20000 for i in range(20)]
        response = await self._turn(blocks)
        self.assertIn("0019", response.text)
        self.assertNotIn("0000", response.text)


class FeatureProbeTests(unittest.TestCase):
    """The probe that stands between us and a hard abort on every turn."""

    def setUp(self):
        from core import llm
        self.llm = llm
        llm._codex_feature_names.cache_clear()
        self.addCleanup(llm._codex_feature_names.cache_clear)

    def _run(self, stdout="", returncode=0, raises=None):
        import subprocess as sp

        class _Done:
            pass

        done = _Done()
        done.stdout = stdout
        done.stderr = ""
        done.returncode = returncode
        if raises is not None:
            return patch.object(sp, "run", side_effect=raises)
        return patch.object(sp, "run", return_value=done)

    def test_it_reads_the_names_out_of_the_table(self):
        table = ("apply_patch_freeform  removed  false\n"
                 "multi_agent           stable   true\n"
                 "multi_agent_v2        stable   false\n")
        with self._run(stdout=table):
            names = self.llm._codex_feature_names("/usr/bin/codex")
        self.assertIn("multi_agent", names)
        self.assertIn("multi_agent_v2", names)
        self.assertIn("apply_patch_freeform", names)

    def test_a_removed_flag_still_counts_as_known(self):
        # Verified against the real binary: `--disable multi_agent_mode`
        # renders fine although the table lists it as removed. Only an
        # UNLISTED name aborts.
        with self._run(stdout="multi_agent_mode  removed  false\n"):
            names = self.llm._codex_feature_names("/usr/bin/codex")
        self.assertIn("multi_agent_mode", names)

    def test_a_failing_subcommand_yields_nothing(self):
        # An older build with no `features` subcommand. Empty means "disable
        # nothing", never "assume the usual names".
        with self._run(stdout="error: unrecognized subcommand", returncode=2):
            names = self.llm._codex_feature_names("/usr/bin/codex")
        self.assertEqual(names, frozenset())

    def test_a_missing_binary_yields_nothing_and_does_not_raise(self):
        with self._run(raises=OSError("no such binary")):
            names = self.llm._codex_feature_names("/nope/codex")
        self.assertEqual(names, frozenset())

    def test_a_timeout_yields_nothing_and_does_not_raise(self):
        import subprocess as sp
        with self._run(raises=sp.TimeoutExpired("codex", 15)):
            names = self.llm._codex_feature_names("/usr/bin/codex")
        self.assertEqual(names, frozenset())


class VersionGateTests(unittest.TestCase):
    def test_parses_the_real_version_line(self):
        self.assertEqual(_parse_codex_version("codex-cli 0.153.4"), (0, 153, 4))

    def test_unparseable_version_is_never_called_too_old(self):
        # A wrong refusal here takes a working install off the air.
        self.assertIsNone(_parse_codex_version("codex"))
        self.assertIsNone(_codex_model_needs_newer_cli(ASTRA, "codex"))

    def test_astra_below_the_floor_is_refused_with_both_numbers(self):
        message = _codex_model_needs_newer_cli(ASTRA, "codex-cli 0.153.0")
        self.assertIsNotNone(message)
        self.assertIn("0.153.0", message)
        self.assertIn("0.153.1", message)
        self.assertIn(ASTRA, message)

    def test_astra_at_the_floor_is_allowed(self):
        self.assertIsNone(_codex_model_needs_newer_cli(ASTRA, "codex-cli 0.153.1"))

    def test_astra_above_the_floor_is_allowed(self):
        self.assertIsNone(_codex_model_needs_newer_cli(ASTRA, "codex-cli 0.154.0"))
        self.assertIsNone(_codex_model_needs_newer_cli(ASTRA, "codex-cli 1.0.0"))

    def test_an_ungated_model_is_never_refused(self):
        self.assertIsNone(_codex_model_needs_newer_cli("gpt-5.5", "codex-cli 0.1.0"))


class HealthCheckTests(unittest.IsolatedAsyncioTestCase):
    """The version gate, with the binary-presence check satisfied by a real
    executable rather than by whatever the host happens to have installed.

    health_check() returns "Codex CLI binary not found" BEFORE it ever spawns
    anything, so patching create_subprocess_exec alone leaves these two tests
    passing on a machine with codex on PATH and failing everywhere else. It
    did exactly that: green here, red on CI. sys.executable is a path that
    exists on every runner.
    """

    @staticmethod
    def _provider():
        provider = CodexCLIProvider(ASTRA)
        provider._cli_binary = sys.executable
        return provider

    async def test_an_old_build_fails_the_health_check_for_astra(self):
        provider = self._provider()

        class _Proc:
            returncode = 0

            async def communicate(self):
                return b"codex-cli 0.152.9\n", b""

        async def fake_spawn(*cmd, **kwargs):
            return _Proc()

        with patch("asyncio.create_subprocess_exec", new=fake_spawn):
            healthy, reason = await provider.health_check()
        self.assertFalse(healthy)
        self.assertIn("0.153.1", reason)

    async def test_a_current_build_passes(self):
        provider = self._provider()

        class _Proc:
            returncode = 0

            async def communicate(self):
                return b"codex-cli 0.153.4\n", b""

        async def fake_spawn(*cmd, **kwargs):
            return _Proc()

        with patch("asyncio.create_subprocess_exec", new=fake_spawn):
            healthy, reason = await provider.health_check()
        self.assertTrue(healthy)
        self.assertIn("0.153.4", reason)


class CatalogTests(unittest.TestCase):
    def test_astra_is_offered(self):
        ids = [mid for mid, _ in wizard.PROVIDER_MODELS["codex"]]
        self.assertIn(ASTRA, ids)

    def test_astra_is_not_the_default(self):
        # Same house rule as Opus on the Claude list: the heaviest model is
        # offered, never picked for someone by a fresh install.
        self.assertNotEqual(wizard.DEFAULT_MODELS["codex"], ASTRA)

    def test_the_entry_names_its_cli_requirement(self):
        entry = dict(wizard.PROVIDER_MODELS["codex"])[ASTRA]
        self.assertIn("0.153.1", entry)

    def test_every_offered_codex_model_either_has_levels_or_none(self):
        # Not an assertion that all are known — an unknown one is handled by
        # sending no override. This locks that the two states are the only
        # two, so a typo in the table cannot produce a partial set.
        for mid, _ in wizard.PROVIDER_MODELS["codex"]:
            levels = me.efforts_for("codex", mid)
            with self.subTest(model=mid):
                self.assertTrue(levels == () or set(levels) <= set(me.EFFORT_ORDER))

    def test_the_default_codex_model_never_gets_an_unsupported_effort(self):
        # The regression that wiring effort in could have introduced: this
        # repo's default effort is max and its default codex model has none.
        default = wizard.DEFAULT_MODELS["codex"]
        clamped = me.clamp_effort("codex", default, "max")
        if clamped:
            self.assertIn(clamped, me.efforts_for("codex", default))


class _Body:
    """Minimal stand-in for a FastAPI Request: the handlers only await .json()."""

    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self):
        return self._payload


ADMIN = {"_profile": {"role": "admin", "display_name": "T"}}


class MiniAppEffortTests(unittest.IsolatedAsyncioTestCase):
    """The picker offers, validates and clamps against the selected model."""

    def setUp(self):
        import miniapp.server as srv
        self.srv = srv
        self.tmp = Path(tempfile.mkdtemp(prefix="mom-astra-mini-"))
        self.env = self.tmp / ".env"
        self.env.write_text(
            "LLM_PROVIDER=codex\nLLM_MODEL=gpt-6-astra\nLLM_EFFORT=ultra\n",
            encoding="utf-8",
        )
        self._saved = srv.ENV_FILE
        srv.ENV_FILE = self.env
        self._status = patch.object(
            srv, "_bot_status",
            return_value={"active": True, "pid": 1, "uptime_seconds": 1,
                          "memory_mb": 1, "service": "x", "supported": True})
        self._status.start()
        self.addCleanup(self._status.stop)

    def tearDown(self):
        self.srv.ENV_FILE = self._saved
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stored(self, key):
        for line in self.env.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1]
        return ""

    async def test_astra_gets_six_rows_and_keeps_ultra(self):
        data = await self.srv.get_status(user=ADMIN)
        self.assertEqual([o["id"] for o in data["available_efforts"]],
                         ["low", "medium", "high", "xhigh", "max", "ultra"])
        self.assertEqual(data["effort"], "ultra")
        self.assertTrue(data["provider_supports_effort"])

    async def test_a_claude_model_gets_five_rows(self):
        self.env.write_text(
            "LLM_PROVIDER=claude\nLLM_MODEL=claude-sonnet-5\nLLM_EFFORT=max\n",
            encoding="utf-8")
        data = await self.srv.get_status(user=ADMIN)
        self.assertEqual(len(data["available_efforts"]), 5)
        self.assertNotIn("ultra", [o["id"] for o in data["available_efforts"]])

    async def test_a_stale_ultra_is_shown_clamped_not_as_selected(self):
        # .env hand-edited, or the bot restarted mid-switch. The row must not
        # highlight a button that is not on it.
        self.env.write_text(
            "LLM_PROVIDER=claude\nLLM_MODEL=claude-sonnet-5\nLLM_EFFORT=ultra\n",
            encoding="utf-8")
        data = await self.srv.get_status(user=ADMIN)
        self.assertEqual(data["effort"], "max")
        self.assertIn(data["effort"],
                      [o["id"] for o in data["available_efforts"]])

    async def test_a_codex_model_with_no_known_levels_gets_no_row(self):
        self.env.write_text(
            "LLM_PROVIDER=codex\nLLM_MODEL=gpt-5.4\nLLM_EFFORT=max\n",
            encoding="utf-8")
        data = await self.srv.get_status(user=ADMIN)
        self.assertEqual(data["available_efforts"], [])
        self.assertFalse(data["provider_supports_effort"])

    async def test_ultra_is_accepted_for_astra(self):
        result = await self.srv.set_effort(_Body({"effort": "ultra"}), user=ADMIN)
        self.assertEqual(result["effort"], "ultra")
        self.assertEqual(self._stored("LLM_EFFORT"), "ultra")

    async def test_ultra_is_refused_for_a_claude_model(self):
        from fastapi import HTTPException
        self.env.write_text(
            "LLM_PROVIDER=claude\nLLM_MODEL=claude-sonnet-5\nLLM_EFFORT=max\n",
            encoding="utf-8")
        with self.assertRaises(HTTPException) as caught:
            await self.srv.set_effort(_Body({"effort": "ultra"}), user=ADMIN)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(self._stored("LLM_EFFORT"), "max")

    async def test_max_is_refused_for_gpt_5_5(self):
        from fastapi import HTTPException
        self.env.write_text(
            "LLM_PROVIDER=codex\nLLM_MODEL=gpt-5.5\nLLM_EFFORT=high\n",
            encoding="utf-8")
        with self.assertRaises(HTTPException):
            await self.srv.set_effort(_Body({"effort": "max"}), user=ADMIN)

    async def test_switching_model_clamps_the_stored_effort(self):
        # Astra at ultra, then down to gpt-5.5, which has neither ultra nor max.
        result = await self.srv.set_model(_Body({"model": "gpt-5.5"}), user=ADMIN)
        self.assertEqual(result["effort"], "xhigh")
        self.assertEqual(self._stored("LLM_EFFORT"), "xhigh")

    async def test_switching_provider_clamps_the_stored_effort(self):
        result = await self.srv.set_provider(_Body({"provider": "claude"}),
                                             user=ADMIN)
        self.assertEqual(result["effort"], "max")
        self.assertEqual(self._stored("LLM_EFFORT"), "max")

    async def test_switching_to_a_model_with_no_levels_keeps_the_preference(self):
        # Wiping it would cost the user their setting for the round trip.
        await self.srv.set_model(_Body({"model": "gpt-5.4"}), user=ADMIN)
        self.assertEqual(self._stored("LLM_EFFORT"), "ultra")

    async def test_a_non_admin_cannot_set_effort(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as caught:
            await self.srv.set_effort(_Body({"effort": "low"}),
                                      user={"_profile": {"role": "user"}})
        self.assertEqual(caught.exception.status_code, 403)

    async def test_astra_is_in_the_pickers_model_list(self):
        data = await self.srv.get_status(user=ADMIN)
        self.assertIn(ASTRA, [m["id"] for m in data["available_models"]])


if __name__ == "__main__":
    unittest.main()


class StaleCliInstallTests(unittest.TestCase):
    """The installer's blind spot: a codex already on PATH is never upgraded.

    `wizard.setup` treats `shutil.which("codex")` as "done". A machine
    provisioned before Astra existed therefore keeps a build that refuses the
    model on every single turn, and the refusal names ChatGPT accounts rather
    than the CLI version. The health check catches it at runtime; this catches
    it while someone is still sitting at the installer.
    """

    def _probe(self, stdout="", returncode=0, exc=None):
        def fake_run(cmd, *a, **kw):
            if exc is not None:
                raise exc
            return subprocess.CompletedProcess(cmd, returncode, stdout, "")
        return patch.object(wizard.subprocess, "run", fake_run)

    def test_an_old_build_is_reported_for_astra(self):
        with self._probe("codex-cli 0.152.9\n"):
            msg = wizard._codex_build_too_old_for(ASTRA)
        self.assertIsNotNone(msg)
        self.assertIn("0.152.9", msg)
        self.assertIn("0.153.1", msg)

    def test_a_current_build_is_not_reported(self):
        with self._probe("codex-cli 0.153.4\n"):
            self.assertIsNone(wizard._codex_build_too_old_for(ASTRA))

    def test_an_ungated_model_is_never_reported(self):
        with self._probe("codex-cli 0.100.0\n"):
            self.assertIsNone(wizard._codex_build_too_old_for("gpt-5.5"))

    def test_an_unreadable_version_is_never_called_too_old(self):
        """A wrong refusal here would take a working install off the air."""
        for kwargs in ({"stdout": "who knows\n"},
                       {"returncode": 1, "stdout": "boom"},
                       {"exc": OSError("no binary")},
                       {"exc": subprocess.TimeoutExpired("codex", 20)}):
            with self.subTest(**kwargs), self._probe(**kwargs):
                self.assertIsNone(wizard._codex_build_too_old_for(ASTRA))

    def test_the_installer_actually_consults_it(self):
        """Structural, not source text: the call has to survive a reword.

        Twice, because the second call is the re-check that decides whether
        the npm upgrade actually fixed anything.
        """
        tree = ast.parse(Path(wizard.__file__).read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name)
                 and n.func.id == "_codex_build_too_old_for"]
        self.assertGreaterEqual(len(calls), 2, "installer never re-checks")

    def test_the_check_does_not_import_the_http_stack(self):
        """It runs before dependencies are guaranteed, so core.llm is out."""
        src = ast.parse(Path(wizard.__file__).read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(src)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_codex_build_too_old_for")
        imported = {a.module for a in ast.walk(fn)
                    if isinstance(a, ast.ImportFrom)}
        self.assertIn("core.model_efforts", imported)
        self.assertNotIn("core.llm", imported)


class CliFloorOwnershipTests(unittest.TestCase):
    """One table, two readers, and no second copy of the numbers."""

    def test_the_provider_reads_the_shared_table(self):
        self.assertIs(_codex_model_needs_newer_cli, me.model_needs_newer_cli)

    def test_every_gated_model_is_one_the_catalog_offers(self):
        offered = {m for m, _ in wizard.PROVIDER_MODELS["codex"]}
        for model in me.MODEL_MIN_CLI:
            self.assertIn(model, offered)

    def test_the_module_stays_stdlib_only(self):
        """install/wizard.py imports it before dependencies exist."""
        tree = ast.parse(
            Path(me.__file__).read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertEqual(names - {"__future__"}, {"re"})


class AstraStaysOptInTests(unittest.TestCase):
    """The second way Astra could become a fresh install's model.

    CatalogTests already pins DEFAULT_MODELS["codex"], and the mutation
    battery still found an arm that got past the suite: the wizard falls back
    to ``models[0][0]`` when DEFAULT_MODELS has no row for the provider, so
    first place in the list is a second door to the same outcome. Landing a
    new install on Astra breaks it, because Astra needs Codex CLI 0.153.1+ AND
    a ChatGPT plan carrying Astra, and the install step treats a codex already
    on PATH as done — an older binary then fails every turn with "not
    supported when using Codex with a ChatGPT account".

    CatalogTests.test_astra_is_offered is the control for this class: none of
    it may be satisfied by dropping Astra from the catalog.
    """

    ASTRA = "gpt-6-astra"

    def test_the_codex_default_is_a_model_the_catalog_offers(self):
        offered = [m for m, _ in wizard.PROVIDER_MODELS["codex"]]
        self.assertIn(wizard.DEFAULT_MODELS["codex"], offered)

    def test_astra_is_not_first_in_the_list(self):
        """models[0][0] is the wizard's fallback default if DEFAULT_MODELS
        ever loses its codex row, so first place is a second way in."""
        self.assertNotEqual(wizard.PROVIDER_MODELS["codex"][0][0], self.ASTRA)

    def test_the_recommended_label_is_not_on_astra(self):
        for model, desc in wizard.PROVIDER_MODELS["codex"]:
            if "recommended" in desc.lower():
                self.assertNotEqual(model, self.ASTRA)

    def test_astra_carries_its_cli_floor_in_the_offer_text(self):
        desc = dict(wizard.PROVIDER_MODELS["codex"])[self.ASTRA]
        floor = me.MODEL_MIN_CLI[self.ASTRA]
        self.assertIn(".".join(str(p) for p in floor), desc)

    def test_astra_is_gated_by_a_cli_floor(self):
        self.assertIn(self.ASTRA, me.MODEL_MIN_CLI)

    def test_the_default_codex_model_needs_no_new_cli(self):
        """A fresh install must never be told to upgrade for the default."""
        self.assertNotIn(wizard.DEFAULT_MODELS["codex"], me.MODEL_MIN_CLI)
