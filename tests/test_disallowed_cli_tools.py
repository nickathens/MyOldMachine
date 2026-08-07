#!/usr/bin/env python3
"""The Claude CLI must never be handed a tool that spawns subagents.

Reported live 2026-08-04. The user's standing rule is one agent, linear work,
and the deny list that enforces it has carried "Task" since 2026-03-06. It
worked -- every check confirms Task absent from the CLI's tool list. But the
CLI later shipped a SECOND spawner, Workflow, which was never added, and three
Workflow runs put 111 unapproved subagents and ~1.17M output tokens through one
afternoon. The fence held and a new gate had appeared beside it.

Nothing in the repo guarded that list, which is why the gate went unnoticed for
months. These tests are that guard, and they are built around four facts
measured against CLI 2.1.217 rather than assumed:

  1. It is a DENY list under --dangerously-skip-permissions, so it is the only
     fence and every future upstream tool is permitted by default.
  2. The CLI does not validate the names. `--tools NotARealToolXYZ` was
     accepted in silence and the turn ran normally, so a typo here looks like
     protection and is none.
  3. Matching is exact, not by prefix: with "Task" denied, TaskCreate,
     TaskGet, TaskList, TaskOutput, TaskStop and TaskUpdate all survive. They
     are the to-do tools and are unrelated to the spawner.
  4. --disallowedTools is variadic (<tools...>, comma OR space separated), so
     the names must go over as ONE comma-joined argv entry. Loose names would
     run on and eat whatever followed. The prompt is safe from this only
     because it travels on stdin.

The live probe at the bottom is the part that catches the NEXT Workflow. It
reads the CLI's own init event, which enumerates the tools the turn actually
got -- no model involved, no guessing. Measured while writing these tests:
29 tools with no deny list (Task and Workflow both present), 20 with the deny
list applied (both gone).

It has since done its job twice, both on 2026-08-07 against CLI 2.1.224:

  - It caught ListAgents, a 21st built-in nobody had reviewed. Reviewed and
    allowed, with what it actually reaches written down beside the set.
  - It was itself indeterminate. The probe attached whatever MCP servers the
    operator has, so a network handshake decided whether three MCP-gated
    built-ins appeared, and the module passed or failed by luck. It now
    measures upstream surface alone.

Run: python3 -m unittest tests.test_disallowed_cli_tools  (from repo root)
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import llm  # noqa: E402

USER = 8044898180
DENY_FLAG = "--disallowedTools"

# The set as reviewed on 2026-08-04. This is a canary: it exists so that
# adding or dropping a tool is a deliberate edit in two places, never a
# silent drift in one.
REVIEWED_DENY_SET = frozenset({
    "Task", "Workflow",
    "EnterPlanMode", "AskUserQuestion", "Monitor",
    "EnterWorktree", "ExitWorktree",
    "ScheduleWakeup", "RemoteTrigger", "PushNotification",
    "NotebookEdit",
})

# Every built-in the CLI handed a bot turn on 2026-08-04 with the deny list
# applied, measured from the init event of `claude -p --output-format
# stream-json`. The live probe fails on anything outside this set, because an
# unrecognised tool is exactly how Workflow arrived. Reviewing a newcomer means
# deciding whether it spawns agents, then either denying it or listing it here.
#
# ListAgents joined in CLI 2.1.224 and this module's probe caught it, which is
# what the probe is for. Reviewed by running it, not by reading its name --
# the measurement is written out below the MCP-gated set.
REVIEWED_ALLOWED_TOOLS = frozenset({
    "Bash", "Edit", "Read", "Write", "Skill", "ToolSearch", "ReportFindings",
    "WebFetch", "WebSearch", "SendMessage", "ListAgents", "DesignSync",
    "CronCreate", "CronDelete", "CronList",
    "TaskCreate", "TaskGet", "TaskList", "TaskOutput", "TaskStop", "TaskUpdate",
})

# Built-ins that exist only once an MCP server has attached. They carry no
# mcp__ prefix, so the filter in the live probe cannot see them, and whether
# they reach a given turn depends on a remote handshake beating the init event.
# Measured on Linux with CLI 2.1.224 against this repo: 3 probes in 5 attached
# 27 MCP tools and these three, 2 attached none. The probe passes
# --strict-mcp-config so it no longer flaps; these stay listed so a run WITHOUT
# that flag is still a pass rather than a failure in the other direction. They
# are MCP resource accessors, reviewed and not spawners.
MCP_GATED_ALLOWED_TOOLS = frozenset({
    "ListMcpResourcesTool", "ReadMcpResourceTool", "ReadMcpResourceDirTool",
})

# What ListAgents actually does here, measured on 2026-08-07 with CLI 2.1.224
# rather than inferred from the name:
#
#   - It ENUMERATES. Its schema, read out of the CLI itself, says it lists the
#     agents SendMessage can reach: in-process subagents, other local Claude
#     sessions on this machine, cloud sessions, remote bridges. It starts
#     nothing, so it cannot breach the one-agent constraint Task and Workflow
#     would, and it does not belong on a deny list that means "spawns agents".
#   - The directory is NOT empty on a machine running this bot. A spawned
#     `claude -p` turn listed the bot's own live session, and two spawned turns
#     in the same working directory listed each other.
#   - SendMessage really delivers to what ListAgents finds. Proven between two
#     throwaway sessions, neither of them the bot's: the sender was refused on
#     the bare name, re-sent with the ref the refusal disclosed, and the
#     receiver reported the sentinel string back. Transport is a unix socket
#     under /run/user/<uid>/cc-socks/, and that directory is mode 0700, so the
#     reach is bounded by the OS user, not by the Telegram user.
#
# The consequence for THIS repo is the one worth writing down: the soft
# multi-user model puts every Telegram user on one OS account, so peer
# messaging is inside that boundary, not outside it. `session_guard` and the
# per-user directories are not a fence against it. Whether the CLI providers
# should be handed SendMessage and ListAgents at all is a deliberate decision
# about what the deny list means, not a drift fix, so it is deliberately NOT
# made here -- this module records the measurement so the decision is made with
# it rather than without it.

LIVE_PROBE_TIMEOUT = 45


def _reviewed_allowed() -> set:
    """Every built-in that has been looked at and let through."""
    return set(REVIEWED_ALLOWED_TOOLS | MCP_GATED_ALLOWED_TOOLS)


def _newcomers(tools) -> set:
    """Built-ins in a live tool list that nobody has reviewed.

    Separate from the live test so it can be exercised on a runner with no
    CLI and no login, which is every CI run.
    """
    builtin = {t for t in tools if not t.startswith("mcp__")}
    return builtin - _reviewed_allowed()


class _Stdin:
    def write(self, _data): pass

    async def drain(self): pass

    def close(self): pass

    async def wait_closed(self): pass


class _Stderr:
    async def read(self) -> bytes:
        return b""


class _Stdout:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""


class _Process:
    """Answers one turn's worth of stream-json, then exits clean."""

    def __init__(self, lines):
        self.stdout = _Stdout(lines)
        self.stderr = _Stderr()
        self.stdin = _Stdin()
        self.returncode = None

    async def wait(self):
        self.returncode = 0
        return 0

    def kill(self):
        pass


