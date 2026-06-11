#!/usr/bin/env python3
"""Regression test: edited Telegram messages must not reach the handlers.

Run: python3 -m unittest tests.test_edited_message_filter  (from repo root)

Both handle_message and unknown_command read update.message immediately,
and update.message is None on an edited message (PTB delivers edits as
update.edited_message). Without UpdateType.MESSAGE in the registration the
base filters match edits too (they key on effective_message), so editing a
sent message crashed the handler with AttributeError. The fix adds
filters.UpdateType.MESSAGE to both MessageHandler registrations so edits
are dropped before the handler runs.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from telegram import Chat, Message, MessageEntity, Update, User  # noqa: E402
from telegram.ext import filters  # noqa: E402

# The exact filters bot.py registers, kept in sync by the registration
# tests below.
MESSAGE_FILTER = (
    (filters.TEXT | filters.PHOTO | filters.Document.ALL |
     filters.AUDIO | filters.VIDEO | filters.VOICE |
     filters.VIDEO_NOTE | filters.Sticker.ALL) & ~filters.COMMAND
    & filters.UpdateType.MESSAGE
)
UNKNOWN_COMMAND_FILTER = filters.COMMAND & filters.UpdateType.MESSAGE


def _text_message(text: str = "hello") -> Message:
    user = User(id=12345, first_name="Test", is_bot=False)
    chat = Chat(id=12345, type="private")
    entities = []
    if text.startswith("/"):
        # Telegram marks commands with a bot_command entity; filters.COMMAND
        # keys on the entity, not the leading slash.
        entities = [MessageEntity(
            type=MessageEntity.BOT_COMMAND, offset=0,
            length=len(text.split()[0]),
        )]
    return Message(
        message_id=99,
        date=dt.datetime.now(dt.timezone.utc),
        chat=chat,
        from_user=user,
        text=text,
        entities=entities,
    )


class TestEditedMessageFilter(unittest.TestCase):
    def test_new_message_passes(self):
        upd = Update(update_id=1, message=_text_message())
        self.assertTrue(MESSAGE_FILTER.check_update(upd))

    def test_edited_message_dropped(self):
        upd = Update(update_id=1, edited_message=_text_message("edited"))
        self.assertFalse(MESSAGE_FILTER.check_update(upd))
        # The crash precondition the filter now guards against:
        self.assertIsNone(upd.message)

    def test_command_still_excluded_from_message_handler(self):
        upd = Update(update_id=1, message=_text_message("/status"))
        self.assertFalse(MESSAGE_FILTER.check_update(upd))

    def test_unknown_command_passes_new_command(self):
        upd = Update(update_id=1, message=_text_message("/notacommand"))
        self.assertTrue(UNKNOWN_COMMAND_FILTER.check_update(upd))

    def test_unknown_command_drops_edited_command(self):
        upd = Update(update_id=1, edited_message=_text_message("/notacommand"))
        self.assertFalse(UNKNOWN_COMMAND_FILTER.check_update(upd))
        self.assertIsNone(upd.message)

    def test_registrations_include_update_type(self):
        """bot.py's actual registrations must carry UpdateType.MESSAGE."""
        source = (ROOT / "bot.py").read_text()
        handle_msg = re.search(
            r"app\.add_handler\(MessageHandler\(\s*\(filters\.TEXT.*?handle_message",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(handle_msg, "handle_message registration not found")
        self.assertIn("filters.UpdateType.MESSAGE", handle_msg.group(0))

        unknown_cmd = re.search(
            r"app\.add_handler\(MessageHandler\(\s*filters\.COMMAND.*?unknown_command",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(unknown_cmd, "unknown_command registration not found")
        self.assertIn("filters.UpdateType.MESSAGE", unknown_cmd.group(0))


if __name__ == "__main__":
    unittest.main()
