"""Long-lived setup-token wiring (CLAUDE_CODE_OAUTH_TOKEN).

The token created once by `claude setup-token` and stored in the macOS keychain
is injected into every Claude CLI turn. It outranks the /login keychain+file
store (documented auth precedence), so the CLI stops reading the rotating
per-store credentials behind the recurring daily re-login outage.
"""
import os
import subprocess
import unittest
from unittest.mock import patch

from core import credentials, llm


def _completed(returncode, stdout):
    return subprocess.CompletedProcess(
        args=["security"], returncode=returncode, stdout=stdout, stderr=""
    )


class ReadClaudeOAuthTokenTests(unittest.TestCase):
    def setUp(self):
        # The helper caches a found token at module scope; isolate each test.
        credentials._oauth_token_cache = ""

    def tearDown(self):
        credentials._oauth_token_cache = ""

    def test_returns_empty_off_darwin(self):
        with patch.object(credentials.platform, "system", return_value="Linux"), \
             patch.object(credentials.subprocess, "run") as run:
            self.assertEqual(credentials.read_claude_oauth_token(), "")
            run.assert_not_called()  # never shell out on a machine with no keychain

    def test_reads_and_strips_token_from_keychain(self):
        fake = _completed(0, "sk-ant-oat01-XXXX\n")
        with patch.object(credentials.platform, "system", return_value="Darwin"), \
             patch.object(credentials.subprocess, "run", return_value=fake) as run:
            self.assertEqual(credentials.read_claude_oauth_token(), "sk-ant-oat01-XXXX")
            run.assert_called_once()

    def test_missing_item_returns_empty_and_is_not_cached(self):
        miss = _completed(44, "")  # `security` exit 44 == item not found
        with patch.object(credentials.platform, "system", return_value="Darwin"), \
             patch.object(credentials.subprocess, "run", return_value=miss):
            self.assertEqual(credentials.read_claude_oauth_token(), "")
        self.assertEqual(credentials._oauth_token_cache, "")

    def test_transient_error_returns_empty_and_is_not_cached(self):
        with patch.object(credentials.platform, "system", return_value="Darwin"), \
             patch.object(credentials.subprocess, "run", side_effect=OSError("boom")):
            self.assertEqual(credentials.read_claude_oauth_token(), "")
        self.assertEqual(credentials._oauth_token_cache, "")

    def test_found_token_is_cached_and_not_reread(self):
        fake = _completed(0, "tok-123\n")
        with patch.object(credentials.platform, "system", return_value="Darwin"), \
             patch.object(credentials.subprocess, "run", return_value=fake):
            self.assertEqual(credentials.read_claude_oauth_token(), "tok-123")
        # A second call must not shell out again -- any run() call would raise.
        with patch.object(credentials.subprocess, "run",
                          side_effect=AssertionError("re-read the keychain")):
            self.assertEqual(credentials.read_claude_oauth_token(), "tok-123")


class ClaudeCliEnvTokenTests(unittest.TestCase):
    def _provider(self):
        return llm.ClaudeCLIProvider(model="claude-opus-4-8")

    def test_token_injected_for_system_turn(self):
        # user_id=None is the auth probe / nightly jobs -- the exact turns that
        # failed each morning. They must carry the token too.
        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True), \
             patch.object(llm, "read_claude_oauth_token", return_value="oat-sys"), \
             patch.object(llm, "_claude_workspace_env", return_value={}):
            env = self._provider()._get_cli_env(None)
            self.assertEqual(env.get("CLAUDE_CODE_OAUTH_TOKEN"), "oat-sys")

    def test_token_injected_for_user_turn_alongside_workspace(self):
        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True), \
             patch.object(llm, "read_claude_oauth_token", return_value="oat-user"), \
             patch.object(llm, "_claude_workspace_env",
                          return_value={"JARVIS_USER_ID": "7"}):
            env = self._provider()._get_cli_env(7)
            self.assertEqual(env.get("CLAUDE_CODE_OAUTH_TOKEN"), "oat-user")
            self.assertEqual(env.get("JARVIS_USER_ID"), "7")

    def test_token_survives_the_token_scrub(self):
        # The name ends in _TOKEN, which env hardening strips by pattern. It
        # must still reach the CLI subprocess env.
        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True), \
             patch.object(llm, "read_claude_oauth_token", return_value="oat-scrub"), \
             patch.object(llm, "_claude_workspace_env", return_value={}):
            env = self._provider()._get_cli_env(None)
            self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", env)

    def test_no_token_key_when_none_stored(self):
        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True), \
             patch.object(llm, "read_claude_oauth_token", return_value=""), \
             patch.object(llm, "_claude_workspace_env", return_value={}):
            env = self._provider()._get_cli_env(None)
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)

    def test_env_override_wins_over_keychain(self):
        with patch.dict(os.environ,
                        {"PATH": "/usr/bin", "CLAUDE_CODE_OAUTH_TOKEN": "from-env"},
                        clear=True), \
             patch.object(llm, "read_claude_oauth_token",
                          return_value="from-keychain") as kc, \
             patch.object(llm, "_claude_workspace_env", return_value={}):
            env = self._provider()._get_cli_env(None)
            self.assertEqual(env.get("CLAUDE_CODE_OAUTH_TOKEN"), "from-env")
            kc.assert_not_called()

    def test_fcc_proxy_provider_does_not_inject_token(self):
        # The proxy provider routes to a local gateway that doesn't understand
        # the subscription token; it must not receive it.
        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True), \
             patch.object(llm, "read_claude_oauth_token", return_value="oat-x"), \
             patch.object(llm, "_claude_workspace_env", return_value={}):
            env = llm.FreeCCProvider()._get_cli_env(None)
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)


if __name__ == "__main__":
    unittest.main()
