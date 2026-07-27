"""MCP servers must be reachable from the subprocess CLI providers.

The gap these lock: `core.tools` merges MCP tools into the unified tool list,
but only the direct-API providers execute through it. The Claude CLI (and
FreeCC, which inherits it) runs its own agent loop, so a server configured in
mcp_servers.json was invisible to the bot's headline provider, while the
system prompt happily listed its tools. The fix passes --mcp-config, which the
CLI only accepts in `{"mcpServers": {...}}` shape, so the documented
`{"servers": [...]}` config has to be normalized rather than passed through.

Codex is deliberately excluded: `codex exec` has no MCP flag.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep test logging out of the production bot.log

import bot as botmod  # noqa: E402
from core import mcp_client  # noqa: E402
from core.llm import (  # noqa: E402
    ClaudeCLIProvider,
    CodexCLIProvider,
    FreeCCProvider,
    LLMProvider,
)

SERVERS_SHAPE = {
    "servers": [
        {
            "name": "codebase-memory",
            "command": "codebase-memory-mcp",
            "args": ["--root", "/tmp/x"],
            "env": {"CBM_MEM_BUDGET_MB": "1024"},
        }
    ]
}
CLI_SHAPE = {
    "mcpServers": {
        "codebase-memory": {
            "command": "codebase-memory-mcp",
            "args": ["--root", "/tmp/x"],
            "env": {"CBM_MEM_BUDGET_MB": "1024"},
        }
    }
}
LIST_SHAPE = SERVERS_SHAPE["servers"]


class CliConfigArgsTests(unittest.TestCase):
    """cli_config_args() normalizes every documented shape, or stays silent."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.source = tmp / "mcp_servers.json"
        self.target = tmp / "data" / "mcp_cli_config.json"
        self._patches = [
            patch.object(mcp_client, "_CONFIG_FILE", self.source),
            patch.object(mcp_client, "_CLI_CONFIG_FILE", self.target),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def _write(self, payload):
        self.source.write_text(json.dumps(payload), encoding="utf-8")

    def test_no_config_file_means_no_flags(self):
        self.assertEqual(mcp_client.cli_config_args(), [])
        self.assertFalse(self.target.exists())

    def test_empty_config_means_no_flags(self):
        self._write({"servers": []})
        self.assertEqual(mcp_client.cli_config_args(), [])

    def test_documented_servers_shape_is_normalized(self):
        self._write(SERVERS_SHAPE)
        args = mcp_client.cli_config_args()
        self.assertEqual(args, ["--mcp-config", str(self.target), "--strict-mcp-config"])
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), CLI_SHAPE)

    def test_cli_shape_passes_through_unchanged(self):
        self._write(CLI_SHAPE)
        mcp_client.cli_config_args()
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), CLI_SHAPE)

    def test_bare_list_shape_is_normalized(self):
        self._write(LIST_SHAPE)
        mcp_client.cli_config_args()
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), CLI_SHAPE)

    def test_strict_flag_is_present(self):
        """Without it, a server in the operator's own Claude config joins the turn."""
        self._write(SERVERS_SHAPE)
        self.assertIn("--strict-mcp-config", mcp_client.cli_config_args())

    def test_entry_without_command_is_dropped(self):
        self._write({"servers": [{"name": "broken"}, SERVERS_SHAPE["servers"][0]]})
        mcp_client.cli_config_args()
        written = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(list(written["mcpServers"]), ["codebase-memory"])

    def test_all_entries_invalid_means_no_flags(self):
        self._write({"servers": [{"name": "broken"}]})
        self.assertEqual(mcp_client.cli_config_args(), [])

    def test_written_config_is_owner_only(self):
        """A server's env block can hold a token."""
        self._write(SERVERS_SHAPE)
        mcp_client.cli_config_args()
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)

    def test_unchanged_config_is_not_rewritten(self):
        self._write(SERVERS_SHAPE)
        mcp_client.cli_config_args()
        before = self.target.stat().st_mtime_ns
        mcp_client.cli_config_args()
        self.assertEqual(self.target.stat().st_mtime_ns, before)

    def test_changed_config_is_rewritten(self):
        self._write(SERVERS_SHAPE)
        mcp_client.cli_config_args()
        self._write({"servers": [{"name": "other", "command": "othersrv"}]})
        mcp_client.cli_config_args()
        written = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(list(written["mcpServers"]), ["other"])

    def test_unwritable_target_degrades_instead_of_raising(self):
        self._write(SERVERS_SHAPE)
        with patch.object(Path, "mkdir", side_effect=OSError("read-only")):
            self.assertEqual(mcp_client.cli_config_args(), [])

    def test_corrupt_config_degrades_instead_of_raising(self):
        self.source.write_text("{not json", encoding="utf-8")
        self.assertEqual(mcp_client.cli_config_args(), [])


class _SpawnRefused(RuntimeError):
    """Raised in place of a real subprocess once argv has been captured."""


