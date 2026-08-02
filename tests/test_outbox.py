#!/usr/bin/env python3
"""Unit tests for the durable reply outbox (utils/outbox.py + bot.drain_outbox).

Run: python3 -m unittest tests.test_outbox  (from repo root)

The failure this guards against: send_chunks_with_retry burns four attempts
over about nine seconds against a dead Telegram endpoint, gives up, and every
caller then clears the pending-message marker in a finally block regardless. So
an outage that outlives the retry budget destroyed a finished answer AND the
record that a turn had been interrupted. On the production Linux bot on
2026-08-02 at 12:05 that cost a completed 386 character reply and left the user
with 46 minutes of silence.

The contract these tests pin:
  1. A reply that cannot be sent is on disk BEFORE the first backoff, so a
     process killed mid retry still leaves it behind.
  2. Redelivery never repeats a chunk the user already received.
  3. Nothing here ever deletes an undelivered reply. Give-up moves it aside.
  4. A permanent BadRequest is never queued (it would retry forever).
  5. An unconfigured allowlist holds replies rather than destroying them.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep test logging out of the production bot.log

import bot as botmod  # noqa: E402
import core.users as users_mod  # noqa: E402
from telegram.error import BadRequest, NetworkError, TimedOut  # noqa: E402
from utils import outbox  # noqa: E402


class _FakeBot:
    """Minimal stand-in for telegram.Bot with a scriptable failure count."""

    def __init__(self, fail_first: int = 0, exc=None):
        self.sent: list[tuple[int, str]] = []
        self.fail_first = fail_first
        self.exc = exc or NetworkError("simulated outage")

    async def send_message(self, chat_id: int, text: str):
        if self.fail_first > 0:
            self.fail_first -= 1
            raise self.exc
        self.sent.append((chat_id, text))


class _IsolatedUsersDir:
    """Point both user-dir roots at a temp tree for the duration of a test.

    Two roots, not one: bot.get_user_dir resolves through
    core.users.USERS_DATA_DIR (the single source of truth per AGENTS.md), while
    drain_outbox scans bot.USERS_DIR. Patching only one of them leaves the
    tests writing fake replies into the real data/users/<id>/outbox, where the
    live drain would try to deliver them to a real Telegram account.
    """

    def _start_isolation(self):
        self._tmp = TemporaryDirectory()
        self.users_dir = Path(self._tmp.name)
        self._orig_users_data_dir = users_mod.USERS_DATA_DIR
        self._orig_users_dir = botmod.USERS_DIR
        users_mod.USERS_DATA_DIR = self.users_dir
        botmod.USERS_DIR = self.users_dir

    def _stop_isolation(self):
        users_mod.USERS_DATA_DIR = self._orig_users_data_dir
        botmod.USERS_DIR = self._orig_users_dir
        self._tmp.cleanup()


class OutboxStoreTests(unittest.TestCase):
    """utils/outbox.py on its own: record, update, validate, retire."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.users_dir = Path(self._tmp.name)
        self.user_dir = self.users_dir / "42"
        self.user_dir.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_record_then_pending_round_trip(self):
        path = outbox.record(self.user_dir, 42, ["a", "b"], sent=1)
        self.assertIsNotNone(path)
        found = outbox.pending(self.users_dir)
        self.assertEqual(len(found), 1)
        entry_path, entry = found[0]
        self.assertEqual(entry_path, path)
        self.assertEqual(entry["user_id"], 42)
        self.assertEqual(entry["chunks"], ["a", "b"])
        self.assertEqual(entry["sent"], 1)
        self.assertEqual(entry["attempts"], 0)
        self.assertFalse(entry["header_sent"])

    def test_record_declines_when_nothing_is_owed(self):
        self.assertIsNone(outbox.record(self.user_dir, 42, ["a"], sent=1))
        self.assertEqual(outbox.pending(self.users_dir), [])

    def test_update_advances_cursor(self):
        path = outbox.record(self.user_dir, 42, ["a", "b"])
        outbox.update(path, sent=2, header_sent=True)
        _, entry = outbox.pending(self.users_dir)[0]
        self.assertEqual(entry["sent"], 2)
        self.assertTrue(entry["header_sent"])

    def test_update_on_missing_file_is_a_noop(self):
        path = outbox.record(self.user_dir, 42, ["a"])
        outbox.clear(path)
        outbox.update(path, sent=1)  # must not raise, must not recreate
        self.assertFalse(Path(path).exists())

    def test_corrupt_entry_is_retired_not_deleted(self):
        directory = outbox.outbox_dir(self.user_dir)
        directory.mkdir(parents=True, exist_ok=True)
        bad = directory / "20260802_120511_000000_deadbeef.json"
        bad.write_text("{not json at all", encoding="utf-8")

        self.assertEqual(outbox.pending(self.users_dir), [])
        self.assertFalse(bad.exists())
        kept = list((directory / outbox.EXPIRED_DIRNAME).glob("*.json"))
        self.assertEqual(len(kept), 1, "a corrupt entry must be kept, not destroyed")

    def test_entry_missing_user_id_is_rejected(self):
        directory = outbox.outbox_dir(self.user_dir)
        directory.mkdir(parents=True, exist_ok=True)
        bad = directory / "20260802_120511_000000_beefdead.json"
        bad.write_text('{"chunks": ["hi"], "created": "2026-08-02T12:05:11"}', encoding="utf-8")
        self.assertEqual(outbox.pending(self.users_dir), [])

    def test_entry_with_unparseable_created_is_rejected(self):
        directory = outbox.outbox_dir(self.user_dir)
        directory.mkdir(parents=True, exist_ok=True)
        bad = directory / "20260802_120511_000000_cafe0000.json"
        bad.write_text('{"chunks": ["hi"], "user_id": 42, "created": "yesterday"}', encoding="utf-8")
        self.assertEqual(outbox.pending(self.users_dir), [])

    def test_out_of_range_cursor_is_clamped_to_zero(self):
        path = outbox.record(self.user_dir, 42, ["a", "b"])
        outbox.update(path, sent=99)
        _, entry = outbox.pending(self.users_dir)[0]
        self.assertEqual(entry["sent"], 0, "a bogus cursor must resend, never skip")

    def test_pending_is_oldest_first_across_users(self):
        other = self.users_dir / "7"
        other.mkdir()
        first = outbox.record(other, 7, ["old"])
        outbox.update(first, created=(datetime.now() - timedelta(hours=2)).isoformat())
        second = outbox.record(self.user_dir, 42, ["new"])
        order = [path for path, _ in outbox.pending(self.users_dir)]
        self.assertEqual(order, [first, second])

    def test_retire_preserves_content(self):
        path = outbox.record(self.user_dir, 42, ["irreplaceable"])
        target = outbox.retire(path, "test")
        self.assertIsNotNone(target)
        self.assertIn("irreplaceable", target.read_text(encoding="utf-8"))

    def test_retire_twice_keeps_both_copies(self):
        directory = outbox.outbox_dir(self.user_dir)
        for _ in range(2):
            path = directory / "collide.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"chunks": ["x"], "user_id": 42, "created": "2026-08-02T12:00:00"}',
                            encoding="utf-8")
            outbox.retire(path, "test")
        kept = list((directory / outbox.EXPIRED_DIRNAME).glob("*.json"))
        self.assertEqual(len(kept), 2, "a name collision must not overwrite a kept reply")

    def test_half_written_entry_is_never_picked_up(self):
        """safe_json renames through "<name>.json.tmp"; *.json must not see it."""
        directory = outbox.outbox_dir(self.user_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "20260802_120511_000000_abc123.json.tmp").write_text(
            "half written", encoding="utf-8"
        )
        self.assertEqual(outbox.pending(self.users_dir), [])

    def test_missing_users_dir_is_empty_not_an_error(self):
        self.assertEqual(outbox.pending(self.users_dir / "nope"), [])


