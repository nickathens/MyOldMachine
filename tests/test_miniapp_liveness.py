"""A Mini App that does not come back from a restart must say so.

`restart_service()` returns True the moment it has spawned the detached
restart script, on every platform. That is the honest answer to "was the
restart scheduled" and it is the only answer available about the bot's own
service, which is about to be killed by the restart it just asked for. It is
not an answer to "did it come back", and /restart presented it as one: the
`if not mini_ok` branch could not be reached on Linux or on macOS, so a Mini
App that failed to start after an update reported nothing at all.

The Mini App is the one target the bot can actually check, because it
outlives it by a few seconds, and it is an HTTP server, so its own socket
answers on both platforms with nothing asked of systemd or launchd.

Two traps this locks down:

* **The old process answers first.** The restart script sleeps before it
  touches the unit, so a naive "is it healthy" poll passes immediately,
  against the very process it is replacing. Proof of a bounce is an instance
  id that changed, or a probe that failed and then recovered.
* **A Mini App that was never up is not a Mini App that failed.** This bot
  restarts the unit whether or not the Mini App was ever installed, so the
  verification is skipped unless it was answering beforehand.
* **"Cannot tell" is not "down".** A probe that could not run must not put a
  red line on screen about a service that is running fine.
"""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

from core import updater  # noqa: E402


class _Health(http.server.BaseHTTPRequestHandler):
    payload: dict = {"ok": True}
    status: int = 200
    raw: bytes | None = None

    def do_GET(self):
        body = (self.raw if self.raw is not None
                else json.dumps(self.payload).encode())
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class _Server:
    """A Mini App stand-in on a real loopback port."""

    def __init__(self, payload=None, status=200, raw=None):
        handler = type("H", (_Health,), {
            "payload": payload if payload is not None else {"ok": True},
            "status": status,
            "raw": raw,
        })
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


class _PortEnv:
    """Point the probe at a chosen port for the duration of a test."""

    def __init__(self, test, port):
        self.saved = os.environ.get("MINIAPP_PORT")
        os.environ["MINIAPP_PORT"] = str(port)
        test.addCleanup(self.restore)

    def restore(self):
        if self.saved is None:
            os.environ.pop("MINIAPP_PORT", None)
        else:
            os.environ["MINIAPP_PORT"] = self.saved


