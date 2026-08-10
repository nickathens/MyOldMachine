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
GUIDE_H3 = MODELS_DIR / "minimax-h3.md"
SKILL_MD = ROOT / "skills" / "image-gen" / "SKILL.md"


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


class TestPromptCeiling(unittest.TestCase):
    """The 4000-character ceiling, and the unit it is counted in.

    The ceiling itself is cheap to state and cheap to get wrong in the one way
    that costs you work: counting bytes. The API counts Unicode characters, so a
    byte count over-reports every non-ASCII prompt -- measured live, a 4000
    character Greek prompt is 7525 bytes, which `wc -c` calls 88 percent over a
    limit the API accepts without complaint. A guide that says `wc -c` makes
    people cut good prompts in half, so the wrong tool is asserted absent, not
    merely the right one present.
    """

    @classmethod
    def setUpClass(cls):
        cls.text = GUIDE_25.read_text(encoding="utf-8")

    def test_the_ceiling_is_stated_in_the_specs_table(self):
        row = re.search(r"^\| Prompt length \|.*\|\s*\*\*4000 characters, hard\*\*", self.text, re.M)
        self.assertIsNotNone(row, "could not find the Prompt length row in the hard specs table")

    def test_the_boundary_is_walked_on_both_sides(self):
        # 4000 alone would not distinguish "at most 4000" from "under 4000".
        self.assertIn("4000\ncharacters is accepted, 4001 is rejected", self.text)

    def test_the_rejection_wording_is_quoted_verbatim(self):
        self.assertIn("`prompt: String should have at most 4000 characters`", self.text)

    def test_it_prescribes_character_counting_not_byte_counting(self):
        self.assertIn("wc -m", self.text)
        self.assertIn("**Use `wc -m`, never `wc -c`.**", self.text)

    def test_no_byte_counting_command_sits_in_a_copyable_block(self):
        """`wc -c` may be named in prose as the wrong tool, never offered as a command.

        Prose can hedge; a fenced block is what gets copied and run. The earlier
        version of this guide shipped `tr '\\n' ' ' < prompt.txt | wc -c` as the
        prescribed count -- and the `tr` was a no-op besides, since swapping a
        newline for a space leaves the byte count untouched.
        """
        in_fence, fenced = False, []
        for line in self.text.splitlines():
            if line.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                fenced.append(line)
        offenders = [ln for ln in fenced if "wc -c" in ln]
        self.assertEqual(offenders, [], msg=f"byte counting offered as a command: {offenders}")
        # And the counting block that does exist must be the character one.
        self.assertTrue(any("wc -m" in ln for ln in fenced),
                        msg="no `wc -m` command block found in the guide")

    def test_the_measured_byte_inflation_is_self_consistent(self):
        m = re.search(r"a (\d+)[- ]character Greek\s*\n?prompt is (\d+) bytes", self.text)
        self.assertIsNotNone(m, "could not find the measured Greek byte count")
        chars, byts = int(m.group(1)), int(m.group(2))
        self.assertEqual(chars, 4000)
        pct = round((byts - chars) / chars * 100)
        self.assertIn(f"{pct} percent\nover the limit", self.text,
                      msg=f"{byts} bytes for {chars} chars is {pct}% over, not what the guide says")

    def test_it_warns_that_the_estimator_does_not_enforce_it(self):
        # The trap is a clean quote read as a green light. `generate cost` takes
        # any length, so only `generate create` can refuse an over-long prompt.
        self.assertIn("**`generate cost` does not enforce it**", self.text)


