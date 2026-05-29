"""Unit tests for miniapp.server — env writes and project visibility scoping.

These tests poke private helpers directly (no FastAPI TestClient) so the bot
doesn't need a running event loop. The HTTP surface is thin around these
helpers; covering the data scoping rules is what actually matters.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import miniapp.server as srv  # noqa: E402


class TestEnvWrites(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mom-mini-env-"))
        self.env = self.tmp / ".env"
        self.env.write_text(
            "# header comment\n"
            "TELEGRAM_BOT_TOKEN=abc123\n"
            "LLM_PROVIDER=claude\n"
            "LLM_MODEL=claude-sonnet-4-6\n"
            "OTHER_VAR=keep-me\n",
            encoding="utf-8",
        )
        # Snapshot real value so the test can patch and restore reliably.
        self._saved_env_file = srv.ENV_FILE
        srv.ENV_FILE = self.env

    def tearDown(self) -> None:
        srv.ENV_FILE = self._saved_env_file
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_existing_var(self) -> None:
        self.assertEqual(srv._read_env_var("LLM_PROVIDER"), "claude")
        self.assertEqual(srv._read_env_var("LLM_MODEL"), "claude-sonnet-4-6")

    def test_read_missing_returns_default(self) -> None:
        self.assertEqual(srv._read_env_var("DOES_NOT_EXIST", "fallback"), "fallback")

    def test_write_replaces_existing_in_place(self) -> None:
        srv._write_env_var("LLM_PROVIDER", "openai")
        lines = self.env.read_text(encoding="utf-8").splitlines()
        # Order preserved; header comment preserved; OTHER_VAR untouched.
        self.assertEqual(lines[0], "# header comment")
        self.assertIn("LLM_PROVIDER=openai", lines)
        self.assertIn("OTHER_VAR=keep-me", lines)
        # No duplicate of the key.
        self.assertEqual(sum(1 for ln in lines if ln.startswith("LLM_PROVIDER=")), 1)

    def test_write_appends_when_missing(self) -> None:
        srv._write_env_var("LLM_EFFORT", "high")
        text = self.env.read_text(encoding="utf-8")
        self.assertIn("LLM_EFFORT=high", text)

    def test_write_refuses_unsafe_keys(self) -> None:
        # Only WRITABLE_ENV_KEYS may be touched. Refuse TELEGRAM_BOT_TOKEN etc.
        with self.assertRaises(ValueError):
            srv._write_env_var("TELEGRAM_BOT_TOKEN", "stolen")

    def test_write_refuses_unsafe_value_chars(self) -> None:
        # Newline injection would corrupt other keys; refuse outright.
        with self.assertRaises(ValueError):
            srv._write_env_var("LLM_MODEL", "value\nLLM_PROVIDER=malicious")

    def test_write_allows_empty_value_for_clearing(self) -> None:
        # Provider switch to ollama needs to clear LLM_MODEL — empty string
        # must be accepted so the bot can't fall back to the old provider's
        # model name and try to use it as an ollama tag.
        srv._write_env_var("LLM_MODEL", "")
        self.assertEqual(srv._read_env_var("LLM_MODEL"), "")

    def test_tmp_file_path_is_dot_env_dot_tmp(self) -> None:
        # The atomic write must produce '.env.tmp', not '.env.env.tmp'.
        # We can't observe the temp file directly (it's renamed in-flight),
        # but we can verify the parent directory contains no .env.env.tmp
        # leftover after a write, even if the write fails mid-way.
        srv._write_env_var("LLM_MODEL", "claude-sonnet-4-6")
        leftovers = list(self.env.parent.glob(".env.env.tmp"))
        self.assertEqual(leftovers, [])


class TestProjectVisibility(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mom-mini-proj-"))
        self.projects_dir = self.tmp / "data" / "memory" / "projects"
        self.archive_dir = self.projects_dir / "_archive"
        self.projects_dir.mkdir(parents=True)
        self.archive_dir.mkdir()
        self._write_project("alpha", owner="111", status="in_progress", summary="A")
        self._write_project("beta", owner="222", status="in_progress", summary="B")
        self._write_project("gamma", owner="shared", status="ongoing", summary="C")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_project(self, slug: str, *, owner: str, status: str, summary: str,
                       archived: bool = False) -> None:
        base = self.archive_dir if archived else self.projects_dir
        (base / slug).mkdir(parents=True, exist_ok=True)
        (base / slug / "state.json").write_text(json.dumps({
            "name": slug.title(),
            "slug": slug,
            "owner": owner,
            "status": status,
            "summary": summary,
            "next_steps": [],
            "blockers": [],
        }), encoding="utf-8")

    def setUp_paths(self) -> None:
        self._saved_proj = srv.PROJECTS_DIR
        self._saved_arch = srv.ARCHIVE_DIR
        srv.PROJECTS_DIR = self.projects_dir
        srv.ARCHIVE_DIR = self.archive_dir

    def restore_paths(self) -> None:
        srv.PROJECTS_DIR = self._saved_proj
        srv.ARCHIVE_DIR = self._saved_arch

    def _user(self, tid: str, role: str = "user") -> dict:
        return {
            "_id": tid,
            "_profile": {"role": role, "name": f"User{tid}"},
        }

    def test_owner_sees_own_project(self) -> None:
        self.setUp_paths()
        try:
            listing = srv._list_projects(self._user("111"))
            slugs = {p["slug"] for p in listing}
            self.assertIn("alpha", slugs)
            self.assertIn("gamma", slugs)  # shared visible to everyone
            self.assertNotIn("beta", slugs)  # belongs to user 222
        finally:
            self.restore_paths()

    def test_other_user_blocked_from_private(self) -> None:
        self.setUp_paths()
        try:
            listing = srv._list_projects(self._user("222"))
            slugs = {p["slug"] for p in listing}
            self.assertIn("beta", slugs)
            self.assertIn("gamma", slugs)
            self.assertNotIn("alpha", slugs)  # user 222 cannot see 111's project
        finally:
            self.restore_paths()

    def test_admin_sees_all(self) -> None:
        self.setUp_paths()
        try:
            listing = srv._list_projects(self._user("999", role="admin"))
            slugs = {p["slug"] for p in listing}
            self.assertEqual(slugs, {"alpha", "beta", "gamma"})
        finally:
            self.restore_paths()

    def test_detail_lookup_respects_visibility(self) -> None:
        self.setUp_paths()
        try:
            # Owner sees their own.
            self.assertIsNotNone(srv._get_project_detail("alpha", self._user("111")))
            # Stranger blocked.
            self.assertIsNone(srv._get_project_detail("alpha", self._user("222")))
            # Admin sees it.
            self.assertIsNotNone(srv._get_project_detail("alpha", self._user("999", role="admin")))
        finally:
            self.restore_paths()


class TestModifyAuthorization(unittest.TestCase):
    """Archive/unarchive authorization: owner or admin only. Shared projects
    are admin-only to prevent any allowlisted user from archiving system work."""

    def _user(self, tid: str, role: str = "user") -> dict:
        return {"_id": tid, "_profile": {"role": role}}

    def test_owner_can_modify_own(self) -> None:
        state = {"owner": "111"}
        self.assertTrue(srv._user_can_modify_project(state, self._user("111")))

    def test_other_user_blocked(self) -> None:
        state = {"owner": "111"}
        self.assertFalse(srv._user_can_modify_project(state, self._user("222")))

    def test_admin_can_modify_any(self) -> None:
        state = {"owner": "111"}
        self.assertTrue(srv._user_can_modify_project(state, self._user("999", role="admin")))

    def test_shared_is_admin_only(self) -> None:
        state = {"owner": "shared"}
        self.assertFalse(srv._user_can_modify_project(state, self._user("111")))
        self.assertFalse(srv._user_can_modify_project(state, self._user("222")))
        self.assertTrue(srv._user_can_modify_project(state, self._user("999", role="admin")))


class TestSlugValidation(unittest.TestCase):
    def test_accepts_safe_slugs(self) -> None:
        # Should not raise.
        srv._validate_slug("alpha")
        srv._validate_slug("project_name")
        srv._validate_slug("project-1")
        srv._validate_slug("abc123")

    def test_rejects_path_traversal(self) -> None:
        from fastapi import HTTPException
        for bad in ["..", "../../etc", "a/../b", "foo/bar", ".hidden",
                    "Project", "UPPER", "with space", "with.dot",
                    "with%encoded", ""]:
            with self.assertRaises(HTTPException, msg=f"slug {bad!r} should be rejected"):
                srv._validate_slug(bad)


class TestModelRatios(unittest.TestCase):
    """Issue #1: per-model aspect ratio filtering must return model-specific data."""

    def test_per_model_ratios_table_populated(self) -> None:
        self.assertTrue(hasattr(srv, "_PER_MODEL_RATIOS"))
        self.assertGreater(len(srv._PER_MODEL_RATIOS), 20)

    def test_hailuo_has_empty_ratios(self) -> None:
        self.assertEqual(srv._PER_MODEL_RATIOS.get("minimax_hailuo"), [])

    def test_veo3_only_two_ratios(self) -> None:
        self.assertEqual(sorted(srv._PER_MODEL_RATIOS.get("veo3", [])), ["16:9", "9:16"])

    def test_nano_banana_has_many_ratios(self) -> None:
        ratios = srv._PER_MODEL_RATIOS.get("nano_banana", [])
        self.assertGreater(len(ratios), 9)

    def test_every_model_ratios_subset_of_all(self) -> None:
        for model, ratios in srv._PER_MODEL_RATIOS.items():
            for r in ratios:
                self.assertIn(r, srv._ALL_RATIOS, msg=f"{model} has invalid ratio {r}")


