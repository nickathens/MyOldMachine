"""Unit tests for core.llm._interrupted_notice and the salvage-labelling rule.

Run: python3 -m unittest tests.test_interrupted_reply  (from repo root)
  or: python3 tests/test_interrupted_reply.py

The bug this pins: a subprocess turn that dies before its final result leaves
only the narration written along the way. Both CLI providers salvage that
narration on several paths. Some paths labelled it ("[Stopped by /stop
command]", the time-limit trailer, the OOM trailer); the rest returned it bare,
so it arrived looking exactly like a deliberate reply.

Observed on the Linux production bot on 2026-08-15: the service was restarted
under a live turn twice, the child died with exit 143 (128+SIGTERM), and 831
then 1262 characters of raw working notes were delivered as the answer. The
user's report was "your reply is strange. why?" -- a crash that does not
announce itself gets read as the assistant misbehaving.

Two things are tested here:
  1. _interrupted_notice maps every way a signal can be reported to plain
     words (asyncio reaps -> negative returncode; a wrapper reaps -> 128+N).
  2. No salvage return in EITHER CLI provider ships text bare. Structural on
     purpose: it covers salvage paths added later, and it covers the second
     provider, which is exactly the thing a hand-written case list forgets.
"""
from __future__ import annotations

import ast
import signal
import sys
import unittest
from pathlib import Path

# Make the project root importable when tests run from the repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm import _interrupted_notice, _is_oom_exit, _stopped_response  # noqa: E402

LLM_SOURCE = ROOT / "core" / "llm.py"

# Working notes: no greeting, no conclusion, present tense, ends mid-stride.
NARRATION = (
    "Porting the triage change. First I'll load the required context: the "
    "agents protocol, the coding skill, and the exact diff.\n"
    "Now the existing tests and a first look at the triage module."
)


class InterruptedNoticeTests(unittest.TestCase):
    def test_sigterm_both_spellings_give_the_same_reason(self):
        # 143 is what a wrapper reports; -15 is what asyncio reports when it
        # reaps the child itself. Same event, so the same words.
        wrapper = _interrupted_notice(128 + int(signal.SIGTERM))
        asyncio_style = _interrupted_notice(-int(signal.SIGTERM))
        self.assertEqual(wrapper, asyncio_style)
        self.assertIn("stopped or restarted", wrapper)

    def test_sigkill_both_spellings(self):
        for rc in (-int(signal.SIGKILL), 128 + int(signal.SIGKILL)):
            with self.subTest(rc=rc):
                self.assertIn("killed outright", _interrupted_notice(rc))

    def test_sigint_and_sighup(self):
        self.assertIn("interrupted", _interrupted_notice(-int(signal.SIGINT)))
        self.assertIn("lost the session", _interrupted_notice(128 + int(signal.SIGHUP)))

    def test_clean_exit_without_a_result(self):
        # The CLI reached EOF with exit 0 but never emitted a final result.
        # Nothing was signalled, so the notice must not invent one.
        notice = _interrupted_notice(0)
        self.assertIn("ended before finishing", notice)
        self.assertNotIn("exit 0", notice)

    def test_plain_failure_names_its_code(self):
        self.assertIn("exit 1", _interrupted_notice(1))

    def test_unknown_high_code_is_not_forced_into_a_signal(self):
        # 200 is 128+72, and 72 is not a signal we name.
        notice = _interrupted_notice(200)
        self.assertIn("exit 200", notice)
        for reason in ("stopped or restarted", "killed outright", "interrupted"):
            self.assertNotIn(reason, notice)

    def test_none_returncode_does_not_crash(self):
        # process.returncode is None while the child is still alive.
        self.assertIn("ended before finishing", _interrupted_notice(None))

    def test_shape_is_a_separated_trailer(self):
        notice = _interrupted_notice(143)
        self.assertTrue(notice.startswith("\n\n["), notice[:8])
        self.assertTrue(notice.rstrip().endswith("]"))
        self.assertIn("working notes, not an answer", notice)

    def test_salvaged_narration_keeps_every_character(self):
        delivered = NARRATION + _interrupted_notice(143)
        self.assertTrue(delivered.startswith(NARRATION))
        self.assertIn("Unfinished", delivered)
        self.assertIn("Send the message again", delivered)


