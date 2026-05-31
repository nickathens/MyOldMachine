"""Tests for core.worker — the multi-machine compute pool.

The whole job lifecycle (submit -> run -> poll -> complete/fail, plus cancel)
is exercised end-to-end through LocalTransport, which runs the real
worker_runner.py as a real subprocess on this machine. No SSH or second machine
is needed: SSHTransport implements the same four primitives, so the lifecycle
code under test is identical for both.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import worker  # noqa: E402

USER_A = 111111
USER_B = 222222


class _TempPoolStore(unittest.TestCase):
    """Redirect the worker registry and per-user data dirs into a tempdir."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mom_worker_test_"))
        self.workers_file = self.tmp / "workers.json"
        self.users_dir = self.tmp / "users"
        self.users_dir.mkdir()
        self.worker_root = self.tmp / "workerroot"
        self.worker_root.mkdir()
        self._patches = [
            patch.object(worker, "WORKERS_FILE", self.workers_file),
            patch.object(worker, "USERS_DIR", self.users_dir),
            patch.object(worker, "resolve_user_dir",
                         lambda uid: self.users_dir / str(uid)),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _add_local_worker(self, user_id=USER_A, name="local1", probe=True):
        return worker.add_worker(
            user_id, name, transport="local",
            local_root=str(self.worker_root), probe=probe,
        )

    def _await_terminal(self, user_id, job_id, timeout=25):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rec = worker.poll_job(user_id, job_id)
            if rec and rec["state"] in worker.TERMINAL_STATES:
                return rec
            time.sleep(0.2)
        return worker.get_job(user_id, job_id)

    def _await_state(self, user_id, job_id, target, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rec = worker.poll_job(user_id, job_id)
            if rec and rec["state"] == target:
                return rec
            time.sleep(0.2)
        return worker.get_job(user_id, job_id)


class RegistryTests(_TempPoolStore):
    def test_add_list_get_remove(self):
        ok, msg, record = self._add_local_worker(probe=False)
        self.assertTrue(ok, msg)
        self.assertEqual(record["transport"], "local")
        self.assertEqual(record["remote_root"], str(self.worker_root))

        self.assertIn("local1", worker.list_workers(USER_A))
        self.assertIsNotNone(worker.get_worker(USER_A, "local1"))

        ok, _ = worker.remove_worker(USER_A, "local1")
        self.assertTrue(ok)
        self.assertEqual(worker.list_workers(USER_A), {})

    def test_rejects_bad_worker_name(self):
        ok, msg, _ = worker.add_worker(USER_A, "bad/name", transport="local",
                                       local_root=str(self.worker_root), probe=False)
        self.assertFalse(ok)
        self.assertIn("name", msg.lower())

    def test_ssh_worker_requires_host(self):
        ok, msg, _ = worker.add_worker(USER_A, "remote", transport="ssh", probe=False)
        self.assertFalse(ok)
        self.assertIn("host", msg.lower())

    def test_probe_populates_caps(self):
        ok, msg, record = self._add_local_worker(probe=True)
        self.assertTrue(ok, msg)
        self.assertIsNotNone(record["caps"])
        self.assertIn("os", record["caps"])
        self.assertIn("binaries", record["caps"])

    def test_workers_are_per_user(self):
        self._add_local_worker(USER_A, "a-box", probe=False)
        self.assertIn("a-box", worker.list_workers(USER_A))
        self.assertEqual(worker.list_workers(USER_B), {})


class RoutingTests(_TempPoolStore):
    def _set_caps(self, name, **caps):
        all_workers = worker._load_all_workers()
        all_workers[str(USER_A)][name]["caps"] = caps
        worker._save_all_workers(all_workers)

    def test_routes_to_first_match_when_no_requirements(self):
        self._add_local_worker(name="w1", probe=False)
        chosen = worker.route_worker(USER_A)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["name"], "w1")

    def test_gpu_requirement_filters(self):
        self._add_local_worker(name="cpu", probe=False)
        self._set_caps("cpu", gpu=False, ram_gb=8.0, binaries={})
        self.assertIsNone(worker.route_worker(USER_A, needs=["gpu"]))

        self._set_caps("cpu", gpu=True, gpu_name="Test GPU", ram_gb=8.0, binaries={})
        chosen = worker.route_worker(USER_A, needs=["gpu"])
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["name"], "cpu")

    def test_min_ram_filters(self):
        self._add_local_worker(name="small", probe=False)
        self._set_caps("small", gpu=False, ram_gb=4.0, binaries={})
        self.assertIsNone(worker.route_worker(USER_A, min_ram_gb=16))
        self.assertIsNotNone(worker.route_worker(USER_A, min_ram_gb=2))

    def test_uncapped_worker_only_for_explicit_use(self):
        self._add_local_worker(name="unknown", probe=False)  # caps None
        # No requirements -> usable.
        self.assertIsNotNone(worker.route_worker(USER_A))
        # A requirement on an uncapped worker -> not routable.
        self.assertIsNone(worker.route_worker(USER_A, needs=["gpu"]))


