"""Watchlist with buy/sell situation alerts.

The user says "look at TSLA" in chat; the assistant runs `watch.py add
TSLA --user <id>`. On the first add for a user this arms a per user
scheduler sweep (alert_sweep.py, every 15 min, 08:00 to 23:45 local
time) that evaluates each watched symbol and pings Telegram when a
situation fires; the sweep is removed again when the watchlist empties.
This module holds the state file I/O, the pure signal engine, the sweep
job registration, and the management CLI. It imports no finance
libraries at module level, so the engine is unit testable anywhere.

Signal rules (all on daily bars, evaluated intraday):
  rsi_oversold    RSI(14) crosses under 30 (buy side), rearms above 35
  rsi_overbought  RSI(14) crosses over 70 (sell side), rearms under 65
  macd_cross      MACD line crosses its signal line (side change only)
  sma_cross       SMA50 vs SMA200 golden/death cross (side change only)
  day_move        abs day change reaches move_pct (default 5%), once per
                  direction per day; stocks need a bar dated today
  levels          explicit price levels (--above / --below), one shot

State transitions always progress; alert delivery is additionally gated
by a 4 hour per rule cooldown so choppy intraday flapping cannot spam.
On first sight of a symbol every rule seeds silently: the conversational
add is where the current standing is reported, the sweep only reports
changes after that.

CLI (remove/list work anywhere; add wants a live quote and status needs
the full finance stack):
  watch.py add TSLA --user <id> [--above 350] [--below 290]
                    [--move-pct 5] [--note "text"] [--exchange binance]
  watch.py remove TSLA --user <id>
  watch.py list --user <id> [--json]
  watch.py status [TSLA] --user <id> [--json]
"""

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BOT_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BOT_DIR))

from core.users import resolve_user_dir  # noqa: E402
from utils.safe_json import load_json  # noqa: E402
from utils.session_guard import enforce as enforce_session_user  # noqa: E402

import trading_common as tc  # noqa: E402
from trading_common import update_json  # noqa: E402

RSI_FIRE_LOW, RSI_REARM_LOW = 30.0, 35.0
RSI_FIRE_HIGH, RSI_REARM_HIGH = 70.0, 65.0
DEFAULT_MOVE_PCT = 5.0
COOLDOWN_HOURS = 4.0
MAX_SYMBOLS = 15  # politeness ceiling for the free data sources

ET = ZoneInfo("America/New_York")  # NYSE session gate; crypto anchors to UTC

# Per user sweep job, registered in the bot scheduler on first add.
SWEEP_REPEAT = "cron:*/15:8-23"   # every 15 min, 08:00 to 23:45 local time
SWEEP_TIMEOUT_SECONDS = 300       # well under the 15 min cadence


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

def trading_dir(user_id: int, base_dir: Path | None = None) -> Path:
    # base_dir is a test injection point; production resolves the user's
    # private dir through core.users (data/users/<id>), MOM's single source
    # of truth for per user paths.
    if base_dir:
        return Path(base_dir) / "users" / str(user_id) / "trading"
    return resolve_user_dir(user_id) / "trading"


def watchlist_path(user_id: int, base_dir: Path | None = None) -> Path:
    return trading_dir(user_id, base_dir) / "watchlist.json"


def alerts_log_path(user_id: int, base_dir: Path | None = None) -> Path:
    return trading_dir(user_id, base_dir) / "alerts.jsonl"


class WatchError(Exception):
    """User-facing refusal raised inside a mutator; aborts without writing."""


def load_watchlist(user_id: int, base_dir: Path | None = None) -> dict:
    data = load_json(watchlist_path(user_id, base_dir), default={})
    data.setdefault("symbols", {})
    return data