class TestVideoReferencePricingTier(unittest.TestCase):
    """Two price tiers, selected by the presence of a video reference.

    Measured live on 2026-08-10: 6.5 credits/s at 720p and 3 at 480p with no
    video reference, 4 and 2 with one, in every mode that accepts one. The tier
    follows the file, not the mode name -- `omni_reference` carrying a video
    reference is charged the lower rate, which is why the table below is keyed on
    the reference rather than on the mode.
    """

    T2V_720, T2V_480 = 6.5, 3.0
    VREF_720, VREF_480 = 4.0, 2.0

    @classmethod
    def setUpClass(cls):
        cls.text = GUIDE_25.read_text(encoding="utf-8")

    def test_the_stated_rates_are_the_measured_ones(self):
        self.assertIn("**4\ncredits/s at 720p**", self.text)
        self.assertIn("**2 credits/s at 480p**", self.text)

    def test_the_discount_percentages_match_the_rates(self):
        at_720 = round((self.T2V_720 - self.VREF_720) / self.T2V_720 * 100)
        at_480 = round((self.T2V_480 - self.VREF_480) / self.T2V_480 * 100)
        self.assertIn(f"{at_720} percent off at 720p, {at_480} at 480p", self.text,
                      msg=f"rates give {at_720}% / {at_480}% off")

    def test_every_row_of_the_price_table_is_rate_times_five_seconds(self):
        """The table is hand-typed, and it is what a job gets budgeted against."""
        table = re.search(r"^\| 5 s job \| 720p \| 480p \|\n(?:\|[-| ]+\|\n)((?:\|.*\|\n)+)",
                          self.text, re.M)
        self.assertIsNotNone(table, "could not find the 5 s price table")
        rows = [r for r in table.group(1).strip().splitlines() if r.strip()]
        self.assertEqual(len(rows), 5, msg=f"expected 5 priced rows, got {rows}")

        seen_vref = seen_plain = 0
        for row in rows:
            cells = [c.strip().replace("*", "") for c in row.strip().strip("|").split("|")]
            self.assertEqual(len(cells), 3, msg=f"malformed row: {row!r}")
            label, at720, at480 = cells[0], float(cells[1]), float(cells[2])
            carries_video = ("video reference" in label
                            or "video_edit" in label or "video_extension" in label)
            rate720 = self.VREF_720 if carries_video else self.T2V_720
            rate480 = self.VREF_480 if carries_video else self.T2V_480
            self.assertAlmostEqual(at720, rate720 * 5, places=6,
                                   msg=f"{label}: {at720} at 720p is not {rate720}/s x 5 s")
            self.assertAlmostEqual(at480, rate480 * 5, places=6,
                                   msg=f"{label}: {at480} at 480p is not {rate480}/s x 5 s")
            seen_vref += carries_video
            seen_plain += not carries_video
        # Both tiers must be represented, or the table proves nothing.
        self.assertEqual((seen_vref, seen_plain), (3, 2),
                         msg=f"expected 3 video-reference rows and 2 without, got {seen_vref}/{seen_plain}")

    def test_the_table_covers_every_mode_the_wrapper_offers(self):
        table = re.search(r"^\| 5 s job \| 720p \| 480p \|.*?\n\n", self.text, re.M | re.S)
        self.assertIsNotNone(table)
        for mode in generate.MODEL_PARAMS["seedance_2_5"]["mode"]["options"]:
            self.assertIn(mode, table.group(0), msg=f"price table omits mode {mode}")

    def test_the_lower_tier_is_linear(self):
        para = re.search(r"Linear at the lower rate as well:(.*?)extension\.", self.text, re.S)
        self.assertIsNotNone(para, "could not find the lower-tier linearity sentence")
        pairs = [(float(c), int(s)) for c, s in
                 re.findall(r"(\d+(?:\.\d+)?) for (?:the )?(\d+) s", para.group(1))]
        self.assertGreaterEqual(len(pairs), 4, f"only parsed {pairs}")
        for credits, seconds in pairs:
            self.assertAlmostEqual(credits, self.VREF_720 * seconds, places=6,
                                   msg=f"{credits} for {seconds}s is not {self.VREF_720}/s")

    def test_the_thirty_second_comparison_uses_both_rates(self):
        dur = generate.VIDEO_DURATIONS["seedance_2_5"]["max"]
        self.assertIn(f"costs {self.T2V_720 * dur:g} as `t2v` costs **{self.VREF_720 * dur:g}**",
                      self.text)

    def test_the_overstatement_warning_is_the_arithmetic_difference(self):
        dur = generate.VIDEO_DURATIONS["seedance_2_5"]["max"]
        gap = (self.T2V_720 - self.VREF_720) * dur
        self.assertIn(f"overstates a 30 s job by {gap:g} credits", self.text)

    def test_audio_is_documented_as_free(self):
        # Measured on/off in both resolutions and all four modes: identical quote.
        self.assertIn("**`generate_audio` changes nothing.**", self.text)


class TestImplicitModeTrap(unittest.TestCase):
    """`t2v` rejects references only when `--mode` is passed explicitly.

    The rule is written against the `mode` parameter and the CLI omits the
    parameter when the flag is absent, so the default path never evaluates it:
    a video reference is accepted and billed at the lower rate. This is the
    dangerous shape -- it succeeds rather than failing -- so the guide has to
    say it in both the mode section and the don't-list.
    """

    @classmethod
    def setUpClass(cls):
        cls.text = GUIDE_25.read_text(encoding="utf-8")

    def test_the_t2v_section_qualifies_the_rejection(self):
        self.assertIn("**But only when you say `t2v` out loud", self.text)

    def test_the_dont_list_carries_it(self):
        self.assertIn("**Do not omit `--mode` when you attach a reference.**", self.text)

    def test_the_rule_ordering_is_recorded(self):
        # Mode rules run before field rules, so a doubly-invalid request reports
        # only the mode error. That is why the default path looks silent.
        self.assertIn("run *before* the field rules", self.text)

    def test_the_wrapper_still_cannot_quote_a_video_reference(self):
        """The guide tells you to skip `generate.py --cost`; that must stay true."""
        self.assertNotIn("video_references", inspect.signature(generate.estimate_cost).parameters)
        self.assertIn("never forwards media references", self.text)

    def test_resolution_is_still_dropped_for_video_quotes(self):
        src = inspect.getsource(generate.main)
        self.assertIn('resolution=args.resolution if kind == "image" else None', src,
                      msg="main() now forwards resolution for video; drop the guide's warning")
        self.assertIn("drops\n`--resolution` for video kinds", self.text)