class SendQueuesOnFailureTests(_IsolatedUsersDir, unittest.IsolatedAsyncioTestCase):
    """bot.send_chunks_with_retry: what reaches disk, and when."""

    def setUp(self):
        self._start_isolation()
        self._orig_delay = botmod._SEND_RETRY_DELAY
        botmod._SEND_RETRY_DELAY = 0

    def tearDown(self):
        botmod._SEND_RETRY_DELAY = self._orig_delay
        self._stop_isolation()

    def queued(self):
        return outbox.pending(self.users_dir)

    async def test_success_writes_nothing_to_disk(self):
        sent = []

        async def send_fn(chunk):
            sent.append(chunk)

        self.assertTrue(await botmod.send_chunks_with_retry(send_fn, "hello", 42))
        self.assertEqual(sent, ["hello"])
        self.assertEqual(self.queued(), [], "the happy path must not touch the filesystem")

    async def test_queue_lands_under_the_resolved_user_dir(self):
        """The entry must sit where core.users says that user's data lives."""
        async def send_fn(chunk):
            raise NetworkError("connect error")

        await botmod.send_chunks_with_retry(send_fn, "hello", 42)
        expected = outbox.outbox_dir(users_mod.resolve_user_dir(42))
        self.assertTrue(any(expected in path.parents for path, _ in self.queued()))

    async def test_queued_on_the_first_failure_not_the_last(self):
        """A process killed mid retry must still leave the reply on disk."""
        seen_on_first_backoff = []

        async def send_fn(chunk):
            seen_on_first_backoff.append(len(self.queued()))
            raise TimedOut()

        await botmod.send_chunks_with_retry(send_fn, "hello", 42)
        # Attempt 1 sees nothing queued yet; attempt 2 already sees it on disk.
        self.assertEqual(seen_on_first_backoff[0], 0)
        self.assertEqual(seen_on_first_backoff[1], 1)

    async def test_total_failure_reports_safe_and_keeps_the_text(self):
        async def send_fn(chunk):
            raise NetworkError("connect error")

        # True means "not lost": delivery failed but the reply is queued.
        self.assertTrue(await botmod.send_chunks_with_retry(send_fn, "the answer", 42))
        (_, entry), = self.queued()
        self.assertEqual(entry["chunks"], ["the answer"])
        self.assertEqual(entry["sent"], 0)

    async def test_lost_reply_reports_false(self):
        """If the queue write itself fails the caller must learn the reply is gone."""
        async def send_fn(chunk):
            raise NetworkError("connect error")

        orig = outbox.record
        outbox.record = lambda *a, **k: None
        try:
            self.assertFalse(await botmod.send_chunks_with_retry(send_fn, "the answer", 42))
        finally:
            outbox.record = orig

    async def test_partial_delivery_queues_only_the_remainder(self):
        long_text = ("A" * 4000) + "\n\n" + ("B" * 4000)
        chunks = botmod.split_message(long_text)
        self.assertGreater(len(chunks), 1)
        delivered = []

        async def send_fn(chunk):
            if len(delivered) >= 1:
                raise NetworkError("connect error")
            delivered.append(chunk)

        self.assertTrue(await botmod.send_chunks_with_retry(send_fn, long_text, 42))
        (_, entry), = self.queued()
        self.assertEqual(entry["chunks"], chunks)
        self.assertEqual(entry["sent"], 1, "an already-delivered chunk must never be repeated")

    async def test_bad_request_is_not_queued(self):
        async def send_fn(chunk):
            raise BadRequest("message is too long")

        self.assertTrue(await botmod.send_chunks_with_retry(send_fn, "hello", 42))
        self.assertEqual(self.queued(), [], "a permanent error must not be retried forever")

    async def test_cursor_advances_after_a_chunk_recovers(self):
        """The case the on-disk cursor exists for.

        Chunk one fails (entry written with sent=0), then succeeds on retry, then
        chunk two fails for good. If the cursor were not advanced on disk the
        drain would resend chunk one and the user would read it twice.
        """
        long_text = ("A" * 4000) + "\n\n" + ("B" * 4000)
        chunks = botmod.split_message(long_text)
        self.assertEqual(len(chunks), 2)
        calls = {"n": 0}

        async def send_fn(chunk):
            calls["n"] += 1
            if calls["n"] == 1:          # chunk one, first attempt: transient
                raise NetworkError("connect error")
            if chunk == chunks[1]:        # chunk two: never lands
                raise NetworkError("connect error")

        self.assertTrue(await botmod.send_chunks_with_retry(send_fn, long_text, 42))
        (_, entry), = self.queued()
        self.assertEqual(entry["sent"], 1, "a chunk that landed on retry must not be resent")

    async def test_a_dropped_chunk_is_never_redelivered(self):
        """A chunk Telegram refuses outright must not be retried by the drain."""
        long_text = ("A" * 4000) + "\n\n" + ("B" * 4000)
        chunks = botmod.split_message(long_text)

        async def send_fn(chunk):
            if chunk == chunks[0]:
                raise BadRequest("message is too long")
            raise NetworkError("connect error")

        self.assertTrue(await botmod.send_chunks_with_retry(send_fn, long_text, 42))
        (_, entry), = self.queued()
        self.assertEqual(entry["sent"], 1, "the refused chunk must be behind the cursor")

    async def test_recovery_mid_flight_clears_the_entry(self):
        n = {"count": 0}

        async def send_fn(chunk):
            n["count"] += 1
            if n["count"] == 1:
                raise NetworkError("connect error")

        self.assertTrue(await botmod.send_chunks_with_retry(send_fn, "hello", 42))
        self.assertEqual(self.queued(), [], "a reply that lands on retry must leave nothing behind")

    async def test_unclassified_error_is_queued_too(self):
        async def send_fn(chunk):
            raise RuntimeError("something new")

        self.assertTrue(await botmod.send_chunks_with_retry(send_fn, "hello", 42))
        self.assertEqual(len(self.queued()), 1)


