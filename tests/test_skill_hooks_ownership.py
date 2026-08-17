#!/usr/bin/env python3
"""
Stop-hook process ownership containment.

Regression cover for the 17-18 Aug 2026 incident: skills/watch/hooks.json
declared `"kill_processes": ["whisper", "yt-dlp", "ffmpeg"]`, the Stop handler
matched that by plain substring against EVERY process on the machine, and this
machine hosts several Telegram users behind one OS account. So the end of any
assistant turn, for any user, killed every ffmpeg running anywhere. It took
down a 4K render five times over four hours, and the deaths looked random
because they were not frame counts, they were the moments a reply was sent.

The invariant these tests pin: a Stop hook may only kill processes inside the
stopping session's own subtree, and when that subtree cannot be identified it
kills nothing at all.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.skill_hooks import (  # noqa: E402
    _expand_tmp_pattern,
    _is_agent_process,
    clean_old_temp_files,
    find_processes_by_patterns,
    get_all_pids,
    get_owned_pids,
    get_session_root,
)

BOT = "/opt/homebrew/.../Python /Users/coocooai/MyOldMachine/bot.py"
AGENT = "/Users/coocooai/.local/bin/claude -p --model claude-opus-5 --output-format stream-json"
OTHER_AGENT = "/Users/coocooai/.local/bin/claude -p --model claude-opus-5 --verbose -"

# (pid, ppid, command). Mirrors the shape measured on the live machine:
# launchd -> bot.py -> one claude per user session -> one shell per tool call,
# plus a setsid-detached render that launchd has adopted.
TABLE = [
    (1, 0, "/sbin/launchd"),
    (695, 1, BOT),
    # This session: agent -> shell -> its own ffmpeg.
    (85103, 695, AGENT),
    (85403, 85103, "/bin/zsh -c ffmpeg -i mine.mp4 out.mp4"),
    (85404, 85403, "ffmpeg -i mine.mp4 out.mp4"),
    # Another user's session, live, doing the same kind of work.
    (70000, 695, OTHER_AGENT),
    (70001, 70000, "ffmpeg -i theirs.mp4 theirs.mov"),
    # Nick's detached 4K master render: setsid'd, so launchd is its parent.
    (83910, 1, "/bin/bash .../delphi_grade_2026-08-17/tools/render_all.sh"),
    (85097, 83910, "Python tools/render_seg.py 480 599 out/segments/seg_0480.mov"),
    (85099, 85097, "ffmpeg -v warning -y -f rawvideo -s 3840x2160 -c:v prores_ks out/seg.mov.partial"),
]

HOOK_PID = 85403  # the Stop hook runs under this session's shell


class TestSessionRoot(unittest.TestCase):

    def test_root_is_the_nearest_agent_not_the_bot_or_launchd(self):
        self.assertEqual(get_session_root(TABLE, HOOK_PID), 85103)

    def test_root_of_the_other_session_is_that_session(self):
        self.assertEqual(get_session_root(TABLE, 70001), 70000)

    def test_no_agent_in_the_chain_gives_no_root(self):
        # A cron job or a stray shell: nothing above it is an agent session.
        self.assertIsNone(get_session_root(TABLE, 83910))

    def test_agent_process_detection(self):
        self.assertTrue(_is_agent_process(AGENT))
        self.assertTrue(_is_agent_process("node /usr/local/lib/claude/cli.js -p"))
        self.assertFalse(_is_agent_process(BOT))
        self.assertFalse(_is_agent_process("/sbin/launchd"))
        self.assertFalse(_is_agent_process("ffmpeg -i claude.mp4 out.mp4"))
        self.assertFalse(_is_agent_process(""))


class TestOwnership(unittest.TestCase):

    def test_owned_is_only_this_session_subtree(self):
        self.assertEqual(get_owned_pids(TABLE, HOOK_PID), {85403, 85404})

    def test_detached_render_is_never_owned(self):
        owned = get_owned_pids(TABLE, HOOK_PID)
        for pid in (83910, 85097, 85099):
            self.assertNotIn(pid, owned)

    def test_another_users_live_work_is_never_owned(self):
        self.assertNotIn(70001, get_owned_pids(TABLE, HOOK_PID))

    def test_unprovable_ownership_is_none_not_empty(self):
        # None is the "kill nothing" signal. An empty set would be a legitimate
        # answer meaning "nothing to kill"; callers must be able to tell them
        # apart, because the safe response to not knowing is to do nothing.
        self.assertIsNone(get_owned_pids(TABLE, 83910))


class TestPatternContainment(unittest.TestCase):

    def setUp(self):
        self.all_pids = [(pid, cmd) for pid, _ppid, cmd in TABLE]
        self.owned = get_owned_pids(TABLE, HOOK_PID)

    def test_the_incident_itself(self):
        """The exact watch config, against the exact table, must spare them all."""
        patterns = ["whisper", "yt-dlp", "ffmpeg"]

        unscoped = find_processes_by_patterns(self.all_pids, patterns)
        self.assertIn(85099, unscoped, "table must reproduce the old blast radius")
        self.assertIn(70001, unscoped)

        # Left: this session's own ffmpeg and the shell that launched it.
        contained = find_processes_by_patterns(
            self.all_pids, patterns, owned_pids=self.owned
        )
        self.assertEqual(contained, [85403, 85404])

    def test_containment_holds_for_a_catch_all_pattern(self):
        # Even a pattern that matches literally everything cannot escape.
        matched = find_processes_by_patterns(
            self.all_pids, [""], owned_pids=self.owned
        )
        self.assertEqual(set(matched), self.owned)

    def test_empty_owned_set_kills_nothing(self):
        self.assertEqual(
            find_processes_by_patterns(self.all_pids, ["ffmpeg"], owned_pids=set()), []
        )

    def test_exclude_pids_still_applies(self):
        matched = find_processes_by_patterns(
            self.all_pids, ["ffmpeg"], exclude_pids={85403, 85404},
            owned_pids=self.owned,
        )
        self.assertEqual(matched, [])


class TestTempPatternExpansion(unittest.TestCase):

    def test_tmp_pattern_reaches_the_real_temp_dir(self):
        # macOS mkdtemp honours TMPDIR, so "/tmp/watch-*" matched nothing here
        # and the watch skill never once cleaned up after itself.
        real_tmp = os.path.realpath(tempfile.gettempdir())
        expanded = _expand_tmp_pattern("/tmp/watch-*")
        self.assertIn("/tmp/watch-*", expanded)
        if real_tmp not in ("/tmp", os.path.realpath("/tmp")):
            self.assertIn(os.path.join(real_tmp, "watch-*"), expanded)

    def test_non_tmp_patterns_are_untouched(self):
        self.assertEqual(_expand_tmp_pattern("/var/x_*"), ["/var/x_*"])


class TestInUseScratchIsKept(unittest.TestCase):

    def test_old_scratch_named_by_a_live_process_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            in_use = os.path.join(tmp, "watch-live")
            stale = os.path.join(tmp, "watch-dead")
            for path in (in_use, stale):
                os.mkdir(path)
                old = time.time() - 86400
                os.utime(path, (old, old))

            live = [(4242, f"ffmpeg -i {in_use}/frame_%04d.png out.mp4")]
            cleaned = clean_old_temp_files([os.path.join(tmp, "watch-*")], live)

            self.assertEqual(cleaned, 1)
            self.assertTrue(os.path.isdir(in_use))
            self.assertFalse(os.path.exists(stale))


class TestLiveMachine(unittest.TestCase):
    """Cheap sanity checks against the real process table."""

    def test_get_all_pids_hides_our_own_hook(self):
        pids = {pid for pid, _cmd in get_all_pids()}
        self.assertNotIn(os.getpid(), pids)

    def test_shipped_hooks_never_reap_a_bare_shared_binary(self):
        # Containment makes a broad pattern harmless, but a skill claiming a
        # bare shared binary name is still a bug worth failing on: it says the
        # author believed the reaper was theirs alone.
        import json

        banned = {"ffmpeg", "python", "python3", "node", "bash", "sh", "whisper"}
        skills = Path(__file__).resolve().parent.parent / "skills"
        for hooks_file in skills.glob("*/hooks.json"):
            config = json.loads(hooks_file.read_text(encoding="utf-8"))
            patterns = config.get("stop", {}).get("kill_processes", []) or []
            for pattern in patterns:
                self.assertNotIn(
                    pattern.strip(), banned,
                    f"{hooks_file.parent.name}/hooks.json claims the shared "
                    f"binary '{pattern}'",
                )


if __name__ == "__main__":
    unittest.main()
