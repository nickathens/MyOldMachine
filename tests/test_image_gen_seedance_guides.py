"""Consistency guards between the Seedance prompt guides and the wrapper.

The two Seedance guides make numeric and parameter claims that only stay true
while `generate.py`'s catalog agrees with them. Nothing else checks that pairing:
`test_image_gen.py` pins the catalog against the live API, and the routing tests
pin guide *filenames*, but a guide that documents a 5s floor against a wrapper
that allows 4 passes both.

Every claim here was measured against `higgsfield generate cost` on CLI 1.1.23:
the Seedance 2.5 numbers on 2026-08-19 and re-verified 2026-08-23, the rest on
2026-08-09. Each test that reads guide text asserts its anchor was found, so a
reformat fails loudly instead of quietly matching nothing.

The catalogue drifts server side while these tables are written by hand, and it
has drifted twice already: a 2026-08-10 revision of this file pinned a
video-reference discount tier and a 4000-character prompt ceiling that both
stopped existing. Guards that only pin what the guide currently says will pass a
stale guide, so the ones below that matter pin arithmetic (rate times duration)
and pin the retired claims **absent** from every surface that used to carry them.
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
GUIDE_FLUX = MODELS_DIR / "flux-3-video.md"


def rate_rows(case):
    """`{rate: models cell}` for every row of SKILL.md's credits/s table."""
    table = re.search(r"^\| Credits/s \| Models \|\n\|[-| ]+\|\n((?:\|.*\|\n)+)",
                      SKILL_MD.read_text(encoding="utf-8"), re.M)
    case.assertIsNotNone(table, "could not find the credits/s table in SKILL.md")
    rows = {}
    for line in table.group(1).strip().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        case.assertEqual(len(cells), 2, msg=f"malformed rate row: {line!r}")
        rows[float(cells[0])] = cells[1]
    return rows


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
        self.assertIn("duration: Input should be greater than or equal to 4", self.text)

    def test_documented_resolutions_match_model_params(self):
        # 1080p went live between 2026-08-10 and 2026-08-19 and the old row said
        # it did not exist, so the wrapper hid a resolution the API accepts.
        params = generate.MODEL_PARAMS["seedance_2_5"]
        self.assertEqual(params["resolution"]["options"], ["480p", "720p", "1080p"])
        row = re.search(r"^\| Resolution \|.*\|\s*\*\*480p, 720p, 1080p\*\*\. 4K rejected",
                        self.text, re.M)
        self.assertIsNotNone(row, "could not find the Resolution row in the hard specs table")
        self.assertIn("Invalid values: resolution=4k (allowed: 480p,720p,1080p)", self.text,
                      msg="the guide no longer quotes the measured 4K rejection")

    def test_bitrate_mode_is_offered_again(self):
        # Rejected as an unknown param on 2026-08-10, back in the schema by
        # 2026-08-19 and still there on 2026-08-23, proven by an out-of-enum
        # rejection rather than by the schema listing it. The wrapper's table is
        # what the Mini App renders, so a param the API takes and the wrapper
        # hides is a capability nobody can reach.
        params = generate.MODEL_PARAMS["seedance_2_5"]
        self.assertIn("bitrate_mode", params, "bitrate_mode is live again")
        self.assertEqual(params["bitrate_mode"]["options"], ["standard", "high"])
        self.assertRegex(self.text, r"bitrate_mode\s+standard, high")
        self.assertIn("Invalid values: bitrate_mode=bogus (allowed: standard,high)", self.text)

    def test_the_prompt_length_row_no_longer_states_a_ceiling(self):
        # 4000 characters was a hard submit-time rejection on 2026-08-10 and is
        # not enforced now. The row is the first thing read, so a stale ceiling
        # there makes people cut good prompts in half.
        row = re.search(r"^\| Prompt length \|.*$", self.text, re.M)
        self.assertIsNotNone(row, "could not find the Prompt length row in the hard specs table")
        self.assertIn("no ceiling found", row.group(0))
        self.assertNotIn("4000 characters, hard", self.text)


