"""Consistency guards between the Seedance prompt guides and the wrapper.

The two Seedance guides make numeric and parameter claims that only stay true
while `generate.py`'s catalog agrees with them. Nothing else checks that pairing:
`test_image_gen.py` pins the catalog against the live API, and the routing tests
pin guide *filenames*, but a guide that documents a 5s floor against a wrapper
that allows 4 passes both.

Every claim here was measured against `higgsfield generate cost` on CLI 1.1.23 on
2026-08-09. Each test that reads guide text asserts its anchor was found, so a
reformat fails loudly instead of quietly matching nothing.
"""
from __future__ import annotations

import inspect
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT / "skills" / "image-gen" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import generate  # noqa: E402

MODELS_DIR = ROOT / "skills" / "image-gen" / "models"
GUIDE_25 = MODELS_DIR / "seedance-2-5.md"
GUIDE_20 = MODELS_DIR / "seedance.md"


class TestSeedance25GuideMatchesTheWrapper(unittest.TestCase):
    """The 2.5 guide's hard specs have to be the wrapper's numbers."""

    @classmethod
    def setUpClass(cls):
        cls.text = GUIDE_25.read_text(encoding="utf-8")

    def test_guide_exists_and_is_not_a_stub(self):
        self.assertTrue(GUIDE_25.is_file())
        self.assertGreater(len(self.text.splitlines()), 100)

    def test_documented_duration_bounds_match_video_durations(self):
        row = re.search(r"^\| Duration \|.*\|\s*\*\*(\d+) to (\d+) s\*\*.*$",
                        self.text, re.M)
        self.assertIsNotNone(row, "could not find the Duration row in the hard specs table")
        lo, hi = int(row.group(1)), int(row.group(2))
        dur = generate.VIDEO_DURATIONS["seedance_2_5"]
        self.assertEqual((dur["min"], dur["max"]), (lo, hi),
                         msg="the guide and VIDEO_DURATIONS disagree on the seedance2.5 bounds")

    def test_the_floor_is_four_not_five(self):
        # The 2026-08-07 catalog sweep recorded 5. `--duration 3` is what the API
        # actually rejects, and 4 quotes 26 credits at 720p.
        self.assertEqual(generate.VIDEO_DURATIONS["seedance_2_5"]["min"], 4)
        self.assertIn("greater than or\nequal to 4", self.text.replace("\r", ""))

    def test_documented_resolutions_match_model_params(self):
        params = generate.MODEL_PARAMS["seedance_2_5"]
        self.assertEqual(params["resolution"]["options"], ["480p", "720p"])
        row = re.search(r"^\| Resolution \|.*\|\s*\*\*480p or 720p only\.\*\*", self.text, re.M)
        self.assertIsNotNone(row, "could not find the Resolution row in the hard specs table")

    def test_quoted_costs_are_linear_at_the_documented_rate(self):
        """Every credit figure in the cost paragraph must be rate x seconds.

        A hand-typed table is exactly where a transposed digit hides, and the
        figures are what someone budgets a shoot against.
        """
        para = re.search(r"\*\*Cost, measured not estimated.*?length\.", self.text, re.S)
        self.assertIsNotNone(para, "could not find the measured cost paragraph")
        body = para.group(0)
        self.assertIn("480p is", body, "cost paragraph no longer splits by resolution")
        at_720, at_480 = body.split("480p is", 1)

        def pairs(chunk):
            return [(float(c), int(s)) for c, s in
                    re.findall(r"(\d+(?:\.\d+)?)\s+(?:credits\s+)?for\s+(?:the\s+)?(\d+)\s*s", chunk)]

        seen_720, seen_480 = pairs(at_720), pairs(at_480)
        self.assertGreaterEqual(len(seen_720), 4, f"only parsed {seen_720} at 720p")
        self.assertGreaterEqual(len(seen_480), 3, f"only parsed {seen_480} at 480p")
        for credits, seconds in seen_720:
            self.assertAlmostEqual(credits, 6.5 * seconds, places=6,
                                   msg=f"720p: {credits} for {seconds}s is not 6.5/s")
        for credits, seconds in seen_480:
            self.assertAlmostEqual(credits, 3.0 * seconds, places=6,
                                   msg=f"480p: {credits} for {seconds}s is not 3.0/s")

    def test_the_cheapest_and_dearest_rolls_sit_on_the_bounds(self):
        dur = generate.VIDEO_DURATIONS["seedance_2_5"]
        self.assertIn(f"{6.5 * dur['min']:g} credits for the {dur['min']} s minimum", self.text)
        self.assertIn(f"{6.5 * dur['max']:g} for the {dur['max']} s maximum", self.text)


