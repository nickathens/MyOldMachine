#!/usr/bin/env python3
"""A turn the user stopped must not be reported to them as an OOM crash.

Reported live 2026-08-04. A 54-minute turn was ended with /stop and the user
was told "[Hit memory limit after 54m 45s]". The machine's own log disproves
the memory story outright:

    13:47:49,050  /stop for user 8044898180: killed=True, running=True
    13:47:49,106  Claude error (exit -9)          <- 56ms later, stderr EMPTY

Two defects compound into it:

  1. stop_user() SIGKILLs the process immediately, but complete()'s read loop
     only tests _stop_requested at the TOP of an iteration while it is parked
     inside _read_line_with_timeout. EOF lands first, the loop leaves by the
     `elif line == b'': break` door, and the in-loop stop branch that appends
     "[Stopped by /stop command]" is never reached. Confirmation the loop took
     that door in the live incident: the in-loop log line "Claude stop
     requested for user" never appears in bot.log.

  2. Control then falls to `is_oom = (process.returncode == -9 or ...)`, which
     cannot tell our own SIGKILL from a kernel OOM kill, so it invents a memory
     limit and prints an elapsed time that reads as a diagnosis.

So the misreport was not specific to that one long turn: EVERY /stop on a turn
that had produced output was told it ran out of memory.

The fix claims the stop after the read loop, before the exit-code guard. These
tests drive the real complete() and reproduce the real ordering: the stop flag
is set at the moment the stream EOFs, never before, so the in-loop check cannot
be the thing that catches it.

Run: python3 -m unittest tests.test_stop_not_oom  (from repo root)
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import llm  # noqa: E402

USER = 8044898180
SIGKILL_RC = -9


def _run(coro):
    return asyncio.run(coro)


def _line(obj) -> bytes:
    return (json.dumps(obj) + "\n").encode()


CLAUDE_WORK = {"type": "assistant", "message": {"content": [
    {"type": "text", "text": "Findings so far: the montage has six shots."}]}}

CODEX_WORK = {"type": "item.completed", "item": {
    "type": "agent_message", "text": "Findings so far: the montage has six shots."}}


class _Stdin:
    def write(self, _data): pass

    async def drain(self): pass

    def close(self): pass

    async def wait_closed(self): pass


class _Stderr:
    async def read(self) -> bytes:
        return b""  # a SIGKILL leaves nothing behind, unlike a real OOM


class _KilledStdout:
    """Streams real output, then dies mid-read the way a SIGKILL does.

    on_eof fires as the stream ends, so the flag it sets is only visible AFTER
    the loop has already committed to the EOF branch. That ordering is the
    whole bug: setting it any earlier would let the in-loop check catch it and
    the test would pass against the unfixed code.
    """

    def __init__(self, lines: list, on_eof):
        self._lines = list(lines)
        self._on_eof = on_eof

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        self._on_eof()
        return b""


class KilledProcess:
    """Subprocess stand-in that reports its exit code only once it has EOF'd.

    complete() breaks out of the read loop as soon as returncode goes
    non-None, so a process that exposes -9 too early never reaches the read
    that ends the stream.
    """

    def __init__(self, lines: list, on_eof=lambda: None, rc: int = SIGKILL_RC):
        self._rc = rc
        self._ended = False
        self._on_eof = on_eof
        self.stdout = _KilledStdout(lines, self._end)
        self.stderr = _Stderr()
        self.stdin = _Stdin()

    def _end(self):
        self._ended = True
        self._on_eof()

    @property
    def returncode(self):
        return self._rc if self._ended else None

    async def wait(self):
        return self._rc

    def kill(self):
        pass


class ProviderTurnTestBase(unittest.TestCase):
    """Shared harness. Carries no tests of its own."""

    def _turn(self, provider, events: list, stop: bool):
        # The stop arrives while the reader is parked, exactly as /stop does.
        on_eof = (lambda: provider._stop_requested.add(USER)) if stop else (lambda: None)
        proc = KilledProcess([_line(e) for e in events], on_eof)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            return _run(provider.complete("sys", [], user_id=USER))

    def _claude(self):
        p = llm.ClaudeCLIProvider("claude-sonnet-4-6", api_key="")
        p._cli_binary = "/usr/bin/env"
        return p

    def _codex(self):
        p = llm.CodexCLIProvider("gpt-5", api_key="")
        p._cli_binary = "/usr/bin/env"
        return p


class StopIsNotOOMTests(ProviderTurnTestBase):
    """The reported regression, one provider each."""

    def test_claude_stop_is_not_called_a_memory_limit(self):
        resp = self._turn(self._claude(), [CLAUDE_WORK], stop=True)
        self.assertNotIn("memory limit", resp.text.lower())
        self.assertIn("[Stopped by /stop command]", resp.text)

    def test_claude_stop_keeps_the_work_already_produced(self):
        resp = self._turn(self._claude(), [CLAUDE_WORK], stop=True)
        self.assertIn("six shots", resp.text)

    def test_claude_stop_is_not_flagged_as_a_failed_turn(self):
        # .error routes a turn to the admin alert and drops it from history.
        # A deliberate stop is neither an outage nor lost work.
        resp = self._turn(self._claude(), [CLAUDE_WORK], stop=True)
        self.assertIsNone(resp.error)

    def test_claude_stop_with_no_output_still_reads_as_a_stop(self):
        resp = self._turn(self._claude(), [], stop=True)
        self.assertNotIn("memory limit", resp.text.lower())
        self.assertEqual("Task stopped.", resp.text)

    def test_codex_stop_is_not_called_a_memory_limit(self):
        resp = self._turn(self._codex(), [CODEX_WORK], stop=True)
        self.assertNotIn("memory limit", resp.text.lower())
        self.assertIn("[Stopped by /stop command]", resp.text)

    def test_codex_stop_with_no_output_still_reads_as_a_stop(self):
        resp = self._turn(self._codex(), [], stop=True)
        self.assertNotIn("memory limit", resp.text.lower())
        self.assertEqual("Task stopped.", resp.text)


class GenuineOOMStillReportsTests(ProviderTurnTestBase):
    """Guard against over-correcting: a real OOM must still say so.

    Same -9 exit, same empty stderr, no stop requested. If the fix had simply
    deleted the OOM branch these would fail, which is the point of keeping the
    two cases side by side.
    """

    def test_claude_oom_without_a_stop_still_reports_memory(self):
        resp = self._turn(self._claude(), [CLAUDE_WORK], stop=False)
        self.assertIn("memory limit", resp.text.lower())
        self.assertIn("six shots", resp.text)

    def test_codex_oom_without_a_stop_still_reports_memory(self):
        # No output on purpose. Codex's error guard is `returncode != 0 and
        # not agent_message_blocks`, so a -9 that arrives after any agent
        # message returns as an ordinary success and never mentions memory.
        # That is pre-existing and separate from the /stop misreport; pinning
        # it here so a later change to that guard is a deliberate one.
        resp = self._turn(self._codex(), [], stop=False)
        self.assertIn("memory limit", resp.text.lower())

    def test_codex_oom_after_output_returns_the_output_unlabelled(self):
        resp = self._turn(self._codex(), [CODEX_WORK], stop=False)
        self.assertIn("six shots", resp.text)
        self.assertNotIn("memory limit", resp.text.lower())

    def test_claude_oom_with_no_output_still_explains_itself(self):
        resp = self._turn(self._claude(), [], stop=False)
        self.assertIn("memory limit", resp.text.lower())
        self.assertEqual("OOM killed", resp.error)


class StopFlagLifecycleTests(unittest.TestCase):
    """The post-loop check reads a flag, so the flag's lifetime matters."""

    def test_flag_is_cleared_after_a_stopped_turn(self):
        # A leftover flag would make the NEXT turn report itself stopped the
        # instant it finished, silently swallowing a healthy reply.
        provider = llm.ClaudeCLIProvider("claude-sonnet-4-6", api_key="")
        provider._cli_binary = "/usr/bin/env"
        proc = KilledProcess([_line(CLAUDE_WORK)],
                             lambda: provider._stop_requested.add(USER))
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            _run(provider.complete("sys", [], user_id=USER))
        self.assertNotIn(USER, provider._stop_requested)
        self.assertNotIn(USER, provider._user_processes)


if __name__ == "__main__":
    unittest.main()
