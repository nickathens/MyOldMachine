#!/usr/bin/env python3
"""Unit tests for /stop cancelling queued work, not just a live subprocess.

Run: python3 -m unittest tests.test_stop_queue  (from repo root)

Reported live 2026-07-21 on the Linux side. While another user's request held
the global LLM semaphore, /stop replied "No active task to stop." and did
nothing: both of the waiting user's queued messages ran once the other request
finished. Two defects in one, because /stop only ever consulted the provider's
process table and a turn waiting at the semaphore has no subprocess yet.

The fix replaces the sticky _stop_drain flag with a per-user /stop epoch. Every
unit of work stamps the epoch when it ARRIVES and re-checks it before it runs,
so a stop drops exactly the work that predates it and can never swallow a
message sent afterwards (which the sticky flag could).

These tests drive the real call_llm, the real handlers, and the real reply
composer, so they cover the mechanism rather than a replica of it.
"""
from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot as botmod  # noqa: E402
from core.llm import ClaudeCLIProvider, CodexCLIProvider, LLMResponse, Message  # noqa: E402


class _FakeProvider:
    """Stands in for an API provider (not a CLI provider)."""

    def __init__(self, on_complete=None):
        self.last_health = None
        # Prompts that actually reached the model. API providers are called
        # without user_id, so the message text is what identifies the caller.
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


class _FakeCLIProvider(_FakeProvider):
    """Stands in for a subprocess CLI provider: it can kill a live task."""

    def __init__(self, killable: bool = True, raises: bool = False, **kw):
        super().__init__(**kw)
        self.killable = killable
        self.raises = raises
        self.stopped: list[int] = []

    def stop_user(self, user_id: int) -> bool:
        self.stopped.append(user_id)
        if self.raises:
            raise RuntimeError("provider blew up")
        return self.killable


def _fake_update(user_id: int, text: str = "hello"):
    """Minimal Update stand-in for the handler paths under test."""
    sent: list[str] = []

    async def reply_text(msg, **kwargs):
        sent.append(msg)

    message = types.SimpleNamespace(
        text=text, caption=None, message_id=1,
        reply_text=reply_text, chat=None,
    )
    update = types.SimpleNamespace(
        effective_user=types.SimpleNamespace(id=user_id, first_name="T"),
        message=message,
    )
    return update, sent


class _StopStateMixin:
    """Isolate every piece of module state /stop touches."""

    def setUp(self):
        self._saved = {
            "epoch": botmod._stop_epoch,
            "pending": botmod._pending_turns,
            "running": botmod._running_turns,
            "locks": botmod._user_locks,
            "holder": botmod._semaphore_holder,
            "sem_active": botmod._semaphore_active,
            "sem": botmod._llm_semaphore,
            "provider": botmod._llm_provider,
            "cli_providers": botmod._CLI_PROVIDERS,
            "refresh": botmod._refresh_provider_if_env_changed,
            "sys_prompt": botmod.build_system_prompt,
            "messages": botmod.build_messages,
            "user_dir": botmod.get_user_dir,
        }
        botmod._stop_epoch = {}
        botmod._pending_turns = {}
        botmod._running_turns = set()
        botmod._user_locks = {}
        botmod._semaphore_holder = None
        botmod._semaphore_active = False
        botmod._llm_semaphore = None
        botmod._refresh_provider_if_env_changed = lambda: None
        botmod.build_system_prompt = lambda uid: "sys"
        botmod.build_messages = lambda uid, msg: [Message(role="user", content=msg)]
        botmod.get_user_dir = lambda uid: str(ROOT)

    def tearDown(self):
        botmod._stop_epoch = self._saved["epoch"]
        botmod._pending_turns = self._saved["pending"]
        botmod._running_turns = self._saved["running"]
        botmod._user_locks = self._saved["locks"]
        botmod._semaphore_holder = self._saved["holder"]
        botmod._semaphore_active = self._saved["sem_active"]
        botmod._llm_semaphore = self._saved["sem"]
        botmod._llm_provider = self._saved["provider"]
        botmod._CLI_PROVIDERS = self._saved["cli_providers"]
        botmod._refresh_provider_if_env_changed = self._saved["refresh"]
        botmod.build_system_prompt = self._saved["sys_prompt"]
        botmod.build_messages = self._saved["messages"]
        botmod.get_user_dir = self._saved["user_dir"]


