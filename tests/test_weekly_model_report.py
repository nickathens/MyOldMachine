"""Weekly person-model change report (utils/weekly_model_report.py).

Reflection edits person models nightly; nobody grades the edits. The weekly
report diffs each user's model.md against the version snapshot nearest 7 days
back and sends a short section-by-section note. Deterministic: pure file
diff, no LLM.

Load-bearing details locked here:
- core.memory.set_model writes model_YYYY-MM-DD_HHMMSS.md (SIX-digit time).
  The production bot's snapshot regex expects four digits; a blind copy would
  match nothing and report "no snapshots" forever. The regex test pins the
  MOM format and rejects the prod-style name.
- Self-gating: no model or no snapshots means silent skip (the job is
  registered unconditionally), a removed user is never messaged, and a week
  with no changes is recorded but not sent.
- The sent text is recorded with sent/delivered flags through
  core.users.resolve_user_dir, so tests patch core.users.USERS_DATA_DIR (and
  bot.USERS_DIR where bot code is driven) per the two-roots rule.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

import core.users as users_mod  # noqa: E402
import utils.weekly_model_report as wmr  # noqa: E402
from core.memory import MemoryManager  # noqa: E402

UID = 930_000_001

OLD_MODEL = """# Test — Working Model

Last updated: 2026-08-01

## Identity
- Name: Test
- Plays bass in a band

## Preferences
- Wants short replies

## Current State
- Building a portfolio site
"""

NEW_MODEL = """# Test — Working Model

Last updated: 2026-08-11

## Identity
- Name: Test

## Preferences
- Wants short replies
- Prefers dark UI themes

