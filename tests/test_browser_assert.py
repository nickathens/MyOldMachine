"""browser_assert.py: no_text judges what a reader sees, and a busy page still
gets checked.

Linux bot review 2026-09-27, S047: no_text searched page.content(), the HTML source
with inline scripts, so `--no-text undefined` (the skill's own canonical
example) failed any self contained page whose JavaScript mentions undefined.
Same review, S045 class: navigation waited for "networkidle" with no
fallback, so a page that never stops fetching (a streaming background video,
an analytics beacon) failed with "navigation failed" before any check ran.
"""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('playwright', 'yaml') if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import asyncio
import importlib.util
import tempfile
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "browser" / "scripts" / "browser_assert.py"
spec = importlib.util.spec_from_file_location("browser_assert_under_test", str(SCRIPT))
ba = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ba)


def _run(url, checks, timeout=10):
    run = ba.AssertionRun({"url": url, "timeout": timeout, "checks": checks})
    ok = asyncio.run(run.run())
    return ok, run.results


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class NoTextIsWhatTheReaderSees(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="browser-assert-"))
        (cls.dir / "script.html").write_text(
            "<html><body><p>Treatment</p>"
            "<script>let x = undefined; if (x === undefined) {}</script>"
            "<div style='display:none'>Lorem ipsum</div></body></html>",
            encoding="utf-8",
        )
        (cls.dir / "visible.html").write_text(
            "<html><body><p>Price: undefined</p></body></html>", encoding="utf-8")
        (cls.dir / "busy.html").write_text(
            "<html><body><p>Busy page</p><script>"
            "setInterval(() => fetch('/busy.html?' + Date.now()), 150);"
            "</script></body></html>",
            encoding="utf-8",
        )
        handler = partial(_Quiet, directory=str(cls.dir))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_script_and_hidden_text_do_not_count(self):
        ok, results = _run((self.dir / "script.html").as_uri(),
                           [{"no_text": "undefined"}, {"no_text": "Lorem ipsum"}])
        self.assertTrue(ok, results)

    def test_visible_text_fails(self):
        ok, results = _run((self.dir / "visible.html").as_uri(), [{"no_text": "undefined"}])
        self.assertFalse(ok)
        self.assertIn("present=True", results[0][1])

    def test_page_that_never_goes_idle_is_still_checked(self):
        url = f"http://127.0.0.1:{self.port}/busy.html"
        ok, results = _run(url, [{"http_status": 200}, {"selector_exists": "p"}], timeout=4)
        self.assertTrue(ok, results)
        self.assertTrue(any("never went idle" in msg for _, msg in results), results)


if __name__ == "__main__":
    unittest.main()
