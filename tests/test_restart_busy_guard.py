#!/usr/bin/env python3
"""Unit tests for /restart refusing while a request is in flight.

Run: python3 -m unittest tests.test_restart_busy_guard  (from repo root)

Before this, /restart announced the shutdown, waited up to 10 seconds for a
CLI subprocess, and bounced the service regardless. For a 20-minute job 10
seconds is nothing, so an admin restarting during long work destroyed it: the
user lost the turn and, on a CLI provider, whatever partial answer had been
written died with the process. Nothing went silent (the pending-message marker
still fires on the way back up) but the work was gone.

The guard is deliberately NOT a copy of the Linux bot's, which reads
_semaphore_holder. That name is only recorded while _semaphore_active, and
main() turns that on for low-RAM machines only, so here it would be dead code
on exactly the machines that run best. _running_turns is the signal that holds
for all twelve providers; the API-provider test below is the one that pins it.

These tests drive the real restart_command, the real _restart_blockers and the
real call_llm, so they cover the mechanism rather than a replica of it.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep test logging out of the production bot.log

import bot as botmod  # noqa: E402
import core.session as session_mod  # noqa: E402
import core.updater as updater_mod  # noqa: E402
from core.llm import LLMResponse, Message  # noqa: E402


class _FakeProvider:
    """Stands in for an API provider: one HTTP call, no subprocess."""

    def __init__(self, on_complete=None):
        self.last_health = None
        self.calls: list[str] = []
        self._on_complete = on_complete

    async def complete(self, system_prompt=None, messages=None, **kwargs):
        self.calls.append(messages[-1].content if messages else "")
        if self._on_complete is not None:
            await self._on_complete()
        return LLMResponse(text="ok", model="m", provider="fake")

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def has_active_processes(self) -> bool:
        """Every real provider inherits this from LLMProvider; API ones say False."""
        return False


class _BusyCLIProvider(_FakeProvider):
    """Stands in for a CLI provider holding a live subprocess."""

    def __init__(self, active: bool = True, **kw):
        super().__init__(**kw)
        self._active = active

    @property
    def has_active_processes(self) -> bool:
        return self._active


class _FakeRegistry:
    def __init__(self):
        self.cleaned = False

    def list_running(self):
        return []

    async def cleanup_all(self):
        self.cleaned = True


def _fake_update(user_id: int, text: str = "/restart"):
    sent: list[str] = []

    async def reply_text(msg, **kwargs):
        sent.append(msg)

    message = types.SimpleNamespace(
        text=text, caption=None, message_id=1, reply_text=reply_text, chat=None,
    )
    update = types.SimpleNamespace(
        effective_user=types.SimpleNamespace(id=user_id, first_name="T"),
        message=message,
    )
    return update, sent


class _RestartStateMixin:
    """Isolate every piece of module state the guard and the restart touch."""

    def setUp(self):
        self._saved = {
            "running": botmod._running_turns,
            "pending": botmod._pending_turns,
            "epoch": botmod._stop_epoch,
            "locks": botmod._user_locks,
            "holder": botmod._semaphore_holder,
            "sem_active": botmod._semaphore_active,
            "sem": botmod._llm_semaphore,
            "provider": botmod._llm_provider,
            "is_admin": botmod.is_admin,
            "allowed": botmod.get_allowed_users,
            "profile": botmod.get_user_profile,
            "scheduler": botmod.get_scheduler,
            "registry": botmod.get_process_registry,
            "restart_service": updater_mod.restart_service,
            "refresh": botmod._refresh_provider_if_env_changed,
            "sys_prompt": botmod.build_system_prompt,
            "messages": botmod.build_messages,
            "user_dir": botmod.get_user_dir,
        }
        botmod._running_turns = set()
        botmod._pending_turns = {}
        botmod._stop_epoch = {}
        botmod._user_locks = {}
        botmod._semaphore_holder = None
        # False is the capable-machine default, and the case where a guard
        # built on _semaphore_holder would silently do nothing.
        botmod._semaphore_active = False
        botmod._llm_semaphore = None
        botmod._llm_provider = _FakeProvider()
        botmod.is_admin = lambda uid: uid == 1
        botmod.get_allowed_users = lambda: [1, 2]
        botmod.get_user_profile = lambda uid: {"display_name": f"User{uid}", "name": f"User{uid}"}
        botmod.get_scheduler = lambda: None
        self.registry = _FakeRegistry()
        botmod.get_process_registry = lambda: self.registry
        botmod._refresh_provider_if_env_changed = lambda: None
        botmod.build_system_prompt = lambda uid: "sys"
        botmod.build_messages = lambda uid, msg: [Message(role="user", content=msg)]
        botmod.get_user_dir = lambda uid: str(ROOT)

        self.restarts: list[str] = []

        def _record_restart(target: str = "bot"):
            self.restarts.append(target)
            return True, f"{target} restarted"

        updater_mod.restart_service = _record_restart

        self._compactions = set(session_mod._compaction_scheduled)
        session_mod._compaction_scheduled.clear()

    def tearDown(self):
        botmod._running_turns = self._saved["running"]
        botmod._pending_turns = self._saved["pending"]
        botmod._stop_epoch = self._saved["epoch"]
        botmod._user_locks = self._saved["locks"]
        botmod._semaphore_holder = self._saved["holder"]
        botmod._semaphore_active = self._saved["sem_active"]
        botmod._llm_semaphore = self._saved["sem"]
        botmod._llm_provider = self._saved["provider"]
        botmod.is_admin = self._saved["is_admin"]
        botmod.get_allowed_users = self._saved["allowed"]
        botmod.get_user_profile = self._saved["profile"]
        botmod.get_scheduler = self._saved["scheduler"]
        botmod.get_process_registry = self._saved["registry"]
        updater_mod.restart_service = self._saved["restart_service"]
        botmod._refresh_provider_if_env_changed = self._saved["refresh"]
        botmod.build_system_prompt = self._saved["sys_prompt"]
        botmod.build_messages = self._saved["messages"]
        botmod.get_user_dir = self._saved["user_dir"]
        session_mod._compaction_scheduled.clear()
        session_mod._compaction_scheduled.update(self._compactions)


# --------------------------------------------------------------------------
# The blocker list itself
# --------------------------------------------------------------------------

class BlockerListTests(_RestartStateMixin, unittest.TestCase):
    def test_idle_machine_has_no_blockers(self):
        self.assertEqual(botmod._restart_blockers(), [])

    def test_every_running_user_is_named_once_in_id_order(self):
        botmod._running_turns = {9, 2}
        lines = botmod._restart_blockers()
        self.assertEqual(len(lines), 2)
        self.assertIn("User2 (2)", lines[0])
        self.assertIn("User9 (9)", lines[1])

    def test_profile_without_a_display_name_falls_back_to_the_id(self):
        # get_user_profile fills defaults, but a hand-edited users.json can
        # still yield an empty string; the id must survive that.
        botmod.get_user_profile = lambda uid: {}
        botmod._running_turns = {5}
        self.assertIn("5", botmod._restart_blockers()[0])

    def test_scheduled_compaction_blocks(self):
        session_mod._compaction_scheduled.add("/tmp/u/conversation_summary.json")
        lines = botmod._restart_blockers()
        self.assertEqual(len(lines), 1)
        self.assertIn("compaction", lines[0])

    def test_orphan_cli_subprocess_blocks(self):
        botmod._llm_provider = _BusyCLIProvider(active=True)
        lines = botmod._restart_blockers()
        self.assertEqual(len(lines), 1)
        self.assertIn("subprocess", lines[0])

    def test_a_normal_cli_request_is_not_counted_twice(self):
        botmod._llm_provider = _BusyCLIProvider(active=True)
        botmod._running_turns = {1}
        self.assertEqual(len(botmod._restart_blockers()), 1)

    def test_api_provider_never_reports_a_subprocess(self):
        botmod._llm_provider = _FakeProvider()
        self.assertEqual(botmod._restart_blockers(), [])

    def test_survives_a_provider_that_is_not_set_up_yet(self):
        botmod._llm_provider = None
        self.assertEqual(botmod._restart_blockers(), [])


# --------------------------------------------------------------------------
# The handler
# --------------------------------------------------------------------------

class RestartCommandTests(_RestartStateMixin, unittest.IsolatedAsyncioTestCase):
    async def test_idle_restart_proceeds(self):
        update, sent = _fake_update(1)
        await botmod.restart_command(update, None)
        self.assertIn("bot", self.restarts)
        self.assertTrue(any("Shutting down" in m for m in sent))

    async def test_busy_restart_is_refused_and_names_the_user(self):
        botmod._running_turns = {7}
        update, sent = _fake_update(1)
        await botmod.restart_command(update, None)
        self.assertEqual(self.restarts, [], "a busy bot must not be restarted")
        self.assertEqual(len(sent), 1)
        self.assertIn("Cannot restart", sent[0])
        self.assertIn("User7 (7)", sent[0])
        self.assertIn("/stop", sent[0])
        self.assertFalse(self.registry.cleaned,
                         "the refusal must happen before anything is torn down")

    async def test_force_overrides_the_guard(self):
        botmod._running_turns = {7}
        update, sent = _fake_update(1, "/restart force")
        await botmod.restart_command(update, None)
        self.assertIn("bot", self.restarts)
        self.assertFalse(any("Cannot restart" in m for m in sent))

    async def test_force_is_case_and_space_insensitive(self):
        botmod._running_turns = {7}
        update, _ = _fake_update(1, "/restart   FORCE  ")
        await botmod.restart_command(update, None)
        self.assertIn("bot", self.restarts)

    async def test_an_unrelated_argument_does_not_force(self):
        botmod._running_turns = {7}
        update, sent = _fake_update(1, "/restart now")
        await botmod.restart_command(update, None)
        self.assertEqual(self.restarts, [])
        self.assertIn("Cannot restart", sent[0])

    async def test_non_admin_is_still_rejected_before_any_check(self):
        update, sent = _fake_update(2)
        await botmod.restart_command(update, None)
        self.assertEqual(self.restarts, [])
        self.assertEqual(sent, ["Admin only."])

    async def test_a_stranger_never_reaches_the_handler(self):
        """/restart stays behind requires_auth.

        Inserting _restart_blockers directly above restart_command stole its
        @requires_auth decorator on the first draft of this port, which would
        have opened the handler to anyone who knows the bot handle. The
        blocker helper must stay undecorated and the handler decorated.
        """
        self.assertIsNone(getattr(botmod._restart_blockers, "__wrapped__", None),
                          "the blocker helper must not be a command wrapper")
        self.assertIsNotNone(getattr(botmod.restart_command, "__wrapped__", None),
                             "restart_command lost its @requires_auth decorator")
        update, sent = _fake_update(99)
        await botmod.restart_command(update, None)
        self.assertEqual(self.restarts, [])
        self.assertEqual(sent, ["Unauthorized."])

    async def test_the_refusal_offers_the_way_out(self):
        botmod._running_turns = {7}
        update, sent = _fake_update(1)
        await botmod.restart_command(update, None)
        self.assertIn("/restart force", sent[0])


# --------------------------------------------------------------------------
# The port trap: the guard must hold for the ten providers that have no
# subprocess, on a machine where the semaphore is a no-op.
# --------------------------------------------------------------------------

class ApiProviderInFlightTests(_RestartStateMixin, unittest.IsolatedAsyncioTestCase):
    async def test_a_live_api_turn_blocks_restart(self):
        seen: dict[str, object] = {}

        async def on_complete():
            seen["blockers"] = botmod._restart_blockers()
            seen["holder"] = botmod._semaphore_holder
            seen["subprocess"] = botmod._llm_provider.has_active_processes

        botmod._llm_provider = _FakeProvider(on_complete=on_complete)
        turn = botmod._PendingTurn(7).start()
        result = await botmod.call_llm(7, "hi", turn=turn)

        self.assertEqual(result, "ok")
        self.assertTrue(seen["blockers"],
                        "a running API turn must block /restart")
        self.assertIn("User7 (7)", seen["blockers"][0])
        self.assertIsNone(seen["holder"],
                          "on a capable machine the semaphore holder stays None, "
                          "which is why the guard cannot be built on it")
        self.assertFalse(seen["subprocess"],
                         "an API provider holds no subprocess to detect either")
        self.assertEqual(botmod._restart_blockers(), [],
                         "the block must clear when the turn ends")


if __name__ == "__main__":
    unittest.main()
