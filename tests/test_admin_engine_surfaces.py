"""Two rules that only hold if every surface agrees, so every surface is here.

1. **An administrator picks the MACHINE's engine, not one of their own.**
   A stored engine would sit below the provider, model and effort they set
   and quietly outrank all three: a second setting, underneath the first,
   winning. So the picker they are offered is the machine setting itself,
   and it carries every engine this install can actually run rather than the
   two curated rows a non-admin chooses between. Pressing one writes
   LLM_PROVIDER and LLM_MODEL, the same pair /provider and /model write.
   Clearing an older personal pick stays open, because a pick made before
   this rule existed is theirs to delete.

2. **Usage is everybody's.** It used to sit *inside* the picker's section,
   which made it collateral of rule 1 and, before that, collateral of a
   machine with no engine installed: two ways for a person to lose the only
   view of what they spent. It is its own section now, and nothing hides it.

The page tests read the real markup rather than a rendered DOM, because
nothing in this repo runs a browser. A structural parse is the closest
honest instrument: it can answer "is the usage panel inside the engine
section", which a string search cannot.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

import bot  # noqa: E402
from core import engines, user_prefs, users  # noqa: E402

PAGE = ROOT / "miniapp" / "static" / "index.html"


def _update(user_id: int, text: str = ""):
    """The two attributes these handlers touch, and a reply we can read."""
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id),
                           message=message)


def _said(update) -> str:
    return "\n".join(call.args[0] for call
                     in update.message.reply_text.call_args_list)


class _Tree(HTMLParser):
    """Which element each id sits inside.

    Void elements never close, so they are never pushed; an unclosed tag
    unwinds to its own name rather than corrupting everything after it.
    """

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, markup: str):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str]] = []
        self.ancestors: dict[str, tuple[str, ...]] = {}
        self.attrs: dict[str, dict] = {}
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        found = dict(attrs)
        node_id = found.get("id", "")
        if node_id:
            self.ancestors[node_id] = tuple(i for _t, i in self.stack if i)
            self.attrs[node_id] = found
        if tag not in self.VOID:
            self.stack.append((tag, node_id))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


class _BotSurface(unittest.TestCase):
    """Drives the real handlers with the preference store in a temp dir."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mom-admin-engine-"))
        self._saved = users.USERS_DATA_DIR
        users.USERS_DATA_DIR = self.tmp
        engines.probe_cache_clear()
        for target, value in (("bot.get_allowed_users", {7}),
                              ("bot.get_llm_provider", "claude"),
                              ("bot.get_llm_model", "claude-opus-5"),
                              ("core.config.get_llm_effort", "max")):
            p = patch(target, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        users.USERS_DATA_DIR = self._saved
        engines.probe_cache_clear()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class AdminEngineCommandTests(_BotSurface):
    """/engine, asked by an administrator: the machine, with every option."""

    def setUp(self):
        super().setUp()
        # engine_command rebuilds the module-level provider on a switch, and
        # a bare stub left in that global outlives this file: later modules
        # read bot._llm_provider and ask it whether it supports tool use.
        self.addCleanup(setattr, bot, "_llm_provider_spec", bot._llm_provider_spec)
        self._saved_provider = bot._llm_provider
        self.addCleanup(setattr, bot, "_llm_provider", self._saved_provider)

    @staticmethod
    def _stub_provider():
        return SimpleNamespace(supports_tool_use=True, last_health=(True, "ok"),
                               provider_name="claude", model="claude-sonnet-5")

    def _run(self, text: str = ""):
        """/engine as an admin, with both CLIs answering as installed."""
        update = _update(7, text)
        with (patch("bot.is_admin", return_value=True),
              patch("core.engines._login_status", return_value=(True, "")),
              patch("core.engines._cli_version_text",
                    return_value="codex-cli 0.154.0")):
            asyncio.run(bot.engine_command(update, None))
        return update

    def test_engine_shows_the_machine_setting_and_every_option(self):
        said = _said(self._run())
        self.assertIn("the machine setting", said)
        self.assertIn("claude-opus-5", said)
        self.assertIn("max effort", said)
        # The complaint that produced this: a machine that runs ten engines
        # offering two. Every model the install catalog carries for a
        # subscription CLI is named here.
        for label in ("Claude Sonnet 5", "Claude Fable 5.1", "GPT-6 Astra",
                      "GPT-5.6 Sol", "GPT-5.3 Codex Spark"):
            with self.subTest(label=label):
                self.assertIn(label, said)
        self.assertIn("(running now)", said)
        self.assertIn("/engine sonnet", said)

    def test_the_running_engine_is_not_offered_as_a_switch(self):
        said = _said(self._run())
        self.assertNotIn("/engine opus", said)

    def test_an_admin_naming_an_engine_moves_the_machine_not_a_preference(self):
        written = []
        health = AsyncMock(return_value=(True, "ok"))
        with (patch("bot._write_machine_llm",
                    side_effect=lambda p, m: written.append((p, m)) or True),
              patch("bot._build_llm_provider", return_value=self._stub_provider()),
              patch("bot._refresh_provider_health", health)):
            said = _said(self._run("/engine sonnet"))
        self.assertEqual(written, [("claude-cli", "claude-sonnet-5")])
        self.assertEqual(engines.user_engine_id(7), "")
        self.assertIn("Claude Sonnet 5", said)
        self.assertIn("Ordinary users keep their personal engine or available Opus default", said)

    def test_a_health_check_failure_is_reported_not_swallowed(self):
        health = AsyncMock(return_value=(False, "no login"))
        with (patch("bot._write_machine_llm", return_value=True),
              patch("bot._build_llm_provider", return_value=self._stub_provider()),
              patch("bot._refresh_provider_health", health)):
            said = _said(self._run("/engine astra"))
        self.assertIn("Health-check FAILED", said)

    def test_switching_to_what_is_already_running_writes_nothing(self):
        with patch("bot._write_machine_llm") as write:
            said = _said(self._run("/engine opus"))
        write.assert_not_called()
        self.assertIn("already", said)

    def test_an_engine_this_machine_cannot_run_is_refused_at_the_press(self):
        update = _update(7, "/engine astra")
        with (patch("bot.is_admin", return_value=True),
              patch("core.engines._login_status", return_value=(True, "")),
              patch("core.engines._cli_version_text",
                    return_value="codex-cli 0.152.0"),
              patch("bot._write_machine_llm") as write):
            asyncio.run(bot.engine_command(update, None))
        write.assert_not_called()
        self.assertIn("0.153.1", _said(update))

    def test_a_name_that_is_not_an_engine_lists_the_ones_that_are(self):
        with patch("bot._write_machine_llm") as write:
            said = _said(self._run("/engine banana"))
        write.assert_not_called()
        self.assertIn("sonnet", said)
        self.assertEqual(engines.user_engine_id(7), "")

    def test_an_admin_can_still_clear_a_pick_made_before_the_rule(self):
        user_prefs.set_pref(7, "engine", "astra")
        update = _update(7, "/engine default")
        with patch("bot.is_admin", return_value=True):
            asyncio.run(bot.engine_command(update, None))
        self.assertEqual(engines.user_engine_id(7), "")
        self.assertIn("Cleared", _said(update))

    def test_an_inert_pick_from_before_the_rule_is_named_not_hidden(self):
        # The fault that started this: two settings on screen and no way to
        # tell which one won. A stored pick that no longer does anything is
        # still a thing they can see elsewhere, so /engine says so.
        user_prefs.set_pref(7, "engine", "astra")
        said = _said(self._run())
        self.assertIn("Astra", said)
        self.assertIn("not used", said)
        self.assertIn("/engine default", said)

    def test_no_such_line_when_nothing_is_stored(self):
        self.assertNotIn("also stored", _said(self._run()))

    def test_an_ordinary_user_still_gets_their_own_two(self):
        update = _update(7)
        with (patch("bot.is_admin", return_value=False),
              patch("core.engines._login_status", return_value=(True, "")),
              patch("core.engines._cli_version_text",
                    return_value="codex-cli 0.154.0")):
            asyncio.run(bot.engine_command(update, None))
        said = _said(update)
        self.assertIn("Switch with /engine", said)
        self.assertNotIn("the machine setting.", said)
        self.assertNotIn("GPT-5.6 Sol", said)


class MachineEnvWriteTests(unittest.TestCase):
    """One writer for three commands, because they set the same two keys.

    /provider, /model and /engine each carried a copy of this loop, and a
    copy is how a key gets replaced in one path and appended in another.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mom-env-write-"))
        self.env = self.tmp / ".env"
        self.saved = {k: os.environ.get(k) for k in ("LLM_PROVIDER", "LLM_MODEL")}
        self.addCleanup(self._restore)

    def _restore(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_both_keys_are_replaced_and_everything_else_survives(self):
        self.env.write_text("# top\nLLM_PROVIDER=claude\nLLM_MODEL=claude-opus-5\n"
                            "LLM_EFFORT=max\nOTHER=keep\n", encoding="utf-8")
        self.assertTrue(bot._write_machine_llm("codex", "gpt-6-astra", self.env))
        text = self.env.read_text(encoding="utf-8")
        self.assertIn("LLM_PROVIDER=codex", text)
        self.assertIn("LLM_MODEL=gpt-6-astra", text)
        self.assertIn("LLM_EFFORT=max", text)
        self.assertIn("OTHER=keep", text)
        self.assertIn("# top", text)
        self.assertEqual(text.count("LLM_PROVIDER="), 1)
        self.assertEqual(text.count("LLM_MODEL="), 1)

    def test_a_missing_key_is_appended_once(self):
        self.env.write_text("OTHER=keep\n", encoding="utf-8")
        bot._write_machine_llm("codex", "gpt-5.5", self.env)
        text = self.env.read_text(encoding="utf-8")
        self.assertEqual(text.count("LLM_PROVIDER=codex"), 1)
        self.assertEqual(text.count("LLM_MODEL=gpt-5.5"), 1)

    def test_the_live_process_sees_the_new_pair_without_a_restart(self):
        self.env.write_text("LLM_PROVIDER=claude\nLLM_MODEL=claude-opus-5\n",
                            encoding="utf-8")
        bot._write_machine_llm("codex", "gpt-5.5", self.env)
        self.assertEqual(os.environ["LLM_PROVIDER"], "codex")
        self.assertEqual(os.environ["LLM_MODEL"], "gpt-5.5")

    def test_no_env_file_is_false_rather_than_a_new_one(self):
        self.assertFalse(bot._write_machine_llm("codex", "gpt-5.5", self.env))
        self.assertFalse(self.env.exists())


class HelpTextTests(_BotSurface):
    """The guide is one text for everybody; this line is not true for
    everybody, and a line telling an admin to pick is the same wrong
    sentence the picker itself was."""

    def _help(self, admin: bool) -> str:
        update = _update(7)
        with patch("bot.is_admin", return_value=admin):
            asyncio.run(bot.help_command(update, None))
        return _said(update)

    def test_an_admin_is_told_what_they_actually_set(self):
        said = self._help(True)
        self.assertIn("/engine — Set the engine this machine runs on", said)
        self.assertNotIn("Pick which AI answers you", said)

    def test_an_ordinary_user_is(self):
        self.assertIn("/engine — Pick which AI answers you", self._help(False))

    def test_usage_is_offered_to_both(self):
        for admin in (True, False):
            with self.subTest(admin=admin):
                self.assertIn("/usage — What you used", self._help(admin))


class PageStructureTests(unittest.TestCase):
    """Where usage sits in the markup, which is the whole of rule 2."""

    @classmethod
    def setUpClass(cls):
        cls.source = PAGE.read_text(encoding="utf-8")
        cls.tree = _Tree(cls.source)

    def test_the_parse_found_the_sections_at_all(self):
        # A parser that silently found nothing would pass every test below.
        for node in ("engine-section", "usage-section", "usage-btn",
                     "usage-panel", "engine-selector"):
            with self.subTest(node=node):
                self.assertIn(node, self.tree.ancestors)
        self.assertIn("engine-section", self.tree.ancestors["engine-selector"])

    def test_usage_is_not_inside_the_engine_picker(self):
        for node in ("usage-btn", "usage-panel"):
            with self.subTest(node=node):
                self.assertNotIn("engine-section", self.tree.ancestors[node])
                self.assertIn("usage-section", self.tree.ancestors[node])

    def test_the_usage_section_is_visible_from_the_start(self):
        style = self.tree.attrs["usage-section"].get("style", "")
        self.assertNotIn("display:none", style.replace(" ", ""))
        # The control: the picker IS hidden until the payload says otherwise,
        # so this test can tell a hidden section from a shown one.
        engine_style = self.tree.attrs["engine-section"].get("style", "")
        self.assertIn("display:none", engine_style.replace(" ", ""))

    def test_no_script_on_the_page_can_hide_usage(self):
        # Being outside the picker is worth nothing if a later line hides the
        # section by id. Nothing reaches for it, so nothing can.
        self.assertNotIn("getElementById('usage-section')", self.source)
        self.assertNotIn('getElementById("usage-section")', self.source)

    def test_the_page_renders_the_machine_picker_on_the_payload_flag(self):
        # The flag in the payload is worthless if the front end ignores it,
        # and nothing here runs a browser, so the line itself is the guard.
        self.assertIn("if(data.machine){renderMachineEngine(data);return}",
                      self.source)
        # And the picker is no longer hidden from an admin: the whole point
        # is that they have every option, not none.
        self.assertNotIn("if(data.admin){section.style.display='none';return}",
                         self.source)

    def test_the_live_machine_engine_is_not_a_button(self):
        # A machine setting has no "clear" the way a personal pick does.
        # Tapping the lit row must not blank what the install runs on.
        self.assertIn("if(e.available&&!e.current)btn.onclick=", self.source)

    def test_the_rows_above_are_reread_after_a_machine_switch(self):
        # Provider, Model and Effort are views of the value the press just
        # wrote. Two views of one setting may never disagree on screen.
        body = self.source.split("function setEngine(id){")[1]
        body = body.split("\n    function ")[0]
        self.assertIn("if(data.machine){", body)
        self.assertIn("loadStatus()", body)
        self.assertNotIn("showRestartHint()", body)


if __name__ == "__main__":
    unittest.main()
