"""Unit tests for the idle heavyweight-app sweep in utils.process_reaper.

Skills launch LibreOffice/GIMP/Inkscape/Blender headless and leave them
orphaned as socket daemons holding hundreds of MB. reap_idle_apps closes a
listed app only when it was launched headless/batch (never an interactively
opened GUI copy), is older than a TTL, AND is near-idle on CPU. These tests
feed synthetic `ps` rows and a fake killer, so no real GUI app is needed and
nothing on the host is signalled.
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import process_reaper  # noqa: E402
from utils.process_reaper import (  # noqa: E402
    DEFAULT_IDLE_APPS,
    _is_headless_launch,
    _parse_etime,
    _select_idle_apps,
    reap_idle_apps,
)

TTL = 1200.0     # 20 minutes
CPU_IDLE = 5.0

# --- Full command lines as they actually appear in `ps -axo command=` ------
# Headless/batch launches (skill-spawned) — reapable when old + idle:
SOFFICE_HL = ("/Applications/LibreOffice.app/Contents/MacOS/soffice --headless "
              "--norestore --nologo --accept=socket,host=localhost,port=2002;urp;")
GIMP_HL = "/opt/homebrew/bin/gimp -i -b (script-fu-something) -b (gimp-quit 0)"
INKSCAPE_HL = "/opt/homebrew/bin/inkscape --export-type=png --export-filename=/tmp/o.png in.svg"
BLENDER_HL = "/Applications/Blender.app/Contents/MacOS/Blender -b scene.blend -o /tmp/f -f 1"
# Interactive GUI launches (user opened by hand) — must NEVER be reaped:
SOFFICE_GUI = "/Applications/LibreOffice.app/Contents/MacOS/soffice --calc /Users/nick/book.ods"
BLENDER_GUI = "/Applications/Blender.app/Contents/MacOS/Blender /Users/nick/scene.blend"
GIMP_GUI = "/Applications/GIMP.app/Contents/MacOS/gimp"
# Never-a-match processes:
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --type=renderer"
PYTHON = "/usr/bin/python3.11 train.py --data blender/scene"  # 'blender' only in an arg


def _row(pid, etime, cpu, rss, command):
    return f"{pid} {etime} {cpu} {rss} {command}"


class ParseEtimeTests(unittest.TestCase):
    def test_mm_ss(self):
        self.assertEqual(_parse_etime("02:00"), 120.0)

    def test_hh_mm_ss(self):
        self.assertEqual(_parse_etime("10:48:42"), 10 * 3600 + 48 * 60 + 42)

    def test_dd_hh_mm_ss(self):
        self.assertEqual(_parse_etime("3-01:00:00"), 3 * 86400 + 3600)

    def test_whitespace_tolerated(self):
        self.assertEqual(_parse_etime("  05:00 "), 300.0)

    def test_malformed_returns_none(self):
        for bad in ("", "garbage", "1:2:3:4", "aa:bb", "x-01:00"):
            self.assertIsNone(_parse_etime(bad), bad)


class HeadlessLatchTests(unittest.TestCase):
    def test_headless_markers_detected(self):
        for cmd in (SOFFICE_HL, GIMP_HL, INKSCAPE_HL, BLENDER_HL):
            self.assertTrue(_is_headless_launch(cmd.split()), cmd)

    def test_gui_launches_have_no_marker(self):
        for cmd in (SOFFICE_GUI, BLENDER_GUI, GIMP_GUI):
            self.assertFalse(_is_headless_launch(cmd.split()), cmd)


class SelectIdleAppsTests(unittest.TestCase):
    def setUp(self):
        self.self_pids = {os.getpid(), os.getppid()}

    def _select(self, lines, ttl=TTL, cpu=CPU_IDLE):
        return _select_idle_apps(lines, DEFAULT_IDLE_APPS, ttl, cpu, self.self_pids)

    def test_selects_old_idle_headless_apps(self):
        lines = [
            _row(97349, "10:48:42", "0.0", "298736", SOFFICE_HL),  # select
            _row(333, "45:00", "0.1", "400000", GIMP_HL),          # select
            _row(340, "21:00", "0.0", "220000", INKSCAPE_HL),      # select
        ]
        picked = {c["pid"] for c in self._select(lines)}
        self.assertEqual(picked, {97349, 333, 340})

    def test_spares_interactive_gui_even_when_old_and_idle(self):
        # The whole point of the safety latch: a hand-opened GUI app with
        # unsaved work, idle for hours, is never touched.
        lines = [
            _row(10, "05:00:00", "0.0", "500000", SOFFICE_GUI),
            _row(11, "05:00:00", "0.0", "900000", BLENDER_GUI),
            _row(12, "05:00:00", "0.0", "300000", GIMP_GUI),
        ]
        self.assertEqual(self._select(lines), [])

    def test_skips_young_app(self):
        lines = [_row(111, "02:00", "0.0", "200000", SOFFICE_HL)]
        self.assertEqual(self._select(lines), [])

    def test_skips_busy_app(self):
        # Old headless Blender render at 240% CPU — actively working, spared.
        lines = [_row(222, "30:00", "240.0", "1500000", BLENDER_HL)]
        self.assertEqual(self._select(lines), [])

    def test_skips_unlisted_and_arg_only_matches(self):
        # Chrome is excluded; python only mentions 'blender' in an argument, and
        # its executable basename is python, so it never matches.
        lines = [
            _row(555, "99:00", "0.0", "900000", CHROME),
            _row(444, "99:00", "0.0", "50000", PYTHON),
        ]
        self.assertEqual(self._select(lines), [])

    def test_skips_self(self):
        lines = [_row(os.getpid(), "99:00", "0.0", "100", SOFFICE_HL)]
        self.assertEqual(self._select(lines), [])

    def test_malformed_lines_ignored(self):
        lines = ["garbage", "",
                 _row("notanint", "10:00", "0.0", "1", SOFFICE_HL),
                 _row(900, "40:00", "0.0", "300000", SOFFICE_HL)]
        picked = {c["pid"] for c in self._select(lines)}
        self.assertEqual(picked, {900})

    def test_rss_reported_in_mb(self):
        lines = [_row(97349, "40:00", "0.0", "307200", SOFFICE_HL)]  # 300 MB
        c = self._select(lines)[0]
        self.assertAlmostEqual(c["rss_mb"], 300.0, places=3)


class ReapIdleAppsTests(unittest.TestCase):
    """reap_idle_apps: config gating + kill dispatch, with an injected killer."""

    def _make_killer(self, fail_pids=()):
        killed = []

        async def killer(pid):
            if pid in fail_pids:
                raise PermissionError(f"cannot signal {pid}")
            killed.append(pid)

        return killer, killed

    LINES = [
        _row(97349, "10:48:42", "0.0", "298736", SOFFICE_HL),  # old idle headless -> reap
        _row(333, "45:00", "0.1", "400000", GIMP_HL),          # old idle headless -> reap
        _row(222, "30:00", "240.0", "1500000", BLENDER_HL),    # busy -> spare
        _row(111, "02:00", "0.0", "200000", SOFFICE_HL),       # young -> spare
        _row(10, "05:00:00", "0.0", "500000", SOFFICE_GUI),    # interactive -> spare
        _row(555, "99:00", "0.0", "900000", CHROME),           # unlisted -> spare
    ]

    def test_disabled_config_is_a_noop(self):
        killer, killed = self._make_killer()
        reaped = asyncio.run(reap_idle_apps(
            config={"reap_idle_apps": False},
            ps_lines=self.LINES, killer=killer,
        ))
        self.assertEqual(reaped, [])
        self.assertEqual(killed, [])

    def test_enabled_reaps_only_old_idle_headless(self):
        killer, killed = self._make_killer()
        reaped = asyncio.run(reap_idle_apps(
            config={"reap_idle_apps": True, "reap_idle_app_minutes": 20},
            ps_lines=self.LINES, killer=killer,
        ))
        self.assertEqual(set(killed), {97349, 333})
        self.assertEqual({c["pid"] for c in reaped}, {97349, 333})

    def test_minutes_config_drives_the_ttl(self):
        killer, killed = self._make_killer()
        asyncio.run(reap_idle_apps(
            config={"reap_idle_apps": True, "reap_idle_app_minutes": 1},
            ps_lines=self.LINES, killer=killer,
        ))
        self.assertIn(111, killed)  # 2-min-old now qualifies at a 1-min TTL

    def test_kill_failure_is_isolated(self):
        killer, killed = self._make_killer(fail_pids={97349})
        reaped = asyncio.run(reap_idle_apps(
            config={"reap_idle_apps": True, "reap_idle_app_minutes": 20},
            ps_lines=self.LINES, killer=killer,
        ))
        self.assertEqual(killed, [333])
        self.assertEqual({c["pid"] for c in reaped}, {333})

    def test_default_config_enables_reaping(self):
        killer, killed = self._make_killer()
        asyncio.run(reap_idle_apps(config={}, ps_lines=self.LINES, killer=killer))
        self.assertEqual(set(killed), {97349, 333})


class ListProcessLinesSmokeTest(unittest.TestCase):
    """The real `ps` invocation parses on this host (no killing involved)."""

    def test_ps_snapshot_is_parseable(self):
        lines = asyncio.run(process_reaper._list_process_lines())
        self.assertIsInstance(lines, list)
        parsed = [ln.split(None, 4) for ln in lines]
        self.assertTrue(any(len(p) == 5 for p in parsed),
                        "expected at least one 5-field ps row")


if __name__ == "__main__":
    unittest.main()