class TestTheSelectionSurfacesCarryBothTiers(unittest.TestCase):
    """`SKILL.md`'s credits/s table is where a model is picked, before any guide is opened.

    The video reference tier is the one fact that changes that pick: at 4.0/s
    `seedance2.5` stops being the dearest model on the route and ties `h3`. A
    correction that lands only in `seedance-2-5.md` leaves the decision it was
    meant to change still reading the superseded ranking, so the selection table
    and the head to head in `minimax-h3.md` are pinned here against the rate the
    model guide itself states.
    """

    @classmethod
    def setUpClass(cls):
        cls.skill = SKILL_MD.read_text(encoding="utf-8")
        cls.h3 = GUIDE_H3.read_text(encoding="utf-8")
        cls.guide = GUIDE_25.read_text(encoding="utf-8")

    def rate_rows(self):
        """`{rate: models cell}` for every row of the credits/s table."""
        table = re.search(r"^\| Credits/s \| Models \|\n\|[-| ]+\|\n((?:\|.*\|\n)+)",
                          self.skill, re.M)
        self.assertIsNotNone(table, "could not find the credits/s table in SKILL.md")
        rows = {}
        for line in table.group(1).strip().splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            self.assertEqual(len(cells), 2, msg=f"malformed rate row: {line!r}")
            rows[float(cells[0])] = cells[1]
        return rows

    def documented_video_reference_rate(self):
        """The 720p video reference rate, read out of the model guide."""
        m = re.search(r"drops from 6\.5 to \*\*(\d+(?:\.\d+)?)\s+credits/s at 720p\*\*", self.guide)
        self.assertIsNotNone(m, "could not read the video reference rate out of seedance-2-5.md")
        return float(m.group(1))

    def test_the_selection_table_files_seedance_under_the_cheaper_rate(self):
        rate, rows = self.documented_video_reference_rate(), self.rate_rows()
        self.assertIn(rate, rows,
                      msg=f"SKILL.md has no {rate}/s row to file the cheaper tier under")
        self.assertIn("seedance2.5", rows[rate],
                      msg=f"the {rate}/s row does not name seedance2.5: {rows[rate]!r}")

    def test_the_headline_row_no_longer_reads_as_the_only_price(self):
        rows = self.rate_rows()
        self.assertIn(6.5, rows, msg="the 6.5/s row is gone from SKILL.md")
        self.assertIn("seedance2.5", rows[6.5])
        self.assertIn("t2v", rows[6.5],
                      msg=f"the 6.5/s row still reads as the model's only price: {rows[6.5]!r}")

    def test_level_with_h3_and_below_flux_are_arithmetic_not_assertion(self):
        """The guide's ranking claim is only true while this table backs it."""
        rate, rows = self.documented_video_reference_rate(), self.rate_rows()
        for claim in ("level with `h3`", "below `flux-video`"):
            self.assertIn(claim, self.guide.replace("\n", " "),
                          msg=f"seedance-2-5.md no longer claims it lands {claim}")
        h3_rate = next((r for r, cell in rows.items() if "`h3`" in cell), None)
        flux_rate = next((r for r, cell in rows.items() if "`flux-video`" in cell), None)
        self.assertEqual(h3_rate, rate, msg=f"h3 is {h3_rate}/s, so 'level with h3' is wrong")
        self.assertGreater(flux_rate, rate,
                           msg=f"flux-video is {flux_rate}/s, so 'below flux-video' is wrong")

    def test_the_two_tier_note_states_both_resolutions(self):
        """Both discounted rates, and both are read out of the model guide."""
        m = re.search(r"and from 3 to \*\*(\d+(?:\.\d+)?) credits/s at 480p\*\*", self.guide)
        self.assertIsNotNone(m, "could not read the 480p video reference rate out of the guide")
        at_720, at_480 = self.documented_video_reference_rate(), float(m.group(1))
        note = re.search(r"`seedance2\.5` is the only model here with two tiers.*?`seedance-2-5\.md`",
                         self.skill, re.S)
        self.assertIsNotNone(note, "SKILL.md has no two-tier note under the table")
        self.assertIn(f"**{at_720:.1f}/s at 720p and {at_480:.1f} at 480p**", note.group(0),
                      msg="the two-tier note does not state the guide's measured rates")

    def test_the_overstatement_figure_is_the_arithmetic_difference(self):
        """Same sum in both files, derived here so neither can drift alone."""
        dur = generate.VIDEO_DURATIONS["seedance_2_5"]["max"]
        gap = (6.5 - self.documented_video_reference_rate()) * dur
        self.assertIn(f"overstate a {dur} s job by {gap:g} credits", self.skill)
        self.assertIn(f"overstates a {dur} s job by {gap:g} credits", self.guide)

    def test_the_head_to_head_table_carries_the_qualifier(self):
        """`minimax-h3.md` prints the two rates directly against each other.

        Without the qualifier that table says h3 is 38 percent cheaper than
        seedance2.5 even for an edit, where the two are identical.
        """
        self.assertIn("| `seedance2.5` | 6.5 | | **`h3`** | **4.0** |", self.h3)
        self.assertIn("exactly level with H3", self.h3)
        self.assertIn("only for a plain\n`t2v` roll", self.h3)


if __name__ == "__main__":
    unittest.main()
