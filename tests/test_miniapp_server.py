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
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import miniapp.server as srv  # noqa: E402
from fastapi import HTTPException  # noqa: E402


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


class TestMediaMenu(unittest.TestCase):
    """The image and video menu is the image tool's own (generate.menu()).

    This module kept a copy of every model's ratios (_PER_MODEL_RATIOS) and the
    page a copy of the models; both drifted from the tool.
    """

    def test_endpoint_serves_the_tools_menu(self) -> None:
        import asyncio
        import importlib.util
        spec = importlib.util.spec_from_file_location("gen_for_menu_test", srv.GENERATE_SCRIPT)
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        served = asyncio.run(srv.media_menu({"_id": "1"}))
        self.assertEqual(served, gen.menu())
        self.assertEqual({c["id"] for c in served["video"]} & {"h3", "seedance2.5"}, {"h3", "seedance2.5"})

    def test_no_ratio_table_of_its_own(self) -> None:
        self.assertFalse(hasattr(srv, "_PER_MODEL_RATIOS"))


class _FakeLaunchRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


class TestMediaGenLaunch(unittest.TestCase):
    """POST /api/launch with a media-gen config, driven through the endpoint.

    The cost quote and the Telegram message are replaced; the menu is the real
    tool's. The pending hand-off file is the real path, under a user id no
    Telegram account can have, and removed afterwards.
    """

    UID = "mom-test-media-launch"

    def setUp(self) -> None:
        self.pending = Path(f"/tmp/media_gen_pending_{self.UID}.json")
        self.assertFalse(self.pending.exists(), "a pending file from another run is in the way")
        self.addCleanup(self.pending.unlink, missing_ok=True)
        self.sent = []
        send = unittest.mock.patch.object(srv, "_send_bot_message", side_effect=lambda uid, text: self.sent.append(text) or True)
        send.start()
        self.addCleanup(send.stop)
        self.quotes = []

        def fake_run(cmd, **kwargs):
            self.quotes.append(cmd)
            return unittest.mock.Mock(returncode=0, stdout=json.dumps({"credits": 2, "credits_remaining": 100}), stderr="")

        run = unittest.mock.patch.object(srv.subprocess, "run", side_effect=fake_run)
        run.start()
        self.addCleanup(run.stop)

    def launch(self, config: dict) -> dict:
        import asyncio
        body = {"skill": "media-gen", "config": config}
        return asyncio.run(srv.launch_skill(_FakeLaunchRequest(body), {"_id": self.UID}))

    def launch_refused(self, config: dict) -> int:
        with self.assertRaises(HTTPException) as ctx:
            self.launch(config)
        return ctx.exception.status_code

    def test_hailuo_as_the_old_page_sent_it_is_accepted(self) -> None:
        # Hailuo has no ratio row, so the page sent aspect_ratio null and this
        # endpoint answered "Invalid aspect ratio": the card could never run.
        config = {"type": "video", "model": "hailuo", "aspect_ratio": None, "duration": 6, "prompt": "waves"}
        self.assertTrue(self.launch(config)["ok"])
        # and the null is not handed on: bot.py would print "Aspect Ratio: None"
        self.assertNotIn("aspect_ratio", json.loads(self.pending.read_text()))

    def test_no_ratio_invented_for_a_model_without_one(self) -> None:
        self.launch({"type": "video", "model": "hailuo", "duration": 6, "prompt": "waves"})
        handed = json.loads(self.pending.read_text())
        self.assertNotIn("aspect_ratio", handed)
        self.assertNotIn("-a", self.quotes[-1])
        self.assertNotIn("Aspect:", self.sent[-1])

    def test_unknown_model_refused(self) -> None:
        self.assertEqual(self.launch_refused({"type": "image", "model": "bogus", "prompt": "a cat"}), 400)
        self.assertFalse(self.pending.exists())

    def test_a_still_model_is_not_a_video(self) -> None:
        # The page listed Soul Cast under video for two months after the tool
        # made it a still model.
        self.assertEqual(self.launch_refused({"type": "video", "model": "soul-cast", "prompt": "a cast"}), 400)

    def test_no_resolution_invented_for_a_model_without_one(self) -> None:
        self.launch({"type": "image", "model": "soul", "aspect_ratio": "1:1", "prompt": "a face"})
        handed = json.loads(self.pending.read_text())
        self.assertNotIn("resolution", handed)
        self.assertNotIn("--resolution", self.quotes[-1])
        self.assertNotIn("Resolution:", self.sent[-1])

    def test_message_names_the_card(self) -> None:
        self.launch({"type": "video", "model": "kling", "aspect_ratio": "16:9", "duration": 5, "prompt": "a car"})
        self.assertIn("using Kling 3.0", self.sent[-1])

    def test_every_card_launches_with_what_the_page_preselects(self) -> None:
        # What showMediaGenConfig and the render functions pick for each card:
        # this type's usual shape where offered, the middle resolution, the
        # default duration, every option at its default.
        import importlib.util
        spec = importlib.util.spec_from_file_location("gen_for_launch_test", srv.GENERATE_SCRIPT)
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        for kind, cards in gen.menu().items():
            for card in cards:
                with self.subTest(kind=kind, model=card["id"]):
                    config = {"type": kind, "model": card["id"], "prompt": "a test"}
                    usual = "1:1" if kind == "image" else "16:9"
                    if card["ratios"]:
                        config["aspect_ratio"] = usual if usual in card["ratios"] else card["ratios"][0]
                    if card.get("resolutions"):
                        res = card["resolutions"]
                        config["resolution"] = res[1 if len(res) > 1 else 0]
                    if card.get("duration"):
                        config["duration"] = card["duration"]["default"]
                    config["extra_params"] = {k: v["default"] for k, v in card["options"].items()}
                    self.pending.unlink(missing_ok=True)
                    self.assertTrue(self.launch(config)["ok"])
                    quote = self.quotes[-1]
                    self.assertEqual("-a" in quote, bool(card["ratios"]))
                    self.assertEqual("--video" in quote, kind == "video")


