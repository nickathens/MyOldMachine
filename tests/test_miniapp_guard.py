"""Offline tests for utils/miniapp_guard.py (self-fixing App button + outside-in
Mini App health check).

No network, no tailscale, no Telegram. Every Bot API call, funnel probe, and
public load is mocked; state round-trips through a temporary repo dir.
"""

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from utils import miniapp_guard as mg


def _temp_repo(users=("111", "222"), token="123:abc") -> Path:
    """A throwaway repo dir with data/users.json and .env in the real shape."""
    repo = Path(tempfile.mkdtemp(prefix="mg_test_"))
    data = repo / "data"
    data.mkdir()
    (data / "users.json").write_text(
        json.dumps({uid: {"name": f"u{uid}", "role": "admin"} for uid in users}),
        encoding="utf-8",
    )
    if token:
        (repo / ".env").write_text(f"TELEGRAM_BOT_TOKEN={token}\n", encoding="utf-8")
    return repo


def _fake_urlopen(status=200, body=b"", headers=None):
    resp = mock.MagicMock()
    resp.status = status
    resp.headers = headers or {}
    resp.read = lambda n=-1: body if n < 0 else body[:n]
    resp.__enter__ = lambda s: resp
    resp.__exit__ = lambda s, *a: False
    return resp


class TestDetection(unittest.TestCase):
    def test_live_funnel_beats_recorded(self):
        repo = _temp_repo()
        mg.save_state(repo, {"public_url": "https://old.ts.net"})
        with mock.patch.object(mg, "funnel_url", return_value="https://new.ts.net"):
            url, source = mg.detect_public_url(repo)
        self.assertEqual((url, source), ("https://new.ts.net", "tailscale-funnel"))

    def test_recorded_when_no_funnel(self):
        repo = _temp_repo()
        mg.save_state(repo, {"public_url": "https://dash.example.com/"})
        with mock.patch.object(mg, "funnel_url", return_value=None):
            url, source = mg.detect_public_url(repo)
        self.assertEqual((url, source), ("https://dash.example.com", "recorded"))

    def test_none_when_nothing(self):
        repo = _temp_repo()
        with mock.patch.object(mg, "funnel_url", return_value=None):
            url, source = mg.detect_public_url(repo)
        self.assertEqual((url, source), (None, "none"))

    def test_funnel_parse(self):
        out = "# Funnel on:\n#     - https://mac.tailnet.ts.net (Funnel on)\n"
        proc = mock.MagicMock(returncode=0, stdout=out)
        with mock.patch.object(mg, "_tailscale_bin", return_value="/usr/bin/tailscale"), \
             mock.patch.object(mg.subprocess, "run", return_value=proc):
            self.assertIsNone(mg.funnel_url())
        # The URL line must start with https:// after strip, as tailscale prints it.
        proc2 = mock.MagicMock(returncode=0, stdout="https://mac.tailnet.ts.net (Funnel on)\n")
        with mock.patch.object(mg, "_tailscale_bin", return_value="/usr/bin/tailscale"), \
             mock.patch.object(mg.subprocess, "run", return_value=proc2):
            self.assertEqual(mg.funnel_url(), "https://mac.tailnet.ts.net")


class TestPublicLoad(unittest.TestCase):
    def test_ok_with_marker(self):
        with mock.patch.object(mg.urllib.request, "urlopen",
                               return_value=_fake_urlopen(200, b"<script src='telegram-web-app.js'>")):
            ok, detail = mg.check_public_load("https://x.example")
        self.assertTrue(ok)
        self.assertIn("app content confirmed", detail)

    def test_200_without_marker_fails(self):
        with mock.patch.object(mg.urllib.request, "urlopen",
                               return_value=_fake_urlopen(200, b"<html>tunnel error page</html>")):
            ok, detail = mg.check_public_load("https://x.example")
        self.assertFalse(ok)
        self.assertIn("marker missing", detail)

    def test_http_error_fails(self):
        err = urllib.error.HTTPError("https://x.example", 530, "boom", {}, io.BytesIO(b""))
        with mock.patch.object(mg.urllib.request, "urlopen", side_effect=err):
            ok, detail = mg.check_public_load("https://x.example")
        self.assertFalse(ok)
        self.assertIn("530", detail)

    def test_unreachable_fails(self):
        with mock.patch.object(mg.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("down")):
            ok, detail = mg.check_public_load("https://x.example")
        self.assertFalse(ok)
        self.assertIn("unreachable", detail)


