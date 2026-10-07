#!/usr/bin/env python3
"""Greek set in capitals by the Remotion engine drops its tonos.

CSS text-transform: uppercase follows the element's language, and Remotion's
page is <html lang="en">, so a subtitle «Δελτίο αποστολής» drew ΔΕΛΤΊΟ
ΑΠΟΣΤΟΛΉΣ. Greek capitals carry no tonos, and a tonos that kept two vowels
apart becomes a diaeresis (τσάι ΤΣΑΪ); Chromium applies both rules only under
lang="el". Every element that sets textTransform uppercase now carries
lang={capsLang(text)} (src/font.ts).

The structural test always runs. The renders take about a minute and run only
with REMOTION_TESTS=1: each pair renders the same frame from lower-case Greek
and from correctly typed capitals, and the two must be pixel identical.
"""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENGINE = REPO / "skills" / "remotion" / "render-engine"
SRC = ENGINE / "src"

LIVE = os.environ.get("REMOTION_TESTS") == "1" and (ENGINE / "node_modules").exists()


class EveryCapitalisedElementNamesItsLanguage(unittest.TestCase):
    def test_each_uppercase_style_has_a_lang_attribute(self):
        found = 0
        for tsx in sorted(SRC.glob("*.tsx")):
            text = tsx.read_text(encoding="utf-8")
            caps = len(re.findall(r"textTransform:\s*['\"]uppercase['\"]", text))
            if not caps:
                continue
            found += caps
            langs = len(re.findall(r"lang=\{capsLang\(", text))
            with self.subTest(file=tsx.name):
                self.assertGreaterEqual(
                    langs, caps,
                    f"{tsx.name}: {caps} element(s) set in capitals by CSS but "
                    f"{langs} carry lang={{capsLang(...)}}; Greek there keeps its tonos",
                )
        self.assertGreater(found, 0, "no capitalised element found; the scan is broken")

    def test_caps_lang_marks_greek_only(self):
        font = (SRC / "font.ts").read_text(encoding="utf-8")
        self.assertIn("export const capsLang", font)
        self.assertIn("\\u0370-\\u03FF\\u1F00-\\u1FFF", font)


def render_still(comp, frame, props, out):
    subprocess.run(
        ["node", "still.mjs", "--comp", comp, "--frame", str(frame),
         "--props", json.dumps(props, ensure_ascii=False), "--out", str(out)],
        cwd=ENGINE, check=True, capture_output=True, timeout=240,
    )


@unittest.skipUnless(LIVE, "renders with Remotion; set REMOTION_TESTS=1")
class GreekCapitalsRenderLikeTypedCapitals(unittest.TestCase):
    CASES = [
        ("TitleCard", 120,
         {"title": "ΚΑΛΗΜΕΡΑ", "subtitle": "Δελτίο αποστολής τσάι άυλος"},
         {"title": "ΚΑΛΗΜΕΡΑ", "subtitle": "ΔΕΛΤΙΟ ΑΠΟΣΤΟΛΗΣ ΤΣΑΪ ΑΫΛΟΣ"}),
        ("MetricStomp", 120,
         {"label": "Αποστολές τσάι", "caption": "Δελτίο άυλος"},
         {"label": "ΑΠΟΣΤΟΛΕΣ ΤΣΑΪ", "caption": "ΔΕΛΤΙΟ ΑΫΛΟΣ"}),
        ("BarChartBuild", 150,
         {"data": [{"label": "Κάμερα", "value": 42}, {"label": "Ήχος", "value": 11}]},
         {"data": [{"label": "ΚΑΜΕΡΑ", "value": 42}, {"label": "ΗΧΟΣ", "value": 11}]}),
    ]

    def test_lower_case_greek_renders_as_typed_capitals(self):
        from PIL import Image, ImageChops

        with tempfile.TemporaryDirectory() as tmp:
            for comp, frame, lower, upper in self.CASES:
                with self.subTest(comp=comp):
                    a, b = Path(tmp, f"{comp}_lower.png"), Path(tmp, f"{comp}_upper.png")
                    render_still(comp, frame, lower, a)
                    render_still(comp, frame, upper, b)
                    diff = ImageChops.difference(
                        Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
                    ).getbbox()
                    self.assertIsNone(
                        diff, f"{comp}: capitals from lower case differ from typed capitals in {diff}"
                    )


class TheEngineWorksFromItsOwnFolder(unittest.TestCase):
    """Remotion caches its headless browser relative to the WORKING folder.

    getDownloadsCacheDir() walks up from process.cwd() to a package.json and
    uses its node_modules/.remotion, or <cwd>/.remotion when there is none. The
    SKILL runs `node $ENGINE/render.mjs` from the bot's folder, which has no
    package.json, so a second 92 MB browser landed in the repo root (220 MB on
    disk, hidden by a .gitignore line). Both entry points now resolve the
    paths they were given and then change to the engine folder.
    """

    def test_both_entry_points_change_folder_after_resolving_paths(self):
        for name in ("render.mjs", "still.mjs"):
            text = (ENGINE / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertIn("process.chdir(__dirname);", text)
                self.assertLess(
                    text.index("path.resolve(values.out)"),
                    text.index("process.chdir(__dirname);"),
                    "the output path must be resolved against the caller's folder first",
                )

    @unittest.skipUnless(LIVE, "renders with Remotion; set REMOTION_TESTS=1")
    def test_a_render_from_elsewhere_downloads_nothing_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                ["node", str(ENGINE / "still.mjs"), "--comp", "TitleCard",
                 "--frame", "60", "--out", "rel.png"],
                cwd=tmp, check=True, capture_output=True, text=True, timeout=240,
            )
            self.assertFalse(Path(tmp, ".remotion").exists(),
                             "a browser was downloaded into the caller's folder")
            self.assertTrue(Path(tmp, "rel.png").is_file(),
                            "a relative --out must land in the caller's folder")
            self.assertEqual(proc.stdout.strip(), str(Path(tmp, "rel.png").resolve()))


if __name__ == "__main__":
    unittest.main()
