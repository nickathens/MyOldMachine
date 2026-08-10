"""Tests for resume-time detection of optional features in install.wizard.

When an existing install is upgraded to a version that ships a new optional
feature (e.g. local Telegram Bot API server), the wizard's resume path used
to short-circuit out before ever asking about it. The user had to wipe
.env or hand-edit it to discover the new feature exists.

`_offer_missing_optional_features` walks an in-process registry on every
resume. Each entry knows:
  - whether it applies to the current host (`applies_to`, optional)
  - how to check whether the feature is already configured
  - how to ask the user if they want to set it up now (defaulting to no)

If anything is newly enabled, the caller rewrites .env so the gated install
steps later in main() pick up the change.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep test backup runs out of the production backup.log

from install import wizard  # noqa: E402


class OfferMissingOptionalFeaturesTests(unittest.TestCase):
    def setUp(self):
        # Replace the live registry with a controllable double per-test, so
        # adding real features later doesn't break these cases.
        self._original_registry = wizard.OPTIONAL_FEATURES

    def tearDown(self):
        wizard.OPTIONAL_FEATURES = self._original_registry

    def _set_registry(self, features):
        wizard.OPTIONAL_FEATURES = features

    def test_returns_false_when_registry_empty(self):
        self._set_registry([])
        config = {"telegram_token": "x"}
        with patch.object(wizard, "ask") as ask_mock:
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertFalse(result)
        ask_mock.assert_not_called()

    def test_returns_false_when_all_features_configured(self):
        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": lambda c: c.update({"feat_a_on": True}),
            }
        ])
        config = {"feat_a_on": True}
        with patch.object(wizard, "ask") as ask_mock:
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertFalse(result)
        ask_mock.assert_not_called()

    def test_skips_feature_when_user_declines(self):
        configure_mock = unittest_mock_callable()
        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": configure_mock,
            }
        ])
        config = {}
        with patch.object(wizard, "ask", return_value="n"):
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertFalse(result)
        self.assertFalse(configure_mock.called)
        self.assertNotIn("feat_a_on", config)

    def test_default_decline_on_empty_input(self):
        # ask() returns the default ("n") on empty input; we get the literal
        # default back. Verify we treat that as decline.
        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": lambda c: c.update({"feat_a_on": True}),
            }
        ])
        config = {}
        # Simulate user pressing Enter — `ask` returns its default.
        with patch.object(wizard, "ask", return_value="n"):
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertFalse(result)
        self.assertNotIn("feat_a_on", config)

    def test_runs_configure_when_user_accepts(self):
        def configure(c):
            c["feat_a_on"] = True

        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": configure,
            }
        ])
        config = {}
        with patch.object(wizard, "ask", return_value="y"):
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertTrue(result)
        self.assertTrue(config.get("feat_a_on"))

    def test_returns_false_when_user_accepts_outer_but_inner_declines(self):
        # User says "y" to "Set up now?" but then bails out inside the
        # feature's own prompts. The configure callback explicitly leaves
        # feat_a_on as False to model this.
        def configure_that_aborts(c):
            c["feat_a_on"] = False  # user declined inside

        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": configure_that_aborts,
            }
        ])
        config = {}
        with patch.object(wizard, "ask", return_value="y"):
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertFalse(result)
        self.assertFalse(config.get("feat_a_on"))

    def test_walks_multiple_features_independently(self):
        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": lambda c: c.update({"feat_a_on": True}),
            },
            {
                "key": "feat_b",
                "label": "Feature B",
                "summary": "does B",
                "is_configured": lambda c: c.get("feat_b_on", False),
                "configure": lambda c: c.update({"feat_b_on": True}),
            },
        ])
        config = {}
        # User says yes to A, no to B
        with patch.object(wizard, "ask", side_effect=["y", "n"]):
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertTrue(result)  # at least one enabled
        self.assertTrue(config.get("feat_a_on"))
        self.assertNotIn("feat_b_on", config)

    def test_already_configured_features_are_not_prompted(self):
        # Pre-existing feat_a (already configured); only feat_b should prompt.
        ask_calls = []

        def fake_ask(prompt, default=None):
            ask_calls.append(prompt)
            return "n"

        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": lambda c: c.update({"feat_a_on": True}),
            },
            {
                "key": "feat_b",
                "label": "Feature B",
                "summary": "does B",
                "is_configured": lambda c: c.get("feat_b_on", False),
                "configure": lambda c: c.update({"feat_b_on": True}),
            },
        ])
        config = {"feat_a_on": True}
        with patch.object(wizard, "ask", side_effect=fake_ask):
            wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertEqual(len(ask_calls), 1)  # only feat_b

    def test_accepts_yes_in_any_case(self):
        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": lambda c: c.update({"feat_a_on": True}),
            }
        ])
        for accept_word in ("y", "Y", "yes", "YES", "Yes"):
            with self.subTest(answer=accept_word):
                config = {}
                with patch.object(wizard, "ask", return_value=accept_word):
                    result = wizard._offer_missing_optional_features(Path("/tmp"), config)
                self.assertTrue(result)
                self.assertTrue(config.get("feat_a_on"))


class TelegramBotApiRegistryEntryTests(unittest.TestCase):
    """The shipped registry must contain the telegram-bot-api entry."""

    def test_registry_contains_telegram_bot_api(self):
        keys = {f["key"] for f in wizard.OPTIONAL_FEATURES}
        self.assertIn("telegram_bot_api", keys)

    def test_telegram_bot_api_is_configured_reads_env_flag(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES
                    if f["key"] == "telegram_bot_api")
        self.assertFalse(feat["is_configured"]({}))
        self.assertFalse(feat["is_configured"]({"telegram_local_api_enabled": False}))
        self.assertTrue(feat["is_configured"]({"telegram_local_api_enabled": True}))

    def test_telegram_bot_api_configure_invokes_step(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES
                    if f["key"] == "telegram_bot_api")
        config = {}
        with patch.object(wizard, "_run_telegram_bot_api_step") as step_mock:
            feat["configure"](config)
        step_mock.assert_called_once_with(config)

    def test_required_keys_present_on_every_entry(self):
        for feat in wizard.OPTIONAL_FEATURES:
            with self.subTest(feature=feat.get("key")):
                for key in ("key", "label", "summary", "is_configured", "configure"):
                    self.assertIn(key, feat)
                self.assertTrue(callable(feat["is_configured"]))
                self.assertTrue(callable(feat["configure"]))

    def test_keys_are_unique(self):
        keys = [f["key"] for f in wizard.OPTIONAL_FEATURES]
        self.assertEqual(len(keys), len(set(keys)),
                         "Optional feature keys must be unique")


class AppliesToFilterTests(unittest.TestCase):
    """`applies_to` is the per-host filter that hides features that don't
    belong on the current platform (e.g. macOS softwareupdate on Linux).
    """

    def setUp(self):
        self._original_registry = wizard.OPTIONAL_FEATURES

    def tearDown(self):
        wizard.OPTIONAL_FEATURES = self._original_registry

    def _set_registry(self, features):
        wizard.OPTIONAL_FEATURES = features

    def test_applies_to_false_filters_feature_out(self):
        # When applies_to returns False, the feature is invisible — its
        # is_configured/configure callbacks must NEVER run.
        is_configured = unittest_mock_callable()
        configure = unittest_mock_callable()
        self._set_registry([
            {
                "key": "linux_only",
                "label": "Linux-only feature",
                "summary": "should not appear on darwin",
                "applies_to": lambda: False,
                "is_configured": is_configured,
                "configure": configure,
            }
        ])
        config = {}
        with patch.object(wizard, "ask") as ask_mock:
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertFalse(result)
        ask_mock.assert_not_called()
        self.assertFalse(is_configured.called)
        self.assertFalse(configure.called)

    def test_applies_to_true_lets_feature_through(self):
        self._set_registry([
            {
                "key": "always",
                "label": "Always-on feature",
                "summary": "applies everywhere",
                "applies_to": lambda: True,
                "is_configured": lambda c: c.get("on", False),
                "configure": lambda c: c.update({"on": True}),
            }
        ])
        config = {}
        with patch.object(wizard, "ask", return_value="y"):
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertTrue(result)
        self.assertTrue(config.get("on"))

    def test_missing_applies_to_defaults_to_always_apply(self):
        # Backward-compat: entries that omit applies_to behave as if it
        # returned True. This keeps the old telegram_bot_api entry and any
        # future external feature definitions safe.
        self._set_registry([
            {
                "key": "no_filter",
                "label": "No filter feature",
                "summary": "applies_to is absent",
                "is_configured": lambda c: c.get("on", False),
                "configure": lambda c: c.update({"on": True}),
            }
        ])
        config = {}
        with patch.object(wizard, "ask", return_value="y"):
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertTrue(result)
        self.assertTrue(config.get("on"))

    def test_mixed_applies_to_only_runs_applicable_features(self):
        ask_calls = []

        def fake_ask(prompt, default=None):
            ask_calls.append(prompt)
            return "n"

        self._set_registry([
            {
                "key": "linux_only",
                "label": "Linux only",
                "summary": "...",
                "applies_to": lambda: False,
                "is_configured": lambda c: False,
                "configure": lambda c: None,
            },
            {
                "key": "everywhere",
                "label": "Everywhere",
                "summary": "...",
                "applies_to": lambda: True,
                "is_configured": lambda c: False,
                "configure": lambda c: None,
            },
        ])
        config = {}
        with patch.object(wizard, "ask", side_effect=fake_ask):
            wizard._offer_missing_optional_features(Path("/tmp"), config)
        # Only "everywhere" should have prompted; linux_only is filtered out.
        self.assertEqual(len(ask_calls), 1)


class BackupRegistryEntryTests(unittest.TestCase):
    """The shipped registry must contain a backup entry that reads/writes
    maintenance.json (not .env). State location is by design — /maintenance
    can also toggle backup at runtime, so .env must NOT diverge from
    maintenance.json.
    """

    def test_registry_contains_backup(self):
        keys = {f["key"] for f in wizard.OPTIONAL_FEATURES}
        self.assertIn("backup", keys)

    def test_backup_applies_everywhere(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES if f["key"] == "backup")
        self.assertTrue(feat.get("applies_to", lambda: True)())

    def test_backup_is_configured_reads_maintenance_json(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES if f["key"] == "backup")
        # Both backup_enabled and backup_path must be present
        with patch.object(wizard, "_load_maintenance_for_check",
                          return_value={"backup_enabled": False, "backup_path": ""}):
            self.assertFalse(feat["is_configured"]({}))
        with patch.object(wizard, "_load_maintenance_for_check",
                          return_value={"backup_enabled": True, "backup_path": ""}):
            self.assertFalse(feat["is_configured"]({}))
        with patch.object(wizard, "_load_maintenance_for_check",
                          return_value={"backup_enabled": False, "backup_path": "/x"}):
            self.assertFalse(feat["is_configured"]({}))
        with patch.object(wizard, "_load_maintenance_for_check",
                          return_value={"backup_enabled": True, "backup_path": "/x"}):
            self.assertTrue(feat["is_configured"]({}))

    def test_backup_configure_writes_maintenance_json(self):
        # Drive the configure step with a temp dir as the backup target.
        # Ensures it calls update_config with the right keys.
        # User picks "tarball" so this exercises the simpler tarball branch.
        feat = next(f for f in wizard.OPTIONAL_FEATURES if f["key"] == "backup")
        with tempfile.TemporaryDirectory() as tmpdir:
            captured = {}

            def fake_update(**kwargs):
                captured.update(kwargs)
                return kwargs

            answers = iter([tmpdir, "5"])

            def fake_ask(prompt, default=None, **_):
                try:
                    return next(answers)
                except StopIteration:
                    return default or ""

            with patch.object(wizard, "ask", side_effect=fake_ask), \
                 patch.object(wizard, "ask_choice", return_value="tarball"), \
                 patch("utils.maintenance.update_config", side_effect=fake_update):
                config = {}
                feat["configure"](config)

            self.assertTrue(captured.get("backup_enabled"))
            self.assertEqual(captured.get("backup_tool"), "tarball")
            self.assertEqual(Path(captured["backup_path"]).resolve(),
                             Path(tmpdir).resolve())
            self.assertEqual(captured["backup_retention"], 5)
            self.assertTrue(config.get("backup_enabled"))

    def test_backup_configure_falls_back_to_default_retention(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES if f["key"] == "backup")
        with tempfile.TemporaryDirectory() as tmpdir:
            captured = {}

            def fake_update(**kwargs):
                captured.update(kwargs)
                return kwargs

            # Non-numeric retention should fall back to the canonical default
            answers = iter([tmpdir, "garbage"])

            def fake_ask(prompt, default=None, **_):
                try:
                    return next(answers)
                except StopIteration:
                    return default or ""

            with patch.object(wizard, "ask", side_effect=fake_ask), \
                 patch.object(wizard, "ask_choice", return_value="tarball"), \
                 patch("utils.maintenance.update_config", side_effect=fake_update):
                config = {}
                feat["configure"](config)

            from utils.backup import DEFAULT_RETENTION
            self.assertEqual(captured["backup_retention"], DEFAULT_RETENTION)

    def test_backup_configure_borg_branch_initializes_repo(self):
        """When the user picks borg, the wizard should install borg, generate
        a passphrase, init the repo, and save the borg-specific keys."""
        feat = next(f for f in wizard.OPTIONAL_FEATURES if f["key"] == "backup")
        with tempfile.TemporaryDirectory() as tmpdir:
            captured = {}

            def fake_update(**kwargs):
                captured.update(kwargs)
                return kwargs

            # ask: just the path; ask_choice: pick borg; secret-passphrase ask:
            # default empty (auto-generate).
            answers = iter([tmpdir])

            def fake_ask(prompt, default=None, required=True, secret=False):
                # secret prompt for passphrase comes after path; return empty
                # to trigger auto-generation.
                if secret:
                    return ""
                try:
                    return next(answers)
                except StopIteration:
                    return default or ""

            with patch.object(wizard, "ask", side_effect=fake_ask), \
                 patch.object(wizard, "ask_choice", return_value="borg"), \
                 patch("install.borg_setup.have_borg", return_value=True), \
                 patch("utils.backup_borg.init_repo",
                       return_value=(True, "init ok")), \
                 patch("utils.backup_borg.is_repo", return_value=False), \
                 patch("utils.maintenance.update_config", side_effect=fake_update):
                config = {}
                feat["configure"](config)

            self.assertTrue(captured.get("backup_enabled"))
            self.assertEqual(captured.get("backup_tool"), "borg")
            self.assertEqual(captured.get("backup_keep_daily"), 7)
            self.assertEqual(captured.get("backup_keep_weekly"), 4)
            self.assertEqual(captured.get("backup_keep_monthly"), 6)
            self.assertEqual(captured.get("backup_compression"), "zstd,3")
            self.assertIn("backup_passphrase_path", captured)


class MacosSystemUpdatesRegistryEntryTests(unittest.TestCase):
    """macOS softwareupdate must only prompt on Darwin. On Linux the entry
    is filtered out by `applies_to` and never executes.
    """

    def test_registry_contains_macos_system_updates(self):
        keys = {f["key"] for f in wizard.OPTIONAL_FEATURES}
        self.assertIn("macos_system_updates", keys)

    def test_applies_to_only_darwin(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES
                    if f["key"] == "macos_system_updates")
        with patch("platform.system", return_value="Darwin"):
            self.assertTrue(feat["applies_to"]())
        with patch("platform.system", return_value="Linux"):
            self.assertFalse(feat["applies_to"]())
        with patch("platform.system", return_value="Windows"):
            self.assertFalse(feat["applies_to"]())

    def test_macos_is_configured_reads_maintenance_json(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES
                    if f["key"] == "macos_system_updates")
        with patch.object(wizard, "_load_maintenance_for_check",
                          return_value={"macos_system_updates": False}):
            self.assertFalse(feat["is_configured"]({}))
        with patch.object(wizard, "_load_maintenance_for_check",
                          return_value={"macos_system_updates": True}):
            self.assertTrue(feat["is_configured"]({}))
        # Missing key = not configured (defaults False)
        with patch.object(wizard, "_load_maintenance_for_check", return_value={}):
            self.assertFalse(feat["is_configured"]({}))

    def test_macos_configure_decline_does_not_write(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES
                    if f["key"] == "macos_system_updates")
        captured = {}

        def fake_update(**kwargs):
            captured.update(kwargs)
            return kwargs

        with patch.object(wizard, "ask", return_value="n"), \
             patch("utils.maintenance.update_config", side_effect=fake_update):
            config = {}
            feat["configure"](config)
        self.assertEqual(captured, {})
        self.assertNotIn("macos_system_updates", config)

    def test_macos_configure_accept_writes_with_optional_restart(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES
                    if f["key"] == "macos_system_updates")
        captured = {}

        def fake_update(**kwargs):
            captured.update(kwargs)
            return kwargs

        # First "y" enables, second "y" allows auto-restart
        answers = iter(["y", "y"])

        def fake_ask(prompt, default=None, **_):
            try:
                return next(answers)
            except StopIteration:
                return default or ""

        with patch.object(wizard, "ask", side_effect=fake_ask), \
             patch("utils.maintenance.update_config", side_effect=fake_update):
            config = {}
            feat["configure"](config)
        self.assertTrue(captured.get("macos_system_updates"))
        self.assertTrue(captured.get("macos_system_updates_restart"))
        self.assertTrue(config.get("macos_system_updates"))

    def test_macos_configure_accept_no_restart(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES
                    if f["key"] == "macos_system_updates")
        captured = {}

        def fake_update(**kwargs):
            captured.update(kwargs)
            return kwargs

        # First "y" enables, second "n" declines auto-restart
        answers = iter(["y", "n"])

        def fake_ask(prompt, default=None, **_):
            try:
                return next(answers)
            except StopIteration:
                return default or ""

        with patch.object(wizard, "ask", side_effect=fake_ask), \
             patch("utils.maintenance.update_config", side_effect=fake_update):
            config = {}
            feat["configure"](config)
        self.assertTrue(captured.get("macos_system_updates"))
        self.assertFalse(captured.get("macos_system_updates_restart"))


class McpServersRegistryEntryTests(unittest.TestCase):
    """MCP support is detected by file existence, not by an .env flag.
    The user is expected to edit mcp_servers.json after we scaffold it.
    """

    def test_registry_contains_mcp_servers(self):
        keys = {f["key"] for f in wizard.OPTIONAL_FEATURES}
        self.assertIn("mcp_servers", keys)

    def test_mcp_is_configured_checks_file_exists(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES if f["key"] == "mcp_servers")
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_repo = Path(tmpdir)
            with patch.object(wizard, "REPO_DIR", fake_repo):
                # File does not exist
                self.assertFalse(feat["is_configured"]({}))
                # File exists (any content)
                (fake_repo / "mcp_servers.json").write_text(
                    '{"servers": []}', encoding="utf-8"
                )
                self.assertTrue(feat["is_configured"]({}))

    def test_mcp_configure_decline_does_not_create_file(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES if f["key"] == "mcp_servers")
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_repo = Path(tmpdir)
            (fake_repo / "mcp_servers.json.example").write_text(
                '{"servers": []}', encoding="utf-8"
            )
            with patch.object(wizard, "REPO_DIR", fake_repo), \
                 patch.object(wizard, "ask", return_value="n"):
                config = {}
                feat["configure"](config)
            self.assertFalse((fake_repo / "mcp_servers.json").exists())

    def test_mcp_configure_accept_copies_template_when_pip_missing(self):
        # When the venv pip binary doesn't exist, we still scaffold the
        # config file so the user has something to edit. SDK install just
        # gets skipped with a warning.
        feat = next(f for f in wizard.OPTIONAL_FEATURES if f["key"] == "mcp_servers")
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_repo = Path(tmpdir)
            example_content = '{"servers": [{"name": "filesystem"}]}'
            (fake_repo / "mcp_servers.json.example").write_text(
                example_content, encoding="utf-8"
            )
            # No .venv → pip missing → SDK install skipped
            with patch.object(wizard, "REPO_DIR", fake_repo), \
                 patch.object(wizard, "ask", return_value="y"):
                config = {}
                feat["configure"](config)
            target = fake_repo / "mcp_servers.json"
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), example_content)

    def test_mcp_configure_accept_runs_pip_when_venv_present(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES if f["key"] == "mcp_servers")
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_repo = Path(tmpdir)
            (fake_repo / "mcp_servers.json.example").write_text(
                '{"servers": []}', encoding="utf-8"
            )
            # Make a fake .venv/bin/pip so the install path is taken
            venv_bin = fake_repo / ".venv" / "bin"
            venv_bin.mkdir(parents=True)
            pip = venv_bin / "pip"
            pip.write_text("#!/bin/sh\nexit 0\n")
            pip.chmod(0o755)

            captured = {}

            def fake_run(cmd, *args, **kwargs):
                captured["cmd"] = cmd

                class R:
                    returncode = 0
                    stderr = ""
                return R()

            with patch.object(wizard, "REPO_DIR", fake_repo), \
                 patch.object(wizard, "ask", return_value="y"), \
                 patch.object(wizard.subprocess, "run", side_effect=fake_run):
                config = {}
                feat["configure"](config)

            self.assertEqual(captured["cmd"][0], str(pip))
            self.assertIn("install", captured["cmd"])
            self.assertIn("mcp[cli]", captured["cmd"])

    def test_mcp_configure_does_not_overwrite_existing_file(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES if f["key"] == "mcp_servers")
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_repo = Path(tmpdir)
            existing = '{"servers": [{"name": "user_custom"}]}'
            (fake_repo / "mcp_servers.json").write_text(existing, encoding="utf-8")
            (fake_repo / "mcp_servers.json.example").write_text(
                '{"servers": []}', encoding="utf-8"
            )
            with patch.object(wizard, "REPO_DIR", fake_repo), \
                 patch.object(wizard, "ask", return_value="y"):
                config = {}
                feat["configure"](config)
            content = (fake_repo / "mcp_servers.json").read_text(encoding="utf-8")
            self.assertEqual(content, existing)


def unittest_mock_callable():
    """Tiny callable double that records whether it was invoked.

    Avoids importing MagicMock just for one boolean — keeps test deps minimal
    and the call-site obvious.
    """

    class _Probe:
        def __init__(self):
            self.called = False
            self.calls = []

        def __call__(self, *args, **kwargs):
            self.called = True
            self.calls.append((args, kwargs))

    return _Probe()


class StdoutSilencer:
    """Context manager: swallows stdout to keep test output clean."""

    def __enter__(self):
        self._real = sys.stdout
        sys.stdout = io.StringIO()
        return self

    def __exit__(self, *exc):
        sys.stdout = self._real


# Wrap every test method's stdout. The wizard prints headers and labels via
# `print` on the real stdout; that noise drowns the actual test report.
def _wrap_with_stdout_silencer(cls):
    for name in list(vars(cls)):
        if name.startswith("test_"):
            method = getattr(cls, name)

            def make_wrapped(m):
                def wrapped(self, *a, **kw):
                    with StdoutSilencer():
                        return m(self, *a, **kw)
                return wrapped

            setattr(cls, name, make_wrapped(method))
    return cls


OfferMissingOptionalFeaturesTests = _wrap_with_stdout_silencer(
    OfferMissingOptionalFeaturesTests
)
TelegramBotApiRegistryEntryTests = _wrap_with_stdout_silencer(
    TelegramBotApiRegistryEntryTests
)
AppliesToFilterTests = _wrap_with_stdout_silencer(AppliesToFilterTests)
BackupRegistryEntryTests = _wrap_with_stdout_silencer(BackupRegistryEntryTests)
MacosSystemUpdatesRegistryEntryTests = _wrap_with_stdout_silencer(
    MacosSystemUpdatesRegistryEntryTests
)
McpServersRegistryEntryTests = _wrap_with_stdout_silencer(
    McpServersRegistryEntryTests
)


if __name__ == "__main__":
    unittest.main()
