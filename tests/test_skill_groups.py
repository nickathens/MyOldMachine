"""The skill menu is grouped, complete, and names no skill it does not list.

Guards core/skill_groups.py and both menu builders: the shared
core.skill_loader.SkillManager.build_context, and the per-user
core.skill_overlay.build_user_context that bot.py calls every turn.
  - every shared skill sits in exactly one group (a new skill with no group
    fails here on purpose, so grouping is a choice, not a default);
  - GROUPS names no skill that does not exist (no stale entries);
  - no skill whose opening paragraph hits the loader's 200-char cut is left
    without a curated, finished-sentence summary;
  - both builders list every enabled skill exactly once, under group headers,
    with nothing falling through to "Other";
  - a skill off a user's menu (blocked_skills, an "off" override) is not named
    again by a Pick clause or a SEE_ALSO sentence, a name-only skill loses its
    Pick clause, and a fork keeps the description its own SKILL.md gives;
  - every pointer is written {skill}, so it can be dropped exactly.
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import skill_groups, skill_overlay  # noqa: E402
from core.skill_loader import SkillManager  # noqa: E402

SKILLS = ROOT / "skills"
MANAGER = SkillManager(SKILLS)
ENABLED = {s.name for s in MANAGER.get_enabled_skills()}
GROUP_OF = skill_groups.group_of()
GROUP_NAMES = {name for name, _ in skill_groups.GROUPS}

# A household-style block: a second user kept off the owner's mail, calendar,
# Drive, GitHub, cloud storage and containers.
BLOCK = ["email", "calendar", "google-workspace", "git", "cloud-sync",
         "docker-services"]


def _bullets(ctx: str) -> list[str]:
    return [ln for ln in ctx.splitlines() if ln.startswith("- **")]


def _bullet(ctx: str, name: str) -> str:
    return next(ln for ln in ctx.splitlines() if ln.startswith(f"- **{name}**:"))


def _pick_text(ctx: str) -> str:
    return "\n".join(ln for ln in ctx.splitlines() if ln.startswith("Pick: "))


def _block(ctx: str, group: str) -> str:
    return ctx.split(f"### {group}\n", 1)[1].split("\n### ", 1)[0]


class _UserDir(unittest.TestCase):
    """A fresh, empty user dir per test: no overlay, no settings."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skill-groups-"))
        self.user_dir = self.tmp / "user"
        self.user_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def menu(self, exclude=None, **kw) -> str:
        """The production menu: the per-user builder bot.py calls."""
        return skill_overlay.build_user_context(MANAGER, self.user_dir,
                                                exclude=exclude, **kw)


class GroupCoverage(unittest.TestCase):
    def test_every_skill_in_exactly_one_group(self):
        ungrouped = sorted(ENABLED - set(GROUP_OF))
        self.assertEqual(ungrouped, [], f"skills with no group: {ungrouped}")

    def test_groups_name_no_missing_skill(self):
        stale = sorted(set(GROUP_OF) - ENABLED)
        self.assertEqual(stale, [], f"GROUPS names skills not on disk: {stale}")

    def test_no_skill_in_two_groups(self):
        flat = [s for _, skills in skill_groups.GROUPS for s in skills]
        dupes = sorted({s for s in flat if flat.count(s) > 1})
        self.assertEqual(dupes, [], f"skills listed in two groups: {dupes}")

    def test_group_names_are_unique(self):
        names = [name for name, _ in skill_groups.GROUPS]
        self.assertEqual(len(names), len(set(names)))


