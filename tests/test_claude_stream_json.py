"""Unit tests for core.llm._build_claude_stream_json.

This serializer feeds the Claude CLI via ``--input-format stream-json``, which
replaced the flat sentinel-delimited text blob for the Claude provider. The
tests pin the three behaviors the live CLI probes proved are load-bearing:

- ``content`` is ALWAYS a block array, never a bare string. The CLI's stdin
  parser calls ``.some()`` on ``message.content`` and raises
  ``H.message.content.some is not a function`` on a plain string.
- ``type`` and ``message.role`` both carry the turn's role, so assistant-turn
  seeding is ingested as genuine prior context.
- Output is newline-delimited, one valid JSON object per turn, so an embedded
  newline in user content cannot corrupt the record framing.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm import Message, _build_claude_stream_json  # noqa: E402


def _lines(out: str) -> list[str]:
    return [ln for ln in out.split("\n") if ln]


class BuildClaudeStreamJsonTests(unittest.TestCase):
    def test_single_user_turn_shape(self):
        out = _build_claude_stream_json([Message(role="user", content="hello")])
        self.assertEqual(
            json.loads(out),
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "hello"}],
                },
            },
        )

    def test_content_is_always_a_block_array(self):
        # The CLI calls .some() on message.content; a bare string raises a
        # TypeError. Every turn's content must be a list of blocks.
        out = _build_claude_stream_json(
            [Message(role="user", content="hi"), Message(role="assistant", content="yo")]
        )
        for line in _lines(out):
            content = json.loads(line)["message"]["content"]
            self.assertIsInstance(content, list)
            self.assertEqual(content[0]["type"], "text")

    def test_role_maps_to_both_type_and_message_role(self):
        out = _build_claude_stream_json([Message(role="assistant", content="seed")])
        obj = json.loads(out)
        self.assertEqual(obj["type"], "assistant")
        self.assertEqual(obj["message"]["role"], "assistant")

    def test_one_valid_json_object_per_line(self):
        msgs = [
            Message(role="user", content="a"),
            Message(role="assistant", content="b"),
            Message(role="user", content="c"),
        ]
        out = _build_claude_stream_json(msgs)
        lines = _lines(out)
        self.assertEqual(len(lines), len(msgs))
        for line in lines:
            json.loads(line)  # raises if any line is not standalone JSON

    def test_newline_terminated(self):
        out = _build_claude_stream_json([Message(role="user", content="x")])
        self.assertTrue(out.endswith("\n"))

    def test_order_is_preserved(self):
        msgs = [
            Message(role="user", content="first"),
            Message(role="assistant", content="second"),
            Message(role="user", content="third"),
        ]
        out = _build_claude_stream_json(msgs)
        texts = [json.loads(ln)["message"]["content"][0]["text"] for ln in _lines(out)]
        self.assertEqual(texts, ["first", "second", "third"])

    def test_embedded_newline_does_not_break_framing(self):
        # A literal newline in content must be escaped inside the JSON string,
        # not emitted raw -- otherwise it would split one turn into two records
        # for the newline-delimited reader.
        out = _build_claude_stream_json([Message(role="user", content="line1\nline2")])
        self.assertEqual(len(_lines(out)), 1)
        self.assertEqual(out.count("\n"), 1)  # only the trailing terminator
        self.assertEqual(json.loads(out)["message"]["content"][0]["text"], "line1\nline2")

    def test_json_significant_chars_are_escaped(self):
        body = 'quote " backslash \\ brace } bracket ]'
        out = _build_claude_stream_json([Message(role="user", content=body)])
        self.assertEqual(json.loads(out)["message"]["content"][0]["text"], body)

    def test_non_ascii_is_not_escaped(self):
        # ensure_ascii=False keeps real UTF-8 in the payload (smaller, and the
        # CLI consumes UTF-8 directly). The Greek text appears literally.
        greek = "Απάντησε μόνο με τη λέξη: εντάξει"
        out = _build_claude_stream_json([Message(role="user", content=greek)])
        self.assertIn(greek, out)
        self.assertEqual(json.loads(out)["message"]["content"][0]["text"], greek)

    def test_empty_content_still_valid_block(self):
        out = _build_claude_stream_json([Message(role="user", content="")])
        self.assertEqual(
            json.loads(out)["message"]["content"], [{"type": "text", "text": ""}]
        )

    def test_empty_conversation_returns_empty_string(self):
        self.assertEqual(_build_claude_stream_json([]), "")


if __name__ == "__main__":
    unittest.main()
