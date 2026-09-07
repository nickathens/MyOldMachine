"""Regression tests for the 2026-09-06 production-bot audit, core half.

The audit ran against the Linux production bot (a sibling codebase that shares
this repo's skills and much of its plumbing) and reported 42 findings. This
file pins the ones whose defect was re-verified in THIS repo before anything
was changed. Each test names its finding id.

  F02  a stopped turn left its tool processes running
  F03  a CLI that exits with lines still buffered lost them, result included
  F04  a Codex turn cut before turn.completed was shipped as a finished answer
  F05  the stored history was shortened the moment compaction was SCHEDULED
  F06  a rejected Telegram send still exited 0
  F07  a scheduled job whose result never arrived was logged as a success
  F11  /remind fed the whole line to the time parser, so the message vanished
  F12  a recurring reminder for a future date started weeks early
  F16  a Codex turn ran every skill with none of the Claude turn's hooks
  F17  the Mini App submitted stale settings and mid-upload references

Standard library only. Subprocesses, the CLIs and Telegram are faked, so what
is pinned is the decision each path makes, not the tool behind it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# bot.py installs a file handler on the production log at import time, and
# logging is configured once per process (tests/test_log_isolation.py).
os.environ["MOM_TEST"] = "1"

from core import llm as llm_mod  # noqa: E402
from core.scheduler import Scheduler, parse_natural_time  # noqa: E402
from core.session import DEFAULT_CONFIG, SessionManager, history_digest  # noqa: E402


# --- F11: /remind must not eat its own message ------------------------------

class StrictTimeParsingTests(unittest.TestCase):
    """The parser had one mode: match a PREFIX. /remind searched longest-first
    and handed it the whole line, so "in 30 minutes Check the oven" parsed as a
    time and left no message, and the command answered "Please include a
    message." A longer line lost its opening words instead."""

    WHOLE = ["in 30 minutes", "tomorrow at 9am", "at 3pm", "15:30",
             "monday at 10am", "next monday", "5pm on monday"]
    WITH_MESSAGE = ["in 30 minutes Check the oven", "tomorrow at 9am Meeting",
                    "at 3pm Call mom", "15:30 do laundry",
                    "monday at 10am Team sync"]

    def test_strict_accepts_a_bare_time_expression(self):
        for text in self.WHOLE:
            with self.subTest(text=text):
                self.assertIsNotNone(parse_natural_time(text, strict=True))

    def test_strict_refuses_a_time_followed_by_a_message(self):
        for text in self.WITH_MESSAGE:
            with self.subTest(text=text):
                self.assertIsNone(parse_natural_time(text, strict=True))

    def test_default_still_matches_a_prefix(self):
        # utils/scheduler_cli.py and /task pass an already-isolated time
        # string and rely on the lenient behaviour. It must not change.
        self.assertIsNotNone(parse_natural_time("in 30 minutes Check the oven"))

    def test_remind_keeps_the_whole_message(self):
        """The handler's own search, run here rather than described."""
        def split(text):
            words = text.split()
            for i in range(min(6, len(words)), 0, -1):
                parsed = parse_natural_time(" ".join(words[:i]), strict=True)
                if parsed:
                    return parsed, " ".join(words[i:])
            return None, None

        when, msg = split("in 30 minutes Check the oven")
        self.assertIsNotNone(when)
        self.assertEqual(msg, "Check the oven")

        when, msg = split("at 3pm Please remember to turn off the oven")
        self.assertIsNotNone(when)
        self.assertEqual(msg, "Please remember to turn off the oven")


# --- F12: a recurring reminder starts when it was asked to -------------------

