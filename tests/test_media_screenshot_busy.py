"""media screenshot.py: a page that never goes network idle is shot once, fast.

Linux bot review 2026-09-27: the script waited 30 s for "networkidle", then
navigated again and shot at DOMContentLoaded, before images had arrived.
"""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('playwright',) if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "media" / "scripts" / "screenshot.py"


class _Counting(SimpleHTTPRequestHandler):
    page_hits = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/busy.html":
            type(self).page_hits += 1
        super().do_GET()


class BusyPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="shot-"))
        (cls.dir / "busy.html").write_text(
            "<html><body style='margin:0;background:#00ff00'>"
            "<script>setInterval(() => fetch('/ping?' + Date.now()), 150);</script>"
            "</body></html>", encoding="utf-8")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Counting, directory=str(cls.dir)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/busy.html"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_shot_without_a_reload_and_within_the_bound(self):
        out = self.dir / "shot.png"
        began = time.monotonic()
        run = subprocess.run([sys.executable, str(SCRIPT), self.url, str(out), "--wait", "0"],
                             capture_output=True, text=True, timeout=120)
        elapsed = time.monotonic() - began
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(out.is_file())
        self.assertEqual(_Counting.page_hits, 1, "the page was loaded more than once")
        self.assertLess(elapsed, 25)


if __name__ == "__main__":
    unittest.main()
