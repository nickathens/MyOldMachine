"""torrent download.py: the VPN gate binds aria2c to the tunnel.

Linux bot review 2026-09-27, S046: the gate was checked once before aria2c
started; if ProtonVPN dropped mid-download, aria2c carried on over the home
line although the skill is sold as VPN-gated.
"""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "torrent" / "scripts" / "download.py"
spec = importlib.util.spec_from_file_location("torrent_download_under_test", str(SCRIPT))
dl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dl)

NMCLI_UP = "Wired connection 1:802-3-ethernet:enp3s0\nProtonVPN NL#12:wireguard:proton0\n"
MAGNET = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"


class VpnBind(unittest.TestCase):
    def _run(self, nmcli_out, argv):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "nmcli":
                return subprocess.CompletedProcess(cmd, 0, nmcli_out, "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        tmp = tempfile.mkdtemp(prefix="torrent-")
        with mock.patch.object(dl.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(dl.shutil, "which", return_value="/usr/bin/aria2c"), \
                mock.patch.object(sys, "argv", ["download.py", "--magnet", MAGNET, "--dir", tmp] + argv), \
                mock.patch("sys.stderr"), mock.patch("builtins.print"):
            try:
                dl.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, [c for c in calls if c[0] == "aria2c"]

    def test_aria2c_is_bound_to_the_vpn_device(self):
        code, aria = self._run(NMCLI_UP, [])
        self.assertEqual(code, 0)
        self.assertIn("--interface=proton0", aria[0])

    def test_no_vpn_refuses_before_aria2c(self):
        code, aria = self._run("Wired connection 1:802-3-ethernet:enp3s0\n", [])
        self.assertEqual(code, 3)
        self.assertEqual(aria, [])

    def test_macos_checks_the_tunnel_and_binds_nothing(self):
        # The ProtonVPN app on macOS has no CLI that names its tunnel, so the
        # gate keeps MOM's scutil check there and leaves aria2c unbound.
        def fake_run(cmd, **kwargs):
            if cmd[0] == "scutil":
                return subprocess.CompletedProcess(cmd, 0, "Network information\n  VPN server : 1.2.3.4\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        calls = []
        tmp = tempfile.mkdtemp(prefix="torrent-")
        with mock.patch.object(dl.platform, "system", return_value="Darwin"), \
                mock.patch.object(dl.subprocess, "run", side_effect=lambda c, **k: (calls.append(c), fake_run(c))[1]), \
                mock.patch.object(dl.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(sys, "argv", ["download.py", "--magnet", MAGNET, "--dir", tmp]), \
                mock.patch("sys.stderr"), mock.patch("builtins.print"):
            dl.main()
        aria = [c for c in calls if c[0] == "aria2c"]
        self.assertEqual(len(aria), 1)
        self.assertFalse(any(a.startswith("--interface") for a in aria[0]))
        self.assertFalse(any(c[0] == "nmcli" for c in calls))

    def test_no_vpn_flag_does_not_bind(self):
        code, aria = self._run("", ["--no-vpn"])
        self.assertEqual(code, 0)
        self.assertFalse(any(a.startswith("--interface") for a in aria[0]))


if __name__ == "__main__":
    unittest.main()
