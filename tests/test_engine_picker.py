"""The per-user engine picker: the catalog, the probe, the store, the routing.

Four things are locked here, each because it fails quietly rather than loudly:

1. **An engine's effort must be one the model accepts.** The engine rows and
   the effort table are edited by different hands. A level a model does not
   take is not an error at either CLI: claude warns on stderr and runs at its
   default, codex ignores the config key. The picker would look right and the
   turn would run at the wrong level.

2. **A button is only offered when its CLI can run.** Codex answers every turn
   for a model an older build does not know with a message about ChatGPT
   accounts, which reads like an account problem. A stored choice is
   re-probed on the way in AND on the way out, so a CLI removed after the
   pick falls back to the install's own provider instead of failing turns.

3. **One person's pick is nobody else's.** The .env knobs are global and
   admin-only; this store is per user and must never touch them.

4. **The turn actually runs on the picked engine**, with that engine's effort,
   and /stop still reaches it. The provider that holds a user's subprocess is
   no longer the one global object.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

from core import engines, user_prefs, users  # noqa: E402
from core import model_efforts as me  # noqa: E402
from install import wizard  # noqa: E402


class EngineCatalogTests(unittest.TestCase):
    """The rows themselves, against the tables that already own these facts."""

    def test_every_engine_effort_is_one_its_model_accepts(self):
        for engine in engines.ENGINES:
            with self.subTest(engine=engine["id"]):
                allowed = me.efforts_for(engine["provider"], engine["model"])
                self.assertIn(engine["effort"], allowed)
                self.assertEqual(engines.engine_effort(engine), engine["effort"])

    def test_every_engine_model_is_in_the_install_catalog(self):
        # install/wizard.PROVIDER_MODELS is the catalog of models this repo
        # has actually run. An engine naming a model outside it is the same
        # drift that put three API-only ids in the Codex picker.
        for engine in engines.ENGINES:
            with self.subTest(engine=engine["id"]):
                family = "claude" if engine["provider"].startswith("claude") \
                    else engine["provider"]
                ids = {mid for mid, _ in wizard.PROVIDER_MODELS.get(family, [])}
                self.assertIn(engine["model"], ids)

    def test_the_claude_engine_asks_for_the_cli_not_the_api(self):
        # create_provider maps bare "claude" to the API provider the moment
        # an API key exists, and the engine is a subscription CLI choice.
        opus = engines.get_engine("opus")
        self.assertEqual(opus["provider"], "claude-cli")

    def test_astra_is_offered_at_extra_high_not_ultra(self):
        # ultra flips the model-visible prompt to "proactive multi-agent
        # delegation", which is the wrong default on the old machines this
        # project targets.
        self.assertEqual(engines.get_engine("astra")["effort"], "xhigh")

    def test_exactly_one_engine_is_the_default_and_it_is_opus(self):
        defaults = [e for e in engines.ENGINES if e["is_default"]]
        self.assertEqual(len(defaults), 1)
        self.assertEqual(defaults[0]["id"], "opus")
        self.assertEqual(engines.default_engine()["id"], "opus")

    def test_engines_have_distinct_ids_and_accents(self):
        ids = [e["id"] for e in engines.ENGINES]
        accents = [e["accent"] for e in engines.ENGINES]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(len(set(accents)), len(accents))

    def test_an_effort_the_table_does_not_know_sends_nothing(self):
        drifted = dict(engines.get_engine("opus"), effort="ultra")
        self.assertEqual(engines.engine_effort(drifted), "")

    def test_unknown_id_has_no_row(self):
        self.assertIsNone(engines.get_engine("gpt-imaginary"))
        self.assertIsNone(engines.get_engine(""))
        self.assertIsNone(engines.get_engine(None))


class AvailabilityProbeTests(unittest.TestCase):
    def setUp(self):
        login = patch("core.engines._login_status", return_value=(True, ""))
        login.start()
        self.addCleanup(login.stop)
        engines.probe_cache_clear()
        self.addCleanup(engines.probe_cache_clear)

    def test_a_missing_cli_is_unavailable_and_says_so(self):
        with patch("core.engines._cli_version_text", return_value=None):
            ok, reason = engines.engine_available(engines.get_engine("astra"))
        self.assertFalse(ok)
        self.assertIn("codex", reason)

    def test_a_codex_too_old_for_astra_is_refused_with_the_version(self):
        with patch("core.engines._cli_version_text", return_value="codex-cli 0.152.0"):
            ok, reason = engines.engine_available(engines.get_engine("astra"))
        self.assertFalse(ok)
        self.assertIn("0.153.1", reason)

    def test_a_new_enough_codex_is_available(self):
        with patch("core.engines._cli_version_text", return_value="codex-cli 0.154.0"):
            ok, reason = engines.engine_available(engines.get_engine("astra"))
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_claude_has_no_version_floor(self):
        with patch("core.engines._cli_version_text", return_value="1.0.0 (Claude Code)"):
            ok, _ = engines.engine_available(engines.get_engine("opus"))
        self.assertTrue(ok)

    def test_the_answer_is_cached_then_refreshable(self):
        with patch("core.engines._cli_version_text",
                   return_value="codex-cli 0.154.0") as probe:
            engines.engine_available(engines.get_engine("astra"))
            engines.engine_available(engines.get_engine("astra"))
            self.assertEqual(probe.call_count, 1)
            engines.engine_available(engines.get_engine("astra"), refresh=True)
            self.assertEqual(probe.call_count, 2)

    def test_a_cli_that_errors_is_not_treated_as_present(self):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "boom"
        with patch("core.engines.subprocess.run", return_value=Result()):
            self.assertIsNone(engines._cli_version_text("codex"))

    def test_available_engines_reports_every_row_with_its_reason(self):
        with patch("core.engines._cli_version_text", return_value=None):
            rows = engines.available_engines()
        self.assertEqual(len(rows), len(engines.ENGINES))
        for row in rows:
            self.assertFalse(row["available"])
            self.assertTrue(row["reason"])


class _UserDirTestCase(unittest.TestCase):
    """Every test below writes under a temp users tree, never the real one."""

    def setUp(self):
        login = patch("core.engines._login_status", return_value=(True, ""))
        login.start()
        self.addCleanup(login.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="mom-engine-"))
        self._saved = users.USERS_DATA_DIR
        users.USERS_DATA_DIR = self.tmp
        engines.probe_cache_clear()
        config = patch("core.config.get_llm_provider", return_value="openai")
        config.start()
        self.addCleanup(config.stop)

    def tearDown(self):
        users.USERS_DATA_DIR = self._saved
        engines.probe_cache_clear()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class PreferenceStoreTests(_UserDirTestCase):
    def test_absent_file_reads_as_no_preferences(self):
        self.assertEqual(user_prefs.load_prefs(7), {})
        self.assertIsNone(user_prefs.get_pref(7, "engine"))
        self.assertEqual(user_prefs.get_pref(7, "engine", "fallback"), "fallback")

    def test_set_then_read_round_trips(self):
        self.assertTrue(user_prefs.set_pref(7, "engine", "astra"))
        self.assertEqual(user_prefs.get_pref(7, "engine"), "astra")
        self.assertTrue((self.tmp / "7" / "prefs.json").is_file())

    def test_one_users_preference_is_invisible_to_another(self):
        user_prefs.set_pref(7, "engine", "astra")
        self.assertEqual(user_prefs.get_pref(8, "engine", ""), "")

    def test_clear_removes_only_that_key(self):
        user_prefs.set_pref(7, "engine", "astra")
        user_prefs.set_pref(7, "other", 1)
        user_prefs.clear_pref(7, "engine")
        self.assertEqual(user_prefs.load_prefs(7), {"other": 1})

    def test_clearing_a_key_that_was_never_set_is_not_an_error(self):
        self.assertTrue(user_prefs.clear_pref(7, "engine"))

    def test_a_corrupt_file_reads_as_empty_rather_than_raising(self):
        path = self.tmp / "7" / "prefs.json"
        path.parent.mkdir(parents=True)
        path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(user_prefs.load_prefs(7), {})

    def test_a_non_numeric_id_never_becomes_a_path(self):
        with self.assertRaises(ValueError):
            user_prefs.load_prefs("../../etc")


class SelectionTests(_UserDirTestCase):
    def setUp(self):
        login = patch("core.engines._login_status", return_value=(True, ""))
        login.start()
        self.addCleanup(login.stop)
        super().setUp()
        self._probe = patch("core.engines._cli_version_text",
                            return_value="codex-cli 0.154.0")
        self._probe.start()
        self.addCleanup(self._probe.stop)

    def test_api_install_keeps_machine_default(self):
        self.assertEqual(engines.user_engine_id(7), "")
        self.assertIsNone(engines.resolve_engine(7))

    def test_a_pick_is_stored_and_resolves(self):
        ok, message = engines.set_user_engine(7, "astra")
        self.assertTrue(ok, message)
        self.assertEqual(engines.user_engine_id(7), "astra")
        self.assertEqual(engines.resolve_engine(7)["model"], "gpt-6-astra")

    def test_clearing_returns_the_user_to_the_install_default(self):
        engines.set_user_engine(7, "astra")
        ok, _ = engines.set_user_engine(7, "")
        self.assertTrue(ok)
        self.assertEqual(engines.user_engine_id(7), "")
        self.assertIsNone(engines.resolve_engine(7))

    def test_an_unknown_engine_is_refused_and_stores_nothing(self):
        ok, message = engines.set_user_engine(7, "gpt-imaginary")
        self.assertFalse(ok)
        self.assertIn("No such engine", message)
        self.assertEqual(engines.user_engine_id(7), "")

    def test_an_unavailable_engine_is_refused_at_the_press(self):
        with patch("core.engines._cli_version_text", return_value=None):
            ok, message = engines.set_user_engine(7, "astra")
        self.assertFalse(ok)
        self.assertIn("not installed", message)
        self.assertEqual(engines.user_engine_id(7), "")

    def test_a_pick_whose_cli_later_vanishes_falls_back_instead_of_failing(self):
        engines.set_user_engine(7, "astra")
        engines.probe_cache_clear()
        with patch("core.engines._cli_version_text", return_value=None):
            self.assertIsNone(engines.resolve_engine(7))
        # The stored preference survives, so putting the CLI back restores it.
        self.assertEqual(engines.user_engine_id(7), "astra")

    def test_a_stored_id_that_is_no_longer_offered_is_ignored(self):
        user_prefs.set_pref(7, "engine", "retired-engine")
        self.assertEqual(engines.user_engine_id(7), "")
        self.assertIsNone(engines.resolve_engine(7))

    def test_an_admin_runs_the_machine_setting_even_with_a_pick_stored(self):
        # A pick made before the refusal existed must not outrank the model
        # and effort the same person sets in the settings panel.
        user_prefs.set_pref(7, "engine", "astra")
        with patch("core.config.is_admin", return_value=True):
            self.assertIsNone(engines.resolve_engine(7))
        # Nothing is deleted behind their back; it is simply not consulted.
        self.assertEqual(engines.user_engine_id(7), "astra")

    def test_an_admin_cannot_store_a_pick(self):
        with patch("core.config.is_admin", return_value=True):
            ok, message = engines.set_user_engine(7, "astra")
        self.assertFalse(ok)
        self.assertEqual(message, engines.ADMIN_KEEPS_MACHINE_SETTING)
        self.assertEqual(engines.user_engine_id(7), "")

    def test_an_admin_may_still_clear_a_pick_stored_earlier(self):
        user_prefs.set_pref(7, "engine", "astra")
        with patch("core.config.is_admin", return_value=True):
            ok, _ = engines.set_user_engine(7, "")
        self.assertTrue(ok)
        self.assertEqual(engines.user_engine_id(7), "")

    def test_an_ordinary_user_is_untouched_by_the_admin_rule(self):
        with patch("core.config.is_admin", return_value=False):
            ok, _ = engines.set_user_engine(7, "astra")
            self.assertTrue(ok)
            self.assertEqual(engines.resolve_engine(7)["model"], "gpt-6-astra")

    def test_picking_does_not_touch_the_global_env(self):
        before = {k: os.environ.get(k) for k in
                  ("LLM_PROVIDER", "LLM_MODEL", "LLM_EFFORT")}
        engines.set_user_engine(7, "astra")
        after = {k: os.environ.get(k) for k in
                 ("LLM_PROVIDER", "LLM_MODEL", "LLM_EFFORT")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
