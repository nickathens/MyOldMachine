"""Unit tests for install.voice_agents.

The point of the module is which of two states a machine is in: the voice
models loaded at login and pinned resident, or loaded when someone actually
speaks. These tests cover the reading of that state and the rendering that
restores it. Nothing here calls launchctl, writes to ~/Library/LaunchAgents or
signals a daemon.
"""
from __future__ import annotations

import plistlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install import voice_agents as va  # noqa: E402


class TemplateTests(unittest.TestCase):
    def test_both_templates_ship_with_the_repo(self):
        for engine in va.ENGINES:
            self.assertTrue(engine.template_path.exists(),
                            f"missing template for {engine.label}")

    def test_rendered_template_is_a_valid_plist_with_no_placeholder_left(self):
        for engine in va.ENGINES:
            text = va.render_plist(
                engine.template_path.read_text(encoding="utf-8"), Path("/tmp/repo"))
            self.assertNotIn("{{REPO_DIR}}", text)
            data = plistlib.loads(text.encode("utf-8"))
            self.assertEqual(data["Label"], engine.label)
            self.assertTrue(data["ProgramArguments"][0].startswith("/tmp/repo"))

    def test_the_shipped_templates_are_the_always_warm_ones(self):
        # They exist to restore the old behaviour on request, so they must
        # carry it: start at login, and an idle timeout long enough never to
        # fire. If someone ever edits one to be on-demand instead, the two
        # states collapse into one and this catches it.
        for engine in va.ENGINES:
            data = plistlib.loads(engine.template_path.read_bytes())
            self.assertTrue(data.get("RunAtLoad"))
            self.assertTrue(data.get("KeepAlive"))
            idle = int(data["EnvironmentVariables"][engine.idle_env])
            self.assertGreater(idle, 365 * 24 * 3600)


class StateReadingTests(unittest.TestCase):
    def _plist(self, **kw):
        base = {"Label": "x", "ProgramArguments": ["/bin/true"]}
        base.update(kw)
        return plistlib.dumps(base)

    def test_run_at_load_means_it_loads_at_boot(self):
        self.assertTrue(va.plist_starts_at_boot(self._plist(RunAtLoad=True)))

    def test_keep_alive_alone_also_means_it_loads_at_boot(self):
        # KeepAlive is enough on its own: launchd restarts the daemon within
        # seconds of it idling out, so the model comes back for nobody.
        self.assertTrue(va.plist_starts_at_boot(self._plist(KeepAlive=True)))

    def test_neither_flag_is_not_at_boot(self):
        self.assertFalse(va.plist_starts_at_boot(self._plist()))
        self.assertFalse(va.plist_starts_at_boot(
            self._plist(RunAtLoad=False, KeepAlive=False)))

    def test_unreadable_plist_is_unknown_not_false(self):
        self.assertIsNone(va.plist_starts_at_boot(b"not a plist at all"))


class CpuParseTests(unittest.TestCase):
    def test_formats(self):
        self.assertAlmostEqual(va.parse_cpu_seconds("0:02.50"), 2.5, places=2)
        self.assertAlmostEqual(va.parse_cpu_seconds("1:00:00"), 3600.0, places=2)

    def test_junk_is_none(self):
        for junk in ("", "abc", "1:2:3:4"):
            self.assertIsNone(va.parse_cpu_seconds(junk), junk)

    def test_unreadable_cpu_counts_as_busy(self):
        # An engine that cannot be measured must not be interrupted: the
        # fail-safe direction is to leave it running and let it idle out.
        real = va._cpu_seconds
        va._cpu_seconds = lambda pid: None
        self.addCleanup(lambda: setattr(va, "_cpu_seconds", real))
        self.assertTrue(va.is_busy(1, window=0.0))


class EngineTableTests(unittest.TestCase):
    def test_engines_point_at_the_daemons_the_clients_start(self):
        # hear.py and say.py start these exact files on demand. If a path here
        # drifts, the state report would say "not running" forever.
        for engine in va.ENGINES:
            self.assertEqual(engine.daemon_path.name, engine.daemon)
            self.assertIn(engine.subdir, str(engine.daemon_path))

    def test_ports_match_the_clients(self):
        self.assertEqual({e.key: e.port for e in va.ENGINES},
                         {"stt": 8779, "tts": 8778})


if __name__ == "__main__":
    unittest.main()
