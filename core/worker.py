"""core/worker.py — multi-machine compute pool for MyOldMachine.

Offload heavy compute jobs (renders, encodes, stem separation, ML training)
from this MOM instance (the "orchestrator") to other machines the same user
owns ("workers"). Jobs run asynchronously: the orchestrator pushes a
self-contained runner to the worker over SSH, launches it detached, and later
polls a status file for completion. Workers expose NO inbound service — all
contact is the orchestrator reaching out.

Trust model: pairing a worker means your orchestrator can run commands on it as
your SSH user — exactly what you could already do by SSHing in yourself. Only
pair machines you control. The registry stores connection info (host, ssh user,
port, and an optional identity-file path) — never passwords or private keys.

Isolation: workers and jobs are per Telegram user. Worker records live in
data/workers.json keyed by user id; job records live under each user's own data
dir (data/users/<id>/compute_jobs.json). worker_cli enforces session binding so
one user can never submit to, poll, or cancel another user's jobs.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.config import DATA_DIR, USERS_DIR
from core.users import resolve_user_dir
from utils.safe_json import load_json, save_json

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORKERS_FILE = DATA_DIR / "workers.json"
RUNNER_PATH = Path(__file__).parent.parent / "utils" / "worker_runner.py"

# Directory created on each worker to hold per-job workspaces.
REMOTE_DIRNAME = ".mom-worker"

ACTIVE_STATES = {"submitted", "running"}
TERMINAL_STATES = {"completed", "failed", "canceled"}

# Job IDs are uuid4 hex slices; this guards any code path that might use the id
# to build a filesystem path against traversal or injection.
_JOB_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")
# Worker names appear in registry keys and log lines; keep them simple.
_WORKER_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _is_safe_relpath(name: str) -> bool:
    """True if name is a plain relative path that stays inside its base dir.

    Output filenames are joined onto both the worker's job dir and the user's
    local results dir, so an absolute path or a '..' component must never be
    accepted — it would let a job read or overwrite files outside that dir.
    """
    if not name or name.startswith(("/", "~")) or "\x00" in name:
        return False
    parts = Path(name).parts
    return ".." not in parts and not Path(name).is_absolute()

# Probe source executed on the worker (stdlib only, no single quotes so it
# survives shlex.quote single-quote wrapping when sent over SSH).
_PROBE_SRC = """
import json, os, platform, shutil, subprocess


def _ram_gb():
    try:
        if platform.system() == "Darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                 capture_output=True, text=True).stdout.strip()
            return round(int(out) / 1024 / 1024 / 1024, 1)
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    return round(int(line.split()[1]) / 1024 / 1024, 1)
    except Exception:
        return None
    return None


_gpu = bool(shutil.which("nvidia-smi"))
_gpu_name = None
if _gpu:
    try:
        _gpu_name = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True,
        ).stdout.strip().splitlines()[0]
    except Exception:
        _gpu_name = None

_bins = {b: bool(shutil.which(b))
         for b in ["ffmpeg", "claude", "python3", "git", "blender"]}

print(json.dumps({
    "home": os.path.expanduser("~"),
    "hostname": platform.node(),
    "os": platform.system(),
    "arch": platform.machine(),
    "cpu_cores": os.cpu_count(),
    "ram_gb": _ram_gb(),
    "gpu": _gpu,
    "gpu_name": _gpu_name,
    "binaries": _bins,
}))
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Transports — how the orchestrator runs commands and moves files on a worker.
# LocalTransport executes on this machine (used for tests and a localhost
# worker); SSHTransport reaches a remote machine. Both implement the same four
# primitives so the job lifecycle code is transport-agnostic.
# ---------------------------------------------------------------------------

class Transport:
    """Abstract worker transport."""

    def run(self, argv: list[str], timeout: float = 60) -> tuple[int, str, str]:
        """Run argv on the worker and wait. Returns (returncode, stdout, stderr)."""
        raise NotImplementedError

    def run_detached(self, argv: list[str], cwd: str, log_path: str) -> tuple[int, str, str]:
        """Launch argv in the background on the worker and return immediately.

        stdout/stderr are redirected to log_path on the worker; stdin is closed.
        """
        raise NotImplementedError

    def push(self, local_path: Path, remote_path: str) -> None:
        """Copy a local file to remote_path on the worker."""
        raise NotImplementedError

    def pull(self, remote_path: str, local_path: Path) -> bool:
        """Copy remote_path from the worker to a local file. Returns success."""
        raise NotImplementedError