class TestCarryTableConflicts(unittest.TestCase):
    """The carry table names eight things that do not transfer from 2.0.

    Both directions are checked. Absent-on-2.5 alone would pass for a param that
    exists on neither model, which would make the warning noise rather than a
    trap; present-on-2.0 is what proves the habit is real.
    """

    @classmethod
    def setUpClass(cls):
        cls.text = GUIDE_25.read_text(encoding="utf-8")
        cls.p20 = generate.MODEL_PARAMS["seedance_2_0"]
        cls.p25 = generate.MODEL_PARAMS["seedance_2_5"]

    def test_the_carry_table_is_present(self):
        self.assertIn("## What carries from Seedance 2.0", self.text)
        self.assertIn("| From `seedance.md` | On 2.5 | Basis |", self.text)

    def test_rejected_params_are_absent_from_2_5(self):
        for param in ("genre", "bitrate_mode"):
            self.assertNotIn(param, self.p25, msg=f"{param} is documented as rejected on 2.5")

    def test_rejected_params_are_real_2_0_params(self):
        for param in ("genre", "bitrate_mode"):
            self.assertIn(param, self.p20, msg=f"{param} must exist on 2.0 or the warning is noise")

    def test_mode_means_capability_on_2_5_and_a_speed_tier_on_2_0(self):
        self.assertEqual(sorted(self.p20["mode"]["options"]), ["fast", "std"])
        self.assertEqual(self.p25["mode"]["options"],
                         ["t2v", "omni_reference", "video_edit", "video_extension"])
        for tier in ("fast", "std"):
            self.assertNotIn(tier, self.p25["mode"]["options"],
                             msg="a 2.0 speed tier leaked into 2.5's mode enum")

    def test_the_three_rejections_are_quoted_verbatim(self):
        for line in (
            "Invalid values: mode=fast (allowed: t2v,omni_reference,video_edit,video_extension)",
            "Unknown params: genre",
            "Unknown params: bitrate_mode",
        ):
            self.assertIn(line, self.text, msg=f"missing the measured rejection: {line}")

    def test_the_reference_ceiling_is_the_2_5_number_not_the_2_0_one(self):
        # 2.0 is 9 images / 12 files total; 2.5 is 30 images / 50 total. Carrying
        # the smaller ceiling silently caps what the model can be given.
        self.assertIn("**30 images and 50 total**", self.text)


class TestSeedance20GuideCorrections(unittest.TestCase):
    """The 2.0 guide's own corrections, and the pointer that keeps the two in sync."""

    @classmethod
    def setUpClass(cls):
        cls.text = GUIDE_20.read_text(encoding="utf-8")

    def test_it_points_at_the_carry_table(self):
        self.assertIn("row by row table", self.text)
        self.assertIn("seedance-2-5.md", self.text)

    def test_it_names_both_silent_traps(self):
        # A reader who only opens this file has to be told which two habits fail
        # on 2.5, because both fail silently rather than looking wrong.
        self.assertIn("ban on music in the prompt is reversed on 2.5", self.text)
        self.assertIn("`mode` means a speed tier here and a capability there", self.text)

    def test_the_end_image_row_distinguishes_the_three_models(self):
        row = re.search(r"^\| Start / end image \|(.+)\|\s*$", self.text, re.M)
        self.assertIsNotNone(row, "could not find the Start / end image row")
        cells = [c.strip() for c in row.group(1).split("|")]
        self.assertEqual(len(cells), 3, msg=f"expected 3 model cells, got {cells}")
        needs_start = [i for i, c in enumerate(cells) if "needs" in c]
        # Column order is seedance 2.0, seedance1.5, seedance-mini. Only 1.5 refuses
        # an end frame on its own; the old row applied that rule to all three.
        self.assertEqual(needs_start, [1],
                         msg=f"only seedance1.5 requires start_image; row says {cells}")

    def test_the_speed_tier_costs_match_the_measured_ratio(self):
        self.assertIn("17.5 credits for a 5 s clip against 22.5", self.text)
        self.assertAlmostEqual(17.5 / 5, 3.5)
        self.assertAlmostEqual(22.5 / 5, 4.5)

    def test_the_documented_wrapper_defaults_are_the_real_ones(self):
        p20 = generate.MODEL_PARAMS["seedance_2_0"]
        self.assertEqual(p20["mode"]["default"], "std")
        self.assertEqual(p20["bitrate_mode"]["default"], "standard")
        self.assertIn("The wrapper\n  default is `std`", self.text)
        self.assertIn("The wrapper default is `standard`", self.text)


class TestKeyframeCaveat(unittest.TestCase):
    """`--cost` silently drops the keyframe flags, so the guides must say so.

    `estimate_cost` builds its own command and never forwards start/end images,
    which means a keyframed job quotes as a plain t2v roll and the mode rejection
    only surfaces on the paid call. If the wrapper is ever fixed, this fails and
    the warning should come out of both guides with it.
    """

    def test_estimate_cost_takes_no_keyframe_arguments(self):
        params = inspect.signature(generate.estimate_cost).parameters
        for name in ("start_image", "end_image", "video_references"):
            self.assertNotIn(name, params,
                             msg=f"estimate_cost now takes {name}; update the guide warnings")

    def test_generate_video_does_forward_them(self):
        # The asymmetry is the whole point: the paid path sends the flags, the
        # free quote does not, so the quote cannot see the rejection.
        params = inspect.signature(generate.generate_video).parameters
        for name in ("start_image", "end_image", "video_references"):
            self.assertIn(name, params)

    def test_both_guides_carry_the_warning(self):
        self.assertIn("`--cost` cannot check any of this",
                      GUIDE_25.read_text(encoding="utf-8"))
        self.assertIn("never forwards the keyframe flags",
                      GUIDE_20.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
