"""Both screen recorders restore notification banners after a killed run (audit, 2026-09-27).

They mute GNOME's banners while recording and restore them on exit, SIGTERM or
SIGINT. A SIGKILL or the OOM killer skipped the restore and left the desktop's
notifications off for good. gsettings is faked here; the real setting is never
touched.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
# MOM's media recorder draws on a virtual display and mutes nothing
RECORDERS = [ROOT / "skills" / "presentations" / "scripts" / "record_presentation.py"]


def load(path):
    spec = importlib.util.spec_from_file_location(f"rec_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class KilledRecording(unittest.TestCase):
    def test_a_killed_run_is_repaired_by_the_next(self):
        for path in RECORDERS:
            with self.subTest(recorder=path.name), tempfile.TemporaryDirectory() as d:
                mod = load(path)
                setting = {"show-banners": "true"}
                calls = []

                def fake_gsettings(*args):
                    calls.append(args)
                    if args[0] == "get":
                        return setting[args[2]]
                    if args[0] == "set":
                        setting[args[2]] = args[3]
                        return ""
                    return None

                with mock.patch.object(mod, "_gsettings", fake_gsettings), \
                        mock.patch.object(mod, "BANNERS_MARKER", Path(d) / "banners-muted"):
                    mod._quiet_banners()
                    self.assertEqual(setting["show-banners"], "false")
                    # SIGKILL: no cleanup ran. A fresh process starts.
                    fresh = load(path)
                    with mock.patch.object(fresh, "_gsettings", fake_gsettings), \
                            mock.patch.object(fresh, "BANNERS_MARKER", Path(d) / "banners-muted"), \
                            mock.patch("builtins.print"):
                        fresh._restore_after_a_killed_run()
                    self.assertEqual(setting["show-banners"], "true", "a killed run left banners off")
                    self.assertFalse((Path(d) / "banners-muted").exists())

    def test_a_clean_run_leaves_no_marker(self):
        for path in RECORDERS:
            with self.subTest(recorder=path.name), tempfile.TemporaryDirectory() as d:
                mod = load(path)
                setting = {"show-banners": "true"}

                def fake_gsettings(*args):
                    if args[0] == "get":
                        return setting[args[2]]
                    setting[args[2]] = args[3]
                    return ""

                with mock.patch.object(mod, "_gsettings", fake_gsettings), \
                        mock.patch.object(mod, "BANNERS_MARKER", Path(d) / "banners-muted"):
                    mod._quiet_banners()
                    mod._restore_banners()
                self.assertEqual(setting["show-banners"], "true")
                self.assertFalse((Path(d) / "banners-muted").exists())


if __name__ == "__main__":
    unittest.main()