REPLY = {"type": "assistant", "message": {"content": [
    {"type": "text", "text": "ok"}]}}


class ArgvTestBase(unittest.TestCase):
    """Captures the argv the provider really builds, via the real complete()."""

    def _argv(self, provider=None, prompt: str = "system prompt here") -> list:
        provider = provider or self._claude()
        proc = _Process([(json.dumps(REPLY) + "\n").encode()])
        captured = {}

        async def _spawn(*args, **kwargs):
            captured["argv"] = list(args)
            return proc

        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=_spawn)):
            asyncio.run(provider.complete(prompt, [], user_id=USER))
        self.assertIn("argv", captured, "provider never spawned the CLI")
        return captured["argv"]

    def _denied(self, argv: list) -> set:
        self.assertIn(DENY_FLAG, argv, f"{DENY_FLAG} is missing from the command")
        value = argv[argv.index(DENY_FLAG) + 1]
        return {name for name in value.split(",") if name}

    def _claude(self):
        p = llm.ClaudeCLIProvider("claude-sonnet-4-6", api_key="")
        p._cli_binary = "/usr/bin/env"
        return p

    def _fcc(self):
        p = llm.FreeCCProvider("claude-sonnet-4-6", api_key="")
        p._cli_binary = "/usr/bin/env"
        return p


