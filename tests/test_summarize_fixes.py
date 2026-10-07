"""summarize_url.py fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('bs4', 'httpx', 'markitdown') if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import functools
import http.server
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "summarize" / "scripts" / "summarize_url.py"

PAGE = ("<html><head><meta charset='windows-1253'><title>Δοκιμή</title></head><body><nav>menu</nav>"
        "<article class='teaser'>Related: short teaser</article><main><article><h1>Κύριο άρθρο</h1>"
        "<p>Πρώτη παράγραφος.</p><p>Δεύτερη παράγραφος.</p>"
        + "".join(f"<p>Paragraph {i} " + "word " * 40 + "</p>" for i in range(80))
        + "</article></main></body></html>")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Summarize(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        Path(cls.tmp.name, "greek.html").write_bytes(PAGE.encode("cp1253"))
        handler = functools.partial(Quiet, directory=cls.tmp.name)
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/greek.html"
        cls.out = subprocess.run([sys.executable, str(SCRIPT), cls.url], capture_output=True, text=True,
                                 timeout=60).stdout

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def test_meta_charset_is_honoured(self):
        self.assertIn("Title: Δοκιμή", self.out)

    def test_the_main_article_wins_over_a_teaser(self):
        self.assertIn("Κύριο άρθρο", self.out)
        self.assertIn("Paragraph 40", self.out)

    def test_paragraphs_stay_on_their_own_lines(self):
        self.assertGreater(self.out.count("\n"), 20)

    def test_a_cut_is_announced(self):
        self.assertIn("[truncated", self.out)


class BigBodies(unittest.TestCase):
    """Linux bot sweep 2026-10-07: httpx.get read the WHOLE body into memory
    before the content type was looked at, so a link to a large video or
    archive was downloaded in full (inside the bot's memory cap) only to be
    called "Unsupported content type". A server that streams a 40 MB body
    counts how much the script really pulled."""

    @classmethod
    def setUpClass(cls):
        cls.sent = {}

        class Big(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                kind = "video/mp4" if self.path.startswith("/video") else "text/html"
                size = 40 * 1024 * 1024
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(size))
                self.end_headers()
                block = (b"<p>x</p>" * 8192) if kind == "text/html" else b"\0" * 65536
                done = 0
                try:
                    while done < size:
                        self.wfile.write(block[: size - done])
                        done += len(block[: size - done])
                except OSError:
                    pass
                cls.sent[self.path] = done

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Big)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _run(self, path):
        r = subprocess.run([sys.executable, str(SCRIPT), self.base + path, "--max-chars", "200"],
                           capture_output=True, text=True, timeout=60)
        for _ in range(50):          # the handler records its count as it ends
            if path in self.sent:
                break
            threading.Event().wait(0.1)
        return r

    def test_an_unsupported_type_is_refused_before_its_body(self):
        r = self._run("/video.mp4")
        self.assertIn("Unsupported content type: video/mp4", r.stdout)
        self.assertLess(self.sent.get("/video.mp4", 0), 8 * 1024 * 1024)

    def test_an_oversized_page_is_refused_not_held(self):
        from importlib import util
        spec = util.spec_from_file_location("summ_cap", str(SCRIPT))
        mod = util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old = mod.MAX_BYTES
        try:
            mod.MAX_BYTES = 4 * 1024 * 1024
            result = mod.fetch_url(self.base + "/page.html")
        finally:
            mod.MAX_BYTES = old
        self.assertIn("error", result)
        self.assertIn("larger than", result["error"])


if __name__ == "__main__":
    unittest.main()