# Detached children launched by LocalTransport, retained so we can reap them
# instead of leaking handles or emitting spurious ResourceWarnings.
_LOCAL_DETACHED: list[subprocess.Popen] = []


class LocalTransport(Transport):
    """Runs commands and copies files on the local machine.

    Remote paths are ordinary local absolute paths. Used by the test suite and
    by a degenerate "localhost" worker for trying the feature on one box.
    """

    def run(self, argv: list[str], timeout: float = 60) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {timeout}s"
        except OSError as exc:
            return 127, "", str(exc)

    def run_detached(self, argv: list[str], cwd: str, log_path: str) -> tuple[int, str, str]:
        os.makedirs(cwd, exist_ok=True)
        # Reap handles for children that have already exited so we neither leak
        # Popen objects in a long-lived orchestrator nor trip ResourceWarning.
        _LOCAL_DETACHED[:] = [p for p in _LOCAL_DETACHED if p.poll() is None]
        try:
            with open(log_path, "wb") as log_fh, open(os.devnull, "rb") as devnull:
                proc = subprocess.Popen(
                    argv, cwd=cwd, stdout=log_fh, stderr=subprocess.STDOUT,
                    stdin=devnull, start_new_session=True,
                )
            _LOCAL_DETACHED.append(proc)
            return 0, "launched", ""
        except OSError as exc:
            return 127, "", str(exc)

    def push(self, local_path: Path, remote_path: str) -> None:
        os.makedirs(os.path.dirname(remote_path), exist_ok=True)
        shutil.copy2(str(local_path), remote_path)

    def pull(self, remote_path: str, local_path: Path) -> bool:
        if not os.path.exists(remote_path):
            return False
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(remote_path, str(local_path))
        return True