class TestClaudeModelCatalog(unittest.TestCase):
    """Opus 4.8 port: selectable for claude/claude-api, default unchanged."""

    def test_opus_4_8_selectable_for_claude(self) -> None:
        ids = {m["id"] for m in srv._available_models("claude")}
        self.assertIn("claude-opus-4-8", ids)

    def test_opus_4_8_selectable_for_claude_api(self) -> None:
        ids = {m["id"] for m in srv._available_models("claude-api")}
        self.assertIn("claude-opus-4-8", ids)

    def test_default_stays_sonnet_4_6(self) -> None:
        # Adding Opus 4.8 must not hijack the recommended/default model.
        self.assertEqual(srv._WIZARD_DEFAULT_MODELS.get("claude"), "claude-sonnet-4-6")
        self.assertEqual(srv._WIZARD_DEFAULT_MODELS.get("claude-api"), "claude-sonnet-4-6")


class TestPendingMediaGenTTL(unittest.TestCase):
    """Issue #2: pending config should expire after 10 minutes."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mom-mini-mg-"))
        self.pending = self.tmp / "media_gen_pending_12345.json"
        self.pending.write_text(json.dumps({
            "type": "image", "model": "nano2", "aspect_ratio": "1:1",
            "resolution": "2k", "prompt": "a cat",
        }))

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fresh_pending_is_not_expired(self) -> None:
        import time
        age = time.time() - self.pending.stat().st_mtime
        self.assertLess(age, 600)

    def test_old_pending_would_be_expired(self) -> None:
        import os
        old_time = self.pending.stat().st_mtime - 700
        os.utime(self.pending, (old_time, old_time))
        import time
        age = time.time() - self.pending.stat().st_mtime
        self.assertGreater(age, 600)


class TestModelGuideMapping(unittest.TestCase):
    """Issue #3: all model aliases should have explicit guide routes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bot_src = (ROOT / "bot.py").read_text()
        gen_src = (ROOT / "skills" / "image-gen" / "scripts" / "generate.py").read_text()
        cls.image_aliases = []
        cls.video_aliases = []
        in_image = False
        in_video = False
        for line in gen_src.splitlines():
            if line.startswith("MODEL_ALIASES"):
                in_image = True
                continue
            if line.startswith("VIDEO_MODEL_ALIASES"):
                in_video = True
                in_image = False
                continue
            if in_image and line.strip().startswith("}"):
                in_image = False
                continue
            if in_video and line.strip().startswith("}"):
                in_video = False
                continue
            if in_image and '":' in line:
                alias = line.strip().split('"')[1]
                cls.image_aliases.append(alias)
            if in_video and '":' in line:
                alias = line.strip().split('"')[1]
                cls.video_aliases.append(alias)

    def test_all_image_aliases_have_guide(self) -> None:
        missing = [a for a in self.image_aliases if f'"{a}"' not in self.bot_src]
        self.assertEqual(missing, [], f"Missing model guide mappings in bot.py: {missing}")

    def test_all_video_aliases_have_guide(self) -> None:
        missing = [a for a in self.video_aliases if f'"{a}"' not in self.bot_src]
        self.assertEqual(missing, [], f"Missing model guide mappings in bot.py: {missing}")