class LifecycleTests(_TempPoolStore):
    def setUp(self):
        super().setUp()
        self._add_local_worker(probe=False)

    def test_submit_and_complete_captures_stdout(self):
        ok, msg, rec = worker.submit_job(
            USER_A, argv=["python3", "-c", "print('hello-from-worker')"],
            worker_name="local1",
        )
        self.assertTrue(ok, msg)
        rec = self._await_terminal(USER_A, rec["job_id"])
        self.assertEqual(rec["state"], "completed")
        self.assertEqual(rec["returncode"], 0)
        self.assertTrue(rec["log_path"])
        log_text = Path(rec["log_path"]).read_text(encoding="utf-8")
        self.assertIn("hello-from-worker", log_text)

    def test_output_file_is_pulled_back(self):
        ok, msg, rec = worker.submit_job(
            USER_A,
            argv=["python3", "-c", "open('result.txt','w').write('payload')"],
            worker_name="local1", outputs=["result.txt"],
        )
        self.assertTrue(ok, msg)
        rec = self._await_terminal(USER_A, rec["job_id"])
        self.assertEqual(rec["state"], "completed")
        pulled = Path(rec["result_dir"]) / "result.txt"
        self.assertTrue(pulled.is_file())
        self.assertEqual(pulled.read_text(encoding="utf-8"), "payload")

    def test_input_file_is_pushed(self):
        src = self.tmp / "input.txt"
        src.write_text("incoming", encoding="utf-8")
        ok, msg, rec = worker.submit_job(
            USER_A,
            argv=["python3", "-c",
                  "open('echo.txt','w').write(open('input.txt').read())"],
            worker_name="local1", inputs=[str(src)], outputs=["echo.txt"],
        )
        self.assertTrue(ok, msg)
        rec = self._await_terminal(USER_A, rec["job_id"])
        self.assertEqual(rec["state"], "completed")
        pulled = Path(rec["result_dir"]) / "echo.txt"
        self.assertEqual(pulled.read_text(encoding="utf-8"), "incoming")

    def test_failing_command_reports_returncode(self):
        ok, msg, rec = worker.submit_job(
            USER_A, argv=["python3", "-c", "import sys; sys.exit(3)"],
            worker_name="local1",
        )
        self.assertTrue(ok, msg)
        rec = self._await_terminal(USER_A, rec["job_id"])
        self.assertEqual(rec["state"], "failed")
        self.assertEqual(rec["returncode"], 3)

    def test_missing_input_file_is_rejected(self):
        ok, msg, rec = worker.submit_job(
            USER_A, argv=["echo", "hi"], worker_name="local1",
            inputs=["/nonexistent/file/xyz"],
        )
        self.assertFalse(ok)
        self.assertIn("not found", msg.lower())

    def test_unsafe_output_path_is_rejected(self):
        for bad in ["../escape.txt", "/etc/passwd", "sub/../../x"]:
            ok, msg, _ = worker.submit_job(
                USER_A, argv=["echo", "hi"], worker_name="local1", outputs=[bad],
            )
            self.assertFalse(ok, f"{bad!r} should be rejected")
            self.assertIn("output", msg.lower())

    def test_agent_mode_requires_task(self):
        ok, msg, _ = worker.submit_job(USER_A, mode="agent", worker_name="local1")
        self.assertFalse(ok)
        self.assertIn("task", msg.lower())

    def test_unknown_worker_is_rejected(self):
        ok, msg, _ = worker.submit_job(USER_A, argv=["echo", "hi"],
                                       worker_name="ghost")
        self.assertFalse(ok)
        self.assertIn("ghost", msg)

    def test_cancel_running_job(self):
        ok, msg, rec = worker.submit_job(
            USER_A, argv=["sleep", "30"], worker_name="local1",
        )
        self.assertTrue(ok, msg)
        rec = self._await_state(USER_A, rec["job_id"], "running")
        self.assertEqual(rec["state"], "running")
        self.assertIsNotNone(rec.get("pid"))

        ok, _msg, rec = worker.cancel_job(USER_A, rec["job_id"])
        self.assertTrue(ok)
        self.assertEqual(rec["state"], "canceled")
        # Stays terminal on subsequent polls.
        self.assertEqual(worker.poll_job(USER_A, rec["job_id"])["state"], "canceled")