class RecurringStartDateTests(unittest.TestCase):
    """Every cron trigger was built from run_at's hour/minute/day only, so the
    date was thrown away: a daily reminder requested for 20 October began the
    next morning, weekly the next week, monthly on the 20th of THIS month."""

    def _first_fire(self, repeat):
        start = datetime(2026, 10, 20, 9, 0)   # a Tuesday
        trigger = Scheduler._build_trigger(None, start, repeat, None, None)
        now = datetime(2026, 9, 6, 12, 0).astimezone()
        fire = trigger.get_next_fire_time(None, now)
        return fire.replace(tzinfo=None)

    def test_daily_weekly_monthly_wait_for_the_requested_date(self):
        for repeat in ("daily", "weekly", "monthly"):
            with self.subTest(repeat=repeat):
                self.assertGreaterEqual(self._first_fire(repeat),
                                        datetime(2026, 10, 20, 9, 0))

    def test_biweekly_was_already_correct(self):
        self.assertEqual(self._first_fire("biweekly"), datetime(2026, 10, 20, 9, 0))

    def test_weekdays_variant_also_carries_the_start(self):
        start = datetime(2026, 10, 20, 9, 0)
        trigger = Scheduler._build_trigger(None, start, "daily", [0, 2], None)
        now = datetime(2026, 9, 6, 12, 0).astimezone()
        self.assertGreaterEqual(trigger.get_next_fire_time(None, now).replace(tzinfo=None),
                                datetime(2026, 10, 20, 9, 0))


# --- F02: stopping a turn stops what the turn started -----------------------

class KillTurnTests(unittest.TestCase):
    """`process.kill()` reaches the CLI and nothing it launched. The CLI now
    runs in its own session, so its pid is the group id of every tool command,
    and _kill_turn takes the group."""

    def test_a_non_process_stub_never_reaches_killpg(self):
        # A test double carrying an arbitrary integer pid must not be able to
        # signal a real, unrelated process group.
        stub = types.SimpleNamespace(pid=4242, returncode=None, killed=False)
        stub.kill = lambda: setattr(stub, "killed", True)
        with mock.patch.object(llm_mod.os, "killpg") as killpg:
            llm_mod._kill_turn(stub)
        killpg.assert_not_called()
        self.assertTrue(stub.killed)

    def test_an_exited_process_is_left_alone(self):
        stub = types.SimpleNamespace(pid=1, returncode=0, killed=False)
        stub.kill = lambda: setattr(stub, "killed", True)
        llm_mod._kill_turn(stub)
        self.assertFalse(stub.killed)

    def test_the_real_group_is_signalled_and_the_child_subtree_dies(self):
        """A real grandchild, orphaned on purpose, outlives a parent-only kill
        and does not outlive _kill_turn."""
        marker = Path(tempfile.mkdtemp()) / "grandchild-ran"
        script = (
            "import os, subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c',\n"
            "  \"import time, sys, pathlib; time.sleep(6);\"\n"
            "  \"pathlib.Path(sys.argv[1]).write_text('x')\", %r])\n"
            "time.sleep(30)\n" % str(marker)
        )
        proc = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
        try:
            self.assertEqual(os.getpgid(proc.pid), proc.pid,
                             "the child must lead its own group for killpg to be safe")
            os.killpg(os.getpgid(proc.pid), 9)      # what _kill_turn does
        finally:
            proc.wait(timeout=10)
        # Well past the grandchild's own sleep: it never got to write.
        deadline = 8
        while deadline > 0 and not marker.exists():
            deadline -= 1
            subprocess.run([sys.executable, "-c", "import time; time.sleep(1)"], check=False)
        self.assertFalse(marker.exists(),
                         "the grandchild survived a group kill, so the group is wrong")

    def test_both_cli_turns_start_their_own_session(self):
        """Without start_new_session the child shares the bot's group and
        _kill_turn's group kill is either a no-op or aimed at the bot."""
        source = (REPO / "core" / "llm.py").read_text(encoding="utf-8")
        spawns = source.count("await asyncio.create_subprocess_exec(\n                *cmd,")
        self.assertEqual(spawns, 2, "expected exactly the two CLI turn spawns")
        # Count the keyword argument, not the docstring that explains it.
        self.assertEqual(source.count("\n                start_new_session=True,"), 2)