class TestSeedance20HabitsThatDoNotCarry(unittest.TestCase):
    """The habits that do not transfer from 2.0, checked in both directions.

    Absent-on-2.5 alone would pass for a param that exists on neither model,
    which makes the warning noise rather than a trap; present-on-2.0 is what
    proves the habit is real.

    `bitrate_mode` used to be in this list and is deliberately no longer: it was
    rejected as an unknown param on 2026-08-10 and is a real 2.5 param now, so it
    is asserted **present on both** instead. Leaving it in the rejected list
    would teach a limit that no longer exists.
    """

    @classmethod
    def setUpClass(cls):
        cls.text = GUIDE_25.read_text(encoding="utf-8")
        cls.p20 = generate.MODEL_PARAMS["seedance_2_0"]
        cls.p25 = generate.MODEL_PARAMS["seedance_2_5"]

    def test_rejected_params_are_absent_from_2_5(self):
        for param in ("genre",):
            self.assertNotIn(param, self.p25, msg=f"{param} is documented as rejected on 2.5")

    def test_rejected_params_are_real_2_0_params(self):
        for param in ("genre",):
            self.assertIn(param, self.p20, msg=f"{param} must exist on 2.0 or the warning is noise")

    def test_bitrate_mode_is_no_longer_in_the_rejected_set(self):
        for params in (self.p20, self.p25):
            self.assertIn("bitrate_mode", params)
        self.assertNotIn("Unknown params: bitrate_mode", self.text,
                         msg="the guide still teaches bitrate_mode as rejected on 2.5")

    def test_mode_means_capability_on_2_5_and_a_speed_tier_on_2_0(self):
        self.assertEqual(sorted(self.p20["mode"]["options"]), ["fast", "std"])
        self.assertEqual(self.p25["mode"]["options"],
                         ["t2v", "omni_reference", "video_edit", "video_extension"])
        for tier in ("fast", "std"):
            self.assertNotIn(tier, self.p25["mode"]["options"],
                             msg="a 2.0 speed tier leaked into 2.5's mode enum")

    def test_the_surviving_rejections_are_quoted_verbatim(self):
        for line in ("Unknown params: genre", "Invalid values: mode=fast"):
            self.assertIn(line, self.text, msg=f"missing the measured rejection: {line}")

    def test_the_reference_ceiling_is_the_2_5_number_not_the_2_0_one(self):
        # 2.0 is 9 images / 12 files total; 2.5 is 30 images / 50 total. Carrying
        # the smaller ceiling silently caps what the model can be given.
        flat = self.text.replace("\n", " ")
        self.assertIn("up to 30 images, 10 video clips, 10 audio clips", flat)
        self.assertIn("Fifty files is the ceiling", flat)


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
        flat = GUIDE_25.read_text(encoding="utf-8").replace("\n", " ")
        self.assertIn("the wrapper's `--cost` lies about anything with media in it", flat)
        self.assertIn("never forwards the keyframe flags",
                      GUIDE_20.read_text(encoding="utf-8"))


