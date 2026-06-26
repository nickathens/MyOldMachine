"""Offline unit tests for the greek-law document templates (πρότυπα).

Pure data and text: no network, no third party dependencies. Asserts the template
registry integrity, the fill and pretty helpers, the CLI contract, and the signature
property of this stage: a δικόγραφο template, once filled, passes the structural
αοριστία preflight (aoristia_check.py) by construction.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
import os
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "greek-law" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import protypa as pt  # noqa: E402
import aoristia_check as ac  # noqa: E402

BANNED = ("—", "–", "→", "←", "⇒", "⇐")  # dashes, arrows


def run(argv):
    """Invoke the CLI, capturing (returncode, stdout, stderr). argparse p.error
    raises SystemExit(2); the unknown slug path returns 2 directly. Handle both."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = pt.main(argv)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class MetaIntegrityTests(unittest.TestCase):
    def test_required_keys(self):
        for m in pt.META:
            for key in ("slug", "title", "audience", "doc_class", "aoristia",
                        "summary", "pairs"):
                self.assertIn(key, m, f"{m.get('slug')} missing {key}")

    def test_audience_and_aoristia_values(self):
        for m in pt.META:
            self.assertIn(m["audience"], ("professional", "both"))
            self.assertIn(m["aoristia"], (None, "agogi", "generic"))

    def test_slugs_unique(self):
        slugs = [m["slug"] for m in pt.META]
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_every_slug_has_a_nonempty_template_file(self):
        for m in pt.META:
            path = pt.TEMPLATES_DIR / f"{m['slug']}.txt"
            self.assertTrue(path.exists(), f"missing template {path}")
            self.assertTrue(path.read_text(encoding="utf-8").strip())

    def test_no_orphan_template_files(self):
        # Every .txt in templates/ is registered in META.
        known = {m["slug"] for m in pt.META}
        for path in pt.TEMPLATES_DIR.glob("*.txt"):
            self.assertIn(path.stem, known, f"unregistered template {path.name}")


class TemplateTextTests(unittest.TestCase):
    def test_every_template_has_fields(self):
        for m in pt.META:
            self.assertTrue(pt.tokens(pt.load(m["slug"])),
                            f"{m['slug']} has no placeholders")

    def test_tokens_are_ordered_and_deduped(self):
        # exodiki-dilosi names the recipient twice; tokens() returns it once.
        toks = pt.tokens(pt.load("exodiki-dilosi"))
        self.assertEqual(len(toks), len(set(toks)))
        self.assertIn("ΟΝΟΜΑΤΕΠΩΝΥΜΟ ΠΑΡΑΛΗΠΤΗ", toks)

    def test_pretty_leaves_no_braces(self):
        for m in pt.META:
            pretty = pt.pretty(pt.load(m["slug"]))
            self.assertNotIn("{", pretty)
            self.assertNotIn("}", pretty)

    def test_house_style_glyphs(self):
        # Templates and the practice module carry no dashes, arrows or angle brackets.
        targets = [pt.TEMPLATES_DIR / f"{m['slug']}.txt" for m in pt.META]
        targets.append(SCRIPTS.parent / "practice" / "syntaxi-eggrafon.md")
        for path in targets:
            body = path.read_text(encoding="utf-8")
            for ch in BANNED + ("<", ">"):
                self.assertNotIn(ch, body, f"{path.name} contains {ch!r}")


class FillTests(unittest.TestCase):
    def test_fill_substitutes_and_flags_gaps(self):
        text = pt.load("agogi-katavolis")
        filled = pt.fill(text, {"ΑΦΜ ΕΝΑΓΟΝΤΟΣ": "123456789"})
        self.assertIn("ΑΦΜ 123456789", filled)
        # An unprovided field stays loud, never silently blank.
        self.assertIn("[ΣΥΜΠΛΗΡΩΣΤΕ:", filled)

    def test_fill_replaces_every_occurrence(self):
        text = pt.load("exodiki-dilosi")
        filled = pt.fill(text, {"ΟΝΟΜΑΤΕΠΩΝΥΜΟ ΠΑΡΑΛΗΠΤΗ": "Μαρία Νικολάου"})
        self.assertEqual(filled.count("Μαρία Νικολάου"), 2)

    def test_sample_fill_leaves_no_placeholder(self):
        for m in pt.META:
            text = pt.load(m["slug"])
            filled = pt.fill(text, pt.sample_values(text))
            self.assertNotIn("{", filled)
            self.assertNotIn("ΣΥΜΠΛΗΡΩΣΤΕ", filled, f"{m['slug']} sample left a gap")


