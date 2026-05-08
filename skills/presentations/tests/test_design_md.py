"""Tests for design_md.py — frontmatter, prose fallback, and library resolution."""

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import design_md  # noqa: E402
import references  # noqa: E402


# ─────────────────────────────────────────────
#  Frontmatter mode
# ─────────────────────────────────────────────

FRONTMATTER_DOC = """---
name: TestBrand
description: A short voice line.
colors:
  canvas: "#0a0a0a"
  ink: "#f7f8f8"
  ink-muted: "#d0d6e0"
  ink-subtle: "#8a8f98"
  primary: "#5e6ad2"
  brand-secondary: "#7a7fad"
typography:
  display-xl:
    fontFamily: Linear Display
    fontSize: 80px
  body:
    fontFamily: Linear Text
    fontSize: 16px
---

## Overview
First paragraph of prose body.

## Don't
- Don't use lavender as a card fill.
- Don't introduce a second chromatic accent.
"""


class FrontmatterModeTests(unittest.TestCase):
    def test_basic_extraction(self):
        d = design_md.parse_design_md(FRONTMATTER_DOC)
        self.assertEqual(d["name"], "TestBrand")
        self.assertEqual(d["mood"], "A short voice line.")
        self.assertEqual(d["color_palette"]["bg"], "#0a0a0a")
        self.assertEqual(d["color_palette"]["text"], "#f7f8f8")
        self.assertEqual(d["color_palette"]["text_mid"], "#d0d6e0")
        self.assertEqual(d["color_palette"]["text_dim"], "#8a8f98")
        self.assertEqual(d["color_palette"]["accent"], "#5e6ad2")

    def test_proprietary_fonts_mapped(self):
        d = design_md.parse_design_md(FRONTMATTER_DOC)
        self.assertEqual(d["fonts"]["heading"], "Inter")
        self.assertEqual(d["fonts"]["body"], "Inter")
        self.assertEqual(d["fonts_original"]["heading"], "Linear Display")
        self.assertEqual(d["fonts_original"]["body"], "Linear Text")

    def test_dont_list_extracted(self):
        d = design_md.parse_design_md(FRONTMATTER_DOC)
        self.assertEqual(len(d["avoid"]), 2)
        # "Don't " prefix should be stripped from each item
        self.assertTrue(all(not v.lower().startswith("don") for v in d["avoid"]))

    def test_brand_color_omitted_if_equal_to_accent(self):
        """If `brand` and `primary` resolve to the same hex, brand_color is dropped
        so the skill's brand-glow effect doesn't get suppressed."""
        doc = """---
name: X
colors:
  primary: "#ff0000"
  brand: "#ff0000"
---
"""
        d = design_md.parse_design_md(doc)
        self.assertEqual(d["color_palette"]["accent"], "#ff0000")
        self.assertNotIn("brand_color", d["color_palette"])


# ─────────────────────────────────────────────
#  Prose mode
# ─────────────────────────────────────────────

PROSE_DOC = """# Design System Inspired by Foo

## Visual Theme

A near-black canvas with electric coral accents.

## Color Palette

### Primary
- **Pure White** (`#ffffff`): Page background, card surfaces.
- **Foo Black** (`#171717`): Primary text, headings.

### Neutrals
- **Gray 600** (`#4d4d4d`): Secondary text, description copy.
- **Gray 400** (`#808080`): Placeholder text, disabled states.

### Interactive
- **Foo Coral** (`#ff5b4f`): Primary CTA, link emphasis.

## Typography

### Font Family
- **Primary**: `Geist`, with fallback Inter.
"""