class TestButtons(unittest.TestCase):
    def _ensure(self, current_by_user, set_ok=True, fix=True):
        def fake_get(token, tid):
            val = current_by_user.get(tid, "__unreadable__")
            if val == "__unreadable__":
                return False, None
            return True, val

        with mock.patch.object(mg, "get_button_url", side_effect=fake_get), \
             mock.patch.object(mg, "set_button", return_value=set_ok) as setter:
            summary = mg.ensure_buttons("tok", list(current_by_user), "https://live.ts.net/", fix=fix)
        return summary, setter

    def test_matching_button_ok_trailing_slash_normalized(self):
        summary, setter = self._ensure({"111": "https://live.ts.net"})
        self.assertEqual(summary["ok"], ["111"])
        setter.assert_not_called()

    def test_stale_button_repointed(self):
        summary, setter = self._ensure({"111": "https://old.ts.net"})
        self.assertEqual(summary["fixed"], ["111"])
        setter.assert_called_once_with("tok", "111", "https://live.ts.net/")

    def test_missing_button_gets_registered(self):
        summary, _ = self._ensure({"111": None})
        self.assertEqual(summary["fixed"], ["111"])

    def test_fix_off_only_classifies(self):
        summary, setter = self._ensure({"111": "https://old.ts.net"}, fix=False)
        self.assertEqual(summary["stale"], ["111"])
        setter.assert_not_called()

    def test_set_failure_reported(self):
        summary, _ = self._ensure({"111": "https://old.ts.net"}, set_ok=False)
        self.assertEqual(summary["failed"], ["111"])

    def test_unreadable_reported(self):
        summary, _ = self._ensure({"111": "__unreadable__"})
        self.assertEqual(summary["unreadable"], ["111"])


class TestStartupGuard(unittest.TestCase):
    def test_drift_fixed_and_recorded(self):
        repo = _temp_repo(users=("111",))
        mg.save_state(repo, {"public_url": "https://old.ts.net"})
        with mock.patch.object(mg, "funnel_url", return_value="https://new.ts.net"), \
             mock.patch.object(mg, "get_button_url", return_value=(True, "https://old.ts.net")), \
             mock.patch.object(mg, "set_button", return_value=True):
            outcome = mg.run_startup_guard(repo)
        self.assertEqual(outcome["fixed"], ["111"])
        state = mg.load_state(repo)
        self.assertEqual(state["public_url"], "https://new.ts.net")
        self.assertEqual(state["last_startup"]["fixed"], ["111"])

    def test_no_url_is_calm(self):
        repo = _temp_repo()
        with mock.patch.object(mg, "funnel_url", return_value=None):
            outcome = mg.run_startup_guard(repo)
        self.assertIn("no public URL detectable", outcome["detail"])
        self.assertEqual(outcome["fixed"], [])

    def test_no_token_is_calm(self):
        repo = _temp_repo(token=None)
        mg.save_state(repo, {"public_url": "https://dash.example.com"})
        # Pin the environment: a dev machine running the suite may export a
        # real TELEGRAM_BOT_TOKEN, and this test must never reach the network.
        with mock.patch.dict(mg.os.environ, {"TELEGRAM_BOT_TOKEN": ""}), \
             mock.patch.object(mg, "funnel_url", return_value=None):
            outcome = mg.run_startup_guard(repo)
        self.assertIn("token unavailable", outcome["detail"])


