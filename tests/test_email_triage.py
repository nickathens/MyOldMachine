"""Tests for utils/email_triage.py (pure helpers and gates).

Covers the unit-testable surface: dash stripping, JSON extraction from
model output, the non-LLM prefilter, the self-throttle, reply MIME
threading, seen-id pruning, summary rendering, the draft capability
gate, the per-user token privacy gate, the scheduler's cron repeat, the
Gmail read-state gate, the act loop (urgent-only unread-gated nags), and
the summary's unread filter.
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from core.scheduler import Scheduler
from utils.email_triage import (
    MAX_PINGS_PER_PASS,
    act_on_new_mail,
    build_digest_lines,
    build_reply_mime,
    can_draft,
    filter_unread,
    gmail_handled,
    parse_json_block,
    prefilter_category,
    prune_seen,
    should_run,
    strip_dashes,
    token_mode,
    update_classify_fail_streak,
)


def _service_returning(labels=None, exc=None):
    """A fake Gmail service whose messages().get().execute() is scripted."""
    service = Mock()
    call = service.users.return_value.messages.return_value.get.return_value
    if exc is not None:
        call.execute.side_effect = exc
    else:
        call.execute.return_value = {"labelIds": labels or []}
    return service


class TestStripDashes(unittest.TestCase):
    def test_em_and_en_dashes_become_commas(self):
        self.assertEqual(strip_dashes("yes — sure"), "yes, sure")
        self.assertEqual(strip_dashes("yes – sure"), "yes, sure")

    def test_spaced_hyphen_becomes_comma(self):
        self.assertEqual(strip_dashes("yes - sure"), "yes, sure")

    def test_inword_hyphens_and_urls_survive(self):
        self.assertEqual(strip_dashes("post-production"), "post-production")
        self.assertEqual(
            strip_dashes("see https://example.com/a-b-c now"),
            "see https://example.com/a-b-c now")

    def test_no_double_commas(self):
        self.assertNotIn(",,", strip_dashes("a — - b"))


class TestParseJsonBlock(unittest.TestCase):
    def test_plain_array(self):
        self.assertEqual(parse_json_block('[{"a": 1}]'), [{"a": 1}])

    def test_code_fence(self):
        raw = '```json\n[{"index": 0, "category": "fyi"}]\n```'
        self.assertEqual(parse_json_block(raw)[0]["category"], "fyi")

    def test_object_wrapped_in_prose(self):
        raw = 'Here is the reply:\n{"body": "ok"}\nDone.'
        self.assertEqual(parse_json_block(raw), {"body": "ok"})

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            parse_json_block("no json here at all")


class TestPrefilter(unittest.TestCase):
    def test_self_mail(self):
        cat = prefilter_category([], {}, "Me@Example.com", ["me@example.com"])
        self.assertEqual(cat, "self")

    def test_promotions_label(self):
        cat = prefilter_category(["CATEGORY_PROMOTIONS"], {}, "x@y.com", [])
        self.assertEqual(cat, "newsletter")

    def test_unsubscribe_header(self):
        cat = prefilter_category([], {"list-unsubscribe": "<mailto:u@x>"}, "x@y.com", [])
        self.assertEqual(cat, "newsletter")

    def test_updates_label_goes_to_classifier(self):
        # Banks and booking confirmations land in CATEGORY_UPDATES, so it
        # must NOT be auto-filed.
        self.assertIsNone(prefilter_category(["CATEGORY_UPDATES"], {}, "x@y.com", []))

    def test_unknown_goes_to_classifier(self):
        self.assertIsNone(prefilter_category(["INBOX"], {}, "x@y.com", []))


class TestShouldRun(unittest.TestCase):
    def test_first_run(self):
        self.assertTrue(should_run({}, datetime.now()))

    def test_recent_run_throttled(self):
        now = datetime.now()
        state = {"last_run": (now - timedelta(seconds=60)).isoformat()}
        self.assertFalse(should_run(state, now))

    def test_old_run_allowed(self):
        now = datetime.now()
        state = {"last_run": (now - timedelta(seconds=700)).isoformat()}
        self.assertTrue(should_run(state, now))

    def test_garbage_timestamp_allowed(self):
        self.assertTrue(should_run({"last_run": "not-a-date"}, datetime.now()))


class TestReplyMime(unittest.TestCase):
    EMAIL = {
        "from": "Alice <alice@example.com>",
        "reply_to": "",
        "subject": "Project question",
        "message_id_header": "<mid-1@example.com>",
        "references": "<ref-0@example.com>",
    }

    def test_threading_headers(self):
        msg = build_reply_mime(self.EMAIL, "Sure, Thursday works.")
        self.assertEqual(msg["to"], "Alice <alice@example.com>")
        self.assertEqual(msg["subject"], "Re: Project question")
        self.assertEqual(msg["In-Reply-To"], "<mid-1@example.com>")
        self.assertEqual(msg["References"], "<ref-0@example.com> <mid-1@example.com>")

    def test_existing_re_not_doubled(self):
        email = dict(self.EMAIL, subject="RE: Project question")
        msg = build_reply_mime(email, "x")
        self.assertEqual(msg["subject"], "RE: Project question")

    def test_reply_to_preferred(self):
        email = dict(self.EMAIL, reply_to="desk@example.com")
        msg = build_reply_mime(email, "x")
        self.assertEqual(msg["to"], "desk@example.com")


class TestPruneSeen(unittest.TestCase):
    def test_under_cap_unchanged(self):
        self.assertEqual(prune_seen(["a", "b"], cap=5), ["a", "b"])

    def test_over_cap_keeps_newest(self):
        ids = [str(i) for i in range(10)]
        self.assertEqual(prune_seen(ids, cap=3), ["7", "8", "9"])


class TestDigestLines(unittest.TestCase):
    """Summary rendering after the 2026-08-15 port: the caller passes only
    unread entries (filter_unread), so the header counts unread, not new."""

    def test_empty(self):
        self.assertEqual(build_digest_lines([]), "Email (24h): nothing unread.")

    def test_unread_header_marker_and_draft_suffix(self):
        ts = datetime.now().isoformat()
        entries = [
            {"ts": ts, "from": "A <a@x.com>", "subject": "s1",
             "category": "urgent", "summary": "deadline today"},
            {"ts": ts, "from": "B <b@x.com>", "subject": "s2",
             "category": "needs_reply", "summary": "asks for stems",
             "draft_id": "d1"},
            {"ts": ts, "from": "C <c@x.com>", "subject": "s3",
             "category": "newsletter", "summary": ""},
        ]
        out = build_digest_lines(entries)
        self.assertIn("3 unread, 1 waiting on a reply.", out)
        self.assertIn("URGENT A: deadline today", out)
        self.assertIn("B: asks for stems (draft saved)", out)
        self.assertIn("and 1 unread newsletter or notice", out)
        self.assertNotIn(" new.", out)

    def test_bulk_tail_plural_without_needs_reply(self):
        ts = datetime.now().isoformat()
        entries = [{"ts": ts, "from": "N <n@x.com>", "subject": f"s{i}",
                    "category": "notification", "summary": ""} for i in range(3)]
        out = build_digest_lines(entries)
        self.assertIn("3 unread.", out)
        self.assertNotIn("waiting on a reply", out)
        self.assertIn("and 3 unread newsletters and notices", out)

    def test_caps_at_eight_notables(self):
        ts = datetime.now().isoformat()
        entries = [{"ts": ts, "from": f"P{i} <p{i}@x.com>", "subject": f"s{i}",
                    "category": "fyi", "summary": f"note {i}"} for i in range(10)]
        out = build_digest_lines(entries)
        self.assertIn("plus 2 more in Gmail", out)


class TestGmailHandled(unittest.TestCase):
    """The read-state gate: True = read or dealt with, False = still unread
    in the inbox, None = cannot tell (callers treat that as unread)."""

    def test_unread_in_inbox_is_not_handled(self):
        svc = _service_returning(["UNREAD", "INBOX"])
        self.assertIs(gmail_handled(svc, "m1"), False)

    def test_read_mail_is_handled(self):
        svc = _service_returning(["INBOX"])
        self.assertIs(gmail_handled(svc, "m1"), True)

    def test_archived_unread_is_handled(self):
        # Archive, trash, junk and snooze all drop the INBOX label.
        svc = _service_returning(["UNREAD"])
        self.assertIs(gmail_handled(svc, "m1"), True)

    def test_deleted_mail_404_is_handled(self):
        class Gone(Exception):
            status_code = 404
        svc = _service_returning(exc=Gone())
        self.assertIs(gmail_handled(svc, "m1"), True)

    def test_resp_status_404_is_handled(self):
        # Older googleapiclient carries the code at e.resp.status instead.
        class Resp:
            status = 404
        class Gone(Exception):
            resp = Resp()
        svc = _service_returning(exc=Gone())
        self.assertIs(gmail_handled(svc, "m1"), True)

    def test_api_failure_is_unknown(self):
        svc = _service_returning(exc=RuntimeError("boom"))
        self.assertIsNone(gmail_handled(svc, "m1"))

    def test_missing_id_is_unknown(self):
        self.assertIsNone(gmail_handled(Mock(), ""))


class TestActOnNewMail(unittest.TestCase):
    """The 2026-08-15 act loop: urgent-only one-line nags, unread-gated;
    needs_reply silent unless its draft was actually saved."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.user_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.pings = []
        p = patch("utils.email_triage.send_ping",
                  side_effect=lambda uid, text: self.pings.append(text) or True)
        p.start()
        self.addCleanup(p.stop)

    @staticmethod
    def _email(cat, mid="m1"):
        return {"id": mid, "thread_id": "t1",
                "from": "Stelios <s@x.com>", "subject": "Nexus LS",
                "category": cat, "summary": "sum"}

    def test_urgent_unread_gets_one_line_nag(self):
        svc = _service_returning(["UNREAD", "INBOX"])
        e = self._email("urgent")
        with patch("utils.email_triage.can_draft", return_value=False):
            pings, drafts = act_on_new_mail([e], 1, self.user_dir, svc)
        self.assertEqual((pings, drafts), (1, 0))
        self.assertEqual(self.pings, ["Urgent email from Stelios: Nexus LS"])
        self.assertTrue(e["pinged"])
        self.assertFalse(e["seen"])

    def test_urgent_already_read_stays_silent(self):
        svc = _service_returning(["INBOX"])
        e = self._email("urgent")
        with patch("utils.email_triage.can_draft", return_value=False):
            pings, _ = act_on_new_mail([e], 1, self.user_dir, svc)
        self.assertEqual(pings, 0)
        self.assertEqual(self.pings, [])
        self.assertTrue(e["seen"])
        self.assertFalse(e["pinged"])

    def test_unknown_read_state_still_nags(self):
        svc = _service_returning(exc=RuntimeError("api down"))
        e = self._email("urgent")
        with patch("utils.email_triage.can_draft", return_value=False):
            pings, _ = act_on_new_mail([e], 1, self.user_dir, svc)
        self.assertEqual(pings, 1)

    def test_needs_reply_without_draft_never_pings(self):
        svc = _service_returning(["UNREAD", "INBOX"])
        e = self._email("needs_reply")
        with patch("utils.email_triage.can_draft", return_value=False):
            pings, drafts = act_on_new_mail([e], 1, self.user_dir, svc)
        self.assertEqual((pings, drafts), (0, 0))
        self.assertEqual(self.pings, [])

    def test_needs_reply_draft_failure_stays_silent(self):
        svc = _service_returning(["UNREAD", "INBOX"])
        e = self._email("needs_reply")
        with patch("utils.email_triage.can_draft", return_value=True), \
             patch("utils.email_triage.draft_reply_body",
                   side_effect=RuntimeError("provider down")):
            pings, drafts = act_on_new_mail([e], 1, self.user_dir, svc)
        self.assertEqual((pings, drafts), (0, 0))
        self.assertEqual(self.pings, [])

    def test_needs_reply_saved_draft_keeps_its_ping(self):
        svc = _service_returning(["UNREAD", "INBOX"])
        svc.users.return_value.drafts.return_value.create.return_value \
            .execute.return_value = {"id": "d9"}
        e = self._email("needs_reply")
        with patch("utils.email_triage.can_draft", return_value=True), \
             patch("utils.email_triage.draft_reply_body",
                   return_value="Sure, Thursday works."):
            pings, drafts = act_on_new_mail([e], 1, self.user_dir, svc)
        self.assertEqual((pings, drafts), (1, 1))
        self.assertEqual(e["draft_id"], "d9")
        self.assertIn("Draft saved to Gmail drafts", self.pings[0])
        self.assertIn("Sure, Thursday works.", self.pings[0])

    def test_needs_reply_already_read_skips_draft_and_ping(self):
        svc = _service_returning(["INBOX"])
        e = self._email("needs_reply")
        with patch("utils.email_triage.can_draft", return_value=True), \
             patch("utils.email_triage.draft_reply_body") as drafted:
            pings, drafts = act_on_new_mail([e], 1, self.user_dir, svc)
        self.assertEqual((pings, drafts), (0, 0))
        drafted.assert_not_called()
        self.assertTrue(e["seen"])

    def test_filed_mail_gets_no_read_check(self):
        svc = Mock()
        e = self._email("newsletter")
        with patch("utils.email_triage.can_draft", return_value=False):
            pings, _ = act_on_new_mail([e], 1, self.user_dir, svc)
        self.assertEqual(pings, 0)
        svc.users.assert_not_called()
        self.assertFalse(e["pinged"])

    def test_ping_cap_respected(self):
        svc = _service_returning(["UNREAD", "INBOX"])
        emails = [self._email("urgent", mid=f"m{i}")
                  for i in range(MAX_PINGS_PER_PASS + 2)]
        with patch("utils.email_triage.can_draft", return_value=False):
            pings, _ = act_on_new_mail(emails, 1, self.user_dir, svc)
        self.assertEqual(pings, MAX_PINGS_PER_PASS)
        self.assertEqual(len(self.pings), MAX_PINGS_PER_PASS)


