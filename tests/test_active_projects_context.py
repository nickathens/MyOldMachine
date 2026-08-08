"""A live project must stay in the prompt whatever its status says.

The active-projects block matched the status string "in_progress" exactly, so a
project whose status held anything else was dropped without a word. That is not
hypothetical: a status field overwritten with a progress note took the biggest
live client job out of the assistant's context, and the assistant then told the
user the project did not exist. "live" fails the same way.

The rule is inverted here -- a project is active unless its status says the work
is over -- so the list degrades toward showing too much rather than toward
silently hiding a job. The guard in project_manager keeps the status field short
in the first place; these tests cover the case where something already got in.
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

USER = 111111111
OTHER = 222222222

CLOBBERED_STATUS = (
    "DAVINCI RESOLVE PROJECTS BUILT AND DELIVERED (2026-08-07). Native .drp for "
    "both graded films now live at ~/projects/CLIENT_FILMS/resolve_projects"
)


class ProjectTreeFixture:
    """Temp memory tree plus the two helpers, shared by both test classes.

    A plain mixin rather than a base TestCase: subclassing one TestCase from
    another re-runs every inherited test under the child's name too, which
    inflates the count that a mutation run watches.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data = Path(self._tmp.name)
        (self.data / "memory" / "projects").mkdir(parents=True)
        self._patch = patch.object(botmod, "DATA_DIR", self.data)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def _project(self, slug, status, owner="shared", name=None):
        pdir = self.data / "memory" / "projects" / slug
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "state.json").write_text(
            json.dumps({
                "name": name or slug.upper(),
                "slug": slug,
                "status": status,
                "owner": owner,
                "location": str(pdir),
                "summary": f"summary for {slug}",
            }),
            encoding="utf-8",
        )

    def _prompt(self, user_id=USER):
        return botmod.build_system_prompt(user_id=user_id)


class ActiveProjectsBlockTests(ProjectTreeFixture, unittest.TestCase):
    def test_status_overwritten_with_a_note_still_lists(self):
        """The exact failure: a progress note in the status field hid the job."""
        self._project("client-films", CLOBBERED_STATUS, name="CLIENT FILMS")

        self.assertIn("CLIENT FILMS", self._prompt())

    def test_in_progress_still_lists(self):
        self._project("timelines", "in_progress", owner=USER, name="Timelines")

        self.assertIn("Timelines", self._prompt())

    def test_other_active_wordings_list(self):
        for slug, status in (("site", "live"), ("deck", "active"), ("job", "on_hold")):
            self._project(slug, status)

        prompt = self._prompt()
        for slug in ("SITE", "DECK", "JOB"):
            self.assertIn(slug, prompt, f"{slug} should count as active")

    def test_finished_projects_are_excluded(self):
        for slug, status in (
            ("old-a", "archived"), ("old-b", "done"), ("old-c", "Completed"),
            ("old-d", "cancelled"), ("old-e", " CANCELED "),
        ):
            self._project(slug, status)

        prompt = self._prompt()
        for slug in ("OLD-A", "OLD-B", "OLD-C", "OLD-D", "OLD-E"):
            self.assertNotIn(slug, prompt, f"{slug} is finished and should be hidden")

    def test_missing_status_still_lists(self):
        pdir = self.data / "memory" / "projects" / "legacy"
        pdir.mkdir(parents=True)
        (pdir / "state.json").write_text(
            json.dumps({"name": "LEGACY", "slug": "legacy", "location": str(pdir)}),
            encoding="utf-8",
        )

        self.assertIn("LEGACY", self._prompt())

    def test_another_users_private_project_stays_hidden(self):
        """Loosening the status filter must not loosen the ownership filter."""
        self._project("other-team-job", "in_progress", owner=OTHER, name="OTHER TEAM JOB")

        self.assertNotIn("OTHER TEAM JOB", self._prompt())


