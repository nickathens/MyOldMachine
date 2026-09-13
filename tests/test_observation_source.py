"""Where an observation says it came from, and when it says nothing.

The basis on an observation is only worth something if the reference beside it
is real. The rule here is narrow on purpose: the prompt offers a source only
for the message it is being built for, matched by digest, and offers nothing at
all otherwise. A nearby entry from the history would put a checkable-looking
reference on words it never covered, which is worse than no source at all.
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

import bot as botmod  # noqa: E402
from core import users as users_mod  # noqa: E402
from core.memory import MemoryManager  # noqa: E402

USER = 111111111
MESSAGE = "please make the lenses a bit more lime"


class PendingRecordFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data = Path(self._tmp.name) / "data"
        self.users_dir = self.data / "users"
        self.users_dir.mkdir(parents=True)
        self.mm = MemoryManager(self.data)
        for p in (
            patch.object(botmod, "_memory_manager", self.mm),
            patch.object(botmod, "DATA_DIR", self.data),
            patch.object(botmod, "USERS_DIR", self.users_dir),
            patch.object(users_mod, "USERS_DATA_DIR", self.users_dir),
            patch.object(botmod, "get_allowed_users", lambda uid=None: [USER]),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def record(self):
        return json.loads(botmod._pending_message_path(USER).read_text(encoding="utf-8"))


class ObservationSourceTests(PendingRecordFixture):
    def test_the_message_being_handled_gets_a_reference(self):
        botmod.save_pending_message(USER, MESSAGE, 24663)

        self.assertEqual(botmod._observation_source(USER, MESSAGE),
                         f"telegram:{USER}:24663")

    def test_a_different_message_gets_nothing(self):
        botmod.save_pending_message(USER, MESSAGE, 24663)

        self.assertEqual(botmod._observation_source(USER, "something else"), "")

    def test_a_long_message_is_matched_by_its_digest(self):
        long_message = "a long message " * 200
        botmod.save_pending_message(USER, long_message, 24663)

        self.assertGreater(len(long_message), 500)
        self.assertEqual(botmod._observation_source(USER, long_message),
                         f"telegram:{USER}:24663")
        self.assertEqual(botmod._observation_source(USER, long_message[:400]), "")

    def test_no_record_means_no_reference(self):
        self.assertEqual(botmod._observation_source(USER, MESSAGE), "")

    def test_a_record_without_a_usable_id_is_refused(self):
        botmod.save_pending_message(USER, MESSAGE, 24663)
        path = botmod._pending_message_path(USER)
        for broken in ({"message_id": 0}, {"message_id": "24663"},
                       {"message_id": None}, {"user_id": 999}, {"chat_id": 0}):
            with self.subTest(broken=broken):
                record = self.record()
                record.update(broken)
                path.write_text(json.dumps(record), encoding="utf-8")
                self.assertEqual(botmod._observation_source(USER, MESSAGE), "")

    def test_a_corrupt_record_is_not_an_error(self):
        botmod.save_pending_message(USER, MESSAGE, 24663)
        botmod._pending_message_path(USER).write_text("{not json", encoding="utf-8")

        self.assertEqual(botmod._observation_source(USER, MESSAGE), "")

    def test_the_recovery_record_keeps_its_existing_fields(self):
        botmod.save_pending_message(USER, MESSAGE, 24663)
        record = self.record()

        self.assertEqual(record["text"], MESSAGE)
        self.assertEqual(record["user_id"], USER)
        self.assertEqual(record["message_id"], 24663)
        self.assertIn("received", record)


class PromptOffersTheSourceTests(PendingRecordFixture):
    """The block only exists for a provider that can run the CLI at all."""

    def setUp(self):
        super().setUp()
        provider = types.SimpleNamespace(supports_tool_use=True, supports_mcp=False,
                                         provider_name="stub")
        p = patch.object(botmod, "_llm_provider", provider)
        p.start()
        self.addCleanup(p.stop)

    def test_the_prompt_names_the_source_of_this_turn(self):
        botmod.save_pending_message(USER, MESSAGE, 24663)

        prompt = botmod.build_system_prompt(USER, new_message=MESSAGE)

        self.assertIn(f"Current user message source for observations: telegram:{USER}:24663",
                      prompt)
        self.assertIn("--basis", prompt)
        self.assertIn("--quote", prompt)

    def test_a_prompt_built_without_the_message_offers_no_source(self):
        botmod.save_pending_message(USER, MESSAGE, 24663)

        prompt = botmod.build_system_prompt(USER)

        self.assertNotIn("Current user message source", prompt)
        self.assertIn("--basis", prompt)  # control: the block itself is present

    def test_a_stale_record_offers_no_source(self):
        botmod.save_pending_message(USER, "an older message", 24000)

        prompt = botmod.build_system_prompt(USER, new_message=MESSAGE)

        self.assertNotIn("Current user message source", prompt)

    def test_the_instructions_forbid_inventing_evidence(self):
        prompt = botmod.build_system_prompt(USER, new_message=MESSAGE)

        self.assertIn("Never invent a source", prompt)
        self.assertIn("anything you worked out yourself is inferred", prompt.lower())


class TheChatTheMessageArrivedInTests(PendingRecordFixture):
    """A reference names a chat, and the chat is not always the user.

    `chat_id` was written as the user's own id, which is only ever right in a
    private chat. The field exists so a reference can survive a chat that is
    not the private one, and writing the user into it means it cannot.
    """

    GROUP = -1001234567890

    def test_the_chat_the_message_arrived_in_is_recorded(self):
        botmod.save_pending_message(USER, MESSAGE, 24663, self.GROUP)

        self.assertEqual(self.record()["chat_id"], self.GROUP)
        self.assertEqual(botmod._observation_source(USER, MESSAGE),
                         f"telegram:{self.GROUP}:24663")

    def test_a_caller_that_names_no_chat_still_records_the_private_one(self):
        botmod.save_pending_message(USER, MESSAGE, 24663)

        self.assertEqual(self.record()["chat_id"], USER)

    def test_an_unusable_chat_falls_back_to_the_private_one(self):
        # True is not an int here on purpose: bool passes isinstance and would
        # write `telegram:True:24663` into the record.
        for broken in (0, None, "-1001234567890", True, 12.0):
            with self.subTest(chat_id=broken):
                botmod.save_pending_message(USER, MESSAGE, 24663, broken)
                self.assertEqual(self.record()["chat_id"], USER)

    def test_the_single_message_handler_passes_the_chat_it_arrived_in(self):
        # A source pin: _process_single_inner reaches the media-gen queue, the
        # voice gate and a provider probe before this line, so the wiring is
        # pinned here and the behaviour is run in the album test below.
        self.assertIn("save_pending_message(user_id, user_message, "
                      "update.message.message_id, update.message.chat_id)",
                      (ROOT / "bot.py").read_text(encoding="utf-8"))


class _FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id
        self.sent = []

    async def send_action(self, *_a, **_k):
        return None

    async def send_message(self, text, **_k):
        self.sent.append(text)
        return None


def _album_update(chat, message_id, caption=""):
    message = types.SimpleNamespace(message_id=message_id, text=None,
                                    caption=caption or None, chat=chat,
                                    chat_id=chat.id)
    return types.SimpleNamespace(message=message,
                                 effective_user=types.SimpleNamespace(id=USER))


class AnAlbumIsQuotedAgainstTheMessageThatCarriedItTests(
        PendingRecordFixture, unittest.IsolatedAsyncioTestCase):
    """The caption can ride on the second photo, and usually does not.

    The reference was taken from the first update in the batch while the words
    were read from whichever update carried them, so a quote could be filed
    against a photo that never held it. The turn is the same either way, so
    the reference was not false, only pointing at the wrong message in it.
    """

    GROUP = -1001234567890

    def setUp(self):
        super().setUp()
        self.saved = []

        async def no_attachments(*_a, **_k):
            return []

        async def answer(*_a, **_k):
            return "done"

        async def delivered(*_a, **_k):
            return True

        def save_and_send(user_id, user_message, response, **kwargs):
            self.saved.append((user_message, kwargs.get("message_id")))
            return response

        for p in (
            patch.object(botmod, "download_attachments", no_attachments),
            patch.object(botmod, "call_llm", answer),
            patch.object(botmod, "send_chunks_with_retry", delivered),
            patch.object(botmod, "_save_and_send", save_and_send),
            # The marker is cleared in a finally block; the record is the
            # artifact under test, so it is read before it goes.
            patch.object(botmod, "clear_pending_message", lambda *_a, **_k: None),
            patch.object(botmod, "get_session",
                         lambda *_a, **_k: types.SimpleNamespace(
                             should_daily_reset=lambda: False)),
        ):
            p.start()
            self.addCleanup(p.stop)

    async def test_the_reference_names_the_photo_the_caption_came_on(self):
        chat = _FakeChat(self.GROUP)
        updates = [_album_update(chat, 900), _album_update(chat, 901, MESSAGE),
                   _album_update(chat, 902)]

        await botmod._process_media_group_inner(updates, USER, context=None)

        record = self.record()
        self.assertEqual(record["message_id"], 901)
        self.assertEqual(record["chat_id"], self.GROUP)
        sent, logged = self.saved[0]
        self.assertEqual(logged, 901, "the log entry names the same message")
        self.assertEqual(botmod._observation_source(USER, sent),
                         f"telegram:{self.GROUP}:901")

    async def test_an_album_with_no_caption_still_names_its_first_message(self):
        chat = _FakeChat(self.GROUP)

        async def one_photo(*_a, **_k):
            return [(Path("/tmp/p.jpg"), "image")]

        with patch.object(botmod, "download_attachments", one_photo):
            await botmod._process_media_group_inner(
                [_album_update(chat, 900), _album_update(chat, 901)], USER,
                context=None)

        self.assertEqual(self.record()["message_id"], 900)


if __name__ == "__main__":
    unittest.main()
