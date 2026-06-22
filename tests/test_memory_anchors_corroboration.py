"""Tests for the ported memory improvements: anchors, corroboration, semantic dedup.

Covers (MemoryManager-level, no live LLM or embedding model required):
- Anchors: add/update/remove/promote, slug uniqueness, whitespace collapse,
  render formatting, parse, and drift-proof injection in build_memory_context
  (full + lite mode, never truncated).
- Corroboration (lexical): near-restatement suppresses the new line and
  strengthens the existing one; distinct observations are saved normally.
- Corroboration (semantic): mocked backend links a near-duplicate as a
  corroboration, keeping both lines; parse-miss guard never bumps on a bad line.
- parse_observation: [seen:N] parsing; [lastseen]/[corrob] tags consumed cleanly.
- Helpers: _set_tag, _get_int_tag, _find_lexical_match, and the whole-line
  _rewrite_observation_line prefix-collision regression.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.memory import (  # noqa: E402
    MemoryManager,
    render_anchors_section,
    parse_anchor_line,
    _find_lexical_match,
    _set_tag,
    _get_int_tag,
)


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mm = MemoryManager(Path(self._tmp.name))
        self.uid = 12345


# ─────────────────────────── Anchors: storage ────────────────────────────

class AnchorStorageTests(_Base):
    def test_add_and_load_roundtrip(self):
        r = self.mm.add_anchor(self.uid, "Never use em dashes", anchor_id="no-dash", category="aesthetic")
        self.assertEqual(r["status"], "added")
        self.assertEqual(r["id"], "no-dash")
        anchors = self.mm.load_anchors(self.uid)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["id"], "no-dash")
        self.assertEqual(anchors[0]["category"], "aesthetic")
        self.assertEqual(anchors[0]["text"], "Never use em dashes")

    def test_add_is_idempotent_by_id(self):
        self.mm.add_anchor(self.uid, "first text", anchor_id="x")
        r = self.mm.add_anchor(self.uid, "second text", anchor_id="x", category="identity")
        self.assertEqual(r["status"], "updated")
        self.assertEqual(r["total"], 1)
        anchors = self.mm.load_anchors(self.uid)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["text"], "second text")
        self.assertEqual(anchors[0]["category"], "identity")

    def test_auto_slug_uniqueness(self):
        # Two adds with identical text derive the same base slug; the second must
        # get a -2 suffix rather than collide (and silently overwrite) the first.
        r1 = self.mm.add_anchor(self.uid, "example anchored fact")
        r2 = self.mm.add_anchor(self.uid, "example anchored fact")
        self.assertNotEqual(r1["id"], r2["id"])
        self.assertTrue(r2["id"].endswith("-2"))
        self.assertEqual(len(self.mm.load_anchors(self.uid)), 2)

    def test_whitespace_collapse(self):
        self.mm.add_anchor(self.uid, "line one\nline two\t  spaced", anchor_id="multi")
        a = self.mm.load_anchors(self.uid)[0]
        self.assertNotIn("\n", a["text"])
        self.assertEqual(a["text"], "line one line two spaced")

    def test_empty_text_is_error(self):
        r = self.mm.add_anchor(self.uid, "   \n  ", anchor_id="blank")
        self.assertEqual(r["status"], "error")
        self.assertEqual(self.mm.load_anchors(self.uid), [])

    def test_remove(self):
        self.mm.add_anchor(self.uid, "to keep", anchor_id="keep")
        self.mm.add_anchor(self.uid, "to drop", anchor_id="drop")
        r = self.mm.remove_anchor(self.uid, "drop")
        self.assertEqual(r["status"], "removed")
        ids = [a["id"] for a in self.mm.load_anchors(self.uid)]
        self.assertEqual(ids, ["keep"])

    def test_remove_missing(self):
        r = self.mm.remove_anchor(self.uid, "nope")
        self.assertEqual(r["status"], "not_found")

    def test_anchors_are_per_user(self):
        self.mm.add_anchor(self.uid, "mine", anchor_id="m")
        self.assertEqual(self.mm.load_anchors(999), [])

    def test_load_when_none(self):
        self.assertEqual(self.mm.load_anchors(self.uid), [])


# ─────────────────────────── Anchors: promote ────────────────────────────

class AnchorPromoteTests(_Base):
    def test_promote_matching_observation(self):
        self.mm.add_observation(self.uid, "behavioral",
                                "Always verifies primary source before claiming a fix",
                                use_semantic=False)
        r = self.mm.promote_observation(self.uid, "primary source", anchor_id="verify", category="behavioral")
        self.assertIn(r["status"], ("added", "updated"))
        a = self.mm.load_anchors(self.uid)[0]
        self.assertEqual(a["id"], "verify")
        self.assertIn("primary source", a["text"])

    def test_promote_no_match(self):
        self.mm.add_observation(self.uid, "behavioral", "something unrelated entirely",
                                use_semantic=False)
        r = self.mm.promote_observation(self.uid, "nonexistent needle")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason"], "no_match")

    def test_promote_no_observations_file(self):
        r = self.mm.promote_observation(self.uid, "anything")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason"], "no_observations")

    def test_promote_multiple_takes_most_recent(self):
        self.mm.add_observation(self.uid, "preference", "likes the colour blue version one",
                                use_semantic=False)
        self.mm.add_observation(self.uid, "preference", "likes the colour blue version two final",
                                use_semantic=False)
        r = self.mm.promote_observation(self.uid, "colour blue", anchor_id="col")
        self.assertEqual(r["matched"], 2)
        self.assertIn("version two final", self.mm.load_anchors(self.uid)[0]["text"])


# ─────────────────────────── Anchors: render/parse ───────────────────────

class AnchorRenderParseTests(unittest.TestCase):
    def test_render_empty(self):
        self.assertEqual(render_anchors_section([]), "")

    def test_render_format(self):
        section = render_anchors_section([
            {"id": "a", "category": "identity", "text": "is X"},
            {"id": "b", "category": "", "text": "does Y"},
        ])
        self.assertIn("### Anchored Facts (ground truth, never auto-rewritten)", section)
        self.assertIn("- (identity) is X", section)
        self.assertIn("- does Y", section)
        self.assertNotIn("[id:", section)  # id is a management handle, never in context

    def test_parse_valid(self):
        a = parse_anchor_line("- [id:no-dash] (aesthetic) Never use em dashes")
        self.assertEqual(a["id"], "no-dash")
        self.assertEqual(a["category"], "aesthetic")
        self.assertEqual(a["text"], "Never use em dashes")

    def test_parse_no_category(self):
        a = parse_anchor_line("- [id:plain] just a fact")
        self.assertEqual(a["category"], "")
        self.assertEqual(a["text"], "just a fact")

    def test_parse_non_anchor(self):
        self.assertIsNone(parse_anchor_line("# Anchored Facts"))
        self.assertIsNone(parse_anchor_line("- (identity) no id here"))
        self.assertIsNone(parse_anchor_line(""))


# ─────────────────── Anchors: drift-proof context injection ──────────────

class AnchorContextInjectionTests(_Base):
    def test_injected_in_full_mode(self):
        self.mm.init_model(self.uid, name="Tester")
        self.mm.add_anchor(self.uid, "Ground truth fact ABC", anchor_id="g", category="identity")
        ctx = self.mm.build_memory_context(self.uid, full_mode=True)
        self.assertIn("Anchored Facts (ground truth", ctx)
        self.assertIn("Ground truth fact ABC", ctx)
        # Anchors come before the person model block.
        self.assertLess(ctx.index("Anchored Facts"), ctx.index("Person Model"))

    def test_injected_in_lite_mode(self):
        self.mm.add_anchor(self.uid, "Lite mode anchor fact", anchor_id="g")
        ctx = self.mm.build_memory_context(self.uid, full_mode=False)
        self.assertIn("Lite mode anchor fact", ctx)

    def test_no_anchor_section_when_none(self):
        self.mm.init_model(self.uid, name="Tester")
        ctx = self.mm.build_memory_context(self.uid, full_mode=True)
        self.assertNotIn("Anchored Facts", ctx)

    def test_anchor_survives_model_truncation(self):
        # A model larger than the 3000-char cap gets truncated, but the anchor
        # block must survive verbatim (it is injected outside the truncation).
        big_model = "# Model\n" + ("x" * 5000)
        self.mm.set_model(self.uid, big_model)
        self.mm.add_anchor(self.uid, "Survives truncation fact", anchor_id="s")
        ctx = self.mm.build_memory_context(self.uid, full_mode=True)
        self.assertIn("Survives truncation fact", ctx)
        self.assertIn("model truncated", ctx)  # the model itself WAS truncated


# ─────────────────────────── Corroboration: lexical ──────────────────────

class LexicalCorroborationTests(_Base):
    def test_near_restatement_suppressed_and_strengthened(self):
        r1 = self.mm.add_observation(self.uid, "preference",
                                     "User strongly prefers concise written proposals",
                                     use_semantic=False)
        self.assertEqual(r1["status"], "saved")
        r2 = self.mm.add_observation(self.uid, "preference",
                                     "Strongly prefers concise written proposals indeed",
                                     use_semantic=False)
        self.assertEqual(r2["status"], "corroborated_lexical")
        self.assertEqual(r2["seen"], 2)
        # Only one observation line remains (the new one was suppressed).
        obs = self.mm.get_all_observations(self.uid, limit=50)
        self.assertEqual(len(obs), 1)
        self.assertIn("[seen:2]", obs[0])
        self.assertIn("[lastseen:", obs[0])

    def test_distinct_observation_saved(self):
        self.mm.add_observation(self.uid, "preference", "prefers dark cinematic visuals",
                                use_semantic=False)
        r = self.mm.add_observation(self.uid, "state", "currently focused on tax compliance paperwork",
                                    use_semantic=False)
        self.assertEqual(r["status"], "saved")
        self.assertEqual(len(self.mm.get_all_observations(self.uid, limit=50)), 2)

    def test_corroboration_raises_importance(self):
        self.mm.add_observation(self.uid, "preference",
                                "prefers concise written proposals always",
                                importance=4, use_semantic=False)
        r2 = self.mm.add_observation(self.uid, "preference",
                                     "prefers concise written proposals always now",
                                     importance=9, use_semantic=False)
        self.assertEqual(r2["status"], "corroborated_lexical")
        obs = self.mm.get_all_observations(self.uid, limit=50)[0]
        self.assertIn("[importance:9]", obs)

    def test_invalid_type(self):
        r = self.mm.add_observation(self.uid, "not_a_type", "whatever", use_semantic=False)
        self.assertEqual(r["status"], "invalid_type")


# ─────────────────────────── Corroboration: semantic ─────────────────────

class SemanticCorroborationTests(_Base):
    def test_semantic_link_keeps_both_and_carries_count(self):
        self.mm.add_observation(self.uid, "preference",
                                "User strongly prefers concise written proposals",
                                use_semantic=False)

        def fake_sem(_self, new_content, candidate_lines, cache_path):
            return (candidate_lines[0], 0.85) if candidate_lines else None

        with patch.object(MemoryManager, "_semantic_best_match", fake_sem), \
                patch("core.memory.SEMANTIC_ENABLED_DEFAULT", True):
            r = self.mm.add_observation(self.uid, "preference",
                                        "They want brief summaries when reviewing documents",
                                        use_semantic=True)
        self.assertEqual(r["status"], "corroborated_semantic")
        self.assertEqual(r["seen"], 2)
        obs = self.mm.get_all_observations(self.uid, limit=50)
        self.assertEqual(len(obs), 2)  # both lines kept
        self.assertIn("[seen:2]", obs[-1])
        self.assertIn("[corrob:", obs[-1])

    def test_semantic_none_means_plain_save(self):
        self.mm.add_observation(self.uid, "preference", "prefers dark visuals",
                                use_semantic=False)

        with patch.object(MemoryManager, "_semantic_best_match", lambda *a, **k: None), \
                patch("core.memory.SEMANTIC_ENABLED_DEFAULT", True):
            r = self.mm.add_observation(self.uid, "state", "is travelling next week to Berlin",
                                        use_semantic=True)
        self.assertEqual(r["status"], "saved")

    def test_semantic_parse_miss_does_not_bump(self):
        # Matched line has no valid timestamp, so never link or bump.
        self.mm.add_observation(self.uid, "preference", "prefers minimal interfaces",
                                use_semantic=False)

        def bad_line(_self, new_content, candidate_lines, cache_path):
            return ("garbage line without timestamp", 0.9)

        with patch.object(MemoryManager, "_semantic_best_match", bad_line), \
                patch("core.memory.SEMANTIC_ENABLED_DEFAULT", True):
            r = self.mm.add_observation(self.uid, "state", "exploring a new ambient album idea",
                                        use_semantic=True)
        self.assertEqual(r["status"], "saved")
        self.assertNotIn("[seen:2]", self.mm.get_all_observations(self.uid, limit=50)[-1])

    def test_no_semantic_flag_skips_backend(self):
        self.mm.add_observation(self.uid, "preference", "prefers minimal interfaces",
                                use_semantic=False)
        called = {"n": 0}

        def counting(_self, *a, **k):
            called["n"] += 1
            return None

        with patch.object(MemoryManager, "_semantic_best_match", counting), \
                patch("core.memory.SEMANTIC_ENABLED_DEFAULT", True):
            self.mm.add_observation(self.uid, "state", "totally distinct new content here please",
                                    use_semantic=False)
        self.assertEqual(called["n"], 0)


# ─────────────────────────── Helpers ─────────────────────────────────────

class HelperTests(unittest.TestCase):
    def test_set_tag_inserts_after_type(self):
        line = "[2026-06-22 10:00] (behavioral) [importance:5] content here"
        out = _set_tag(line, "seen", 2)
        self.assertIn("[seen:2]", out)
        # New tag sits right after (type), before the content remains intact.
        self.assertTrue(out.startswith("[2026-06-22 10:00] (behavioral) [seen:2]"))

    def test_set_tag_replaces_existing(self):
        line = "[2026-06-22 10:00] (behavioral) [seen:2] [importance:5] content"
        out = _set_tag(line, "seen", 5)
        self.assertIn("[seen:5]", out)
        self.assertNotIn("[seen:2]", out)

    def test_get_int_tag(self):
        line = "[2026-06-22 10:00] (behavioral) [seen:7] content"
        self.assertEqual(_get_int_tag(line, "seen", 1), 7)
        self.assertEqual(_get_int_tag(line, "missing", 3), 3)

    def test_find_lexical_match(self):
        lines = ["[2026-06-22 10:00] (preference) [importance:5] prefers concise written proposals"]
        self.assertIsNotNone(_find_lexical_match("prefers concise written proposals now", lines))
        self.assertIsNone(_find_lexical_match("completely different unrelated subject matter", lines))

    def test_find_lexical_match_too_few_keywords(self):
        lines = ["[2026-06-22 10:00] (state) [importance:5] ok now"]
        self.assertIsNone(_find_lexical_match("ok", lines))


class RewriteLinePrefixCollisionTests(_Base):
    def test_whole_line_match_not_substring(self):
        # line_short is a textual PREFIX of line_long and appears AFTER it, so a
        # naive substring replace would corrupt line_long. Whole-line matching
        # must replace only the exact line_short.
        line_long = "[2026-06-22 10:00] (behavioral) [importance:5] alpha beta gamma"
        line_short = "[2026-06-22 10:00] (behavioral) [importance:5] alpha"
        obs_file = self.mm._observations_file(self.uid)
        obs_file.parent.mkdir(parents=True, exist_ok=True)
        obs_file.write_text(f"# header\n\n{line_long}\n{line_short}\n", encoding="utf-8")

        replacement = "[2026-06-22 10:00] (behavioral) [importance:5] [seen:2] alpha"
        self.mm._rewrite_observation_line(obs_file, line_short, replacement)

        content = obs_file.read_text(encoding="utf-8")
        self.assertIn(line_long, content)        # the longer line is untouched
        self.assertIn(replacement, content)      # the exact short line was replaced
        self.assertNotIn(f"{line_short}\n", content.replace(replacement, ""))


if __name__ == "__main__":
    unittest.main()
