"""Tests for the provider auth step in install.wizard.

CLI providers (claude, codex) require an OAuth login before the bot can
talk to them. The wizard's auth step probes the CLI's login state and, if
missing, runs the interactive login flow with a hybrid retry-skip policy:
try once, retry once on failure, then offer skip-with-warning. Skip is
non-fatal; decline-to-skip aborts the install.

For API-key providers (claude-api, openai, deepseek, grok, kimi, minimax,
gemini, ollama-cloud, openrouter) the step is a no-op — the API key is
captured during the wizard config phase and lives in .env. For 'ollama'
the step is also a no-op — the dedicated `ollama_setup` block handles
its own model pull.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install import wizard  # noqa: E402


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    """subprocess.CompletedProcess factory for clarity in test setup."""
    return subprocess.CompletedProcess(
        args=["mocked"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class FindInstallCliTests(unittest.TestCase):
    """_find_install_cli should locate binaries even outside PATH."""

    def test_path_match_wins(self):
        with patch("install.wizard._shutil", create=True):
            pass
        with patch("shutil.which", return_value="/usr/bin/claude"):
            self.assertEqual(wizard._find_install_cli("claude"), "/usr/bin/claude")

    def test_local_bin_fallback(self):
        with patch("shutil.which", return_value=None), \
             patch.object(Path, "exists", return_value=True), \
             patch("os.access", return_value=True), \
             patch.object(Path, "is_dir", return_value=False):
            result = wizard._find_install_cli("claude")
            self.assertIsNotNone(result)
            self.assertTrue(str(result).endswith("/claude"))

    def test_nothing_found(self):
        with patch("shutil.which", return_value=None), \
             patch.object(Path, "exists", return_value=False), \
             patch.object(Path, "is_dir", return_value=False):
            self.assertIsNone(wizard._find_install_cli("claude"))


class ClaudeProbeTests(unittest.TestCase):
    """_claude_probe parses `claude auth status --json` correctly."""

    def test_binary_missing(self):
        with patch.object(wizard, "_find_install_cli", return_value=None):
            ok, detail = wizard._claude_probe()
        self.assertFalse(ok)
        self.assertIn("binary not found", detail)

    def test_logged_in_with_email(self):
        out = json.dumps({"loggedIn": True, "email": "user@example.com"})
        with patch.object(wizard, "_find_install_cli", return_value="/x/claude"), \
             patch.object(subprocess, "run", return_value=_completed(0, stdout=out)):
            ok, detail = wizard._claude_probe()
        self.assertTrue(ok)
        self.assertEqual(detail, "user@example.com")

    def test_logged_in_without_email_uses_org(self):
        out = json.dumps({"loggedIn": True, "orgName": "Acme"})
        with patch.object(wizard, "_find_install_cli", return_value="/x/claude"), \
             patch.object(subprocess, "run", return_value=_completed(0, stdout=out)):
            ok, detail = wizard._claude_probe()
        self.assertTrue(ok)
        self.assertEqual(detail, "Acme")

    def test_not_logged_in(self):
        out = json.dumps({"loggedIn": False})
        with patch.object(wizard, "_find_install_cli", return_value="/x/claude"), \
             patch.object(subprocess, "run", return_value=_completed(0, stdout=out)):
            ok, detail = wizard._claude_probe()
        self.assertFalse(ok)
        self.assertEqual(detail, "not logged in")

    def test_non_zero_exit(self):
        with patch.object(wizard, "_find_install_cli", return_value="/x/claude"), \
             patch.object(subprocess, "run", return_value=_completed(2, stderr="boom\nmore")):
            ok, detail = wizard._claude_probe()
        self.assertFalse(ok)
        self.assertEqual(detail, "boom")

    def test_malformed_json(self):
        with patch.object(wizard, "_find_install_cli", return_value="/x/claude"), \
             patch.object(subprocess, "run", return_value=_completed(0, stdout="<<not json>>")):
            ok, detail = wizard._claude_probe()
        self.assertFalse(ok)
        self.assertIn("could not parse", detail)

    def test_subprocess_timeout(self):
        with patch.object(wizard, "_find_install_cli", return_value="/x/claude"), \
             patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("c", 20)):
            ok, detail = wizard._claude_probe()
        self.assertFalse(ok)
        self.assertIn("timed out", detail)

    def test_subprocess_oserror(self):
        with patch.object(wizard, "_find_install_cli", return_value="/x/claude"), \
             patch.object(subprocess, "run", side_effect=OSError("perm denied")):
            ok, detail = wizard._claude_probe()
        self.assertFalse(ok)
        self.assertIn("perm denied", detail)


class CodexProbeTests(unittest.TestCase):
    """_codex_probe parses `codex login status` exit code + stdout."""

    def test_binary_missing(self):
        with patch.object(wizard, "_find_install_cli", return_value=None):
            ok, detail = wizard._codex_probe()
        self.assertFalse(ok)
        self.assertIn("binary not found", detail)

    def test_logged_in_zero_exit(self):
        with patch.object(wizard, "_find_install_cli", return_value="/x/codex"), \
             patch.object(subprocess, "run",
                          return_value=_completed(0, stdout="Logged in via ChatGPT")):
            ok, detail = wizard._codex_probe()
        self.assertTrue(ok)
        self.assertIn("Logged in", detail)

    def test_not_logged_in(self):
        with patch.object(wizard, "_find_install_cli", return_value="/x/codex"), \
             patch.object(subprocess, "run",
                          return_value=_completed(1, stderr="not signed in")):
            ok, detail = wizard._codex_probe()
        self.assertFalse(ok)
        self.assertEqual(detail, "not signed in")

    def test_subprocess_timeout(self):
        with patch.object(wizard, "_find_install_cli", return_value="/x/codex"), \
             patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("c", 20)):
            ok, detail = wizard._codex_probe()
        self.assertFalse(ok)
        self.assertIn("timed out", detail)


class RunCliLoginTests(unittest.TestCase):
    """_run_cli_login wraps a foreground subprocess call."""

    def test_success_returns_true(self):
        with patch.object(subprocess, "run", return_value=_completed(0)):
            self.assertTrue(wizard._run_cli_login("/x/claude", ["auth", "login"], "hint"))

    def test_failure_returns_false(self):
        with patch.object(subprocess, "run", return_value=_completed(1)):
            self.assertFalse(wizard._run_cli_login("/x/claude", ["auth", "login"], "hint"))

    def test_keyboardinterrupt_returns_false(self):
        with patch.object(subprocess, "run", side_effect=KeyboardInterrupt):
            self.assertFalse(wizard._run_cli_login("/x/claude", ["auth", "login"], "hint"))

    def test_oserror_returns_false(self):
        with patch.object(subprocess, "run", side_effect=OSError("no exec")):
            self.assertFalse(wizard._run_cli_login("/x/claude", ["auth", "login"], "hint"))


class RunProviderAuthDispatchTests(unittest.TestCase):
    """_run_provider_auth dispatches to the right path per provider."""

    def test_no_provider_noop(self):
        ok, msg = wizard._run_provider_auth({})
        self.assertTrue(ok)
        self.assertIn("no provider configured", msg)

    def test_api_key_providers_noop(self):
        for p in ("claude-api", "openai", "deepseek", "grok", "kimi", "minimax",
                  "gemini", "ollama-cloud", "openrouter"):
            with self.subTest(provider=p):
                ok, msg = wizard._run_provider_auth({"llm_provider": p})
                self.assertTrue(ok)
                self.assertIn("API key", msg)

    def test_ollama_noop(self):
        ok, msg = wizard._run_provider_auth({"llm_provider": "ollama"})
        self.assertTrue(ok)
        self.assertIn("ollama_setup", msg)

    def test_unknown_provider_does_not_block(self):
        ok, msg = wizard._run_provider_auth({"llm_provider": "totally-fake"})
        self.assertTrue(ok)
        self.assertIn("no auth probe defined", msg)

    def test_provider_normalized_to_lowercase(self):
        # Defensive: env vars sometimes come in upper-case.
        ok, msg = wizard._run_provider_auth({"llm_provider": "OPENROUTER"})
        self.assertTrue(ok)
        self.assertIn("API key", msg)

    def test_claude_dispatches_to_loop(self):
        with patch.object(wizard, "_provider_auth_loop",
                          return_value=(True, "stub")) as loop_mock:
            wizard._run_provider_auth({"llm_provider": "claude"})
        loop_mock.assert_called_once()
        kwargs = loop_mock.call_args.kwargs
        self.assertEqual(kwargs["title"], "Claude")
        self.assertEqual(kwargs["login_args"], ["auth", "login"])
        self.assertEqual(kwargs["skip_command"], "claude auth login")

    def test_codex_dispatches_to_loop(self):
        with patch.object(wizard, "_provider_auth_loop",
                          return_value=(True, "stub")) as loop_mock:
            wizard._run_provider_auth({"llm_provider": "codex"})
        loop_mock.assert_called_once()
        kwargs = loop_mock.call_args.kwargs
        self.assertEqual(kwargs["title"], "Codex")
        self.assertEqual(kwargs["login_args"], ["login"])
        self.assertEqual(kwargs["skip_command"], "codex login")


class ProviderAuthLoopHappyPathTests(unittest.TestCase):
    """Already authenticated → fast probe, no prompts."""

    def test_already_authenticated(self):
        probe = MagicMock(return_value=(True, "user@example.com"))
        with patch.object(wizard, "ask") as ask_mock, \
             patch.object(wizard, "_run_cli_login") as login_mock:
            ok, msg = wizard._provider_auth_loop(
                title="Claude",
                probe=probe,
                binary_finder=lambda: "/x/claude",
                login_args=["auth", "login"],
                login_hint="h",
                skip_command="claude auth login",
            )
        self.assertTrue(ok)
        self.assertEqual(msg, "already authenticated")
        ask_mock.assert_not_called()
        login_mock.assert_not_called()

    def test_binary_missing_skips_silently(self):
        probe = MagicMock(return_value=(False, "binary not found"))
        with patch.object(wizard, "ask") as ask_mock, \
             patch.object(wizard, "_run_cli_login") as login_mock:
            ok, msg = wizard._provider_auth_loop(
                title="Claude",
                probe=probe,
                binary_finder=lambda: None,
                login_args=["auth", "login"],
                login_hint="h",
                skip_command="claude auth login",
            )
        self.assertTrue(ok)
        self.assertIn("binary missing", msg)
        ask_mock.assert_not_called()
        login_mock.assert_not_called()


class ProviderAuthLoopRetryFlowTests(unittest.TestCase):
    """Hybrid retry-skip path."""

    def _loop(self, **overrides):
        kwargs = dict(
            title="Claude",
            probe=MagicMock(return_value=(False, "not logged in")),
            binary_finder=lambda: "/x/claude",
            login_args=["auth", "login"],
            login_hint="h",
            skip_command="claude auth login",
        )
        kwargs.update(overrides)
        return wizard._provider_auth_loop(**kwargs)

    def test_first_attempt_succeeds(self):
        probe = MagicMock(side_effect=[
            (False, "not logged in"),
            (True, "user@example.com"),
        ])
        with patch.object(wizard, "ask", return_value="y"), \
             patch.object(wizard, "_run_cli_login", return_value=True):
            ok, msg = self._loop(probe=probe)
        self.assertTrue(ok)
        self.assertEqual(msg, "authenticated on first attempt")
        # 2 probes: pre-attempt + post-attempt
        self.assertEqual(probe.call_count, 2)

    def test_retry_succeeds_after_first_failure(self):
        probe = MagicMock(side_effect=[
            (False, "not logged in"),  # initial
            (False, "not logged in"),  # after first login attempt
            (True, "user@example.com"),  # after retry
        ])
        with patch.object(wizard, "ask", side_effect=["y", "y"]) as ask_mock, \
             patch.object(wizard, "_run_cli_login",
                          side_effect=[True, True]) as login_mock:
            ok, msg = self._loop(probe=probe)
        self.assertTrue(ok)
        self.assertEqual(msg, "authenticated on retry")
        self.assertEqual(probe.call_count, 3)
        self.assertEqual(login_mock.call_count, 2)
        self.assertEqual(ask_mock.call_count, 2)

    def test_user_skips_initial_login(self):
        with patch.object(wizard, "ask", return_value="n"), \
             patch.object(wizard, "_run_cli_login") as login_mock:
            ok, msg = self._loop()
        self.assertTrue(ok)
        self.assertIn("declined sign-in", msg)
        login_mock.assert_not_called()

    def test_user_skips_after_retries_fail(self):
        probe = MagicMock(side_effect=[
            (False, "not logged in"),
            (False, "not logged in"),
            (False, "not logged in"),
        ])
        # Sequence: "Sign in now? y" -> attempt 1 fails ->
        # "Try again? y" -> attempt 2 fails -> "Skip and continue? y"
        with patch.object(wizard, "ask", side_effect=["y", "y", "y"]), \
             patch.object(wizard, "_run_cli_login",
                          side_effect=[False, False]):
            ok, msg = self._loop(probe=probe)
        self.assertTrue(ok)
        self.assertEqual(msg, "user skipped after retry")

    def test_user_declines_skip_aborts(self):
        probe = MagicMock(side_effect=[
            (False, "not logged in"),
            (False, "not logged in"),
            (False, "not logged in"),
        ])
        with patch.object(wizard, "ask", side_effect=["y", "y", "n"]), \
             patch.object(wizard, "_run_cli_login",
                          side_effect=[False, False]):
            ok, msg = self._loop(probe=probe)
        self.assertFalse(ok)
        self.assertIn("declined", msg)

    def test_user_declines_retry_then_skips(self):
        probe = MagicMock(side_effect=[
            (False, "not logged in"),
            (False, "not logged in"),
        ])
        # "Sign in now? y" -> attempt 1 fails ->
        # "Try again? n" -> "Skip? y"
        with patch.object(wizard, "ask", side_effect=["y", "n", "y"]), \
             patch.object(wizard, "_run_cli_login", side_effect=[False]):
            ok, msg = self._loop(probe=probe)
        self.assertTrue(ok)
        self.assertEqual(msg, "user skipped after retry")

    def test_login_subprocess_succeeds_but_probe_still_fails(self):
        # User completed something in the browser but session not detected.
        probe = MagicMock(side_effect=[
            (False, "not logged in"),
            (False, "not logged in"),  # post-attempt-1 probe still says no
            (False, "not logged in"),  # post-retry probe still says no
        ])
        with patch.object(wizard, "ask", side_effect=["y", "y", "y"]), \
             patch.object(wizard, "_run_cli_login",
                          side_effect=[True, True]):
            ok, msg = self._loop(probe=probe)
        self.assertTrue(ok)
        self.assertEqual(msg, "user skipped after retry")

    def test_binary_disappeared_between_probe_and_login(self):
        # Probe says "not logged in" (so binary existed at probe time),
        # but binary_finder returns None at login time. Skip cleanly.
        probe = MagicMock(return_value=(False, "not logged in"))
        with patch.object(wizard, "ask", return_value="y"), \
             patch.object(wizard, "_run_cli_login") as login_mock:
            ok, msg = wizard._provider_auth_loop(
                title="Claude",
                probe=probe,
                binary_finder=lambda: None,
                login_args=["auth", "login"],
                login_hint="h",
                skip_command="claude auth login",
            )
        self.assertTrue(ok)
        self.assertIn("binary gone", msg)
        login_mock.assert_not_called()


class IntegrationDispatchTests(unittest.TestCase):
    """End-to-end shape checks: claude/codex paths use the right argv."""

    def test_claude_probe_called_with_auth_status(self):
        captured = {}
        def fake_run(cmd, *a, **kw):
            captured["cmd"] = cmd
            return _completed(0, stdout=json.dumps({"loggedIn": True, "email": "x@y"}))
        with patch.object(wizard, "_find_install_cli", return_value="/x/claude"), \
             patch.object(subprocess, "run", side_effect=fake_run):
            ok, _ = wizard._claude_probe()
        self.assertTrue(ok)
        self.assertEqual(captured["cmd"], ["/x/claude", "auth", "status", "--json"])

    def test_codex_probe_called_with_login_status(self):
        captured = {}
        def fake_run(cmd, *a, **kw):
            captured["cmd"] = cmd
            return _completed(0, stdout="Logged in")
        with patch.object(wizard, "_find_install_cli", return_value="/x/codex"), \
             patch.object(subprocess, "run", side_effect=fake_run):
            ok, _ = wizard._codex_probe()
        self.assertTrue(ok)
        self.assertEqual(captured["cmd"], ["/x/codex", "login", "status"])


class ConstantsSanityTests(unittest.TestCase):
    """Belt-and-braces checks that the module surface stays sane."""

    def test_cli_auth_providers_constant(self):
        self.assertIn("claude", wizard.CLI_AUTH_PROVIDERS)
        self.assertIn("codex", wizard.CLI_AUTH_PROVIDERS)
        # Must NOT include API-key or local providers.
        for p in ("claude-api", "openai", "ollama", "openrouter"):
            self.assertNotIn(p, wizard.CLI_AUTH_PROVIDERS)


if __name__ == "__main__":
    unittest.main()