class HealthProbeTests(unittest.TestCase):
    def test_a_live_health_route_reads_up_and_carries_the_instance(self):
        srv = _Server({"ok": True, "service": "x", "instance": "abc123"})
        self.addCleanup(srv.stop)
        _PortEnv(self, srv.port)
        state, detail = updater.miniapp_health(timeout=2.0)
        self.assertEqual(state, updater.MINIAPP_HEALTH_UP)
        self.assertEqual(detail, "abc123")

    def test_a_health_route_with_no_instance_is_up_with_nothing_to_compare(self):
        # An install whose Mini App predates the instance field. Up, but no
        # proof of a bounce is available, which must not read as failure.
        srv = _Server({"ok": True, "service": "x"})
        self.addCleanup(srv.stop)
        _PortEnv(self, srv.port)
        state, detail = updater.miniapp_health(timeout=2.0)
        self.assertEqual(state, updater.MINIAPP_HEALTH_UP)
        self.assertIsNone(detail)

    def test_a_refused_connection_reads_down(self):
        srv = _Server()
        port = srv.port
        srv.stop()  # free the port, so nothing is listening on it
        _PortEnv(self, port)
        state, detail = updater.miniapp_health(timeout=1.0)
        self.assertEqual(state, updater.MINIAPP_HEALTH_DOWN)
        self.assertIn(str(port), detail)

    def test_a_500_reads_down(self):
        srv = _Server(status=500)
        self.addCleanup(srv.stop)
        _PortEnv(self, srv.port)
        state, detail = updater.miniapp_health(timeout=2.0)
        self.assertEqual(state, updater.MINIAPP_HEALTH_DOWN)
        self.assertIn("500", detail)

    def test_something_else_on_the_port_reads_down(self):
        # A different service bound to the same port answers 200 and is not
        # the Mini App. "Someone is listening" is not the question.
        srv = _Server(raw=b"<html>hello</html>")
        self.addCleanup(srv.stop)
        _PortEnv(self, srv.port)
        state, detail = updater.miniapp_health(timeout=2.0)
        self.assertEqual(state, updater.MINIAPP_HEALTH_DOWN)
        self.assertIn("JSON", detail)

    def test_ok_false_reads_down(self):
        srv = _Server({"ok": False})
        self.addCleanup(srv.stop)
        _PortEnv(self, srv.port)
        state, _ = updater.miniapp_health(timeout=2.0)
        self.assertEqual(state, updater.MINIAPP_HEALTH_DOWN)

    def test_a_configured_proxy_is_ignored(self):
        """urllib reads http_proxy from the environment, and a machine with
        one set would send this loopback probe out to the proxy and get a
        connection error back — a red line about a Mini App that is running
        perfectly well.

        This has to run in a fresh interpreter. `urlopen` builds its default
        opener once and caches it module-globally, with the proxy list read
        at construction; setting http_proxy inside a process that has already
        used urllib changes nothing, so an in-process version of this test
        passed with the fix deliberately removed.
        """
        srv = _Server({"ok": True, "instance": "abc"})
        self.addCleanup(srv.stop)
        env = dict(os.environ)
        env["MINIAPP_PORT"] = str(srv.port)
        env["http_proxy"] = "http://127.0.0.1:9"
        env["HTTP_PROXY"] = "http://127.0.0.1:9"
        env.pop("no_proxy", None)
        env.pop("NO_PROXY", None)
        env["MOM_TEST"] = "1"
        out = subprocess.run(
            [sys.executable, "-c",
             "import core.updater as u; print(u.miniapp_health(timeout=3.0))"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-500:])
        self.assertIn("'up'", out.stdout)
        self.assertIn("abc", out.stdout)

    def test_an_unresolvable_port_reads_unknown_not_down(self):
        with patch("install.miniapp_setup.miniapp_port",
                   side_effect=RuntimeError("no config")):
            state, detail = updater.miniapp_health(timeout=0.5)
        self.assertEqual(state, updater.MINIAPP_HEALTH_UNKNOWN)
        self.assertIn("port", detail)


