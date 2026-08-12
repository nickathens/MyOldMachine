"""/remember routes through the observation pipeline; the memories.json pile
is retired (memory unification, ported from the production bot 2026-08-12).

What must hold:
- /remember stores a high-importance factual observation via the real
  MemoryManager (in-process, never argv), and the reply is honest about
  saved vs corroborated vs NOT stored.
- /memories reads the observation log; /forget explains the append-only
  correction path instead of deleting by number.
- The "### Persistent Memories" prompt section can never resurrect, even
  when a legacy memories.json is planted on disk.
- migrate_memories_piles folds legacy piles into observations exactly once,
  preserves the original file under a .migrated name, and leaves the pile in
  place for retry when every entry fails to convert.

Every path is a tmp tree. Both user-dir roots (bot.USERS_DIR and
core.users.USERS_DATA_DIR) are patched to the same tmp dir, per the
two-roots rule: a test that patches only one writes into the real
data/users tree, where a live sweep can reach a real chat.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

import bot as botmod  # noqa: E402
import core.users as users_mod  # noqa: E402
from core.memory import MemoryManager  # noqa: E402

# Distinct ids per test class: core.session caches SessionManager per user id,
# so reusing an id across tests would pin the first test's tmp dir.
_UID_COUNTER = iter(range(910_000_000, 910_000_999))


def make_update(user_id: int, text: str):
    replies: list[str] = []

    async def reply_text(msg, **kwargs):
        replies.append(msg)

    message = types.SimpleNamespace(text=text, reply_text=reply_text, chat=None)
    update = types.SimpleNamespace(
        message=message,
        effective_user=types.SimpleNamespace(id=user_id, first_name="T"),
    )
    return update, replies


class TmpMemoryFixture(unittest.TestCase):
    def setUp(self):
        self.uid = next(_UID_COUNTER)
        self._tmp = TemporaryDirectory()
        self.data = Path(self._tmp.name) / "data"
        self.users_dir = self.data / "users"
        self.users_dir.mkdir(parents=True)
        self.mm = MemoryManager(self.data)
        for p in (
            patch.object(botmod, "_memory_manager", self.mm),
            patch.object(botmod, "DATA_DIR", self.data),
            patch.object(botmod, "USERS_DIR", self.users_dir),
            patch.object(users_mod, "USERS_DATA_DIR", self.users_dir),
            patch.object(botmod, "get_allowed_users",
                         lambda uid=None: [self.uid]),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def obs_text(self, uid=None) -> str:
        f = (self.data / "memory" / "people" / str(uid or self.uid)
             / "observations.md")
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def obs_lines(self, uid=None) -> list[str]:
        return [ln for ln in self.obs_text(uid).split("\n")
                if ln.startswith("[")]


class RememberHandlerTests(TmpMemoryFixture):
    def _remember(self, text: str):
        update, replies = make_update(self.uid, text)
        asyncio.run(botmod.remember_command(update, None))
        return replies

    def test_remember_saves_factual_high_importance_observation(self):
        replies = self._remember("/remember The studio monitor is a Neumann KH 120")
        lines = self.obs_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("The studio monitor is a Neumann KH 120", lines[0])
        self.assertIn("(factual)", lines[0])
        self.assertIn("importance:8", lines[0])
        self.assertEqual(len(replies), 1)
        self.assertIn("Saved to long-term memory", replies[0])

    def test_remember_same_fact_corroborates_instead_of_duplicating(self):
        self._remember("/remember The studio monitor is a Neumann KH 120")
        replies = self._remember("/remember The studio monitor is a Neumann KH 120")
        self.assertEqual(len(self.obs_lines()), 1)
        self.assertIn("Already known", replies[0])

    def test_remember_collapses_newlines_to_one_log_line(self):
        # The observation log is line-oriented; a raw newline would split the
        # fact into continuation lines that every parser silently drops.
        self._remember("/remember line one\nline two\n\nline three")
        lines = self.obs_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("line one line two line three", lines[0])

    def test_remember_flag_shaped_content_is_stored_verbatim(self):
        # In-process API, never argv: content that looks like CLI flags or
        # shell must land as inert text.
        hostile = "--type behavioral --importance 1 ; rm -rf / #"
        self._remember(f"/remember {hostile}")
        self.assertIn(hostile, self.obs_text())
        self.assertIn("importance:8", self.obs_lines()[0])

    def test_remember_failure_reply_is_honest(self):
        with patch.object(self.mm, "add_observation",
                          side_effect=OSError("disk full")):
            replies = self._remember("/remember something important")
        self.assertIn("NOT stored", replies[0])
        self.assertEqual(self.obs_lines(), [])

    def test_remember_without_manager_says_not_initialized(self):
        with patch.object(botmod, "_memory_manager", None):
            replies = self._remember("/remember anything")
        self.assertIn("not initialized", replies[0])

    def test_remember_empty_shows_usage(self):
        replies = self._remember("/remember")
        self.assertIn("Usage", replies[0])
        self.assertEqual(self.obs_lines(), [])


class MemoriesAndForgetTests(TmpMemoryFixture):
    def test_memories_shows_recent_observations(self):
        self.mm.add_observation(self.uid, "factual", "Prefers dark UI themes",
                                importance=8, use_semantic=False)
        update, replies = make_update(self.uid, "/memories")
        asyncio.run(botmod.memories_command(update, None))
        self.assertTrue(any("Prefers dark UI themes" in r for r in replies))

    def test_memories_empty_points_at_remember(self):
        update, replies = make_update(self.uid, "/memories")
        asyncio.run(botmod.memories_command(update, None))
        self.assertIn("/remember", replies[0])

    def test_forget_explains_append_only_and_deletes_nothing(self):
        self.mm.add_observation(self.uid, "factual", "A fact to keep",
                                importance=8, use_semantic=False)
        update, replies = make_update(self.uid, "/forget 1")
        asyncio.run(botmod.forget_command(update, None))
        self.assertIn("append-only", replies[0])
        self.assertIn("A fact to keep", self.obs_text())


class GetAllObservationsLimitTests(TmpMemoryFixture):
    def test_limit_none_returns_every_line(self):
        # /status counts with limit=None; the old slice lines[-limit:] would
        # raise TypeError on None, and the default of 50 would undercount.
        # Fully disjoint alphabetic vocabulary per line: the dedup keyword
        # extractor only sees [a-zA-Z]{3,} runs (digits vanish), and any
        # shared words would make the lexical pass (correctly) fold lines.
        import itertools
        words = ["".join(t) for t in itertools.product("abcdefghijklmnop",
                                                       repeat=3)]
        for i in range(60):
            self.mm.add_observation(
                self.uid, "factual",
                " ".join(words[i * 5:(i + 1) * 5]),
                importance=5, use_semantic=False)
        self.assertEqual(len(self.mm.get_all_observations(self.uid, limit=None)), 60)
        self.assertEqual(len(self.mm.get_all_observations(self.uid, limit=20)), 20)


class PromptPileNeverResurrects(TmpMemoryFixture):
    def test_planted_pile_never_reaches_the_prompt(self):
        # Even with a legacy pile on disk (e.g. a failed migration rename),
        # the prompt builder must be pile-blind.
        sentinel = "UNIQUE-PILE-SENTINEL-93b1"
        pile_dir = self.users_dir / str(self.uid)
        pile_dir.mkdir(parents=True, exist_ok=True)
        (pile_dir / "memories.json").write_text(
            json.dumps([{"content": sentinel, "timestamp": "2026-01-01T00:00:00"}]),
            encoding="utf-8")
        prompt = botmod.build_system_prompt(self.uid)
        self.assertNotIn(sentinel, prompt)
        self.assertNotIn("Persistent Memories", prompt)

    def test_remembered_fact_reaches_the_prompt_as_observation(self):
        # The replacement path: a saved fact is visible on the very next turn
        # through the unreflected-observations section.
        self.mm.add_observation(self.uid, "factual",
                                "Ships every release on a Thursday",
                                importance=8, use_semantic=False)
        prompt = botmod.build_system_prompt(self.uid)
        self.assertIn("Ships every release on a Thursday", prompt)


class MigrationTests(TmpMemoryFixture):
    def _plant_pile(self, uid: int, entries) -> Path:
        d = self.users_dir / str(uid)
        d.mkdir(parents=True, exist_ok=True)
        pile = d / "memories.json"
        pile.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        return pile

    def test_migrates_entries_and_retires_pile(self):
        pile = self._plant_pile(self.uid, [
            {"content": "Runs a small recording studio",
             "timestamp": "2026-03-04T10:00:00"},
            "bare string legacy entry",
            {"content": "   "},  # blank content is skipped, not crashed on
        ])
        botmod.migrate_memories_piles()
        text = self.obs_text()
        self.assertIn("Runs a small recording studio (saved 2026-03-04)", text)
        self.assertIn("bare string legacy entry", text)
        self.assertEqual(len(self.obs_lines()), 2)
        self.assertFalse(pile.exists())
        migrated = pile.parent / "memories.json.migrated"
        self.assertTrue(migrated.exists())
        self.assertIn("Runs a small recording studio",
                      migrated.read_text(encoding="utf-8"))

    def test_second_run_is_a_noop(self):
        self._plant_pile(self.uid, [{"content": "one fact"}])
        botmod.migrate_memories_piles()
        botmod.migrate_memories_piles()
        self.assertEqual(len(self.obs_lines()), 1)

    def test_corrupt_pile_is_retired_without_observations(self):
        d = self.users_dir / str(self.uid)
        d.mkdir(parents=True, exist_ok=True)
        pile = d / "memories.json"
        pile.write_text("{not json", encoding="utf-8")
        botmod.migrate_memories_piles()
        self.assertFalse(pile.exists())
        self.assertTrue((d / "memories.json.migrated").exists())
        self.assertEqual(self.obs_lines(), [])

    def test_total_conversion_failure_leaves_pile_for_retry(self):
        pile = self._plant_pile(self.uid, [{"content": "a fact"}])
        with patch.object(self.mm, "add_observation",
                          side_effect=OSError("disk full")):
            botmod.migrate_memories_piles()
        self.assertTrue(pile.exists())
        self.assertFalse((pile.parent / "memories.json.migrated").exists())

    def test_non_numeric_user_dir_is_skipped(self):
        d = self.users_dir / "not-a-user-id"
        d.mkdir(parents=True)
        (d / "memories.json").write_text("[]", encoding="utf-8")
        botmod.migrate_memories_piles()  # must not raise
        self.assertTrue((d / "memories.json").exists())

    def test_no_manager_is_a_noop(self):
        pile = self._plant_pile(self.uid, [{"content": "a fact"}])
        with patch.object(botmod, "_memory_manager", None):
            botmod.migrate_memories_piles()
        self.assertTrue(pile.exists())


if __name__ == "__main__":
    unittest.main()
