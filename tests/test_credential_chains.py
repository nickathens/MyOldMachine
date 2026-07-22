"""Tests for the Claude credential chains: which store wins, and self-repair.

The machine can hold a Claude login in two places, and the difference between
them is not symmetric:

  the shared file    read by every per-user turn, and by default-config turns
                     too whenever no keychain item exists. One file, many
                     readers, so a token refresh updates everybody at once.
  the keychain item  SHADOWS the file for default-config turns (the nightly
                     reflection, health checks, users with no workspace).

Three defects lived in the gap between those two, all found on 2026-07-21:

  1. `security find-generic-password -s SERVICE -w` returns ONE item and never
     says which. With two accounts under the service macOS returned the HOLLOW
     one, so _export_keychain_credentials read empty tokens, correctly refused
     them, and gave up -- while the real login sat under the other account.
     Self-repair was dead, which is why the 2026-07-20 outage needed a human.

  2. The nightly login check read only the file, so a dead keychain chain
     reported healthy while every nightly job was failing.

  3. A repair that WROTE the keychain corrupted it: `security
     add-generic-password` reading its secret from stdin truncates at 128
     bytes and still exits 0. Hence the rule these tests lock down -- this
     module removes keychain items and never writes them.
"""
import ast
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core import claude_workspace as cw
from utils import claude_login_check as lc


def _ms(days_from_now: float) -> float:
    return (datetime.now() + timedelta(days=days_from_now)).timestamp() * 1000


def _cred(access="a", refresh="r", refresh_days=28.0, access_days=0.2) -> str:
    # access_days is the freshness clock: every refresh stamps a new
    # expiresAt, so the larger one is the copy that refreshed last -- and
    # therefore the copy whose rotation revoked the other.
    return json.dumps({"claudeAiOauth": {
        "accessToken": access,
        "refreshToken": refresh,
        "expiresAt": _ms(access_days),
        "refreshTokenExpiresAt": _ms(refresh_days),
    }})


HOLLOW = json.dumps({"claudeAiOauth": {
    "accessToken": "", "refreshToken": "",
    "refreshTokenExpiresAt": _ms(28.0),
}})

# What the corrupting stdin write actually left in the keychain: valid-looking
# for 128 bytes and then cut off mid-token.
TRUNCATED = _cred(access="sk-ant-oat01-" + "v" * 200)[:129]


class _FakeSecurity:
    """Stands in for the `security` binary at the subprocess boundary.

    Mocking the WRAPPERS (_read_keychain_item and friends) leaves the write
    path invisible: a re-added `add-generic-password` calls subprocess
    directly, sails straight past those mocks and reaches the machine's own
    keychain. Measured 2026-07-21 on this very class -- an inserted write ran
    for real out of five separate tests, which is precisely the accident that
    corrupted the live login on 2026-07-20.

    Faking the binary itself puts every keychain touch through one recorder,
    including one nobody has written yet.
    """

    def __init__(self, items: dict, service: str):
        self.items = dict(items)  # account -> raw secret
        self.service = service
        self.calls = []

    def _flags(self, cmd) -> dict:
        return {cmd[i]: cmd[i + 1] for i in range(len(cmd) - 1) if cmd[i].startswith("-")}

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append(list(cmd))
        verb = cmd[1] if len(cmd) > 1 else ""
        flags = self._flags(cmd)

        if verb == "dump-keychain":
            blocks = [f'"acct"<blob>="{acct}"\n"svce"<blob>="{self.service}"'
                      for acct in self.items]
            return subprocess.CompletedProcess(cmd, 0, "\nkeychain: ".join(blocks), "")

        if verb == "find-generic-password":
            if flags.get("-s") != self.service:
                return subprocess.CompletedProcess(cmd, 44, "", "not found")
            acct = flags.get("-a")
            secret = self.items.get(acct) if acct else next(iter(self.items.values()), None)
            if secret is None:
                return subprocess.CompletedProcess(cmd, 44, "", "not found")
            return subprocess.CompletedProcess(cmd, 0, secret + "\n", "")

        if verb == "delete-generic-password":
            if flags.get("-s") != self.service:
                return subprocess.CompletedProcess(cmd, 44, "", "not found")
            acct = flags.get("-a")
            if acct is None:
                self.items.pop(next(iter(self.items), None), None)
            elif self.items.pop(acct, None) is None:
                return subprocess.CompletedProcess(cmd, 44, "", "not found")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        # add-generic-password lands here: recorded, never executed. The
        # assertion that it must not appear is the caller's, so the failure
        # names the rule instead of an opaque subprocess error.
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def verbs(self) -> list:
        return [c[1] for c in self.calls if len(c) > 1]


