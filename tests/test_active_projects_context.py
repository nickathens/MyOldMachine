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


class ActiveProjectsBlockTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