class WaitForMiniAppTests(unittest.TestCase):
    """The verdicts, driven by a scripted sequence of probe answers."""

    def _wait(self, answers, before_pid=None, timeout=5.0, interval=0.5):
        """Drive the wait on a fake clock: `answers` is the probe sequence.

        The clock advances by `interval` per poll and never in real time, so
        the deadline is exercised exactly and the test costs nothing. Once
        the sequence runs out the last answer repeats, which is what a
        service that has settled into one state actually does.
        """
        seq = list(answers)
        clock = {"t": 0.0}

        def fake_probe(timeout=None):
            return seq.pop(0) if seq else answers[-1]

        def fake_sleep(seconds):
            clock["t"] += seconds

        with patch.object(updater, "miniapp_health", side_effect=fake_probe):
            return updater.wait_for_miniapp(
                before_pid, timeout=timeout, interval=interval,
                _sleep=fake_sleep, _now=lambda: clock["t"])

    UP = updater.MINIAPP_HEALTH_UP
    DOWN = updater.MINIAPP_HEALTH_DOWN

    def test_a_changed_instance_is_a_restart(self):
        verdict, _ = self._wait(
            [(self.UP, "old"), (self.UP, "old"), (self.UP, "new")],
            before_pid="old")
        self.assertEqual(verdict, "restarted")

    def test_going_away_and_coming_back_is_a_restart(self):
        verdict, _ = self._wait(
            [(self.UP, None), (self.DOWN, "refused"), (self.UP, None)])
        self.assertEqual(verdict, "restarted")

    def test_never_coming_back_is_down(self):
        # The old process answers, the unit is bounced, nothing comes back.
        verdict, detail = self._wait(
            [(self.UP, "old"), (self.DOWN, "refused")],
            before_pid="old", timeout=2.0)
        self.assertEqual(verdict, updater.MINIAPP_HEALTH_DOWN)
        self.assertIn("refused", detail)

    def test_the_same_process_answering_throughout_is_not_a_restart(self):
        # THE trap. The old Mini App is still up while the detached script
        # sleeps. This must not report "restarted", and must not report a
        # failure either: it is a "did not see it bounce".
        verdict, _ = self._wait(
            [(self.UP, "old")] * 4, before_pid="old", timeout=2.0)
        self.assertEqual(verdict, updater.MINIAPP_HEALTH_UP)

    def test_no_instance_on_either_side_is_up_not_restarted(self):
        verdict, _ = self._wait([(self.UP, None)] * 3, timeout=2.0)
        self.assertEqual(verdict, updater.MINIAPP_HEALTH_UP)

    def test_an_unknown_probe_gives_up_immediately(self):
        verdict, detail = self._wait(
            [(updater.MINIAPP_HEALTH_UNKNOWN, "cannot resolve the port")])
        self.assertEqual(verdict, updater.MINIAPP_HEALTH_UNKNOWN)
        self.assertIn("port", detail)

    def test_it_polls_to_the_deadline_and_then_stops(self):
        # Bounded, and it always takes at least one sample: a zero timeout
        # must still answer from a real probe rather than from nothing.
        calls = {"n": 0}
        clock = {"t": 0.0}

        def fake_probe(timeout=None):
            calls["n"] += 1
            return (self.DOWN, "refused")

        with patch.object(updater, "miniapp_health", side_effect=fake_probe):
            verdict, _ = updater.wait_for_miniapp(
                None, timeout=0.0, interval=0.5,
                _sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
                _now=lambda: clock["t"])
        self.assertEqual(verdict, updater.MINIAPP_HEALTH_DOWN)
        self.assertEqual(calls["n"], 1)

        calls["n"] = 0
        clock["t"] = 0.0
        with patch.object(updater, "miniapp_health", side_effect=fake_probe):
            updater.wait_for_miniapp(
                None, timeout=2.0, interval=0.5,
                _sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
                _now=lambda: clock["t"])
        self.assertEqual(calls["n"], 5)  # t = 0, .5, 1.0, 1.5, 2.0