def _no_real_keychain(cmd, *args, **kwargs):
    """Installed over subprocess.run for every heal test; see _FakeSecurity."""
    raise AssertionError(
        "a test reached the real `security` binary: " + " ".join(map(str, cmd))
        + "\nKeychain access must be faked at the subprocess boundary "
          "(_FakeSecurity), never left to hit the machine's own keychain."
    )


class KeychainAccountSelectionTests(unittest.TestCase):
    """A read must choose an account, not accept whichever one macOS offers."""

    def _read(self, items):
        """items: {account: secret}. Returns _read_keychain_item's choice."""
        with patch.object(cw.sys, "platform", "darwin"), \
             patch.object(cw, "_keychain_accounts", return_value=list(items)), \
             patch.object(cw, "_read_keychain_secret",
                          side_effect=lambda svc, acct: items.get(acct)):
            return cw._read_keychain_item(cw._KEYCHAIN_SERVICE)

    def test_usable_account_beats_hollow_one(self):
        """The exact 2026-07-21 state: a hollow 'unknown' next to the real login."""
        good = _cred(refresh="real")
        chosen = self._read({"unknown": HOLLOW, "coocooai": good})

        self.assertEqual(chosen, good)

    def test_hollow_is_still_returned_when_it_is_all_there_is(self):
        # Callers must be able to tell "nothing here" (leave it) from
        # "something broken here" (clear it). Filtering to usable-only would
        # collapse those and leave a hollow item shadowing the file forever.
        self.assertEqual(self._read({"unknown": HOLLOW}), HOLLOW)

    def test_longest_lived_usable_credential_wins(self):
        near, far = _cred(refresh="near", refresh_days=2), _cred(refresh="far", refresh_days=27)

        self.assertEqual(self._read({"a": near, "b": far}), far)

    def test_falls_back_to_an_accountless_read_when_enumeration_finds_nothing(self):
        # dump-keychain can fail while a direct read works; an enumeration
        # problem must never look like "the machine is not logged in".
        good = _cred()
        with patch.object(cw.sys, "platform", "darwin"), \
             patch.object(cw, "_keychain_accounts", return_value=[]), \
             patch.object(cw, "_read_keychain_secret", return_value=good) as read:
            self.assertEqual(cw._read_keychain_item("svc"), good)
        read.assert_called_once_with("svc", None)

    def test_returns_none_when_there_is_genuinely_nothing(self):
        self.assertIsNone(self._read({}))


class HollowDetectionTests(unittest.TestCase):
    """Deleting a secret needs a stricter test than declining to use one."""

    def test_empty_oauth_is_hollow(self):
        self.assertTrue(cw._credential_is_hollow(HOLLOW))

    def test_unparseable_is_not_hollow(self):
        # Unreadable is not the same as empty, and the cost of confusing them
        # is destroying the last copy of a login.
        for raw in (TRUNCATED, "not json at all", "", b"\xff\xfe"):
            with self.subTest(raw=raw[:20]):
                self.assertFalse(cw._credential_is_hollow(raw))

    def test_populated_credential_is_not_hollow(self):
        self.assertFalse(cw._credential_is_hollow(_cred()))

    def test_unknown_shape_is_not_hollow(self):
        self.assertFalse(cw._credential_is_hollow(json.dumps({"apiKey": "sk-x"})))


class KeychainDeleteTests(unittest.TestCase):
    def test_account_is_passed_through_so_the_right_item_dies(self):
        # An account-less delete removes whichever item macOS picks first,
        # which on the machine that had two was not the one we meant.
        with patch.object(cw.sys, "platform", "darwin"), \
             patch.object(cw.subprocess, "run") as run:
            run.return_value.returncode = 0
            cw._delete_keychain_item("svc", "coocooai")

        self.assertEqual(run.call_args[0][0],
                         ["security", "delete-generic-password",
                          "-s", "svc", "-a", "coocooai"])


