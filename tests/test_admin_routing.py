"""Admin-facing notifications must go to an admin, not the first-by-ID user.

users.json is written with sort_keys=True, so get_allowed_users()[0] is
whoever has the smallest Telegram ID — pure luck that it is the admin today.
get_primary_admin_id() is the correct selector; these tests pin it and the
call sites that used to index the allowed list.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402


class PrimaryAdminTests(unittest.TestCase):
    def test_admin_wins_over_smaller_user_id(self):
        # A non-admin with a smaller Telegram ID sorts first in the allowed
        # list; the admin must still be selected.
        with patch("core.users.get_admin_telegram_ids", return_value=[8044898180]), \
             patch.object(config, "get_allowed_users", return_value=[1111, 8044898180]):
            self.assertEqual(config.get_primary_admin_id(), 8044898180)

    def test_no_admin_falls_back_to_first_allowed(self):
        # Bootstrap window: users.json empty, ALLOWED_USERS env only.
        with patch("core.users.get_admin_telegram_ids", return_value=[]), \
             patch.object(config, "get_allowed_users", return_value=[1111, 2222]):
            self.assertEqual(config.get_primary_admin_id(), 1111)

    def test_nothing_configured_returns_none(self):
        with patch("core.users.get_admin_telegram_ids", return_value=[]), \
             patch.object(config, "get_allowed_users", return_value=[]):
            self.assertIsNone(config.get_primary_admin_id())


class CallSiteTests(unittest.TestCase):
    def test_nightly_report_sends_to_primary_admin(self):
        from utils import nightly_report
        with patch.object(nightly_report, "get_primary_admin_id", return_value=4242), \
             patch.object(nightly_report, "subprocess") as sub:
            sub.run.return_value.returncode = 0
            rc = nightly_report.send("hello")
        self.assertEqual(rc, 0)
        argv = sub.run.call_args.args[0]
        self.assertIn("4242", argv)

    def test_nightly_report_no_admin_is_a_clean_error(self):
        from utils import nightly_report
        with patch.object(nightly_report, "get_primary_admin_id", return_value=None), \
             patch.object(nightly_report, "subprocess") as sub:
            rc = nightly_report.send("hello")
        self.assertEqual(rc, 1)
        sub.run.assert_not_called()

    def test_machine_check_explicit_flag_wins(self):
        from utils import machine_check
        self.assertEqual(machine_check._resolve_admin(123), 123)

    def test_machine_check_falls_back_to_primary_admin(self):
        from utils import machine_check
        with patch("core.config.get_primary_admin_id", return_value=777):
            self.assertEqual(machine_check._resolve_admin(None), 777)


if __name__ == "__main__":
    unittest.main()