class TestTheRetiredPromptCeiling(unittest.TestCase):
    """4000 characters was a hard rejection on 2026-08-10. It is not enforced now.

    Re-probed 2026-08-19 and again 2026-08-23: prompts of 4001, 8000 and 40000
    characters all quote without complaint. What that proves is narrower than it
    looks and the guide has to say so, because **an empty prompt also quotes
    without complaint** (32.5 credits at five seconds), so the quote path never
    looks at the prompt field at all. The validator also answers one error at a
    time, local enum checks ahead of server-side range checks: send
    `--resolution 4k` and `--duration 99` together and only the resolution error
    comes back.

    So the guard is two-sided. The retired ceiling must be gone from the guide,
    and the weaker claim that replaced it must carry its own limits rather than
    reading as proof about `generate create`.
    """

    @classmethod
    def setUpClass(cls):
        cls.text = GUIDE_25.read_text(encoding="utf-8")

    def test_the_retired_ceiling_is_not_still_taught(self):
        for dead in ("4000 characters, hard",
                     "prompt: String should have at most 4000 characters",
                     "4000\ncharacters is accepted, 4001 is rejected"):
            self.assertNotIn(dead, self.text, msg=f"the guide still teaches: {dead!r}")

    def test_the_lifted_limit_is_stated_with_the_probe_that_backs_it(self):
        flat = self.text.replace("\n", " ")
        self.assertIn("4001, 8000 and 40000 characters", flat)

    def test_the_claim_carries_its_own_limits(self):
        """A clean quote is not proof about the paid path, and the guide says so."""
        flat = self.text.replace("\n", " ")
        self.assertIn("an empty prompt also quotes without complaint", flat,
                      msg="the guide no longer records that the quote path skips the prompt field")
        self.assertIn("one error at a time", flat,
                      msg="the guide no longer records that the validator reports one error")
        self.assertIn("generate create", flat,
                      msg="the guide does not distinguish the quote path from the paid path")

    def test_no_byte_counting_command_survives_in_a_copyable_block(self):
        """`wc -c` may be named in prose as the wrong tool, never offered as a command.

        Prose can hedge; a fenced block is what gets copied and run. An earlier
        version of this guide shipped `tr '\\n' ' ' < prompt.txt | wc -c` as the
        prescribed count, and the `tr` was a no-op besides, since swapping a
        newline for a space leaves the byte count untouched. The ceiling it
        served is gone, but the block must not come back with the next revision.
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


class TestSeedance25PriceModel(unittest.TestCase):
    """One rate per resolution, in every mode **[2026-08-19, re-verified 2026-08-23]**.

    The 2026-08-10 revision of this file pinned a second, cheaper tier that any
    job carrying a video reference dropped into: 4.0 credits/s at 720p against
    6.5, and 2.0 at 480p against 3.0. **That tier no longer exists.** An
    `omni_reference` job carrying a clip quotes 32.5 for five seconds, to the
    cent what a plain `t2v` roll quotes. 480p itself fell from 3.0 to 2.5, and
    1080p appeared at 9.0.

    What differs between modes is which duration is billed, not the rate.
    `video_edit` ignores the duration passed and charges the source clip's own
    length with a four second floor: a 2 s clip quotes 26 whether 4 s or 30 s is
    asked for, an 8 s clip quotes 52 whether 5 s or 20 s is. Every other mode
    charges the duration requested. Both measurement rounds ran to an unchanged
    credit balance, so none of it cost anything.
    """

    RATES = {"480p": 2.5, "720p": 6.5, "1080p": 9.0}
    RETIRED_VREF_720, RETIRED_VREF_480 = 4.0, 2.0

    @classmethod
    def setUpClass(cls):
        cls.text = GUIDE_25.read_text(encoding="utf-8")

    def guide_rates(self):
        """`{resolution: credits/s}` read out of the guide rather than retyped."""
        table = re.search(
            r"^\| Resolution \| Credits per second \|\n\|[-| ]+\|\n((?:\|.*\|\n)+)",
            self.text, re.M)
        self.assertIsNotNone(table, "could not find the per second rate table")
        rows = {}
        for line in table.group(1).strip().splitlines():
            cells = [c.strip().replace("*", "") for c in line.strip().strip("|").split("|")]
            self.assertEqual(len(cells), 2, msg=f"malformed rate row: {line!r}")
            rows[cells[0]] = float(cells[1])
        return rows

    def test_the_guide_states_one_rate_per_resolution(self):
        self.assertEqual(self.guide_rates(), self.RATES)

    def test_the_rate_table_covers_every_resolution_the_wrapper_offers(self):
        offered = generate.MODEL_PARAMS["seedance_2_5"]["resolution"]["options"]
        self.assertEqual(sorted(self.guide_rates()), sorted(offered),
                         msg="a resolution the wrapper offers has no documented price")

    def test_the_price_grid_is_rate_times_duration(self):
        """Hand-typed, and it is what a job gets budgeted against."""
        table = re.search(
            r"^\| Duration \| 480p \| 720p \| 1080p \|\n(?:\|[-| ]+\|\n)((?:\|.*\|\n)+)",
            self.text, re.M)
        self.assertIsNotNone(table, "could not find the duration price grid")
        rates = self.guide_rates()
        rows = [r for r in table.group(1).strip().splitlines() if r.strip()]
        self.assertGreaterEqual(len(rows), 4, msg=f"only {len(rows)} priced rows")
        for row in rows:
            cells = [c.strip().replace("*", "") for c in row.strip().strip("|").split("|")]
            self.assertEqual(len(cells), 4, msg=f"malformed price row: {row!r}")
            seconds = float(cells[0].rstrip(" s"))
            for res, quoted in zip(("480p", "720p", "1080p"), cells[1:]):
                self.assertAlmostEqual(
                    float(quoted), rates[res] * seconds, places=6,
                    msg=f"{seconds} s at {res}: {quoted} is not {rates[res]}/s")

    def test_the_grid_prices_both_ends_of_the_legal_range(self):
        dur = generate.VIDEO_DURATIONS["seedance_2_5"]
        seconds = {int(s) for s in re.findall(r"^\| (\d+) s \| [\d.]+ \| [\d.]+ \| [\d.]+ \|$",
                                              self.text, re.M)}
        self.assertIn(dur["min"], seconds, "the grid does not price the shortest legal roll")
        self.assertIn(dur["max"], seconds, "the grid does not price the longest legal roll")

    def test_video_edit_bills_the_source_clip_not_the_request(self):
        # The one rule that can move a budget fivefold, so it may not live only
        # in a paragraph: an 8 s source is 52 at 720p where a 30 s request reads
        # 195.
        flat = self.text.replace("\n", " ")
        self.assertIn("`video_edit`: `duration` is ignored", flat)
        self.assertIn("length of the source clip", flat)
        self.assertIn("four second floor", flat)

    def test_the_billing_examples_are_the_rate_times_the_billed_length(self):
        """The worked figures recomputed, so a transposed digit cannot survive."""
        rate = self.RATES["720p"]
        floor = generate.VIDEO_DURATIONS["seedance_2_5"]["min"]
        table = re.search(
            r"^\| Source clip \| Requested duration \| Quote at 720p \|\n(?:\|[-| ]+\|\n)((?:\|.*\|\n)+)",
            self.text, re.M)
        self.assertIsNotNone(table, "could not find the video_edit billing table")
        rows = [r for r in table.group(1).strip().splitlines() if r.strip()]
        self.assertGreaterEqual(len(rows), 2, "one row proves nothing about ignoring the request")
        sources = set()
        for row in rows:
            cells = [c.strip().replace("*", "") for c in row.strip().strip("|").split("|")]
            self.assertEqual(len(cells), 3, msg=f"malformed billing row: {row!r}")
            source = float(re.match(r"([\d.]+)", cells[0]).group(1))
            quoted = float(re.match(r"([\d.]+)", cells[2]).group(1))
            self.assertAlmostEqual(
                quoted, rate * max(source, floor), places=6,
                msg=f"{cells[0]} source quoted {quoted}, not {rate}/s x max(source, {floor})")
            sources.add(source)
        self.assertTrue(any(s < floor for s in sources),
                        msg=f"no row is under the {floor} s floor, so the floor is untested")
        self.assertTrue(any(s > floor for s in sources),
                        msg="every row sits on the floor, so 'bills the source' is untested")

    def test_no_surface_still_advertises_the_retired_discount(self):
        # The discount was real on 2026-08-10 and was quoted on four surfaces. A
        # correction that lands on one leaves the other three quietly lying, and
        # SKILL.md is the surface a model is picked from before any guide opens.
        dead_rate = f"**{self.RETIRED_VREF_720:g}/s at 720p and {self.RETIRED_VREF_480:g} at 480p**"
        for path in (GUIDE_25, GUIDE_H3, SKILL_MD, GUIDE_FLUX):
            body = path.read_text(encoding="utf-8")
            self.assertNotIn("exactly level with H3", body, msg=path.name)
            self.assertNotIn(dead_rate, body, msg=path.name)
        rows = rate_rows(self)
        self.assertNotIn("seedance2.5", rows.get(self.RETIRED_VREF_720, ""),
                         msg="SKILL.md still files seedance2.5 under the retired 4.0/s row")

    def test_audio_is_documented_as_free(self):
        # Measured on/off in both resolutions and all four modes: identical quote.
        self.assertIn("Turning it off costs exactly the same", self.text)


class TestImplicitModeTrap(unittest.TestCase):
    """`t2v` rejects references only when `--mode` is passed explicitly.

    The rule is written against the `mode` parameter and the CLI omits the
    parameter when the flag is absent, so the default path never evaluates it:
    a video reference is accepted at quote and clears validation at submit, in a
    mode nobody chose. This is the dangerous shape, because it succeeds rather
    than failing, and it survived both re-probes. Re-verified 2026-08-23:
    `--mode t2v` with a clip is refused with "mode 't2v' does not accept
    reference media", and the same call with the flag dropped quotes 32.5.
    """

    @classmethod
    def setUpClass(cls):
        cls.text = GUIDE_25.read_text(encoding="utf-8")

    def test_the_trap_has_its_own_section(self):
        self.assertIn("#### The silent trap", self.text)
        flat = self.text.replace("\n", " ")
        self.assertIn("the CLI omits the parameter entirely when you do not pass the flag", flat)

    def test_the_rejection_it_bypasses_is_quoted_verbatim(self):
        self.assertIn("mode 't2v' does not accept reference media", self.text)

    def test_the_cheatsheet_carries_it(self):
        # A reader who opens only the one-page summary still has to be told.
        self.assertIn("**Always pass `--mode` explicitly.**", self.text)

    def test_the_wrapper_still_cannot_quote_a_video_reference(self):
        """The guide tells you to skip `generate.py --cost`; that must stay true."""
        self.assertNotIn("video_references", inspect.signature(generate.estimate_cost).parameters)
        flat = self.text.replace("\n", " ")
        self.assertIn("never forwards `--start-image`, `--end-image` or video references", flat)

    def test_resolution_is_still_dropped_for_video_quotes(self):
        src = inspect.getsource(generate.main)
        self.assertIn('resolution=args.resolution if kind == "image" else None', src,
                      msg="main() now forwards resolution for video; drop the guide's warning")
        flat = self.text.replace("\n", " ")
        self.assertIn("it drops `--resolution` for video models", flat)

    def test_the_documented_escape_hatch_is_the_wrapper_flag_that_exists(self):
        """The guide tells you to push resolution through `--extra`, so it must.

        `--resolution` on the wrapper is the image side and rejects `480p` in
        argparse, before anything reaches the API.
        """
        self.assertIn('--extra \'{"resolution":"480p"}\'', self.text)
        self.assertIn("--extra", inspect.getsource(generate.main))


class TestTheSelectionSurfacesCarryTheCorrection(unittest.TestCase):
    """`SKILL.md`'s credits/s table is where a model is picked, before any guide opens.

    A correction that lands only in `seedance-2-5.md` leaves the decision it was
    meant to change still reading the superseded ranking. Three surfaces carried
    the retired discount and all three are pinned here: the selection table, the
    note under it, and the head to head in `minimax-h3.md`.
    """

    @classmethod
    def setUpClass(cls):
        cls.skill = SKILL_MD.read_text(encoding="utf-8")
        cls.h3 = GUIDE_H3.read_text(encoding="utf-8")
        cls.guide = GUIDE_25.read_text(encoding="utf-8")

    def guide_720_rate(self):
        """The 720p rate, read out of the model guide rather than retyped."""
        m = re.search(r"^\| 720p \| \*\*([\d.]+)\*\* \|$", self.guide, re.M)
        self.assertIsNotNone(m, "could not read the 720p rate out of seedance-2-5.md")
        return float(m.group(1))

    def test_the_selection_table_files_seedance_under_the_measured_rate(self):
        rate, rows = self.guide_720_rate(), rate_rows(self)
        self.assertIn(rate, rows, msg=f"SKILL.md has no {rate}/s row")
        self.assertIn("seedance2.5", rows[rate], msg=f"the {rate}/s row: {rows[rate]!r}")

    def test_the_headline_row_states_the_other_two_resolutions(self):
        """6.5 alone is a trap now that 480p and 1080p are priced differently."""
        rows = rate_rows(self)
        cell = rows[self.guide_720_rate()]
        for res in ("480p", "1080p"):
            self.assertIn(res, cell, msg=f"the row does not price {res}: {cell!r}")

    def test_the_row_no_longer_reads_as_t2v_only(self):
        # "plain `t2v` only" was true while the discount existed. It is not now,
        # and left there it sends a reader looking for a tier that is gone.
        rows = rate_rows(self)
        self.assertNotIn("t2v", rows[self.guide_720_rate()],
                         msg="the 6.5/s row still qualifies itself as t2v only")

    def test_the_video_edit_billing_rule_reaches_the_selection_surface(self):
        flat = self.skill.replace("\n", " ")
        self.assertIn("`video_edit` ignores the `duration` you pass", flat)
        self.assertIn("source clip's own length", flat)

    def test_the_note_states_the_measured_rates(self):
        """Every rate in the note is the one the model guide states."""
        note = re.search(r"`seedance2\.5` \*\*used to\*\* drop.*?`seedance-2-5\.md`",
                         self.skill, re.S)
        self.assertIsNotNone(note, "SKILL.md has no seedance correction note under the table")
        body = note.group(0)
        for res, rate in (("480p", 2.5), ("720p", 6.5), ("1080p", 9.0)):
            self.assertIn(f"{rate:g}", body, msg=f"the note omits the {res} rate")

    def test_the_worked_example_is_the_rate_times_the_source(self):
        """The 8 s / 52 pairing, recomputed so a typo cannot survive."""
        rate = self.guide_720_rate()
        m = re.search(r"an (\d+) s clip is ([\d.]+) at 720p", self.skill)
        self.assertIsNotNone(m, "SKILL.md dropped the worked video_edit figure")
        source, quoted = int(m.group(1)), float(m.group(2))
        self.assertAlmostEqual(quoted, rate * source, places=6)

    def test_the_head_to_head_records_that_the_gap_no_longer_closes(self):
        """`minimax-h3.md` prints the two rates directly against each other.

        With the old qualifier it said the two tie for an edit. They do not.
        """
        self.assertIn("| `seedance2.5` | 6.5 | | **`h3`** | **4.0** |", self.h3)
        flat = self.h3.replace("\n", " ")
        self.assertIn("That discount is gone.", flat)
        self.assertNotIn("exactly level with H3", self.h3)


class TestFluxVideoCarriesTheOppositeTier(unittest.TestCase):
    """`flux-video` re-prices on the same trigger, upward **[live 2026-08-10]**.

    Measured against the account the same day as the `seedance2.5` tier: a
    `video_references` file takes Flux 3 from 5.5 to 13 credits/s at 720p and
    from 9 to 17 at 1080p, exactly linear at both, while an `image_references`
    file and `generate_audio` move nothing. It is the only other model on the
    route that re-prices on an attached file, so the selection table's note may
    not describe the trigger as belonging to one model. This one hurts in the
    dangerous direction: the table rate understates a continuation, and the
    model has no `mode` param to make the switch visible.
    """

    RATES = {"720p": (5.5, 13.0), "1080p": (9.0, 17.0)}
    DURATIONS = (5, 10, 15, 20)

    @classmethod
    def setUpClass(cls):
        cls.skill = SKILL_MD.read_text(encoding="utf-8")
        cls.flux = (MODELS_DIR / "flux-3-video.md").read_text(encoding="utf-8")

    def test_the_selection_table_qualifies_the_plain_rate(self):
        """5.5/s is only true with no clip attached, so the row has to say so."""
        row = re.search(r"^\| 5\.5 \| (.*) \|$", self.skill, re.M)
        self.assertIsNotNone(row, "the 5.5/s row is gone from SKILL.md")
        self.assertIn("`flux-video`", row.group(1))
        self.assertIn("no video reference", row.group(1),
                      msg=f"the 5.5/s row still reads as the only price: {row.group(1)!r}")

    def test_the_note_names_flux_as_the_one_that_re_prices(self):
        """seedance2.5 left this list on 2026-08-19; flux-video is what remains.

        Both directions matter. Dropping flux-video would hide the only live
        trigger left, and leaving seedance2.5 in would teach a tier that is gone,
        so the note has to name flux-video as the mechanism and seedance2.5 only
        as the correction.
        """
        note = re.search(r"One model re-prices when a `video_references` file is attached"
                         r".*?only a video does\.", self.skill, re.S)
        self.assertIsNotNone(note, "SKILL.md has no video-reference tier note")
        body = note.group(0)
        self.assertIn("`flux-video` climbs to", body, "the note dropped the live trigger")
        self.assertIn("`seedance2.5` **used to**", body,
                      "the note does not record that seedance2.5 left this list")
        self.assertNotIn("is the only model here with two tiers", self.skill)

    def test_the_note_states_the_measured_upward_rates(self):
        note = re.search(r"`flux-video` climbs to \*\*(\d+(?:\.\d+)?)/s at 720p "
                         r"and (\d+(?:\.\d+)?) at 1080p\*\*, from (\d+(?:\.\d+)?) and (\d+(?:\.\d+)?)",
                         self.skill)
        self.assertIsNotNone(note, "SKILL.md does not state flux-video's video reference rates")
        at_720, at_1080, plain_720, plain_1080 = (float(g) for g in note.groups())
        self.assertEqual((plain_720, at_720), self.RATES["720p"])
        self.assertEqual((plain_1080, at_1080), self.RATES["1080p"])

    def test_the_multiplier_in_the_note_is_the_arithmetic_one(self):
        plain, loaded = self.RATES["720p"]
        self.assertIn(f"costs {loaded / plain:.1f}x what this table quotes", self.skill)

    def test_the_worked_example_is_the_rate_times_the_maximum(self):
        """Both figures derived from the rates, so a typo cannot survive."""
        longest = max(self.DURATIONS)
        self.assertEqual(longest, generate.VIDEO_DURATIONS["flux_3_video"]["max"])
        plain, loaded = self.RATES["720p"]
        self.assertIn(f"continuation at {plain * longest:g} and the bill is {loaded * longest:g}",
                      self.skill)
        self.assertIn(f"understate a {longest} s piece by {(loaded - plain) * longest:g} credits",
                      self.flux)

    def test_the_guide_price_table_is_linear_at_both_tiers(self):
        """Every cell recomputed from the rates rather than read as prose."""
        rows = re.findall(r"^\| (\d+) s \| ([\d.]+) \| \*\*([\d.]+)\*\* \| ([\d.]+) \| \*\*([\d.]+)\*\* \|$",
                          self.flux, re.M)
        self.assertEqual(len(rows), len(self.DURATIONS), msg=f"expected one row per duration: {rows}")
        for row, dur in zip(rows, self.DURATIONS):
            self.assertEqual(int(row[0]), dur)
            expected = (self.RATES["720p"][0] * dur, self.RATES["720p"][1] * dur,
                        self.RATES["1080p"][0] * dur, self.RATES["1080p"][1] * dur)
            self.assertEqual(tuple(float(c) for c in row[1:]), expected,
                             msg=f"the {dur} s row is not rate times duration")

    def test_the_guide_headline_row_points_at_the_second_tier(self):
        """The spec table is the first thing read; 5.5/9 alone is a trap there."""
        cost_row = re.search(r"^\| Cost \| n/a \| (.*) \|$", self.flux, re.M)
        self.assertIsNotNone(cost_row, "the flux guide has no Cost spec row")
        self.assertIn("video reference tier", cost_row.group(1),
                      msg=f"the Cost row does not point at the second tier: {cost_row.group(1)!r}")

    def test_the_guide_rules_out_the_alternative_explanations(self):
        """The controls that prove the video file is what moves the price."""
        for control in ("no `mode` param", "`image_references` file leaves the quote untouched",
                        "`generate_audio` makes no difference"):
            self.assertIn(control, self.flux, msg=f"the guide does not record the control: {control}")

    def test_the_two_guides_cross_reference_the_inverted_ranking(self):
        """The inversion survived the correction; the magnitude halved.

        It used to be 13 against Seedance's discounted 4. Seedance no longer
        discounts, so it is 13 against 6.5: still inverted, half as far. Both
        halves are recomputed from the rate the other guide states, so neither
        file can drift alone.
        """
        seedance = float(re.search(r"^\| 720p \| \*\*([\d.]+)\*\* \|$",
                                   (MODELS_DIR / "seedance-2-5.md").read_text(encoding="utf-8"),
                                   re.M).group(1))
        plain, loaded = self.RATES["720p"]
        self.assertGreater(loaded, seedance,
                           msg="flux is no longer the dearer of the two on a continuation")
        self.assertLess(plain, seedance,
                        msg="flux is no longer the cheaper of the two on a plain roll")
        flat = self.flux.replace("\n", " ")
        self.assertIn("seedance-2-5.md", self.flux)
        self.assertIn(f"Flux 3 is {loaded:g}/s", flat,
                      msg="the flux guide no longer states the loaded rate in the comparison")
        self.assertIn(f"against Seedance's unchanged {seedance:g}", flat,
                      msg="the flux guide still compares against the retired discounted rate")


if __name__ == "__main__":
    unittest.main()
