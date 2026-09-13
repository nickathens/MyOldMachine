"""Two rules that only hold if every surface agrees, so every surface is here.

1. **An administrator has no engine to pick.** They set the machine's
   provider, model and effort, and a stored engine would quietly outrank all
   three: a second setting, below the first one, winning. So /engine answers
   them with the machine setting, the Mini App hides the picker, and both the
   endpoint and the store refuse a write. Clearing stays open, because a pick
   made before this rule existed is theirs to delete.

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
    def test_engine_answers_an_admin_with_the_machine_setting(self):
        update = _update(7)
        with patch("bot.is_admin", return_value=True):
            asyncio.run(bot.engine_command(update, None))
        said = _said(update)
        self.assertIn("machine setting", said)
        self.assertIn("claude-opus-5", said)
        self.assertIn("max effort", said)
        # No list of buttons to press, and no instruction to press one.
        self.assertNotIn("/engine opus", said)

    def test_an_admin_naming_an_engine_stores_nothing(self):
        update = _update(7, "/engine astra")
        with patch("bot.is_admin", return_value=True):
            asyncio.run(bot.engine_command(update, None))
        self.assertEqual(engines.user_engine_id(7), "")
        self.assertIn("machine setting", _said(update))

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
        update = _update(7)
        with patch("bot.is_admin", return_value=True):
            asyncio.run(bot.engine_command(update, None))
        said = _said(update)
        self.assertIn("Astra", said)
        self.assertIn("not used", said)
        self.assertIn("/engine default", said)

    def test_no_such_line_when_nothing_is_stored(self):
        update = _update(7)
        with patch("bot.is_admin", return_value=True):
            asyncio.run(bot.engine_command(update, None))
        self.assertNotIn("still stored", _said(update))

    def test_an_ordinary_user_still_gets_the_picker(self):
        update = _update(7)
        with (patch("bot.is_admin", return_value=False),
              patch("core.engines._login_status", return_value=(True, "")),
              patch("core.engines._cli_version_text",
                    return_value="codex-cli 0.154.0")):
            asyncio.run(bot.engine_command(update, None))
        said = _said(update)
        self.assertIn("Switch with /engine", said)
        self.assertNotIn("machine setting (", said)


class HelpTextTests(_BotSurface):
    """The guide is one text for everybody; this line is not true for
    everybody, and a line telling an admin to pick is the same wrong
    sentence the picker itself was."""

    def _help(self, admin: bool) -> str:
        update = _update(7)
        with patch("bot.is_admin", return_value=admin):
            asyncio.run(bot.help_command(update, None))
        return _said(update)

    def test_an_admin_is_not_told_to_pick(self):
        said = self._help(True)
        self.assertIn("/engine — See the engine setting", said)
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

    def test_the_page_hides_the_picker_on_the_admin_flag(self):
        # The flag in the payload is worthless if the front end ignores it,
        # and nothing here runs a browser, so the line itself is the guard.
        self.assertIn("if(data.admin){section.style.display='none';return}",
                      self.source)


if __name__ == "__main__":
    unittest.main()
