"""Watchlist alert sweep, run by the bot scheduler every 15 minutes.

Registered per user as command job `tradewatch-<id>` (cron:*/15:8-23
local time) by watch.py on the first add. For each watched symbol: skip
stocks outside the NYSE session, fetch fresh readings, run the signal
engine, deliver any alerts to Telegram, then persist the new rule state.
A symbol's new state is saved only after its alerts actually went out, so
a Telegram outage retries next sweep rather than losing the alert (at the
cost of a rare duplicate if delivery half succeeded).

Blindness policy: when every symbol due for evaluation fails to produce
readings, one such sweep is treated as a transient blip and exits 0; the
second within ~35 minutes exits 1, which makes the scheduler ping the
failure (with this script's output, via the job's log capture), and the
ping is then throttled to one per 4 hours of sustained blindness. Detail
lines land in sweep.log every time either way. A healthy sweep resets
the record.

Run under the bot venv:
  python skills/trading/scripts/alert_sweep.py --user <id>
  python skills/trading/scripts/alert_sweep.py --user <id> --dry-run

Silent when nothing fires; prints one line per alert, error, or skip so
the job's log stays readable.
"""

import argparse
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import trading_common as tc
import watch  # inserts BOT_DIR into sys.path

from utils.safe_json import load_json, save_json  # noqa: E402
from utils.session_guard import enforce as enforce_session_user  # noqa: E402

BOT_DIR = watch.BOT_DIR
SEND_SCRIPT = BOT_DIR / "utils" / "send_to_telegram.py"

# One all-fail sweep is a blip; the second inside this window is an outage.
UNHEALTHY_CONSECUTIVE_MINUTES = 35
# Sustained blindness pings at most this often via exit code 1.
UNHEALTHY_PING_HOURS = 4.0


def send_alert(user_id: int, text: str) -> bool:
    # One bot venv: the interpreter running this sweep also has httpx/dotenv
    # for send_to_telegram, so reuse it directly.
    try:
        proc = subprocess.run(
            [sys.executable, str(SEND_SCRIPT), "--user", str(user_id), "--message", text],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"send failed: {exc}")
        return False
    if proc.returncode != 0:
        print(f"send failed: {(proc.stderr or proc.stdout)[-200:]}")
    return proc.returncode == 0


def sweep(user_id: int, dry_run: bool = False, base_dir: Path | None = None,
          now: datetime | None = None) -> tuple[int, bool]:
    """Returns (alerts delivered, healthy). healthy is False only when every
    symbol due for evaluation failed to produce readings; health_exit_code
    decides whether that becomes a failing exit (see blindness policy)."""
    now = now or datetime.now(timezone.utc)
    data = watch.load_watchlist(user_id, base_dir)
    symbols = data["symbols"]
    if not symbols:
        return 0, True

    delivered = 0
    attempted = failed = 0
    evaluated: dict = {}
    for symbol in sorted(symbols):
        cfg = symbols[symbol]
        crypto = tc.is_crypto(symbol)
        if not crypto and not watch.stock_market_open(now):
            continue
        attempted += 1
        try:
            readings = watch.fetch_readings(symbol, exchange=cfg.get("exchange", "binance"))
        except Exception as exc:
            failed += 1
            print(f"{symbol}: readings failed, skipped ({exc})")
            continue

        today = watch.today_marker(symbol, now)
        alerts = watch.evaluate_symbol(cfg, readings, now, today,
                                       require_bar_today=not crypto)
        all_sent = True
        for alert in alerts:
            message = watch.compose_message(symbol, alert, readings, crypto)
            if dry_run:
                print(f"DRY {message}")
            elif send_alert(user_id, message):
                print(message)
                delivered += 1
                watch.append_alert_log(user_id, {
                    "ts": now.isoformat(), "symbol": symbol,
                    "rule": alert["rule"], "side": alert["side"],
                    "text": message,
                }, base_dir)
            else:
                all_sent = False
        if all_sent:
            # Seeding and rearming move state even when nothing fired.
            evaluated[symbol] = cfg
        # else: leave this symbol out of the merge; its state on disk stays
        # pre-evaluation, so the next sweep recomputes and retries the send.

    if evaluated and not dry_run:
        def merge(data):
            # Only touch symbols this sweep evaluated. A symbol added
            # mid-sweep survives untouched; one removed mid-sweep is not
            # resurrected.
            # ponytail: a level added to a symbol WHILE that same symbol was
            # being evaluated here is overwritten (window under a second);
            # lift by merging levels by set_at if it ever bites.
            for sym, cfg in evaluated.items():
                if sym in data["symbols"]:
                    data["symbols"][sym] = cfg
        watch.mutate_watchlist(user_id, merge, base_dir)
    healthy = not (attempted and failed == attempted)
    return delivered, healthy


def _parse_ts(text) -> datetime | None:
    if not text:
        return None
    try:
        ts = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def health_path(user_id: int, base_dir: Path | None = None) -> Path:
    return watch.trading_dir(user_id, base_dir) / "sweep_health.json"


def health_exit_code(user_id: int, healthy: bool, now: datetime,
                     base_dir: Path | None = None) -> int:
    """Exit-code policy for blind sweeps (see module docstring). Exit 1 is
    what makes the scheduler ping the failure, so this is the noise gate:
    never on the first blip, then at most once per UNHEALTHY_PING_HOURS."""
    path = health_path(user_id, base_dir)
    if healthy:
        # Drop the failure chain but keep last_ping: a flapping data source
        # (fail, recover, fail) must not earn a fresh ping per flap.
        state = load_json(path, default={}) if path.exists() else {}
        if state.get("last_all_fail"):
            state.pop("last_all_fail", None)
            save_json(path, state)
        return 0
    state = load_json(path, default={})
    prev_fail = _parse_ts(state.get("last_all_fail"))
    last_ping = _parse_ts(state.get("last_ping"))
    consecutive = (prev_fail is not None and
                   (now - prev_fail) <= timedelta(minutes=UNHEALTHY_CONSECUTIVE_MINUTES))
    state["last_all_fail"] = now.isoformat()
    exit_code = 0
    if consecutive and (last_ping is None or
                        (now - last_ping) >= timedelta(hours=UNHEALTHY_PING_HOURS)):
        state["last_ping"] = now.isoformat()
        exit_code = 1
    save_json(path, state)
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Watchlist alert sweep")
    parser.add_argument("--user", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="evaluate and print, send nothing, persist nothing")
    args = parser.parse_args()
    enforce_session_user(args.user)  # MOM soft multi-user guard
    delivered, healthy = sweep(args.user, dry_run=args.dry_run)
    if delivered:
        print(f"{delivered} alert(s) delivered "
              f"at {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    if args.dry_run:  # manual runs report raw truth and persist nothing
        return 0 if healthy else 1
    return health_exit_code(args.user, healthy, datetime.now(timezone.utc))


if __name__ == "__main__":
    sys.exit(main())