class TestReportSection(unittest.TestCase):
    def test_not_installed_yields_empty(self):
        repo = _temp_repo()
        with mock.patch("install.miniapp_setup.is_miniapp_configured", return_value=False):
            self.assertEqual(mg.build_report_section(repo), [])

    def test_healthy_report(self):
        repo = _temp_repo(users=("111",))
        mg.save_state(repo, {"public_url": "https://dash.example.com"})
        with mock.patch.object(mg, "funnel_url", return_value=None), \
             mock.patch.object(mg, "check_public_load", return_value=(True, "HTTP 200, app content confirmed")), \
             mock.patch.object(mg, "get_button_url", return_value=(True, "https://dash.example.com")):
            lines = mg.build_report_section(repo)
        text = "\n".join(lines)
        self.assertIn("Public URL: https://dash.example.com", text)
        self.assertIn("Loads from the internet: OK", text)
        self.assertIn("App button: OK for 1 user(s)", text)

    def test_dead_tunnel_reported_plainly(self):
        repo = _temp_repo(users=("111",))
        mg.save_state(repo, {"public_url": "https://dash.example.com"})
        with mock.patch.object(mg, "funnel_url", return_value=None), \
             mock.patch.object(mg, "check_public_load", return_value=(False, "unreachable (URLError)")), \
             mock.patch.object(mg, "get_button_url", return_value=(True, "https://dash.example.com")):
            lines = mg.build_report_section(repo)
        self.assertIn("Loads from the internet: FAILED (unreachable (URLError))", "\n".join(lines))

    def test_stale_button_selfhealed_in_report(self):
        repo = _temp_repo(users=("111",))
        mg.save_state(repo, {"public_url": "https://dash.example.com"})
        with mock.patch.object(mg, "funnel_url", return_value="https://new.ts.net"), \
             mock.patch.object(mg, "check_public_load", return_value=(True, "HTTP 200, app content confirmed")), \
             mock.patch.object(mg, "get_button_url", return_value=(True, "https://dash.example.com")), \
             mock.patch.object(mg, "set_button", return_value=True):
            lines = mg.build_report_section(repo)
        text = "\n".join(lines)
        self.assertIn("was stale for 1 user(s), re-pointed now", text)
        # The live funnel URL becomes the new record.
        self.assertEqual(mg.load_state(repo)["public_url"], "https://new.ts.net")

    def test_startup_fix_mentioned_once(self):
        repo = _temp_repo(users=("111",))
        mg.save_state(repo, {
            "public_url": "https://dash.example.com",
            "last_startup": {"at": "2026-07-18T09:00:00", "fixed": ["111"]},
        })
        patches = dict(
            funnel_url=mock.patch.object(mg, "funnel_url", return_value=None),
            load=mock.patch.object(mg, "check_public_load",
                                   return_value=(True, "HTTP 200, app content confirmed")),
            get=mock.patch.object(mg, "get_button_url",
                                  return_value=(True, "https://dash.example.com")),
        )
        with patches["funnel_url"], patches["load"], patches["get"]:
            first = mg.build_report_section(repo)
            second = mg.build_report_section(repo)
        self.assertIn("Startup fix: button re-pointed for 1 user(s)", "\n".join(first))
        self.assertNotIn("Startup fix", "\n".join(second))

    def test_lan_url_notes_local_check(self):
        repo = _temp_repo(users=("111",))
        mg.save_state(repo, {"public_url": "http://192.168.1.50:8090"})
        with mock.patch.object(mg, "funnel_url", return_value=None), \
             mock.patch.object(mg, "check_public_load", return_value=(True, "HTTP 200, app content confirmed")), \
             mock.patch.object(mg, "get_button_url", return_value=(True, "http://192.168.1.50:8090")):
            lines = mg.build_report_section(repo)
        self.assertIn("LAN URL, checked from this machine", "\n".join(lines))


class TestNightlyReportIntegration(unittest.TestCase):
    """build_report wiring only; the sibling sections are stubbed so the test
    never touches Time Machine, borg, or the scheduler history db."""

    def _report(self, guard_patch):
        from utils import nightly_report
        with mock.patch.object(nightly_report, "_backup_section", return_value=["Backup", "  stub"]), \
             mock.patch.object(nightly_report, "_jobs_section", return_value=["Maintenance jobs", "  stub"]), \
             mock.patch.object(nightly_report, "_system_section", return_value=["System", "  stub"]), \
             guard_patch:
            return nightly_report.build_report()

    def test_section_included_when_guard_returns_lines(self):
        report = self._report(mock.patch.object(
            mg, "build_report_section",
            return_value=["Mini App", "  Loads from the internet: OK (x)"]))
        self.assertIn("Mini App", report)
        self.assertIn("Loads from the internet: OK", report)

    def test_section_omitted_when_not_configured(self):
        report = self._report(mock.patch.object(mg, "build_report_section", return_value=[]))
        self.assertNotIn("Mini App", report)

    def test_guard_crash_degrades_gracefully(self):
        report = self._report(mock.patch.object(
            mg, "build_report_section", side_effect=RuntimeError("boom")))
        self.assertIn("Guard failed to run: boom", report)


class TestStateAndSetup(unittest.TestCase):
    def test_record_setup_round_trip(self):
        repo = _temp_repo()
        mg.record_setup(repo, "https://mac.tailnet.ts.net/", "tailscale")
        state = mg.load_state(repo)
        self.assertEqual(state["public_url"], "https://mac.tailnet.ts.net")
        self.assertEqual(state["tunnel"], "tailscale")
        self.assertTrue(state["updated_at"])

    def test_corrupt_state_is_empty_dict(self):
        repo = _temp_repo()
        (repo / "data" / mg.STATE_FILENAME).write_text("[]", encoding="utf-8")
        self.assertEqual(mg.load_state(repo), {})


if __name__ == "__main__":
    unittest.main()
