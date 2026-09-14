"""The admin's usage view has to answer "compared with whom".

Opened on 14 Sep 2026 to compare the people sharing this machine, /usage
showed one person: the reader. Nothing was broken. The ledger had started
23 hours earlier and the only other recently active person had not spoken
since, so ``summarise_everyone`` returned a single row and both surfaces
hid the whole section rather than print a list of one.

The fault is that the silence had two meanings -- nobody spent anything, and
nobody was measured -- and the view chose neither. So the roster is what gets
listed rather than the ledgers that happen to exist, a person who consumed
nothing says so on their own line, and the section carries the date counting
began, because a zero nobody can date is not a reading.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

import bot  # noqa: E402
import miniapp.server as server  # noqa: E402
from core import usage, users  # noqa: E402

PAGE = ROOT / "miniapp" / "static" / "index.html"

REGISTRY = {
    "7": {"display_name": "Admin One", "role": "admin"},
    "8": {"display_name": "Quiet One", "role": "user"},
}


@contextmanager
def isolated():
    """Ledgers in a temp tree: this suite must never write the live one."""
    with tempfile.TemporaryDirectory(prefix="mom-usage-compare-") as td, ExitStack() as st:
        root = Path(td)
        st.enter_context(patch.object(users, "USERS_DATA_DIR", root / "users"))
        st.enter_context(patch.object(usage, "USAGE_DIR", root / "usage"))
        st.enter_context(patch.object(usage, "CLAUDE_LIMITS_FILE", root / "usage" / "limits.json"))
        usage.codex_cache_clear()
        yield root
        usage.codex_cache_clear()


SOLO = {"7": {"display_name": "Admin One", "role": "admin"}}


def _ledger(user_id: int, *timestamps: int) -> None:
    """Turns at chosen times, because half of this is about the window."""
    path = usage.ledger_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for ts in timestamps:
            f.write(json.dumps({"ts": ts, "provider": "claude-cli",
                                "model": "claude-opus-5", "input_tokens": 100,
                                "output_tokens": 20, "ok": True}) + "\n")


def _update(user_id: int, text: str = "/usage"):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id),
                           message=message)


def _said(update) -> str:
    return "\n".join(call.args[0] for call in update.message.reply_text.call_args_list)


class RosterTests(unittest.TestCase):
    """core.usage: who the everyone view is allowed to leave out."""

    def test_a_roster_member_who_spent_nothing_is_still_a_row(self):
        with isolated():
            usage.record_turn(7, provider="claude-cli", model="claude-opus-5", input_tokens=10)
            everyone = usage.summarise_everyone(7, roster=REGISTRY.keys())
        self.assertEqual(sorted(everyone), ["7", "8"])
        self.assertEqual(everyone["8"]["turns"], 0)

    def test_consumption_by_someone_off_the_roster_is_never_hidden(self):
        """A removed or renumbered person still spent the subscription."""
        with isolated():
            usage.record_turn(9, provider="codex-cli", model="gpt-6-astra", input_tokens=10)
            everyone = usage.summarise_everyone(7, roster=("7",))
        self.assertEqual(sorted(everyone), ["7", "9"])
        self.assertEqual(everyone["9"]["turns"], 1)

    def test_a_junk_roster_entry_costs_that_entry_and_nothing_else(self):
        with isolated():
            usage.record_turn(7, provider="claude-cli", model="claude-opus-5")
            everyone = usage.summarise_everyone(7, roster=("8", "not-an-id", None))
        self.assertEqual(sorted(everyone), ["7", "8"])

    def test_control_with_no_roster_only_real_ledgers_are_listed(self):
        with isolated():
            usage.record_turn(7, provider="claude-cli", model="claude-opus-5")
            everyone = usage.summarise_everyone(7)
        self.assertEqual(sorted(everyone), ["7"])

    def test_accounting_started_is_the_oldest_turn_on_record(self):
        now = int(time.time())
        with isolated():
            usage.record_turn(7, provider="claude-cli", model="claude-opus-5")
            older = usage.ledger_path(8)
            older.parent.mkdir(parents=True, exist_ok=True)
            older.write_text(json.dumps({"ts": now - 5 * 86400, "model": "m",
                                         "input_tokens": 1}) + "\n")
            self.assertEqual(usage.accounting_started(), now - 5 * 86400)

    def test_accounting_started_is_none_when_nothing_was_ever_recorded(self):
        with isolated():
            self.assertIsNone(usage.accounting_started())

    def test_the_start_date_never_reaches_past_the_window_that_was_read(self):
        """Retention is 90 days and a view is 7, so the oldest turn on record
        is routinely older than anything the view looked at. Named anyway, it
        says the silence covers weeks nobody read."""
        now = int(time.time())
        with isolated():
            _ledger(7, now - 60 * 86400, now)
            _ledger(8, now - 30 * 86400)
            self.assertIsNone(usage.accounting_started(7))
            self.assertEqual(usage.accounting_started(), now - 60 * 86400)

    def test_a_count_that_began_inside_the_window_is_still_named(self):
        now = int(time.time())
        with isolated():
            _ledger(7, now - 2 * 86400)
            self.assertEqual(usage.accounting_started(7), now - 2 * 86400)


class TelegramViewTests(unittest.TestCase):
    """/usage, the surface the complaint came from."""

    def _run(self, user_id: int, admin: bool, registry: dict = REGISTRY) -> str:
        update = _update(user_id)
        blank = {"unavailable": True, "reason": "not read in this test"}
        with (patch("bot.get_allowed_users", return_value={user_id}),
              patch("bot.is_admin", return_value=admin),
              patch.object(users, "list_users", return_value=registry),
              patch.object(usage, "meters", return_value={"claude": blank, "codex": blank})):
            asyncio.run(bot.usage_command(update, None))
        return _said(update)

    def test_the_admin_sees_a_person_who_consumed_nothing(self):
        with isolated():
            usage.record_turn(7, provider="claude-cli", model="claude-opus-5",
                              input_tokens=100, output_tokens=20)
            said = self._run(7, admin=True)
        self.assertIn("Everyone, last 7 days", said)
        self.assertIn("Quiet One: nothing recorded yet", said)
        self.assertIn("Admin One:", said)

    def test_a_zero_row_is_dated_so_it_cannot_read_as_spent_nothing_ever(self):
        with isolated():
            usage.record_turn(7, provider="claude-cli", model="claude-opus-5", input_tokens=100)
            said = self._run(7, admin=True)
        self.assertIn("counting since", said)

    def test_the_comparison_puts_each_person_on_one_line(self):
        with isolated():
            usage.record_turn(7, provider="claude-cli", model="claude-opus-5",
                              input_tokens=100, output_tokens=20, list_cost_usd=1.5)
            said = self._run(7, admin=True)
        line = [row for row in said.splitlines() if row.startswith("  Admin One:")]
        self.assertEqual(len(line), 1, said)
        for figure in ("1 turns", "100 tokens in", "20 out", "$1.50"):
            with self.subTest(figure=figure):
                self.assertIn(figure, line[0])

    def test_an_old_record_does_not_claim_the_silence_covers_unread_time(self):
        now = int(time.time())
        with isolated():
            _ledger(7, now - 60 * 86400, now)
            _ledger(8, now - 30 * 86400)
            said = self._run(7, admin=True)
        self.assertIn("Quiet One: nothing recorded yet", said)
        self.assertNotIn(f"counting since {bot._format_day(now - 60 * 86400)}", said)

    def test_the_only_person_on_the_machine_is_not_compared_with_themselves(self):
        """A list of one is the block at the top of the same message again."""
        with isolated():
            usage.record_turn(7, provider="claude-cli", model="claude-opus-5", input_tokens=100)
            said = self._run(7, admin=True, registry=SOLO)
        self.assertNotIn("Everyone", said)
        self.assertIn("Your usage, last 7 days", said)

    def test_control_a_spender_off_the_roster_brings_the_section_back(self):
        """The guard is "more than the reader", not "more than one registry
        entry": a person dropped from the registry still spent the
        subscription and still has to be listed."""
        with isolated():
            usage.record_turn(7, provider="claude-cli", model="claude-opus-5", input_tokens=100)
            usage.record_turn(9, provider="codex-cli", model="gpt-6-astra", input_tokens=100)
            said = self._run(7, admin=True, registry=SOLO)
        self.assertIn("Everyone, last 7 days", said)
        self.assertIn("user 9:", said)

    def test_the_admin_read_does_not_stall_the_other_chats(self):
        """Every ledger on the machine, read on the event loop, is every other
        person's turn waiting on this one's disk. Measured, not assumed."""
        with isolated():
            usage.record_turn(7, provider="claude-cli", model="claude-opus-5", input_tokens=100)

            real = usage.summarise_everyone

            def slow(days, roster=()):
                time.sleep(0.3)
                return real(days, roster=roster)

            async def drive():
                """Tick all the way through the read, so the measurement
                covers the blocking region rather than the moment before it."""
                update = _update(7)
                blank = {"unavailable": True, "reason": "not read in this test"}
                worst, ticks = 0.0, 0
                with (patch("bot.get_allowed_users", return_value={7}),
                      patch("bot.is_admin", return_value=True),
                      patch.object(usage, "summarise_everyone", side_effect=slow),
                      patch.object(users, "list_users", return_value=REGISTRY),
                      patch.object(usage, "meters", return_value={"claude": blank, "codex": blank})):
                    task = asyncio.ensure_future(bot.usage_command(update, None))
                    while not task.done():
                        started = time.perf_counter()
                        await asyncio.sleep(0.01)
                        worst = max(worst, time.perf_counter() - started - 0.01)
                        ticks += 1
                    await task
                return worst, ticks, _said(update)

            waited, ticks, said = asyncio.run(drive())
        self.assertIn("Everyone, last 7 days", said)
        self.assertGreater(ticks, 0, "nothing was measured")
        self.assertLess(waited, 0.15, "the admin read blocked the event loop")

    def test_the_only_row_being_someone_else_is_still_a_comparison(self):
        """One row that is not the reader is a comparison, not an echo: the
        guard has to ask who the row belongs to, not just how many there are."""
        with isolated():
            usage.record_turn(9, provider="codex-cli", model="gpt-6-astra", input_tokens=100)
            said = self._run(7, admin=True, registry={})
        self.assertIn("Everyone, last 7 days", said)
        self.assertIn("user 9:", said)

    def test_an_empty_registry_does_not_print_a_heading_over_nothing(self):
        """users.json unreadable and no ledger yet: a heading with no rows
        under it is the same silence with no reading attached."""
        with isolated():
            said = self._run(7, admin=True, registry={})
        self.assertNotIn("Everyone", said)

    def test_control_an_ordinary_user_is_told_nothing_about_anyone_else(self):
        with isolated():
            usage.record_turn(7, provider="claude-cli", model="claude-opus-5", input_tokens=100)
            said = self._run(8, admin=False)
        self.assertNotIn("Everyone", said)
        self.assertNotIn("Admin One", said)


class MiniAppViewTests(unittest.TestCase):
    """The same two rules on the other surface, which must not drift."""

    def test_the_admin_payload_carries_the_idle_person_and_the_start_date(self):
        with isolated():
            usage.record_turn(7, provider="claude-cli", model="claude-opus-5", input_tokens=100)
            with (patch.object(usage, "meters", return_value={}),
                  patch.object(server, "_load_users", return_value=REGISTRY)):
                payload = server.get_usage(days=7, user={"_id": "7", "_profile": {"role": "admin"}})
        rows = {row["name"]: row for row in payload["everyone"]}
        self.assertEqual(payload["everyone"][0]["name"], "Admin One")
        self.assertEqual(rows["Quiet One"]["turns"], 0)
        self.assertTrue(payload["counting_since"])

    def test_control_an_ordinary_user_gets_no_roster_at_all(self):
        with isolated():
            usage.record_turn(7, provider="claude-cli", model="claude-opus-5", input_tokens=100)
            with patch.object(usage, "meters", return_value={}):
                payload = server.get_usage(days=7, user={"_id": "8", "_profile": {"role": "user"}})
        self.assertIsNone(payload["everyone"])
        self.assertIsNone(payload.get("counting_since"))

    def test_the_solo_admin_gets_no_roster_to_compare_with(self):
        with isolated():
            usage.record_turn(7, provider="claude-cli", model="claude-opus-5", input_tokens=100)
            with (patch.object(usage, "meters", return_value={}),
                  patch.object(server, "_load_users", return_value=SOLO)):
                payload = server.get_usage(days=7, user={"_id": "7", "_profile": {"role": "admin"}})
        self.assertIsNone(payload["everyone"])
        self.assertIsNone(payload.get("counting_since"))

    def test_the_payload_start_date_is_bounded_by_the_window_too(self):
        now = int(time.time())
        with isolated():
            _ledger(7, now - 60 * 86400, now)
            _ledger(8, now - 30 * 86400)
            with (patch.object(usage, "meters", return_value={}),
                  patch.object(server, "_load_users", return_value=REGISTRY)):
                payload = server.get_usage(days=7, user={"_id": "7", "_profile": {"role": "admin"}})
        self.assertEqual(len(payload["everyone"]), 2)
        self.assertIsNone(payload["counting_since"])

    def test_the_page_renders_both_the_zero_row_and_the_date(self):
        """A string check, because nothing in this repo runs a browser."""
        source = PAGE.read_text(encoding="utf-8")
        self.assertIn("nothing recorded yet", source)
        self.assertIn("data.counting_since", source)


if __name__ == "__main__":
    unittest.main()
