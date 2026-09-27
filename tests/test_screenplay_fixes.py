"""screenplay skill fixes, Linux bot review 2026-09-27.

Each test runs the real command line in its own process, the way the skill
is used, and fails on the code before the fixes.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "screenplay" / "scripts" / "screenplay.py"

GREEK = """Title: Το Σπίτι
Author: Δοκιμή

.ΕΣΩΤ. ΚΟΥΖΙΝΑ - ΜΕΡΑ

Η ΕΛΕΝΗ κόβει ντομάτες.

ΕΛΕΝΗ
Πού ήσουν όλη νύχτα;
"""

# Known answers: 4 scenes (the cut one in the boneyard does not count, nor
# does the line starting with an ellipsis), 3 locations, 2 characters with 3
# and 2 speeches (a dual dialogue is two speeches).
KNOWN = """Title: Known Answers
Author: Tester
Contact:
    12 Somewhere Street
    Athens

INT. KITCHEN - DAY

ANNA stirs a pot.

ANNA
Where were you?
NO! Don't answer.

BOB
Out.

BOB
Nowhere.

ANNA ^
Liar.

...and then nothing happens.

EXT GARDEN - NIGHT

Crickets. [[Note: add rain?]]

.BASEMENT

A single bulb.

/*
INT. CUT SCENE - DAY

CARL
This scene was cut.
*/

CUT TO:

EXT GARDEN - NIGHT