class HealCredentialChainsTests(unittest.TestCase):
    """One working login, reached without a human, whichever store died."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.shared = self.root / ".credentials.json"
        self._patches = [
            patch.object(cw.sys, "platform", "darwin"),
            patch.object(cw, "default_legacy_root", return_value=self.root),
            # The mocks in _heal stand in front of the real keychain today,
            # but they are wrapper mocks: anything calling subprocess itself
            # goes round them. Measured before this guard existed, one test
            # in this class issued four live `security` calls against the
            # machine's own keychain. Fail loudly instead.
            patch.object(cw.subprocess, "run", side_effect=_no_real_keychain),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _heal(self, keychain, accounts=("coocooai",)):
        with patch.object(cw, "_read_keychain_item", return_value=keychain), \
             patch.object(cw, "_keychain_accounts", return_value=list(accounts)), \
             patch.object(cw, "_read_keychain_secret", return_value=keychain), \
             patch.object(cw, "_purge_hollow_keychain_items", return_value=0), \
             patch.object(cw, "_delete_keychain_item", return_value=True) as delete:
            result = cw.heal_credential_chains(self.root)
        return result, delete

    def test_dead_file_is_restored_from_a_live_keychain(self):
        """The repair that could not fire during the 2026-07-20 outage."""
        self.shared.write_text(HOLLOW)
        good = _cred(refresh="from-keychain")

        result, _ = self._heal(good)

        self.assertIsNotNone(result)
        self.assertEqual(self.shared.read_text(), good)

    def test_missing_file_is_restored_from_a_live_keychain(self):
        good = _cred(refresh="from-keychain")

        result, _ = self._heal(good)

        self.assertIsNotNone(result)
        self.assertEqual(self.shared.read_text(), good)

    def test_dead_keychain_item_over_a_healthy_file_is_deleted(self):
        # Deleting is safe *because* the file is alive: default-config turns
        # fall straight back to it (proven live 2026-07-21).
        self.shared.write_text(_cred(refresh="healthy-file"))

        result, delete = self._heal(TRUNCATED)

        self.assertIsNotNone(result)
        delete.assert_called_once_with(cw._KEYCHAIN_SERVICE, "coocooai")
        self.assertIn("healthy-file", self.shared.read_text())

    def test_expired_keychain_item_over_a_healthy_file_is_deleted(self):
        self.shared.write_text(_cred(refresh="healthy-file"))

        _, delete = self._heal(_cred(refresh="stale", refresh_days=-1))

        delete.assert_called_once()

    def test_identical_live_chains_are_left_completely_alone(self):
        # Same login in both stores is the resting state, not a divergence:
        # neither copy can revoke the other, so nothing needs healing.
        before = _cred(refresh="same-login")
        self.shared.write_text(before)

        result, delete = self._heal(before)

        self.assertIsNone(result)
        self.assertEqual(self.shared.read_text(), before)
        delete.assert_not_called()

    def test_a_newer_keychain_login_is_published_to_the_file(self):
        """2026-07-22: a system turn refreshed, every user turn then died.

        The auth probe runs on the default config dir, whose store is the
        plain keychain item. Its refresh rotated the login into the keychain
        and revoked the token still sitting in the shared file. Both stores
        stayed well formed, so the old "both alive, nothing to do" rule fired
        and the machine waited for a human -- while the credential that
        actually worked was sitting right there in the keychain.
        """
        stranded = _cred(refresh="revoked-by-the-probe", access_days=0.05)
        self.shared.write_text(stranded)
        fresh = _cred(refresh="minted-by-the-probe", access_days=0.4)

        result, delete = self._heal(fresh)

        self.assertIsNotNone(result)
        self.assertEqual(self.shared.read_text(), fresh)
        # And removed, so the next system turn reads the same one store.
        delete.assert_called_once_with(cw._KEYCHAIN_SERVICE, "coocooai")

    def test_a_newer_file_login_removes_the_stale_keychain_item(self):
        """The same bug running the other way (2026-07-20, 2026-07-21).

        A user turn refreshed and folded the result into the shared file, so
        the plain item became the revoked side -- and it shadows every
        default-config turn, which is why the nightly jobs and health probes
        were the ones dying with 401 while user turns worked.
        """
        fresh = _cred(refresh="minted-by-a-user-turn", access_days=0.4)
        self.shared.write_text(fresh)

        result, delete = self._heal(_cred(refresh="revoked", access_days=0.05))

        self.assertIsNotNone(result)
        self.assertEqual(self.shared.read_text(), fresh)  # file untouched
        delete.assert_called_once_with(cw._KEYCHAIN_SERVICE, "coocooai")

    def test_the_item_survives_a_write_that_did_not_land(self):
        # Deleting the item after a failed publish would destroy the only
        # copy of the login that still works.
        stranded = _cred(refresh="stranded", access_days=0.05)
        self.shared.write_text(stranded)

        with patch.object(cw, "_write_shared_credential", return_value=False):
            result, delete = self._heal(_cred(refresh="fresh", access_days=0.4))

        self.assertIsNone(result)
        self.assertEqual(self.shared.read_text(), stranded)
        delete.assert_not_called()

    def test_the_item_survives_a_file_that_reads_back_unusable(self):
        # The write reported success but the file is not something the CLI
        # could authenticate from. Keep the keychain copy: a divergence is
        # survivable, no credential at all is not.
        self.shared.write_text(_cred(refresh="older", access_days=0.05))

        def _write_something_useless(shared, payload):
            shared.write_text(HOLLOW)
            return True

        with patch.object(cw, "_write_shared_credential",
                          side_effect=_write_something_useless):
            result, delete = self._heal(_cred(refresh="fresh", access_days=0.4))

        self.assertIsNone(result)
        delete.assert_not_called()

    def test_two_dead_chains_change_nothing_and_wait_for_a_human(self):
        self.shared.write_text(HOLLOW)

        result, delete = self._heal(HOLLOW)

        self.assertIsNone(result)
        self.assertEqual(self.shared.read_text(), HOLLOW)
        delete.assert_not_called()

    def test_healthy_file_with_no_keychain_item_is_already_correct(self):
        before = _cred()
        self.shared.write_text(before)

        result, delete = self._heal(None, accounts=())

        self.assertIsNone(result)
        self.assertEqual(self.shared.read_text(), before)
        delete.assert_not_called()

    def _fake_security(self, items):
        fake = _FakeSecurity(items, cw._KEYCHAIN_SERVICE)
        return fake, patch.object(cw.subprocess, "run", side_effect=fake)

    def test_never_writes_the_keychain_while_restoring_the_file(self):
        """The rule that exists because writing it corrupted the real login.

        `security add-generic-password` fed over stdin truncates at 128 bytes
        and still exits 0, so a 509-byte credential landed as 129 bytes of
        unparseable JSON on top of the working one.

        This drives the branch a re-added write would live in -- the one that
        moves a login BETWEEN stores -- and it asserts the branch was reached
        before asserting what did not happen there. An earlier version of this
        test mocked the wrappers instead, never got past the early return, and
        passed with two live `add-generic-password` calls inserted into the
        function it was guarding.
        """
        self.shared.write_text(HOLLOW)  # file dead, so the keychain is copied to it
        good = _cred(refresh="from-keychain")
        fake, guard = self._fake_security({"coocooai": good})

        with guard:
            result = cw.heal_credential_chains(self.root)

        # Reached: only the restore branch produces this outcome.
        self.assertEqual(result, "restored the shared credential file from the keychain login")
        self.assertEqual(self.shared.read_text(), good)
        self.assertIn("find-generic-password", fake.verbs())
        # ...and the rule itself.
        self.assertNotIn("add-generic-password", fake.verbs())

    def test_never_writes_the_keychain_while_clearing_a_dead_item(self):
        """The other half: the branch that removes, where a "restore the good
        one back" write is the obvious thing for a future hand to add."""
        self.shared.write_text(_cred(refresh="healthy-file"))
        fake, guard = self._fake_security({"coocooai": TRUNCATED})

        with guard:
            result = cw.heal_credential_chains(self.root)

        # Reached: only the delete loop produces this outcome.
        self.assertEqual(
            result,
            "removed a dead keychain login so every job falls back to the shared file",
        )
        self.assertIn("delete-generic-password", fake.verbs())
        self.assertNotIn("add-generic-password", fake.verbs())
        self.assertIn("healthy-file", self.shared.read_text())

    def test_no_keychain_write_exists_anywhere_in_the_module(self):
        """Belt to the two dynamic tests' braces.

        Those cover the branches a write lives in TODAY. This one needs no
        branch to be reached and no scenario to be imagined: it reads the
        module's own syntax tree and fails if the string appears in live code
        at all. Docstrings are exempt -- heal_credential_chains explains the
        accident in prose, and that explanation is the point.
        """
        tree = ast.parse(Path(cw.__file__).read_text())
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        offenders = [
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings
            and "add-generic-password" in node.value
        ]
        self.assertEqual(offenders, [], (
            f"{Path(cw.__file__).name} writes the keychain at line(s) {offenders}. "
            "Two stores holding the same rotating refresh token is a race, not "
            "redundancy, and the stdin form of this command silently truncates "
            "a credential at 128 bytes while exiting 0."
        ))

    def test_a_foreign_root_is_refused_so_real_tokens_never_leak_into_it(self):
        """Caught for real: a test deleted its temp credential and found a
        live machine token waiting there afterwards. The keychain item is the
        partner of the DEFAULT config dir and of nothing else."""
        other = Path(tempfile.mkdtemp())

        result, delete = self._heal(_cred(refresh="machine-token"))
        self.assertIsNotNone(result)  # the default root does heal

        with patch.object(cw, "_read_keychain_item", return_value=_cred()):
            self.assertIsNone(cw.heal_credential_chains(other))
        self.assertFalse((other / ".credentials.json").exists())

    def test_off_darwin_is_a_noop(self):
        self.shared.write_text(HOLLOW)
        with patch.object(cw.sys, "platform", "linux"), \
             patch.object(cw, "_read_keychain_item") as read:
            self.assertIsNone(cw.heal_credential_chains(self.root))
        read.assert_not_called()

    def test_never_raises_into_the_turn_it_runs_beside(self):
        self.shared.write_text(_cred())
        # _purge_hollow_keychain_items runs before the read and is what used
        # to carry this test into the machine's real keychain.
        with patch.object(cw, "_purge_hollow_keychain_items", return_value=0), \
             patch.object(cw, "_read_keychain_item", side_effect=OSError("keychain gone")):
            with self.assertRaises(OSError):
                cw.heal_credential_chains(self.root)
        # ...but the caller swallows it, which is what actually protects the
        # turn. The keychain reconcile past it is not this test's subject and
        # was the second route into the machine's real keychain.
        with patch.object(cw, "heal_credential_chains", side_effect=OSError("boom")), \
             patch.object(cw, "_reconcile_keychain_credential", return_value=False), \
             patch.object(cw, "user_claude_dir", return_value=self.root / "u"):
            cw.reconcile_shared_credentials(1, legacy_root=self.root)


