#!/usr/bin/env python3
"""Worker-side job runner for MyOldMachine's compute pool. Stdlib only.

The orchestrator (see core/worker.py) pushes this script into a per-job
directory on a worker machine and launches it detached. It reads a spec.json
sitting next to itself, runs the job, and records progress in a status.json in
the same directory. It opens no network port — the orchestrator reaches in to
read status and pull results.

Usage:
    python3 worker_runner.py <path/to/spec.json>

spec.json:
    {"job_id": str, "argv": [str, ...], "timeout": int|null, "outputs": [str]}

status.json (written atomically, polled by the orchestrator):
    {"state": "running"|"completed"|"failed",
     "pid": int, "pgid": int, "returncode": int|null,
     "error": str|null, "started_at": iso, "finished_at": iso}

The job runs in its own process group, so the orchestrator can cancel it with
`kill -<pgid>` while this runner survives to write the terminal status.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
from datetime import datetime


def _now() -> str:
    return datetime.now().isoformat()


def _write_status(path: str, data: dict) -> None:
    """Write status atomically so the poller never reads a half-written file."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _kill_group(pgid: int, proc: subprocess.Popen) -> None:
    """Terminate the job's process group, escalating to SIGKILL."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (OSError, AttributeError):
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (OSError, AttributeError):
        try:
            proc.kill()
        except OSError:
            pass


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: worker_runner.py <spec.json>", file=sys.stderr)
        return 2

    spec_path = os.path.abspath(argv[1])
    job_dir = os.path.dirname(spec_path)
    status_path = os.path.join(job_dir, "status.json")
    log_path = os.path.join(job_dir, "output.log")

    status = {
        "state": "submitted", "pid": None, "pgid": None,
        "returncode": None, "error": None,
        "started_at": None, "finished_at": None,
    }

    try:
        with open(spec_path, encoding="utf-8") as f:
            spec = json.load(f)
        cmd = spec.get("argv") or []
        timeout = spec.get("timeout")
        if not cmd:
            raise ValueError("spec has no argv to run")

        with open(log_path, "wb") as log_fh:
            proc = subprocess.Popen(
                cmd, cwd=job_dir, stdout=log_fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
            try:
                pgid = os.getpgid(proc.pid)
            except (OSError, AttributeError):
                pgid = proc.pid

            status.update(state="running", pid=proc.pid, pgid=pgid,
                          started_at=_now())
            _write_status(status_path, status)

            try:
                proc.wait(timeout=timeout)
                status["returncode"] = proc.returncode
                status["state"] = "completed" if proc.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                _kill_group(pgid, proc)
                status["state"] = "failed"
                status["error"] = f"timed out after {timeout}s"
                status["returncode"] = proc.returncode
    except Exception as exc:
        status["state"] = "failed"
        status["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        status["finished_at"] = _now()
        try:
            _write_status(status_path, status)
        except Exception as exc:  # last resort: leave a breadcrumb in the log
            print(f"worker_runner: failed to write status: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    # Detach into our own session if the launcher did not (defensive; the
    # orchestrator already uses setsid/start_new_session).
    if hasattr(os, "setsid"):
        try:
            os.setsid()
        except OSError:
            pass
    sys.exit(main(sys.argv))