class DrainTests(_IsolatedUsersDir, unittest.IsolatedAsyncioTestCase):
    """bot.drain_outbox: redelivery, ordering, give-up, live-turn safety."""

    def setUp(self):
        self._start_isolation()
        self.user_dir = self.users_dir / "42"
        self.user_dir.mkdir()
        botmod._user_locks.clear()
        # The real registry does not contain these test ids, and drain_outbox
        # refuses to message anyone off the allowlist.
        self._orig_allowed = botmod.get_allowed_users
        botmod.get_allowed_users = lambda: [42, 7]

    def tearDown(self):
        botmod.get_allowed_users = self._orig_allowed
        botmod._user_locks.clear()
        self._stop_isolation()

    async def test_never_messages_a_user_off_the_allowlist(self):
        outbox.record(self.user_dir, 42, ["for a stranger"])
        botmod.get_allowed_users = lambda: [99999]
        fake = _FakeBot()

        self.assertEqual(await botmod.drain_outbox(fake, self.users_dir), 0)
        self.assertEqual(fake.sent, [], "a stray entry must not reach an unknown chat")
        kept = list((outbox.outbox_dir(self.user_dir) / outbox.EXPIRED_DIRNAME).glob("*.json"))
        self.assertEqual(len(kept), 1, "and it must still not be destroyed")

    async def test_unconfigured_allowlist_holds_the_reply(self):
        """Empty means "registry unreadable", not "this user is a stranger".

        handle_message rejects everyone in that state, so nothing goes out. But
        the reply is left in the queue for the next sweep rather than retired:
        an unreadable users.json must not cost the user a finished answer.
        """
        outbox.record(self.user_dir, 42, ["hold me"])
        botmod.get_allowed_users = lambda: []
        fake = _FakeBot()

        self.assertEqual(await botmod.drain_outbox(fake, self.users_dir), 0)
        self.assertEqual(fake.sent, [])
        self.assertEqual(len(outbox.pending(self.users_dir)), 1, "still queued, not retired")

        botmod.get_allowed_users = lambda: [42]
        self.assertEqual(await botmod.drain_outbox(fake, self.users_dir), 1)

    async def test_an_announced_reply_is_delivered_however_late(self):
        """A timer must not cancel a promise the user already read."""
        path = outbox.record(self.user_dir, 42, ["the body I promised"])
        old = datetime.now() - timedelta(seconds=outbox.MAX_AGE_SECONDS * 3)
        outbox.update(path, created=old.isoformat(), header_sent=True)
        fake = _FakeBot()

        self.assertEqual(await botmod.drain_outbox(fake, self.users_dir), 1)
        self.assertEqual([t for _, t in fake.sent], ["the body I promised"])

    async def test_header_names_the_day_for_an_older_answer(self):
        path = outbox.record(self.user_dir, 42, ["x"])
        # One second before today started: always yesterday's date, always
        # inside the 24h window, whatever hour the suite runs at.
        yesterday = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
        outbox.update(path, created=yesterday.isoformat())
        fake = _FakeBot()
        await botmod.drain_outbox(fake, self.users_dir)
        header = fake.sent[0][1]
        self.assertIn("written on ", header, "a reply from another day must say which day")

    async def test_delivers_header_then_remaining_chunks(self):
        outbox.record(self.user_dir, 42, ["one", "two"], sent=1)
        fake = _FakeBot()

        self.assertEqual(await botmod.drain_outbox(fake, self.users_dir), 1)
        self.assertEqual([text for _, text in fake.sent][1:], ["two"])
        self.assertIn("Delayed reply", fake.sent[0][1])
        self.assertEqual(fake.sent[0][0], 42)
        self.assertEqual(outbox.pending(self.users_dir), [])

    async def test_header_carries_the_time_the_answer_was_written(self):
        path = outbox.record(self.user_dir, 42, ["x"])
        outbox.update(path, created=datetime.now().replace(hour=12, minute=5).isoformat())
        fake = _FakeBot()
        await botmod.drain_outbox(fake, self.users_dir)
        self.assertIn("12:05", fake.sent[0][1])

    async def test_header_is_sent_once_across_passes(self):
        outbox.record(self.user_dir, 42, ["one", "two"])
        # Header lands, then the first chunk fails: the entry survives.
        failing = _FakeBot()
        original = failing.send_message
        calls = {"n": 0}

        async def flaky(chat_id, text):
            calls["n"] += 1
            if calls["n"] == 2:
                raise NetworkError("still down")
            await original(chat_id=chat_id, text=text)

        failing.send_message = flaky
        await botmod.drain_outbox(failing, self.users_dir)
        self.assertEqual(len(outbox.pending(self.users_dir)), 1)

        good = _FakeBot()
        await botmod.drain_outbox(good, self.users_dir)
        headers = [t for _, t in good.sent if t.startswith("Delayed reply")]
        self.assertEqual(headers, [], "the apology line must not repeat on every pass")
        self.assertEqual([t for _, t in good.sent], ["one", "two"])

    async def test_failure_bumps_attempts_and_keeps_the_entry(self):
        outbox.record(self.user_dir, 42, ["one"])
        await botmod.drain_outbox(_FakeBot(fail_first=99), self.users_dir)
        (_, entry), = outbox.pending(self.users_dir)
        self.assertEqual(entry["attempts"], 1)
        self.assertEqual(entry["sent"], 0)

    async def test_gives_up_after_max_attempts_without_deleting(self):
        path = outbox.record(self.user_dir, 42, ["precious"])
        outbox.update(path, attempts=outbox.MAX_ATTEMPTS)
        fake = _FakeBot()

        self.assertEqual(await botmod.drain_outbox(fake, self.users_dir), 0)
        self.assertEqual(fake.sent, [])
        self.assertEqual(outbox.pending(self.users_dir), [])
        kept = list((outbox.outbox_dir(self.user_dir) / outbox.EXPIRED_DIRNAME).glob("*.json"))
        self.assertEqual(len(kept), 1)
        self.assertIn("precious", kept[0].read_text(encoding="utf-8"))

    async def test_stale_entry_is_retired_without_deleting(self):
        path = outbox.record(self.user_dir, 42, ["ancient"])
        old = datetime.now() - timedelta(seconds=outbox.MAX_AGE_SECONDS + 60)
        outbox.update(path, created=old.isoformat())
        fake = _FakeBot()

        self.assertEqual(await botmod.drain_outbox(fake, self.users_dir), 0)
        self.assertEqual(fake.sent, [], "a day-old answer must not ambush the user")
        kept = list((outbox.outbox_dir(self.user_dir) / outbox.EXPIRED_DIRNAME).glob("*.json"))
        self.assertEqual(len(kept), 1)
        self.assertIn("ancient", kept[0].read_text(encoding="utf-8"))

    async def test_entry_just_inside_the_window_is_still_delivered(self):
        path = outbox.record(self.user_dir, 42, ["still good"])
        recent = datetime.now() - timedelta(seconds=outbox.MAX_AGE_SECONDS - 600)
        outbox.update(path, created=recent.isoformat())
        fake = _FakeBot()
        self.assertEqual(await botmod.drain_outbox(fake, self.users_dir), 1)
        self.assertIn("still good", [t for _, t in fake.sent])

    async def test_skips_a_user_with_a_turn_in_flight(self):
        outbox.record(self.user_dir, 42, ["one"])
        lock = botmod._get_user_lock(42)
        fake = _FakeBot()
        async with lock:
            self.assertEqual(await botmod.drain_outbox(fake, self.users_dir), 0)
        self.assertEqual(fake.sent, [], "must not interleave a stale answer into a live turn")
        # Freed once the turn is done.
        self.assertEqual(await botmod.drain_outbox(fake, self.users_dir), 1)

    async def test_successful_drain_clears_the_pending_marker(self):
        marker = self.user_dir / "pending_message.json"
        marker.write_text('{"user_id": 42, "text": "hi", "received": "2026-08-02T12:00:00"}',
                          encoding="utf-8")
        outbox.record(self.user_dir, 42, ["the answer"])

        await botmod.drain_outbox(_FakeBot(), self.users_dir)
        self.assertFalse(
            marker.exists(),
            "'your message was lost' must not follow the answer it stood in for",
        )

    async def test_failed_drain_leaves_the_pending_marker(self):
        marker = self.user_dir / "pending_message.json"
        marker.write_text('{"user_id": 42, "text": "hi", "received": "2026-08-02T12:00:00"}',
                          encoding="utf-8")
        outbox.record(self.user_dir, 42, ["the answer"])

        await botmod.drain_outbox(_FakeBot(fail_first=99), self.users_dir)
        self.assertTrue(marker.exists())

    async def test_one_stuck_user_does_not_block_another(self):
        other = self.users_dir / "7"
        other.mkdir()
        stuck = outbox.record(self.user_dir, 42, ["blocked"])
        outbox.update(stuck, created=(datetime.now() - timedelta(hours=1)).isoformat())
        outbox.record(other, 7, ["fine"])

        lock = botmod._get_user_lock(42)
        fake = _FakeBot()
        async with lock:
            await botmod.drain_outbox(fake, self.users_dir)
        self.assertIn("fine", [t for _, t in fake.sent])

    async def test_empty_outbox_is_a_cheap_noop(self):
        fake = _FakeBot()
        self.assertEqual(await botmod.drain_outbox(fake, self.users_dir), 0)
        self.assertEqual(fake.sent, [])