def mutate_watchlist(user_id: int, fn, base_dir: Path | None = None) -> dict:
    """Apply fn(data) under the file's exclusive lock and persist the result.

    The CLI and the 15 minute sweep can touch the file concurrently; every
    write goes through here so neither side can clobber the other's change.
    fn raising WatchError aborts cleanly with nothing written.
    """
    path = watchlist_path(user_id, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    def wrapper(data):
        data = data if isinstance(data, dict) else {}
        data.setdefault("symbols", {})
        fn(data)
        return data

    return update_json(path, wrapper, default={})


def new_symbol_entry(now_iso: str, exchange: str = "binance",
                     move_pct: float = DEFAULT_MOVE_PCT, note: str | None = None) -> dict:
    return {
        "added": now_iso,
        "note": note,
        "exchange": exchange,
        "move_pct": move_pct,
        "levels": [],
        "state": {
            # None means unseeded: the first evaluation records the current
            # standing without firing anything.
            "rsi_oversold": {"armed": None, "last_fired": None},
            "rsi_overbought": {"armed": None, "last_fired": None},
            "macd": {"side": None, "last_fired": None},
            "sma": {"side": None, "last_fired": None},
            "day_move": {"date": None, "directions": []},
        },
    }


def append_alert_log(user_id: int, record: dict, base_dir: Path | None = None) -> None:
    path = alerts_log_path(user_id, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Pure signal engine
# ---------------------------------------------------------------------------

def _cooldown_ok(last_fired: str | None, now: datetime) -> bool:
    if not last_fired:
        return True
    try:
        prev = datetime.fromisoformat(last_fired)
    except ValueError:
        return True
    if prev.tzinfo is None:
        prev = prev.replace(tzinfo=timezone.utc)
    return (now - prev) >= timedelta(hours=COOLDOWN_HOURS)


def _fire(alerts: list, rule_state: dict, now: datetime,
          rule: str, side: str, text: str) -> None:
    """State always progresses; the alert itself respects the cooldown."""
    if _cooldown_ok(rule_state.get("last_fired"), now):
        rule_state["last_fired"] = now.isoformat()
        alerts.append({"rule": rule, "side": side, "text": text})


def evaluate_symbol(cfg: dict, readings: dict, now: datetime,
                    today_str: str, require_bar_today: bool) -> list:
    """Evaluate one symbol's rules against fresh readings.

    Mutates cfg["state"] / cfg["levels"] in place and returns the list of
    alerts to deliver: [{rule, side, text}]. `readings` carries price,
    change_pct, rsi, macd, macd_signal, sma50, sma200, last_bar_date; any
    of them may be None and the matching rule is skipped.
    """
    alerts: list = []
    state = cfg["state"]
    price = readings.get("price")

    rsi = readings.get("rsi")
    if rsi is not None:
        over = state["rsi_oversold"]
        if over["armed"] is None:
            over["armed"] = rsi > RSI_FIRE_LOW
        elif over["armed"] and rsi <= RSI_FIRE_LOW:
            over["armed"] = False
            _fire(alerts, over, now, "rsi_oversold", "buy side",
                  f"RSI(14) crossed under {RSI_FIRE_LOW:.0f}, now {rsi:.1f}.")
        elif not over["armed"] and rsi >= RSI_REARM_LOW:
            over["armed"] = True

        under = state["rsi_overbought"]
        if under["armed"] is None:
            under["armed"] = rsi < RSI_FIRE_HIGH
        elif under["armed"] and rsi >= RSI_FIRE_HIGH:
            under["armed"] = False
            _fire(alerts, under, now, "rsi_overbought", "sell side",
                  f"RSI(14) crossed over {RSI_FIRE_HIGH:.0f}, now {rsi:.1f}.")
        elif not under["armed"] and rsi <= RSI_REARM_HIGH:
            under["armed"] = True

    macd, signal = readings.get("macd"), readings.get("macd_signal")
    if macd is not None and signal is not None:
        side = "bull" if macd > signal else "bear"
        macd_state = state["macd"]
        if macd_state["side"] is None:
            macd_state["side"] = side
        elif side != macd_state["side"]:
            macd_state["side"] = side
            if side == "bull":
                _fire(alerts, macd_state, now, "macd_cross", "buy side",
                      "MACD crossed above its signal line.")
            else:
                _fire(alerts, macd_state, now, "macd_cross", "sell side",
                      "MACD crossed under its signal line.")

    sma50, sma200 = readings.get("sma50"), readings.get("sma200")
    if sma50 is not None and sma200 is not None:
        side = "bull" if sma50 > sma200 else "bear"
        sma_state = state["sma"]
        if sma_state["side"] is None:
            sma_state["side"] = side
        elif side != sma_state["side"]:
            sma_state["side"] = side
            if side == "bull":
                _fire(alerts, sma_state, now, "sma_cross", "buy side",
                      "Golden cross, SMA50 moved above SMA200.")
            else:
                _fire(alerts, sma_state, now, "sma_cross", "sell side",
                      "Death cross, SMA50 dropped under SMA200.")

    change = readings.get("change_pct")
    if change is not None:
        bar_ok = (not require_bar_today) or readings.get("last_bar_date") == today_str
        threshold = cfg.get("move_pct", DEFAULT_MOVE_PCT)
        if bar_ok and threshold and abs(change) >= threshold:
            move = state["day_move"]
            if move.get("date") != today_str:
                move["date"] = today_str
                move["directions"] = []
            direction = "up" if change > 0 else "down"
            if direction not in move["directions"]:
                move["directions"].append(direction)
                window = "in 24h" if not require_bar_today else "today"
                alerts.append({"rule": "day_move", "side": "info",
                               "text": f"Big move, {direction} {abs(change):.1f}% {window}."})

    if price is not None:
        for level in cfg.get("levels", []):
            if level.get("triggered_at"):
                continue
            kind, target = level.get("kind"), level.get("price")
            if target is None:
                continue
            hit = price >= target if kind == "above" else price <= target
            if hit:
                level["triggered_at"] = now.isoformat()
                word = "reached" if kind == "above" else "dropped to"
                alerts.append({"rule": "level", "side": "info",
                               "text": f"Price {word} your {target:g} level, now {price:g}."})

    return alerts


def compose_message(symbol: str, alert: dict, readings: dict, is_crypto: bool) -> str:
    """One Telegram line per alert. Prose rules apply: no dashes, no arrows."""
    parts = [f"Watchlist {symbol}: {alert['side']} situation." if alert["side"] != "info"
             else f"Watchlist {symbol}:"]
    parts.append(alert["text"])
    price, change = readings.get("price"), readings.get("change_pct")
    if price is not None and alert["rule"] not in ("level", "day_move"):
        line = f"Price {price:,.2f}"
        if change is not None:
            window = "in 24h" if is_crypto else "today"
            line += f", {'up' if change >= 0 else 'down'} {abs(change):.1f}% {window}"
        parts.append(line + ".")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Market hours (stocks gate on the NYSE session, crypto never gates)
# ---------------------------------------------------------------------------

def stock_market_open(now: datetime) -> bool:
    ny = now.astimezone(ET)
    if ny.weekday() >= 5:
        return False
    minutes = ny.hour * 60 + ny.minute
    return 570 <= minutes < 960  # 09:30 to 16:00 local, regular session


def today_marker(symbol: str, now: datetime) -> str:
    """Date string for day_move dedup and the stock bar freshness guard.

    Crypto trades 24/7, so its calendar day is anchored to UTC (exchange
    neutral and deterministic on any host); stocks use the NYSE day (ET).
    """
    tz = timezone.utc if tc.is_crypto(symbol) else ET
    return now.astimezone(tz).date().isoformat()


# ---------------------------------------------------------------------------
# Live readings (finance stack, only imported when actually fetching)
# ---------------------------------------------------------------------------

def fetch_readings(symbol: str, exchange: str = "binance") -> dict:
    talib = tc.lazy_import("talib")
    df = tc.fetch_history(symbol, period="2y", interval="1d", exchange=exchange)
    close = df["Close"].to_numpy(dtype=float)

    def last_ok(series):
        import math
        v = float(series[-1])
        return None if math.isnan(v) else v

    rsi = last_ok(talib.RSI(close, timeperiod=14)) if len(close) > 14 else None
    sma50 = last_ok(talib.SMA(close, timeperiod=50)) if len(close) >= 50 else None
    sma200 = last_ok(talib.SMA(close, timeperiod=200)) if len(close) >= 200 else None
    macd_v = signal_v = None
    if len(close) >= 34:
        macd_line, signal_line, _ = talib.MACD(close)
        macd_v, signal_v = last_ok(macd_line), last_ok(signal_line)

    quote = tc.fetch_quote(symbol, exchange=exchange)
    price = quote.get("price")
    return {
        "price": float(price) if price is not None else float(close[-1]),
        "change_pct": quote.get("change_pct"),
        "rsi": rsi, "macd": macd_v, "macd_signal": signal_v,
        "sma50": sma50, "sma200": sma200,
        "last_bar_date": df.index[-1].date().isoformat(),
    }


# ---------------------------------------------------------------------------
# Sweep job registration (bot scheduler, per user, demand driven)
# ---------------------------------------------------------------------------

# Base schema mirrors core.scheduler._init_meta_db (its CREATE plus the two
# later ALTER-added columns we set). CREATE IF NOT EXISTS is a no-op once the
# running bot has made the table, and a superset the bot's own migration
# checks accept, so arming a sweep before the scheduler's first init is safe.
_JOB_META_DDL = """
CREATE TABLE IF NOT EXISTS job_meta (
    job_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    job_type TEXT NOT NULL DEFAULT 'reminder',
    name TEXT NOT NULL DEFAULT '',
    notify INTEGER NOT NULL DEFAULT 1,
    command TEXT DEFAULT NULL,
    log_file TEXT DEFAULT NULL,
    repeat TEXT DEFAULT NULL,
    weekdays TEXT DEFAULT NULL,
    channel TEXT NOT NULL DEFAULT 'telegram',
    created_at TEXT NOT NULL,
    run_at TEXT NOT NULL DEFAULT '',
    raw_at TEXT NOT NULL DEFAULT '',
    created_context TEXT NOT NULL DEFAULT '',
    recovery_attempts INTEGER NOT NULL DEFAULT 0,
    end_date TEXT DEFAULT NULL,
    timeout_seconds INTEGER DEFAULT NULL
)
"""


def scheduler_db_path(base_dir: Path | None = None) -> Path:
    """Path to the bot scheduler's SQLite DB.

    Mirrors core.scheduler.SCHEDULER_DIR / "scheduler.db" without importing
    that module (which would pull in APScheduler): the skill only inserts a
    job_meta row, which the running scheduler adopts within 60s. base_dir is
    the test injection point.
    """
    base = Path(base_dir) / "data" if base_dir else BOT_DIR / "data"
    return base / "scheduler" / "scheduler.db"


def sweep_job_name(user_id: int) -> str:
    return f"tradewatch-{user_id}"


def _bot_python() -> str:
    """The bot venv interpreter, matching bot.py's own maintenance jobs."""
    cand = BOT_DIR / ".venv" / "bin" / "python"
    return str(cand) if cand.exists() else sys.executable


def ensure_sweep_job(user_id: int, base_dir: Path | None = None) -> bool:
    """Idempotently register the per user 15 minute sweep as a command job.

    Returns True if a new row was inserted, False if one already existed or
    the scheduler DB could not be reached. Best effort by design: arming the
    sweep must never fail the add itself.
    """
    db = scheduler_db_path(base_dir)
    name = sweep_job_name(user_id)
    script = BOT_DIR / "skills" / "trading" / "scripts" / "alert_sweep.py"
    command = f"{_bot_python()} {script} --user {user_id}"
    log_file = str(trading_dir(user_id, base_dir) / "sweep.log")
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db), timeout=10)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(_JOB_META_DDL)
            existing = conn.execute(
                "SELECT job_id FROM job_meta WHERE name = ? AND user_id = ?",
                (name, user_id),
            ).fetchone()
            if existing:
                return False
            conn.execute(
                "INSERT INTO job_meta (job_id, user_id, message, job_type, name, "
                "notify, command, log_file, repeat, weekdays, channel, created_at, "
                "run_at, raw_at, created_context, recovery_attempts, end_date, "
                "timeout_seconds) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, user_id, "", "command", name, 0, command,
                 log_file, SWEEP_REPEAT, None, "telegram", now_iso, now_iso,
                 now_iso, "trading skill: watchlist sweep", 0, None,
                 SWEEP_TIMEOUT_SECONDS),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def remove_sweep_job(user_id: int, base_dir: Path | None = None) -> bool:
    """Delete the per user sweep job (called when the watchlist empties).

    Best effort; the running scheduler drops the orphaned APScheduler job on
    its next sync. Returns True if a row was deleted.
    """
    db = scheduler_db_path(base_dir)
    if not db.exists():
        return False
    try:
        conn = sqlite3.connect(str(db), timeout=10)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            cur = conn.execute(
                "DELETE FROM job_meta WHERE name = ? AND user_id = ?",
                (sweep_job_name(user_id), user_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
    except sqlite3.Error:
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def validate_level(kind: str, target: float, price: float | None) -> str | None:
    """Reject a level that is already true, it would fire on the next sweep."""
    if price is None:
        return None
    if kind == "above" and price >= target:
        return f"price {price:g} is already at or above {target:g}"
    if kind == "below" and price <= target:
        return f"price {price:g} is already at or below {target:g}"
    return None


def cmd_add(args) -> int:
    symbol = tc.normalize_symbol(args.symbol)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Slow work stays outside the file lock: quote only verifies levels and
    # decorates the confirmation, so a small pre-read race is harmless.
    known = load_watchlist(args.user, args.base_dir)["symbols"].get(symbol)
    exchange = args.exchange or (known or {}).get("exchange") or "binance"
    if tc.is_crypto(symbol):
        # A typo'd exchange id would poison every future sweep; refuse it here.
        try:
            tc.ccxt_exchange(exchange)
        except ValueError as exc:
            print(f"error: {exc}")
            return 1
    price = None
    quote_note = None
    if args.above is not None or args.below is not None or known is None:
        try:
            price = tc.fetch_quote(symbol, exchange=exchange).get("price")
        except SystemExit:
            raise
        except Exception as exc:
            quote_note = f"could not fetch a live quote to verify against ({exc})"

    for kind, target in (("above", args.above), ("below", args.below)):
        if target is None:
            continue
        problem = validate_level(kind, target, price)
        if problem:
            print(f"error: {kind} level {target:g} not set, {problem}. "
                  "A level alerts on a crossing, not on where the price already is.")
            return 1

    result = {}

    def apply(data):
        symbols = data["symbols"]
        entry = symbols.get(symbol)
        created = entry is None
        if created:
            if len(symbols) >= MAX_SYMBOLS:
                raise WatchError(
                    f"watchlist is at its ceiling of {MAX_SYMBOLS} symbols. "
                    "Remove one before adding another.")
            entry = new_symbol_entry(now_iso, exchange=exchange,
                                     move_pct=args.move_pct if args.move_pct is not None
                                     else DEFAULT_MOVE_PCT, note=args.note)
            symbols[symbol] = entry
        else:
            if args.move_pct is not None:
                entry["move_pct"] = args.move_pct
            if args.note is not None:
                entry["note"] = args.note
            if args.exchange is not None:
                entry["exchange"] = args.exchange
        for kind, target in (("above", args.above), ("below", args.below)):
            if target is None:
                continue
            if any(lv.get("kind") == kind and lv.get("price") == float(target)
                   and not lv.get("triggered_at") for lv in entry["levels"]):
                result.setdefault("dup_levels", []).append(f"{kind} {target:g}")
                continue
            entry["levels"].append({"kind": kind, "price": float(target),
                                    "set_at": now_iso, "triggered_at": None})
        result["entry"] = entry
        result["created"] = created

    try:
        mutate_watchlist(args.user, apply, args.base_dir)
    except WatchError as exc:
        print(f"error: {exc}")
        return 1

    entry, created = result["entry"], result["created"]
    ensure_sweep_job(args.user, args.base_dir)  # arm the per user sweep (idempotent)
    warnings = [quote_note] if quote_note else []
    warnings += [f"level {dup} is already armed, not duplicated"
                 for dup in result.get("dup_levels", [])]
    if args.json:
        tc.print_json({"symbol": symbol, "created": created, "price": price,
                       "entry": entry, "warnings": warnings})
        return 0
    verb = "Watching" if created else "Updated"
    print(f"{verb} {symbol}." + (f" Price now {price:,.2f}." if price else ""))
    if entry["levels"]:
        lv = ", ".join(f"{level['kind']} {level['price']:g}"
                       + (" (already hit)" if level["triggered_at"] else "")
                       for level in entry["levels"])
        print(f"  levels: {lv}")
    print(f"  signals: RSI 30/70, MACD cross, SMA 50/200 cross, "
          f"day move {entry['move_pct']:g}%")
    for warning in warnings:
        print(f"  note: {warning}")
    if created:
        print("  First sweep seeds the current standing silently; alerts fire on "
              "changes from here.")
    return 0


def cmd_remove(args) -> int:
    symbol = tc.normalize_symbol(args.symbol)

    def apply(data):
        if symbol not in data["symbols"]:
            raise WatchError(f"{symbol} is not on the watchlist.")
        del data["symbols"][symbol]

    try:
        mutate_watchlist(args.user, apply, args.base_dir)
    except WatchError as exc:
        print(f"error: {exc}")
        return 1
    if not load_watchlist(args.user, args.base_dir)["symbols"]:
        remove_sweep_job(args.user, args.base_dir)  # nothing left to watch
    print(f"Stopped watching {symbol}.")
    return 0


def cmd_list(args) -> int:
    data = load_watchlist(args.user, args.base_dir)
    symbols = data["symbols"]
    if args.json:
        tc.print_json(data)
        return 0
    if not symbols:
        print("Watchlist is empty.")
        return 0
    for symbol, entry in sorted(symbols.items()):
        bits = [f"added {entry['added'][:10]}", f"move {entry.get('move_pct', 0):g}%"]
        for level in entry.get("levels", []):
            tag = " hit" if level.get("triggered_at") else ""
            bits.append(f"{level['kind']} {level['price']:g}{tag}")
        if entry.get("note"):
            bits.append(entry["note"])
        print(f"  {symbol:<12} {', '.join(bits)}")
    print(f"Total: {len(symbols)} of {MAX_SYMBOLS}")
    return 0


def cmd_status(args) -> int:
    data = load_watchlist(args.user, args.base_dir)
    symbols = data["symbols"]
    targets = [tc.normalize_symbol(args.symbol)] if args.symbol else sorted(symbols)
    if not targets:
        print("Watchlist is empty.")
        return 0
    payload = []
    for symbol in targets:
        if symbol not in symbols:
            print(f"error: {symbol} is not on the watchlist.")
            return 1
        entry = symbols[symbol]
        try:
            readings = fetch_readings(symbol, exchange=entry.get("exchange", "binance"))
        except Exception as exc:
            print(f"  {symbol:<12} readings unavailable: {exc}")
            continue
        payload.append({"symbol": symbol, "readings": readings,
                        "state": entry["state"], "levels": entry.get("levels", [])})
        if not args.json:
            r = readings
            print(f"  {symbol:<12} price {r['price']:,.2f}"
                  + (f", {'up' if r['change_pct'] >= 0 else 'down'} "
                     f"{abs(r['change_pct']):.1f}%" if r.get("change_pct") is not None else ""))
            rsi = f"{r['rsi']:.1f}" if r.get("rsi") is not None else "n/a"
            macd_side = ("bull" if r["macd"] > r["macd_signal"] else "bear") \
                if r.get("macd") is not None and r.get("macd_signal") is not None else "n/a"
            sma_side = ("bull" if r["sma50"] > r["sma200"] else "bear") \
                if r.get("sma50") is not None and r.get("sma200") is not None else "n/a"
            print(f"{'':14} RSI {rsi}, MACD {macd_side}, SMA50/200 {sma_side}")
            for level in entry.get("levels", []):
                tag = "hit" if level.get("triggered_at") else "armed"
                print(f"{'':14} level {level['kind']} {level['price']:g} ({tag})")
    if args.json:
        tc.print_json(payload)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Trading watchlist management")
    parser.add_argument("--base-dir", default=None, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="watch a symbol")
    p_add.add_argument("symbol")
    p_add.add_argument("--user", type=int, required=True)
    p_add.add_argument("--above", type=float, default=None, help="alert when price reaches this")
    p_add.add_argument("--below", type=float, default=None, help="alert when price drops to this")
    p_add.add_argument("--move-pct", type=float, default=None,
                       help=f"day move alert threshold in percent (default {DEFAULT_MOVE_PCT:g})")
    p_add.add_argument("--note", default=None)
    p_add.add_argument("--exchange", default=None,
                       help="ccxt exchange id for crypto (default binance; an "
                            "existing entry keeps its exchange unless this is passed)")
    p_add.add_argument("--json", action="store_true")

    p_rm = sub.add_parser("remove", help="stop watching a symbol")
    p_rm.add_argument("symbol")
    p_rm.add_argument("--user", type=int, required=True)

    p_ls = sub.add_parser("list", help="show the watchlist")
    p_ls.add_argument("--user", type=int, required=True)
    p_ls.add_argument("--json", action="store_true")

    p_st = sub.add_parser("status", help="live readings and rule state")
    p_st.add_argument("symbol", nargs="?", default=None)
    p_st.add_argument("--user", type=int, required=True)
    p_st.add_argument("--json", action="store_true")

    args = parser.parse_args()
    # MOM soft multi-user: refuse a --user that disagrees with the bound session.
    enforce_session_user(args.user)
    if getattr(args, "move_pct", None) is not None and args.move_pct <= 0:
        print("error: --move-pct must be positive")
        return 1
    handlers = {"add": cmd_add, "remove": cmd_remove, "list": cmd_list, "status": cmd_status}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