# --- F03: a finished CLI keeps its last lines -------------------------------

class DrainAfterExitTests(unittest.TestCase):
    """The loop broke on `process.returncode is not None`. A process can exit
    with several lines still in the pipe, and the final result is the last of
    them, so the answer was thrown away and the turn reported as empty."""

    def test_a_real_exited_process_still_has_lines_to_read(self):
        """The premise, measured rather than asserted: exit is not EOF."""
        async def go():
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c",
                "print('one'); print('two'); print('three')",
                stdout=asyncio.subprocess.PIPE)
            await proc.wait()
            self.assertIsNotNone(proc.returncode)
            drained = []
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                drained.append(line.decode().strip())
            return drained
        self.assertEqual(asyncio.run(go()), ["one", "two", "three"])

    def test_neither_read_loop_leaves_on_the_exit_code_alone(self):
        source = (REPO / "core" / "llm.py").read_text(encoding="utf-8")
        # The old shape, in either provider, is the defect.
        self.assertNotIn("elif line == b'':\n                    break\n\n"
                         "                if process.returncode is not None:\n"
                         "                    break", source)
        self.assertEqual(
            source.count("if process.returncode is not None and line is None:"), 2)


# --- F04: an interrupted Codex turn says so ---------------------------------

def _codex_provider():
    provider = llm_mod.CodexCLIProvider.__new__(llm_mod.CodexCLIProvider)
    provider.model = "gpt-6-astra"
    provider.api_key = ""
    return provider


class CodexInterruptedTurnTests(unittest.TestCase):
    """Success was `returncode == 0 or some text arrived`. A process killed
    after emitting an agent message (OOM, a crash, a signal) therefore came
    back looking exactly like a completed answer."""

    def test_interrupted_notice_marks_a_signal_death(self):
        notice = llm_mod._interrupted_notice(-9)
        self.assertTrue(notice.strip(), "a cut turn must carry a trailer")

    def test_the_verdict_is_turn_completed_not_the_exit_code(self):
        source = (REPO / "core" / "llm.py").read_text(encoding="utf-8")
        self.assertIn("turn_completed = True", source)
        self.assertIn("if not turn_completed:", source)
        # An `error` EVENT is a retry notice on a turn that often still
        # completes, so it must not be the verdict on its own.
        self.assertIn("stream_error = data.get(\"message\")", source)
        self.assertIn("failure = turn_failed_message or (None if turn_completed else stream_error)",
                      source)


# --- F05: history is not shortened before its summary exists ----------------

class CompactionTrimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.session = SessionManager.__new__(SessionManager)
        self.session.conversation_file = Path(self.tmp) / "conversation.json"
        self.session.summary_file = Path(self.tmp) / "conversation_summary.json"
        self.session.config = dict(DEFAULT_CONFIG)
        self.history = [{"role": "user", "content": f"m{i}"} for i in range(20)]
        self.session.conversation_file.write_text(json.dumps(self.history))

    def _write_summary(self, count, digest_of):
        self.session.summary_file.write_text(json.dumps({
            "summary": "a summary",
            "compacted_messages": count,
            "trim_pending": {"count": count, "digest": history_digest(digest_of)},
        }))

    def test_no_summary_means_no_trim(self):
        self.assertEqual(len(self.session.apply_pending_trim(list(self.history))), 20)

    def test_a_summary_without_a_pending_trim_changes_nothing(self):
        self.session.summary_file.write_text(json.dumps({"summary": "s"}))
        self.assertEqual(len(self.session.apply_pending_trim(list(self.history))), 20)

    def test_the_trim_lands_only_once_its_summary_is_on_disk(self):
        self._write_summary(10, self.history[:10])
        trimmed = self.session.apply_pending_trim(list(self.history))
        self.assertEqual([m["content"] for m in trimmed],
                         [f"m{i}" for i in range(10, 20)])
        # applied to disk, and not applied twice
        self.assertEqual(len(json.loads(self.session.conversation_file.read_text())), 10)
        self.assertNotIn("trim_pending", json.loads(self.session.summary_file.read_text()))
        self.assertEqual(len(self.session.apply_pending_trim(trimmed)), 10)

    def test_a_head_that_moved_keeps_its_messages(self):
        """A /clear, a hand edit or a crash between the two writes must not
        cost messages the summary does not actually cover."""
        self._write_summary(10, self.history[:10])
        other = [{"role": "user", "content": "something else"}] + self.history
        kept = self.session.apply_pending_trim(other)
        self.assertEqual(len(kept), len(other))
        self.assertNotIn("trim_pending", json.loads(self.session.summary_file.read_text()))

    def test_a_shorter_history_than_the_record_keeps_its_messages(self):
        self._write_summary(10, self.history[:10])
        self.assertEqual(len(self.session.apply_pending_trim(self.history[:3])), 3)

    def test_the_trim_survives_the_tidy_that_save_conversation_runs(self):
        """Review of #156: the digest was taken on the batch as it sat in
        memory, and save_conversation() then rewrote those same messages
        (long user turns cut to 1500 chars, long code blocks replaced), so
        the head on disk never matched, the trim was dropped for good, and
        the same ten messages were summarised again on the next cycle."""
        session = SessionManager.__new__(SessionManager)
        session.conversation_file = Path(self.tmp) / "c3.json"
        session.summary_file = Path(self.tmp) / "s3.json"
        session.session_meta_file = Path(self.tmp) / "meta3.json"
        session.config = dict(DEFAULT_CONFIG)
        session._compaction_runner = None
        history = [{"role": "user", "content": "x" * 3000 if i % 2 == 0 else f"m{i}"}
                   for i in range(41)]
        scheduled = []
        session._run_compaction_thread = lambda *a, **k: scheduled.append(k.get("compacted"))
        with mock.patch("core.session.shutil.which", return_value="/usr/bin/claude"):
            returned, _ = session.compact_conversation(list(history), session.summary_file)
        # what the runners write once the summary lands
        session.summary_file.write_text(json.dumps({
            "summary": "a summary", "compacted_messages": 10,
            "trim_pending": {"count": len(scheduled[0]),
                             "digest": history_digest(scheduled[0])},
        }))
        session.save_conversation(returned)  # the tidy
        self.assertEqual(len(session.load_conversation()), 31)
        self.assertNotIn("trim_pending", json.loads(session.summary_file.read_text()))

    def test_compaction_returns_the_history_whole(self):
        """The defect itself: the caller used to receive, and save, a history
        already missing the batch whose summary had not been written yet."""
        session = SessionManager.__new__(SessionManager)
        session.conversation_file = Path(self.tmp) / "c2.json"
        session.summary_file = Path(self.tmp) / "s2.json"
        session.config = {**DEFAULT_CONFIG, "compaction_enabled": True, "compaction_batch_size": 10}
        session._compaction_runner = None
        scheduled = []
        session._run_compaction_thread = lambda *a, **k: scheduled.append(k.get("compacted"))
        with mock.patch("core.session.shutil.which", return_value="/usr/bin/claude"):
            returned, _ = session.compact_conversation(list(self.history), session.summary_file)
        self.assertEqual(len(returned), 20, "compaction must not shorten the caller's history")
        self.assertEqual(len(scheduled[0]), 10, "the batch is handed to the writer")


# --- F06: a rejected send is a failure --------------------------------------

