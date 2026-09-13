"""What each person spent, and what is left on the subscription.

Two different questions, answered from two different places, and the
difference matters enough to keep them apart in the UI as well as here:

**What a person consumed.** Written by this bot, one line per finished turn,
into that user's own directory. Tokens are the CLIs' own counts. The dollar
figure that rides along is Claude Code's ``total_cost_usd``, which is
LIST-PRICE ACCOUNTING and not a charge: on a subscription nobody is billed
it, and the CLI labels the same number ``costBasis: "list"`` in its per-model
breakdown. It is a comparable meter between people, never a bill, and the
field is called ``list_cost_usd`` everywhere so it cannot be read as one.

**What is left on the subscription.** Neither CLI will simply tell you on the
command line, so each is read where it does say:

* Claude Code emits a ``rate_limit_event`` on its stream-json output, carrying
  ``unifiedWindows`` with a five-hour and a seven-day window, each as a
  utilization FRACTION (0.05 = 5%; the CLI itself renders it with
  ``Math.round(utilization * 100)``) and a unix reset time. It arrives as a
  side effect of real work, so the snapshot is only as fresh as the last turn
  and is always shown with the time it was taken.
* Codex has no such event, but its app-server protocol answers
  ``account/rateLimits/read`` with ``usedPercent`` and ``resetsAt`` per window
  straight from the backend. That one is a live read, on demand.

Nothing here ever infers a number it did not read. A meter that cannot be
read is reported as unavailable, with the reason, because a usage bar that
silently shows zero when the real answer is 95% is worse than no bar.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from core import users as _users
from core.users import resolve_user_dir
from utils.safe_json import load_json, save_json

log = logging.getLogger(__name__)

_BOT_DIR = Path(__file__).parent.parent
USAGE_DIR = _BOT_DIR / "data" / "usage"
CLAUDE_LIMITS_FILE = USAGE_DIR / "claude_rate_limits.json"
LEDGER_FILENAME = "usage.jsonl"

# Turns older than this are dropped when the ledger is next trimmed. Long
# enough for a monthly view to be complete, short enough that the file stays
# a few hundred KB on a heavy user.
RETENTION_DAYS = 90
# Trimming rewrites the file, so it is not done on every append.
TRIM_EVERY_BYTES = 512 * 1024

_NUMERIC_FIELDS = (
    "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_creation_tokens", "list_cost_usd",
)


# ─── The per-person ledger ───────────────────────────────────────────


def ledger_path(user_id: int) -> Path:
    return resolve_user_dir(int(user_id)) / LEDGER_FILENAME


def record_turn(
    user_id: int,
    *,
    provider: str,
    model: str,
    effort: str = "",
    engine: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    list_cost_usd: float = 0.0,
    ok: bool = True,
) -> bool:
    """Append one finished turn to this user's ledger. Never raises.

    Returns False when the line could not be written. Usage accounting is
    bookkeeping around somebody's actual answer, so a full disk or a
    read-only volume must cost the log line and nothing else.
    """
    row = {
        "ts": int(time.time()),
        "provider": provider,
        "model": model,
        "effort": effort,
        "engine": engine,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cache_read_tokens": int(cache_read_tokens or 0),
        "cache_creation_tokens": int(cache_creation_tokens or 0),
        "list_cost_usd": round(float(list_cost_usd or 0.0), 6),
        "ok": bool(ok),
    }
    path = ledger_path(user_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("Could not record usage for user %s: %s", user_id, exc)
        return False
    try:
        if path.stat().st_size > TRIM_EVERY_BYTES:
            _trim(path)
    except OSError:
        pass
    return True


def _trim(path: Path) -> None:
    """Drop rows older than RETENTION_DAYS. Best effort, atomic rename."""
    cutoff = time.time() - RETENTION_DAYS * 86400
    kept = [line for line in _read_rows(path) if line.get("ts", 0) >= cutoff]
    tmp = path.with_suffix(".jsonl.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for row in kept:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except OSError as exc:
        log.warning("Could not trim %s: %s", path, exc)
        try:
            tmp.unlink()
        except OSError:
            pass


def _read_rows(path: Path) -> list[dict]:
    """Every parseable row in a ledger. A torn line is skipped, not fatal."""
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def _blank_summary() -> dict:
    summary = {"turns": 0, "failed_turns": 0, "by_model": {}}
    for field in _NUMERIC_FIELDS:
        summary[field] = 0 if field != "list_cost_usd" else 0.0
    return summary


def _add_row(summary: dict, row: dict) -> None:
    summary["turns"] += 1
    if not row.get("ok", True):
        summary["failed_turns"] += 1
    for field in _NUMERIC_FIELDS:
        summary[field] += row.get(field, 0) or 0
    key = row.get("model") or row.get("provider") or "unknown"
    bucket = summary["by_model"].setdefault(
        key, {"turns": 0, "input_tokens": 0, "output_tokens": 0,
              "list_cost_usd": 0.0, "engine": row.get("engine", "")},
    )
    bucket["turns"] += 1
    bucket["input_tokens"] += row.get("input_tokens", 0) or 0
    bucket["output_tokens"] += row.get("output_tokens", 0) or 0
    bucket["list_cost_usd"] += row.get("list_cost_usd", 0.0) or 0.0


def summarise(user_id: int, days: int = 7) -> dict:
    """Totals for one user over the last ``days`` days (0 = everything)."""
    cutoff = 0 if days <= 0 else time.time() - days * 86400
    summary = _blank_summary()
    for row in _read_rows(ledger_path(user_id)):
        if row.get("ts", 0) >= cutoff:
            _add_row(summary, row)
    summary["list_cost_usd"] = round(summary["list_cost_usd"], 4)
    for bucket in summary["by_model"].values():
        bucket["list_cost_usd"] = round(bucket["list_cost_usd"], 4)
    return summary


def summarise_everyone(days: int = 7) -> dict:
    """``{telegram_id: summary}`` for every user with a ledger. Admin view.

    Reads across user directories, which only the bot's own account can do.
    Every caller must gate on admin before showing it.
    """
    out: dict[str, dict] = {}
    try:
        # Through the module, not a from-import: the directory is a module
        # constant, and a snapshot taken at import time would keep pointing
        # at the real tree when a caller (or a test) moves it.
        entries = sorted(_users.USERS_DATA_DIR.iterdir())
    except OSError:
        return out
    for entry in entries:
        if not entry.is_dir() or not (entry / LEDGER_FILENAME).is_file():
            continue
        try:
            uid = int(entry.name)
        except ValueError:
            continue
        summary = summarise(uid, days)
        if summary["turns"]:
            out[str(uid)] = summary
    return out


# ─── Claude: the snapshot its own stream hands us ────────────────────


def _ensure_usage_dir() -> None:
    """Create data/usage private to the bot's account, like the rest of data/.

    The parent is already 0700, so this is depth rather than the only guard;
    core.config tightens its own directories the same way and for the same
    reason, and a chmod that cannot run is a warning, not a refusal to work.
    """
    USAGE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(USAGE_DIR, 0o700)
    except OSError as exc:
        log.warning("Could not chmod %s to 0700: %s", USAGE_DIR, exc)


def save_claude_rate_limits(info: dict) -> bool:
    """Store one ``rate_limit_info`` payload, stamped with the time seen."""
    if not isinstance(info, dict):
        return False
    try:
        _ensure_usage_dir()
        save_json(CLAUDE_LIMITS_FILE, {"captured_at": int(time.time()), "info": info})
        return True
    except OSError as exc:
        log.warning("Could not store Claude rate limits: %s", exc)
        return False


_CLAUDE_WINDOW_LABELS = {
    "five_hour": "5 hours",
    "seven_day": "7 days",
    "seven_day_opus": "7 days (Opus)",
    "seven_day_sonnet": "7 days (Sonnet)",
}


def claude_meter() -> Optional[dict]:
    """The stored Claude snapshot, normalised, or None if none was ever seen.

    ``utilization`` is a fraction of the window; the percentage is what the
    CLI itself renders (``Math.round(utilization * 100)``).
    """
    stored = load_json(CLAUDE_LIMITS_FILE, {})
    info = stored.get("info") if isinstance(stored, dict) else None
    if not isinstance(info, dict):
        return None
    windows = []
    unified = info.get("unifiedWindows")
    if isinstance(unified, dict):
        for key, window in unified.items():
            if not isinstance(window, dict):
                continue
            utilization = window.get("utilization")
            if not isinstance(utilization, (int, float)):
                continue
            windows.append({
                "id": key,
                "label": _CLAUDE_WINDOW_LABELS.get(key, key.replace("_", " ")),
                "used_percent": round(float(utilization) * 100, 1),
                "resets_at": window.get("resetsAt") or window.get("resets_at"),
            })
    windows.sort(key=lambda w: w["id"])
    if not windows:
        return None
    return {
        "source": "claude-cli",
        "live": False,
        "captured_at": stored.get("captured_at"),
        "status": info.get("status"),
        "windows": windows,
    }


# ─── Codex: a live read over its own app-server protocol ─────────────

_CODEX_CACHE_TTL = 60.0
_codex_cache: tuple[float, Optional[dict]] | None = None


def _codex_rpc_rate_limits(binary: str, timeout: float) -> Optional[dict]:
    """Raw ``account/rateLimits/read`` result, or None.

    Speaks the app-server's line-delimited JSON-RPC over stdio: one
    ``initialize``, then the read. Two things this has to get right, both
    measured against the real 0.154.0 binary:

    * **stdin stays open.** Closing it (which is what ``communicate`` does the
      moment it has written) makes the server exit cleanly with rc 0 and no
      output at all, so the read looks like an unavailable account rather
      than a closed pipe.
    * **the server is killed either way.** It is a daemon by design and must
      not outlive the question.
    """
    try:
        proc = subprocess.Popen(
            [binary, "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        log.info("Codex app-server did not start: %s", exc)
        return None

    answer: dict = {}

    def read_until_answer() -> None:
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict) and message.get("id") == 2:
                    answer["message"] = message
                    return
        except (OSError, ValueError):
            return

    reader = threading.Thread(target=read_until_answer, daemon=True)
    reader.start()
    try:
        request = (
            json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "clientInfo": {"name": "myoldmachine", "title": "MyOldMachine",
                                   "version": "1.0.0"},
                    "capabilities": {},
                },
            })
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2,
                          "method": "account/rateLimits/read", "params": None})
            + "\n"
        )
        try:
            proc.stdin.write(request)
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            log.info("Codex app-server closed its input: %s", exc)
            return None
        reader.join(timeout)
        message = answer.get("message")
        if message is None:
            log.info("Codex rate-limit read returned nothing within %ss", timeout)
            return None
        if "error" in message:
            log.info("Codex rate-limit read refused: %s", str(message["error"])[:200])
            return None
        result = message.get("result")
        return result if isinstance(result, dict) else None
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        # Close the pipes explicitly. Popen without communicate() leaves them
        # to the garbage collector, and this runs inside a long-lived bot
        # process that can be asked for the meter all day: one leaked pair of
        # descriptors per press eventually costs the process its file table.
        # Killing first unblocks the reader thread, which treats a pipe
        # closed under it as the end of the stream.
        for pipe in (proc.stdin, proc.stdout):
            try:
                if pipe is not None:
                    pipe.close()
            except OSError:
                pass


def _codex_window(window: object, label: str) -> Optional[dict]:
    if not isinstance(window, dict):
        return None
    used = window.get("usedPercent")
    if not isinstance(used, (int, float)):
        return None
    minutes = window.get("windowDurationMins")
    return {
        "id": label,
        "label": _codex_window_label(minutes, label),
        "used_percent": round(float(used), 1),
        "resets_at": window.get("resetsAt"),
    }


def _codex_window_label(minutes: object, fallback: str) -> str:
    """A window's own duration reads better than "primary"/"secondary"."""
    if not isinstance(minutes, (int, float)) or minutes <= 0:
        return fallback
    minutes = int(minutes)
    if minutes % 10080 == 0:
        weeks = minutes // 10080
        return "7 days" if weeks == 1 else f"{weeks * 7} days"
    if minutes % 1440 == 0:
        days = minutes // 1440
        return "24 hours" if days == 1 else f"{days} days"
    if minutes % 60 == 0:
        hours = minutes // 60
        return "1 hour" if hours == 1 else f"{hours} hours"
    return f"{minutes} minutes"


