"""Every local `claude` subprocess must carry the subscription token.

The long-lived `claude setup-token` value REPLACED the /login credential store
rather than sitting beside it: `~/.claude/.credentials.json` and the
`Claude Code-credentials` keychain item are both gone from the machine. So a
call site that spawns the CLI without injecting CLAUDE_CODE_OAUTH_TOKEN has no
fallback left -- it exits 1 with "Not logged in - Please run /login".

That is what happened. The token landed in the interactive turn path only
(core.llm._get_cli_env). Two other call sites kept inheriting a tokenless
environment and stayed alive purely on the credential file, until the file went
away on 2026-08-07:

  utils.reflect._call_claude_cli   nightly memory reflection -- failed loudly,
                                   two nights, both users, admin alerted.
  core.session compaction          background history summarisation -- failed
                                   SILENTLY, 16 times, logging only
                                   "Compaction returned no output (exit 1)".

These tests pin the token onto both, and pin the two properties that make the
shared helper safe to drop into an existing subprocess call: it only ever ADDS
to the inherited environment, and an explicit operator override wins.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ["MOM_TEST"] = "1"  # keep synthetic output out of the real reflection.log

from core import credentials


class ClaudeCliEnvHelperTests(unittest.TestCase):
    def setUp(self):
        credentials._oauth_token_cache = ""

    def tearDown(self):
        credentials._oauth_token_cache = ""

    def test_injects_stored_token(self):
        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True), \
             patch.object(credentials, "read_claude_oauth_token", return_value="oat-1"):
            env = credentials.claude_cli_env()
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "oat-1")

    def test_preserves_the_inherited_environment(self):
        # The call sites this helper serves pass no env= today, so they rely on
        # inheriting everything. Dropping it in must not take anything away.
        with patch.dict(os.environ,
                        {"PATH": "/usr/bin", "HOME": "/Users/x", "LANG": "en_US.UTF-8"},
                        clear=True), \
             patch.object(credentials, "read_claude_oauth_token", return_value="oat-2"):
            env = credentials.claude_cli_env()
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["HOME"], "/Users/x")
        self.assertEqual(env["LANG"], "en_US.UTF-8")

    def test_env_override_wins_and_skips_the_keychain(self):
        with patch.dict(os.environ,
                        {"PATH": "/usr/bin", "CLAUDE_CODE_OAUTH_TOKEN": "from-env"},
                        clear=True), \
             patch.object(credentials, "read_claude_oauth_token",
                          return_value="from-keychain") as kc:
            env = credentials.claude_cli_env()
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "from-env")
        kc.assert_not_called()

    def test_no_key_added_when_nothing_stored(self):
        # An empty value would be worse than an absent one: it overrides the
        # remaining credential chain with nothing.
        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True), \
             patch.object(credentials, "read_claude_oauth_token", return_value=""):
            env = credentials.claude_cli_env()
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)


class ReflectionCallSiteTests(unittest.TestCase):
    """utils.reflect._call_claude_cli -- the call that alerted for two nights."""

    def test_cli_call_carries_the_token(self):
        from utils import reflect

        done = subprocess.CompletedProcess(args=["claude"], returncode=0,
                                           stdout="summary", stderr="")
        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True), \
             patch.object(reflect, "which", return_value="/usr/bin/claude"), \
             patch.object(reflect, "_call_timeout", return_value=600), \
             patch.object(reflect, "get_llm_model", return_value="claude-opus-5"), \
             patch.object(reflect, "claude_cli_env",
                          return_value={"PATH": "/usr/bin",
                                        "CLAUDE_CODE_OAUTH_TOKEN": "oat-reflect"}), \
             patch.object(reflect.subprocess, "run", return_value=done) as run:
            out = reflect._call_claude_cli("prompt")

        self.assertEqual(out, "summary")
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "oat-reflect")

    def test_not_logged_in_is_reported_with_its_cause(self):
        # The CLI writes the reason to stdout, not stderr. Losing it is how the
        # 2026-07-21 outage logged a bare "stderr=" on the night it mattered.
        from utils import reflect

        failed = subprocess.CompletedProcess(
            args=["claude"], returncode=1,
            stdout="Not logged in · Please run /login", stderr="")
        with patch.object(reflect, "which", return_value="/usr/bin/claude"), \
             patch.object(reflect, "_call_timeout", return_value=600), \
             patch.object(reflect, "get_llm_model", return_value="claude-opus-5"), \
             patch.object(reflect, "claude_cli_env", return_value={"PATH": "/usr/bin"}), \
             patch.object(reflect.subprocess, "run", return_value=failed):
            self.assertEqual(reflect._call_claude_cli("prompt"), "")
        self.assertIn("Not logged in", reflect._recorded_provider_error())


class CompactionCallSiteTests(unittest.TestCase):
    """core.session background compaction -- the one that failed in silence."""

    def _run_compaction(self, result, tmp_summary):
        from core import session

        mgr = session.SessionManager.__new__(session.SessionManager)
        with patch.object(session.subprocess, "run", return_value=result) as run, \
             patch.object(session, "_executor") as executor:
            mgr._run_compaction_thread("prompt", tmp_summary, batch_size=5)
            # The pool is mocked, so run the submitted callable here.
            executor.submit.assert_called_once()
            executor.submit.call_args.args[0]()
        return run

    def test_compaction_call_carries_the_token(self):
        import tempfile

        done = subprocess.CompletedProcess(args=["claude"], returncode=0,
                                           stdout="summary text", stderr="")
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True), \
             patch.object(credentials, "read_claude_oauth_token",
                          return_value="oat-compact"):
            credentials._oauth_token_cache = ""
            run = self._run_compaction(done, Path(tmp) / "summary.json")

        env = run.call_args.kwargs["env"]
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "oat-compact")

    def test_failure_names_its_cause(self):
        import logging
        import tempfile

        failed = subprocess.CompletedProcess(
            args=["claude"], returncode=1,
            stdout="Not logged in · Please run /login", stderr="")
        with tempfile.TemporaryDirectory() as tmp, \
             self.assertLogs("core.session", level=logging.WARNING) as logs:
            self._run_compaction(failed, Path(tmp) / "summary.json")

        self.assertTrue(any("Not logged in" in line for line in logs.output),
                        f"cause missing from the warning: {logs.output}")


if __name__ == "__main__":
    unittest.main()
