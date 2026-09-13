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
from core.project_context import (OMITTED, compact_project_line,  # noqa: E402
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
        self._project("films", current_state={
            "notes": "a long note " * 40, "phase": "colour"})

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
                 + "and there is a great deal more prose after it " * 10}

        block = format_project_block(state, "shared")

        self.assertIn("/work/films/brief.pdf", block)
        self.assertIn(OMITTED, block)
        self.assertNotIn("Summary:\n", block)

    def test_a_field_that_cannot_be_shortened_safely_is_dropped_whole(self):
        state = {"name": "X", "summary": "/" + "a" * 900}

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