class Summaries(unittest.TestCase):
    def test_no_truncated_line_survives(self):
        # The loader caps an auto description at 200 chars. Any skill that hits
        # the cap must carry a curated summary, or its menu line is a mid-word
        # stub.
        for s in MANAGER.get_enabled_skills():
            with self.subTest(skill=s.name):
                if len(s.description) >= 200:
                    self.assertIn(s.name, skill_groups.SUMMARIES,
                                  f"{s.name} description is cut at 200 and has no curated summary")

    def test_summaries_name_real_skills(self):
        unknown = sorted(set(skill_groups.SUMMARIES) - ENABLED)
        self.assertEqual(unknown, [], f"SUMMARIES names non-skills: {unknown}")

    def test_summaries_are_finished_sentences(self):
        for name, text in skill_groups.SUMMARIES.items():
            with self.subTest(skill=name):
                self.assertTrue(text.strip(), f"{name}: empty summary")
                self.assertLessEqual(len(text), 300, f"{name}: summary too long")
                self.assertTrue(text.rstrip().endswith("."),
                                f"{name}: summary does not end on a full stop")

    def test_disambiguation_keys_are_groups(self):
        unknown = sorted(set(skill_groups.DISAMBIGUATION) - GROUP_NAMES)
        self.assertEqual(unknown, [], f"DISAMBIGUATION names non-groups: {unknown}")


class BothBuilders(_UserDir):
    """The shared builder and the per-user one print the same grouped menu."""

    def contexts(self, exclude=None):
        return {
            "build_context": MANAGER.build_context(exclude=exclude),
            "build_user_context": self.menu(exclude=exclude),
        }

    def test_lists_every_skill_exactly_once(self):
        for builder, ctx in self.contexts().items():
            for name in ENABLED:
                with self.subTest(builder=builder, skill=name):
                    self.assertEqual(ctx.count(f"- **{name}**:"), 1,
                                     f"{name} not listed exactly once")
            with self.subTest(builder=builder):
                self.assertEqual(len(_bullets(ctx)), len(ENABLED))

    def test_nothing_falls_through_to_other(self):
        for builder, ctx in self.contexts().items():
            with self.subTest(builder=builder):
                self.assertNotIn("### Other", ctx,
                                 "a skill fell through to Other: it is missing from GROUPS")

    def test_groups_print_in_order_with_their_pick_lines(self):
        for builder, ctx in self.contexts().items():
            positions = []
            for name, skills in skill_groups.GROUPS:
                with self.subTest(builder=builder, group=name):
                    self.assertIn(f"### {name}\n", ctx)
                    positions.append(ctx.index(f"### {name}\n"))
                    pick = skill_groups.pick_line(name, ENABLED)
                    if pick:
                        self.assertTrue(_block(ctx, name).startswith(pick + "\n"),
                                        "the Pick line leads its group")
                    for skill in skills:
                        self.assertIn(f"- **{skill}**:", _block(ctx, name))
            with self.subTest(builder=builder):
                self.assertEqual(positions, sorted(positions))

    def test_header_says_how_the_menu_reads(self):
        for builder, ctx in self.contexts().items():
            with self.subTest(builder=builder):
                self.assertIn(skill_groups.HEADER_NOTE, ctx.split("\n### ", 1)[0])

    def test_respects_exclude(self):
        for builder, ctx in self.contexts(exclude=["weather"]).items():
            with self.subTest(builder=builder):
                self.assertNotIn("- **weather**:", ctx)
                self.assertEqual(len(_bullets(ctx)), len(ENABLED) - 1)

    def test_curated_summary_shown_not_truncation(self):
        # presentations is the worst offender (739 -> 200). Its menu line must
        # be the curated sentence, ending cleanly.
        for builder, ctx in self.contexts().items():
            with self.subTest(builder=builder):
                self.assertIn(skill_groups.summary("presentations", ENABLED),
                              _bullet(ctx, "presentations"))

    def test_resource_note_sits_under_the_header(self):
        # A machine too small for some skills gets the warning note between
        # the header and the first group, where it was before grouping.
        ctxs = {
            "build_context": MANAGER.build_context(ram_gb=0.25, disk_free_gb=0.1),
            "build_user_context": self.menu(ram_gb=0.25, disk_free_gb=0.1),
        }
        for builder, ctx in ctxs.items():
            with self.subTest(builder=builder):
                self.assertIn("WARN:", ctx)
                note = ctx.index("**NOTE:** Some skills are marked")
                self.assertLess(ctx.index(skill_groups.HEADER_NOTE), note)
                self.assertLess(note, ctx.index("### "))
                self.assertEqual(ctx[note - 2:note], "\n\n",
                                 "a blank line separates the note from the header")