class SpawnerDenyTests(ArgvTestBase):
    """The reported regression: a spawner reaching a bot turn."""

    def test_workflow_is_denied(self):
        self.assertIn("Workflow", self._denied(self._argv()))

    def test_task_is_still_denied(self):
        # The 2026-03-06 block. It was never the thing that broke.
        self.assertIn("Task", self._denied(self._argv()))

    def test_every_known_spawner_is_denied(self):
        denied = self._denied(self._argv())
        for tool in llm.AGENT_SPAWNING_CLI_TOOLS:
            with self.subTest(tool=tool):
                self.assertIn(tool, denied)

    def test_spawners_are_a_subset_of_the_deny_list(self):
        # Naming a spawner and then not denying it is the exact failure mode.
        missing = set(llm.AGENT_SPAWNING_CLI_TOOLS) - set(llm.DISALLOWED_CLI_TOOLS)
        self.assertEqual(set(), missing)

    def test_free_claude_code_provider_inherits_the_deny_list(self):
        # FreeCCProvider subclasses ClaudeCLIProvider and does not rebuild the
        # command. If that ever changes, the spawners come back on that route.
        self.assertEqual(REVIEWED_DENY_SET, self._denied(self._argv(self._fcc())))


class DenyListShapeTests(ArgvTestBase):
    """The traps in how the flag is passed, not in what it names."""

    def test_names_travel_as_one_comma_joined_argument(self):
        argv = self._argv()
        value = argv[argv.index(DENY_FLAG) + 1]
        self.assertIn(",", value)
        # A variadic flag would keep consuming loose names and swallow the
        # flags behind them.
        for tool in REVIEWED_DENY_SET:
            with self.subTest(tool=tool):
                self.assertNotIn(tool, argv)

    def test_the_prompt_never_travels_in_argv(self):
        argv = self._argv(prompt="SENTINEL PROMPT TEXT")
        self.assertNotIn("SENTINEL PROMPT TEXT", argv)
        self.assertEqual("-", argv[-1], "prompt must be read from stdin")

    def test_the_deny_flag_is_followed_by_a_flag_not_the_prompt(self):
        # Belt and braces on the variadic: whatever comes after the value must
        # start a new option, so the parser cannot run past it.
        argv = self._argv()
        after = argv[argv.index(DENY_FLAG) + 2]
        self.assertTrue(after.startswith("--"), f"{after!r} follows the deny value")

    def test_names_are_clean(self):
        names = list(llm.DISALLOWED_CLI_TOOLS)
        self.assertEqual(len(names), len(set(names)), "duplicate tool name")
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(name.strip(), "blank tool name")
                # Space or comma inside a name splits it into two bogus names,
                # and the CLI accepts bogus names in silence.
                self.assertEqual(name, name.strip())
                self.assertNotIn(" ", name)
                self.assertNotIn(",", name)

    def test_the_command_is_built_from_the_constant(self):
        # Proves the list is not hardcoded a second time inside complete().
        # Without this, editing the constant could quietly change nothing.
        with patch.object(llm, "DISALLOWED_CLI_TOOLS", ("Sentinel", "Other")):
            self.assertEqual({"Sentinel", "Other"}, self._denied(self._argv()))


