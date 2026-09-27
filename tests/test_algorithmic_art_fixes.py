"""algorithmic-art fixes, Linux bot review 2026-09-27. Each fails on the code before them.

The browser tests need Playwright's Chromium and the p5 CDN (jsdelivr), as
the skill itself does; they skip without them.
"""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('PIL', 'numpy', 'playwright') if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

SKILL = Path(__file__).resolve().parent.parent / "skills" / "algorithmic-art"
SCAFFOLD = SKILL / "scripts" / "art_scaffold.py"
RENDER = SKILL / "scripts" / "art_render.py"
P5 = "https://cdn.jsdelivr.net/npm/p5@1.9.4/lib/p5.min.js"


def browser_ready():
    try:
        import playwright  # noqa: F401
        urllib.request.urlopen(urllib.request.Request(P5, method="HEAD"), timeout=10)
        return True
    except Exception:
        return False


def documented_blocks():
    """Every sketch SKILL.md shows: the javascript blocks and the quick start heredoc."""
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```javascript\n(.*?)```", text, re.S)
    blocks += re.findall(r"cat > /tmp/sketch\.js <<'EOF'\n(.*?)\nEOF", text, re.S)
    return blocks


class Scaffold(unittest.TestCase):
    def test_title_is_escaped(self):
        spec = importlib.util.spec_from_file_location("art_scaffold", SCAFFOLD)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        page = mod.build(title="</title><script>alert(1)</script>", seed=1, width=10, height=10,
                         setup_body="", draw_body="")
        self.assertNotIn("<script>alert(1)", page)
        self.assertIn("&lt;/title&gt;", page)


@unittest.skipUnless(browser_ready(), "needs Playwright's Chromium and the p5 CDN")
class Render(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def page(self, name, code, *flags, size=240):
        js = self.d / f"{name}.js"
        js.write_text(code, encoding="utf-8")
        out = self.d / f"{name}.html"
        subprocess.run([sys.executable, str(SCAFFOLD), "--draw", str(js), "--width", str(size),
                        "--height", str(size), "-o", str(out), *flags], check=True, capture_output=True)
        return out

    def render(self, html, name, *flags, size=240):
        png = self.d / f"{name}.png"
        r = subprocess.run([sys.executable, str(RENDER), str(html), "-o", str(png), "--width", str(size),
                            "--height", str(size), "--frames", "40", *flags],
                           capture_output=True, text=True, timeout=180)
        return r, png

    def test_every_documented_pattern_draws(self):
        # the particle pattern went through --draw, was nested in draw() and
        # never ran; subdivide() was never called; the quick start and the
        # flow field were near black as stills
        blocks = documented_blocks()
        self.assertGreaterEqual(len(blocks), 6)
        for i, code in enumerate(blocks):
            with self.subTest(block=code.strip().splitlines()[0][:60]):
                r, png = self.render(self.page(f"b{i}", code, "--seed", "5"), f"b{i}")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("WARNING", r.stderr)
                grey = np.asarray(Image.open(png).convert("L"))
                self.assertGreater((grey > 40).mean(), 0.01, "a still that is almost black")

    def test_one_seed_one_still(self):
        particles = next(b for b in documented_blocks() if "particles" in b)
        stills = []
        for name, seed in (("a", "5"), ("b", "5"), ("c", "6")):
            r, png = self.render(self.page(name, particles, "--seed", seed), name)
            self.assertEqual(r.returncode, 0, r.stderr)
            stills.append(np.asarray(Image.open(png).convert("RGBA")))
        self.assertTrue(np.array_equal(stills[0], stills[1]), "one seed rendered two different stills")
        self.assertFalse(np.array_equal(stills[0], stills[2]), "the seed changed nothing")

    def test_p5_that_does_not_load_is_reported(self):
        html = self.page("offline", "background(255);")
        html.write_text(html.read_text(encoding="utf-8").replace(P5, "http://127.0.0.1:9/p5.min.js"),
                        encoding="utf-8")
        r, png = self.render(html, "offline", "--timeout", "30")
        self.assertEqual(r.returncode, 1)
        self.assertIn("p5.js did not load", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertFalse(png.exists())

    def test_sketch_error_is_reported(self):
        r, png = self.render(self.page("err", "background(20);\nellipse(width / 2, height / 2, radious);"), "err")
        self.assertEqual(r.returncode, 1)
        self.assertIn("radious is not defined", r.stderr)

    def test_flat_canvas_warns(self):
        r, png = self.render(self.page("flat", "background(0);"), "flat")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("one flat colour", r.stderr)


if __name__ == "__main__":
    unittest.main()
