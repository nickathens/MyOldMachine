"""Independent regression cases for engine selection and subscription accounting."""

import asyncio
import json
import os
import tempfile
import time
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["MOM_TEST"] = "1"

import bot
from core import engines, llm, usage, user_prefs, users
import miniapp.server as server


@contextmanager
def isolated():
    with tempfile.TemporaryDirectory(prefix="mom170-cases-") as td, ExitStack() as st:
        st.enter_context(patch("core.engines._login_status", return_value=(True, ""), create=True))
        root = Path(td)
        st.enter_context(patch.object(users, "USERS_DATA_DIR", root / "users"))
        st.enter_context(patch.object(usage, "USAGE_DIR", root / "usage"))
        st.enter_context(patch.object(usage, "CLAUDE_LIMITS_FILE", root / "usage" / "limits.json"))
        st.enter_context(patch.object(bot, "_engine_providers", {}))
        st.enter_context(patch.object(bot, "_configure_provider_hooks"))
        engines.probe_cache_clear()
        usage.codex_cache_clear()
        yield root
        engines.probe_cache_clear()
        usage.codex_cache_clear()


class Stream:
    def __init__(self, items):
        self.items = list(items)

    async def readline(self):
        return (json.dumps(self.items.pop(0)) + "\n").encode() if self.items else b""

    async def read(self):
        return b""


class Input:
    def write(self, value):
        pass

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


class Process:
    def __init__(self, items, rc=0):
        self.stdout = Stream(items)
        self.stderr = Stream([])
        self.stdin = Input()
        self.returncode = rc

    async def wait(self):
        return self.returncode


async def replay(provider, items, rc=0):
    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=Process(items, rc))),
        patch("core.llm._claude_supports_partial_messages", return_value=True),
        patch("core.llm._codex_feature_names", return_value=frozenset()),
        patch("core.llm._codex_accepts_hook_trust_bypass", return_value=False),
    ):
        return await provider.complete("sys", [], user_id=None)


def claude_result(failed=False):
    return {
        "type": "result",
        "subtype": "error_during_execution" if failed else "success",
        "is_error": failed,
        "result": "Run failed" if failed else "Done",
        "total_cost_usd": 1.25,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 80,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 2000,
        },
    }


def raw_limits(allowed=True):
    return {
        "ordinaryUsageAllowed": allowed,
        "rateLimits": {
            "limitId": "codex",
            "planType": "pro",
            "primary": {"usedPercent": 10, "windowDurationMins": 300, "resetsAt": 2000000000},
            "secondary": None,
        },
    }


class EngineUsageAuditTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(isolated())

    def test_control_success_preserves_reported_usage(self):
        r = asyncio.run(replay(llm.ClaudeCLIProvider("claude-opus-5-5"), [claude_result()]))
        assert (r.input_tokens, r.cache_read_tokens, r.cache_creation_tokens, r.list_cost_usd) == (100, 900, 2000, 1.25)

    def test_failed_result_preserves_reported_usage(self):
        r = asyncio.run(replay(llm.ClaudeCLIProvider("claude-opus-5-5"), [claude_result(True)], 1))
        assert r.error
        assert (r.input_tokens, r.cache_read_tokens, r.cache_creation_tokens, r.list_cost_usd) == (100, 900, 2000, 1.25)

    def test_claude_display_includes_cache_creation(self):
        r = asyncio.run(replay(llm.ClaudeCLIProvider("claude-opus-5-5"), [claude_result()]))
        bot._record_turn_usage(101, llm.ClaudeCLIProvider("claude-opus-5-5"), None, r)
        assert "3,000 tokens in" in "\n".join(bot._usage_block(usage.summarise(101)))

    def test_codex_display_does_not_count_cached_input_twice(self):
        p = llm.CodexCLIProvider("gpt-6-astra")
        r = asyncio.run(
            replay(
                p,
                [
                    {"type": "item.completed", "item": {"type": "agent_message", "text": "Done"}},
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 10000, "cached_input_tokens": 8000, "output_tokens": 200},
                    },
                ],
            )
        )
        bot._record_turn_usage(101, p, {"id": "astra"}, r)
        assert "10,000 tokens in" in "\n".join(bot._usage_block(usage.summarise(101)))

    def test_codex_interrupted_reply_counts_as_failed(self):
        p = llm.CodexCLIProvider("gpt-6-astra")
        r = asyncio.run(
            replay(
                p,
                [
                    {"type": "item.completed", "item": {"type": "agent_message", "text": "Working on it"}},
                    {"type": "turn.failed", "error": {"message": "rate limited"}},
                ],
                1,
            )
        )
        bot._record_turn_usage(101, p, {"id": "astra"}, r)
        assert usage.summarise(101)["failed_turns"] == 1

    def test_non_admin_default_is_opus_max(self):
        fallback = llm.ClaudeCLIProvider("claude-sonnet-5")
        with (
            patch.object(bot, "_llm_provider", fallback),
            patch("bot.is_admin", return_value=False),
            patch("core.engines._cli_version_text", return_value="2.1.280 (Claude Code)"),
        ):
            p, e = bot._provider_for_user(101)
        assert (p.model, p.effort_override) == ("claude-opus-5-5", "max")

    def test_control_per_user_selection_and_explicit_effort(self):
        with patch("core.engines._cli_version_text", return_value="codex-cli 0.154.0"):
            assert engines.set_user_engine(101, "astra")[0]
            p, e = bot._provider_for_user(101)
            assert p.model == "gpt-6-astra"
            assert llm._turn_effort(p) == "xhigh"
            assert engines.user_engine_id(102) == ""

    def test_clear_reports_write_failure(self):
        user_prefs.set_pref(101, "engine", "astra")
        with patch("core.user_prefs.save_json", side_effect=OSError("disk full")):
            ok, message = engines.set_user_engine(101, "")
        assert engines.user_engine_id(101) == "astra"
        assert not ok, message

    def test_picker_finds_cli_that_provider_can_find(self):
        isolated = self.root
        binary = isolated / "bin" / "claude"
        binary.parent.mkdir()
        binary.write_text('#!/bin/sh\necho "2.1.280 (Claude Code)"\n')
        binary.chmod(0o755)
        with (
            patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}),
            patch.object(llm, "_CLI_FALLBACK_DIRS", [str(binary.parent)]),
        ):
            assert llm.ClaudeCLIProvider("claude-opus-5-5")._cli_binary == str(binary)
            ok, reason = engines.engine_available(engines.get_engine("opus"), refresh=True)
        assert ok, reason

    def test_telegram_picker_keeps_event_loop_responsive(self):
        import threading

        loop_thread = threading.get_ident()
        probe_threads = []

        def probe(binary):
            probe_threads.append(threading.get_ident())
            return "codex-cli 0.154.0"

        async def run():
            for command in ("/engine", "/engine astra"):
                engines.probe_cache_clear()
                update = SimpleNamespace(
                    effective_user=SimpleNamespace(id=101),
                    message=SimpleNamespace(text=command, reply_text=AsyncMock()),
                )
                await bot.engine_command.__wrapped__(update, None)

        with patch("core.engines._cli_version_text", side_effect=probe):
            asyncio.run(run())
        assert len(probe_threads) >= 3
        assert all(thread != loop_thread for thread in probe_threads)

    def test_allowance_display_exposes_blocked_status(self):
        rendered = []
        for allowed in (True, False):
            with patch("core.usage._codex_rpc_rate_limits", return_value=raw_limits(allowed)):
                meter = usage.codex_meter(use_cache=False)
            rendered.append(bot._meter_lines(meter, "Codex"))
        assert rendered[0] != rendered[1], rendered

    def test_unknown_allowance_is_not_a_rejection(self):
        with patch("core.usage._codex_rpc_rate_limits", return_value=raw_limits(None)):
            meter = usage.codex_meter(use_cache=False)
        assert meter["status"] != "rejected"

    def test_allowance_keeps_additional_quota_buckets(self):
        raw = raw_limits()
        raw["rateLimitsByLimitId"] = {
            "codex": raw["rateLimits"],
            "codex_other": {
                "limitId": "codex_other",
                "limitName": "Other quota",
                "primary": {"usedPercent": 100, "windowDurationMins": 300},
                "rateLimitReachedType": "rate_limit_reached",
            },
        }
        with patch("core.usage._codex_rpc_rate_limits", return_value=raw):
            meter = usage.codex_meter(use_cache=False)
        assert any(w["used_percent"] == 100 for w in meter["windows"]), meter

    def test_admin_breakdown_identifies_models_and_tokens(self):
        usage.record_turn(
            102, provider="codex-cli", model="gpt-6-astra", engine="astra", input_tokens=10000, output_tokens=200
        )
        with patch("core.usage.meters", return_value={}), patch.object(server, "_load_users", return_value={}):
            payload = server.get_usage(days=7, user={"_id": "101", "_profile": {"role": "admin"}})
        assert payload["everyone"][0].get("by_model"), payload["everyone"]

    def test_control_ordinary_user_cannot_read_other_users_usage(self):
        usage.record_turn(102, provider="codex-cli", model="gpt-6-astra", input_tokens=100)
        with patch("core.usage.meters", return_value={}):
            payload = server.get_usage(days=7, user={"_id": "101", "_profile": {"role": "user"}})
        assert payload["everyone"] is None
        assert payload["you"]["turns"] == 0

    def test_control_signed_http_requests_enforce_user_boundaries(self):
        import hashlib
        import hmac
        from urllib.parse import urlencode
        from fastapi.testclient import TestClient

        token = "test-token-local-only"
        profiles = {"101": {"role": "user"}, "102": {"role": "user"}, "103": {"role": "admin"}}

        def headers(uid):
            fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid}, separators=(",", ":"))}
            check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
            secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
            fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
            return {"X-Telegram-Init-Data": urlencode(fields)}

        usage.record_turn(102, provider="codex-cli", model="gpt-6-astra", input_tokens=100)
        with (
            patch.object(server, "BOT_TOKEN", token),
            patch.object(server, "_load_users", return_value=profiles),
            patch("core.usage.meters", return_value={}),
            patch("core.engines._cli_version_text", return_value="codex-cli 0.154.0"),
        ):
            with TestClient(server.app) as client:
                for endpoint in ("/api/engine", "/api/usage"):
                    assert client.get(endpoint).status_code == 401
                    assert client.get(endpoint, headers=headers(999)).status_code == 403
                own = client.get("/api/usage", headers=headers(101)).json()
                assert own["everyone"] is None and own["you"]["turns"] == 0
                admin = client.get("/api/usage", headers=headers(103)).json()
                assert admin["everyone"][0]["id"] == "102"
                saved = client.post("/api/engine", headers=headers(101), json={"engine": "astra", "user": 102})
                assert saved.status_code == 200
                assert engines.user_engine_id(101) == "astra"
                assert engines.user_engine_id(102) == ""

    def test_control_stop_reaches_both_providers_without_changing_other_user(self):
        providers = [llm.ClaudeCLIProvider("claude-opus-5-5"), llm.CodexCLIProvider("gpt-6-astra")]
        for p in providers:
            p._user_processes = {101: SimpleNamespace(returncode=None), 102: SimpleNamespace(returncode=None)}
        killed = []
        with (
            patch.object(bot, "_llm_provider", providers[0]),
            patch.object(bot, "_engine_providers", {"astra": providers[1]}),
            patch.object(bot, "_stop_epoch", {}),
            patch("core.llm._kill_turn", side_effect=lambda p: killed.append(p)),
        ):
            asyncio.run(bot._apply_stop(AsyncMock(), 101))
        assert len(killed) == 2
        assert all(
            any(k is p._user_processes[101] for k in killed) and not any(k is p._user_processes[102] for k in killed)
            for p in providers
        )

    def test_claude_snapshot_age_starts_at_event_not_completion(self):
        clock = {"now": 1000}

        class TimedStream(Stream):
            async def readline(self):
                if len(self.items) == 1:
                    clock["now"] = 4600
                return await super().readline()

        process = Process([])
        process.stdout = TimedStream(
            [
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {"unifiedWindows": {"five_hour": {"utilization": 0.1, "resetsAt": 20000}}},
                },
                claude_result(),
            ]
        )
        with (
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)),
            patch("core.llm._claude_supports_partial_messages", return_value=True),
            patch("core.usage.time.time", side_effect=lambda: clock["now"]),
        ):
            asyncio.run(llm.ClaudeCLIProvider("claude-opus-5-5").complete("sys", [], user_id=None))
        assert usage.claude_meter()["captured_at"] == 1000

    def test_codex_cached_read_does_not_display_as_live(self):
        with (
            patch("core.usage._codex_rpc_rate_limits", return_value=raw_limits()),
            patch("core.usage.time.monotonic", return_value=100),
        ):
            usage.codex_meter()
        with patch("core.usage._codex_rpc_rate_limits") as rpc, patch("core.usage.time.monotonic", return_value=150):
            cached = usage.codex_meter()
            rpc.assert_not_called()
        assert not cached["live"]

    def test_admin_keeps_existing_model_and_effort(self):
        with patch("core.engines.engine_available", return_value=(True, "")):
            assert engines.resolve_engine(101, default_provider="claude-cli", admin=True) is None
            assert engines.resolve_engine(101, default_provider="ollama", admin=False) is None
            assert engines.resolve_engine(101, default_provider="claude-api", admin=False) is None
            assert engines.resolve_engine(101, default_provider="claude-cli", admin=False)["effort"] == "max"

    def test_api_key_install_does_not_gain_subscription_default(self):
        with (
            patch("core.config.get_llm_provider", return_value="claude"),
            patch("core.config.get_llm_api_key", return_value="test-api-key"),
        ):
            assert engines.resolve_engine(101, admin=False) is None

    def test_unknown_tokens_and_confirmed_zero_are_distinct(self):
        p = llm.CodexCLIProvider("gpt-6-astra")
        for events in (
            [],
            [{"type": "turn.completed", "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}}],
        ):
            r = asyncio.run(replay(p, events))
            bot._record_turn_usage(101, p, None, r)
        summary = usage.summarise(101)
        assert summary["unmeasured_turns"] == 1
        assert summary["turns"] == 2
        assert summary["total_input_tokens"] == 0
        assert "incomplete" in "\n".join(bot._usage_block(summary))

    def test_codex_retry_event_does_not_mark_completed_work_failed(self):
        p = llm.CodexCLIProvider("gpt-6-astra")
        r = asyncio.run(
            replay(
                p,
                [
                    {"type": "error", "message": "Reconnecting 1/5"},
                    {"type": "item.completed", "item": {"type": "agent_message", "text": "Done"}},
                    {"type": "turn.completed", "usage": {"input_tokens": 30, "output_tokens": 2}},
                ],
            )
        )
        bot._record_turn_usage(101, p, None, r)
        assert usage.summarise(101)["failed_turns"] == 0
        assert r.completed

    def test_completed_usage_survives_later_read_error(self):
        process = Process([claude_result()])

        async def broken_read():
            raise OSError("pipe failed")

        process.stderr.read = broken_read
        with (
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)),
            patch("core.llm._claude_supports_partial_messages", return_value=True),
        ):
            r = asyncio.run(llm.ClaudeCLIProvider().complete("sys", []))
        assert r.error and not r.completed
        assert r.list_cost_usd == 1.25
        assert r.usage_reported

    def test_claude_block_without_windows_is_visible(self):
        usage.save_claude_rate_limits({"status": "rejected"})
        meter = usage.claude_meter()
        assert meter is not None
        assert "Blocked" in "\n".join(bot._meter_lines(meter, "Claude"))

    def test_codex_block_without_windows_is_visible(self):
        with patch("core.usage._codex_rpc_rate_limits", return_value={"ordinaryUsageAllowed": False, "rateLimits": {}}):
            meter = usage.codex_meter(use_cache=False)
        assert "Blocked" in "\n".join(bot._meter_lines(meter, "Codex"))

    def test_later_finishing_turn_cannot_replace_newer_allowance(self):
        usage.save_claude_rate_limits(
            {"status": "rejected", "unifiedWindows": {"five_hour": {"utilization": 1}}}, captured_at=2000
        )
        usage.save_claude_rate_limits(
            {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0}}}, captured_at=1000
        )
        assert usage.claude_meter()["captured_at"] == 2000
        assert usage.claude_meter()["status"] == "rejected"

    def test_codex_cache_does_not_mix_binaries(self):
        with patch("core.usage._codex_rpc_rate_limits", return_value=raw_limits()) as rpc:
            usage.codex_meter(binary="/tmp/install-a/codex")
            usage.codex_meter(binary="/tmp/install-b/codex")
        assert rpc.call_count == 2

    def test_model_buckets_include_all_input_categories(self):
        usage.record_turn(
            101,
            provider="claude-cli",
            model="claude-opus-5-5",
            input_tokens=100,
            cache_read_tokens=900,
            cache_creation_tokens=2000,
        )
        usage.record_turn(101, provider="codex-cli", model="gpt-6-astra", input_tokens=10000, cache_read_tokens=8000)
        summary = usage.summarise(101)
        assert summary["total_input_tokens"] == 13000
        assert summary["by_model"]["claude-opus-5-5"]["total_input_tokens"] == 3000
        assert summary["by_model"]["gpt-6-astra"]["total_input_tokens"] == 10000

    def test_invalid_engine_payloads_return_400(self):
        from fastapi import HTTPException

        for body in ([], {"engine": []}, {"engine": 7}, {"engine": None}):
            request = SimpleNamespace(json=AsyncMock(return_value=body))
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(server.set_engine(request, user={"_id": "101", "_profile": {"role": "user"}}))
            assert caught.exception.status_code == 400

    def test_engine_payload_exposes_default_and_fallback(self):
        with (
            patch("core.config.get_llm_provider", return_value="claude-cli"),
            patch("core.engines.engine_available", return_value=(True, "")),
        ):
            payload = server.get_engine(user={"_id": "101", "_profile": {"role": "user"}})
        assert payload["effective"] == "opus"
        user_prefs.set_pref(101, "engine", "astra")
        with patch("core.engines.engine_available", return_value=(False, "not logged in")):
            payload = server.get_engine(user={"_id": "101", "_profile": {"role": "user"}})
        assert payload["picked"] == "astra" and payload["effective"] == ""


