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

# name -> (licence file, token that must appear in it, entry point, or None for
# a vendored skill that ships no scripts and is pure instruction text)
VENDORED = {
    "img2threejs": ("LICENSE", "Apache License", "forge/next.py"),
    "last30days": ("LICENSE", "MIT License", "scripts/last30days.py"),
    "mascot": ("LICENSE", "MIT License", None),
}

# Skills ported in from the donor machine. MOM installs on strangers' machines,
# so an operator's name, home directory or private project must not ride along.
PORTED_SKILLS = ("img2threejs", "remotion", "google-workspace", "last30days")
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
        ran = 0
        for name, (_, _, entry) in VENDORED.items():
            if entry is None:  # instruction-only skill, nothing to execute
                continue
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
                ran += 1
        # Without this the None branch above can widen into a blanket skip and
        # the whole guard passes while executing nothing.
        self.assertEqual(
            ran,
            sum(1 for entry in VENDORED.values() if entry[2] is not None),
            "the entry-point check skipped a skill that declares one",
        )

    def test_skill_md_records_provenance(self):
        """A vendored skill without an upstream pin cannot be updated safely.

        The pin may live in SKILL.md or in a sibling VENDOR.md; last30days keeps
        its whole vendoring record in the latter."""
        for name in VENDORED:
            with self.subTest(skill=name):
                text = (SKILLS / name / "SKILL.md").read_text(errors="replace")
                vendor = SKILLS / name / "VENDOR.md"
                if vendor.is_file():
                    text += vendor.read_text(errors="replace")
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


IMPECCABLE = SKILLS / "impeccable"
ACCESSIBILITY = IMPECCABLE / "reference" / "accessibility"
# The five files taken byte for byte from upstream, plus the one with a single
# edited line. NOTICE.md records which is which.
FOLDED_VERBATIM = [
    "focus-and-keyboard.md",
    "semantics-and-aria.md",
    "screen-readers.md",
    "hit-areas.md",
    "motion-and-zoom.md",
]