class StartupWiringTests(unittest.TestCase):
    """The order and the sweep are load bearing, so pin them in source.

    Both are startup-only behaviour that no unit test can reach without booting
    a real Application against Telegram. A source guard is cheap and catches
    the two ways this silently stops working: the drain moving after
    recover_pending_messages (so the user is told to resend an answer that is
    about to arrive), and the sweep task losing its module-level reference (so
    asyncio collects it and redelivery stops until the next restart).
    """

    SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")

    def test_drain_runs_before_pending_recovery(self):
        drain = self.SOURCE.index("redelivered = await drain_outbox(application.bot)")
        recover = self.SOURCE.index("await recover_pending_messages(application.bot)")
        self.assertLess(
            drain, recover,
            "drain first, or the 'lost message' notice contradicts the answer landing",
        )

    def test_sweep_task_reference_is_kept(self):
        self.assertIn("_outbox_sweep_task = asyncio.create_task(_outbox_sweep_loop(", self.SOURCE)
        self.assertIn("global _outbox_sweep_task", self.SOURCE)

    def test_both_handlers_keep_the_marker_when_the_reply_is_lost(self):
        """The return value is only worth anything if the callers act on it.

        Both handlers clear the pending-message marker in a ``finally`` block,
        which used to run unconditionally. That is the second half of the
        original failure: the answer was destroyed AND so was the record that a
        turn had been interrupted, so even the startup recovery path had
        nothing left to tell the user.
        """
        self.assertNotIn(
            "finally:\n        clear_pending_message(user_id)", self.SOURCE,
            "a handler still clears the marker unconditionally",
        )
        self.assertEqual(
            self.SOURCE.count("reply_is_safe = await send_chunks_with_retry"), 2,
            "both handlers must capture whether the reply survived",
        )
        self.assertEqual(
            self.SOURCE.count("if reply_is_safe:\n            clear_pending_message(user_id)"), 2,
            "both handlers must clear the marker only when the reply is safe",
        )
        self.assertEqual(
            self.SOURCE.count("reply_is_safe = True"), 2,
            "the default must stay True so a cancelled turn still clears as before",
        )

    def test_every_queueing_test_module_isolates_both_user_dir_roots(self):
        """A test that queues a reply must not write into the real user tree.

        Measured, not reasoned: before the isolation went in, running
        tests.test_send_retry left two fake replies in data/users/1/outbox,
        where the live drain would have tried to deliver them to Telegram user
        1. Reading the module could not show that; listing the directory
        afterwards could. This guard is the cheap standing version of that
        check, and it also catches a third module added later without it.
        """
        # Matches the redirect itself, not the save/restore lines around it: a
        # bare "USERS_DATA_DIR" scan still passes after the redirect is deleted,
        # so it could not detect the very bug it was written for.
        redirect = re.compile(
            r"users_mod\.USERS_DATA_DIR\s*=\s*(Path\(self\._tmp\.name\)|self\.users_dir)"
        )
        offenders = []
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            text = path.read_text(encoding="utf-8")
            queues = "send_chunks_with_retry" in text or "outbox.record" in text
            if queues and not redirect.search(text):
                offenders.append(path.name)
        self.assertEqual(
            offenders, [],
            "these modules queue replies without pointing core.users.USERS_DATA_DIR "
            "at a temp dir, so they write into the real data/users tree",
        )