class AoristiaCoherenceTests(unittest.TestCase):
    """The signature property: a filled δικόγραφο template passes the structural check."""

    def _criticals(self, slug, doc_type):
        text = pt.load(slug)
        filled = pt.fill(text, pt.sample_values(text))
        findings = ac.check(filled, doc_type=doc_type)
        return ac.summarize(findings), findings

    def test_agogi_sample_is_structurally_complete(self):
        summary, _ = self._criticals("agogi-katavolis", "agogi")
        self.assertEqual(summary["critical"], 0, summary["verdict"])
        # The flagship is byte clean: not even a warning level gap.
        self.assertEqual(summary["warnings"], 0, summary["verdict"])

    def test_every_dikografo_template_passes_its_declared_check(self):
        # Data driven: each template that declares an αοριστία type must pass it.
        for m in pt.META:
            if not m["aoristia"]:
                continue
            summary, _ = self._criticals(m["slug"], m["aoristia"])
            self.assertEqual(summary["critical"], 0,
                             f"{m['slug']} ({m['aoristia']}): {summary['verdict']}")

    def test_non_court_templates_declare_no_check(self):
        # An εξώδικο and a σύμβαση are not court δικόγραφα, so no αοριστία type.
        self.assertIsNone(pt._meta("exodiki-dilosi")["aoristia"])
        self.assertIsNone(pt._meta("idiotiko-symfonitiko")["aoristia"])


class CliTests(unittest.TestCase):
    def test_list_human_and_json(self):
        code, out, _ = run(["list"])
        self.assertEqual(code, 0)
        self.assertIn("agogi-katavolis", out)
        code, out, _ = run(["list", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(len(data), len(pt.META))

    def test_keys_lists_fields(self):
        code, out, _ = run(["keys", "agogi-katavolis"])
        self.assertEqual(code, 0)
        self.assertIn("ΑΦΜ ΕΝΑΓΟΝΤΟΣ", out)

    def test_get_default_is_pretty_with_footer(self):
        code, out, _ = run(["get", "agogi-katavolis"])
        self.assertEqual(code, 0)
        self.assertIn("[ΑΦΜ ΕΝΑΓΟΝΤΟΣ]", out)
        self.assertNotIn("{", out)
        self.assertIn("Άρθρο 38", out)

    def test_get_raw_keeps_markers(self):
        code, out, _ = run(["get", "agogi-katavolis", "--raw"])
        self.assertEqual(code, 0)
        self.assertIn("{ΑΦΜ ΕΝΑΓΟΝΤΟΣ}", out)

    def test_get_sample_is_marked_and_filled(self):
        code, out, _ = run(["get", "agogi-katavolis", "--sample"])
        self.assertEqual(code, 0)
        self.assertIn("ΥΠΟΔΕΙΓΜΑ", out)
        self.assertIn("123456789", out)

    def test_fill_from_values_file(self):
        with tempfile.TemporaryDirectory() as d:
            vpath = os.path.join(d, "v.json")
            with open(vpath, "w", encoding="utf-8") as fh:
                json.dump({"ΑΦΜ ΕΝΑΓΟΝΤΟΣ": "999999999"}, fh, ensure_ascii=False)
            code, out, _ = run(["fill", "agogi-katavolis", "--values", vpath])
        self.assertEqual(code, 0)
        self.assertIn("ΑΦΜ 999999999", out)

    def test_unknown_slug_returns_2(self):
        code, _, err = run(["get", "no-such-template"])
        self.assertEqual(code, 2)
        self.assertIn("Άγνωστο πρότυπο", err)

    def test_bad_command_errors(self):
        code, _, _ = run(["frobnicate", "agogi-katavolis"])
        self.assertEqual(code, 2)

    def test_keys_without_slug_errors(self):
        code, _, _ = run(["keys"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
