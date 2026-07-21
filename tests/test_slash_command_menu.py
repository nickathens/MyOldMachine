#!/usr/bin/env python3
"""Unit tests for the Telegram slash-command menu (the "/" auto-complete list).

Run: python3 -m unittest tests.test_slash_command_menu  (from repo root)

The bot publishes SLASH_COMMAND_MENU to Telegram in post_init via
publish_slash_commands(). Two things can silently break it and neither shows
up at runtime until a user notices:

  1. Drift: a menu entry names a command the bot no longer handles (or never
     did). Telegram happily advertises it; tapping it does nothing. The
     every-command-is-registered test below ties the menu to _RESERVED_COMMANDS
     so a rename or removal of a handler fails the suite instead of shipping a
     dead menu entry.

  2. Scope shadowing: Telegram resolves a chat's menu most-specific-scope
     first, and a private DM reads all_private_chats BEFORE default. Publishing
     to only one scope lets a stale list in the other silently win. The
     both-scopes test locks that publish_slash_commands writes both.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot as botmod  # noqa: E402
from bot import SLASH_COMMAND_MENU, publish_slash_commands  # noqa: E402
from telegram import BotCommand  # noqa: E402

# Telegram Bot API limits for setMyCommands entries.
_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_DESC_MIN, _DESC_MAX = 1, 256
_MENU_MAX = 100  # Telegram rejects more than 100 commands per scope.

# Provider brand tokens that must not leak into a shipped, provider-neutral
# menu (this bot runs any of a dozen LLM backends).
_BRAND_TOKENS = (
    "claude", "anthropic", "gpt", "openai", "gemini", "grok",
    "kimi", "minimax", "deepseek", "ollama", "llama", "openrouter",
)
# Glyphs banned from shipped copy: em dash, en dash, angle brackets.
_BANNED_GLYPHS = ("—", "–", "<", ">")


class SlashCommandMenuTests(unittest.TestCase):
    def test_menu_is_nonempty_and_within_telegram_cap(self):
        self.assertGreater(len(SLASH_COMMAND_MENU), 0)
        self.assertLessEqual(len(SLASH_COMMAND_MENU), _MENU_MAX)

    def test_every_menu_command_is_registered(self):
        # The load-bearing invariant: the menu can only advertise real,
        # handled commands. _RESERVED_COMMANDS is kept in sync with the
        # CommandHandler registrations in main().
        for name, _ in SLASH_COMMAND_MENU:
            self.assertIn(
                name,
                botmod._RESERVED_COMMANDS,
                f"/{name} is in the slash menu but is not a registered command",
            )

    def test_names_valid_and_unique(self):
        names = [name for name, _ in SLASH_COMMAND_MENU]
        for name in names:
            self.assertRegex(name, _NAME_RE, f"/{name} is not a valid command name")
        self.assertEqual(len(names), len(set(names)), "duplicate command in menu")

    def test_descriptions_within_limits(self):
        for name, desc in SLASH_COMMAND_MENU:
            self.assertTrue(desc.strip(), f"/{name} has an empty description")
            self.assertGreaterEqual(len(desc), _DESC_MIN)
            self.assertLessEqual(len(desc), _DESC_MAX, f"/{name} description too long")

    def test_descriptions_provider_neutral_and_glyph_clean(self):
        for name, desc in SLASH_COMMAND_MENU:
            low = desc.lower()
            for token in _BRAND_TOKENS:
                self.assertNotIn(
                    token, low, f"/{name} description leaks a provider brand: {token}"
                )
            for glyph in _BANNED_GLYPHS:
                self.assertNotIn(
                    glyph, desc, f"/{name} description contains a banned glyph"
                )


class PublishSlashCommandsTests(unittest.IsolatedAsyncioTestCase):
    async def test_writes_both_scopes(self):
        bot = AsyncMock()
        ok = await publish_slash_commands(bot)

        self.assertTrue(ok)
        self.assertEqual(bot.set_my_commands.await_count, 2)

        scopes = set()
        for call in bot.set_my_commands.await_args_list:
            menu = call.args[0]
            scope = call.kwargs["scope"]
            scopes.add(scope.type)
            # Each scope gets the full menu as BotCommand objects, in order.
            self.assertEqual(len(menu), len(SLASH_COMMAND_MENU))
            self.assertTrue(all(isinstance(c, BotCommand) for c in menu))
            self.assertEqual(
                [c.command for c in menu],
                [name for name, _ in SLASH_COMMAND_MENU],
            )
        # Both the DM-facing scope and the fallback default must be written,
        # or a stale menu in the unwritten scope can shadow this one.
        self.assertEqual(scopes, {"default", "all_private_chats"})

    async def test_swallows_telegram_error(self):
        # A rejected menu update must never propagate out of startup.
        bot = AsyncMock()
        bot.set_my_commands.side_effect = RuntimeError("Telegram rejected")
        ok = await publish_slash_commands(bot)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