## Current State
- Building a portfolio site
"""


class WeeklyReportFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data = Path(self._tmp.name) / "data"
        self.users_dir = self.data / "users"
        self.users_dir.mkdir(parents=True)
        self.mm = MemoryManager(self.data)
        self.now = datetime(2026, 8, 11, 8, 5)
        for p in (
            patch.object(users_mod, "USERS_DATA_DIR", self.users_dir),
            patch.object(wmr, "is_registered", lambda uid: True),
            patch.object(wmr, "get_user",
                         lambda uid: {"display_name": "Test"}),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def seed_model(self, uid=UID, new_text=NEW_MODEL, old_text=OLD_MODEL,
                   snapshot_age_days=7):
        user_dir = self.mm.people_dir / str(uid)
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "model.md").write_text(new_text, encoding="utf-8")
        if old_text is not None:
            versions = user_dir / "model_versions"
            versions.mkdir(exist_ok=True)
            ts = self.now - timedelta(days=snapshot_age_days)
            name = f"model_{ts.strftime('%Y-%m-%d_%H%M%S')}.md"
            (versions / name).write_text(old_text, encoding="utf-8")
        return user_dir

    def report_log(self, uid=UID) -> list[dict]:
        path = self.users_dir / str(uid) / "weekly_model_report_log.jsonl"
        if not path.exists():
            return []
        return [json.loads(ln) for ln in
                path.read_text(encoding="utf-8").splitlines() if ln.strip()]


class SnapshotFormatTests(WeeklyReportFixture):
    def test_regex_matches_mom_six_digit_names_only(self):
        # The port trap: prod snapshots are model_YYYY-MM-DD_HHMM.md. MOM's
        # set_model writes six-digit HHMMSS; the regex must track MOM.
        self.assertTrue(wmr._SNAPSHOT_RE.match("model_2026-08-04_031502.md"))
        self.assertFalse(wmr._SNAPSHOT_RE.match("model_2026-08-04_0315.md"))

    def test_set_model_output_is_picked_up(self):
        # End-to-end against the real writer: whatever name set_model uses,
        # pick_baseline must find it.
        self.mm.set_model(UID, OLD_MODEL)   # creates model.md, no version yet
        self.mm.set_model(UID, NEW_MODEL)   # versions the old one
        versions = self.mm.people_dir / str(UID) / "model_versions"
        found = wmr.pick_baseline(versions, datetime.now(), 7)
        self.assertIsNotNone(found)
        self.assertIn("Plays bass", found[0].read_text(encoding="utf-8"))

    def test_pick_baseline_prefers_closest_to_target(self):
        user_dir = self.seed_model(snapshot_age_days=7)
        versions = user_dir / "model_versions"
        for age in (1, 13):
            ts = self.now - timedelta(days=age)
            (versions / f"model_{ts.strftime('%Y-%m-%d_%H%M%S')}.md").write_text(
                "x", encoding="utf-8")
        picked = wmr.pick_baseline(versions, self.now, 7)
        picked_ts = picked[1]
        self.assertEqual((self.now - picked_ts).days, 7)


class DiffEngineTests(WeeklyReportFixture):
    def test_split_sections_drops_preamble_and_last_updated(self):
        sections = wmr.split_sections(NEW_MODEL)
        self.assertIn("Identity", sections)
        joined = "\n".join(ln for lines in sections.values() for ln in lines)
        self.assertNotIn("Last updated", joined)
        self.assertNotIn("Working Model", joined)

    def test_section_changes_counts_and_snippets(self):
        changes = wmr.section_changes(OLD_MODEL, NEW_MODEL)
        by_name = {c["name"]: c for c in changes}
        self.assertEqual(by_name["Identity"]["removed"], 1)
        self.assertEqual(by_name["Preferences"]["added"], 1)
        self.assertIn("dark UI themes", by_name["Preferences"]["snippet"])
        self.assertIn("Plays bass", by_name["Identity"]["snippet"])
        self.assertNotIn("Current State", by_name)

    def test_added_and_removed_sections_are_named(self):
        old = "## Alpha\n- a\n"
        new = "## Beta\n- b\n"
        notes = {c["name"]: c["note"] for c in wmr.section_changes(old, new)}
        self.assertEqual(notes["Beta"], "section added")
        self.assertEqual(notes["Alpha"], "section removed")

    def test_identical_models_mean_no_changes(self):
        self.assertEqual(wmr.section_changes(NEW_MODEL, NEW_MODEL), [])

    def test_compose_message_lists_unchanged_and_correction_hint(self):
        changes = wmr.section_changes(OLD_MODEL, NEW_MODEL)
        all_names = [wmr._display_name(h) for h in wmr.split_sections(NEW_MODEL)]
        msg = wmr.compose_message("Test", self.now - timedelta(days=7),
                                  self.now, changes, Path("/tmp/x.diff"),
                                  all_names)
        self.assertIn("Weekly memory report for Test", msg)
        self.assertIn("Unchanged: Current State", msg)
        self.assertIn("tell me in chat", msg)
        self.assertLessEqual(len(msg), 3500)

    def test_compose_message_truncates_under_telegram_limit(self):
        changes = [{"name": f"S{i}", "added": 1, "removed": 0,
                    "snippet": "y" * 90, "note": ""} for i in range(6)]
        all_names = ["Z" * 400 for _ in range(30)]
        msg = wmr.compose_message("T", self.now, self.now, changes,
                                  None, all_names)
        self.assertLessEqual(len(msg), 3500)


class ProcessUserTests(WeeklyReportFixture):
    def test_no_model_is_silent_skip(self):
        self.assertEqual(
            wmr.process_user(self.mm, UID, "tok", self.now, 7, False),
            "no_model")
        self.assertEqual(self.report_log(), [])

    def test_no_snapshots_is_silent_skip(self):
        self.seed_model(old_text=None)
        self.assertEqual(
            wmr.process_user(self.mm, UID, "tok", self.now, 7, False),
            "no_snapshots")
        self.assertEqual(self.report_log(), [])

    def test_removed_user_is_never_messaged(self):
        self.seed_model()
        sends = []
        with patch.object(wmr, "is_registered", lambda uid: False), \
             patch.object(wmr, "send_message",
                          lambda *a: sends.append(a) or True):
            status = wmr.process_user(self.mm, UID, "tok", self.now, 7, False)
        self.assertEqual(status, "unregistered")
        self.assertEqual(sends, [])

    def test_no_changes_recorded_but_not_sent(self):
        self.seed_model(old_text=NEW_MODEL)
        sends = []
        with patch.object(wmr, "send_message",
                          lambda *a: sends.append(a) or True):
            status = wmr.process_user(self.mm, UID, "tok", self.now, 7, False)
        self.assertEqual(status, "no_changes")
        self.assertEqual(sends, [])
        log = self.report_log()
        self.assertEqual(len(log), 1)
        self.assertFalse(log[0]["sent"])

    def test_changed_model_is_reported_and_recorded(self):
        self.seed_model()
        sends = []
        with patch.object(wmr, "send_message",
                          lambda tok, uid, msg: sends.append((uid, msg)) or True):
            status = wmr.process_user(self.mm, UID, "tok", self.now, 7, False)
        self.assertEqual(status, "reported")
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0][0], UID)
        self.assertIn("Preferences: 1 added", sends[0][1])
        log = self.report_log()
        self.assertTrue(log[0]["sent"])
        self.assertTrue(log[0]["delivered"])
        diff_dir = self.mm.people_dir / str(UID) / "weekly_reports"
        diffs = list(diff_dir.glob("*.diff"))
        self.assertEqual(len(diffs), 1)
        self.assertIn("Plays bass", diffs[0].read_text(encoding="utf-8"))

    def test_dry_run_prints_but_records_and_sends_nothing(self):
        self.seed_model()
        sends = []
        with patch.object(wmr, "send_message",
                          lambda *a: sends.append(a) or True):
            status = wmr.process_user(self.mm, UID, "tok", self.now, 7, True)
        self.assertEqual(status, "dry_run")
        self.assertEqual(sends, [])
        self.assertEqual(self.report_log(), [])

    def test_failed_send_is_recorded_undelivered(self):
        self.seed_model()
        with patch.object(wmr, "send_message", lambda *a: False):
            status = wmr.process_user(self.mm, UID, "tok", self.now, 7, False)
        self.assertEqual(status, "undelivered")
        log = self.report_log()
        self.assertTrue(log[0]["sent"])
        self.assertFalse(log[0]["delivered"])


class JobRegistrationTests(unittest.TestCase):
    class FakeScheduler:
        def __init__(self):
            self.jobs = []

        def add_job(self, **kw):
            self.jobs.append(kw)

    def _register(self, existing_meta):
        import bot as botmod
        import core.scheduler as sched_mod
        fake = self.FakeScheduler()
        with patch.object(sched_mod, "_get_all_meta",
                          lambda: existing_meta), \
             patch.object(botmod, "get_primary_admin_id", lambda: 77):
            botmod._setup_weekly_model_report_job(fake)
        return fake.jobs

    def test_registers_weekly_monday_0805_command_job(self):
        jobs = self._register([])
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["name"], "weekly-model-report")
        self.assertEqual(job["repeat"], "weekly")
        self.assertEqual(job["job_type"], "command")
        self.assertFalse(job["notify"])
        self.assertIn("weekly_model_report.py", job["command"])
        run_at = job["run_at"]
        self.assertEqual(run_at.weekday(), 0)  # Monday
        self.assertEqual((run_at.hour, run_at.minute), (8, 5))
        self.assertGreater(run_at, datetime.now())

    def test_existing_job_is_not_duplicated(self):
        jobs = self._register([{"name": "weekly-model-report"}])
        self.assertEqual(jobs, [])


if __name__ == "__main__":
    unittest.main()