# --------------------------------------------------------------------------
# The epoch primitive
# --------------------------------------------------------------------------

class PendingTurnTests(_StopStateMixin, unittest.TestCase):
    def test_counts_and_releases(self):
        turn = botmod._PendingTurn(5).start()
        self.assertEqual(botmod._pending_turns.get(5), 1)
        turn.release()
        self.assertNotIn(5, botmod._pending_turns, "counter key must not leak at zero")

    def test_release_is_idempotent(self):
        turn = botmod._PendingTurn(5).start()
        turn.release()
        turn.release()
        turn.release()
        self.assertEqual(botmod._pending_turns.get(5, 0), 0,
                         "double release must not drive the counter negative")

    def test_context_manager_releases_on_exception(self):
        with self.assertRaises(ValueError):
            with botmod._PendingTurn(5):
                self.assertEqual(botmod._pending_turns.get(5), 1)
                raise ValueError("boom")
        self.assertNotIn(5, botmod._pending_turns)

    def test_stop_cancels_work_that_arrived_first(self):
        turn = botmod._PendingTurn(5).start()
        self.assertFalse(turn.cancelled())
        botmod._stop_epoch[5] = botmod._stop_epoch.get(5, 0) + 1
        self.assertTrue(turn.cancelled())

    def test_stop_never_swallows_a_later_message(self):
        """The regression the sticky _stop_drain flag could not avoid."""
        botmod._stop_epoch[5] = botmod._stop_epoch.get(5, 0) + 1
        later = botmod._PendingTurn(5).start()
        self.assertFalse(later.cancelled(),
                         "a message sent after /stop must always run")

    def test_epochs_are_per_user(self):
        mine = botmod._PendingTurn(1).start()
        theirs = botmod._PendingTurn(2).start()
        botmod._stop_epoch[2] = 1
        self.assertFalse(mine.cancelled(), "one user's /stop must not touch another's")
        self.assertTrue(theirs.cancelled())

    def test_counts_multiple_queued_units(self):
        botmod._PendingTurn(5).start()
        botmod._PendingTurn(5).start()
        botmod._PendingTurn(5).start()
        self.assertEqual(botmod._pending_turns.get(5), 3)


# --------------------------------------------------------------------------
# What /stop says
# --------------------------------------------------------------------------

class StopReplyTests(unittest.TestCase):
    def test_killed_live_task(self):
        self.assertEqual(
            botmod._stop_reply(True, True, 0, None, True),
            "Stopping current task...",
        )

    def test_killed_plus_one_queued_is_singular(self):
        out = botmod._stop_reply(True, True, 1, None, True)
        self.assertIn("Also cancelled 1 queued message.", out)

    def test_killed_plus_many_queued_is_plural(self):
        out = botmod._stop_reply(True, True, 3, None, True)
        self.assertIn("Also cancelled 3 queued messages.", out)

    def test_queued_behind_another_user(self):
        """The reported case: nothing of theirs running, someone else holds it."""
        out = botmod._stop_reply(False, False, 2, "user", True)
        self.assertIn("Cancelled 2 pending requests.", out)
        self.assertIn("Nothing of yours had reached the model yet.", out)
        self.assertIn("Another user's request is still running.", out)
        self.assertNotIn("No active task to stop", out)

    def test_queued_behind_compaction(self):
        out = botmod._stop_reply(False, False, 1, "compaction", True)
        self.assertIn("Cancelled 1 pending request.", out)
        self.assertIn("background task", out)
        self.assertNotIn("Another user", out)

    def test_queued_with_no_semaphore_holder(self):
        out = botmod._stop_reply(False, False, 1, None, True)
        self.assertIn("Cancelled 1 pending request.", out)
        self.assertNotIn("Another user", out)
        self.assertNotIn("background task", out)

    def test_running_but_unkillable_on_cli(self):
        out = botmod._stop_reply(False, True, 0, None, True)
        self.assertIn("could not be interrupted", out)
        self.assertNotIn("Nothing of yours", out)

    def test_running_on_api_provider_names_the_limit(self):
        out = botmod._stop_reply(False, True, 1, None, False)
        self.assertIn("Cancelled 1 pending request.", out)
        self.assertIn("Claude CLI and Codex CLI", out)
        self.assertNotIn("Nothing of yours", out)

    def test_nothing_pending(self):
        self.assertEqual(
            botmod._stop_reply(False, False, 0, None, True),
            "No active task to stop.",
        )

    def test_nothing_pending_even_when_another_user_holds_the_semaphore(self):
        """No work of theirs to cancel means the old wording is still the truth."""
        self.assertEqual(
            botmod._stop_reply(False, False, 0, "user", True),
            "No active task to stop.",
        )