class ImpeccableAccessibilityFoldTests(unittest.TestCase):
    """The impeccable skill absorbed MIT material from jakubkrehel/skills.

    Like the remotion port this is a fold-in, not a whole-directory vendor, so
    nothing about it is self-describing: the attribution lives in one NOTICE
    file and the material is only ever reached through SKILL.md's reference
    table. Both are one tidy-up away from disappearing.
    """

    def test_notice_exists_and_names_the_licence(self):
        notice = ACCESSIBILITY / "NOTICE.md"
        self.assertTrue(notice.is_file(), "reference/accessibility/NOTICE.md is missing")
        text = notice.read_text(errors="replace")
        for token in ("jakubkrehel", "MIT", "a673333", "Jakub Krehel"):
            self.assertIn(token, text, f"NOTICE.md lost {token!r}")
        # MIT is only satisfied if the permission notice itself travels.
        self.assertIn("Permission is hereby granted", text)
        self.assertIn("WITHOUT WARRANTY OF ANY KIND", text)

    def test_skill_md_keeps_the_attribution(self):
        text = (IMPECCABLE / "SKILL.md").read_text(errors="replace")
        self.assertIn("jakubkrehel/skills", text, "SKILL.md lost the attribution link")
        self.assertIn("MIT", text, "SKILL.md lost the licence name")

    def test_every_folded_file_is_present(self):
        for name in FOLDED_VERBATIM + ["forms.md"]:
            with self.subTest(file=name):
                self.assertTrue(
                    (ACCESSIBILITY / name).is_file(),
                    f"reference/accessibility/{name} is missing",
                )
        self.assertTrue(
            (IMPECCABLE / "reference" / "accessibility.md").is_file(),
            "reference/accessibility.md, the entry document, is missing",
        )

    def test_entry_doc_is_reachable_from_skill_md(self):
        """This skill ships no scripts, so SKILL.md's table is the only way in.

        An unlisted reference doc is never read: the loader only injects the
        skill's one-line description, and the table is what says which file to
        open next. Orphaning it costs the whole fold-in with no error anywhere.
        """
        text = (IMPECCABLE / "SKILL.md").read_text(errors="replace")
        self.assertIn(
            "reference/accessibility.md",
            text,
            "SKILL.md no longer points at the accessibility reference doc",
        )

    def test_no_sibling_skill_pointers_survive(self):
        """Upstream is seven skills that cross-reference each other by name.

        Only the accessibility one was taken, so a surviving `better-colors` or
        `better-typography` pointer sends the reader to a skill that does not
        exist here. Re-pulling upstream reintroduces every one of them.
        """
        allowed = {"better-accessibility"}  # the source's own name, in NOTICE.md
        for md in sorted((IMPECCABLE / "reference").rglob("*.md")):
            text = md.read_text(errors="replace")
            if md.name == "NOTICE.md":
                # The notice lists what was left behind by name, on purpose.
                continue
            for found in set(re.findall(r"better-[a-z]+", text)) - allowed:
                with self.subTest(file=md.name, pointer=found):
                    self.fail(
                        f"{md.relative_to(IMPECCABLE)} points at `{found}`, a skill "
                        "that does not exist here"
                    )

    def test_entry_doc_has_no_yaml_frontmatter(self):
        """Upstream ships a `---` block; a re-pull would put it back."""
        first = (
            (IMPECCABLE / "reference" / "accessibility.md")
            .read_text(errors="replace")
            .lstrip()
            .splitlines()[0]
        )
        self.assertTrue(
            first.startswith("# "),
            f"accessibility.md must open with a markdown title, got {first!r}",
        )

    def test_every_internal_doc_reference_resolves(self):
        """A backticked or linked `.md` path that is not there is a dead end.

        Nothing reads these files but the model, so a broken pointer produces
        no error: it produces a confident answer built on a document that was
        never opened. Three such paths existed in the first draft of NOTICE.md,
        which is why this guard is here.
        """
        checked = 0
        for md in sorted(IMPECCABLE.rglob("*.md")):
            text = md.read_text(errors="replace")
            targets = [
                t
                for t in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text)
                if not t.startswith(("http://", "https://", "#", "mailto:"))
            ]
            targets += re.findall(r"`((?:\.\./)*[a-z0-9/-]+\.md)`", text)
            for target in targets:
                checked += 1
                with self.subTest(file=md.name, target=target):
                    self.assertTrue(
                        (md.parent / target).resolve().is_file(),
                        f"{md.relative_to(IMPECCABLE)} points at {target}, "
                        "which does not exist",
                    )
        self.assertGreater(checked, 20, "the reference cross-links look truncated")

    def test_animation_fold_notice_names_the_licence(self):
        """The emilkowalski/skills fold keeps its notice inside the vendored
        file itself (there is no subfolder to hold a NOTICE.md)."""
        vocab = IMPECCABLE / "reference" / "animation-vocabulary.md"
        self.assertTrue(vocab.is_file(), "reference/animation-vocabulary.md is missing")
        text = vocab.read_text(errors="replace")
        for token in ("emilkowalski", "MIT", "78761e1", "Emil Kowalski"):
            self.assertIn(token, text, f"animation-vocabulary.md lost {token!r}")
        # MIT is only satisfied if the permission notice itself travels.
        self.assertIn("Permission is hereby granted", text)
        self.assertIn("WITHOUT WARRANTY OF ANY KIND", text)

    def test_animation_fold_skill_md_keeps_attribution_and_reachability(self):
        """Same failure mode as the accessibility fold: this skill ships no
        scripts, so a reference doc SKILL.md never names is never read."""
        text = (IMPECCABLE / "SKILL.md").read_text(errors="replace")
        self.assertIn("emilkowalski/skills", text, "SKILL.md lost the attribution link")
        self.assertIn(
            "reference/animation-vocabulary.md",
            text,
            "SKILL.md no longer points at the animation vocabulary",
        )

    def test_animation_vocabulary_glossary_is_whole(self):
        """91 terms in 12 categories at fold time. A truncated re-pull or an
        overeager tidy would shrink these counts silently; the glossary's value
        is coverage, so shrinkage is breakage."""
        text = (IMPECCABLE / "reference" / "animation-vocabulary.md").read_text(
            errors="replace"
        )
        self.assertTrue(
            text.lstrip().startswith("# "),
            "animation-vocabulary.md must open with a markdown title",
        )
        glossary = text.split("## Glossary", 1)[1].split("## Source and Licence", 1)[0]
        terms = re.findall(r"^- \*\*(.+?)\*\*: ", glossary, re.M)
        categories = re.findall(r"^### ", glossary, re.M)
        self.assertEqual(len(terms), 91, "glossary terms went missing or gained")
        self.assertEqual(len(categories), 12, "glossary categories changed")
        for ch in ("—", "–"):
            self.assertNotIn(
                ch, text,
                "a dash survived in animation-vocabulary.md; the fold's documented "
                "punctuation rule (no em or en dashes) has been undone",
            )

    def test_motion_design_carries_the_folded_sections(self):
        """The other half of the fold: three sections adapted into
        motion-design.md. Losing one silently narrows the skill's judgment."""
        text = (IMPECCABLE / "reference" / "motion-design.md").read_text(
            errors="replace"
        )
        for heading in (
            "## Should It Animate At All?",
            "## Origin: Where Motion Starts From",
            "## Interruptibility",
        ):
            self.assertIn(heading, text, f"motion-design.md lost {heading!r}")
        self.assertIn("emilkowalski/skills", text, "motion-design.md lost its source note")

    def test_the_reconciliation_section_still_stands(self):
        """interaction-design.md predates this material and is looser in three
        places (focus rings, validation timing, hit-area thresholds). The
        entry doc names all three and says which wins. Without it the skill
        holds two contradictory answers and neither is marked authoritative.
        """
        text = (IMPECCABLE / "reference" / "accessibility.md").read_text(
            errors="replace"
        )
        self.assertIn("interaction-design.md", text)
        for topic in ("Focus rings", "Validation timing", "Hit areas"):
            self.assertIn(topic, text, f"the reconciliation lost {topic!r}")

    def test_no_donor_machine_paths_ride_along(self):
        """MOM ships to strangers, and this skill exists on the donor box too.

        The donor's copy of SKILL.md points at `~/claude-telegram-bot/skills/
        impeccable/reference/`, an absolute path into a private repo. A future
        re-sync that copies the file wholesale instead of patching the
        changeset would ship that path to every stranger who installs MOM.
        impeccable is deliberately not in PORTED_SKILLS above, because that
        list also demands a deps.json and this skill is pure markdown with no
        dependencies, so the guard is repeated here at the right scope.
        """
        for md in sorted(IMPECCABLE.rglob("*.md")):
            text = md.read_text(errors="replace").lower()
            for marker in DONOR_MARKERS:
                with self.subTest(file=md.name, marker=marker):
                    self.assertNotIn(
                        marker,
                        text,
                        f"{md.relative_to(REPO)} names the donor machine",
                    )


