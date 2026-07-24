"""A turn the Claude CLI marked failed must not read as a normal reply.

The 2026-07-20 outage: the CLI's OAuth credential for one workspace was
revoked, every turn came back as

    {"type":"result","subtype":"success","is_error":true,
     "result":"Failed to authenticate. API Error: 401 ..."}

and the provider returned that string as the assistant's answer. It was
texted to the user, logged as a normal "LLM response ... 73 chars", and
stored in conversation history, where the model then read its own 401 as
something it had said. The outage ran silently for 90 minutes.

The cause was the guard `if returncode != 0 and not final_result:`. The
CLI exits non-zero on auth failure but still emits result text, so
`not final_result` was False and the error branch never fired.

Two shapes verified against the live CLI on 2026-07-20:
  failure: is_error true,  subtype "success", api_error_status set
  success: is_error false, subtype "success", api_error_status null
so `subtype` cannot discriminate and `is_error` is the only signal. Note
the field is `api_error_status` (snake_case).

Run: python3 -m unittest tests.test_llm_auth_errors  (from repo root)
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import llm  # noqa: E402
from core.llm import _is_auth_failure  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _line(obj) -> bytes:
    return (json.dumps(obj) + "\n").encode()


AUTH_RESULT = {
    "type": "result", "subtype": "success", "is_error": True,
    "api_error_status": 401,
    "result": "Failed to authenticate. API Error: 401 OAuth access token has been revoked.",
}
OK_RESULT = {
    "type": "result", "subtype": "success", "is_error": False,
    "api_error_status": None, "result": "Here is your answer.",
}


class FakeStdout:
    """Feeds pre-baked stream-json lines, then EOF."""

    def __init__(self, lines: list):
        self._lines = lines

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""


class FakeStderr:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self) -> bytes:
        return self._data


class FakeStdin:
    def write(self, _data): pass

    async def drain(self): pass

    def close(self): pass

    async def wait_closed(self): pass


class FakeProcess:
    """Minimal stand-in for the CLI subprocess.

    returncode stays None while lines remain so complete()'s read loop
    drains the whole stream before exiting, matching a real process.
    """

    def __init__(self, lines: list, returncode: int = 0, stderr: bytes = b""):
        self._lines = list(lines)
        self._rc = returncode
        self.stdout = FakeStdout(self._lines)
        self.stderr = FakeStderr(stderr)
        self.stdin = FakeStdin()

    @property
    def returncode(self):
        return None if self._lines else self._rc

    async def wait(self):
        return self._rc

    def kill(self):
        pass


class ProviderTurnTestBase(unittest.TestCase):
    def run_turn(self, events: list, returncode: int = 0, stderr: bytes = b""):
        provider = llm.ClaudeCLIProvider("claude-sonnet-4-6", api_key="")
        provider._cli_binary = "/usr/bin/env"
        proc = FakeProcess([_line(e) for e in events], returncode, stderr)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            return _run(provider.complete("sys", [], user_id=None))


class AuthFailureTurnTests(ProviderTurnTestBase):
    """The exact regression: an auth failure must be a failed turn."""

    def test_auth_failure_is_marked_as_an_error(self):
        resp = self.run_turn(
            [{"type": "system", "subtype": "init"}, AUTH_RESULT], returncode=1)
        # .error is what routes this to the admin alert and keeps it out of
        # conversation history. Before the fix it was None.
        self.assertTrue(resp.error)
        self.assertIn("auth", resp.error.lower())

    def test_cli_auth_wording_is_not_relayed_as_the_answer(self):
        resp = self.run_turn([AUTH_RESULT], returncode=1)
        self.assertNotIn("API Error: 401", resp.text)
        self.assertNotIn("OAuth access token", resp.text)
        # The user is told it is the bot's problem, not theirs.
        self.assertIn("login", resp.text.lower())

    def test_exit_zero_auth_failure_is_still_caught(self):
        # Do not rely on the exit code: is_error alone must be enough.
        resp = self.run_turn([AUTH_RESULT], returncode=0)
        self.assertTrue(resp.error)

    def test_session_expired_wording_is_also_auth(self):
        # The second user's error during the same outage, different text.
        event = dict(AUTH_RESULT, api_error_status=None,
                     result="Failed to authenticate: OAuth session expired "
                            "and could not be refreshed")
        resp = self.run_turn([event], returncode=1)
        self.assertTrue(resp.error)
        self.assertIn("login", resp.text.lower())


class NonAuthFailureTurnTests(ProviderTurnTestBase):
    """Other failures are still failures, but real work is not discarded."""

    def test_non_auth_error_is_marked_but_keeps_partial_work(self):
        events = [
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Here is the analysis you asked for."}]}},
            {"type": "result", "subtype": "success", "is_error": True,
             "api_error_status": None, "result": "Tool execution failed"},
        ]
        resp = self.run_turn(events, returncode=1)
        self.assertTrue(resp.error)
        self.assertIn("analysis you asked for", resp.text)

    def test_non_auth_error_without_output_explains_itself(self):
        resp = self.run_turn(
            [{"type": "result", "subtype": "success", "is_error": True,
              "api_error_status": None, "result": "Tool execution failed"}],
            returncode=1)
        self.assertTrue(resp.error)
        self.assertIn("failed", resp.text.lower())


class SuccessfulTurnTests(ProviderTurnTestBase):
    """The fix must not turn healthy turns into failures."""

    def test_successful_turn_has_no_error(self):
        resp = self.run_turn(
            [{"type": "system", "subtype": "init"}, OK_RESULT], returncode=0)
        self.assertIsNone(resp.error)
        self.assertIn("Here is your answer.", resp.text)

    def test_result_text_mentioning_auth_is_not_a_failure(self):
        # A turn that legitimately discusses authentication is not an auth
        # failure: is_error gates the check, wording never does on its own.
        event = dict(OK_RESULT,
                     result="You can authenticate with an OAuth credentials file.")
        resp = self.run_turn([event], returncode=0)
        self.assertIsNone(resp.error)
        self.assertIn("authenticate", resp.text)


class IsAuthFailureTests(unittest.TestCase):
    def test_live_outage_strings(self):
        for text in (
            "Failed to authenticate. API Error: 401 OAuth access token has been revoked.",
            "Failed to authenticate: OAuth session expired and could not be refreshed",
            "Invalid API key",
            "Not logged in",
        ):
            with self.subTest(text=text):
                self.assertTrue(_is_auth_failure(text, None))

    def test_status_code_alone_is_enough(self):
        self.assertTrue(_is_auth_failure("", 401))
        self.assertTrue(_is_auth_failure("", "401"))
        self.assertTrue(_is_auth_failure("", 403))

    def test_unrelated_failures_are_not_auth(self):
        for text in ("Tool execution failed: file not found",
                     "Rate limit exceeded, please retry",
                     "", None):
            with self.subTest(text=text):
                self.assertFalse(_is_auth_failure(text, None))


class AuthProbeTests(unittest.TestCase):
    """health_check must exercise auth: `claude --version` passes with zero
    valid credentials, which is why the outage looked healthy throughout."""

    def setUp(self):
        # The probe now folds credentials after running, which reaches the
        # macOS keychain and ~/.claude/.credentials.json for real. Stub it,
        # and put a loud trap behind the stub: a future edit that calls
        # `security` from this test would otherwise rewrite the machine's
        # own login, which is exactly how the 2026-07-20 credential was
        # corrupted in the first place.
        from core import claude_workspace as cw

        def _no_real_keychain(cmd, *a, **k):
            raise AssertionError(f"test reached the real keychain: {cmd}")

        self.heal = MagicMock(return_value=None)
        self._patches = [
            patch.object(cw, "heal_credential_chains", self.heal),
            patch.object(cw.subprocess, "run", side_effect=_no_real_keychain),
            # The probe's env build now reads a long-lived setup-token from the
            # keychain. These tests cover the legacy no-token folding path, so
            # report "no token stored" -- which also keeps the real-keychain
            # trap above intact (subprocess is a singleton, so that patch is
            # global and this read would otherwise trip it).
            patch.object(llm, "read_claude_oauth_token", return_value=""),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _provider(self):
        provider = llm.ClaudeCLIProvider("claude-sonnet-4-6", api_key="")
        provider._cli_binary = "/usr/bin/env"
        return provider

    def _proc(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(stdout, stderr))
        proc.returncode = returncode
        proc.kill = MagicMock()
        return proc

    def test_probe_reports_auth_failure(self):
        provider = self._provider()
        proc = self._proc(json.dumps(AUTH_RESULT).encode(), b"", 1)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            ok, detail = _run(provider._auth_probe())
        self.assertFalse(ok)
        self.assertIn("not authenticated", detail)

    def test_probe_passes_on_a_healthy_turn(self):
        provider = self._provider()
        proc = self._proc(json.dumps(OK_RESULT).encode(), b"", 0)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            ok, _ = _run(provider._auth_probe())
        self.assertTrue(ok)

    def test_probe_folds_its_own_refresh_back_into_the_shared_file(self):
        """The 2026-07-22 outage, at its source.

        This probe is a real turn on the DEFAULT config dir, so it refreshes
        like any other -- and on macOS that rotation lands in the keychain,
        revoking the token in the shared file that every per-user turn reads.
        Nothing was watching, so the machine sat diverged from the 05:00
        probe until the first user message at 12:43 died on it.
        """
        provider = self._provider()
        proc = self._proc(json.dumps(OK_RESULT).encode(), b"", 0)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            _run(provider._auth_probe())
        self.heal.assert_called_once()

    def test_probe_folds_even_when_the_probe_turn_failed(self):
        # A probe that could not refresh is precisely the case where the
        # OTHER store holds the login that still works.
        provider = self._provider()
        proc = self._proc(json.dumps(AUTH_RESULT).encode(), b"", 1)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            _run(provider._auth_probe())
        self.heal.assert_called_once()

    def test_a_failing_repair_never_takes_down_the_health_check(self):
        provider = self._provider()
        self.heal.side_effect = OSError("keychain on fire")
        proc = self._proc(json.dumps(OK_RESULT).encode(), b"", 0)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            ok, _ = _run(provider._auth_probe())
        self.assertTrue(ok)

    def test_health_check_fails_when_auth_is_dead(self):
        # --version succeeds, the probe does not: overall must be unhealthy.
        provider = self._provider()
        version_proc = self._proc(b"claude 1.2.3\n", b"", 0)
        with patch("asyncio.create_subprocess_exec",
                   AsyncMock(return_value=version_proc)), \
             patch.object(provider, "_auth_probe",
                          AsyncMock(return_value=(False, "not authenticated"))):
            ok, detail = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("not authenticated", detail)


if __name__ == "__main__":
    unittest.main()
