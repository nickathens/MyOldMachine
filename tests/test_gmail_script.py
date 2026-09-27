"""gmail.py: header lookups that ignore case.

Linux bot review 2026-09-27, S041: Gmail returns header names as written;
MIMEText writes 'to' and 'subject' in lower case, so drafts made by this very
script listed with a blank To and '(no subject)'. (The Linux bot also removed
its send command, S040; that is a rule of that install, not a MOM defect.)
"""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('google', 'google_auth_oauthlib', 'googleapiclient') if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "email" / "scripts" / "gmail.py"


def _load():
    # The module resolves its token path at import and creates
    # TG_USER_DIR/google, so point it at a scratch dir, never a real user.
    scratch = tempfile.mkdtemp(prefix="gmail-test-")
    with mock.patch.dict(os.environ, {"JARVIS_USER_DIR": scratch}):
        spec = importlib.util.spec_from_file_location("gmail_under_test", str(SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


gmail = _load()


class _Exec:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _FakeService:
    """Answers the handful of calls the listers make."""

    def __init__(self, headers):
        self._headers = headers
        self.sent = []

    def users(self):
        return self

    def messages(self):
        return self

    def drafts(self):
        return self

    def list(self, **kwargs):
        return _Exec({"messages": [{"id": "m1"}], "drafts": [{"id": "d1"}]})

    def get(self, **kwargs):
        payload = {"headers": [{"name": n, "value": v} for n, v in self._headers]}
        if kwargs.get("id") == "d1":
            return _Exec({"message": {"payload": payload, "snippet": "hi"}})
        return _Exec({"payload": payload, "snippet": "hi"})

    def send(self, **kwargs):  # pragma: no cover - must never be reached
        self.sent.append(kwargs)
        return _Exec({"id": "x"})


LOWER = [("to", "a@b.com"), ("subject", "Offer"), ("from", "Me <m@b.com>"), ("date", "Sun")]


class HeaderCase(unittest.TestCase):
    def _with(self, headers):
        return mock.patch.object(gmail, "get_gmail_service", return_value=_FakeService(headers))

    def test_drafts_read_lower_case_headers(self):
        with self._with(LOWER):
            items = gmail.list_drafts(1)
        self.assertEqual(items[0]["to"], "a@b.com")
        self.assertEqual(items[0]["subject"], "Offer")

    def test_inbox_search_sent_read_lower_case_headers(self):
        with self._with(LOWER):
            inbox = gmail.get_inbox(1)[0]
            found = gmail.search_emails("x", 1)[0]
            sent = gmail.get_sent(1)[0]
            full = gmail.read_email("m1")
        self.assertEqual((inbox["from"], inbox["subject"]), ("Me <m@b.com>", "Offer"))
        self.assertEqual((found["from"], found["subject"]), ("Me <m@b.com>", "Offer"))
        self.assertEqual((sent["to"], sent["subject"]), ("a@b.com", "Offer"))
        self.assertEqual((full["to"], full["subject"], full["date"]), ("a@b.com", "Offer", "Sun"))

    def test_capitalised_headers_still_read(self):
        with self._with([("To", "c@d.com"), ("Subject", "Hi")]):
            items = gmail.list_drafts(1)
        self.assertEqual((items[0]["to"], items[0]["subject"]), ("c@d.com", "Hi"))


if __name__ == "__main__":
    unittest.main()