class RestartHandlerTests(unittest.IsolatedAsyncioTestCase):
    """The probe is worthless if /restart does not act on it.

    These drive the real `restart_command`. The first version of them read
    bot.py's source text for the two names instead, and the mutation battery
    caught them passing for the wrong reason: `from core.updater import
    miniapp_health, restart_service, wait_for_miniapp` sits INSIDE the
    handler, so both names are in the window whether or not anything calls
    them. Deleting the whole verification block left them green.
    """

    def setUp(self):
        import bot as botmod
        import core.session as session_mod
        from core import updater as updater_mod
        self.bot = botmod
        self.updater = updater_mod
        self.session = session_mod
        self.calls: list = []
        self.verdict = ("restarted", "a different process is answering now")
        self.before_state = updater.MINIAPP_HEALTH_UP
        self.before_id = "instance-before"

        self._saved = {
            "restart_service": updater_mod.restart_service,
            "miniapp_health": updater_mod.miniapp_health,
            "wait_for_miniapp": updater_mod.wait_for_miniapp,
            "is_admin": botmod.is_admin,
            "profile": botmod.get_user_profile,
            "scheduler": botmod.get_scheduler,
            "registry": botmod.get_process_registry,
            "allowed": botmod.get_allowed_users,
            "running": botmod._running_turns,
            "pending": botmod._pending_turns,
            "provider": botmod._llm_provider,
        }

        def _restart(target="bot"):
            self.calls.append(("restart", target))
            return True, f"{target} restarting"

        def _health(timeout=2.0):
            self.calls.append(("health", timeout))
            return self.before_state, self.before_id

        def _wait(before_instance=None, timeout=25.0, **kw):
            self.calls.append(("wait", before_instance))
            return self.verdict

        updater_mod.restart_service = _restart
        updater_mod.miniapp_health = _health
        updater_mod.wait_for_miniapp = _wait
        # restart_command sits behind @requires_auth, which reads the real
        # allow-list. Without this the handler answers "Unauthorized." and
        # every assertion below is about a code path that never ran.
        botmod.get_allowed_users = lambda: [1]
        botmod.is_admin = lambda uid: True
        botmod.get_user_profile = lambda uid: {"display_name": "T", "name": "T"}
        botmod.get_scheduler = lambda: None
        botmod.get_process_registry = lambda: _NoRegistry()
        botmod._running_turns = set()
        botmod._pending_turns = {}
        botmod._llm_provider = None
        # _restart_blockers also counts scheduled compactions, and under
        # `unittest discover` other modules leave entries there. Without this
        # the handler answers "Cannot restart, the bot is busy" and every
        # assertion below is about a path that never ran — which is what the
        # guard in _run catches, and why it is there.
        self._compactions = set(session_mod._compaction_scheduled)
        session_mod._compaction_scheduled.clear()

    def tearDown(self):
        self.session._compaction_scheduled.clear()
        self.session._compaction_scheduled.update(self._compactions)
        for name in ("restart_service", "miniapp_health", "wait_for_miniapp"):
            setattr(self.updater, name, self._saved[name])
        self.bot.get_allowed_users = self._saved["allowed"]
        self.bot.is_admin = self._saved["is_admin"]
        self.bot.get_user_profile = self._saved["profile"]
        self.bot.get_scheduler = self._saved["scheduler"]
        self.bot.get_process_registry = self._saved["registry"]
        self.bot._running_turns = self._saved["running"]
        self.bot._pending_turns = self._saved["pending"]
        self.bot._llm_provider = self._saved["provider"]

    async def _run(self):
        update, sent = _fake_update()
        await self.bot.restart_command(update, None)
        self.assertTrue(
            any("Shutting down" in m for m in sent),
            f"the handler never reached the restart path; it said {sent}")
        return sent

    async def test_the_instance_is_read_before_the_bounce_is_asked_for(self):
        # Reading it afterwards would compare the new process against itself,
        # so a Mini App that never restarted would look like one that did.
        await self._run()
        kinds = [c[0] for c in self.calls]
        self.assertIn("health", kinds)
        self.assertIn("restart", kinds)
        self.assertLess(kinds.index("health"),
                        kinds.index("restart"),
                        f"call order was {kinds}")

    async def test_the_wait_is_handed_the_instance_that_was_read(self):
        await self._run()
        waits = [c for c in self.calls if c[0] == "wait"]
        self.assertEqual(len(waits), 1, "the Mini App was never waited for")
        self.assertEqual(waits[0][1], self.before_id)

    async def test_the_wait_happens_after_the_mini_app_restart(self):
        await self._run()
        order = [c for c in self.calls if c[0] in ("restart", "wait")]
        self.assertEqual(order[0], ("restart", "miniapp"))
        self.assertEqual(order[1][0], "wait")

    async def test_a_mini_app_that_never_came_back_is_reported(self):
        self.verdict = ("down", "port 8090 did not answer")
        sent = await self._run()
        self.assertTrue(any("did not come back" in m for m in sent),
                        f"nothing told the user; messages were {sent}")
        self.assertTrue(any("8090" in m for m in sent))

    async def test_a_restarted_mini_app_says_nothing(self):
        sent = await self._run()
        self.assertFalse(any("did not come back" in m for m in sent))

    async def test_an_unprovable_bounce_says_nothing(self):
        # "still answering, no restart observed" is not a failure to put on
        # the user's screen: an install whose /health predates the pid field
        # reaches this on every single restart.
        self.verdict = ("up", "still answering; no restart observed")
        sent = await self._run()
        self.assertFalse(any("did not come back" in m for m in sent))

    async def test_a_probe_that_could_not_run_says_nothing(self):
        self.verdict = ("unknown", "cannot resolve the port")
        sent = await self._run()
        self.assertFalse(any("did not come back" in m for m in sent))

    async def test_a_mini_app_that_was_never_up_is_not_verified(self):
        # This bot bounces the unit whether or not the Mini App was ever
        # installed, and most installs do not have one. Reporting "it did not
        # come back" at them on every /restart would be a false alarm on the
        # most common configuration there is.
        self.before_state = updater.MINIAPP_HEALTH_DOWN
        self.before_id = "port 8090 did not answer"
        self.verdict = ("down", "port 8090 did not answer")
        sent = await self._run()
        self.assertFalse(any("did not come back" in m for m in sent), sent)
        self.assertNotIn("wait", [c[0] for c in self.calls],
                         "nothing to verify, so nothing should be waited for")
        self.assertIn(("restart", "bot"), self.calls)

    async def test_an_unknown_probe_before_the_restart_skips_verification(self):
        self.before_state = updater.MINIAPP_HEALTH_UNKNOWN
        self.before_id = "cannot resolve the port"
        sent = await self._run()
        self.assertFalse(any("did not come back" in m for m in sent), sent)
        self.assertNotIn("wait", [c[0] for c in self.calls])

    async def test_the_bot_still_restarts_after_a_dead_mini_app(self):
        # The Mini App is a note, never a reason to leave the bot on old code.
        self.verdict = ("down", "port 8090 did not answer")
        await self._run()
        self.assertIn(("restart", "bot"), self.calls)