class EndToEndTests(_IsolatedUsersDir, unittest.IsolatedAsyncioTestCase):
    """The outage replayed: dead endpoint, then recovery."""

    def setUp(self):
        self._start_isolation()
        (self.users_dir / "8044898180").mkdir()
        self._orig_delay = botmod._SEND_RETRY_DELAY
        botmod._SEND_RETRY_DELAY = 0
        botmod._user_locks.clear()
        self._orig_allowed = botmod.get_allowed_users
        botmod.get_allowed_users = lambda: [8044898180]

    def tearDown(self):
        botmod._SEND_RETRY_DELAY = self._orig_delay
        botmod.get_allowed_users = self._orig_allowed
        botmod._user_locks.clear()
        self._stop_isolation()

    async def test_outage_then_restart_delivers_the_answer(self):
        answer = "Here. Both halves are done. " * 20

        async def dead_api(chunk):
            raise NetworkError("All connection attempts failed")

        # The endpoint is gone, every attempt fails.
        self.assertTrue(await botmod.send_chunks_with_retry(dead_api, answer, 8044898180))

        # The bot is back up and drains on startup.
        fake = _FakeBot()
        self.assertEqual(await botmod.drain_outbox(fake, self.users_dir), 1)

        body = "".join(text for _, text in fake.sent[1:])
        self.assertEqual(body, answer)
        self.assertEqual(outbox.pending(self.users_dir), [])

    async def test_kill_mid_retry_still_delivers_on_restart(self):
        """No graceful exit at all: the queue write is what saves the reply."""
        answer = "the finished answer"

        async def killed_mid_retry(chunk):
            raise NetworkError("All connection attempts failed")

        task = asyncio.create_task(
            botmod.send_chunks_with_retry(killed_mid_retry, answer, 8044898180)
        )
        # Let the first attempt fail and the queue write land, then kill the
        # coroutine the way SIGKILL would kill the process: no cleanup runs.
        botmod._SEND_RETRY_DELAY = 5
        await asyncio.sleep(0)
        for _ in range(20):
            if outbox.pending(self.users_dir):
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        fake = _FakeBot()
        self.assertEqual(await botmod.drain_outbox(fake, self.users_dir), 1)
        self.assertIn(answer, [text for _, text in fake.sent])


if __name__ == "__main__":
    unittest.main()