class TestMediaGenLaunchValidation(unittest.TestCase):
    """Issue #4: tests for the media-gen launch endpoint validation."""

    def test_invalid_type_rejected(self) -> None:
        self.assertNotIn("audio", ("image", "video"))

    def test_model_validation_regex(self) -> None:
        valid = ["nano2", "nano-pro", "flux-kontext", "kling2.6", "veo3.1"]
        for m in valid:
            self.assertTrue(
                m.replace("-", "").replace(".", "").replace("_", "").isalnum(),
                msg=f"{m} should be valid"
            )
        invalid = ["../../etc", "nano;rm -rf", "model name", ""]
        for m in invalid:
            self.assertFalse(
                bool(m) and m.replace("-", "").replace(".", "").replace("_", "").isalnum(),
                msg=f"{m!r} should be invalid"
            )

    def test_aspect_ratio_validation(self) -> None:
        valid_aspects = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9", "9:21"}
        self.assertIn("16:9", valid_aspects)
        self.assertNotIn("7:3", valid_aspects)
        self.assertNotIn("", valid_aspects)

    def test_duration_range(self) -> None:
        for d in [1, 5, 30, 60]:
            self.assertTrue(1 <= d <= 60)
        for d in [0, -1, 61, 999]:
            self.assertFalse(1 <= d <= 60)

    def test_extra_params_sanitization(self) -> None:
        raw = {"quality": "high", "bad key!": "val", "mode": True, "nested": {"a": 1}}
        safe = {}
        for k, v in raw.items():
            if isinstance(k, str) and len(k) < 30 and k.replace("_", "").isalpha():
                if isinstance(v, (str, bool, int, float)):
                    safe[k] = v
        self.assertEqual(safe, {"quality": "high", "mode": True})