class ReviewedSetCanaryTests(ArgvTestBase):
    """Drift alarm. Failing here is fine; failing here silently is not."""

    def test_the_deny_set_matches_the_last_review(self):
        self.assertEqual(
            REVIEWED_DENY_SET,
            set(llm.DISALLOWED_CLI_TOOLS),
            "core.llm.DISALLOWED_CLI_TOOLS changed. If a tool was added, "
            "update REVIEWED_DENY_SET here too. If one was REMOVED, be sure "
            "it does not spawn agents or reach for a terminal the bot lacks.",
        )

    def test_what_ships_matches_what_was_reviewed(self):
        self.assertEqual(REVIEWED_DENY_SET, self._denied(self._argv()))

    def test_nothing_is_both_denied_and_allowed(self):
        # A name in both sets means the live probe would go quiet about a tool
        # the deny list is meant to remove.
        overlap = set(llm.DISALLOWED_CLI_TOOLS) & _reviewed_allowed()
        self.assertEqual(set(), overlap)

    def test_no_spawner_sits_in_the_reviewed_allowed_set(self):
        # The failure mode this guards: reviewing a spawner INTO the allowed
        # set to make a failing probe go green. That would turn the only check
        # that can see a new Workflow into a rubber stamp.
        for tool in llm.AGENT_SPAWNING_CLI_TOOLS:
            with self.subTest(tool=tool):
                self.assertNotIn(tool, _reviewed_allowed())


class ProbeShapeTests(unittest.TestCase):
    """The probe must measure upstream surface, not the operator's config."""

    def test_probe_attaches_no_mcp_servers(self):
        cmd = _probe_cmd("/usr/bin/env")
        self.assertIn("--strict-mcp-config", cmd)
        # strict ALONE is what empties the list. With a --mcp-config beside it
        # the turn gets exactly that file, and the probe would be measuring
        # configuration again.
        self.assertNotIn("--mcp-config", cmd)

    def test_probe_still_applies_the_deny_list(self):
        cmd = _probe_cmd("/usr/bin/env")
        self.assertIn(DENY_FLAG, cmd)
        value = cmd[cmd.index(DENY_FLAG) + 1]
        self.assertEqual(set(llm.DISALLOWED_CLI_TOOLS), set(value.split(",")))

    def test_probe_reads_the_prompt_from_stdin(self):
        # Same rule as the real command: prompt text on argv is reaped by the
        # process sweepers on the Linux side.
        self.assertEqual("-", _probe_cmd("/usr/bin/env")[-1])


class NewcomerFilterTests(unittest.TestCase):
    """The live probe's judgement, run on a machine with no CLI at all.

    On CI there is no binary and no login, so the live class skips and every
    name in the reviewed sets goes unexercised. These feed it tool lists
    directly instead.
    """

    def test_a_reviewed_tool_is_not_a_newcomer(self):
        self.assertEqual(set(), _newcomers(sorted(REVIEWED_ALLOWED_TOOLS)))

    def test_mcp_prefixed_tools_are_ignored(self):
        self.assertEqual(set(), _newcomers(["mcp__figma__get_file", "Bash"]))

    def test_mcp_gated_builtins_are_not_newcomers(self):
        # They carry no mcp__ prefix, so only the by-name subtraction spares
        # them. Dropping that set is what this catches.
        self.assertEqual(set(), _newcomers(sorted(MCP_GATED_ALLOWED_TOOLS)))

    def test_an_unknown_builtin_is_a_newcomer(self):
        # The whole point: ListAgents looked exactly like this on 2026-08-07.
        self.assertEqual({"SomeNewTool"}, _newcomers(["Bash", "SomeNewTool"]))