class ProseModeTests(unittest.TestCase):
    def test_palette_extracted_from_prose(self):
        d = design_md.parse_design_md(PROSE_DOC)
        self.assertEqual(d["color_palette"]["bg"], "#ffffff")
        self.assertEqual(d["color_palette"]["text"], "#171717")
        self.assertEqual(d["color_palette"]["text_mid"], "#4d4d4d")
        self.assertEqual(d["color_palette"]["text_dim"], "#808080")
        self.assertEqual(d["color_palette"]["accent"], "#ff5b4f")

    def test_font_extracted_and_resolved(self):
        d = design_md.parse_design_md(PROSE_DOC)
        self.assertEqual(d["fonts_original"]["heading"], "Geist")
        self.assertEqual(d["fonts"]["heading"], "Inter")  # Geist is mapped to Inter

    def test_name_from_h1(self):
        d = design_md.parse_design_md(PROSE_DOC)
        self.assertEqual(d["name"], "Foo")  # "Design System Inspired by " prefix stripped


# ─────────────────────────────────────────────
#  Edge cases
# ─────────────────────────────────────────────

class EdgeCaseTests(unittest.TestCase):
    def test_empty_doc_raises(self):
        with self.assertRaises(design_md.DesignMdError):
            design_md.parse_design_md("# Header only, nothing extractable")

    def test_invalid_yaml_raises(self):
        with self.assertRaises(design_md.DesignMdError):
            design_md.parse_design_md("---\n: not valid yaml :\n---\nbody")

    def test_resolve_unknown_mono_falls_back(self):
        self.assertEqual(design_md._resolve_font("Acme Mono"), "JetBrains Mono")

    def test_resolve_known_proprietary_mapped(self):
        self.assertEqual(design_md._resolve_font("SF Pro Display"), "Inter")
        self.assertEqual(design_md._resolve_font("Geist Sans"), "Inter")
        self.assertEqual(design_md._resolve_font("abcNormal"), "Inter")
        self.assertEqual(design_md._resolve_font("sohne-var"), "Inter")
        self.assertEqual(design_md._resolve_font("Notion Sans"), "Inter")

    def test_resolve_empty_returns_empty(self):
        self.assertEqual(design_md._resolve_font(""), "")


# ─────────────────────────────────────────────
#  Library resolution
# ─────────────────────────────────────────────

class LibraryTests(unittest.TestCase):
    def test_list_includes_curated_brands(self):
        names = set(design_md.list_library())
        for slug in ("linear", "stripe", "apple", "notion", "vercel", "runway"):
            self.assertIn(slug, names, f"missing curated entry {slug!r}")

    def test_load_linear(self):
        d = design_md.load_design("linear")
        self.assertEqual(d["name"], "Linear")
        self.assertEqual(d["color_palette"]["bg"], "#010102")
        self.assertEqual(d["fonts"]["heading"], "Inter")

    def test_resolve_path_for_unknown_raises(self):
        with self.assertRaises(design_md.DesignMdError):
            design_md.load_design("nonexistent-brand-xyz")

    def test_resolve_path_accepts_absolute_path(self):
        # Should accept any file that parses cleanly.
        path = design_md.LIB_DIR / "linear.md"
        d = design_md.load_design(str(path))
        self.assertEqual(d["name"], "Linear")


# ─────────────────────────────────────────────
#  Merge with apply_reference (treatment wins)
# ─────────────────────────────────────────────

class MergeTests(unittest.TestCase):
    def test_treatment_wins_on_palette_keys(self):
        design = design_md.load_design("linear")
        treatment = {"scheme": {"bg": "#ffffff"}}
        out = references.apply_reference(treatment, design)
        # Treatment kept its bg
        self.assertEqual(out["scheme"]["bg"], "#ffffff")
        # Filled from design where not set
        self.assertEqual(out["scheme"]["accent"], "#5e6ad2")
        self.assertEqual(out["scheme"]["text"], "#f7f8f8")

    def test_treatment_wins_on_fonts(self):
        design = design_md.load_design("linear")
        treatment = {"fonts": {"heading": "Manrope"}}
        out = references.apply_reference(treatment, design)
        self.assertEqual(out["fonts"]["heading"], "Manrope")
        self.assertEqual(out["fonts"]["body"], "Inter")  # filled from design


if __name__ == "__main__":
    unittest.main()
