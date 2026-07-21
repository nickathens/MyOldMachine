"""Tests for Claude login expiry warning and provider-failure reporting.

Two related regressions, both from the night the shared Claude login lapsed:

  1. reflect.py logged only the CLI's stderr on failure. The Claude CLI puts the
     reason ("OAuth session expired and could not be refreshed") on STDOUT and
     leaves stderr for unrelated warnings, so the log recorded a bare "stderr="
     at precisely the moment the reason mattered.

  2. Every no_llm outcome was reported to the admin as "No capable model
     available", which points at model configuration no matter what actually
     broke. An expired login needs a completely different response.

Plus the hardening that prevents a repeat: warn while the login still works,
keying off the REFRESH token (the clock that forces a human back to the
machine), not the access token (which refreshes itself).
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make MOM utils importable without spinning up the bot
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from utils import reflect
from utils.claude_login_check import read_login_state, warning_message


def _cred_file(oauth: dict) -> Path:
    """Write a throwaway credentials file containing the given claudeAiOauth block."""
    path = Path(tempfile.mkdtemp()) / ".credentials.json"
    path.write_text(json.dumps({"claudeAiOauth": oauth}))
    return path


def _ms(days_from_now: float) -> float:
    """Epoch milliseconds, offset from now — matches the credential file's units."""
    return (datetime.now() + timedelta(days=days_from_now)).timestamp() * 1000


class LoginExpiryTests(unittest.TestCase):
    """read_login_state keys off the refresh token, not the access token."""

    def test_healthy_login_is_quiet(self):
        state = read_login_state(path=_cred_file({
            "expiresAt": _ms(0.3), "refreshTokenExpiresAt": _ms(28)}))

        self.assertEqual(state["status"], "ok")
        self.assertAlmostEqual(state["days_left"], 28, delta=0.1)
        self.assertIsNone(warning_message(state), "healthy login must not alert")

    def test_stale_access_token_alone_is_not_a_problem(self):
        """The access token expiring is normal and self-healing — it must not alert."""
        state = read_login_state(path=_cred_file({
            "expiresAt": _ms(-5), "refreshTokenExpiresAt": _ms(20)}))

        self.assertEqual(state["status"], "ok")
        self.assertIsNone(warning_message(state))

    def test_warns_inside_the_notice_window(self):
        state = read_login_state(path=_cred_file({
            "refreshTokenExpiresAt": _ms(2)}), warn_days=3)

        self.assertEqual(state["status"], "expiring")
        message = warning_message(state)
        self.assertIsNotNone(message)
        self.assertIn("claude", message.lower(), "must tell the reader how to fix it")

    def test_expired_login_is_reported_as_expired(self):
        state = read_login_state(path=_cred_file({
            "refreshTokenExpiresAt": _ms(-2)}))

        self.assertEqual(state["status"], "expired")
        self.assertLess(state["days_left"], 0)
        self.assertIn("expired", warning_message(state).lower())

    def test_missing_credential_file(self):
        state = read_login_state(path=Path("/nonexistent/.credentials.json"))

        self.assertEqual(state["status"], "missing")
        self.assertIsNotNone(warning_message(state))

    def test_unreadable_credential_file(self):
        path = Path(tempfile.mkdtemp()) / ".credentials.json"
        path.write_text("{ not json")

        state = read_login_state(path=path)

        self.assertEqual(state["status"], "unreadable")
        self.assertIsNotNone(warning_message(state))

    def test_absent_refresh_expiry_says_unknown_not_ok(self):
        """A credential shape without a refresh expiry must not read as all-clear."""
        state = read_login_state(path=_cred_file({"expiresAt": _ms(0.3)}))

        self.assertEqual(state["status"], "unknown")
        self.assertIsNone(state["days_left"])
        self.assertIsNone(warning_message(state), "unknown is not actionable, stay quiet")

    def test_garbage_expiry_value_does_not_crash(self):
        state = read_login_state(path=_cred_file({
            "refreshTokenExpiresAt": "not-a-number"}))

        self.assertEqual(state["status"], "unknown")