class TestFilterUnread(unittest.TestCase):
    """The summary's unread filter: read mail drops out, errors fail open."""

    TS = datetime.now().isoformat()

    def _entry(self, mid, cat="fyi", ts=None, **extra):
        e = {"id": mid, "ts": ts or self.TS, "from": "A <a@x.com>",
             "subject": "s", "category": cat, "summary": ""}
        e.update(extra)
        return e

    def test_read_entries_drop_unread_stay(self):
        def get_side(userId, id, format):
            labels = {"m-read": ["INBOX"], "m-unread": ["UNREAD", "INBOX"]}[id]
            m = Mock()
            m.execute.return_value = {"labelIds": labels}
            return m
        svc = Mock()
        svc.users.return_value.messages.return_value.get.side_effect = get_side
        out = filter_unread([self._entry("m-read"), self._entry("m-unread")], svc)
        self.assertEqual([e["id"] for e in out], ["m-unread"])

    def test_check_error_fails_open(self):
        svc = _service_returning(exc=RuntimeError("api down"))
        out = filter_unread([self._entry("m1")], svc)
        self.assertEqual(len(out), 1)

    def test_no_service_lists_all_but_self(self):
        entries = [self._entry("m1"), self._entry("m2", cat="self")]
        out = filter_unread(entries, None)
        self.assertEqual([e["id"] for e in out], ["m1"])

    def test_self_mail_never_listed(self):
        svc = _service_returning(["UNREAD", "INBOX"])
        out = filter_unread([self._entry("m1", cat="self")], svc)
        self.assertEqual(out, [])

    def test_duplicate_ids_keep_newest(self):
        old = self._entry("m1", ts="2026-08-15T08:00:00")
        new = self._entry("m1", ts="2026-08-15T09:00:00", summary="newer")
        svc = _service_returning(["UNREAD", "INBOX"])
        out = filter_unread([old, new], svc)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["summary"], "newer")


