"""Unit tests for install.macos_resolver.

The resolver is the dynamic version-picker that replaced the hardcoded
_MACOS_DIRECT_DOWNLOADS table. Tests mock all network access; nothing
here hits the network so the suite is offline-safe.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Make the project root importable when tests run from the repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install import macos_resolver as r  # noqa: E402


# A trimmed snapshot of the TDF /libreoffice/stable/ index. Real index has
# many more directories; this is enough to exercise version selection.
LIBREOFFICE_LISTING_HTML = """<html><body>
<a href="../">../</a>
<a href="24.2.0/">24.2.0/</a>
<a href="24.8.7/">24.8.7/</a>
<a href="25.2.5/">25.2.5/</a>
<a href="25.8.5/">25.8.5/</a>
<a href="25.8.6/">25.8.6/</a>
<a href="26.2.2/">26.2.2/</a>
<a href="26.2.3/">26.2.3/</a>
</body></html>"""

BLENDER_36_LISTING_HTML = """<html><body>
<a href="blender-3.6.0-macos-x64.dmg">blender-3.6.0-macos-x64.dmg</a>
<a href="blender-3.6.0-macos-arm64.dmg">blender-3.6.0-macos-arm64.dmg</a>
<a href="blender-3.6.23-macos-x64.dmg">blender-3.6.23-macos-x64.dmg</a>
<a href="blender-3.6.23-macos-arm64.dmg">blender-3.6.23-macos-arm64.dmg</a>
<a href="blender-3.6.5-macos-x64.dmg">blender-3.6.5-macos-x64.dmg</a>
</body></html>"""


class ParseVersionTests(unittest.TestCase):
    def test_three_part(self):
        self.assertEqual(r._parse_version("25.8.6"), (25, 8, 6))

    def test_two_part(self):
        self.assertEqual(r._parse_version("11.0"), (11, 0))

    def test_alphanumeric_segment_becomes_zero(self):
        # e.g. "25.8.6rc1" — non-numeric tail in a segment is stripped to int prefix
        self.assertEqual(r._parse_version("25.8.6rc1"), (25, 8, 6))

    def test_garbage(self):
        self.assertEqual(r._parse_version("abc"), (0,))

    def test_macos_ge(self):
        self.assertTrue(r._macos_ge((11, 0), (10, 15)))
        self.assertTrue(r._macos_ge((10, 15), (10, 15)))
        self.assertFalse(r._macos_ge((10, 14), (10, 15)))
        self.assertFalse(r._macos_ge((10, 15), (11, 0)))


class LibreOfficeResolverTests(unittest.TestCase):
    def test_catalina_picks_25_8_branch(self):
        with patch.object(r, "_fetch", return_value=LIBREOFFICE_LISTING_HTML):
            res = r._resolve_libreoffice((10, 15), "x86_64")
        self.assertIsNotNone(res)
        # Catalina cannot run 26.x (requires 11.0+), must pick 25.8.x newest
        self.assertEqual(res.version, "25.8.6")
        self.assertEqual(res.type, "dmg")
        self.assertEqual(res.app_name, "LibreOffice.app")
        self.assertIn("25.8.6/mac/x86_64/", res.url)
        self.assertIn("MacOS_x86-64.dmg", res.url)

    def test_big_sur_picks_26_2_branch(self):
        with patch.object(r, "_fetch", return_value=LIBREOFFICE_LISTING_HTML):
            res = r._resolve_libreoffice((11, 0), "x86_64")
        self.assertIsNotNone(res)
        self.assertEqual(res.version, "26.2.3")

    def test_arm64_url_uses_aarch64_dir(self):
        with patch.object(r, "_fetch", return_value=LIBREOFFICE_LISTING_HTML):
            res = r._resolve_libreoffice((11, 0), "arm64")
        self.assertIsNotNone(res)
        self.assertIn("/mac/aarch64/", res.url)
        self.assertIn("MacOS_aarch64.dmg", res.url)

    def test_high_sierra_returns_none(self):
        # 10.13 cannot run any branch in our compat table
        with patch.object(r, "_fetch", return_value=LIBREOFFICE_LISTING_HTML):
            res = r._resolve_libreoffice((10, 13), "x86_64")
        self.assertIsNone(res)

    def test_offline_falls_back_to_known_good(self):
        # listing fetch fails -> baked-in fallback table is used
        with patch.object(r, "_fetch", return_value=None):
            res = r._resolve_libreoffice((10, 15), "x86_64")
        self.assertIsNotNone(res)
        self.assertEqual(res.version, r.LIBREOFFICE_FALLBACK["10.15"])
        self.assertIn("offline fallback", res.compat_note)

    def test_offline_below_floor_returns_none(self):
        # No fallback entry has a macOS floor 10.13 can meet
        with patch.object(r, "_fetch", return_value=None):
            res = r._resolve_libreoffice((10, 13), "x86_64")
        self.assertIsNone(res)


class BlenderResolverTests(unittest.TestCase):
    def test_catalina_uses_3_6_branch(self):
        # Catalina cannot run 4.x (needs 11.0+); must pick 3.6.x branch
        def fake_fetch(url, timeout=15):
            if "Blender3.6/" in url:
                return BLENDER_36_LISTING_HTML
            return None
        with patch.object(r, "_fetch", side_effect=fake_fetch):
            res = r._resolve_blender((10, 15), "x86_64")
        self.assertIsNotNone(res)
        self.assertEqual(res.version, "3.6.23")
        self.assertEqual(res.type, "dmg")
        self.assertIn("Blender3.6/blender-3.6.23-macos-x64.dmg", res.url)

    def test_arm64_filename_uses_arm64(self):
        def fake_fetch(url, timeout=15):
            if "Blender3.6/" in url:
                return BLENDER_36_LISTING_HTML
            return None
        with patch.object(r, "_fetch", side_effect=fake_fetch):
            res = r._resolve_blender((10, 15), "arm64")
        self.assertIsNotNone(res)
        self.assertIn("blender-3.6.23-macos-arm64.dmg", res.url)

    def test_too_old_returns_none(self):
        # 10.12 is below the lowest LTS branch floor in our table
        with patch.object(r, "_fetch", return_value=None):
            res = r._resolve_blender((10, 12), "x86_64")
        self.assertIsNone(res)

    def test_offline_falls_back(self):
        # No listing fetched -> baked-in 3.6 fallback used on Catalina
        with patch.object(r, "_fetch", return_value=None):
            res = r._resolve_blender((10, 15), "x86_64")
        self.assertIsNotNone(res)
        self.assertEqual(res.version, r.BLENDER_FALLBACK["3.6"][0])
        self.assertIn("offline fallback", res.compat_note)


class InkscapeResolverTests(unittest.TestCase):
    def test_returns_first_alive_url(self):
        # Pretend only 1.4.4 returns 200; everything earlier is 404.
        def fake_head(url, timeout=15):
            return "1.4.4_" in url
        with patch.object(r, "_head_ok", side_effect=fake_head):
            res = r._resolve_inkscape((10, 15), "x86_64")
        self.assertIsNotNone(res)
        self.assertEqual(res.version, "1.4.4")
        self.assertIn("Inkscape-1.4.4_x86_64.dmg", res.url)

    def test_below_floor_returns_none(self):
        with patch.object(r, "_head_ok", return_value=True):
            res = r._resolve_inkscape((10, 12), "x86_64")
        self.assertIsNone(res)

    def test_offline_falls_back_to_known_version(self):
        with patch.object(r, "_head_ok", return_value=False):
            res = r._resolve_inkscape((10, 15), "x86_64")
        self.assertIsNotNone(res)
        self.assertEqual(res.version, r.INKSCAPE_FALLBACK_VERSION)
        self.assertIn("offline fallback", res.compat_note)


class RcloneResolverTests(unittest.TestCase):
    def test_returns_current_url(self):
        res = r._resolve_rclone((10, 15), "x86_64")
        self.assertIsNotNone(res)
        self.assertEqual(res.type, "zip")
        self.assertEqual(res.binary, "rclone")
        self.assertIn("rclone-current-osx-amd64.zip", res.url)

    def test_arm64_uses_arm64_zip(self):
        res = r._resolve_rclone((11, 0), "arm64")
        self.assertIsNotNone(res)
        self.assertIn("rclone-current-osx-arm64.zip", res.url)

    def test_pre_high_sierra_returns_none(self):
        res = r._resolve_rclone((10, 12), "x86_64")
        self.assertIsNone(res)


class UnsupportedPackagesTests(unittest.TestCase):
    def test_imagemagick_returns_none(self):
        self.assertIsNone(r._resolve_imagemagick((10, 15), "x86_64"))

    def test_chromium_returns_none(self):
        self.assertIsNone(r._resolve_chromium((10, 15), "x86_64"))

    def test_skip_notes_have_imagemagick_and_chromium(self):
        self.assertTrue(r.macos_skip_note("imagemagick"))
        self.assertTrue(r.macos_skip_note("chromium"))
        self.assertEqual(r.macos_skip_note("blender"), "")


class ResolveDispatchTests(unittest.TestCase):
    def test_unknown_name_returns_none(self):
        self.assertIsNone(r.resolve("nope-not-a-package", (11, 0), "x86_64"))

    def test_resolver_exception_does_not_crash(self):
        with patch.dict(r._RESOLVERS, {"libreoffice": lambda h, a: 1 / 0}):
            self.assertIsNone(r.resolve("libreoffice", (11, 0), "x86_64"))

    def test_known_name_routes_to_resolver(self):
        called = {}

        def fake(host, arch):
            called["host"] = host
            called["arch"] = arch
            return None
        with patch.dict(r._RESOLVERS, {"blender": fake}):
            r.resolve("blender", (10, 15), "x86_64")
        self.assertEqual(called["host"], (10, 15))
        self.assertEqual(called["arch"], "x86_64")


if __name__ == "__main__":
    unittest.main()