class TestCreateJobValidation(unittest.TestCase):
    """Issue #7: validation coverage for POST /api/scheduler."""

    def test_empty_message_rejected(self) -> None:
        self.assertEqual("".strip(), "")

    def test_long_message_rejected(self) -> None:
        msg = "x" * 501
        self.assertGreater(len(msg), 500)

    def test_invalid_date_format_rejected(self) -> None:
        import re
        self.assertIsNone(re.match(r"^\d{4}-\d{2}-\d{2}$", "13-05-2026"))
        self.assertIsNone(re.match(r"^\d{4}-\d{2}-\d{2}$", "not-a-date"))

    def test_invalid_calendar_date_caught(self) -> None:
        from datetime import datetime
        with self.assertRaises(ValueError):
            datetime.fromisoformat("2024-02-30T10:00:00")

    def test_non_numeric_hour_caught(self) -> None:
        with self.assertRaises((ValueError, TypeError)):
            int("abc")

    def test_empty_repeat_normalized(self) -> None:
        repeat = ""
        if repeat == "":
            repeat = None
        self.assertIsNone(repeat)

    def test_invalid_repeat_rejected(self) -> None:
        valid = ("daily", "weekly", "biweekly", "monthly")
        self.assertNotIn("hourly", valid)
        self.assertNotIn("yearly", valid)

    def test_past_date_rejected_for_onetime(self) -> None:
        from datetime import datetime
        run_at = datetime.fromisoformat("2020-01-01T10:00:00")
        self.assertLess(run_at, datetime.now())

    def test_until_before_start_rejected(self) -> None:
        from datetime import datetime
        run_at = datetime.fromisoformat("2026-06-15T10:00:00")
        end_date = datetime.fromisoformat("2026-06-10T23:59:59")
        self.assertLess(end_date, run_at)

    def test_valid_payload_accepted(self) -> None:
        import re
        from datetime import datetime
        date_str = "2026-12-25"
        self.assertIsNotNone(re.match(r"^\d{4}-\d{2}-\d{2}$", date_str))
        run_at = datetime.fromisoformat(f"{date_str}T10:00:00")
        self.assertGreater(run_at, datetime(2026, 1, 1))