class CliProviderArgvTests(unittest.IsolatedAsyncioTestCase):
    """The flags must actually reach the command line of the right providers."""

    def setUp(self):
        # These tests refuse the spawn on purpose; the provider logs the
        # resulting traceback, which is expected noise, not a signal.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.captured: list[str] = []

        async def fake_spawn(*cmd, **kwargs):
            self.captured.extend(cmd)
            raise _SpawnRefused("no real subprocess in tests")

        self._spawn = patch("asyncio.create_subprocess_exec", new=fake_spawn)
        self._spawn.start()
        self._args = patch.object(
            mcp_client, "cli_config_args",
            return_value=["--mcp-config", "/tmp/cfg.json", "--strict-mcp-config"],
        )
        self._args.start()

    def tearDown(self):
        self._args.stop()
        self._spawn.stop()

    async def _run(self, provider):
        await provider.complete("sys", [], user_id=None)

    async def test_claude_cli_carries_the_mcp_flags(self):
        await self._run(ClaudeCLIProvider("claude-sonnet-5"))
        self.assertIn("--mcp-config", self.captured)
        self.assertIn("/tmp/cfg.json", self.captured)
        self.assertIn("--strict-mcp-config", self.captured)

    async def test_flags_precede_the_stdin_marker(self):
        """`-` must stay last or the CLI reads the flag as the prompt source."""
        await self._run(ClaudeCLIProvider("claude-sonnet-5"))
        self.assertEqual(self.captured[-1], "-")
        self.assertLess(self.captured.index("--mcp-config"), len(self.captured) - 1)

    async def test_freecc_inherits_the_wiring(self):
        await self._run(FreeCCProvider("claude-sonnet-5"))
        self.assertIn("--mcp-config", self.captured)

    async def test_codex_is_not_wired(self):
        """`codex exec` has no MCP flag; passing one would abort every turn."""
        await self._run(CodexCLIProvider("gpt-5.5"))
        self.assertNotIn("--mcp-config", self.captured)
        self.assertNotIn("--strict-mcp-config", self.captured)

    async def test_mcp_failure_does_not_take_the_turn_down(self):
        with patch.object(mcp_client, "cli_config_args", side_effect=OSError("boom")):
            await self._run(ClaudeCLIProvider("claude-sonnet-5"))
        self.assertIn("-p", self.captured)
        self.assertNotIn("--mcp-config", self.captured)


class _FakeAPIProvider(LLMProvider):
    """An API provider, which reaches MCP through core.tools rather than argv."""

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7):
        raise NotImplementedError

    @property
    def provider_name(self) -> str:
        return "fake-api"


class PromptOffersOnlyReachableToolsTests(unittest.TestCase):
    """The prompt must not advertise MCP tools the turn has no route to.

    Same failure mode as the one this wiring fixed, mirrored: before, the CLI
    providers were handed the tool list with no way to call it; the fix must
    not now hand that list to Codex, whose `codex exec` takes no MCP flag.
    """

    TOOL = mcp_client.MCPTool(
        name="cbm__search",
        original_name="search",
        description="search the indexed codebase",
        input_schema={},
        server_name="codebase-memory",
    )

    def setUp(self):
        manager = unittest.mock.Mock()
        manager.get_tools.return_value = [self.TOOL]
        self._mcp = patch.object(mcp_client, "get_mcp_manager", return_value=manager)
        self._mcp.start()
        self.addCleanup(self._mcp.stop)

    def _prompt(self, provider) -> str:
        with patch.object(botmod, "_llm_provider", provider):
            return botmod.build_system_prompt(user_id=1)

    def test_claude_cli_is_offered_the_tools(self):
        prompt = self._prompt(ClaudeCLIProvider("claude-sonnet-5"))
        self.assertIn("cbm__search", prompt)
        self.assertIn("Call MCP tools just like built-in tools", prompt)

    def test_api_provider_is_offered_the_tools(self):
        prompt = self._prompt(_FakeAPIProvider("some-model"))
        self.assertIn("cbm__search", prompt)
        self.assertIn("Call MCP tools just like built-in tools", prompt)

    def test_codex_is_offered_nothing_it_cannot_call(self):
        prompt = self._prompt(CodexCLIProvider("gpt-5.5"))
        self.assertNotIn("cbm__search", prompt)
        self.assertNotIn("MCP Server Tools", prompt)
        self.assertNotIn("Call MCP tools just like built-in tools", prompt)

    def test_capability_matches_the_argv_wiring(self):
        """supports_mcp is the single source of truth for both halves."""
        self.assertTrue(ClaudeCLIProvider("claude-sonnet-5").supports_mcp)
        self.assertTrue(FreeCCProvider("claude-sonnet-5").supports_mcp)
        self.assertTrue(_FakeAPIProvider("some-model").supports_mcp)
        self.assertFalse(CodexCLIProvider("gpt-5.5").supports_mcp)


if __name__ == "__main__":
    unittest.main()