class ContextSlotCompetitionTests(ProjectTreeFixture, unittest.TestCase):
    """The block is capped, so admitting more projects has a cost.

    Inverting the status filter does not just add rows: every status nobody
    anticipated now competes for the same MAX_CONTEXT_PROJECTS slots, in plain
    alphabetical order. On a real 21-project tree that took the block from 8
    live projects to 4, evicting four live jobs in favour of two marked
    "shipped", one "parked" and one "on_hold" -- the same silent disappearance
    the filter change exists to prevent, arriving by a different door.

    Recognised-live statuses therefore fill the slots first. These tests pin
    that, and pin that it is an ordering rule rather than a new exclusion:
    an unfamiliar status still shows when a slot is going spare.
    """

    CAP = None  # set in setUp from the module constant, never hardcoded here

    def setUp(self):
        super().setUp()
        self.CAP = botmod.MAX_CONTEXT_PROJECTS

    def _shown(self, user_id=USER):
        prompt = self._prompt(user_id)
        block = prompt.split("### Active Projects:", 1)
        if len(block) < 2:
            return []
        return re.findall(r"^\*\*(.+?)\*\* \[", block[1], re.M)

    def test_a_live_project_is_never_evicted_by_a_non_live_one(self):
        # Non-live projects sort first, live ones last, so alphabetical order
        # alone hands every slot to the non-live half.
        for i in range(self.CAP):
            self._project(f"a-shipped-{i:02d}", "shipped", name=f"SHIPPED{i:02d}")
            self._project(f"z-running-{i:02d}", "in_progress", name=f"RUNNING{i:02d}")

        shown = self._shown()

        self.assertEqual(len(shown), self.CAP)
        self.assertEqual(shown, [f"RUNNING{i:02d}" for i in range(self.CAP)])

    def test_the_real_tree_shape_keeps_every_slot_live(self):
        """The measured failure: statuses that sort early are not live."""
        early = (("agent-channel", "on_hold"), ("channels-bridge", "parked"),
                 ("greek-engineer-skill", "shipped"), ("greek-law-skill", "shipped"))
        for slug, status in early:
            self._project(slug, status, name=slug.upper())
        for i in range(self.CAP):
            self._project(f"m-job-{i:02d}", "in_progress", name=f"MJOB{i:02d}")

        shown = self._shown()

        self.assertEqual(len(shown), self.CAP)
        for slug, _status in early:
            self.assertNotIn(slug.upper(), shown,
                             f"{slug} took a slot a live project needed")

    def test_an_unfamiliar_status_still_shows_when_a_slot_is_spare(self):
        """Ordering, not exclusion. Over-blocking would be the worse bug."""
        self._project("odd", "mothballed pending client sign-off", name="ODD")
        self._project("busy", "in_progress", name="BUSY")

        self.assertEqual(self._shown(), ["BUSY", "ODD"])

    def test_alphabetical_order_survives_inside_the_live_tier(self):
        for slug in ("charlie", "alpha", "bravo"):
            self._project(slug, "in_progress", name=slug.upper())

        self.assertEqual(self._shown(), ["ALPHA", "BRAVO", "CHARLIE"])

    def test_each_live_wording_wins_a_slot_under_pressure(self):
        """Pinned by literal, not by iterating the table.

        Looping over ACTIVE_PROJECT_STATUSES would audit the table against
        itself: delete a word and the loop simply stops testing it. These four
        are the wordings a real project tree actually carries -- "ongoing" is
        the one this branch set out to rescue, and dropping it from the table
        was the single mutation the rest of this class did not catch.
        """
        for status in ("in_progress", "ongoing", "active", "live"):
            with self.subTest(status=status):
                self.setUp()  # a fresh tree per wording
                for i in range(self.CAP):
                    self._project(f"a-old-{i:02d}", "shipped", name=f"OLD{i:02d}")
                self._project("z-the-one", status, name="THEONE")

                self.assertIn("THEONE", self._shown(),
                              f"status {status!r} lost its slot to a shipped project")

    def test_the_two_status_tables_do_not_overlap(self):
        """A word in both tables would make its tier meaningless."""
        both = botmod.ACTIVE_PROJECT_STATUSES & botmod.FINISHED_PROJECT_STATUSES

        self.assertEqual(both, set(), f"status(es) in both tables: {both}")


if __name__ == "__main__":
    unittest.main()