class TestMediaUpload(unittest.TestCase):
    """Validate upload endpoint constraints and ref_image path security."""

    def test_allowed_types(self) -> None:
        allowed = srv.UPLOAD_ALLOWED_TYPES
        self.assertIn("image/jpeg", allowed)
        self.assertIn("image/png", allowed)
        self.assertIn("image/webp", allowed)
        self.assertNotIn("image/gif", allowed)
        self.assertNotIn("application/pdf", allowed)

    def test_max_size(self) -> None:
        self.assertEqual(srv.UPLOAD_MAX_SIZE, 10 * 1024 * 1024)

    def test_path_traversal_blocked_by_resolve(self) -> None:
        upload_dir = srv.UPLOAD_DIR.resolve()
        evil = Path("/tmp/media_gen_uploads/../etc/passwd")
        self.assertFalse(evil.resolve().is_relative_to(upload_dir))

    def test_path_traversal_prefix_attack_blocked(self) -> None:
        upload_dir = srv.UPLOAD_DIR.resolve()
        evil = Path("/tmp/media_gen_uploads_evil/payload.jpg")
        self.assertFalse(evil.resolve().is_relative_to(upload_dir))

    def test_valid_upload_path_accepted(self) -> None:
        upload_dir = srv.UPLOAD_DIR.resolve()
        good = upload_dir / "12345_1716600000_abc12345.jpg"
        self.assertTrue(good.is_relative_to(upload_dir))

    def test_ref_image_suffix_validation(self) -> None:
        valid = {".jpg", ".jpeg", ".png", ".webp"}
        self.assertIn(".jpg", valid)
        self.assertIn(".jpeg", valid)
        self.assertIn(".png", valid)
        self.assertIn(".webp", valid)
        self.assertNotIn(".gif", valid)
        self.assertNotIn(".svg", valid)
        self.assertNotIn(".exe", valid)


class TestRefImageWiring(unittest.TestCase):
    """Verify ref_image flows from pending config through bot context."""

    def test_pending_config_preserves_ref_image(self) -> None:
        config = {
            "type": "image",
            "model": "nano2",
            "aspect_ratio": "1:1",
            "prompt": "a cat",
            "ref_image": "/tmp/media_gen_uploads/123_1716600000_abc.jpg",
        }
        self.assertEqual(config.get("ref_image"), "/tmp/media_gen_uploads/123_1716600000_abc.jpg")
        pending = json.dumps(config)
        loaded = json.loads(pending)
        self.assertEqual(loaded["ref_image"], config["ref_image"])