# --------------------------------------------------------------------------
# _apply_stop
# --------------------------------------------------------------------------

class ApplyStopTests(_StopStateMixin, unittest.IsolatedAsyncioTestCase):
    async def _stop(self, user_id: int) -> str:
        sent: list[str] = []

        async def reply(msg):
            sent.append(msg)

        await botmod._apply_stop(reply, user_id)
        return sent[0] if sent else ""

    async def test_bumps_epoch_even_with_nothing_pending(self):
        botmod._llm_provider = _FakeProvider()
        out = await self._stop(7)
        self.assertEqual(botmod._stop_epoch.get(7), 1,
                         "the epoch must bump so buffered albums drop too")
        self.assertEqual(out, "No active task to stop.")

    async def test_cancels_queued_work_on_an_api_provider(self):
        """The MOM-specific half: queue cancellation is not CLI-only."""
        botmod._llm_provider = _FakeProvider()
        turn = botmod._PendingTurn(7).start()
        out = await self._stop(7)
        self.assertTrue(turn.cancelled())
        self.assertIn("Cancelled 1 pending request.", out)

    async def test_kills_through_the_cli_provider(self):
        provider = _FakeCLIProvider(killable=True)
        botmod._llm_provider = provider
        botmod._CLI_PROVIDERS = (_FakeCLIProvider,)
        out = await self._stop(7)
        self.assertEqual(provider.stopped, [7])
        self.assertIn("Stopping current task...", out)

    async def test_never_calls_stop_user_on_a_non_cli_provider(self):
        provider = _FakeCLIProvider(killable=True)
        botmod._llm_provider = provider
        botmod._CLI_PROVIDERS = (ClaudeCLIProvider,)  # provider is not one
        await self._stop(7)
        self.assertEqual(provider.stopped, [],
                         "stop_user does not exist on API providers")

    async def test_survives_a_provider_that_raises(self):
        provider = _FakeCLIProvider(raises=True)
        botmod._llm_provider = provider
        botmod._CLI_PROVIDERS = (_FakeCLIProvider,)
        botmod._PendingTurn(7).start()
        out = await self._stop(7)
        self.assertIn("Cancelled 1 pending request.", out,
                      "a failing kill must not lose the queue cancellation")

    async def test_survives_a_reply_that_raises(self):
        botmod._llm_provider = _FakeProvider()
        turn = botmod._PendingTurn(7).start()

        async def reply(msg):
            raise RuntimeError("telegram down")

        await botmod._apply_stop(reply, 7)  # must not propagate
        self.assertTrue(turn.cancelled(), "the cancel must land even if the reply fails")

    async def test_reports_a_running_turn(self):
        botmod._llm_provider = _FakeCLIProvider(killable=False)
        botmod._CLI_PROVIDERS = (_FakeCLIProvider,)
        botmod._running_turns.add(7)
        out = await self._stop(7)
        self.assertIn("could not be interrupted", out)

    async def test_one_user_stop_does_not_cancel_another(self):
        botmod._llm_provider = _FakeProvider()
        mine = botmod._PendingTurn(1).start()
        theirs = botmod._PendingTurn(2).start()
        await self._stop(2)
        self.assertFalse(mine.cancelled())
        self.assertTrue(theirs.cancelled())

    def test_real_cli_providers_expose_stop_user(self):
        """_apply_stop's kill path depends on this contract, so pin it."""
        for cls in (ClaudeCLIProvider, CodexCLIProvider):
            self.assertTrue(callable(getattr(cls, "stop_user", None)), cls.__name__)
        self.assertEqual(botmod._CLI_PROVIDERS, (ClaudeCLIProvider, CodexCLIProvider))


