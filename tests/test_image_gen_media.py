"""Offline unit tests for the image-gen media additions (workflows, voiced TTS, soul-id, brand).

Covers pure logic (no Higgsfield CLI calls): the workflow registry + dispatch, the
result-URL extractor (result_url vs the image_decompose medias shape), the shared workflow
param builder, the brand URL collector, and the soul-id image-count guard.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT / "skills" / "image-gen" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import brand  # noqa: E402
import generate  # noqa: E402
import soul_id  # noqa: E402


class TestWorkflowsRegistry(unittest.TestCase):
    def test_registry_dispatch(self):
        self.assertEqual(set(generate.WORKFLOWS), {
            "reframe", "draw_to_video", "dubbing", "voice_change",
            "image_decompose", "kling3_0_motion_control",
        })
        # video post-production runs via `generate workflow`
        for name in ("reframe", "draw_to_video", "dubbing", "voice_change"):
            self.assertEqual(generate.WORKFLOWS[name]["cmd"], "workflow")
        # prompt-less create models run via `generate create`
        for name in ("image_decompose", "kling3_0_motion_control"):
            self.assertEqual(generate.WORKFLOWS[name]["cmd"], "create")
        self.assertEqual(generate.WORKFLOWS["image_decompose"]["out"], ".png")
        self.assertEqual(generate.WORKFLOWS["reframe"]["out"], ".mp4")


class TestExtractResultUrl(unittest.TestCase):
    def test_prefers_result_url(self):
        self.assertEqual(generate._extract_result_url({"result_url": "http://x/a.mp4"}), "http://x/a.mp4")

    def test_medias_fallback(self):
        # image_decompose leaves result_url null and returns outputs under params.medias[].data.url
        data = {"result_url": None, "params": {"medias": [{"role": "out", "data": {"url": "http://x/layer.jpg"}}]}}
        self.assertEqual(generate._extract_result_url(data), "http://x/layer.jpg")

    def test_empty(self):
        self.assertEqual(generate._extract_result_url({"result_url": None, "params": {}}), "")
        self.assertEqual(generate._extract_result_url({}), "")


class TestWorkflowParams(unittest.TestCase):
    def test_flag_mapping(self):
        params = generate._workflow_params(
            video="/tmp/a.mp4", aspect_ratio="9:16", voice_id="vid",
            voice_type="preset", target_language="spa",
            extra_params={"mode": "std", "generate_audio": True},
        )
        joined = " ".join(params)
        self.assertIn("--video /tmp/a.mp4", joined)
        self.assertIn("--aspect-ratio 9:16", joined)
        self.assertIn("--voice-id vid", joined)
        self.assertIn("--voice-type preset", joined)
        self.assertIn("--target-language spa", joined)
        self.assertIn("--mode std", joined)
        self.assertIn("--generate_audio true", joined)  # bool lowercased

    def test_multi_image(self):
        params = generate._workflow_params(image_refs=["/a.jpg", "/b.jpg"])
        self.assertEqual(params.count("--image"), 2)

    def test_single_image_string(self):
        # MOM passes a single ref_image STRING (not a list); the builder must wrap it,
        # never index it character-by-character. Guards the singular-ref_image port.
        params = generate._workflow_params(image_refs="/only.jpg")
        self.assertEqual(params.count("--image"), 1)
        self.assertIn("/only.jpg", params)


class TestBrandHelpers(unittest.TestCase):
    def test_collect_urls_recursive(self):
        data = {"jobs": [{"result_url": "http://x/1.jpg"}, {"nested": {"result_url": "http://x/2.png"}}]}
        self.assertEqual(brand._collect_urls(data), ["http://x/1.jpg", "http://x/2.png"])

    def test_ext_from_url(self):
        self.assertEqual(brand._ext_from_url("http://x/a.png?sig=1"), ".png")
        self.assertEqual(brand._ext_from_url("http://x/a"), ".jpg")


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestSoulGuard(unittest.TestCase):
    def test_too_few(self):
        r = soul_id.cmd_create(_Args(name="X", model="soul-2", image=["a.jpg", "b.jpg"]))
        self.assertFalse(r["success"])
        self.assertIn("5-20", r["error"])

    def test_too_many(self):
        r = soul_id.cmd_create(_Args(name="X", model="soul-2", image=[f"{i}.jpg" for i in range(21)]))
        self.assertFalse(r["success"])
        self.assertIn("5-20", r["error"])


if __name__ == "__main__":
    unittest.main()