class HiddenSkills(_UserDir):
    """A skill off a user's menu must not come back through another line that
    points at it. The menu is only what the model is shown (every turn runs as
    the same OS user), so a pointer elsewhere would undo the hiding."""

    def test_a_blocked_set_holds(self):
        ctx = self.menu(exclude=BLOCK)
        full = self.menu()
        for name in BLOCK:
            with self.subTest(hidden=name):
                self.assertNotIn(f"- **{name}**:", ctx)
                self.assertIn(f"- **{name}**:", full)
        gone = ["email for Gmail", "calendar for Google Calendar",
                "google-workspace for Drive/Docs/Sheets/Slides",
                "email and calendar skills", "git for version control",
                "docker-services for containers", "cloud-sync for rclone"]
        for text in gone:
            with self.subTest(gone=text):
                self.assertNotIn(text, ctx)
                self.assertIn(text, full)  # the filter is not a no-op
        # ...while the rest of each group's guidance stays.
        picks = _pick_text(ctx)
        for text in ["translate for machine translation",
                     "clipboard bridges phone and desktop copy",
                     "coding is the build/debug methodology",
                     "workflow for YAML pipelines", "compress for archives"]:
            with self.subTest(kept=text):
                self.assertIn(text, picks)

    def test_hiding_mail_keeps_workspace_summary(self):
        # Hiding email and calendar drops google-workspace's pointer sentence,
        # never its own summary.
        ctx = self.menu(exclude=["email", "calendar"])
        self.assertNotIn("email and calendar skills", ctx)
        self.assertIn("google-workspace for Drive/Docs/Sheets/Slides", _pick_text(ctx))
        self.assertIn(skill_groups.SUMMARIES["google-workspace"],
                      _bullet(ctx, "google-workspace"))

    def test_every_single_hide_drops_its_pointers_and_only_those(self):
        for hidden in sorted(ENABLED):
            ctx = self.menu(exclude=[hidden])
            picks = _pick_text(ctx)
            for group, clauses in skill_groups.DISAMBIGUATION.items():
                for clause in clauses:
                    text = skill_groups.plain(clause)
                    with self.subTest(hidden=hidden, clause=text):
                        if hidden in skill_groups.refs(clause):
                            self.assertNotIn(text, picks)
                        else:
                            self.assertIn(text, picks)
            for name, sentence in skill_groups.SEE_ALSO.items():
                if name == hidden:
                    continue
                text = skill_groups.plain(sentence)
                with self.subTest(hidden=hidden, see_also=name):
                    if hidden in skill_groups.refs(sentence):
                        self.assertNotIn(text, ctx)
                    else:
                        self.assertIn(text, _bullet(ctx, name))

    def test_off_override_drops_pointers(self):
        ok, msg = skill_overlay.set_override(SKILLS, self.user_dir, "video-editing", "off")
        self.assertTrue(ok, msg)
        ctx = self.menu()
        self.assertNotIn("- **video-editing**:", ctx)
        self.assertNotIn("video-editing for cuts", ctx)
        self.assertNotIn("Video reels live in video-editing.", _bullet(ctx, "presentations"))
        self.assertNotIn("use video-editing", _bullet(ctx, "davinci-resolve"))
        self.assertIn("colorgrade for a whole-video colour pass", _pick_text(ctx))

    def test_name_only_keeps_the_name_and_drops_its_clause(self):
        ok, msg = skill_overlay.set_override(SKILLS, self.user_dir, "video-editing",
                                             "name-only")
        self.assertTrue(ok, msg)
        ctx = self.menu()
        self.assertTrue(_bullet(ctx, "video-editing").startswith("- **video-editing**: "))
        self.assertNotIn("Edit videos: cut, merge", ctx)
        self.assertNotIn("video-editing for cuts", ctx)
        # The skill is still listed, so other lines may still send work to it.
        self.assertIn("Video reels live in video-editing.", _bullet(ctx, "presentations"))
        self.assertIn("colorgrade for a whole-video colour pass", _pick_text(ctx))

    def test_described_never_widens_the_menu(self):
        # A described set naming a skill with no line on the menu must not
        # bring that skill's clause back.
        lines = {"email": "- **email**: x"}
        body = "\n".join(skill_groups.grouped(lines, {"email", "calendar"}))
        self.assertIn("Pick: email for Gmail.", body)
        self.assertNotIn("calendar for Google Calendar", body)

    def test_pick_lines_stay_well_formed(self):
        pattern = re.compile(r"^Pick: [^;{}]+(; [^;{}]+)*\.$")
        for hidden in ([[]] + [[s] for s in sorted(ENABLED)]
                       + [["email", "calendar"], ["email", "calendar", "google-workspace"],
                          BLOCK]):
            ctx = self.menu(exclude=hidden)
            for line in _pick_text(ctx).splitlines():
                with self.subTest(hidden=hidden, line=line):
                    self.assertRegex(line, pattern)
            # Braces are checked only on the lines this module writes: a skill's
            # own SKILL.md opening may legitimately contain one.
            for name in skill_groups.SUMMARIES:
                if name in ENABLED and name not in hidden:
                    with self.subTest(hidden=hidden, summary=name):
                        bullet = _bullet(ctx, name)
                        self.assertNotIn("{", bullet)
                        self.assertNotIn("}", bullet)

    def test_group_whose_clauses_are_all_hidden_prints_no_pick_line(self):
        # screenplay has no clause, so hiding every skill the Film clauses name
        # leaves the group on the menu with nothing to pick.
        hidden = sorted(set().union(*map(skill_groups.refs,
                                         skill_groups.DISAMBIGUATION["Film & Video"])))
        block = _block(self.menu(exclude=hidden), "Film & Video")
        self.assertIn("- **screenplay**:", block)
        self.assertNotIn("Pick:", block)