class MachineLoginStateTests(unittest.TestCase):
    """The nightly check reports the WORSE chain, not the convenient one."""

    def _state(self, file_state, keychain_state):
        with patch.object(lc, "read_login_state", return_value=file_state), \
             patch.object(lc, "read_keychain_login_state", return_value=keychain_state):
            return lc.read_machine_login_state()

    def test_a_dead_keychain_chain_is_not_hidden_by_a_healthy_file(self):
        """The blind spot: file-only reporting stayed green through an outage."""
        combined = self._state(
            {"status": "ok", "days_left": 28.0, "detail": "file fine",
             "expires_at": None, "access_expires_at": None},
            {"status": "expired", "days_left": -1.0, "detail": "keychain dead",
             "expires_at": None, "access_expires_at": None})

        self.assertEqual(combined["status"], "expired")
        self.assertIsNotNone(lc.warning_message(combined))

    def test_a_dead_file_chain_is_not_hidden_by_a_healthy_keychain(self):
        combined = self._state(
            {"status": "expired", "days_left": -1.0, "detail": "file dead",
             "expires_at": None, "access_expires_at": None},
            {"status": "ok", "days_left": 28.0, "detail": "keychain fine",
             "expires_at": None, "access_expires_at": None})

        self.assertEqual(combined["status"], "expired")

    def test_one_dead_chain_says_the_machine_will_repair_itself(self):
        # Without this the alert reads like an outage and sends someone to the
        # Terminal for a machine that is about to fix itself.
        combined = self._state(
            {"status": "ok", "days_left": 28.0, "detail": "file fine",
             "expires_at": None, "access_expires_at": None},
            {"status": "expired", "days_left": -1.0, "detail": "keychain dead",
             "expires_at": None, "access_expires_at": None})

        self.assertIn("repair", combined["detail"])

    def test_both_healthy_stays_quiet(self):
        healthy = {"status": "ok", "days_left": 28.0, "detail": "fine",
                   "expires_at": None, "access_expires_at": None}

        combined = self._state(healthy, healthy)

        self.assertEqual(combined["status"], "ok")
        self.assertIsNone(lc.warning_message(combined))
        self.assertNotIn("repair", combined["detail"])

    def test_both_chains_are_reported_for_the_json_consumer(self):
        healthy = {"status": "ok", "days_left": 28.0, "detail": "fine",
                   "expires_at": None, "access_expires_at": None}

        combined = self._state(healthy, healthy)

        self.assertEqual(set(combined["chains"]), {"file", "keychain"})


