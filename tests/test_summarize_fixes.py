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


if __name__ == "__main__":
    unittest.main()