class Overlay(unittest.TestCase):
    """Forks and the user's own skills, on a small shared tree."""

    LONG = ("Build decks of every kind: treatments, lookbooks, pitch decks, "
            "proposals, microsites, scrollytelling, playbooks and status reports, "
            "with a scroll pipeline, PDF export and video export, plus far more "
            "detail than a menu line can hold.")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skill-groups-"))
        self.shared = self.tmp / "shared"
        self.user_dir = self.tmp / "user"
        self.user_dir.mkdir()
        for name, desc in [("presentations", self.LONG),
                           ("video-editing", "Edit videos.")]:
            (self.shared / name).mkdir(parents=True)
            (self.shared / name / "SKILL.md").write_text(f"# {name}\n\n{desc}\n",
                                                         encoding="utf-8")
        self.mgr = SkillManager(self.shared)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def menu(self) -> str:
        return skill_overlay.build_user_context(self.mgr, self.user_dir)

    def test_shared_skill_gets_its_curated_summary(self):
        self.assertGreaterEqual(len(self.mgr.get_skill("presentations").description), 200)
        self.assertIn(skill_groups.SUMMARIES["presentations"],
                      _bullet(self.menu(), "presentations"))

    def test_fork_keeps_its_own_description(self):
        ok, msg = skill_overlay.fork_skill(self.shared, self.user_dir, "presentations")
        self.assertTrue(ok, msg)
        (self.user_dir / "skills" / "presentations" / "SKILL.md").write_text(
            "# presentations\n\nMy own deck builder.\n", encoding="utf-8")
        line = _bullet(self.menu(), "presentations")
        self.assertIn("[FORKED] My own deck builder.", line)
        self.assertNotIn(skill_groups.SUMMARIES["presentations"], line)

    def test_overlay_only_skill_lands_in_other(self):
        own = self.user_dir / "skills" / "my-tool"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text("# my-tool\n\nA tool of my own.\n", encoding="utf-8")
        ctx = self.menu()
        self.assertIn("- **my-tool**:", _block(ctx, "Other"))
        self.assertIn("- **presentations**:", _block(ctx, "Web & Design"))