MASCOT = SKILLS / "mascot"


class MascotPortTests(unittest.TestCase):
    """The mascot skill is instruction text only, so nothing about it fails loudly.

    Its one executable surface is the image-gen aliases its commands name, and its
    one load-bearing craft rule is the instruction never to tell the generator it
    is drawing a logo (models answer that word with flat clip art and invented
    text). Both are a tidy-up away from disappearing with no error anywhere.

    Like impeccable it is deliberately outside PORTED_SKILLS, which also demands a
    deps.json, so the donor-machine guard is repeated here at the right scope.
    """

    @staticmethod
    def _load_generate():
        """Read the wrapper's real alias table without needing its dependencies.

        generate.py imports httpx at module level, and skills install their own
        dependencies on first use, so on a fresh install httpx is absent and a
        plain exec_module turns this text check into a red suite. Stubbing the
        import keeps the table real: nothing here makes a request.
        """
        import importlib.util
        import types

        stubbed = []
        for name in ("httpx",):
            if name in sys.modules:
                continue
            try:
                importlib.import_module(name)
            except ImportError:
                sys.modules[name] = types.ModuleType(name)
                stubbed.append(name)
        try:
            spec = importlib.util.spec_from_file_location(
                "gen_mod", SKILLS / "image-gen" / "scripts" / "generate.py"
            )
            gen = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(gen)
        finally:
            for name in stubbed:
                sys.modules.pop(name, None)
        return gen

    def test_named_image_models_still_resolve(self):
        gen = self._load_generate()
        text = (MASCOT / "SKILL.md").read_text(errors="replace")
        for alias in ("gpt", "nano-pro", "nano2"):
            with self.subTest(alias=alias):
                self.assertIn(f"`{alias}`", text, f"SKILL.md stopped naming {alias}")

        # Read the aliases out of the document rather than trusting the list
        # above: a table row or an example command that names a model the
        # wrapper does not know fails at run time with "unknown model".
        named = set(re.findall(r"-m ([A-Za-z0-9._-]+)", text))
        named |= {row for row in re.findall(r"^\| `([a-z0-9-]+)` \|", text, re.M)}
        self.assertGreaterEqual(len(named), 3, "no model aliases found in SKILL.md")
        for alias in sorted(named):
            with self.subTest(alias=alias):
                self.assertIn(
                    alias, gen.MODEL_ALIASES,
                    f"SKILL.md tells the model to run -m {alias}, which generate.py "
                    "does not know",
                )

    def test_the_never_say_logo_rule_survives(self):
        text = (MASCOT / "SKILL.md").read_text(errors="replace")
        self.assertIn("Never tell the image model that the image is a logo", text)
        self.assertIn("Create one complete full-bleed 1:1 square image.", text)

    def test_mit_permission_notice_travels(self):
        licence = (MASCOT / "LICENSE").read_text(errors="replace")
        self.assertIn("Permission is hereby granted", licence)
        self.assertIn("WITHOUT WARRANTY OF ANY KIND", licence)
        self.assertIn("s1dashu", licence)

    def test_no_donor_machine_paths_ride_along(self):
        for md in sorted(MASCOT.rglob("*.md")):
            text = md.read_text(errors="replace").lower()
            for marker in DONOR_MARKERS:
                with self.subTest(file=md.name, marker=marker):
                    self.assertNotIn(
                        marker, text, f"{md.relative_to(REPO)} names the donor machine"
                    )


if __name__ == "__main__":
    unittest.main()
