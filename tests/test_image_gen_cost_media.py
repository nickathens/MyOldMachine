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

    # --ref-images: one --image per reference, and the quote carries the list the
    # job sends. The flag first reached the create call only, so a quote priced a
    # job with no references, and an -r given beside it was dropped without a word.

    @staticmethod
    def images(cmd):
        return [cmd[i + 1] for i, t in enumerate(cmd) if t == "--image"]

    def create(self, *argv):
        """The create command the tool builds. The fake CLI returns no result, so it exits 1."""
        with mock.patch("sys.argv", ["generate.py", *argv, "--backend", "higgsfield"]), \
                mock.patch("builtins.print"), self.assertRaises(SystemExit):
            self.gen.main()
        made = [c for c in self.calls if c[:3] == ["higgsfield", "generate", "create"]]
        self.assertEqual(len(made), 1, self.calls)
        return made[0]

    def test_several_references_reach_the_quote(self):
        cmd = self.quote("the same bottle", "-m", "nano-pro", "--ref-images", "/tmp/a.png", "/tmp/b.png")
        self.assertEqual(self.images(cmd), ["/tmp/a.png", "/tmp/b.png"])

    def test_the_job_sends_what_the_quote_priced(self):
        argv = ("the same bottle", "-m", "nano-pro", "--ref-images", "/tmp/a.png", "/tmp/b.png")
        quoted = self.images(self.quote(*argv))
        self.calls.clear()
        self.assertEqual(self.images(self.create(*argv)), quoted)

    def test_ref_image_beside_ref_images_goes_first(self):
        argv = ("p", "-m", "nano-pro", "-r", "/tmp/a.png", "--ref-images", "/tmp/b.png", "/tmp/c.png")
        both = ["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"]
        self.assertEqual(self.images(self.quote(*argv)), both)
        self.calls.clear()
        self.assertEqual(self.images(self.create(*argv)), both)

    def test_a_single_path_after_ref_images_is_sent(self):
        argv = ("p", "-m", "nano-pro", "--ref-images", "/tmp/a.png")
        self.assertEqual(self.images(self.quote(*argv)), ["/tmp/a.png"])
        self.calls.clear()
        self.assertEqual(self.images(self.create(*argv)), ["/tmp/a.png"])

    def test_one_reference_string_is_one_flag(self):
        self.gen.generate_higgsfield("p", "/tmp/x.jpg", model="nano-pro", ref_image="/tmp/photo.png")
        made = [c for c in self.calls if c[:3] == ["higgsfield", "generate", "create"]]
        self.assertEqual(self.images(made[0]), ["/tmp/photo.png"])

    def test_refused_where_nothing_would_send_it(self):
        failed = {"success": False, "path": "", "error": "not reached"}
        for extra in (["--video"], ["--threed"], ["--audio"], ["--workflow", "reframe"],
                      ["--backend", "pollinations"]):
            with self.subTest(extra=extra):
                self.calls.clear()
                with mock.patch("sys.argv", ["generate.py", "p", "--ref-images", "/tmp/a.png", "/tmp/b.png", *extra]), \
                        mock.patch("sys.stderr"), mock.patch("builtins.print"), \
                        mock.patch.object(self.gen, "generate_pollinations", return_value=failed) as poll, \
                        mock.patch.object(self.gen, "generate_video", return_value=failed) as video, \
                        mock.patch.object(self.gen, "generate_job", return_value=failed) as job, \
                        mock.patch.object(self.gen, "generate_workflow", return_value=failed) as flow, \
                        self.assertRaises(SystemExit) as stop:
                    self.gen.main()
                self.assertEqual(stop.exception.code, 2)
                self.assertEqual(self.calls, [])
                for runner in (poll, video, job, flow):
                    runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