class KeychainLoginStateTests(unittest.TestCase):
    """An absent keychain item is health; a present broken one is an outage."""

    def _state(self, raw):
        with patch.object(lc.sys, "platform", "darwin"), \
             patch.object(cw, "_read_keychain_item", return_value=raw):
            return lc.read_keychain_login_state()

    def test_absent_item_is_not_a_chain_at_all(self):
        # Absent means default-config turns read the shared file, which is
        # the healthy single-store configuration -- not a missing login.
        # Reporting "missing" here would alert on every Linux install.
        self.assertIsNone(self._state(None))

    def test_hollow_item_is_reported_because_it_shadows_the_file(self):
        state = self._state(HOLLOW)

        self.assertEqual(state["status"], "missing")
        self.assertIsNotNone(lc.warning_message(state))

    def test_corrupt_item_is_reported_rather_than_silently_skipped(self):
        state = self._state(TRUNCATED)

        self.assertEqual(state["status"], "unreadable")
        self.assertIsNotNone(lc.warning_message(state))

    def test_healthy_item_is_quiet(self):
        state = self._state(_cred())

        self.assertEqual(state["status"], "ok")
        self.assertIsNone(lc.warning_message(state))

    def test_expiring_item_warns_on_the_refresh_clock(self):
        state = self._state(_cred(refresh_days=2))

        self.assertEqual(state["status"], "expiring")

    def test_off_darwin_there_is_no_second_chain(self):
        with patch.object(lc.sys, "platform", "linux"):
            self.assertIsNone(lc.read_keychain_login_state())


