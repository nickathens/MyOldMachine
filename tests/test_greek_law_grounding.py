"""Offline unit tests for the greek-law grounding scripts.

All network is mocked, per the CI contract. beautifulsoup4 is not in
requirements.txt, so HTML extraction tests are skipped when it is absent; the
Diavgeia JSON client, the http_get retry logic and the source registry never
need it and always run.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "greek-law" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _common  # noqa: E402
import diavgeia  # noqa: E402
import eurlex  # noqa: E402
import legal_search  # noqa: E402

try:
    import bs4  # noqa: F401
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


class FakeResponse:
    def __init__(self, *, status_code=200, json_data=None, content=b"", url="http://x"):
        self.status_code = status_code
        self._json = json_data
        self.content = content
        self.url = url

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fake_client(responses, calls, ctor_kwargs):
    """Build a stand-in for httpx.Client that serves queued responses.

    Records constructor kwargs (so header assertions work) and each get URL,
    and supports the context manager protocol http_get uses.
    """
    class _Client:
        def __init__(self, **kwargs):
            ctor_kwargs.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, params=None):
            calls.append(url)
            return responses.pop(0)

    return _Client


class TestHttpGetRetry(unittest.TestCase):
    def test_retries_on_202_then_succeeds(self):
        calls, ctor = [], {}
        responses = [
            FakeResponse(status_code=202, content=b""),
            FakeResponse(status_code=202, content=b""),
            FakeResponse(status_code=200, content=b"ok"),
        ]
        with mock.patch.object(_common.httpx, "Client",
                               _fake_client(responses, calls, ctor)), \
                mock.patch.object(_common.time, "sleep"):
            r = _common.http_get("http://eur-lex", retries=5)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(calls), 3)

    def test_no_retry_on_200(self):
        calls, ctor = [], {}
        responses = [FakeResponse(status_code=200, content=b"ok")]
        with mock.patch.object(_common.httpx, "Client",
                               _fake_client(responses, calls, ctor)), \
                mock.patch.object(_common.time, "sleep"):
            _common.http_get("http://x", retries=5)
        self.assertEqual(len(calls), 1)

    def test_sends_browser_user_agent(self):
        calls, ctor = [], {}
        responses = [FakeResponse(status_code=200)]
        with mock.patch.object(_common.httpx, "Client",
                               _fake_client(responses, calls, ctor)):
            _common.http_get("http://x")
        self.assertIn("Mozilla", ctor["headers"]["User-Agent"])


class TestDiavgeia(unittest.TestCase):
    def test_search_builds_verified_params(self):
        captured = {}

        def fake_get(url, *, params=None, accept_json=False):
            captured["url"] = url
            captured["params"] = params
            return FakeResponse(json_data={"decisions": [], "info": {"total": 0}})

        with mock.patch.object(diavgeia._common, "http_get", side_effect=fake_get):
            diavgeia.search("προμήθεια", size=5, page=1,
                            from_date="2024-01-01", to_date="2024-03-31")
        self.assertEqual(captured["url"], diavgeia.SEARCH_URL)
        self.assertEqual(captured["params"]["q"], "προμήθεια")
        self.assertEqual(captured["params"]["size"], 5)
        self.assertEqual(captured["params"]["page"], 1)
        self.assertEqual(captured["params"]["from_issue_date"], "2024-01-01")
        self.assertEqual(captured["params"]["to_issue_date"], "2024-03-31")

    def test_search_omits_dates_when_absent(self):
        captured = {}

        def fake_get(url, *, params=None, accept_json=False):
            captured["params"] = params
            return FakeResponse(json_data={"decisions": [], "info": {}})

        with mock.patch.object(diavgeia._common, "http_get", side_effect=fake_get):
            diavgeia.search("x")
        self.assertNotIn("from_issue_date", captured["params"])
        self.assertNotIn("to_issue_date", captured["params"])

    def test_get_uses_ada_url(self):
        captured = {}

        def fake_get(url, *, accept_json=False):
            captured["url"] = url
            return FakeResponse(json_data={"decision": {}})

        with mock.patch.object(diavgeia._common, "http_get", side_effect=fake_get):
            diavgeia.get("ΡΨ0Ι-Α7Κ")
        self.assertIn("ΡΨ0Ι-Α7Κ", captured["url"])
        self.assertTrue(captured["url"].startswith("https://diavgeia.gov.gr/"))

    def test_fmt_date_epoch_ms(self):
        # 1 782 432 000 000 ms is 2026-06-26 UTC
        self.assertEqual(diavgeia._fmt_date(1782432000000), "2026-06-26")
        self.assertEqual(diavgeia._fmt_date(None), "")

    def test_summarize_handles_empty(self):
        out = diavgeia.summarize({"decisions": [], "info": {"total": 0}})
        self.assertIn("Σύνολο αποτελεσμάτων: 0", out)
        self.assertIn("Καμία απόφαση", out)

    def test_summarize_lists_decision(self):
        data = {
            "info": {"total": 1},
            "decisions": [{
                "ada": "ΑΒΓ-123", "issueDate": 1782432000000,
                "subject": "Δοκιμή", "organizationId": "999",
                "documentUrl": "https://diavgeia.gov.gr/doc/ΑΒΓ-123",
            }],
        }
        out = diavgeia.summarize(data)
        self.assertIn("ΑΒΓ-123", out)
        self.assertIn("2026-06-26", out)
        self.assertIn("Δοκιμή", out)


class TestEurlexUrl(unittest.TestCase):
    def test_celex_url_construction(self):
        url = eurlex.URL.format(lang="EL", celex="32016R0679")
        self.assertIn("CELEX:32016R0679", url)
        self.assertIn("/EL/", url)

    def test_empty_body_raises(self):
        with mock.patch.object(eurlex._common, "http_get",
                               return_value=FakeResponse(content=b"")):
            with self.assertRaises(RuntimeError):
                eurlex.fetch("32016R0679")


class TestRegistry(unittest.TestCase):
    def test_every_source_well_formed(self):
        ids = set()
        for s in legal_search.SOURCES:
            for key in ("id", "name", "covers", "access", "status", "tool"):
                self.assertIn(key, s)
                self.assertTrue(s[key])
            self.assertIn(s["status"], legal_search.STATUSES)
            ids.add(s["id"])
        self.assertEqual(len(ids), len(legal_search.SOURCES))

    def test_status_classification_matches_what_was_verified(self):
        by_id = {s["id"]: s for s in legal_search.SOURCES}
        self.assertEqual(by_id["diavgeia"]["status"], "verified")
        self.assertEqual(by_id["eurlex"]["status"], "verified")
        self.assertEqual(by_id["enomothesia"]["status"], "verified")
        self.assertEqual(by_id["areiospagos"]["status"], "fetch-only")
        # kodiko and et render statute text with JavaScript, not static HTML
        self.assertEqual(by_id["kodiko"]["status"], "browser-required")
        self.assertEqual(by_id["et"]["status"], "browser-required")

    def test_render_is_text(self):
        out = legal_search.render()
        self.assertIn("Διαύγεια", out)
        self.assertIn("[verified]", out)


@unittest.skipUnless(HAS_BS4, "beautifulsoup4 not installed")
class TestExtraction(unittest.TestCase):
    def test_extract_drops_scripts_and_collapses(self):
        html = (b"<html><head><title>T</title><style>x{}</style></head>"
                b"<body><script>var a=1;</script><p>Alpha</p>"
                b"<p>  Beta  </p></body></html>")
        text = _common.extract_text(html)
        self.assertIn("Alpha", text)
        self.assertIn("Beta", text)
        self.assertNotIn("var a", text)
        self.assertNotIn("x{}", text)

    def test_max_chars_truncates(self):
        html = b"<html><body><p>" + b"A" * 500 + b"</p></body></html>"
        text = _common.extract_text(html, max_chars=100)
        self.assertIn("truncated", text)
        self.assertLess(len(text), 200)

    def test_page_title(self):
        self.assertEqual(
            _common.page_title(b"<html><head><title>Hello</title></head></html>"),
            "Hello",
        )


if __name__ == "__main__":
    unittest.main()