def _probe_cmd(binary: str) -> list:
    """The probe's command line. Separate so a test can pin its shape.

    --strict-mcp-config with no --mcp-config beside it attaches zero MCP
    servers, and that is what makes this probe deterministic. Without it the
    CLI attaches whatever the operator has configured, including account-level
    connectors that are fetched over the network, and three built-ins appear
    or not depending on whether that handshake beats the init event. Measured
    on Linux with CLI 2.1.224: 3 runs in 5 came back with 27 MCP tools and
    three extra built-ins, 2 came back with none. This module is here to catch
    an upstream tool nobody has reviewed, so it measures upstream surface
    alone; the operator's own servers are their configuration, not new surface.
    """
    return [
        binary, "-p",
        "--output-format", "stream-json", "--verbose",
        "--strict-mcp-config",  # zero MCP servers; see docstring
        DENY_FLAG, ",".join(llm.DISALLOWED_CLI_TOOLS),
        "-",
    ]


def _probe_live_tool_list() -> list:
    """Ask the real CLI which tools a bot turn gets. Returns None if it cannot.

    Reads only the stream-json init event, which the CLI emits before it
    contacts the model, then kills the process. No subagent, no tool call, and
    the turn never completes.
    """
    binary = llm.ClaudeCLIProvider("claude-sonnet-4-6", api_key="")._cli_binary
    if not binary or not Path(binary).exists():
        return None
    cmd = _probe_cmd(binary)
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=str(ROOT),
        )
    except OSError:
        return None
    try:
        proc.stdin.write(b"x")
        proc.stdin.close()
        first = proc.stdout.readline()
    except OSError:
        return None
    finally:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        for stream in (proc.stdin, proc.stdout):
            try:
                stream.close()
            except OSError:
                pass
    try:
        event = json.loads(first)
    except (TypeError, ValueError):
        return None
    if event.get("subtype") != "init":
        return None
    return event.get("tools")


class LiveToolInventoryTests(unittest.TestCase):
    """The only check that can see a tool nobody has heard of yet.

    Everything above tests our own list against itself. This one asks the
    installed CLI what it actually handed over. It needs the binary and a
    working login, so it skips rather than fails when either is missing -- an
    offline machine is not a security finding. It fails only on a positive:
    a spawner that got through, or a tool outside the reviewed inventory.

    Set MOM_SKIP_LIVE_CLI=1 to skip it outright.
    """

    @classmethod
    def setUpClass(cls):
        if os.environ.get("MOM_SKIP_LIVE_CLI"):
            raise unittest.SkipTest("MOM_SKIP_LIVE_CLI is set")
        try:
            cls.tools = _probe_live_tool_list()
        except Exception as exc:  # noqa: BLE001 - infrastructure, not a finding
            raise unittest.SkipTest(f"live CLI probe unavailable: {exc}")
        if cls.tools is None:
            raise unittest.SkipTest(
                "live CLI probe unavailable (no binary, no login, or offline)"
            )

    def test_no_agent_spawner_reaches_a_bot_turn(self):
        for tool in llm.AGENT_SPAWNING_CLI_TOOLS:
            with self.subTest(tool=tool):
                self.assertNotIn(
                    tool, self.tools,
                    f"{tool} is live in a bot turn despite being denied. "
                    f"Either the CLI renamed it or the deny stopped working.",
                )

    def test_no_unreviewed_tool_reaches_a_bot_turn(self):
        newcomers = _newcomers(self.tools)
        self.assertEqual(
            set(), newcomers,
            f"The CLI is handing bot turns {sorted(newcomers)}, which nobody "
            f"has reviewed. This is how Workflow arrived. Decide whether each "
            f"one spawns agents: deny it in core.llm.DISALLOWED_CLI_TOOLS, or "
            f"add it to REVIEWED_ALLOWED_TOOLS here.",
        )

    def test_the_deny_list_still_removes_things(self):
        # Guards the case where the flag stops being honoured altogether and
        # every name above becomes decoration.
        self.assertTrue(
            set(llm.DISALLOWED_CLI_TOOLS) - set(self.tools),
            "not one denied tool is missing from the live list -- the deny "
            "flag looks like it is being ignored",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