class TestCanDraft(unittest.TestCase):
    def test_cli_providers_always_draft(self):
        for p in ("claude", "claude-cli", "fcc"):
            self.assertTrue(can_draft(p, "anything"))

    def test_capable_apis_draft(self):
        for p in ("claude-api", "openai", "deepseek", "grok", "kimi",
                  "minimax", "gemini"):
            self.assertTrue(can_draft(p, "some-model"))

    def test_openrouter_free_tier_classify_only(self):
        self.assertFalse(can_draft("openrouter", "meta-llama/llama-3.3-70b:free"))
        self.assertTrue(can_draft("openrouter", "anthropic/claude-sonnet-4.6"))

    def test_ollama_size_gate(self):
        self.assertFalse(can_draft("ollama", "qwen3.5:4b"))
        self.assertTrue(can_draft("ollama", "llama3.3:70b"))
        self.assertTrue(can_draft("ollama-cloud", "qwen3.5:235b-cloud"))

    def test_unknown_provider_classify_only(self):
        self.assertFalse(can_draft("mystery", "model"))


class TestTokenMode(unittest.TestCase):
    """The privacy gate: a second user on a shared install must never
    fall through to the owner's bot-root token."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bot_dir = Path(self.tmp.name) / "bot"
        self.user_dir = Path(self.tmp.name) / "users" / "111"
        (self.bot_dir).mkdir(parents=True)
        (self.user_dir).mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_per_user_token_wins(self):
        token = self.user_dir / "google" / "gmail_token.json"
        token.parent.mkdir(parents=True)
        token.write_text("{}")
        self.assertEqual(
            token_mode(111, self.user_dir, self.bot_dir, primary_user=222),
            "user")

    def test_legacy_token_only_for_primary_admin(self):
        (self.bot_dir / "gmail_token.json").write_text("{}")
        self.assertEqual(
            token_mode(111, self.user_dir, self.bot_dir, primary_user=111),
            "legacy")
        self.assertEqual(
            token_mode(222, self.user_dir, self.bot_dir, primary_user=111),
            "missing")

    def test_legacy_token_not_granted_by_smaller_user_id(self):
        # The old gate was allowed[0] (smallest Telegram ID). A non-admin
        # with a smaller ID must NOT reach the owner's mailbox.
        (self.bot_dir / "gmail_token.json").write_text("{}")
        self.assertEqual(
            token_mode(111, self.user_dir, self.bot_dir, primary_user=222),
            "missing")

    def test_no_token_missing(self):
        self.assertEqual(
            token_mode(111, self.user_dir, self.bot_dir, primary_user=111),
            "missing")


class TestCronRepeatTrigger(unittest.TestCase):
    """The scheduler's new intra-day cron repeat (used by the triage job)."""

    def test_valid_cron_pattern(self):
        trigger = Scheduler._build_trigger(None, datetime.now(), "cron:5-59/15:8-23")
        self.assertIsInstance(trigger, CronTrigger)
        self.assertIn("5-59/15", str(trigger))
        self.assertIn("8-23", str(trigger))

    def test_minute_only_pattern(self):
        trigger = Scheduler._build_trigger(None, datetime.now(), "cron:*/30")
        self.assertIsInstance(trigger, CronTrigger)
        self.assertIn("*/30", str(trigger))

    def test_invalid_pattern_falls_back_to_one_shot(self):
        trigger = Scheduler._build_trigger(None, datetime.now(), "cron:99-200")
        self.assertIsInstance(trigger, DateTrigger)

    def test_existing_repeats_unaffected(self):
        trigger = Scheduler._build_trigger(None, datetime.now(), "daily")
        self.assertIsInstance(trigger, CronTrigger)