class SubscriptionLoginTests(unittest.TestCase):
    def check(self, engine, result):
        cls = llm.ClaudeCLIProvider if engine == "opus" else llm.CodexCLIProvider
        with (
            patch.object(cls, "_get_cli_env", return_value={"AUTH_CONTEXT": "expected"}),
            patch("core.engines.subprocess.run", return_value=result) as run,
        ):
            ok, reason = engines._login_status(engines.get_engine(engine), "/test/bin/cli")
        assert run.call_args.kwargs["env"] == {"AUTH_CONTEXT": "expected"}
        return ok, reason

    def test_codex_logged_out_not_available(self):
        assert not self.check("astra", SimpleNamespace(returncode=1, stdout="", stderr="Not logged in"))[0]

    def test_codex_api_key_is_not_subscription_login(self):
        assert not self.check("astra", SimpleNamespace(returncode=0, stdout="", stderr="Logged in using an API key"))[0]

    def test_codex_chatgpt_login_is_available(self):
        assert self.check("astra", SimpleNamespace(returncode=0, stdout="", stderr="Logged in using ChatGPT"))[0]

    def test_claude_login_methods(self):
        for method, expected in [("claude.ai", True), ("oauth_token", True), ("api_key", False), ("none", False)]:
            result = SimpleNamespace(returncode=0, stdout=json.dumps({"loggedIn": True, "authMethod": method}))
            assert self.check("opus", result)[0] == expected

    def test_claude_unknown_or_false_login_is_unavailable(self):
        for output in ("{}", "not JSON", '{"loggedIn": false, "authMethod": "claude.ai"}', "[]"):
            assert not self.check("opus", SimpleNamespace(returncode=0, stdout=output))[0]

    def test_login_timeout_is_not_permission(self):
        import subprocess

        with (
            patch.object(llm.CodexCLIProvider, "_get_cli_env", return_value={}),
            patch("core.engines.subprocess.run", side_effect=subprocess.TimeoutExpired("codex", 10)),
        ):
            ok, reason = engines._login_status(engines.get_engine("astra"), "codex")
        assert not ok and "verify" in reason


class ProbeStopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.enterContext(patch.object(users, "USERS_DATA_DIR", Path(self.tmp.name)))
        self.enterContext(patch.object(llm.ClaudeCLIProvider, "_get_cli_env", return_value={}))
        self.enterContext(patch.object(llm.CodexCLIProvider, "_get_cli_env", return_value={}))

    async def check_stop(self, provider, probe_name, probe_result):
        import threading

        entered = threading.Event()
        release = threading.Event()

        def probe(*args):
            entered.set()
            release.wait(2)
            return probe_result

        process = Process([])
        with (
            patch(probe_name, side_effect=probe),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn,
        ):
            task = asyncio.create_task(provider.complete("sys", [], user_id=101))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                stopped = provider.stop_user(101)
            finally:
                release.set()
            result = await task
        assert stopped, "Stop did not recognise the preparing turn"
        spawn.assert_not_called()
        assert not result.completed

    async def test_stop_during_claude_capability_probe_prevents_spawn(self):
        await self.check_stop(llm.ClaudeCLIProvider(), "core.llm._claude_supports_partial_messages", True)

    async def test_stop_during_codex_capability_probe_prevents_spawn(self):
        await self.check_stop(llm.CodexCLIProvider(), "core.llm._codex_feature_names", frozenset())

    async def test_stop_while_process_is_spawning_sends_no_prompt(self):
        for provider in (llm.ClaudeCLIProvider(), llm.CodexCLIProvider()):
            process = Process([])
            writes = []
            process.stdin.write = writes.append

            async def spawn(*args, **kwargs):
                assert provider.stop_user(101)
                return process

            with (
                patch("asyncio.create_subprocess_exec", new=spawn),
                patch("core.llm._claude_supports_partial_messages", return_value=False),
                patch("core.llm._codex_feature_names", return_value=frozenset()),
                patch("core.llm._codex_accepts_hook_trust_bypass", return_value=False),
            ):
                response = await provider.complete("sys", [], user_id=101)
            assert not writes
            assert not response.completed
            assert not provider._preparing_users

    async def test_preparing_users_are_isolated(self):
        import threading

        provider = llm.CodexCLIProvider()
        entered = threading.Event()
        release = threading.Event()

        def probe(*args):
            entered.set()
            release.wait(2)
            return frozenset()

        with (
            patch("core.llm._codex_feature_names", side_effect=probe),
            patch("asyncio.create_subprocess_exec", new=AsyncMock()) as spawn,
        ):
            task = asyncio.create_task(provider.complete("sys", [], user_id=101))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                assert not provider.stop_user(102)
                assert 101 not in provider._stop_requested
                assert provider.stop_user(101)
            finally:
                release.set()
            await task
        spawn.assert_not_called()