# --------------------------------------------------------------------------
# call_llm: the point of no return
# --------------------------------------------------------------------------

class CallLLMCancellationTests(_StopStateMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        botmod._semaphore_active = True
        botmod._llm_semaphore = asyncio.Semaphore(1)

    async def test_cancelled_turn_never_reaches_the_provider(self):
        provider = _FakeProvider()
        botmod._llm_provider = provider
        turn = botmod._PendingTurn(7).start()
        botmod._stop_epoch[7] = 1  # /stop while it waited

        with self.assertRaises(botmod._TurnCancelled):
            await botmod.call_llm(7, "hi", turn=turn)

        self.assertEqual(provider.calls, [], "cancelled work must not run")
        self.assertNotIn(7, botmod._pending_turns)
        self.assertNotIn(7, botmod._running_turns)

    async def test_cancelled_turn_releases_the_semaphore(self):
        """The worst failure mode this could have: a dropped turn wedging the bot.

        _TurnGate raises from __aenter__, so the semaphore and the holder must
        unwind. If they did not, one /stop would silence the bot permanently.
        """
        provider = _FakeProvider()
        botmod._llm_provider = provider
        turn = botmod._PendingTurn(7).start()
        botmod._stop_epoch[7] = 1

        with self.assertRaises(botmod._TurnCancelled):
            await botmod.call_llm(7, "dropped", turn=turn)

        self.assertFalse(botmod._llm_semaphore.locked(), "semaphore leaked")
        self.assertIsNone(botmod._semaphore_holder, "holder leaked")

        # Proof the bot still works: the next turn goes straight through.
        result = await asyncio.wait_for(
            botmod.call_llm(7, "next one", turn=botmod._PendingTurn(7).start()),
            timeout=2,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(provider.calls, ["next one"])

    async def test_live_turn_runs_and_is_marked_running(self):
        seen = {}
        gate = asyncio.Event()

        async def on_complete():
            seen["running"] = 7 in botmod._running_turns
            seen["pending"] = botmod._pending_turns.get(7, 0)
            seen["holder"] = botmod._semaphore_holder
            gate.set()

        botmod._llm_provider = _FakeProvider(on_complete=on_complete)
        turn = botmod._PendingTurn(7).start()
        result = await botmod.call_llm(7, "hi", turn=turn)

        self.assertEqual(result, "ok")
        self.assertTrue(seen["running"], "/stop must see a turn that is running")
        self.assertEqual(seen["pending"], 0,
                         "a turn past the gate no longer counts as cancellable")
        self.assertEqual(seen["holder"], "user")
        self.assertNotIn(7, botmod._running_turns, "running flag must not leak")
        self.assertIsNone(botmod._semaphore_holder, "holder must not leak")
        self.assertTrue(gate.is_set())

    async def test_background_caller_is_never_cancelled(self):
        """A scheduled reminder passes no turn, so an unrelated /stop cannot eat it."""
        provider = _FakeProvider()
        botmod._llm_provider = provider
        botmod._stop_epoch[7] = 99
        result = await botmod.call_llm(7, "reminder", turn=None)
        self.assertEqual(result, "ok")
        self.assertEqual(len(provider.calls), 1)

    async def test_running_flag_cleared_when_the_provider_raises(self):
        async def blow_up():
            raise RuntimeError("provider down")

        botmod._llm_provider = _FakeProvider(on_complete=blow_up)
        with self.assertRaises(RuntimeError):
            await botmod.call_llm(7, "hi", turn=botmod._PendingTurn(7).start())
        self.assertNotIn(7, botmod._running_turns)
        self.assertIsNone(botmod._semaphore_holder)
        self.assertNotIn(7, botmod._pending_turns)

    async def test_reported_scenario_end_to_end(self):
        """User B queued behind user A's request, then presses /stop.

        Before the fix: "No active task to stop." and B's message ran anyway.
        """
        release = asyncio.Event()
        provider = _FakeProvider(on_complete=release.wait)
        botmod._llm_provider = provider

        a_turn = botmod._PendingTurn(1).start()
        a_task = asyncio.create_task(botmod.call_llm(1, "A's work", turn=a_turn))
        await asyncio.sleep(0.05)
        self.assertEqual(provider.calls, ["A's work"], "A should hold the semaphore")

        b_turn = botmod._PendingTurn(2).start()
        b_task = asyncio.create_task(botmod.call_llm(2, "B's work", turn=b_turn))
        await asyncio.sleep(0.05)
        self.assertEqual(botmod._pending_turns.get(2), 1, "B is queued and cancellable")

        sent: list[str] = []

        async def reply(msg):
            sent.append(msg)

        await botmod._apply_stop(reply, 2)

        self.assertIn("Cancelled 1 pending request.", sent[0])
        self.assertIn("Another user's request is still running.", sent[0])
        self.assertNotIn("No active task to stop", sent[0])

        release.set()
        await a_task
        with self.assertRaises(botmod._TurnCancelled):
            await b_task
        self.assertEqual(provider.calls, ["A's work"],
                         "B's cancelled message must never reach the model")

    async def test_a_message_sent_after_the_stop_still_runs(self):
        """End-to-end version of the sticky-flag hazard."""
        provider = _FakeProvider()
        botmod._llm_provider = provider

        stale = botmod._PendingTurn(7).start()
        sent: list[str] = []

        async def reply(msg):
            sent.append(msg)

        await botmod._apply_stop(reply, 7)
        self.assertTrue(stale.cancelled())

        fresh = botmod._PendingTurn(7).start()
        result = await botmod.call_llm(7, "sent after the stop", turn=fresh)
        self.assertEqual(result, "ok")
        self.assertEqual(len(provider.calls), 1)


# --------------------------------------------------------------------------
# Handler wiring
# --------------------------------------------------------------------------

class ProcessSingleTests(_StopStateMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._orig_inner = botmod._process_single_inner
        self.ran: list[int] = []

        async def fake_inner(update, context, turn=None):
            self.ran.append(update.effective_user.id)

        botmod._process_single_inner = fake_inner
        botmod._llm_provider = _FakeProvider()

    def tearDown(self):
        botmod._process_single_inner = self._orig_inner
        super().tearDown()

    async def test_normal_message_runs(self):
        update, _ = _fake_update(7)
        await botmod._process_single(update, None)
        self.assertEqual(self.ran, [7])
        self.assertNotIn(7, botmod._pending_turns, "the turn must be released")

    async def test_plain_stop_while_busy_cancels_the_queue(self):
        lock = botmod._get_user_lock(7)
        await lock.acquire()
        try:
            queued, _ = _fake_update(7, "queued work")
            queued_task = asyncio.create_task(botmod._process_single(queued, None))
            await asyncio.sleep(0.05)
            self.assertEqual(botmod._pending_turns.get(7), 1)

            stop_update, sent = _fake_update(7, "stop")
            await botmod._process_single(stop_update, None)

            self.assertIn("Cancelled 1 pending request.", sent[0])
            self.assertEqual(botmod._stop_epoch.get(7), 1)
        finally:
            lock.release()
        await queued_task
        self.assertEqual(self.ran, [], "the queued message must be dropped")

    async def test_plain_stop_does_not_count_itself(self):
        """The stop message is a control command, not queued work."""
        lock = botmod._get_user_lock(7)
        await lock.acquire()
        try:
            stop_update, sent = _fake_update(7, "STOP  ")
            await botmod._process_single(stop_update, None)
        finally:
            lock.release()
        self.assertEqual(sent[0], "No active task to stop.",
                         "/stop must not report cancelling itself")

    async def test_queued_message_survives_when_no_stop_arrives(self):
        """Control for the test above: without a /stop the queue still drains."""
        lock = botmod._get_user_lock(7)
        await lock.acquire()
        queued, sent = _fake_update(7, "queued work")
        queued_task = asyncio.create_task(botmod._process_single(queued, None))
        await asyncio.sleep(0.05)
        lock.release()
        await queued_task
        self.assertEqual(self.ran, [7])
        self.assertIn("Still working on your previous request.", sent[0])

    async def test_plain_stop_works_on_an_api_provider_too(self):
        botmod._llm_provider = _FakeProvider()
        botmod._CLI_PROVIDERS = (ClaudeCLIProvider,)
        lock = botmod._get_user_lock(7)
        await lock.acquire()
        try:
            queued, _ = _fake_update(7, "queued work")
            queued_task = asyncio.create_task(botmod._process_single(queued, None))
            await asyncio.sleep(0.05)
            stop_update, sent = _fake_update(7, "stop")
            await botmod._process_single(stop_update, None)
            self.assertIn("Cancelled 1 pending request.", sent[0])
        finally:
            lock.release()
        await queued_task
        self.assertEqual(self.ran, [])


class MediaGroupTests(_StopStateMixin, unittest.IsolatedAsyncioTestCase):
    """An album is stamped when it starts buffering, not when it runs."""

    def setUp(self):
        super().setUp()
        self._orig_inner = botmod._process_media_group_inner
        self._orig_wait = botmod._MEDIA_GROUP_WAIT
        self._orig_buffers = botmod._media_group_buffers
        botmod._MEDIA_GROUP_WAIT = 0.01
        botmod._media_group_buffers = {}
        self.ran: list[str] = []

        async def fake_inner(updates, user_id, context, turn=None):
            self.ran.append("ran")

        botmod._process_media_group_inner = fake_inner
        botmod._llm_provider = _FakeProvider()

    def tearDown(self):
        botmod._process_media_group_inner = self._orig_inner
        botmod._MEDIA_GROUP_WAIT = self._orig_wait
        botmod._media_group_buffers = self._orig_buffers
        super().tearDown()

    def _buffer(self, user_id: int):
        update, _ = _fake_update(user_id)
        update.message.chat = types.SimpleNamespace(
            send_message=self._noop, send_action=self._noop
        )
        botmod._media_group_buffers["g1"] = {
            "updates": [update], "timer": None, "user_id": user_id,
            "turn": botmod._PendingTurn(user_id).start(),
        }

    @staticmethod
    async def _noop(*args, **kwargs):
        return None

    async def test_album_runs_when_no_stop_arrives(self):
        self._buffer(7)
        await botmod._process_media_group("g1", None)
        self.assertEqual(self.ran, ["ran"])
        self.assertNotIn(7, botmod._pending_turns, "the album turn must be released")

    async def test_stop_during_the_collection_window_drops_the_album(self):
        self._buffer(7)
        self.assertEqual(botmod._pending_turns.get(7), 1,
                         "a buffering album must count as cancellable")

        sent: list[str] = []

        async def reply(msg):
            sent.append(msg)

        await botmod._apply_stop(reply, 7)
        await botmod._process_media_group("g1", None)

        self.assertEqual(self.ran, [], "a cancelled album must not run")
        self.assertIn("Cancelled 1 pending request.", sent[0])
        self.assertNotIn(7, botmod._pending_turns)


class SourcePinTests(unittest.TestCase):
    """Pins for wiring that unit tests cannot reach from outside."""

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "bot.py").read_text(encoding="utf-8")

    def test_sticky_drain_flag_is_gone(self):
        self.assertNotIn("_stop_drain.add", self.src)
        self.assertNotIn("_stop_drain.discard", self.src)
        self.assertNotIn("in _stop_drain", self.src)

    def test_stop_command_delegates_to_apply_stop(self):
        self.assertIn("await _apply_stop(update.message.reply_text, update.effective_user.id)",
                      self.src)

    def test_call_llm_guards_the_semaphore_with_the_turn_gate(self):
        self.assertIn('async with sem, _SemaphoreOwner("user"), _TurnGate(user_id, turn):',
                      self.src)

    def test_album_is_stamped_at_arrival(self):
        self.assertIn('"turn": _PendingTurn(user_id).start()', self.src)

    def test_compaction_is_named_as_a_semaphore_holder(self):
        self.assertIn('_SemaphoreOwner("compaction")', self.src)


if __name__ == "__main__":
    unittest.main()
