"""reverse-engineering skill, HAR importer (har_import.py).

A HAR exported from the browser while logged in lets the web tools analyse a page
behind a login. These tests check:
  - only safe fields are read; credential-looking URL params become REDACTED and
    no cookie/header/body secret reaches the output;
  - response bodies extract (base64-decoded) under the gate, and a hostile URL
    path never escapes the output folder;
  - malformed input and an undecodable body are handled, and the gate refuses an
    unlawful purpose, a blank note and a non-empty folder.

Pure Python, no Docker needed.
"""
import base64
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT.parent / "skills" / "reverse-engineering" / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(f"re_{name}_under_test", str(SCRIPTS / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


har = load("har_import")

JS = b"console.log('hi');// gsap marker"


def build_har():
    return json.dumps({"log": {"version": "1.2", "creator": {"name": "Chrome DevTools"},
        "pages": [{"id": "p1", "title": "https://app.example.com/"}],
        "entries": [
            {"_resourceType": "script",
             "request": {"method": "GET", "url": "https://app.example.com/static/app.js?token=SECRET123&v=5",
                         "headers": [{"name": "Cookie", "value": "session=TOPSECRET"}],
                         "cookies": [{"name": "session", "value": "TOPSECRET"}]},
             "response": {"status": 200, "content": {"mimeType": "application/javascript", "encoding": "base64",
                                                     "text": base64.b64encode(JS).decode(), "size": len(JS)}}},
            {"_resourceType": "xhr",
             "request": {"method": "GET", "url": "https://app.example.com/api/me?apikey=abc"},
             "response": {"status": 200, "content": {"mimeType": "application/json", "text": '{"user":"anon"}',
                                                     "size": 15}}},
            {"request": {"method": "GET", "url": "https://evil.test/../../etc/passwd"},
             "response": {"status": 200, "content": {"mimeType": "text/plain", "text": "PWNED", "size": 5}}},
        ]}})


def scratch(test, prefix):
    """A temp folder removed when the test ends (these tests once left one in /tmp per run)."""
    d = tempfile.mkdtemp(prefix=prefix)
    test.addCleanup(shutil.rmtree, d, True)
    return d


class Parse(unittest.TestCase):
    def setUp(self):
        self.rep = har.parse(build_har())

    def test_summary_and_types(self):
        self.assertEqual(self.rep["summary"]["total"], 3)
        self.assertEqual(self.rep["summary"]["by_type"]["script"], 1)
        self.assertEqual(self.rep["summary"]["by_type"]["xhr"], 1)

    def test_url_secrets_are_redacted(self):
        js = next(e for e in self.rep["entries"] if e["resource_type"] == "script")
        self.assertIn("token=REDACTED", js["url"])
        self.assertNotIn("SECRET123", js["url"])
        api = next(e for e in self.rep["entries"] if e["resource_type"] == "xhr")
        self.assertIn("apikey=REDACTED", api["url"])

    def test_no_cookie_or_header_secret_anywhere(self):
        blob = json.dumps(self.rep)
        self.assertNotIn("TOPSECRET", blob)
        self.assertNotIn("Cookie", blob)
        self.assertNotIn("session=", blob)


class RedactUnit(unittest.TestCase):
    def test_secret_params_redacted_others_kept(self):
        out = har.redact_url("https://x/y?password=hunter2&page=3&access_token=zzz")
        self.assertIn("password=REDACTED", out)
        self.assertIn("access_token=REDACTED", out)
        self.assertIn("page=3", out)
        self.assertNotIn("hunter2", out)

    def test_no_query_is_unchanged(self):
        self.assertEqual(har.redact_url("https://x/y/z.js"), "https://x/y/z.js")

    def test_safe_rel_strips_dotdot(self):
        rel = har._safe_rel("https://evil.test/../../etc/passwd")
        self.assertNotIn("..", rel.split(os.sep))
        self.assertTrue(rel.endswith(os.path.join("etc", "passwd")))


class Bodies(unittest.TestCase):
    def test_bad_base64_body_is_skipped(self):
        self.assertIsNone(har._body_bytes({"text": "!!!not base64!!!", "encoding": "base64"}))

    def test_plain_text_body(self):
        self.assertEqual(har._body_bytes({"text": "abc"}), b"abc")


class Extract(unittest.TestCase):
    def setUp(self):
        self.har = build_har()
        self.out = scratch(self, "har-")
        os.rmdir(self.out)

    def test_bodies_extracted_and_decoded(self):
        written, skipped = har.extract(self.har, self.out, "own_work", "reading my own app session")
        app = next(p for p in written if p.endswith("app.js"))
        self.assertEqual(Path(app).read_bytes(), JS)
        self.assertEqual(skipped, [])

    def test_hostile_path_stays_inside(self):
        written, _ = har.extract(self.har, self.out, "own_work", "path safety")
        root = os.path.realpath(self.out)
        for dp, _, fs in os.walk(root):
            for f in fs:
                self.assertTrue(os.path.realpath(os.path.join(dp, f)).startswith(root + os.sep))
        # the ../../etc/passwd entry landed under the output (the .. stripped), not at the real /etc
        evil = next(p for p in written if p.endswith("passwd"))
        self.assertTrue(os.path.realpath(evil).startswith(root + os.sep))
        self.assertEqual(Path(evil).read_bytes(), b"PWNED")

    def test_body_over_the_cap_is_skipped(self):
        old = har.MAX_BODY
        har.MAX_BODY = 4
        try:
            written, skipped = har.extract(self.har, self.out, "own_work", "cap test")
            self.assertTrue(any("cap" in why for _, why in skipped))
        finally:
            har.MAX_BODY = old

    def test_gate_refusals(self):
        for purpose, note in (("piracy", "x"), ("own_work", "  ")):
            with self.assertRaises(har.HarError):
                har.extract(self.har, tempfile.mktemp(prefix="har-g-"), purpose, note)

    def test_gate_refuses_non_empty_folder(self):
        os.makedirs(self.out)
        Path(self.out, "x").write_text("here")
        with self.assertRaises(har.HarError):
            har.extract(self.har, self.out, "own_work", "reading my app")

    def test_lawful_purposes_from_schema(self):
        schema = json.loads((SCRIPTS / "dossier-schema.json").read_text())
        enum = schema["dossier_schema"]["properties"]["lawful_basis"]["properties"]["purpose"]["enum"]
        self.assertEqual(har.lawful_purposes(), enum)


class Malformed(unittest.TestCase):
    def test_not_json(self):
        with self.assertRaises(har.HarError):
            har.parse("<html>not har</html>")

    def test_no_entries(self):
        with self.assertRaises(har.HarError):
            har.parse(json.dumps({"log": {"version": "1.2"}}))

    def test_entries_not_a_list(self):
        # a non-list 'entries' is iterable garbage; only the list check refuses it
        with self.assertRaises(har.HarError):
            har.parse(json.dumps({"log": {"entries": "oops"}}))


if __name__ == "__main__":
    unittest.main()
