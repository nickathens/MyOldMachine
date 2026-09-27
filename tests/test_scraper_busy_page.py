"""scrape.py: a page that never goes network idle is still scraped.

Linux bot review 2026-09-27, S045: every command waited for "networkidle" with no
fallback, so a page with a beacon or a streaming video died at 30 s.
"""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('playwright',) if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import importlib.util
import tempfile
import threading
import time
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "scraper" / "scripts" / "scrape.py"
spec = importlib.util.spec_from_file_location("scrape_under_test", str(SCRIPT))
scrape = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scrape)


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class BusyPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="scrape-"))
        (cls.dir / "busy.html").write_text(
            "<html><head><title>Busy</title></head><body><p>Still here</p>"
            "<a href='https://example.org/x'>out</a><script>"
            "setInterval(() => fetch('/busy.html?' + Date.now()), 150);"
            "</script></body></html>", encoding="utf-8")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(cls.dir)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/busy.html"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_content_and_links_come_back(self):
        began = time.monotonic()
        content = scrape.get_content(self.url)
        links = scrape.get_links(self.url, external_only=True)
        self.assertTrue(content.get("success"), content)
        self.assertIn("Still here", content["content"])
        self.assertEqual(links.get("count"), 1, links)
        self.assertLess(time.monotonic() - began, 25)


if __name__ == "__main__":
    unittest.main()
