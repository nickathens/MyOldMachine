"""Project-scoped observations must reach the project they name.

Routing looked for projects under the per-user people directory, which no
installation has ever created: projects live in the shared memory tree and are
separated by an `owner` field, not by path. The lookup therefore missed every
time, and the miss was reported as "project not found" -- a live project that
existed on disk was described as nonexistent, and three months of notes fell
back into the general model pool. 727 such warnings, zero successful routes.

So the test asserts on the real directory layout produced by
project_manager.create, not on a fixture built to match the lookup: a fixture
that mirrors the wrong path would have passed throughout the outage.

Privacy is asserted here too, because pointing the lookup at the shared tree is
exactly what puts one user's notes within reach of another user's private
project. Path separation used to do that job by accident; now the owner field
has to do it on purpose.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep synthetic output out of the real reflection.log

from core.memory import MemoryManager  # noqa: E402
from utils import reflect  # noqa: E402

OWNER = 111111111
OTHER = 222222222


def _record(slug, content="Client moved the deadline", ts="2026-08-08 07:24"):
    return {
        "timestamp": ts,
        "type": "project",
        "importance": 5,
        "project": slug,
        "content": content,
    }


class ProjectRoutingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data = Path(self._tmp.name)
        self.mm = MemoryManager(self.data)
        self.projects = self.data / "memory" / "projects"
        self.addCleanup(self._tmp.cleanup)

    def _project(self, slug, owner):
        """Write a project exactly where project_manager.create puts one."""
        pdir = self.projects / slug
        pdir.mkdir(parents=True)
        state = {
            "name": slug.upper(),
            "slug": slug,
            "status": "in_progress",
            "owner": owner,
            "location": str(pdir),
        }
        (pdir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        return pdir / "state.json"

    def _lessons(self, slug):
        state = json.loads((self.projects / slug / "state.json").read_text(encoding="utf-8"))
        return state.get("lessons", [])

    def test_routes_into_the_shared_projects_tree(self):
        """The lookup must find a project where the project manager creates it."""
        self._project("client-films", owner="shared")
        rec = _record("client-films")

        routed, remaining = reflect.route_project_observations([rec], self.mm, OWNER)

        self.assertEqual(routed, [rec])
        self.assertEqual(remaining, [])
        lessons = self._lessons("client-films")
        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0]["content"], "Client moved the deadline")
        self.assertEqual(lessons[0]["date"], "2026-08-08")

    def test_per_user_projects_dir_is_not_consulted(self):
        """A project only under the old per-user path must not satisfy the lookup.

        This is the shape the lookup used to require. Pinning it as a miss stops
        a future 'fix' from restoring the dead path and passing.
        """
        legacy = self.mm._user_dir(OWNER) / "projects" / "ghost"
        legacy.mkdir(parents=True)
        (legacy / "state.json").write_text(
            json.dumps({"slug": "ghost", "owner": "shared"}), encoding="utf-8"
        )

        routed, remaining = reflect.route_project_observations([_record("ghost")], self.mm, OWNER)

        self.assertEqual(routed, [])
        self.assertEqual(len(remaining), 1)

    def test_owner_reaches_own_private_project(self):
        self._project("timelines", owner=OWNER)

        routed, _ = reflect.route_project_observations([_record("timelines")], self.mm, OWNER)

        self.assertEqual(len(routed), 1)
        self.assertEqual(len(self._lessons("timelines")), 1)

    def test_another_users_private_project_is_never_written(self):
        """Shared tree plus private project: only the owner may write lessons."""
        self._project("other-team-job", owner=OTHER)

        routed, remaining = reflect.route_project_observations([_record("other-team-job")], self.mm, OWNER)

        self.assertEqual(routed, [])
        self.assertEqual(len(remaining), 1, "note must stay in the pool, not vanish")
        self.assertEqual(self._lessons("other-team-job"), [], "no leak into another user's project")

    def test_legacy_project_without_owner_is_shared(self):
        pdir = self.projects / "coocooplace"
        pdir.mkdir(parents=True)
        (pdir / "state.json").write_text(json.dumps({"slug": "coocooplace"}), encoding="utf-8")

        routed, _ = reflect.route_project_observations([_record("coocooplace")], self.mm, OWNER)

        self.assertEqual(len(routed), 1)

    def test_unknown_slug_keeps_the_note_in_the_pool(self):
        routed, remaining = reflect.route_project_observations(
            [_record("no-such-project")], self.mm, OWNER
        )

        self.assertEqual(routed, [])
        self.assertEqual(len(remaining), 1)

    def test_path_traversal_slug_is_refused(self):
        routed, remaining = reflect.route_project_observations(
            [_record("../../../etc")], self.mm, OWNER
        )

        self.assertEqual(routed, [])
        self.assertEqual(len(remaining), 1)

    def test_untagged_observations_pass_through(self):
        rec = _record(None)
        rec["project"] = None

        routed, remaining = reflect.route_project_observations([rec], self.mm, OWNER)

        self.assertEqual(routed, [])
        self.assertEqual(remaining, [rec])

    def test_corrupt_state_file_is_not_routed_into(self):
        pdir = self.projects / "broken"
        pdir.mkdir(parents=True)
        (pdir / "state.json").write_text("{not json", encoding="utf-8")

        routed, remaining = reflect.route_project_observations([_record("broken")], self.mm, OWNER)

        self.assertEqual(routed, [])
        self.assertEqual(len(remaining), 1)


if __name__ == "__main__":
    unittest.main()