class NotificationTests(_TempPoolStore):
    def setUp(self):
        super().setUp()
        self._add_local_worker(probe=False)

    def test_unnotified_then_marked(self):
        ok, msg, rec = worker.submit_job(
            USER_A, argv=["python3", "-c", "print('done')"], worker_name="local1",
        )
        self.assertTrue(ok, msg)
        self._await_terminal(USER_A, rec["job_id"])

        pending = worker.unnotified_terminal_jobs()
        ids = [r["job_id"] for _uid, r in pending]
        self.assertIn(rec["job_id"], ids)

        worker.mark_notified(USER_A, rec["job_id"])
        pending_after = [r["job_id"] for _uid, r in worker.unnotified_terminal_jobs()]
        self.assertNotIn(rec["job_id"], pending_after)

    def test_format_completion_mentions_worker(self):
        text = worker.format_completion(
            {"state": "completed", "label": "render", "worker": "local1",
             "returncode": 0}
        )
        self.assertIn("local1", text)
        self.assertIn("render", text)

    def test_notified_flag_is_durable_across_restart(self):
        # The completion notification must survive a bot restart: the notified
        # flag lives in compute_jobs.json, never in memory. Proof = the flag is
        # on disk after mark_notified, and a fresh scan (the scheduler's first
        # tick post-restart) neither loses the job nor re-delivers it.
        ok, msg, rec = worker.submit_job(
            USER_A, argv=["python3", "-c", "print('done')"], worker_name="local1",
        )
        self.assertTrue(ok, msg)
        job_id = rec["job_id"]
        self._await_terminal(USER_A, job_id)

        # Before notifying: the scan picks it up.
        self.assertIn(job_id,
                      [r["job_id"] for _uid, r in worker.unnotified_terminal_jobs()])

        worker.mark_notified(USER_A, job_id)

        # The flag is persisted to the on-disk store, not held in memory.
        jobs_file = self.users_dir / str(USER_A) / "compute_jobs.json"
        on_disk = json.loads(jobs_file.read_text(encoding="utf-8"))
        self.assertTrue(on_disk[job_id]["notified"])

        # A fresh scan (always reads from disk) skips the already-notified job.
        self.assertNotIn(job_id,
                         [r["job_id"] for _uid, r in worker.unnotified_terminal_jobs()])


class SshArgvTests(unittest.TestCase):
    """Snapshot the exact scp argv so the remote-path quoting cannot regress.

    SSHTransport is exercised without a real remote by capturing the argv handed
    to subprocess.run. This guards the fix where an unquoted remote half broke
    transfers on a worker whose home/job dir contained spaces.
    """

    def _transport(self):
        return worker.SSHTransport(host="box.local", user="me", port=2222,
                                   identity_file="/keys/id_ed25519")

    def test_push_quotes_remote_path_with_spaces(self):
        t = self._transport()
        remote = "/home/me/Job Folder/in/data.wav"
        with patch.object(worker.subprocess, "run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
            t.push(Path("/tmp/local.wav"), remote)
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "scp")
        # On a push the remote half is the destination (last token).
        self.assertEqual(argv[-1], f"me@box.local:{shlex.quote(remote)}")
        # The unquoted form must never appear as a bare token.
        self.assertNotIn(f"me@box.local:{remote}", argv)

    def test_pull_quotes_remote_path_with_spaces(self):
        t = self._transport()
        remote = "/home/me/Job Folder/out/result.wav"
        dest_dir = Path(tempfile.mkdtemp(prefix="mom_pull_test_"))
        try:
            with patch.object(worker.subprocess, "run") as run:
                run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
                ok = t.pull(remote, dest_dir / "result.wav")
            self.assertTrue(ok)
            argv = run.call_args.args[0]
            self.assertEqual(argv[0], "scp")
            # On a pull the remote half is the source (second-to-last token).
            self.assertEqual(argv[-2], f"me@box.local:{shlex.quote(remote)}")
            self.assertNotIn(f"me@box.local:{remote}", argv)
        finally:
            shutil.rmtree(dest_dir, ignore_errors=True)

    def test_scp_opts_carry_port_identity_and_batchmode(self):
        t = self._transport()
        with patch.object(worker.subprocess, "run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
            t.push(Path("/tmp/x"), "/remote/x")
        argv = run.call_args.args[0]
        self.assertIn("-P", argv)            # scp uppercases the port flag
        self.assertIn("2222", argv)
        self.assertIn("-i", argv)
        self.assertIn("/keys/id_ed25519", argv)
        self.assertIn("BatchMode=yes", argv)


class JobIdSafetyTests(unittest.TestCase):
    def test_job_id_regex_rejects_traversal(self):
        self.assertIsNone(worker._JOB_ID_RE.match("../etc/passwd"))
        self.assertIsNone(worker._JOB_ID_RE.match("a/b"))
        self.assertIsNone(worker._JOB_ID_RE.match(""))
        self.assertIsNotNone(worker._JOB_ID_RE.match("ab12cd34ef56"))


class CliGuardTests(_TempPoolStore):
    def test_cli_enforces_session_binding(self):
        from utils import worker_cli
        with patch.dict(os.environ, {"JARVIS_USER_ID": "999000111"}):
            with self.assertRaises(SystemExit) as cm:
                worker_cli.main(["list", "--user", "222000333"])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
