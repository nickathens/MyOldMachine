#!/usr/bin/env python3
"""Tests for the observability port: error-tail logging + admin failure alerts.

Run: python3 -m pytest tests/test_observability_port.py  (from repo root)

Two changes are covered:

1. core.llm._error_excerpt -- the subprocess CLIs (Claude, Codex) spew startup
   warnings to the HEAD of stderr, so head-truncation hid the real error (which
   lands at the tail). _error_excerpt keeps the tail instead.

2. bot._alert_admin / bot._alert_turn_failure -- a failed turn returns an error
   string inline to the triggering user, but the operator never learned about
   failures hitting OTHER users. These alert admins (resolved generically from
   the allowlist, excluding the triggering user) and throttle so a misconfigured
   provider cannot spam the admin.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot as botmod  # noqa: E402
from core.llm import _error_excerpt  # noqa: E402


class ErrorExcerptTests(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(_error_excerpt(None), "")

    def test_empty_returns_empty(self):
        self.assertEqual(_error_excerpt(""), "")

    def test_whitespace_only_returns_empty(self):
        # "" after strip() -> falls through to the "" return
        self.assertEqual(_error_excerpt("   \n  "), "")

    def test_short_string_passes_through_stripped(self):
        self.assertEqual(_error_excerpt("  boom  ", 100), "boom")

    def test_exactly_at_limit_passes_through(self):
        s = "x" * 50
        self.assertEqual(_error_excerpt(s, 50), s)

    def test_long_string_keeps_tail_with_ellipsis(self):
        s = "A" * 100 + "B" * 200
        out = _error_excerpt(s, 200)
        self.assertTrue(out.startswith("..."))
        self.assertEqual(out, "..." + "B" * 200)
        self.assertEqual(len(out), 203)  # limit + len("...")

    def test_real_error_at_tail_survives(self):
        # Reproduces the actual bug: warnings fill the head, error is at the end.
        stderr = (
            "UserWarning: pkg_resources is deprecated\n" * 40
            + "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 20.00 MiB."
        )
        out = _error_excerpt(stderr, 200)
        self.assertIn("OutOfMemoryError: CUDA out of memory", out)
        # The whole point: the OLD head-truncation would have missed the error
        # entirely (first 200 chars are all warnings), the tail surfaces it.
        self.assertNotIn("OutOfMemoryError", stderr[:200])


class AlertAdminTests(unittest.IsolatedAsyncioTestCase):
    async def test_excludes_triggering_user(self):
        sched = AsyncMock()
        with patch.object(botmod, "get_allowed_users", return_value=[1, 2, 3]), \
             patch.object(botmod, "is_admin", side_effect=lambda u: u in (1, 2)), \
             patch.object(botmod, "get_scheduler", return_value=sched):
            await botmod._alert_admin("hi", exclude_user_id=1)
        # admin 1 is the triggering user -> excluded; only admin 2 is messaged
        sent_uids = [c.args[0] for c in sched.send_message.await_args_list]
        self.assertEqual(sent_uids, [2])

    async def test_sends_to_all_admins_when_no_exclusion(self):
        sched = AsyncMock()
        with patch.object(botmod, "get_allowed_users", return_value=[1, 2, 3]), \
             patch.object(botmod, "is_admin", side_effect=lambda u: u in (1, 2)), \
             patch.object(botmod, "get_scheduler", return_value=sched):
            await botmod._alert_admin("hi")
        sent_uids = sorted(c.args[0] for c in sched.send_message.await_args_list)
        self.assertEqual(sent_uids, [1, 2])

    async def test_no_admins_no_send(self):
        sched = AsyncMock()
        with patch.object(botmod, "get_allowed_users", return_value=[3, 4]), \
             patch.object(botmod, "is_admin", return_value=False), \
             patch.object(botmod, "get_scheduler", return_value=sched):
            await botmod._alert_admin("hi")
        sched.send_message.assert_not_awaited()

    async def test_scheduler_none_no_crash(self):
        with patch.object(botmod, "get_allowed_users", return_value=[1]), \
             patch.object(botmod, "is_admin", return_value=True), \
             patch.object(botmod, "get_scheduler", return_value=None):
            # Must not raise (scheduler not yet initialized).
            await botmod._alert_admin("hi")

    async def test_send_failure_is_swallowed_and_other_admins_attempted(self):
        sched = AsyncMock()
        sched.send_message.side_effect = [RuntimeError("network down"), None]
        with patch.object(botmod, "get_allowed_users", return_value=[1, 2]), \
             patch.object(botmod, "is_admin", return_value=True), \
             patch.object(botmod, "get_scheduler", return_value=sched):
            await botmod._alert_admin("hi")  # must not raise
        # both admins attempted despite the first raising
        self.assertEqual(sched.send_message.await_count, 2)


class AlertTurnFailureThrottleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        botmod._LAST_TURN_ALERT.clear()

    async def test_first_call_fires_and_excludes_user(self):
        with patch.object(botmod, "_alert_admin", new=AsyncMock()) as alert:
            await botmod._alert_turn_failure(42, "claude-cli", "boom")
            alert.assert_awaited_once()
            self.assertEqual(alert.await_args.kwargs.get("exclude_user_id"), 42)

    async def test_identical_repeat_within_cooldown_suppressed(self):
        with patch.object(botmod, "_alert_admin", new=AsyncMock()) as alert:
            await botmod._alert_turn_failure(42, "claude-cli", "boom")
            await botmod._alert_turn_failure(99, "claude-cli", "boom")  # diff user, same sig
            alert.assert_awaited_once()  # second suppressed by (provider,error) signature

    async def test_distinct_error_fires_separately(self):
        with patch.object(botmod, "_alert_admin", new=AsyncMock()) as alert:
            await botmod._alert_turn_failure(42, "claude-cli", "boom")
            await botmod._alert_turn_failure(42, "codex-cli", "different")
            self.assertEqual(alert.await_count, 2)

    async def test_fires_again_after_cooldown(self):
        with patch.object(botmod, "_alert_admin", new=AsyncMock()) as alert:
            await botmod._alert_turn_failure(42, "claude-cli", "boom")
            # Backdate the stored timestamp past the cooldown window.
            for k in list(botmod._LAST_TURN_ALERT):
                botmod._LAST_TURN_ALERT[k] -= (botmod._TURN_ALERT_COOLDOWN + 1)
            await botmod._alert_turn_failure(42, "claude-cli", "boom")
            self.assertEqual(alert.await_count, 2)

    async def test_none_error_does_not_crash(self):
        with patch.object(botmod, "_alert_admin", new=AsyncMock()) as alert:
            await botmod._alert_turn_failure(42, "ollama", None)
            alert.assert_awaited_once()


class CallLlmWiringTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end wiring: the bug this fixes is that the failure alert was gated
    on ``not response.text``, but the CLI providers (Claude, Codex) return
    failures WITH user-facing text -- so the alert never fired for the default
    providers. These drive call_llm with a mocked provider and assert the alert
    fires for ANY error, while the user still receives the response text."""

    def setUp(self):
        botmod._LAST_TURN_ALERT.clear()

    async def _drive(self, response):
        """Run call_llm against a mocked provider returning ``response``;
        return (result_text, alert_mock)."""
        alert = AsyncMock()
        provider = MagicMock()  # not a _CLI_PROVIDERS instance -> simple API path
        provider.last_health = None
        provider.provider_name = response.provider
        provider.complete = AsyncMock(return_value=response)
        with patch.object(botmod, "_llm_provider", provider), \
             patch.object(botmod, "build_system_prompt", lambda uid: "sys"), \
             patch.object(botmod, "build_messages",
                          lambda uid, msg: [botmod.Message(role="user", content=msg)]), \
             patch.object(botmod, "_get_llm_semaphore", return_value=asyncio.Semaphore(1)), \
             patch.object(botmod, "_semaphore_active", False), \
             patch.object(botmod, "get_user_dir", lambda uid: "/tmp"), \
             patch.object(botmod, "_alert_turn_failure", new=alert):
            result = await botmod.call_llm(123, "hello")
        return result, alert

    async def test_error_with_text_still_alerts(self):
        # The regression: a CLI failure carries BOTH error and user-facing text.
        resp = botmod.LLMResponse(
            text="Error (exit code 1): ...torch.OutOfMemoryError: CUDA out of memory",
            model="m", provider="claude-cli", error="OOM killed",
        )
        result, alert = await self._drive(resp)
        alert.assert_awaited_once()
        # Alerting must not swallow the response -- the user still gets the text.
        self.assertIn("OutOfMemoryError", result)

    async def test_error_without_text_alerts_and_returns_error_string(self):
        resp = botmod.LLMResponse(text="", model="m", provider="gemini", error="boom")
        result, alert = await self._drive(resp)
        alert.assert_awaited_once()
        self.assertEqual(result, "Error from gemini: boom")

    async def test_success_does_not_alert(self):
        resp = botmod.LLMResponse(text="hello world", model="m", provider="claude-cli")
        result, alert = await self._drive(resp)
        alert.assert_not_awaited()
        self.assertIn("hello world", result)


if __name__ == "__main__":
    unittest.main()