class _NoRegistry:
    def list_running(self):
        return []

    async def cleanup_all(self):
        return None


def _fake_update(user_id: int = 1, text: str = "/restart"):
    """Minimal Update: an effective_user and a message that records replies."""
    sent: list[str] = []

    async def reply_text(msg, **kw):
        sent.append(msg)

    message = type("M", (), {"text": text, "reply_text": staticmethod(reply_text)})()
    update = type("U", (), {
        "effective_user": type("U2", (), {"id": user_id})(),
        "message": message,
    })()
    return update, sent


class HealthEndpointTests(unittest.TestCase):
    def test_health_reports_an_instance_id(self):
        import asyncio

        import miniapp.server as srv
        body = asyncio.run(srv.health())
        self.assertTrue(body["ok"])
        self.assertEqual(body["instance"], srv._INSTANCE_ID)
        self.assertTrue(body["instance"])

    def test_two_processes_get_different_instance_ids(self):
        # The whole point of the field. A constant would make every restart
        # look like "the same process is still answering", which is exactly
        # the silence this change exists to break, and no in-process test can
        # see it — the module is imported once.
        ids = []
        for _ in range(2):
            env = dict(os.environ, MOM_TEST="1")
            out = subprocess.run(
                [sys.executable, "-c",
                 "import miniapp.server as s; print(s._INSTANCE_ID)"],
                cwd=str(ROOT), env=env, capture_output=True, text=True,
                timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr[-500:])
            ids.append(out.stdout.strip())
        self.assertTrue(all(ids), ids)
        self.assertNotEqual(ids[0], ids[1],
                            "every process answers with the same id")

    def test_health_discloses_neither_the_pid_nor_the_uptime(self):
        # /health takes no auth and this project's installer offers to put a
        # tunnel in front of the Mini App, so anything here can end up
        # answering the internet. "Is this the same process" needs no pid.
        import asyncio

        import miniapp.server as srv
        body = asyncio.run(srv.health())
        self.assertEqual(set(body), {"ok", "service", "instance"})
        # A substring search for the pid was the first version of this and it
        # is not a test: run in a PID namespace the pid is a single digit,
        # which turns up inside a random hex id about half the time. The set
        # of keys is the claim.
        self.assertNotEqual(body["instance"], str(os.getpid()))


if __name__ == "__main__":
    unittest.main()