ANNA
(whispering)
Still here.
"""


def pdf_text(path):
    import pypdf

    return "\n".join(page.extract_text() for page in pypdf.PdfReader(str(path)).pages)


# The command runs in this Python, so its libraries must be here too: the tools
# alone let the class run, and fail, where the test Python has none of them
# (the Mac mini's, review of #187).
_LIBS = [m for m in ("screenplain", "reportlab", "fontTools", "pypdf") if importlib.util.find_spec(m) is None]


@unittest.skipUnless(shutil.which("fc-match") and shutil.which("afterwriting") and not _LIBS,
                     "needs fontconfig, afterwriting, and screenplain, reportlab, fonttools and pypdf in this Python")
class Screenplay(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="sp-test-"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_cli(self, *args, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True,
                              text=True, timeout=300, cwd=self.d, env=env)

    def project(self, title, draft):
        run = self.run_cli("create", title, "--dir", self.d / "p")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        (self.d / "p" / "draft.fountain").write_text(draft, encoding="utf-8")
        return self.d / "p"

    def exported(self, project, suffix=".pdf"):
        files = sorted((project / "exports").glob(f"*{suffix}"))
        self.assertEqual(len(files), 1, files)
        return files[0]

    def test_greek_pdf_keeps_every_letter(self):
        # the standard Courier turned every accented vowel into a black box
        project = self.project("Το Σπίτι", GREEK)
        run = self.run_cli("export", project)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        text = pdf_text(self.exported(project))
        self.assertIn("Πού ήσουν όλη νύχτα;", text)
        self.assertIn("κόβει ντομάτες", text)

    def test_greek_keeps_its_bold_and_italic(self):
        # review of #187: the swapped face printed **bold** and *italic* as
        # regular (registerFont points a TrueType face's styles at itself),
        # and screenplain 0.12 set the Greek in its own Courier Prime, which
        # has no Greek at all
        project = self.project("Έντονα", GREEK + "\nΗ ΕΛΕΝΗ **φωνάζει** και *ψιθυρίζει*.\n")
        run = self.run_cli("export", project)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        pdf = self.exported(project)
        self.assertIn("φωνάζει", pdf_text(pdf))
        import pypdf

        faces = {str(font.get_object()["/BaseFont"])
                 for page in pypdf.PdfReader(str(pdf)).pages
                 for font in page["/Resources"]["/Font"].values()}
        self.assertTrue(any("Bold" in face for face in faces), faces)
        self.assertTrue(any("Italic" in face or "Oblique" in face for face in faces), faces)

    def test_a_word_no_courier_face_has_falls_back(self):
        # afterwriting printed this word as blank space in a delivered script
        project = self.project("Mixed", "INT. ROOM - DAY\n\nCHEN\nThey call us 提示猴. Prompt monkeys.\n")
        run = self.run_cli("export", project)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("提示猴", pdf_text(self.exported(project)))

    def test_lyrics_print_without_the_tilde(self):
        # the Fountain spec prints ~ lines as italic lyrics; screenplain printed the tilde
        project = self.project("Song", "INT. ROOM - DAY\n\nANNA\n~When the morning comes\n")
        run = self.run_cli("export", project)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        text = pdf_text(self.exported(project))
        self.assertIn("When the morning comes", text)
        self.assertNotIn("~", text)

    def test_a4_is_honoured_by_the_default_engine(self):
        project = self.project("Paper", GREEK)
        run = self.run_cli("export", project, "--a4")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        import pypdf

        box = pypdf.PdfReader(str(self.exported(project))).pages[0].mediabox
        self.assertAlmostEqual(float(box.height), 841.9, delta=1)

    def test_afterwriting_refuses_greek(self):
        # its fonts have no Greek; the PDF came out blank with exit 0
        project = self.project("Το Σπίτι", GREEK)
        run = self.run_cli("export", project, "--engine", "afterwriting")
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertEqual(list((project / "exports").glob("*.pdf")), [])

    def test_afterwriting_that_writes_nothing_is_a_failure(self):
        project = self.project("Stale", KNOWN)
        first = self.run_cli("export", project, "--engine", "afterwriting")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        stub = self.d / "bin"
        stub.mkdir()
        (stub / "afterwriting").write_text("#!/bin/sh\necho 'Cannot open script file'\nexit 0\n")
        (stub / "afterwriting").chmod(0o755)
        env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}")
        earlier = self.exported(project)
        kept = earlier.read_bytes()
        again = self.run_cli("export", project, "--engine", "afterwriting", env=env)
        self.assertEqual(again.returncode, 1, "an earlier export passed for a fresh one: " + again.stdout)
        # audit pass 2: the failed run deleted the earlier export first
        self.assertEqual(earlier.read_bytes(), kept, "a failed export destroyed the earlier one")
        self.assertEqual([p.name for p in (project / "exports").iterdir()], [earlier.name])

    def test_afterwriting_flags_without_afterwriting_are_refused(self):
        project = self.project("Flags", KNOWN)
        run = self.run_cli("export", project, "--scene-numbers", "both")
        self.assertEqual(run.returncode, 1, run.stdout)

    def test_title_with_a_slash_exports(self):
        project = self.project("Love/Hate: A Test", KNOWN)
        run = self.run_cli("export", project)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(self.exported(project).name.startswith("love_hate_a_test_"))

    def test_versions_can_be_written_v3(self):
        # SKILL.md showed --version v3 and --v1 v2, which argparse refused
        project = self.project("Versions", KNOWN)
        self.assertEqual(self.run_cli("save", project).returncode, 0)
        with open(project / "draft.fountain", "a", encoding="utf-8") as fh:
            fh.write("\nMore.\n")
        self.assertEqual(self.run_cli("save", project).returncode, 0)
        diff = self.run_cli("diff", project, "--v1", "v1", "--v2", "v2")
        self.assertEqual(diff.returncode, 0, diff.stderr)
        self.assertIn("+More.", diff.stdout)
        restore = self.run_cli("restore", project, "--version", "v1")
        self.assertEqual(restore.returncode, 0, restore.stderr)
        self.assertNotIn("More.", (project / "draft.fountain").read_text(encoding="utf-8"))
        meta = json.loads((project / "metadata.json").read_text())
        # the draft was already saved as v2, so no duplicate auto-save
        self.assertEqual(meta["current_version"], 2)

    def test_create_keeps_an_existing_script(self):
        folder = self.d / "film"
        folder.mkdir()
        (folder / "draft.fountain").write_text("INT. ROOM - DAY\n\nMy only copy.\n", encoding="utf-8")
        run = self.run_cli("create", "Mine", "--dir", folder)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("My only copy.", (folder / "draft.fountain").read_text(encoding="utf-8"))

    def test_analyze_reads_fountain_as_the_spec_does(self):
        project = self.project("Known Answers", KNOWN)
        run = self.run_cli("analyze", project)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        out = run.stdout
        self.assertIn("Scenes: 4", out)
        self.assertIn("Dialogue blocks: 5", out)
        self.assertIn("Characters: 2", out)
        self.assertIn("- ANNA: 3, 9 words", out)
        self.assertIn("- BOB: 2, 2 words", out)
        self.assertIn("Locations: 3", out)
        for wrong in ("CARL", "ANNA ^", "and then nothing", "CUT SCENE"):
            self.assertNotIn(wrong, out)
        self.assertIn("Pages: 1 ", out)


if __name__ == "__main__":
    unittest.main()