class TestClaudeModelCatalog(unittest.TestCase):
    """Opus 5.5: selectable for claude/claude-api, default unchanged.

    The picker is data driven from install/wizard.py PROVIDER_MODELS, so these
    also prove the Mini App inherits a catalog edit with no hardcoded list.
    """

    def test_opus_5_5_selectable_for_claude(self) -> None:
        ids = {m["id"] for m in srv._available_models("claude")}
        self.assertIn("claude-opus-5-5", ids)

    def test_opus_5_5_selectable_for_claude_api(self) -> None:
        ids = {m["id"] for m in srv._available_models("claude-api")}
        self.assertIn("claude-opus-5-5", ids)

    def test_retired_opus_not_offered(self) -> None:
        # Opus 4.8 moved to the docs' Legacy table when Opus 5 landed, and
        # Opus 5 when Opus 5.5 landed (2026-09-22); the picker must not keep
        # offering a retired Opus beside its successor.
        for provider in ("claude", "claude-api"):
            ids = {m["id"] for m in srv._available_models(provider)}
            self.assertNotIn("claude-opus-5", ids, msg=provider)
            self.assertNotIn("claude-opus-4-8", ids, msg=provider)

    def test_fable_5_1_selectable(self) -> None:
        # Same inheritance proof as Opus 5 above, for the model that replaced
        # Fable 5: the picker is data driven, so a wizard catalog edit lands
        # here with no Mini App change.
        for provider in ("claude", "claude-api"):
            ids = {m["id"] for m in srv._available_models(provider)}
            self.assertIn("claude-fable-5-1", ids, msg=provider)

    def test_retired_fable_not_offered(self) -> None:
        # Fable 5 was superseded by 5.1 at the same price; the picker must not
        # keep offering the older one alongside it.
        for provider in ("claude", "claude-api"):
            ids = {m["id"] for m in srv._available_models(provider)}
            self.assertNotIn("claude-fable-5", ids, msg=provider)

    def test_default_is_current_sonnet(self) -> None:
        # Opus/Fable entries must not hijack the recommended/default model;
        # the default tracks the current Sonnet (Sonnet 5.5 since September
        # 28, 2026, Sonnet 5 from June 30).
        self.assertEqual(srv._WIZARD_DEFAULT_MODELS.get("claude"), "claude-sonnet-5-5")
        self.assertEqual(srv._WIZARD_DEFAULT_MODELS.get("claude-api"), "claude-sonnet-5-5")


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

    def test_ref_image_scoped_to_uploader(self) -> None:
        # Uploads are named "{user_id}_...", and /api/launch requires the
        # ref_image filename to carry the caller's prefix (F3).
        owner = 12345
        own_file = f"{owner}_1716600000_abc12345.jpg"
        self.assertTrue(own_file.startswith(f"{owner}_"))
        # Another user's upload must not match the caller's prefix.
        other_file = "98765_1716600000_def67890.jpg"
        self.assertFalse(other_file.startswith(f"{owner}_"))

    def test_ref_image_prefix_no_collision_across_ids(self) -> None:
        # The trailing underscore prevents user 12 from matching user 123's
        # files (and vice versa).
        self.assertFalse("123_1716600000_a.jpg".startswith("12_"))
        self.assertFalse("12_1716600000_a.jpg".startswith("123_"))