class CliChainSelectionTests(unittest.TestCase):
    """Which chains a given invocation actually inspects.

    The usage comment claimed `--path FILE` skipped the keychain; it does
    not, `--file-only` does. Prose drifts, so the contract is pinned here.
    """

    def _dispatch(self, argv):
        healthy = {"status": "ok", "detail": "fine", "days_left": 28.0,
                   "expires_at": None, "access_expires_at": None, "chains": {}}
        with patch.object(sys, "argv", ["claude_login_check.py"] + argv), \
             patch.object(lc, "read_login_state", return_value=dict(healthy)) as file_only, \
             patch.object(lc, "read_machine_login_state", return_value=dict(healthy)) as both:
            lc.main()
        return file_only, both

    def test_file_only_skips_the_keychain(self):
        file_only, both = self._dispatch(["--file-only"])

        self.assertEqual(file_only.call_count, 1)
        both.assert_not_called()

    def test_path_alone_still_reads_the_keychain(self):
        """The exact claim the old usage comment got wrong."""
        file_only, both = self._dispatch(["--path", "/tmp/whatever.json"])

        both.assert_called_once()
        file_only.assert_not_called()
        self.assertEqual(both.call_args.kwargs["path"], Path("/tmp/whatever.json"))

    def test_the_default_invocation_reads_both_chains(self):
        file_only, both = self._dispatch([])

        both.assert_called_once()
        file_only.assert_not_called()

    def test_file_only_and_path_combine(self):
        """The form the corrected usage line now advertises."""
        file_only, both = self._dispatch(["--file-only", "--path", "/tmp/x.json"])

        both.assert_not_called()
        self.assertEqual(file_only.call_args.kwargs["path"], Path("/tmp/x.json"))


if __name__ == "__main__":
    unittest.main()
