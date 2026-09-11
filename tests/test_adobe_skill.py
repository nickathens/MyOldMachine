"""Tests for the Adobe skill's state probe.

Covers:
- signed_in() distinguishes installer scratch from a real sign-in
- logged_out_guid is honoured only when nothing positive is present
- a STALE logged_out_guid does not override a real sign-in
- either instrument (on-disk account markers, keychain item) is sufficient
- None is returned when nothing on disk is decisive

The stale-marker case is the one that matters, and it is here because the
probe got this exact question wrong twice in one day on 11 Sep 2026, once in
each direction.

The first version inferred "signed in" from the OOBE directory being
non-empty. The Creative Cloud installer drops filesync.db, temp_lbs_wid and
container prefs there before anyone has typed a password, so it answered yes
on a machine where nothing could be installed.

The fix over-corrected: it read logged_out_guid as authoritative. Adobe writes
that file before the first sign-in and does NOT delete it on sign-in, so when
the user actually signed in the probe still answered no, with the marker
sitting 26 minutes stale beside a freshly written entitlement cache.

Both failures are the same shape as the verifier-threshold lesson: a presence
test over a path that something else also writes is not a state test. The
markers this version reads are keyed by the ACCOUNT ID, which is what makes
them state: before sign-in the same prefs files carry the literal suffix
"default", and no products/<id>@AdobeID directory exists at all.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROBE = REPO / "skills/adobe/scripts/adobe_status.py"

# A real account id as Adobe writes it: a long uppercase hex string.
ACCOUNT = "AC6680FC6A7B6F390A495F8E"


def load_probe():
    spec = importlib.util.spec_from_file_location("adobe_status", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SignedInTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_probe()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.oobe = Path(tmp.name) / "OOBE"
        self.mod.OOBE = self.oobe
        # Isolate the on-disk markers from the live machine's keychain.
        self.mod._keychain_user_info = lambda: False

    def write_installer_scratch(self):
        """Exactly what the installer leaves before anyone has signed in."""
        self.oobe.mkdir(parents=True, exist_ok=True)
        (self.oobe / "filesync.db").write_text("x")
        (self.oobe / "temp_lbs_wid").write_text("{28628304-0B7C-4156-80ED-1316BBA5B2C9}")
        (self.oobe / "com.adobe.acc.container.default.prefs").write_text("<prefs/>")
        (self.oobe / "com.adobe.accc.apps.default.prefs").write_text("<prefs/>")

    def write_signed_in(self):
        """The account-keyed artifacts that appear only after a real sign-in."""
        cache = self.oobe / "com.adobe.accc.apps/products" / f"{ACCOUNT}@AdobeID"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "_data").write_text("<channels/>")
        (self.oobe / f"com.adobe.acc.onboarding.{ACCOUNT}.prefs").write_text("<prefs/>")

    def test_no_adobe_directory_at_all(self):
        self.assertIs(self.mod.signed_in(), False)

    def test_installer_scratch_does_not_read_as_signed_in(self):
        self.write_installer_scratch()
        self.assertIsNone(
            self.mod.signed_in(),
            "installer scratch must never be mistaken for an account",
        )

    def test_never_signed_in_is_reported_false(self):
        self.write_installer_scratch()
        (self.oobe / "logged_out_guid").write_text("{28628304-0B7C-4156-80ED-1316BBA5B2C9}")
        self.assertIs(self.mod.signed_in(), False)

    def test_stale_logged_out_marker_does_not_beat_a_real_sign_in(self):
        """Adobe leaves logged_out_guid in place after sign-in. It is not a state."""
        self.write_installer_scratch()
        (self.oobe / "logged_out_guid").write_text("{28628304-0B7C-4156-80ED-1316BBA5B2C9}")
        self.write_signed_in()
        self.assertIs(self.mod.signed_in(), True)

    def test_signed_in_without_the_stale_marker(self):
        self.write_installer_scratch()
        self.write_signed_in()
        self.assertIs(self.mod.signed_in(), True)

    def test_entitlement_cache_alone_is_sufficient(self):
        (self.oobe / "com.adobe.accc.apps/products" / f"{ACCOUNT}@AdobeID").mkdir(parents=True)
        self.assertIs(self.mod.signed_in(), True)

    def test_account_scoped_prefs_alone_are_sufficient(self):
        self.oobe.mkdir(parents=True)
        (self.oobe / f"com.adobe.acc.container.{ACCOUNT}.prefs").write_text("<prefs/>")
        self.assertIs(self.mod.signed_in(), True)

    def test_default_suffixed_prefs_are_not_account_scoped(self):
        """The discriminator is the account id, not the filename prefix."""
        self.oobe.mkdir(parents=True)
        (self.oobe / "com.adobe.acc.container.default.prefs").write_text("<prefs/>")
        (self.oobe / "com.adobe.acc.fonts.default.prefs").write_text("<prefs/>")
        self.assertIsNone(self.mod.signed_in())

    def test_keychain_alone_is_sufficient(self):
        """Second instrument: disk markers absent, keychain item present."""
        self.write_installer_scratch()
        (self.oobe / "logged_out_guid").write_text("{28628304-0B7C-4156-80ED-1316BBA5B2C9}")
        self.mod._keychain_user_info = lambda: True
        self.assertIs(self.mod.signed_in(), True)


if __name__ == "__main__":
    unittest.main()
