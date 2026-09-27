"""drain_outbox stays inside the users folder it is given (Linux bot review 2026-09-27).

After delivering, drain_outbox cleared the pending-message marker through
get_user_dir(), which resolves the global data/users root and creates the
user's folder there, whatever folder the drain was given. On the Linux bot a
test drain created a real users/42 that way. MOM's own outbox tests move both
roots, so they never showed it; production passes no folder, so nothing
changes there.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["MOM_TEST"] = "1"    # before bot: its import otherwise logs into the real bot.log

import bot  # noqa: E402
import core.users as users_mod  # noqa: E402
from utils import outbox  # noqa: E402


class _FakeBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, chat_id=None, text=None, **kw):
        self.calls.append((chat_id, text))


class DrainStaysInItsFolder(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # self.users_dir stands in for the global data/users root; the drain
        # is given a different folder and must never touch this one.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.users_dir = Path(self._tmp.name)
        self._given = tempfile.TemporaryDirectory()
        self.addCleanup(self._given.cleanup)
        self.given = Path(self._given.name)

    async def test_a_given_folder_is_the_only_one_touched(self):
        (self.given / "4242").mkdir()
        outbox.record(self.given / "4242", 4242, ["hello"])
        marker = self.given / "4242" / "pending_message.json"
        marker.write_text("{}", encoding="utf-8")
        with mock.patch.object(bot, "USERS_DIR", self.users_dir), \
                mock.patch.object(users_mod, "USERS_DATA_DIR", self.users_dir), \
                mock.patch.object(bot, "get_allowed_users", return_value=[4242]):
            done = await bot.drain_outbox(_FakeBot(), users_dir=self.given)
        self.assertEqual(done, 1)
        self.assertFalse((self.users_dir / "4242").exists(), "the drain created a folder under data/users")
        self.assertFalse(marker.exists(), "the marker in the given folder was not cleared")


if __name__ == "__main__":
    unittest.main()