class SendExitStatusTests(unittest.TestCase):
    """Alert sweeps and scheduled deliverers gate on the return code. The CLI
    printed "Document sent: False" and exited 0, so a rejected send was
    recorded as delivered."""

    def _run_main(self, ok):
        spec = importlib.util.spec_from_file_location(
            "send_to_telegram_under_test", REPO / "utils" / "send_to_telegram.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        argv = ["send_to_telegram.py", "--user", "1", "--message", "hello"]
        with mock.patch.object(module, "get_token", return_value="t"), \
             mock.patch.object(module, "send_message", return_value=ok), \
             mock.patch.object(sys, "argv", argv), \
             mock.patch.dict(os.environ, {"JARVIS_USER_ID": ""}, clear=False):
            try:
                module.main()
            except SystemExit as exc:
                return exc.code
        return 0

    def test_a_rejected_send_exits_non_zero(self):
        self.assertEqual(self._run_main(False), 2)

    def test_an_accepted_send_exits_zero(self):
        self.assertEqual(self._run_main(True), 0)


# --- F07: a scheduled job is done when its result arrives -------------------

class ScheduledDeliveryTests(unittest.TestCase):
    def test_a_failed_delivery_is_not_a_completed_job(self):
        from core import scheduler as sched_mod
        fake = types.SimpleNamespace(_call_claude_fn=None)

        async def run_task(user_id, prompt):
            return "the answer"
        fake._call_claude_fn = run_task

        meta = {"user_id": 1, "name": "nightly", "message": "do it", "notify": True}
        logged, deleted = [], []
        with mock.patch.object(sched_mod, "get_scheduler", return_value=fake), \
             mock.patch.object(sched_mod, "_get_meta", return_value=meta), \
             mock.patch.object(sched_mod, "_log_execution",
                               side_effect=lambda *a, **k: logged.append(a)), \
             mock.patch.object(sched_mod, "_delete_meta",
                               side_effect=lambda jid: deleted.append(jid)), \
             mock.patch.object(sched_mod, "_send_with_retry",
                               new=mock.AsyncMock(return_value=False)):
            asyncio.run(sched_mod._execute_agent("job-1"))

        self.assertTrue(logged, "the run must be recorded")
        self.assertFalse(logged[0][3], "an undelivered result is not a success")
        self.assertEqual(deleted, [], "a one-shot job stays for retry")


    def test_an_undelivered_result_is_not_chased_by_a_failure_notice(self):
        """Review of #156: raising on the failed delivery sent the job into
        the except path, which then tried to deliver the failure notice down
        the same dead line, another three attempts and fifteen seconds."""
        from core import scheduler as sched_mod
        fake = types.SimpleNamespace(_call_claude_fn=None)

        async def run_task(user_id, prompt):
            return "the answer"
        fake._call_claude_fn = run_task

        meta = {"user_id": 1, "name": "nightly", "message": "do it", "notify": True}
        send = mock.AsyncMock(return_value=False)
        with mock.patch.object(sched_mod, "get_scheduler", return_value=fake), \
             mock.patch.object(sched_mod, "_get_meta", return_value=meta), \
             mock.patch.object(sched_mod, "_log_execution"), \
             mock.patch.object(sched_mod, "_delete_meta"), \
             mock.patch.object(sched_mod, "_send_with_retry", new=send):
            asyncio.run(sched_mod._execute_agent("job-1"))
        self.assertEqual(send.await_count, 1, "only the result itself is attempted")


# --- F16: both CLIs run the same skill hooks --------------------------------

class CodexHookParityTests(unittest.TestCase):
    """A Claude turn passed every Bash call through the resource gate, logged
    it, and cleaned up after the turn. A Codex turn did none of that, so which
    engine was selected decided whether the safeguards applied at all."""

    def setUp(self):
        import bot
        self.bot = bot
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = bot.BOT_DIR
        bot.BOT_DIR = self.tmp

    def tearDown(self):
        self.bot.BOT_DIR = self._orig

    def test_the_hooks_file_is_written_with_this_installs_paths(self):
        self.bot._configure_codex_hooks()
        written = json.loads((self.tmp / ".codex" / "hooks.json").read_text())
        commands = [h["command"]
                    for entries in written["hooks"].values()
                    for entry in entries for h in entry["hooks"]]
        self.assertTrue(any(str(self.tmp / "utils" / "skill_hook_gate.sh") == c
                            for c in commands))
        self.assertTrue(any(c.endswith(str(self.tmp / "utils" / "skill_hooks.py"))
                            for c in commands))
        self.assertEqual(set(written["hooks"]), {"PreToolUse", "PostToolUse", "Stop"})

    def test_writing_twice_changes_nothing(self):
        self.bot._configure_codex_hooks()
        first = (self.tmp / ".codex" / "hooks.json").read_text()
        self.bot._configure_codex_hooks()
        self.assertEqual((self.tmp / ".codex" / "hooks.json").read_text(), first)

    def test_a_moved_install_replaces_its_stale_absolute_paths(self):
        hooks = self.tmp / ".codex" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        hooks.write_text(json.dumps({"hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "/old/install/utils/skill_hook_gate.sh"}]}]}}))
        self.bot._configure_codex_hooks()
        text = hooks.read_text()
        self.assertNotIn("/old/install/", text)
        self.assertIn(str(self.tmp), text)

    def test_a_foreign_hook_is_kept(self):
        hooks = self.tmp / ".codex" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        hooks.write_text(json.dumps({"hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "/usr/local/bin/somebody-elses-hook"}]}]}}))
        self.bot._configure_codex_hooks()
        self.assertIn("somebody-elses-hook", hooks.read_text())

    def test_hooks_are_configured_wherever_a_provider_is_built(self):
        """Review of #156: the hooks were written once at boot. /provider,
        /model, /apikey and the .env hot reload all rebuild the provider
        through _build_llm_provider and none of them touched the hooks, so a
        switch to Codex on a running bot ran with none of the safeguards."""
        codex = llm_mod.CodexCLIProvider.__new__(llm_mod.CodexCLIProvider)
        codex.warm_hook_trust_probe = mock.Mock(return_value=True)
        claude = llm_mod.ClaudeCLIProvider.__new__(llm_mod.ClaudeCLIProvider)
        self.addCleanup(setattr, self.bot, "_llm_provider_spec", self.bot._llm_provider_spec)
        with mock.patch.object(self.bot, "create_provider", return_value=codex), \
             mock.patch.object(self.bot, "_configure_codex_hooks") as codex_hooks, \
             mock.patch.object(self.bot, "_configure_claude_hooks") as claude_hooks:
            self.bot._build_llm_provider("codex", "gpt-5", "")
        codex_hooks.assert_called_once()
        claude_hooks.assert_not_called()
        codex.warm_hook_trust_probe.assert_called_once()
        with mock.patch.object(self.bot, "create_provider", return_value=claude), \
             mock.patch.object(self.bot, "_configure_codex_hooks") as codex_hooks, \
             mock.patch.object(self.bot, "_configure_claude_hooks") as claude_hooks:
            self.bot._build_llm_provider("claude", "opus", "")
        claude_hooks.assert_called_once()
        codex_hooks.assert_not_called()

    def test_the_trust_bypass_is_only_passed_to_a_cli_that_takes_it(self):
        """An unknown flag is a hard abort with no events, so an older codex
        must never be handed it."""
        llm_mod._codex_accepts_hook_trust_bypass.cache_clear()
        with mock.patch.object(llm_mod.subprocess, "run",
                               return_value=types.SimpleNamespace(stdout="", stderr="")):
            self.assertFalse(llm_mod._codex_accepts_hook_trust_bypass("codex-old"))
        llm_mod._codex_accepts_hook_trust_bypass.cache_clear()
        with mock.patch.object(llm_mod.subprocess, "run",
                               return_value=types.SimpleNamespace(
                                   stdout="  --dangerously-bypass-hook-trust\n", stderr="")):
            self.assertTrue(llm_mod._codex_accepts_hook_trust_bypass("codex-new"))
        llm_mod._codex_accepts_hook_trust_bypass.cache_clear()

    def test_a_missing_binary_is_not_treated_as_support(self):
        llm_mod._codex_accepts_hook_trust_bypass.cache_clear()
        with mock.patch.object(llm_mod.subprocess, "run", side_effect=OSError):
            self.assertFalse(llm_mod._codex_accepts_hook_trust_bypass("nope"))
        llm_mod._codex_accepts_hook_trust_bypass.cache_clear()


# --- F17: the Mini App submits what the user chose --------------------------

class MediaGenControlTests(unittest.TestCase):
    """Selecting a model re-rendered some dependent controls and not others,
    so a 4k resolution and a previous model's reference survived a switch; and
    Generate stayed live while a reference was still uploading, which sends
    the job without it."""

    @classmethod
    def setUpClass(cls):
        cls.source = (REPO / "miniapp" / "static" / "index.html").read_text(encoding="utf-8")

    def _model_click_handler(self):
        start = self.source.index("function renderMgModels()")
        end = self.source.index("function getModelAspects()")
        return self.source[start:end]

    def test_choosing_a_model_re_renders_every_dependent_control(self):
        handler = self._model_click_handler()
        for call in ("renderMgAspect()", "renderMgDuration()", "renderMgResolution()",
                     "renderMgOptions()", "renderMgAttach()"):
            with self.subTest(call=call):
                self.assertIn(call, handler)

    def test_generate_is_held_while_a_reference_uploads(self):
        start = self.source.index("function updateGenerateBtn()")
        body = self.source[start:self.source.index("}", self.source.index("btn.disabled", start))]
        self.assertIn("mgAttachState.uploading", body)
        self.assertIn("btn.disabled=!prompt||needsAttach||mgAttachState.uploading;", body)

    def test_a_failed_upload_hands_control_back(self):
        upload = self.source.index("mgAttachState.uploading=true;")
        catch = self.source.index(".catch(function(err)", upload)
        end = self.source.index("});", catch)
        self.assertIn("updateGenerateBtn()", self.source[catch:end],
                      "the button is held during an upload, so a failure must release it")

    def test_the_button_is_held_the_moment_the_upload_starts(self):
        upload = self.source.index("mgAttachState.uploading=true;")
        self.assertIn("updateGenerateBtn()", self.source[upload:upload + 120])

    def test_a_model_with_no_resolution_choice_sends_none(self):
        """Review of #156: renderMgResolution() hid the control for such a
        model and left the previous model's 4k in mgState, and the request
        sent it anyway, the exact fault the PR said it closed."""
        start = self.source.index("function renderMgResolution()")
        body = self.source[start:self.source.index("function ", start + 10)]
        hidden = [i for i in range(len(body)) if body.startswith("sec.style.display='none'", i)]
        self.assertEqual(len(hidden), 2)
        for i in hidden:
            with self.subTest(branch=body[i - 40:i]):
                self.assertIn("mgState.resolution=null", body[i:body.index("return", i)])
        click = self.source.index("document.getElementById('mg-generate').addEventListener('click'")
        payload = self.source[click:self.source.index("mgAttachState.path", click)]
        self.assertNotIn("resolution:mgState.resolution,", payload)
        self.assertIn("if(mgState.resolution)config.resolution=mgState.resolution;", payload)

    def test_leaving_a_reference_model_releases_generate(self):
        """Review of #156: switching away from a model that required a
        reference cleared the reference but never re-evaluated the button,
        so it stayed greyed out until the prompt was retyped."""
        start = self.source.index("function renderMgAttach()")
        body = self.source[start:self.source.index("function ", start + 10)]
        no_support = body.index("if(!support){")
        self.assertIn("updateGenerateBtn()", body[no_support:body.index("return;", no_support)])


if __name__ == "__main__":
    unittest.main()