class TestClassifyFailStreak(unittest.TestCase):
    """The dead-classifier alarm (ported from the production bot, 2026-08-11).

    classify_batch failures degrade every chunk to "unclassified", a FILED
    category, so a dead classifier silently switches the urgent-mail alarm
    off. The streak counts consecutive failed passes and pings the owner.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_file = Path(self.tmp.name) / "state.json"
        self.pings = []
        patcher = patch("utils.email_triage.send_ping",
                        side_effect=lambda uid, text: self.pings.append(text) or True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_failure_increments_streak_without_early_ping(self):
        state = {}
        update_classify_fail_streak(1, state, self.state_file, True, "boom")
        self.assertEqual(state["classify_fail_streak"], 1)
        self.assertEqual(self.pings, [])

    def test_clean_pass_resets_streak(self):
        state = {"classify_fail_streak": 2}
        update_classify_fail_streak(1, state, self.state_file, True, "")
        self.assertEqual(state["classify_fail_streak"], 0)

    def test_nothing_to_classify_leaves_streak_untouched(self):
        state = {"classify_fail_streak": 2}
        update_classify_fail_streak(1, state, self.state_file, False, "")
        self.assertEqual(state["classify_fail_streak"], 2)

    def test_third_failure_pings_and_persists_state(self):
        state = {}
        for _ in range(3):
            update_classify_fail_streak(1, state, self.state_file, True, "dead CLI")
        self.assertEqual(len(self.pings), 1)
        self.assertIn("will NOT ping", self.pings[0])
        self.assertIn("dead CLI", self.pings[0])
        # The ping helper saves state itself, so a crash later in the pass
        # cannot repeat the ping on the next one.
        on_disk = json.loads(self.state_file.read_text())
        self.assertEqual(on_disk["classify_fail_streak"], 3)
        self.assertTrue(on_disk["classify_error_pinged_at"])

    def test_cooldown_suppresses_repeat_within_24h(self):
        state = {"classify_fail_streak": 2,
                 "classify_error_pinged_at": datetime.now().isoformat()}
        update_classify_fail_streak(1, state, self.state_file, True, "still dead")
        self.assertEqual(state["classify_fail_streak"], 3)
        self.assertEqual(self.pings, [])

    def test_cooldown_expiry_pings_again(self):
        stale = (datetime.now() - timedelta(hours=25)).isoformat()
        state = {"classify_fail_streak": 5, "classify_error_pinged_at": stale}
        update_classify_fail_streak(1, state, self.state_file, True, "still dead")
        self.assertEqual(len(self.pings), 1)

    def test_corrupt_streak_value_recovers(self):
        state = {"classify_fail_streak": "garbage"}
        update_classify_fail_streak(1, state, self.state_file, True, "boom")
        self.assertEqual(state["classify_fail_streak"], 1)

    def test_gmail_read_ping_cooldown_is_a_separate_key(self):
        # A fresh Gmail-unreachable ping must not eat the classifier alert.
        state = {"classify_fail_streak": 2,
                 "error_pinged_at": datetime.now().isoformat()}
        update_classify_fail_streak(1, state, self.state_file, True, "boom")
        self.assertEqual(len(self.pings), 1)

    def test_run_triage_is_wired_to_the_streak(self):
        # run_triage needs a live Gmail service, so the streak seam is tested
        # directly above and this deliberate source-text guard pins the
        # wiring: silently dropping the call site must break a test.
        source = (Path(__file__).parent.parent / "utils" / "email_triage.py").read_text()
        self.assertIn("update_classify_fail_streak(user_id, state, state_file,", source)


class TestUnreadGateWiring(unittest.TestCase):
    """Same deliberate source-text pattern as the streak guard above: the
    act loop and the unread filter are tested directly as seams, and these
    pins make silently dropping either call site break a test."""

    SOURCE = (Path(__file__).parent.parent / "utils" / "email_triage.py").read_text()

    def test_run_triage_is_wired_to_the_act_loop(self):
        self.assertIn("act_on_new_mail(emails, user_id, user_dir, service)",
                      self.SOURCE)

    def test_daily_summary_is_wired_to_the_unread_filter(self):
        self.assertIn("build_digest_lines(filter_unread(entries, service)",
                      self.SOURCE)


if __name__ == "__main__":
    unittest.main()
