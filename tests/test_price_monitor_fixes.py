"""price-monitor fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('bs4', 'requests') if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import functools
import http.server
import importlib.util
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "price-monitor" / "scripts" / "price_tracker.py"

spec = importlib.util.spec_from_file_location("price_tracker", SCRIPT)
pt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pt)


class ExtractPrice(unittest.TestCase):
    def test_price_formats(self):
        cases = {
            "1.299 €": 1299.0,            # was 1.299
            "Από 1.049€": 1049.0,         # was 1.049
            "1,299": 1299.0,              # was 1.299
            "Τιμή 2 τεμάχια: 49,90 €": 49.9,   # was 2.0
            "1.299,00 €": 1299.0,
            "$1,234,567.89": 1234567.89,
            "12 999,50 €": 12999.5,
            "€49,90": 49.9,
            "0,99€": 0.99,
            "1299.00": 1299.0,
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(pt.extract_price(text), want)


class FetchPrice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "label.html").write_text(
            '<html><body><span class="price">Τιμή</span><span class="price">1.299,00 €</span></body></html>',
            encoding="utf-8")
        (root / "itemprop.html").write_text(
            '<html><body><span itemprop="price" content="849.00">849,00 € με ΦΠΑ</span></body></html>',
            encoding="utf-8")
        handler = functools.partial(QuietHandler, directory=str(root))
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_a_label_before_the_price_is_skipped(self):
        # the first .price is a label; select_one stopped there and found nothing
        self.assertEqual(pt.fetch_price(f"{self.base}/label.html"), 1299.0)

    def test_machine_readable_price_wins(self):
        self.assertEqual(pt.fetch_price(f"{self.base}/itemprop.html"), 849.0)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


if __name__ == "__main__":
    unittest.main()