class OomExitTests(unittest.TestCase):
    """An OOM kill keeps its own dedicated trailer, so it has to stay
    detectable in both spellings; 137 was missed while the check lived inline
    in a 500-line coroutine where nothing could reach it."""

    def test_both_spellings_of_sigkill(self):
        for rc in (-int(signal.SIGKILL), 128 + int(signal.SIGKILL)):
            with self.subTest(rc=rc):
                self.assertTrue(_is_oom_exit(rc, ""))

    def test_stderr_wording_still_counts(self):
        self.assertTrue(_is_oom_exit(1, "fatal: Out Of Memory"))
        self.assertTrue(_is_oom_exit(1, "Killed"))

    def test_sigterm_is_not_an_oom(self):
        # Calling a service restart an OOM sends the user to /clear their
        # context over something that had nothing to do with memory.
        for rc in (-int(signal.SIGTERM), 128 + int(signal.SIGTERM), 0, 1):
            with self.subTest(rc=rc):
                self.assertFalse(_is_oom_exit(rc, ""))

    def test_missing_stderr_does_not_crash(self):
        self.assertFalse(_is_oom_exit(1, None))


class SalvagePathsAreLabelledTests(unittest.TestCase):
    """Every return of salvaged text in either CLI provider must be labelled.

    Structural, and deliberately so. The failure mode is a salvage path that
    forgets its label, which no test of the paths known today can see -- and
    this repo runs TWO subprocess providers with the same shape, so "fixed in
    one, missed in the other" is the likeliest way it comes back.
    """

    PROVIDERS = ("ClaudeCLIProvider", "CodexCLIProvider")
    #: names holding salvaged assistant text inside a provider's complete()
    SALVAGE_NAMES = {"fallback", "fallback_text", "last_turn", "salvage"}
    #: literal markers that count as a label
    LABEL_MARKERS = ("[Stopped", "[Task incomplete", "[Hit ")
    #: helpers that attach a label themselves; each is pinned by a test below,
    #: so the guard trusts behaviour rather than a reassuring function name
    LABELLING_CALLS = {"_interrupted_notice", "_stopped_response"}

    @classmethod
    def setUpClass(cls):
        tree = ast.parse(LLM_SOURCE.read_text(encoding="utf-8"))
        cls.methods = {}
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name in cls.PROVIDERS:
                for fn in node.body:
                    if isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)) and fn.name == "complete":
                        cls.methods[node.name] = fn

    def _is_labelled(self, value: ast.AST) -> bool:
        for sub in ast.walk(value):
            if isinstance(sub, ast.Call) and getattr(sub.func, "id", None) in self.LABELLING_CALLS:
                return True
            if isinstance(sub, ast.Name) and sub.id == "suffix":
                return True  # the error branch builds its trailer into `suffix`
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if any(m in sub.value for m in self.LABEL_MARKERS):
                    return True
            if isinstance(sub, ast.JoinedStr):
                for part in sub.values:
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        if any(m in part.value for m in self.LABEL_MARKERS):
                            return True
        return False

    def _salvage_returns(self, fn):
        for node in ast.walk(fn):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            names = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
            if names & self.SALVAGE_NAMES:
                yield node

    def test_the_labelling_helpers_really_label(self):
        # Without this, adding a name to LABELLING_CALLS would silence the
        # guard for a path that ships bare text -- a check passing for the
        # wrong reason, which is the failure this whole file exists to stop.
        stopped = _stopped_response("half-written notes", "m", "p").text
        self.assertIn("[Stopped", stopped)
        self.assertIn("half-written notes", stopped)
        self.assertIn("[Unfinished", "x" + _interrupted_notice(143))

    def test_both_providers_were_found(self):
        # Guards the guard: a rename would otherwise make every check below
        # pass by inspecting nothing at all.
        self.assertEqual(set(self.methods), set(self.PROVIDERS))

    def test_the_salvage_paths_are_found_in_each(self):
        for name, fn in self.methods.items():
            with self.subTest(provider=name):
                self.assertGreaterEqual(len(list(self._salvage_returns(fn))), 2)

    def test_every_salvage_return_carries_a_label(self):
        for name, fn in self.methods.items():
            with self.subTest(provider=name):
                unlabelled = [n.lineno for n in self._salvage_returns(fn)
                              if not self._is_labelled(n.value)]
                self.assertEqual(
                    unlabelled, [],
                    f"{name}.complete returns salvaged narration with no label at "
                    f"line(s) {unlabelled} - that is the 'strange reply' bug",
                )

    def test_notice_is_wired_in_both_providers(self):
        for name, fn in self.methods.items():
            with self.subTest(provider=name):
                calls = [n for n in ast.walk(fn)
                         if isinstance(n, ast.Call)
                         and getattr(n.func, "id", None) == "_interrupted_notice"]
                self.assertGreaterEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
