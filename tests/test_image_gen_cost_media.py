"""image-gen --cost quotes the job it will run, media included (Linux bot review 2026-09-27).

The quote sent no media, so a flux_3_video continuation (13 credits/s with a
video_references clip) was quoted at the plain 5.5/s: 27.5 credits for 5 s
against a real 65, measured live. Offline: subprocess.run is replaced.
"""

import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "image-gen" / "scripts" / "generate.py"


def load():
    spec = importlib.util.spec_from_file_location("image_gen_generate_cost", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CostCommand(unittest.TestCase):
    def setUp(self):
        self.gen = load()
        self.calls = []

        def fake_run(cmd, **kwargs):
            self.calls.append(cmd)
            return mock.Mock(returncode=0, stdout=json.dumps({"credits": 1}), stderr="")

        patcher = mock.patch.object(self.gen.subprocess, "run", side_effect=fake_run)
        patcher.start()
        self.addCleanup(patcher.stop)
        acct = mock.patch.object(self.gen, "get_account_status", return_value={"credits": 0})
        acct.start()
        self.addCleanup(acct.stop)

    def quote(self, *argv):
        with mock.patch("sys.argv", ["generate.py", *argv, "--cost"]), \
                mock.patch("builtins.print"):
            self.gen.main()
        cost_calls = [c for c in self.calls if c[:3] == ["higgsfield", "generate", "cost"]]
        self.assertEqual(len(cost_calls), 1, self.calls)
        return cost_calls[0]

    def test_video_media_reach_the_quote(self):
        cmd = self.quote("push in", "--video", "-m", "flux-video", "--duration", "5",
                         "--video-references", "/tmp/clip.mp4")
        self.assertIn("--video-references", cmd)
        self.assertEqual(cmd[cmd.index("--video-references") + 1], "/tmp/clip.mp4")

    def test_keyframes_reach_the_quote(self):
        cmd = self.quote("a turn", "--video", "-m", "wan", "--duration", "5",
                         "--start-image", "/tmp/a.png", "--end-image", "/tmp/b.png")
        self.assertIn("--start-image", cmd)
        self.assertIn("--end-image", cmd)

    def test_image_reference_reaches_the_quote(self):
        cmd = self.quote("the same bottle on marble", "-m", "nano-pro", "-r", "/tmp/a.png")
        self.assertEqual([cmd[i + 1] for i, t in enumerate(cmd) if t == "--image"], ["/tmp/a.png"])

    def test_per_model_ratios_are_accepted(self):
        # 'auto' and '2:1' are model values the fixed-size table did not list,
        # so argparse refused them before any model could answer
        for ratio in ("auto", "2:1", "1:2"):
            with self.subTest(ratio=ratio):
                self.calls.clear()
                cmd = self.quote("a wide plate", "-m", "gpt", "-a", ratio)
                self.assertEqual(cmd[cmd.index("--aspect_ratio") + 1], ratio)

    def test_no_duration_for_a_model_without_one(self):
        # veo3 has no duration param; create never sends one, so the quote must not either
        cmd = self.quote("waves", "--video", "-m", "veo3", "--duration", "8", "--start-image", "/tmp/a.png")
        self.assertNotIn("--duration", cmd)


if __name__ == "__main__":
    unittest.main()