class ReflectionPreflightTests(unittest.TestCase):
    """The nightly job warns on the way in, while reflection still works."""

    def _run_preflight(self, state, dry=False):
        sent = []
        with patch("utils.claude_login_check.read_login_state", return_value=state), \
             patch("utils.reflect._send_alert", side_effect=lambda m: sent.append(m)):
            reflect._warn_if_login_expiring(dry=dry)
        return sent

    def test_alerts_when_expiring(self):
        sent = self._run_preflight({
            "status": "expiring", "days_left": 2.0, "detail": "expires in 2.0 days"})

        self.assertEqual(len(sent), 1)

    def test_silent_when_healthy(self):
        sent = self._run_preflight({
            "status": "ok", "days_left": 28.0, "detail": "healthy"})

        self.assertEqual(sent, [])

    def test_dry_run_does_not_alert(self):
        sent = self._run_preflight(
            {"status": "expired", "days_left": -1.0, "detail": "expired"}, dry=True)

        self.assertEqual(sent, [])

    def test_credential_failure_never_blocks_reflection(self):
        """A broken login check must not take the whole nightly run down with it."""
        with patch("utils.claude_login_check.read_login_state",
                   side_effect=OSError("disk gone")), \
             patch("utils.reflect._send_alert"):
            reflect._warn_if_login_expiring()  # must not raise


class ProviderErrorReportingTests(unittest.TestCase):
    """The reason a provider failed has to survive as far as the admin alert."""

    def setUp(self):
        reflect._record_provider_error("")

    def _cli_result(self, stdout="", stderr="", returncode=1):
        result = MagicMock()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = stderr
        return result

    def test_captures_reason_from_stdout_not_stderr(self):
        """The regression: the CLI reports why it failed on stdout."""
        reason = "Failed to authenticate: OAuth session expired and could not be refreshed"
        with patch("utils.reflect.which", return_value="/usr/bin/claude"), \
             patch("utils.reflect.subprocess.run",
                   return_value=self._cli_result(stdout=reason, stderr="")):
            out = reflect._call_claude_cli("prompt")

        self.assertEqual(out, "")
        self.assertIn("OAuth session expired", reflect._last_provider_error)

    def test_falls_back_to_stderr_when_stdout_is_empty(self):
        with patch("utils.reflect.which", return_value="/usr/bin/claude"), \
             patch("utils.reflect.subprocess.run",
                   return_value=self._cli_result(stdout="", stderr="boom on stderr")):
            reflect._call_claude_cli("prompt")

        self.assertIn("boom on stderr", reflect._last_provider_error)

    def test_records_something_even_with_no_output_at_all(self):
        with patch("utils.reflect.which", return_value="/usr/bin/claude"), \
             patch("utils.reflect.subprocess.run", return_value=self._cli_result()):
            reflect._call_claude_cli("prompt")

        self.assertIn("no output", reflect._last_provider_error)

    def test_auth_failure_names_the_login(self):
        reflect._record_provider_error(
            "Claude CLI failed: exit=1: Failed to authenticate: OAuth session "
            "expired and could not be refreshed")

        described = reflect._describe_no_llm_failure()

        self.assertIn("login expired", described.lower())
        self.assertNotIn("No capable model available", described)

    def test_api_auth_failure_also_names_the_login(self):
        reflect._record_provider_error(
            'Claude API returned 401: {"error":{"type":"authentication_error"}}')

        self.assertIn("login expired", reflect._describe_no_llm_failure().lower())

    def test_non_auth_failure_is_reported_verbatim(self):
        reflect._record_provider_error("Claude CLI timed out (120s)")

        described = reflect._describe_no_llm_failure()

        self.assertIn("timed out", described)
        self.assertNotIn("login expired", described.lower())

    def test_falls_back_to_generic_when_nothing_was_recorded(self):
        self.assertIn("No capable model available", reflect._describe_no_llm_failure())

    def test_error_does_not_leak_between_users(self):
        """A failure recorded for one user must not be blamed for the next one's."""
        reflect._record_provider_error("stale error from a previous user")
        mm = MagicMock()
        mm.parse_reflection_output.return_value = ("model body", "summary")

        with patch("utils.reflect._call_llm_for_phase2", return_value="OUTPUT"):
            reflect._run_phase2_with_retry("prompt", mm)

        self.assertEqual(reflect._last_provider_error, "")


if __name__ == "__main__":
    unittest.main()