class PointerMarks(unittest.TestCase):
    """Pointers are written as {skill} so they can be dropped exactly. Most skill
    names are also English words ("research", "charts"), so plain-word matching
    cannot tell a pointer from prose; the marks are the only reliable signal."""

    HYPHENATED = sorted((s for s in ENABLED if "-" in s), key=len, reverse=True)

    def _unmarked_hyphenated(self, text: str, own: str = "") -> list[str]:
        bare = re.sub(r"\{[a-z0-9_-]+\}", " ", text)
        return [s for s in self.HYPHENATED if s != own
                and re.search(rf"(?<![\w-]){re.escape(s)}(?![\w-])", bare)]

    def test_clauses_point_at_real_skills(self):
        for group, clauses in skill_groups.DISAMBIGUATION.items():
            for clause in clauses:
                with self.subTest(group=group, clause=clause):
                    self.assertTrue(clause.startswith("{"),
                                    "a Pick clause starts with the skill it is about, marked")
                    refs = skill_groups.refs(clause)
                    self.assertTrue(refs)
                    self.assertLessEqual(refs, ENABLED, f"unknown skill in {clause!r}")
                    self.assertEqual(self._unmarked_hyphenated(clause), [])

    def test_see_also_points_at_real_skills(self):
        for name, sentence in skill_groups.SEE_ALSO.items():
            with self.subTest(skill=name):
                self.assertIn(name, skill_groups.SUMMARIES)
                refs = skill_groups.refs(sentence)
                self.assertTrue(refs)
                self.assertLessEqual(refs, ENABLED)
                self.assertEqual(self._unmarked_hyphenated(sentence, name), [])
                self.assertTrue(skill_groups.plain(sentence).endswith("."))

    def test_summaries_carry_no_pointers(self):
        # A pointer inside a summary could never be dropped; it goes in SEE_ALSO.
        for name, text in skill_groups.SUMMARIES.items():
            with self.subTest(skill=name):
                self.assertEqual(skill_groups.refs(text), set())
                self.assertEqual(self._unmarked_hyphenated(text, name), [])


class PickClausesMatchTheSkills(unittest.TestCase):
    """A Pick clause steers every turn, so it must not narrow or overstate what
    a skill does here. Each check reads the skill itself, so it follows the
    skill rather than this wording (Mac review of #207)."""

    @staticmethod
    def _clause(group: str, skill: str) -> str:
        return next(skill_groups.plain(c) for c in skill_groups.DISAMBIGUATION[group]
                    if c.startswith("{" + skill + "}"))

    def test_ai_video_is_offered_where_video_is_picked(self):
        # image-gen is MOM's only route to an AI clip (its SKILL.md title reads
        # "Media Generation (Image + Video + 3D + Audio)"). A clause calling it
        # an image maker, and a Film & Video line that never names it, steer a
        # request for a new clip to the editing tools.
        title = (SKILLS / "image-gen" / "SKILL.md").read_text(encoding="utf-8").splitlines()[0]
        if "Video" not in title:
            self.skipTest("image-gen no longer generates video")
        self.assertIn("video", self._clause("Image", "image-gen"))
        film = set().union(*map(skill_groups.refs, skill_groups.DISAMBIGUATION["Film & Video"]))
        self.assertIn("image-gen", film)

    def test_screen_recording_says_where_it_works(self):
        # record_video.py refuses on anything but Linux, so a clause that offers
        # recording sends a Mac's request to a script that will not run.
        script = (SKILLS / "media" / "scripts" / "record_video.py").read_text(encoding="utf-8")
        if 'platform.system() != "Linux"' not in script:
            self.skipTest("record_video.py runs beyond Linux now")
        clause = self._clause("System & Files", "media")
        if "record" in clause:
            self.assertIn("Linux", clause)


if __name__ == "__main__":
    unittest.main(verbosity=2)
