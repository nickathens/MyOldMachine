#!/usr/bin/env python3
"""Integrity guards for third-party skills vendored into skills/.

These are copies of external repositories, not code written here, and CI never
looks at skills/ (ruff, py_compile and py-compile all scope to bot.py, core/,
utils/, install/, miniapp/ and tests/). So these tests are the only automated
check that stands over them.

The risks are different from a first-party skill: an upstream re-pull can
silently reintroduce YAML frontmatter that leaks into the skill listing every
turn, drop the licence file Apache-2.0 requires us to keep, move the entry
point named in SKILL.md, or reintroduce a machine-specific path from the
donor machine. Every guard below is anchored on a failure actually observed
while porting, not on a hypothetical.
"""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"

sys.path.insert(0, str(REPO))

# name -> (licence file, token that must appear in it, entry point)
VENDORED = {
    "img2threejs": ("LICENSE", "Apache License", "forge/next.py"),
}

# Skills ported in from the donor machine. MOM installs on strangers' machines,
# so an operator's name, home directory or private project must not ride along.
PORTED_SKILLS = ("img2threejs", "remotion", "google-workspace")
DONOR_MARKERS = (
    "claude-telegram-bot",
    "/home/ntouri",
    "coocoo",
)


class VendoredSkillIntegrityTests(unittest.TestCase):
    def test_each_vendored_skill_is_present(self):
        for name in VENDORED:
            with self.subTest(skill=name):
                self.assertTrue(
                    (SKILLS / name / "SKILL.md").is_file(),
                    f"skills/{name}/SKILL.md is missing; the skill will not load",
                )

    def test_licence_file_survives(self):
        """Apache-2.0 requires the licence to travel with the files."""
        for name, (licence, token, _) in VENDORED.items():
            with self.subTest(skill=name):
                path = SKILLS / name / licence
                self.assertTrue(path.is_file(), f"{name}: {licence} is missing")
                self.assertIn(token, path.read_text(errors="replace"))

    def test_skill_md_has_no_yaml_frontmatter(self):
        """The loader reads the first paragraph as the description.

        Upstream img2threejs ships a `---` YAML block, which the loader would
        render verbatim into the skill listing ("--- name: ... license: ...").
        The port rewrites the header; this catches a re-pull that undoes it.
        """
        for name in VENDORED:
            with self.subTest(skill=name):
                first = (SKILLS / name / "SKILL.md").read_text(
                    errors="replace"
                ).lstrip().splitlines()[0]
                self.assertFalse(
                    first.startswith("---"),
                    f"{name}: SKILL.md opens with frontmatter, which leaks into the listing",
                )
                self.assertTrue(
                    first.startswith("#"),
                    f"{name}: SKILL.md must open with a markdown title, got {first!r}",
                )

    def test_loader_description_is_clean(self):
        from core.skill_loader import SkillManager

        manager = SkillManager(SKILLS)
        for name in VENDORED:
            with self.subTest(skill=name):
                skill = manager.get_skill(name)
                self.assertIsNotNone(skill, f"{name} did not load")
                for leaked in ("---", "license:", "version:", "name: "):
                    self.assertNotIn(
                        leaked,
                        skill.description,
                        f"{name}: frontmatter leaked into the skill listing",
                    )
                self.assertGreater(len(skill.description), 40)

    def test_entry_point_exists_and_runs(self):
        """SKILL.md names an entry point; a moved script makes the doc a lie."""
        for name, (_, _, entry) in VENDORED.items():
            with self.subTest(skill=name):
                root = SKILLS / name
                self.assertTrue((root / entry).is_file(), f"{name}: {entry} is missing")
                proc = subprocess.run(
                    [sys.executable, entry, "--help"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(
                    proc.returncode, 0, f"{name}: {entry} --help failed: {proc.stderr}"
                )

    def test_skill_md_records_provenance(self):
        """A vendored skill without an upstream pin cannot be updated safely."""
        for name in VENDORED:
            with self.subTest(skill=name):
                text = (SKILLS / name / "SKILL.md").read_text(errors="replace")
                self.assertIn("github.com", text, f"{name}: no upstream URL recorded")
                self.assertIn("commit", text.lower(), f"{name}: no upstream commit pinned")

    def test_ported_skills_carry_no_donor_machine_paths(self):
        """MOM ships to strangers: no operator name, home dir or private project."""
        for name in PORTED_SKILLS:
            root = SKILLS / name
            if not root.is_dir():
                continue
            candidates = [
                p for pattern in ("*.md", "*.json", "*.py", "*.ts", "*.tsx")
                for p in sorted(root.rglob(pattern))
            ]
            for path in candidates:
                if "node_modules" in path.parts or "package-lock.json" in path.name:
                    continue
                text = path.read_text(errors="replace").lower()
                for marker in DONOR_MARKERS:
                    with self.subTest(file=str(path.relative_to(REPO)), marker=marker):
                        self.assertNotIn(
                            marker, text,
                            f"{path.relative_to(REPO)} still names the donor machine",
                        )

    def test_ported_skills_declare_dependencies(self):
        """Every skill here carries a deps.json; the loader reads its weight."""
        for name in PORTED_SKILLS:
            root = SKILLS / name
            if not root.is_dir():
                continue
            with self.subTest(skill=name):
                deps = root / "deps.json"
                self.assertTrue(deps.is_file(), f"{name}: deps.json is missing")
                payload = json.loads(deps.read_text(encoding="utf-8"))
                self.assertIsInstance(payload, dict)


REMOTION = SKILLS / "remotion"
SHOTCRAFT_FILES = [
    "render-engine/src/Caption.tsx",
    "render-engine/src/DigitRoll.tsx",
    "render-engine/src/FlashCut.tsx",
    "render-engine/src/PageCam.tsx",
    "render-engine/src/VerticalTicker.tsx",
    "render-engine/src/helpers/motion.ts",
    "render-engine/src/helpers/shake.ts",
    "render-engine/src/helpers/rand.ts",
]


class RemotionShotcraftPortTests(unittest.TestCase):
    """The remotion skill absorbed Apache-2.0 material from video-shotcraft.

    Unlike img2threejs it is not a whole-directory vendor, so the attribution
    lives in per-file headers that a routine tidy-up would delete. These guards
    exist because losing them is a licence breach, not a style regression.
    """

    def test_notice_exists_and_names_the_licence(self):
        notice = REMOTION / "references" / "NOTICE.md"
        self.assertTrue(notice.is_file(), "references/NOTICE.md is missing")
        text = notice.read_text(errors="replace")
        for token in ("video-shotcraft", "Apache License 2.0", "93fe427"):
            self.assertIn(token, text, f"NOTICE.md lost {token!r}")

    def test_every_ported_file_keeps_its_attribution(self):
        for rel in SHOTCRAFT_FILES:
            with self.subTest(file=rel):
                path = REMOTION / rel
                self.assertTrue(path.is_file(), f"{rel} is missing")
                head = "".join(path.read_text(errors="replace").splitlines(True)[:4])
                self.assertIn("video-shotcraft", head, f"{rel}: attribution stripped")
                self.assertIn("Apache-2.0", head, f"{rel}: licence stripped")

    def test_shot_index_matches_the_cards_on_disk(self):
        """The index is the only English surface on 104 Chinese cards.

        If it drifts from the filesystem it silently routes to a card that is
        not there, which reads as "we do not have that shot".
        """
        shots = REMOTION / "references" / "shots"
        self.assertTrue(shots.is_dir(), "references/shots/ is missing")
        on_disk = {p.stem for p in shots.rglob("*.md")}
        self.assertGreater(len(on_disk), 90, "shot cards look truncated")

        index = (REMOTION / "references" / "shot-index.md").read_text(errors="replace")
        indexed = set(re.findall(r"^\| `([a-z0-9-]+)` \|", index, re.M))

        self.assertEqual(
            indexed - on_disk, set(), "shot-index.md names cards that do not exist"
        )
        self.assertEqual(
            on_disk - indexed, set(), "shot cards exist that shot-index.md never lists"
        )

    def test_every_shot_card_is_actually_tracked_by_git(self):
        """On disk is not the same as in the repo, and the gap is silent.

        On the donor machine an unanchored `data/` ignore rule for the bot's
        runtime directory also matched this library's own `data` shot category,
        so 8 cards, including the one MetricStomp is built from, were dropped
        from the commit with no warning: a fresh clone got an index pointing at
        files that were never pushed. This repo's rule (`data/*`) is anchored to
        the root and does not have that bug, so this guard is here to keep it
        that way rather than to fix it.
        """
        shots = REMOTION / "references" / "shots"
        on_disk = {str(p.relative_to(REPO)) for p in shots.rglob("*.md")}

        # Ask what git actually carries, not what the ignore rules say. Once a
        # path is in the index, `git check-ignore` reports it as not-ignored
        # regardless of the rules, so it cannot see this failure at all.
        proc = subprocess.run(
            ["git", "ls-files", "--", str(shots.relative_to(REPO))],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:  # not a git checkout (tarball install)
            self.skipTest("not a git working tree")
        tracked = {line for line in proc.stdout.splitlines() if line.endswith(".md")}

        self.assertEqual(
            on_disk - tracked,
            set(),
            "these shot cards exist on disk but git does not carry them, so a "
            "fresh clone gets an index pointing at missing files",
        )

    def test_bot_runtime_data_dir_is_still_ignored(self):
        """Widening the rule above must not expose the runtime data/ directory."""
        proc = subprocess.run(
            ["git", "check-ignore", "data/maintenance.json"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode == 128:
            self.skipTest("not a git working tree")
        self.assertEqual(
            proc.returncode, 0, "the bot's runtime data/ is no longer gitignored"
        )

    def test_metric_stomp_is_registered(self):
        """SKILL.md documents MetricStomp as a --comp target; Root.tsx must agree."""
        root = (REMOTION / "render-engine" / "src" / "Root.tsx").read_text(
            errors="replace"
        )
        self.assertIn('id="MetricStomp"', root)
        self.assertIn("MetricStomp", (REMOTION / "SKILL.md").read_text(errors="replace"))

    def test_registered_compositions_all_resolve(self):
        """A Root.tsx import of a component file that is not here breaks EVERY
        render, not just that composition: the bundle fails to build."""
        src = REMOTION / "render-engine" / "src"
        root = (src / "Root.tsx").read_text(errors="replace")
        for rel in re.findall(r'from "\./([A-Za-z0-9/_-]+)"', root):
            with self.subTest(component=rel):
                self.assertTrue(
                    (src / f"{rel}.tsx").is_file() or (src / f"{rel}.ts").is_file(),
                    f"Root.tsx imports ./{rel}, which does not exist",
                )

    def test_frame_rebasing_trap_stays_documented(self):
        """FlashCut/Caption read the global clock and draw nothing if mounted bare.

        It fails silently with no error, so the warning in SKILL.md is the only
        thing standing between the next author and a blank overlay.
        """
        skill_md = (REMOTION / "SKILL.md").read_text(errors="replace")
        self.assertIn("Sequence", skill_md)
        stomp = (REMOTION / "render-engine" / "src" / "MetricStomp.tsx").read_text(
            errors="replace"
        )
        self.assertIn("<Sequence", stomp, "MetricStomp lost its Sequence wrapper")


if __name__ == "__main__":
    unittest.main()