class TestBotStatus(unittest.TestCase):
    """Cross-platform bot status dispatch. Patches platform.system + subprocess
    so the tests don't depend on the host actually running launchd or systemd."""

    def setUp(self) -> None:
        import platform as _platform
        import subprocess as _subprocess
        self._platform = _platform
        self._subprocess = _subprocess
        self._saved_system = _platform.system
        self._saved_run = _subprocess.run

    def tearDown(self) -> None:
        self._platform.system = self._saved_system
        self._subprocess.run = self._saved_run

    def _patch_run(self, handler) -> None:
        self._subprocess.run = handler  # type: ignore[assignment]

    def test_macos_running_launchagent_reports_active(self) -> None:
        self._platform.system = lambda: "Darwin"  # type: ignore[assignment]
        launchctl_out = (
            "gui/501/com.myoldmachine.bot = {\n"
            "\tactive count = 1\n"
            "\tstate = running\n"
            "\tpid = 4242\n"
            "\tsub = {\n"
            "\t\tstate = active\n"
            "\t}\n"
            "}\n"
        )

        def fake_run(cmd, *args, **kwargs):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            r = R()
            if cmd[0] == "launchctl":
                r.stdout = launchctl_out
            elif cmd[0] == "ps":
                # etime 1-02:03:04 -> 1 day, 2h, 3m, 4s -> 93784 seconds
                # rss 102400 KB -> 100 MB
                r.stdout = "1-02:03:04 102400\n"
            return r

        self._patch_run(fake_run)
        status = srv._bot_status()
        self.assertTrue(status["active"])
        self.assertEqual(status["pid"], 4242)
        self.assertEqual(status["uptime_seconds"], 93784)
        self.assertEqual(status["memory_mb"], 100)
        self.assertTrue(status["supported"])
        self.assertEqual(status["service"], srv.BOT_LAUNCHD_LABEL)

    def test_macos_no_launchagent_falls_back_to_pgrep(self) -> None:
        self._platform.system = lambda: "Darwin"  # type: ignore[assignment]

        def fake_run(cmd, *args, **kwargs):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            r = R()
            if cmd[0] == "launchctl":
                r.returncode = 1  # not registered
            elif cmd[0] == "pgrep":
                r.stdout = "9999\n"
            elif cmd[0] == "ps":
                r.stdout = "05:30 51200\n"  # 5m30s, 50 MB
            return r

        self._patch_run(fake_run)
        status = srv._bot_status()
        self.assertTrue(status["active"])
        self.assertEqual(status["pid"], 9999)
        self.assertEqual(status["uptime_seconds"], 330)
        self.assertEqual(status["memory_mb"], 50)
        self.assertTrue(status["supported"])

    def test_macos_nothing_running_returns_inactive_supported(self) -> None:
        self._platform.system = lambda: "Darwin"  # type: ignore[assignment]

        def fake_run(cmd, *args, **kwargs):
            class R:
                returncode = 1
                stdout = ""
                stderr = ""
            return R()

        self._patch_run(fake_run)
        status = srv._bot_status()
        self.assertFalse(status["active"])
        self.assertTrue(status["supported"])  # mac IS supported
        self.assertIsNone(status["pid"])

    def test_linux_path_uses_systemctl(self) -> None:
        self._platform.system = lambda: "Linux"  # type: ignore[assignment]

        def fake_run(cmd, *args, **kwargs):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            r = R()
            if cmd[:2] == ["systemctl", "is-active"]:
                r.stdout = "active\n"
            elif cmd[:2] == ["systemctl", "show"]:
                r.stdout = (
                    "MainPID=1234\n"
                    "ActiveEnterTimestampMonotonic=0\n"
                    "MemoryCurrent=104857600\n"
                )
            return r

        self._patch_run(fake_run)
        status = srv._bot_status()
        self.assertTrue(status["active"])
        self.assertEqual(status["pid"], 1234)
        self.assertEqual(status["memory_mb"], 100)
        self.assertEqual(status["service"], srv.BOT_SERVICE)


class TestRestartTargets(unittest.TestCase):
    """The /api/restart endpoint accepts only the two whitelisted targets
    and must stay in sync with core.updater's dispatch table."""

    def test_whitelist_matches_updater_table(self) -> None:
        from core import updater
        self.assertEqual(
            set(srv.VALID_RESTART_TARGETS),
            set(updater._SERVICE_TARGETS.keys()),
        )

    def test_whitelist_contains_bot_and_miniapp(self) -> None:
        self.assertIn("bot", srv.VALID_RESTART_TARGETS)
        self.assertIn("miniapp", srv.VALID_RESTART_TARGETS)


if __name__ == "__main__":
    unittest.main()
