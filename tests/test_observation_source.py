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


if __name__ == "__main__":
    unittest.main()