class SSHTransport(Transport):
    """Runs commands and copies files on a remote worker over SSH/scp.

    BatchMode=yes ensures we never hang on a password prompt (key auth only);
    a misconfigured worker fails fast instead. accept-new trusts a worker's
    host key on first contact (TOFU) so pairing does not require a manual
    known_hosts step.
    """

    def __init__(self, host: str, user: Optional[str] = None,
                 port: Optional[int] = None, identity_file: Optional[str] = None):
        self.host = host
        self.user = user
        self.port = port
        self.identity_file = identity_file

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    def _ssh_opts(self) -> list[str]:
        opts = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                "-o", "StrictHostKeyChecking=accept-new"]
        if self.port:
            opts += ["-p", str(self.port)]
        if self.identity_file:
            opts += ["-i", self.identity_file]
        return opts

    def _scp_opts(self) -> list[str]:
        opts = ["-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                "-o", "StrictHostKeyChecking=accept-new"]
        if self.port:
            opts += ["-P", str(self.port)]
        if self.identity_file:
            opts += ["-i", self.identity_file]
        return opts

    def run(self, argv: list[str], timeout: float = 60) -> tuple[int, str, str]:
        remote_cmd = " ".join(shlex.quote(a) for a in argv)
        full = ["ssh", *self._ssh_opts(), self.target, remote_cmd]
        try:
            proc = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"ssh timed out after {timeout}s"
        except OSError as exc:
            return 127, "", str(exc)

    def run_detached(self, argv: list[str], cwd: str, log_path: str) -> tuple[int, str, str]:
        inner = " ".join(shlex.quote(a) for a in argv)
        # setsid + nohup + closed stdin detaches the runner from the SSH channel
        # so ssh returns as soon as the foreground `echo` completes.
        remote_cmd = (
            f"cd {shlex.quote(cwd)} && "
            f"setsid nohup {inner} > {shlex.quote(log_path)} 2>&1 < /dev/null & "
            f"echo launched"
        )
        full = ["ssh", "-n", *self._ssh_opts(), self.target, remote_cmd]
        try:
            proc = subprocess.run(full, capture_output=True, text=True, timeout=30)
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return 124, "", "ssh launch timed out"
        except OSError as exc:
            return 127, "", str(exc)

    def push(self, local_path: Path, remote_path: str) -> None:
        # scp re-parses the remote half through a shell, so quote it to survive
        # spaces in the worker's home/job path.
        remote = f"{self.target}:{shlex.quote(remote_path)}"
        full = ["scp", *self._scp_opts(), str(local_path), remote]
        subprocess.run(full, capture_output=True, text=True, timeout=300, check=True)

    def pull(self, remote_path: str, local_path: Path) -> bool:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        remote = f"{self.target}:{shlex.quote(remote_path)}"
        full = ["scp", *self._scp_opts(), remote, str(local_path)]
        proc = subprocess.run(full, capture_output=True, text=True, timeout=300)
        return proc.returncode == 0


def make_transport(record: dict) -> Transport:
    """Build the Transport for a worker record."""
    if record.get("transport") == "local":
        return LocalTransport()
    return SSHTransport(
        host=record["host"],
        user=record.get("user"),
        port=record.get("port"),
        identity_file=record.get("identity_file"),
    )


# ---------------------------------------------------------------------------
# Capability probe
# ---------------------------------------------------------------------------

def probe_worker(transport: Transport) -> Optional[dict]:
    """Collect a worker's capabilities over the transport.

    Returns a caps dict (home, hostname, os, arch, cpu_cores, ram_gb, gpu,
    gpu_name, binaries) or None if the worker is unreachable / has no python3.
    """
    rc, out, _err = transport.run(["python3", "-c", _PROBE_SRC], timeout=30)
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Worker registry (data/workers.json, keyed by user id)
# ---------------------------------------------------------------------------

def _load_all_workers() -> dict:
    return load_json(WORKERS_FILE, default={})


def _save_all_workers(data: dict) -> None:
    save_json(WORKERS_FILE, data)


def list_workers(user_id: int) -> dict:
    """Return {name: record} for one user's workers."""
    return _load_all_workers().get(str(user_id), {})


def get_worker(user_id: int, name: str) -> Optional[dict]:
    return list_workers(user_id).get(name)


def add_worker(user_id: int, name: str, host: str = "", user: str = None,
               port: int = None, identity_file: str = None, label: str = "",
               transport: str = "ssh", local_root: str = None,
               probe: bool = True) -> tuple[bool, str, Optional[dict]]:
    """Register or update a worker for a user.

    For transport="local" (tests / localhost), local_root sets the base
    directory the worker uses for job workspaces. For transport="ssh", host is
    required and remote_root is discovered by probing the worker's home dir.
    """
    if not _WORKER_NAME_RE.match(name or ""):
        return False, "Worker name must be 1-64 chars of [A-Za-z0-9._-].", None
    if transport == "ssh" and not host:
        return False, "An SSH worker requires --host.", None

    record = {
        "name": name,
        "transport": transport,
        "host": host,
        "user": user,
        "port": port,
        "identity_file": identity_file,
        "label": label,
        "caps": None,
        "remote_root": None,
        "added_at": _now(),
    }

    if transport == "local":
        base = local_root or str(Path.home() / REMOTE_DIRNAME)
        record["remote_root"] = base

    if probe:
        caps = probe_worker(make_transport(record))
        if caps:
            record["caps"] = caps
            if transport != "local":
                home = caps.get("home") or "."
                record["remote_root"] = f"{home}/{REMOTE_DIRNAME}"
        elif transport == "ssh":
            return False, (f"Could not reach worker {name!r} (ssh/python3 probe "
                           f"failed). Check SSH key auth and that python3 exists "
                           f"on the worker."), None

    all_workers = _load_all_workers()
    all_workers.setdefault(str(user_id), {})[name] = record
    _save_all_workers(all_workers)
    return True, f"Worker {name!r} registered.", record


def remove_worker(user_id: int, name: str) -> tuple[bool, str]:
    all_workers = _load_all_workers()
    user_workers = all_workers.get(str(user_id), {})
    if name not in user_workers:
        return False, f"No worker named {name!r}."
    del user_workers[name]
    if user_workers:
        all_workers[str(user_id)] = user_workers
    else:
        all_workers.pop(str(user_id), None)
    _save_all_workers(all_workers)
    return True, f"Worker {name!r} removed."


def refresh_caps(user_id: int, name: str) -> tuple[bool, str, Optional[dict]]:
    """Re-probe a worker and store fresh capabilities."""
    record = get_worker(user_id, name)
    if not record:
        return False, f"No worker named {name!r}.", None
    caps = probe_worker(make_transport(record))
    if not caps:
        return False, f"Could not reach worker {name!r}.", None
    record["caps"] = caps
    if record.get("transport") != "local":
        home = caps.get("home") or "."
        record["remote_root"] = f"{home}/{REMOTE_DIRNAME}"
    all_workers = _load_all_workers()
    all_workers.setdefault(str(user_id), {})[name] = record
    _save_all_workers(all_workers)
    return True, f"Refreshed capabilities for {name!r}.", caps


def _worker_satisfies(record: dict, needs: list[str], min_ram_gb: Optional[float]) -> bool:
    caps = record.get("caps")
    if not caps:
        # A worker with unknown caps can only be used by explicit selection.
        return not needs and min_ram_gb is None
    for need in needs or []:
        if need == "gpu":
            if not caps.get("gpu"):
                return False
        elif not caps.get("binaries", {}).get(need):
            return False
    if min_ram_gb is not None:
        ram = caps.get("ram_gb")
        if ram is None or ram < min_ram_gb:
            return False
    return True


def route_worker(user_id: int, needs: list[str] = None,
                 min_ram_gb: float = None) -> Optional[dict]:
    """Pick a worker that satisfies the job's requirements, or None."""
    candidates = [r for r in list_workers(user_id).values()
                  if _worker_satisfies(r, needs, min_ram_gb)]
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Job records (data/users/<id>/compute_jobs.json)
# ---------------------------------------------------------------------------

def _jobs_file(user_id: int) -> Path:
    return resolve_user_dir(user_id) / "compute_jobs.json"


def _results_dir(user_id: int, job_id: str) -> Path:
    return resolve_user_dir(user_id) / "compute_results" / job_id


def load_jobs(user_id: int) -> dict:
    return load_json(_jobs_file(user_id), default={})


def _save_jobs(user_id: int, data: dict) -> None:
    resolve_user_dir(user_id).mkdir(parents=True, exist_ok=True)
    save_json(_jobs_file(user_id), data)


def get_job(user_id: int, job_id: str) -> Optional[dict]:
    return load_jobs(user_id).get(job_id)


def list_jobs(user_id: int) -> list[dict]:
    return list(load_jobs(user_id).values())


def _put_job(user_id: int, record: dict) -> None:
    jobs = load_jobs(user_id)
    jobs[record["job_id"]] = record
    _save_jobs(user_id, jobs)


def delete_job(user_id: int, job_id: str) -> bool:
    jobs = load_jobs(user_id)
    if job_id not in jobs:
        return False
    del jobs[job_id]
    _save_jobs(user_id, jobs)
    return True


# ---------------------------------------------------------------------------
# Job lifecycle: submit -> (running) -> poll -> completed/failed; or cancel
# ---------------------------------------------------------------------------

def submit_job(user_id: int, argv: list[str] = None, *, worker_name: str = None,
               mode: str = "spine", task: str = None, needs: list[str] = None,
               min_ram_gb: float = None, inputs: list[str] = None,
               outputs: list[str] = None, timeout: int = None,
               label: str = None, launch_wait: float = 8.0) -> tuple[bool, str, Optional[dict]]:
    """Submit one compute job to a worker and launch it asynchronously.

    mode="spine": run argv verbatim on the worker (no LLM involved).
    mode="agent": opt-in; wraps `claude -p <task>` (the worker must have the
    claude CLI configured). Everything else is identical — agent mode is just a
    different command.
    """
    if mode == "agent":
        if not task or not task.strip():
            return False, "Agent mode requires a non-empty --task.", None
        argv = ["claude", "-p", task]
    if not argv:
        return False, "No command to run (provide a command or --task).", None

    for base in outputs or []:
        if not _is_safe_relpath(base):
            return False, (f"Unsafe output path {base!r}: outputs must be plain "
                           f"relative names inside the job dir."), None

    if worker_name:
        record = get_worker(user_id, worker_name)
        if not record:
            return False, f"No worker named {worker_name!r}.", None
    else:
        record = route_worker(user_id, needs, min_ram_gb)
        if not record:
            return False, ("No worker matches the requirements. Register one with "
                           "`worker_cli.py add` or relax --needs/--min-ram."), None

    # Ensure we know where to put the job on the worker.
    if not record.get("remote_root"):
        ok, msg, _caps = refresh_caps(user_id, record["name"])
        if not ok:
            return False, msg, None
        record = get_worker(user_id, record["name"])

    transport = make_transport(record)
    job_id = uuid.uuid4().hex[:12]
    if not _JOB_ID_RE.match(job_id):  # belt-and-braces; uuid hex always matches
        return False, "Failed to generate a safe job id.", None

    remote_dir = f"{record['remote_root']}/jobs/{job_id}"
    status_path = f"{remote_dir}/status.json"
    boot_path = f"{remote_dir}/boot.log"

    rc, _out, err = transport.run(["mkdir", "-p", remote_dir], timeout=30)
    if rc != 0:
        return False, f"Could not create job dir on worker: {err.strip() or rc}", None

    # Stage the runner, inputs, and spec.
    transport.push(RUNNER_PATH, f"{remote_dir}/worker_runner.py")
    pushed_inputs = []
    for src in inputs or []:
        src_path = Path(src).expanduser()
        if not src_path.is_file():
            return False, f"Input file not found: {src_path}", None
        base = src_path.name
        transport.push(src_path, f"{remote_dir}/{base}")
        pushed_inputs.append(base)

    spec = {
        "job_id": job_id,
        "argv": argv,
        "timeout": timeout,
        "outputs": outputs or [],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as tf:
        json.dump(spec, tf)
        spec_local = tf.name
    try:
        transport.push(Path(spec_local), f"{remote_dir}/spec.json")
    finally:
        os.unlink(spec_local)

    record_job = {
        "job_id": job_id,
        "user_id": user_id,
        "worker": record["name"],
        "state": "submitted",
        "mode": mode,
        "argv": argv,
        "label": label or " ".join(argv)[:60],
        "needs": needs or [],
        "min_ram_gb": min_ram_gb,
        "remote_dir": remote_dir,
        "inputs": pushed_inputs,
        "outputs": outputs or [],
        "timeout": timeout,
        "returncode": None,
        "error": None,
        "pid": None,
        "pgid": None,
        "submitted_at": _now(),
        "started_at": None,
        "finished_at": None,
        "log_path": None,
        "result_dir": None,
        "notified": False,
    }
    _put_job(user_id, record_job)

    # Launch detached.
    rc, _out, err = transport.run_detached(
        ["python3", f"{remote_dir}/worker_runner.py", f"{remote_dir}/spec.json"],
        cwd=remote_dir, log_path=boot_path,
    )
    if rc != 0:
        record_job["state"] = "failed"
        record_job["error"] = f"launch failed: {err.strip() or rc}"
        record_job["finished_at"] = _now()
        _put_job(user_id, record_job)
        return False, record_job["error"], record_job

    # NOTE: this blocks up to launch_wait seconds. From any async context (e.g.
    # a Telegram handler) call submit_job via asyncio.to_thread so the event
    # loop stays free.
    # Confirm the runner came up by waiting briefly for its status file. A
    # status we can read means the runner ran: the job may already be running,
    # completed, or failed-with-a-returncode — all successful *submissions*.
    launch_broke = False
    deadline = time.monotonic() + launch_wait
    while time.monotonic() < deadline:
        srec, _changed = _read_remote_status(transport, status_path)
        if srec:
            _apply_status(record_job, srec)
            break
        time.sleep(0.3)
    else:
        # No status ever appeared. If the boot log has output, the runner itself
        # crashed before recording anything — that is a real launch failure.
        brc, bout, _ = transport.run(["cat", boot_path], timeout=15)
        if brc == 0 and bout.strip():
            launch_broke = True
            record_job["state"] = "failed"
            record_job["error"] = f"runner did not start: {bout.strip()[-400:]}"
            record_job["finished_at"] = _now()

    if record_job["state"] in TERMINAL_STATES:
        _finalize_terminal(transport, user_id, record_job)
    _put_job(user_id, record_job)

    if launch_broke:
        return False, record_job["error"] or "job failed to start", record_job
    return True, f"Job {job_id} submitted to {record['name']!r}.", record_job


def _read_remote_status(transport: Transport, status_path: str) -> tuple[Optional[dict], bool]:
    """Read and parse the worker's status.json. Returns (status_dict, ok)."""
    rc, out, _err = transport.run(["cat", status_path], timeout=30)
    if rc != 0 or not out.strip():
        return None, False
    try:
        return json.loads(out), True
    except ValueError:
        return None, False


def _apply_status(record: dict, status: dict) -> None:
    """Merge a worker status dict into the local job record."""
    state = status.get("state")
    if state in ACTIVE_STATES or state in TERMINAL_STATES:
        record["state"] = state
    if status.get("pid") is not None:
        record["pid"] = status["pid"]
    if status.get("pgid") is not None:
        record["pgid"] = status["pgid"]
    if status.get("started_at"):
        record["started_at"] = status["started_at"]
    if status.get("finished_at"):
        record["finished_at"] = status["finished_at"]
    if status.get("returncode") is not None:
        record["returncode"] = status["returncode"]
    if status.get("error"):
        record["error"] = status["error"]


def _finalize_terminal(transport: Transport, user_id: int, record: dict) -> None:
    """Pull the log and declared output files for a finished job."""
    local_dir = _results_dir(user_id, record["job_id"])
    local_dir.mkdir(parents=True, exist_ok=True)
    log_remote = f"{record['remote_dir']}/output.log"
    log_local = local_dir / "output.log"
    if transport.pull(log_remote, log_local):
        record["log_path"] = str(log_local)
    for base in record.get("outputs", []):
        transport.pull(f"{record['remote_dir']}/{base}", local_dir / base)
    record["result_dir"] = str(local_dir)


def poll_job(user_id: int, job_id: str, transport: Transport = None) -> Optional[dict]:
    """Advance one job: read its remote status, pull results if it finished."""
    record = get_job(user_id, job_id)
    if not record:
        return None
    if record["state"] in TERMINAL_STATES:
        return record

    if transport is None:
        wrec = get_worker(user_id, record["worker"])
        if not wrec:
            record["state"] = "failed"
            record["error"] = "worker is no longer registered"
            record["finished_at"] = _now()
            _put_job(user_id, record)
            return record
        transport = make_transport(wrec)

    status, ok = _read_remote_status(transport, f"{record['remote_dir']}/status.json")
    if not ok:
        return record  # still starting or transiently unreachable; no transition
    _apply_status(record, status)
    if record["state"] in TERMINAL_STATES:
        _finalize_terminal(transport, user_id, record)
    _put_job(user_id, record)
    return record


def poll_user_jobs(user_id: int) -> list[tuple[dict, str]]:
    """Poll all active jobs for a user. Returns (record, old_state) transitions."""
    transitions = []
    for record in list_jobs(user_id):
        if record["state"] not in ACTIVE_STATES:
            continue
        old_state = record["state"]
        updated = poll_job(user_id, record["job_id"])
        if updated and updated["state"] != old_state and updated["state"] in TERMINAL_STATES:
            transitions.append((updated, old_state))
    return transitions


def poll_all_jobs() -> list[tuple[int, dict, str]]:
    """Poll active jobs across every user. Returns (user_id, record, old_state)."""
    results = []
    if not WORKERS_FILE.exists() or not USERS_DIR.exists():
        return results
    for user_dir in USERS_DIR.iterdir():
        if not user_dir.is_dir() or not (user_dir / "compute_jobs.json").exists():
            continue
        try:
            uid = int(user_dir.name)
        except ValueError:
            continue
        for record, old_state in poll_user_jobs(uid):
            results.append((uid, record, old_state))
    return results


def unnotified_terminal_jobs() -> list[tuple[int, dict]]:
    """Find finished jobs the user has not yet been told about.

    Used by the scheduler poll loop to deliver completion notifications even
    across a bot restart or a transient send failure (the notified flag is the
    durable record of "already told the user").
    """
    results = []
    if not WORKERS_FILE.exists() or not USERS_DIR.exists():
        return results
    for user_dir in USERS_DIR.iterdir():
        if not user_dir.is_dir() or not (user_dir / "compute_jobs.json").exists():
            continue
        try:
            uid = int(user_dir.name)
        except ValueError:
            continue
        for record in list_jobs(uid):
            if record["state"] in TERMINAL_STATES and not record.get("notified"):
                results.append((uid, record))
    return results


def mark_notified(user_id: int, job_id: str) -> None:
    record = get_job(user_id, job_id)
    if record and not record.get("notified"):
        record["notified"] = True
        _put_job(user_id, record)


def cancel_job(user_id: int, job_id: str) -> tuple[bool, str, Optional[dict]]:
    """Stop a running job by killing its process group on the worker."""
    record = get_job(user_id, job_id)
    if not record:
        return False, f"No job {job_id!r}.", None
    if record["state"] in TERMINAL_STATES:
        return False, f"Job {job_id} is already {record['state']}.", record

    wrec = get_worker(user_id, record["worker"])
    if not wrec:
        record["state"] = "canceled"
        record["finished_at"] = _now()
        _put_job(user_id, record)
        return True, f"Job {job_id} marked canceled (worker unregistered).", record

    transport = make_transport(wrec)
    # Refresh once so we have the latest pid/pgid the runner recorded.
    status, ok = _read_remote_status(transport, f"{record['remote_dir']}/status.json")
    if ok:
        _apply_status(record, status)
    pgid = record.get("pgid")
    pid = record.get("pid")
    if isinstance(pgid, int) and pgid > 1:
        transport.run(["kill", "-TERM", f"-{pgid}"], timeout=15)
        time.sleep(0.5)
        transport.run(["kill", "-KILL", f"-{pgid}"], timeout=15)
    elif isinstance(pid, int) and pid > 1:
        transport.run(["kill", "-TERM", str(pid)], timeout=15)

    record["state"] = "canceled"
    record["finished_at"] = _now()
    _finalize_terminal(transport, user_id, record)
    _put_job(user_id, record)
    return True, f"Job {job_id} canceled.", record


def forget_job(user_id: int, job_id: str, wipe_remote: bool = True) -> tuple[bool, str]:
    """Delete a job's local record and (optionally) its remote workspace."""
    record = get_job(user_id, job_id)
    if not record:
        return False, f"No job {job_id!r}."
    if wipe_remote:
        wrec = get_worker(user_id, record["worker"])
        if wrec and record.get("remote_dir"):
            make_transport(wrec).run(["rm", "-rf", record["remote_dir"]], timeout=30)
    delete_job(user_id, job_id)
    return True, f"Forgot job {job_id}."


def format_completion(record: dict) -> str:
    """Build a Telegram notification line for a finished job."""
    state = record["state"]
    label = record.get("label") or record["job_id"]
    worker = record.get("worker", "?")
    if state == "completed":
        head = f"✅ Compute job done on {worker}: {label}"
    elif state == "failed":
        head = f"⚠️ Compute job failed on {worker}: {label}"
    else:
        head = f"\U0001f6d1 Compute job {state} on {worker}: {label}"
    rc = record.get("returncode")
    if rc is not None:
        head += f"\nExit code: {rc}"
    if record.get("error"):
        head += f"\n{record['error'][:300]}"
    if record.get("result_dir"):
        head += f"\nResults: {record['result_dir']}"
    return head
