"""What the active-projects block actually shows, and what it silently drops.

Two failures this pins, both measured on real project records rather than
fixtures that happened to fit:

  * the block carried a project's name, path and summary and nothing about
    where the work stands, so the one field that answers "what is the latest
    delivery" was on the record and never in the prompt;
  * the slots went in alphabetical order, so a job touched this week could be
    cut while one untouched since spring kept its place, and the cut ones
    vanished without a word. A project the assistant cannot see is a project
    the user cannot ask about.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

import bot as botmod  # noqa: E402
from core.project_context import (OMITTED, _section, compact_project_line,  # noqa: E402
                                  format_project_block, summarize_projects)

USER = 111111111


class PromptFixture:
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data = Path(self._tmp.name)
        (self.data / "memory" / "projects").mkdir(parents=True)
        self._patch = patch.object(botmod, "DATA_DIR", self.data)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def _project(self, slug, **fields):
        pdir = self.data / "memory" / "projects" / slug
        pdir.mkdir(parents=True, exist_ok=True)
        state = {"name": fields.pop("name", slug.upper()), "slug": slug,
                 "status": fields.pop("status", "in_progress"),
                 "owner": fields.pop("owner", "shared"),
                 "location": fields.pop("location", str(pdir))}
        state.update(fields)
        (pdir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def _block(self, user_id=USER):
        prompt = botmod.build_system_prompt(user_id=user_id)
        parts = prompt.split("### Active Projects:", 1)
        return parts[1].split("\n### ", 1)[0] if len(parts) > 1 else ""

    def _expanded(self):
        return re.findall(r"^\*\*(.+?)\*\* \[", self._block(), re.M)


class CurrentStateReachesThePromptTests(PromptFixture, unittest.TestCase):
    def test_the_state_of_the_work_is_carried(self):
        self._project("films", current_state={
            "phase": "colour", "latest_delivery": "/work/films/out/v6",
            "notes": "waiting on the client"})

        block = self._block()

        self.assertIn("Current state:", block)
        self.assertIn("latest_delivery: /work/films/out/v6", block)
        self.assertIn("phase: colour", block)

    def test_the_current_phase_is_not_pushed_out_by_a_long_note(self):
        # Enough long entries to contest the whole per-project allowance, not
        # merely the field cap. One long note no longer crowds anything out:
        # entries are capped at 800 each, so a block with room to spare seats
        # the phase whatever order it comes in, and the fixture would pass
        # with the ordering deleted.
        self._project("films", current_state={
            "notes": "a long note " * 200, "handover": "a handover line " * 60,
            "risks": "a risk line " * 60, "phase": "colour"})

        block = self._block()

        self.assertIn("phase: colour", block)
        self.assertIn(OMITTED, block)

    def test_related_files_are_listed_as_references(self):
        self._project("films", related_files=["/work/films/brief.pdf",
                                              "/work/films/notes.md"])

        block = self._block()

        self.assertIn("/work/films/brief.pdf", block)
        self.assertIn("references only", block)

    def test_blockers_and_lessons_are_carried(self):
        self._project("films", blockers=["client has not signed off"],
                      lessons=[{"date": "2026-09-01", "content": "old one"},
                               {"date": "2026-09-12", "content": "the newest lesson"}])

        block = self._block()

        self.assertIn("client has not signed off", block)
        self.assertIn("the newest lesson", block)

    def test_a_plain_string_state_still_renders(self):
        self._project("films", current_state="colour pass running")

        self.assertIn("Current state: colour pass running", self._block())

    def test_one_value_stays_on_one_line(self):
        self._project("films", status="in_progress")

        self.assertNotIn("Location:\n", self._block())

    def test_the_per_project_ceiling_is_unchanged(self):
        self._project("films", summary="x" * 4000, current_state={"a": "y" * 4000},
                      related_files=["/p/" + "z" * 2000])

        blocks = self._block().split("\n\n**")
        for block in blocks:
            self.assertLessEqual(len(block), 1600)


class NothingIsCutMidPathTests(unittest.TestCase):
    """Shortening happens on a word boundary, so half a path cannot appear."""

    def test_a_long_field_is_shortened_rather_than_deleted(self):
        state = {"name": "X", "summary": "the brief lives at /work/films/brief.pdf "
                 + "and there is a great deal more prose after it " * 40}

        block = format_project_block(state, "shared")

        self.assertIn("/work/films/brief.pdf", block)
        self.assertIn(OMITTED, block)
        self.assertNotIn("Summary:\n", block)

    def test_a_field_that_cannot_be_shortened_safely_is_dropped_whole(self):
        # No word break anywhere, and longer than the block itself, so no
        # budget this project could hand the field makes it safe to show.
        state = {"name": "X", "summary": "/" + "a" * 2400}

        block = format_project_block(state, "shared")

        self.assertNotIn("a" * 200, block)

    def test_the_block_is_cut_on_a_line_boundary(self):
        state = {"name": "X", "related_files": [f"/work/f{i}/cues.json" for i in range(200)]}

        block = format_project_block(state, "shared", limit=400)

        self.assertTrue(block.rstrip().endswith(OMITTED))
        for line in block.split("\n"):
            self.assertFalse(line.strip().startswith("/work/f") and not line.strip().endswith("json"))


class OrderAndOverflowTests(PromptFixture, unittest.TestCase):
    def test_the_most_recently_touched_project_is_expanded_first(self):
        self._project("zulu", name="ZULU", updated="2026-09-12")
        self._project("alpha", name="ALPHA", updated="2026-06-01")

        self.assertEqual(self._expanded()[:2], ["ZULU", "ALPHA"])

    def test_an_undated_tree_keeps_its_existing_order(self):
        for slug in ("charlie", "alpha", "bravo"):
            self._project(slug, name=slug.upper())

        self.assertEqual(self._expanded(), ["ALPHA", "BRAVO", "CHARLIE"])

    def test_a_project_past_the_cap_still_names_itself(self):
        cap = botmod.MAX_CONTEXT_PROJECTS
        for i in range(cap + 3):
            self._project(f"job-{i:02d}", name=f"JOB{i:02d}",
                          updated=f"2026-09-{(30 - i):02d}")

        block = self._block()

        self.assertEqual(len(self._expanded()), cap)
        for i in range(cap, cap + 3):
            self.assertIn(f"JOB{i:02d}", block, "a live project vanished from the block")
        self.assertIn("not expanded here", block)

    def test_the_named_line_carries_where_to_read_the_rest(self):
        line = compact_project_line({"name": "FILMS", "updated": "2026-09-12",
                                     "location": "/work/films"}, "private")

        self.assertEqual(line, "- FILMS [private], updated 2026-09-12, /work/films")

    def test_the_tail_of_a_long_list_is_reserved_before_anything_expands(self):
        """Sized so the reserve is the only thing that keeps the last one.

        Expanding greedily fits three records and then has too little left for
        even a named line, so the fourth project disappears. Reserving its line
        first costs two expansions and loses nothing.
        """
        records = [(f"**P{i}**\n" + "x" * 290, f"- P{i} " + "n" * 194) for i in range(4)]

        summary = summarize_projects(records, expand_max=8, limit=1000)

        for i in range(4):
            self.assertIn(f"P{i}", summary, f"project {i} vanished from the block")
        self.assertIn("**P0**", summary)
        self.assertLessEqual(len(summary), 1000)

    def test_a_broken_record_cannot_take_a_slot(self):
        self.assertEqual(format_project_block("not a dict", "shared"), "")
        self.assertEqual(compact_project_line(None, "shared"), "")


if __name__ == "__main__":
    unittest.main()


class UnspentAllowanceGoesBackToTheRecordTests(PromptFixture, unittest.TestCase):
    """A field cap is a floor, not a ration.

    Every field had its own cap and the leftovers went nowhere, so a record
    whose length sits in one field spent a third of its allowance and left the
    rest empty. Measured on the real store the day this was written: the Frame
    Repair record rendered 773 characters of its 1500, and the 400 the summary
    was allowed ran out inside the sentence naming the client's rejection, so
    the prompt carried the warning's first four words and not the warning.
    """

    REJECTION = ("READ CONTEXT.md FIRST, including the section 'THE CLIENT "
                 "REJECTED THAT DELIVERY', which overrides everything above it.")
    # Shaped like the record it was measured on: the warning sits just past the
    # field cap, with most of the prose behind it.
    LEAD = "Reusable frame repair system. " + "Prose about the planner. " * 14
    TAIL = " Prose about the windows planner and what it pins. " * 40
    SUMMARY = LEAD + REJECTION + TAIL

    def test_a_record_spends_the_allowance_its_caps_left_over(self):
        state = {"name": "X", "summary": self.SUMMARY}

        block = format_project_block(state, "shared")

        self.assertGreater(len(block), 1200)
        self.assertLessEqual(len(block), 1500)

    def test_the_sentence_past_the_field_cap_reaches_the_prompt(self):
        self._project("films", summary=self.SUMMARY)

        self.assertIn(self.REJECTION, self._block())

    def test_a_field_that_now_fits_is_rendered_whole(self):
        state = {"name": "X", "summary": "a readable summary. " * 30}

        block = format_project_block(state, "shared")

        self.assertNotIn(OMITTED, block)
        self.assertTrue(block.rstrip().endswith("a readable summary."))

    def test_the_spare_is_shared_rather_than_taken_by_the_first_long_field(self):
        state = {"name": "X", "summary": "summary prose here. " * 60,
                 "current_state": {"phase": "colour",
                                   "notes": "state prose here. " * 60}}

        block = format_project_block(state, "shared")
        summary = block.split("  Summary: ", 1)[1].split("\n", 1)[0]
        note = block.split("    notes: ", 1)[1].split("\n", 1)[0]

        # Measured: 630 and 692 with the spare shared, 342 and 410 with it
        # not shared at all, 910 and 410 with the first long field taking it.
        self.assertGreater(len(summary), 500)
        self.assertGreater(len(note), 500)
        self.assertLess(abs(len(summary) - len(note)), 200)

    def test_a_long_field_cannot_take_the_floor_of_a_short_one(self):
        state = {"name": "X", "summary": "x " * 3000,
                 "current_state": {"phase": "colour"},
                 "blockers": ["client has not signed off"],
                 "related_files": ["/work/films/brief.pdf"]}

        block = format_project_block(state, "shared")

        self.assertIn("phase: colour", block)
        self.assertIn("client has not signed off", block)
        self.assertIn("/work/films/brief.pdf", block)

    def test_the_ceiling_holds_with_every_field_over_its_cap(self):
        state = {"name": "X" * 300, "location": "/p/" + "loc " * 200,
                 "summary": "s " * 3000, "current_state": {"a": "y " * 3000},
                 "related_files": ["/work/f%d/cues.json" % i for i in range(300)],
                 "next_steps": ["step %d " % i + "detail " * 60 for i in range(5)],
                 "blockers": ["b " * 900], "lessons": [{"content": "l " * 900}] * 4}

        for limit in (200, 400, 1500, 4000):
            with self.subTest(limit=limit):
                self.assertLessEqual(
                    len(format_project_block(state, "shared", limit=limit)), limit)

    def test_a_section_never_runs_past_the_budget_it_was_given(self):
        """The growth pass spends the spare on the strength of this.

        A field that ends in the omission marker reserved the marker's two
        spaces and its own text, but not the newline that joins it on, so a
        section could come back one character past the budget it was handed
        and the block would spend that character before knowing it was gone.
        The boundary is exact: an entry accepted at its largest allowed size,
        and a second entry that cannot fit behind it.
        """
        label = "Blockers"
        for budget in range(60, 1200, 13):
            # `  Blockers:` and the `    - ` on the entry are what stand
            # between the budget and the text, and the marker line costs
            # len(OMITTED) + 3 with its newline.
            boundary = budget - len(label) - len(OMITTED) - 12
            for size in range(boundary - 2, boundary + 3):
                with self.subTest(budget=budget, size=size):
                    section = _section(label, ["x" * max(1, size),
                                               "a second entry that cannot fit"],
                                       budget)
                    self.assertLessEqual(len(section), budget)

    def test_the_whole_list_is_still_named_when_every_record_is_long(self):
        for index in range(20):
            self._project("p%02d" % index, summary="prose here. " * 400,
                          updated="2026-09-%02d" % (index % 28 + 1))

        block = self._block()

        for index in range(20):
            self.assertIn("P%02d" % index, block)
        self.assertLessEqual(len(block), 14000 + 200)