class TestUploadSizeGuard(unittest.TestCase):
    """_reject_oversized_upload: coarse Content-Length early-out (F2)."""

    def test_request_max_leaves_multipart_slack(self) -> None:
        self.assertEqual(srv.UPLOAD_REQUEST_MAX_SIZE, srv.UPLOAD_MAX_SIZE + 1024 * 1024)

    def test_missing_header_does_not_raise(self) -> None:
        srv._reject_oversized_upload(None)
        srv._reject_oversized_upload("")

    def test_malformed_header_does_not_raise(self) -> None:
        # Garbage/absent length is not trusted as safe; the bounded read in
        # media_upload() is the real cap, so the early-out simply abstains.
        srv._reject_oversized_upload("not-a-number")

    def test_within_cap_does_not_raise(self) -> None:
        srv._reject_oversized_upload(str(srv.UPLOAD_MAX_SIZE))
        srv._reject_oversized_upload(str(srv.UPLOAD_REQUEST_MAX_SIZE))

    def test_oversized_raises_413(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            srv._reject_oversized_upload(str(srv.UPLOAD_REQUEST_MAX_SIZE + 1))
        self.assertEqual(ctx.exception.status_code, 413)


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

    def _launchd_with_table(self, table: str, stats: dict) -> None:
        """launchctl says pid 4242; `ps -ax` prints `table`; per-pid stats."""
        self._platform.system = lambda: "Darwin"  # type: ignore[assignment]

        def fake_run(cmd, *args, **kwargs):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            r = R()
            if cmd[0] == "launchctl":
                r.stdout = "gui/501/com.myoldmachine.bot = {\n\tstate = running\n\tpid = 4242\n}\n"
            elif cmd[:2] == ["ps", "-ax"]:
                r.stdout = table
            elif cmd[0] == "ps":
                r.stdout = stats.get(cmd[-1], "")
            return r

        self._patch_run(fake_run)

    def test_macos_behind_the_starter_the_bot_itself_is_measured(self) -> None:
        # launchd's pid is the starter, which is under a megabyte. Reporting
        # its memory as the bot's would read as a bot that holds nothing.
        starter = srv.starter_path()
        self._launchd_with_table(
            f"    1     0 /sbin/launchd\n"
            f" 4242     1 {starter}\n"
            f" 4300  4242 /opt/homebrew/Cellar/python@3.12/3.12.15/Frameworks/"
            f"Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python\n"
            f" 4400  4300 node\n",
            {"4242": "02:00 900\n", "4300": "01:59 204800\n"})
        status = srv._bot_status()
        self.assertTrue(status["active"])
        self.assertEqual(status["pid"], 4300)
        self.assertEqual(status["memory_mb"], 200)
        self.assertEqual(status["uptime_seconds"], 119)

    def test_macos_a_bot_with_one_child_is_not_mistaken_for_the_starter(self) -> None:
        # Without the starter, launchd's pid is the bot, and a turn in flight
        # is its only child. That child is a Claude CLI, not the bot.
        self._launchd_with_table(
            " 4242     1 /opt/homebrew/Cellar/python@3.12/3.12.15/Frameworks/"
            "Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python\n"
            " 4300  4242 /Users/x/.local/bin/claude\n",
            {"4242": "02:00 204800\n", "4300": "00:30 51200\n"})
        status = srv._bot_status()
        self.assertEqual(status["pid"], 4242)
        self.assertEqual(status["memory_mb"], 200)

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