def codex_meter(*, timeout: float = 25.0, binary: str = "codex",
                use_cache: bool = True) -> Optional[dict]:
    """Live subscription usage for the Codex account, or None.

    Cached briefly: the read costs a process launch and a network round trip,
    and the picker asks on every render.
    """
    global _codex_cache
    now = time.monotonic()
    if use_cache and _codex_cache is not None and (now - _codex_cache[0]) < _CODEX_CACHE_TTL:
        return _codex_cache[1]
    raw = _codex_rpc_rate_limits(binary, timeout)
    meter = None
    if isinstance(raw, dict):
        limits = raw.get("rateLimits")
        if isinstance(limits, dict):
            windows = [
                w for w in (
                    _codex_window(limits.get("primary"), "primary"),
                    _codex_window(limits.get("secondary"), "secondary"),
                ) if w
            ]
            if windows:
                meter = {
                    "source": "codex-app-server",
                    "live": True,
                    "captured_at": int(time.time()),
                    "plan": limits.get("planType"),
                    "status": None if raw.get("ordinaryUsageAllowed", True) else "rejected",
                    "windows": windows,
                }
    _codex_cache = (now, meter)
    return meter


def codex_cache_clear() -> None:
    global _codex_cache
    _codex_cache = None


def meters(*, codex_timeout: float = 25.0) -> dict:
    """Both subscription meters, each either a reading or an explained gap."""
    claude = claude_meter()
    codex = codex_meter(timeout=codex_timeout)
    return {
        "claude": claude or {
            "source": "claude-cli", "unavailable": True,
            "reason": "No reading yet. Claude Code reports its limits during a "
                      "turn, so this fills in after the next Claude answer.",
        },
        "codex": codex or {
            "source": "codex-app-server", "unavailable": True,
            "reason": "Codex did not answer a usage read. It needs the Codex "
                      "CLI installed and signed in on this machine.",
        },
    }
