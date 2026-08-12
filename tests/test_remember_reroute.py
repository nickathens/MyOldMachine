"""/remember pins a ground-truth anchor; the memories.json pile is retired
(memory unification, ported from the production bot 2026-08-12).

What must hold:
- /remember pins the fact as an anchor via the real MemoryManager
  (in-process, never argv), and the reply is honest about saved vs NOT
  stored.
- /memories lists pinned facts by number plus the observation log; /forget
  <number> removes a pinned fact and nothing else.
- The "### Persistent Memories" prompt section can never resurrect, even
  when a legacy memories.json is planted on disk.
- A remembered fact stays in the prompt *permanently* — the property the
  retired pile actually provided. Routing /remember to a plain observation
  instead looks equivalent on the next turn and is not: the unreflected
  window is the last 10 lines, reflection then hides the line, and the
  person model that is supposed to carry it forward is truncated to 2800
  chars. The DurabilityTests below pin all three.
- migrate_memories_piles folds legacy piles into observations exactly once,
  preserves the original file under a .migrated name, and leaves the pile in
  place for retry when every entry fails to convert. (Migrated entries stay
  observations on purpose: they are install-time leftovers of unknown age,
  e.g. a machine-spec line that only rots, so they decay rather than pin.)

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

    def anchor_texts(self, uid=None) -> list[str]:
        return [a["text"] for a in self.mm.load_anchors(uid or self.uid)]


class RememberHandlerTests(TmpMemoryFixture):
    def _remember(self, text: str):
        update, replies = make_update(self.uid, text)
        asyncio.run(botmod.remember_command(update, None))
        return replies

    def test_remember_pins_the_fact_as_an_anchor(self):
        replies = self._remember("/remember The studio monitor is a Neumann KH 120")
        self.assertEqual(self.anchor_texts(),
                         ["The studio monitor is a Neumann KH 120"])
        self.assertEqual(len(replies), 1)
        self.assertIn("Saved to long-term memory", replies[0])
        # An anchor is the point; it must not also be logged as an
        # observation, which would inject the same fact into the prompt twice.
        self.assertEqual(self.obs_lines(), [])

    def test_remember_same_fact_twice_does_not_duplicate(self):
        # add_anchor suffixes a colliding generated slug rather than
        # replacing, so without a text-level check this pins the fact twice.
        self._remember("/remember The studio monitor is a Neumann KH 120")
        replies = self._remember("/remember the STUDIO monitor is a Neumann KH 120")
        self.assertEqual(len(self.anchor_texts()), 1)
        self.assertIn("Already pinned", replies[0])

    def test_two_long_facts_sharing_a_prefix_both_survive(self):
        # The anchor slug truncates at 40 chars: keying dedup on a
        # text-derived id would silently overwrite the first fact here.
        shared = "The mastering chain for the Allwyn films "
        self._remember(f"/remember {shared}starts with a Neumann console")
        self._remember(f"/remember {shared}ends with a Weiss limiter")
        self.assertEqual(len(self.anchor_texts()), 2)

    def test_remember_collapses_newlines_to_one_line(self):
        # Anchors are stored one per line and the parse regex depends on it;
        # a raw newline would split the fact into unparseable fragments.
        self._remember("/remember line one\nline two\n\nline three")
        self.assertEqual(self.anchor_texts(), ["line one line two line three"])

    def test_remember_flag_shaped_content_is_stored_verbatim(self):
        # In-process API, never argv: content that looks like CLI flags or
        # shell must land as inert text.
        hostile = "--type behavioral --importance 1 ; rm -rf / #"
        self._remember(f"/remember {hostile}")
        self.assertEqual(self.anchor_texts(), [hostile])

    def test_remember_failure_reply_is_honest(self):
        with patch.object(self.mm, "add_anchor",
                          side_effect=OSError("disk full")):
            replies = self._remember("/remember something important")
        self.assertIn("NOT stored", replies[0])
        self.assertEqual(self.anchor_texts(), [])

    def test_remember_without_manager_says_not_initialized(self):
        with patch.object(botmod, "_memory_manager", None):
            replies = self._remember("/remember anything")
        self.assertIn("not initialized", replies[0])

    def test_remember_empty_shows_usage(self):
        replies = self._remember("/remember")
        self.assertIn("Usage", replies[0])
        self.assertEqual(self.obs_lines(), [])


class MemoriesAndForgetTests(TmpMemoryFixture):
    def _memories(self):
        update, replies = make_update(self.uid, "/memories")
        asyncio.run(botmod.memories_command(update, None))
        return replies

    def _forget(self, text: str):
        update, replies = make_update(self.uid, text)
        asyncio.run(botmod.forget_command(update, None))
        return replies

    def test_memories_shows_pinned_facts_and_observations(self):
        self.mm.add_anchor(self.uid, "Mixes on Neumann KH 120")
        self.mm.add_observation(self.uid, "factual", "Prefers dark UI themes",
                                importance=8, use_semantic=False)
        joined = "\n".join(self._memories())
        self.assertIn("1. Mixes on Neumann KH 120", joined)
        self.assertIn("Prefers dark UI themes", joined)

    def test_memories_empty_points_at_remember(self):
        self.assertIn("/remember", self._memories()[0])

    def test_forget_by_number_removes_that_pinned_fact(self):
        self.mm.add_anchor(self.uid, "First pinned fact")
        self.mm.add_anchor(self.uid, "Second pinned fact")
        replies = self._forget("/forget 1")
        self.assertIn("Forgot: First pinned fact", replies[0])
        self.assertEqual(self.anchor_texts(), ["Second pinned fact"])

    def test_forget_numbering_matches_the_memories_listing(self):
        # The number the user types comes off the /memories listing; both
        # sides must read load_anchors in the same order or /forget deletes
        # the wrong fact.
        for t in ("alpha fact", "beta fact", "gamma fact"):
            self.mm.add_anchor(self.uid, t)
        listing = "\n".join(self._memories())
        self.assertIn("2. beta fact", listing)
        self._forget("/forget 2")
        self.assertEqual(self.anchor_texts(), ["alpha fact", "gamma fact"])

    def test_forget_out_of_range_removes_nothing(self):
        self.mm.add_anchor(self.uid, "Only fact")
        replies = self._forget("/forget 7")
        self.assertIn("no pinned fact number 7", replies[0])
        self.assertEqual(self.anchor_texts(), ["Only fact"])

    def test_forget_without_number_shows_usage_and_deletes_nothing(self):
        self.mm.add_anchor(self.uid, "Only fact")
        replies = self._forget("/forget")
        self.assertIn("Usage", replies[0])
        self.assertEqual(self.anchor_texts(), ["Only fact"])

    def test_forget_with_nothing_pinned_explains_the_observation_log(self):
        self.mm.add_observation(self.uid, "factual", "A noticed thing",
                                importance=5, use_semantic=False)
        replies = self._forget("/forget")
        self.assertIn("append-only", replies[0])
        self.assertIn("A noticed thing", self.obs_text())

    def test_forget_never_touches_the_observation_log(self):
        self.mm.add_anchor(self.uid, "Pinned fact")
        self.mm.add_observation(self.uid, "factual", "A noticed thing",
                                importance=5, use_semantic=False)
        self._forget("/forget 1")
        self.assertEqual(self.anchor_texts(), [])
        self.assertIn("A noticed thing", self.obs_text())


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

    def test_remembered_fact_reaches_the_prompt(self):
        update, _ = make_update(self.uid, "/remember Ships every release on a Thursday")
        asyncio.run(botmod.remember_command(update, None))
        prompt = botmod.build_system_prompt(self.uid)
        self.assertIn("Ships every release on a Thursday", prompt)


class DurabilityTests(TmpMemoryFixture):
    """A remembered fact must stay in the prompt permanently.

    That is the one property the retired memories.json pile really had, and
    the three gates below are why a plain observation does not replace it.
    Each test carries a control that proves the gate was actually reached —
    without it, a test can pass because the flood never happened.
    """

    #: Disjoint alphabetic vocabulary per line: the dedup keyword extractor
    #: only sees [a-zA-Z]{3,} runs, and any shared word makes the lexical
    #: pass (correctly) fold lines into one, so a naive flood of similar
    #: sentences appends almost nothing and the flood silently never occurs.
    @staticmethod
    def _distinct(n: int) -> list[str]:
        import itertools
        words = ["".join(t) for t in itertools.product("abcdefghijklmnop",
                                                       repeat=3)]
        return [" ".join(words[i * 5:(i + 1) * 5]) for i in range(n)]

    def _remember(self, text: str):
        update, _ = make_update(self.uid, f"/remember {text}")
        asyncio.run(botmod.remember_command(update, None))

    def test_survives_a_flood_of_later_observations(self):
        pinned = "My accountant is Maria Papadopoulou"
        control = "CONTROL-OBSERVATION-4f21"
        self._remember(pinned)
        self.mm.add_observation(self.uid, "factual", control,
                                importance=8, use_semantic=False)

        for line in self._distinct(40):
            self.mm.add_observation(self.uid, "behavioral", line,
                                    importance=5, use_semantic=False)
        # Prove the flood actually landed; dedup could otherwise swallow it
        # and make the assertions below meaningless.
        self.assertGreater(len(self.obs_lines()), 30)

        ctx = self.mm.build_memory_context(self.uid, full_mode=False)
        self.assertNotIn(control, ctx)  # the gate was reached
        self.assertIn(pinned, ctx)      # the anchor is not subject to it

    def test_survives_reflection_marking_observations_reflected(self):
        # Once reflection marks a line [reflected] it drops out of the
        # unreflected section, and whether it reaches the prompt at all then
        # depends on the LLM having copied it into the person model.
        pinned = "My accountant is Maria Papadopoulou"
        control = "CONTROL-OBSERVATION-9c07"
        self._remember(pinned)
        self.mm.add_observation(self.uid, "factual", control,
                                importance=8, use_semantic=False)
        people = self.data / "memory" / "people" / str(self.uid)
        (people / "model.md").write_text("# T\n\nA person model.\n",
                                         encoding="utf-8")
        obs_file = people / "observations.md"
        obs_file.write_text(
            obs_file.read_text(encoding="utf-8").replace(
                control, f"{control} [reflected]"),
            encoding="utf-8")

        ctx = self.mm.build_memory_context(self.uid, full_mode=True)
        self.assertNotIn(control, ctx)  # the gate was reached
        self.assertIn(pinned, ctx)

    def test_survives_person_model_truncation(self):
        # The person model is capped at 2800 chars in the prompt. A fact that
        # reflection wrote into the tail of a long model never arrives; this
        # install's model.md is already an order of magnitude over the cap.
        pinned = "My accountant is Maria Papadopoulou"
        control = "CONTROL-MODEL-TAIL-1a55"
        self._remember(pinned)
        people = self.data / "memory" / "people" / str(self.uid)
        (people / "model.md").write_text(
            "# T\n\n" + ("filler line of the person model.\n" * 400)
            + f"\n{control}\n", encoding="utf-8")

        ctx = self.mm.build_memory_context(self.uid, full_mode=True)
        self.assertNotIn(control, ctx)  # the gate was reached
        self.assertIn(pinned, ctx)


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
