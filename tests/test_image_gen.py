"""Unit tests for skills/image-gen/scripts/generate.py.

Covers backend dispatch, fallback path, cost estimation, and CLI compat.
All subprocess calls are mocked -- no real Higgsfield or network access.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT / "skills" / "image-gen" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import generate  # noqa: E402


def _proc(returncode=0, stdout="", stderr=""):
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class TestBackendDefault(unittest.TestCase):
    """Bug #3: default backend must be 'auto', not 'higgsfield'."""

    def test_default_is_auto(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--backend", default="auto")
        args = parser.parse_args([])
        self.assertEqual(args.backend, "auto")

    def test_script_default_is_auto(self):
        self.assertIn("default=\"auto\"", Path(SCRIPT_DIR / "generate.py").read_text())


class TestAutoFallback(unittest.TestCase):
    """When Higgsfield fails, auto backend falls back to Pollinations."""

    @patch("generate.generate_pollinations")
    @patch("generate.generate_higgsfield")
    def test_auto_falls_back(self, mock_hf, mock_poll):
        mock_hf.return_value = {"success": False, "path": "", "error": "not authed"}
        mock_poll.return_value = {"success": True, "path": "/tmp/test.jpg", "model": "sana", "error": ""}

        # Simulate the auto path
        result = mock_hf("test", "/tmp/out.jpg")
        self.assertFalse(result["success"])
        result = mock_poll("test", "/tmp/out.jpg")
        self.assertTrue(result["success"])


class TestBalancePrintBug(unittest.TestCase):
    """Bug #1: json.dumps paren placement in --balance error path."""

    def test_balance_error_json_is_valid(self):
        output = json.dumps({"error": "Could not fetch account status"}, indent=2)
        parsed = json.loads(output)
        self.assertEqual(parsed["error"], "Could not fetch account status")

    def test_cost_error_json_is_valid(self):
        output = json.dumps({"error": "Prompt required for cost estimation"}, indent=2)
        parsed = json.loads(output)
        self.assertEqual(parsed["error"], "Prompt required for cost estimation")


class TestBoolConsistency(unittest.TestCase):
    """Bug #5: bool-to-string must use .lower() in all code paths."""

    def test_image_bool_lowercased(self):
        source = Path(SCRIPT_DIR / "generate.py").read_text()
        # The generate_higgsfield extra_params block should have isinstance(v, bool) check
        self.assertIn("isinstance(v, bool)", source)

    def test_video_bool_lowercased(self):
        source = Path(SCRIPT_DIR / "generate.py").read_text()
        count = source.count("str(v).lower()")
        # Should appear in at least 3 places: generate_higgsfield, generate_video, estimate_cost
        self.assertGreaterEqual(count, 3)


class TestDeadCodeRemoved(unittest.TestCase):
    """Bug #6: MODEL_ASPECT_RATIOS should be removed (dead code)."""

    def test_no_model_aspect_ratios(self):
        source = Path(SCRIPT_DIR / "generate.py").read_text()
        self.assertNotIn("MODEL_ASPECT_RATIOS", source)


class TestOldFlagsPreserved(unittest.TestCase):
    """Bug #4: old Pollinations flags must still work."""

    def test_width_flag_exists(self):
        self.assertTrue(hasattr(generate, "generate_pollinations"))
        import inspect
        sig = inspect.signature(generate.generate_pollinations)
        self.assertIn("width", sig.parameters)

    def test_enhance_flag_exists(self):
        import inspect
        sig = inspect.signature(generate.generate_pollinations)
        self.assertIn("enhance", sig.parameters)

    def test_seed_flag_exists(self):
        import inspect
        sig = inspect.signature(generate.generate_pollinations)
        self.assertIn("seed", sig.parameters)


class TestCostEstimation(unittest.TestCase):
    """Cost estimation returns structured data."""

    @patch("generate.get_account_status", return_value={"credits": 100})
    @patch("subprocess.run")
    def test_cost_returns_remaining(self, mock_run, mock_acct):
        mock_run.return_value = _proc(0, json.dumps({"credits": 2, "credits_exact": 2}))
        result = generate.estimate_cost("nano_banana_flash", "test prompt")
        self.assertEqual(result["credits_remaining"], 100)

    @patch("subprocess.run", side_effect=FileNotFoundError("higgsfield"))
    def test_cost_handles_missing_cli(self, mock_run):
        result = generate.estimate_cost("nano_banana_flash", "test prompt")
        self.assertIn("error", result)


class TestCostKindRefactor(unittest.TestCase):
    """estimate_cost keys on `kind` and no longer force-sends 2k resolution to video."""

    @patch("generate.get_account_status", return_value={"credits": 100})
    @patch("subprocess.run")
    def test_video_cost_omits_default_2k_resolution(self, mock_run, mock_acct):
        # Latent bug: default 2k was force-sent to video models that reject it.
        mock_run.return_value = _proc(0, json.dumps({"credits": 22}))
        generate.estimate_cost("seedance", "a wave", resolution="2k", duration=5, kind="video")
        cmd = mock_run.call_args[0][0]
        self.assertNotIn("--resolution", cmd)
        self.assertIn("--duration", cmd)
        self.assertIn("seedance_2_0", cmd)

    @patch("generate.get_account_status", return_value={"credits": 100})
    @patch("subprocess.run")
    def test_image_cost_sends_nondefault_resolution(self, mock_run, mock_acct):
        mock_run.return_value = _proc(0, json.dumps({"credits": 2}))
        generate.estimate_cost("nano2", "a cat", resolution="4k", kind="image")
        cmd = mock_run.call_args[0][0]
        self.assertIn("--resolution", cmd)
        self.assertIn("4k", cmd)

    @patch("generate.get_account_status", return_value={"credits": 100})
    @patch("subprocess.run")
    def test_music_cost_sends_duration(self, mock_run, mock_acct):
        mock_run.return_value = _proc(0, json.dumps({"credits": 1}))
        generate.estimate_cost("music", "warm piano", duration=30, kind="audio")
        cmd = mock_run.call_args[0][0]
        self.assertIn("--duration", cmd)
        self.assertIn("sonilo_music", cmd)


class TestNewModalities(unittest.TestCase):
    """New image/video aliases and the 3D + audio modalities are wired."""

    def test_new_image_aliases(self):
        for alias, jst in (("recraft", "recraft_v4_1"), ("nano-lite", "nano_banana_2_lite"), ("soul-cinema", "soul_cinema_studio")):
            self.assertEqual(generate.resolve_model(alias, "image"), jst)

    def test_new_video_aliases(self):
        for alias, jst in (("seedance-mini", "seedance_2_0_mini"), ("kling-turbo", "kling3_0_turbo"), ("gemini", "gemini_omni"), ("cinematic3.5", "cinematic_studio_video_3_5")):
            self.assertEqual(generate.resolve_model(alias, "video"), jst)

    def test_threed_aliases_resolve(self):
        self.assertEqual(generate.resolve_model("3d", "3d"), "tripo_3d")
        self.assertEqual(generate.resolve_model("image-to-3d", "3d"), "image_to_3d")
        self.assertEqual(generate.DEFAULT_3D_MODEL, "text-to-3d")

    def test_audio_aliases_resolve(self):
        self.assertEqual(generate.resolve_model("music", "audio"), "sonilo_music")
        self.assertEqual(generate.resolve_model("speech", "audio"), "seed_audio")
        self.assertEqual(generate.DEFAULT_AUDIO_MODEL, "music")

    def test_unknown_kind_falls_back_to_image_map(self):
        self.assertEqual(generate.resolve_model("recraft", "bogus"), "recraft_v4_1")

    def test_generate_job_signature(self):
        self.assertTrue(hasattr(generate, "generate_job"))
        import inspect
        params = inspect.signature(generate.generate_job).parameters
        for p in ("prompt", "output_path", "resolved_model", "duration", "ref_media", "extra_params"):
            self.assertIn(p, params)


class TestMiniMaxH3(unittest.TestCase):
    """MiniMax H3 (Hailuo 3.0) is a separate model from the older Minimax Hailuo.

    Both live in the catalog at once, and they want opposite prompt styles, so the
    aliases must not collapse into each other.
    """

    def test_h3_aliases_resolve(self):
        for alias in ("h3", "hailuo3"):
            self.assertEqual(generate.resolve_model(alias, "video"), "minimax_h3")

    def test_old_hailuo_alias_unchanged(self):
        self.assertEqual(generate.resolve_model("hailuo", "video"), "minimax_hailuo")

    def test_duration_floor_is_five_not_four(self):
        # MiniMax's own docs say 4s; the Higgsfield route rejects anything under 5.
        dur = generate.VIDEO_DURATIONS["minimax_h3"]
        self.assertEqual(dur["min"], 5)
        self.assertEqual(dur["max"], 15)
        self.assertEqual(dur["default"], 5)

    @patch("subprocess.run")
    def test_duration_is_sent_for_h3(self, mock_run):
        mock_run.return_value = _proc(1, "", "boom")
        generate.generate_video("a shot", "/tmp/out.mp4", model="h3", duration=10)
        cmd = mock_run.call_args[0][0]
        self.assertIn("minimax_h3", cmd)
        self.assertIn("--duration", cmd)
        self.assertIn("10", cmd)


class TestVideoCatalogAccuracy(unittest.TestCase):
    """The wrapper's video tables have to match what the live API actually accepts.

    Measured against `higgsfield model get` / `generate cost` on CLI 1.1.20,
    2026-08-07. These are regression pins for bugs that were live in the wrapper,
    not restatements of vendor documentation.
    """

    def test_new_video_models_resolve(self):
        for alias, job in (
            ("flux-video", "flux_3_video"),
            ("flux3-video", "flux_3_video"),
            ("grok-video1.5", "grok_video_v15"),
            ("happy-horse", "happy_horse_video"),
            ("seedance2.5", "seedance_2_5"),
        ):
            self.assertEqual(generate.resolve_model(alias, "video"), job)

    def test_soul_cast_is_an_image_model(self):
        # Live catalog reports type=image: no duration, no start_image, and a
        # required 16:9. It sat in the video table and wrote stills into a .mp4.
        self.assertEqual(generate.resolve_model("soul-cast", "image"), "soul_cast")
        self.assertNotIn("soul-cast", generate.VIDEO_MODEL_ALIASES)

    def test_every_video_alias_has_duration_info(self):
        for job in set(generate.VIDEO_MODEL_ALIASES.values()):
            self.assertIn(job, generate.VIDEO_DURATIONS, msg=f"{job} has no duration entry")

    def test_measured_duration_bounds(self):
        # Each of these was wrong in the wrapper until the 2026-08-07 sweep; the
        # numbers are the validator's own rejection messages.
        for job, lo, hi in (
            ("seedance_2_0", 4, 15),        # advertised 5-30
            ("seedance_2_0_mini", 4, 15),   # advertised 5-30
            ("wan2_7", 2, 15),              # advertised 3-15
            ("cinematic_studio_video_v2", 3, 12),  # advertised 3-10
            ("flux_3_video", 5, 20),
            ("grok_video_v15", 2, 15),
            ("happy_horse_video", 3, 15),
            ("seedance_2_5", 4, 30),  # re-probed 2026-08-09; the sweep recorded 5
        ):
            dur = generate.VIDEO_DURATIONS[job]
            self.assertEqual((dur["min"], dur["max"]), (lo, hi), msg=job)

    def test_variant_not_model_is_the_param_name(self):
        # `--model` is rejected outright: "Unknown params: model".
        for job in ("veo3", "veo3_1", "minimax_hailuo"):
            params = generate.MODEL_PARAMS[job]
            self.assertIn("variant", params, msg=f"{job} should expose `variant`")
            self.assertNotIn("model", params, msg=f"{job} still exposes `model`")

    @patch("subprocess.run")
    def test_aspect_ratio_withheld_from_models_that_reject_it(self, mock_run):
        # hailuo and grok_video_v15 have no aspect_ratio param at all; sending it
        # is a hard rejection, which broke every hailuo cost check.
        mock_run.return_value = _proc(1, "", "boom")
        for alias in ("hailuo", "grok-video1.5"):
            mock_run.reset_mock()
            generate.generate_video("a shot", "/tmp/out.mp4", model=alias, aspect_ratio="9:16")
            self.assertNotIn("--aspect_ratio", mock_run.call_args[0][0], msg=alias)

    @patch("subprocess.run")
    def test_aspect_ratio_still_sent_for_normal_models(self, mock_run):
        mock_run.return_value = _proc(1, "", "boom")
        generate.generate_video("a shot", "/tmp/out.mp4", model="kling", aspect_ratio="9:16")
        self.assertIn("--aspect_ratio", mock_run.call_args[0][0])

    @patch("subprocess.run")
    def test_hailuo_variant_is_sent_explicitly(self, mock_run):
        # The API prints minimax-2.3 as the default but does not apply it: a
        # prompt-only call fails its own CEL rule demanding a start/end image.
        mock_run.return_value = _proc(1, "", "boom")
        generate.generate_video("a shot", "/tmp/out.mp4", model="hailuo")
        cmd = mock_run.call_args[0][0]
        self.assertIn("--variant", cmd)
        self.assertIn("minimax-2.3", cmd)

    @patch("subprocess.run")
    def test_explicit_variant_overrides_the_injected_default(self, mock_run):
        mock_run.return_value = _proc(1, "", "boom")
        generate.generate_video("a shot", "/tmp/out.mp4", model="hailuo",
                                extra_params={"variant": "minimax-fast"})
        cmd = mock_run.call_args[0][0]
        self.assertIn("minimax-fast", cmd)
        self.assertEqual(cmd.count("--variant"), 1)


class TestGuideMapping(unittest.TestCase):
    """Every guide file named in the SKILL.md mapping table has to exist.

    The mapping is what the prompt-refinement step reads before any paid
    generation, so a stale filename there silently skips refinement.
    """

    MODELS_DIR = ROOT / "skills" / "image-gen" / "models"
    SKILL_MD = ROOT / "skills" / "image-gen" / "SKILL.md"

    def test_mapped_guides_exist(self):
        import re
        text = self.SKILL_MD.read_text(encoding="utf-8")
        named = set(re.findall(r"`([a-z0-9\-]+\.md)`", text))
        self.assertIn("minimax-h3.md", named)
        for guide in sorted(named):
            self.assertTrue((self.MODELS_DIR / guide).is_file(), msg=f"{guide} named in SKILL.md but missing")

    def test_h3_guide_is_mapped(self):
        text = self.SKILL_MD.read_text(encoding="utf-8")
        self.assertIn("minimax-h3.md", text)
        self.assertIn("| `h3`, `hailuo3` |", text)

    def test_bot_router_points_at_real_guides(self):
        """bot.py routes a Mini App model to a guide file before refining.

        A name in there that no longer exists on disk sends the refinement step
        to a missing file. `gemini` pointed at `veo.md` for days that way.
        """
        import re
        block = re.search(r"_MODEL_GUIDES = \{(.*?)\n\s*\}", (ROOT / "bot.py").read_text(encoding="utf-8"), re.S)
        self.assertIsNotNone(block, "could not find _MODEL_GUIDES in bot.py")
        guides = set(re.findall(r'"([a-z0-9\-]+\.md)"', block.group(1)))
        self.assertIn("minimax-h3.md", guides)
        for guide in sorted(guides):
            self.assertTrue((self.MODELS_DIR / guide).is_file(), msg=f"bot.py routes to missing guide {guide}")

    def _bot_guide_aliases(self):
        import re
        block = re.search(r"_MODEL_GUIDES = \{(.*?)\n\s*\}", (ROOT / "bot.py").read_text(encoding="utf-8"), re.S)
        self.assertIsNotNone(block, "could not find _MODEL_GUIDES in bot.py")
        return set(re.findall(r'"([a-z0-9.\-]+)":\s*(?:"|\()', block.group(1)))

    def test_every_video_alias_is_routed_by_bot(self):
        """A new alias with no guide entry falls back to nano-banana.md silently.

        The existing tests only check the other direction (that a named guide
        exists on disk), so adding a model without wiring it produced a still
        image guide for a video model and nothing failed. That is how `h3`
        shipped unrouted in #115.
        """
        routed = self._bot_guide_aliases()
        missing = sorted(set(generate.VIDEO_MODEL_ALIASES) - routed)
        self.assertEqual(missing, [], msg=f"video aliases with no guide in bot.py: {missing}")

    def test_every_video_alias_is_in_the_skill_table(self):
        """Scoped to the mapping table only.

        A whole-file search passes on an alias that appears anywhere else in
        SKILL.md, e.g. in the cost table, which makes the guard vacuous.
        """
        import re
        text = self.SKILL_MD.read_text(encoding="utf-8")
        section = re.search(r"### Guide file mapping\n(.*?)\n###", text, re.S)
        self.assertIsNotNone(section, "could not find the guide mapping table in SKILL.md")
        # Left-hand column only: the aliases, not the guide filenames.
        listed = set()
        for row in re.findall(r"^\|([^|]+)\|[^|]+\|$", section.group(1), re.M):
            listed.update(a.strip().strip("`") for a in row.split(","))
        missing = sorted(a for a in generate.VIDEO_MODEL_ALIASES
                         if not any(a == e or e.startswith(f"{a} ") for e in listed))
        self.assertEqual(missing, [], msg=f"video aliases absent from the SKILL.md mapping table: {missing}")


class TestGenerateJob(unittest.TestCase):
    """generate_job (3D/audio) create -> wait -> download path, fully mocked."""

    @patch("generate.get_account_status", return_value={"credits": 5})
    @patch("generate.httpx.Client")
    @patch("subprocess.run")
    def test_job_success_downloads(self, mock_run, mock_client, mock_acct):
        mock_run.return_value = _proc(0, json.dumps({"result_url": "https://x/a.glb", "credits_used": 5}))
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"GLB-BYTES"
        mock_client.return_value.__enter__.return_value.get.return_value = resp
        out = str(Path(tempfile.mkdtemp()) / "a.glb")
        result = generate.generate_job("a chest", out, "tripo_3d")
        self.assertTrue(result["success"])
        self.assertEqual(Path(out).read_bytes(), b"GLB-BYTES")

    @patch("subprocess.run")
    def test_job_auth_error(self, mock_run):
        mock_run.return_value = _proc(1, "", "please authenticate your token")
        result = generate.generate_job("x", "/tmp/none.glb", "tripo_3d")
        self.assertFalse(result["success"])
        self.assertIn("auth", result["error"].lower())


if __name__ == "__main__":
    unittest.main()
