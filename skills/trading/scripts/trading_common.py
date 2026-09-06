"""Shared core for the trading skill.

The finance stack (yfinance, ccxt, TA-Lib, backtesting, mplfinance,
tradingview-screener, quantstats-lumi, alpaca-py) is declared in
deps.json and self installed into the bot venv on first use, the same
way the other heavy skills work. Scripts run under the bot's own
interpreter, no dedicated venv.

Heavy libraries are imported lazily inside functions so this module
imports clean even before the stack is installed, which lets the pure
logic be unit tested without network or finance dependencies.
"""

import importlib
import json
import sys
import time
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "trading-skill"
DATA_DIR = Path.home() / ".local" / "share" / "trading-skill"
CHART_DIR = DATA_DIR / "charts"
REPORT_DIR = DATA_DIR / "reports"

# User-facing tokens follow yfinance spelling; ccxt gets its own map.
PERIOD_DAYS = {
    "5d": 5, "1mo": 30, "3mo": 91, "6mo": 182, "1y": 365,
    "2y": 730, "5y": 1825, "10y": 3650, "max": None,
}
CCXT_TIMEFRAMES = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d", "1wk": "1w"}
STOCK_INTERVALS = {"5m", "15m", "1h", "1d", "1wk"}  # yfinance has no 4h
# Free-data history ceilings per interval (yfinance hard limits; applied to
# crypto too so a 5m/5y request cannot paginate for minutes).
INTERVAL_MAX_DAYS = {"5m": 59, "15m": 59, "1h": 729, "4h": 1500, "1d": None, "1wk": None}

CRYPTO_MAX_EPOCH_MS = 1420070400000  # 2015-01-01, floor for period="max"

COST_PROFILES = {
    "alpaca-us-equity": {"fees_bps": 0.0, "slippage_bps": 5.0,
                         "note": "Alpaca commission-free US equities, 5 bps slippage allowance"},
    "crypto-taker": {"fees_bps": 10.0, "slippage_bps": 5.0,
                     "note": "typical 0.10% taker fee plus 5 bps slippage"},
    "zero-cost": {"fees_bps": 0.0, "slippage_bps": 0.0,
                  "note": "frictionless sanity check only, never a trading decision"},
}

STRATEGIES = {
    "sma-cross": {
        "params": {"fast": 20, "slow": 50},
        "doc": "Long when fast SMA crosses above slow SMA, flat on cross back down.",
    },
    "rsi-meanrev": {
        "params": {"period": 14, "low": 30, "high": 70},
        "doc": "Long when RSI drops below `low`, exit when RSI rises above `high`.",
    },
}

INDICATORS = {
    # name: (param required?, default param, takes param at all?)
    "rsi": (False, 14, True),
    "sma": (True, None, True),
    "ema": (True, None, True),
    "macd": (False, None, False),
    "bbands": (False, 20, True),
    "atr": (False, 14, True),
    "adx": (False, 14, True),
    "stoch": (False, None, False),
    "obv": (False, None, False),
}


class InputGateError(Exception):
    """Raised when a computation would have to assume a number the user never gave."""


def lazy_import(module: str):
    try:
        return importlib.import_module(module)
    except ImportError:
        raise SystemExit(
            f"{module} is not available yet. The trading skill's finance stack "
            "installs on first use (deps.json); if this persists, install the "
            "skill dependencies and retry."
        )


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def is_crypto(symbol: str) -> bool:
    """CCXT pairs carry a slash (BTC/USDT). Everything else routes to yfinance,
    including Yahoo-style crypto tickers like BTC-USD."""
    return "/" in symbol


def ccxt_exchange(name: str):
    """Instantiate a ccxt exchange by id. Raises ValueError (not SystemExit)
    on an unknown id so a typo'd exchange stays a per-symbol error in the
    alert sweep instead of killing the whole run."""
    ccxt = lazy_import("ccxt")
    try:
        return getattr(ccxt, name)()
    except AttributeError:
        raise ValueError(f"unknown exchange {name!r} (ccxt id expected, e.g. binance, kraken)")


def validate_interval(symbol: str, interval: str) -> None:
    if is_crypto(symbol):
        if interval not in CCXT_TIMEFRAMES:
            raise ValueError(f"interval {interval!r} not supported for crypto. "
                             f"Use one of: {', '.join(sorted(CCXT_TIMEFRAMES))}")
    elif interval not in STOCK_INTERVALS:
        raise ValueError(f"interval {interval!r} not supported for stocks. "
                         f"Use one of: {', '.join(sorted(STOCK_INTERVALS))}")


def clamp_period_days(period: str, interval: str):
    """Return (days_or_None, clamp_note_or_None) honoring free-data ceilings."""
    if period not in PERIOD_DAYS:
        raise ValueError(f"period {period!r} unknown. Use one of: {', '.join(PERIOD_DAYS)}")
    days = PERIOD_DAYS[period]
    ceiling = INTERVAL_MAX_DAYS.get(interval)
    if ceiling is not None and (days is None or days > ceiling):
        return ceiling, (f"note: {interval} data is capped at {ceiling} days by the free "
                         f"data source, period {period} was clamped")
    return days, None


def ttl_for(interval: str) -> int:
    return 3600 if interval in ("1d", "1wk") else 300


def cache_path(kind: str, symbol: str, period: str, interval: str,
               exchange: str | None = None) -> Path:
    safe = symbol.replace("/", "-")
    # The exchange is part of the identity of crypto candles: binance and
    # kraken quote different books, and one key served both (audit F32).
    tag = f"_{exchange}" if exchange else ""
    return CACHE_DIR / f"{kind}_{safe}{tag}_{period}_{interval}.pkl"


def cache_fresh(path: Path, ttl_seconds: int, now: float | None = None) -> bool:
    if not path.exists():
        return False
    now = time.time() if now is None else now
    return (now - path.stat().st_mtime) < ttl_seconds


def fetch_history(symbol: str, period: str = "1y", interval: str = "1d",
                  exchange: str = "binance", use_cache: bool = True):
    """OHLCV DataFrame (Open, High, Low, Close, Volume) for stock or crypto."""
    symbol = normalize_symbol(symbol)
    validate_interval(symbol, interval)
    days, clamp_note = clamp_period_days(period, interval)
    if clamp_note:
        print(clamp_note, file=sys.stderr)  # keep --json stdout parseable

    pd = lazy_import("pandas")
    path = cache_path("hist", symbol, period, interval,
                      exchange if is_crypto(symbol) else None)
    if use_cache and cache_fresh(path, ttl_for(interval)):
        try:
            return pd.read_pickle(path)
        except Exception:
            pass  # corrupt cache entry: fall through and refetch

    if is_crypto(symbol):
        df = _fetch_crypto(symbol, days, interval, exchange)
    else:
        df = _fetch_stock(symbol, period, days, interval)

    if df is None or df.empty:
        raise RuntimeError(
            f"No data returned for {symbol} ({period}, {interval}). "
            "Check the symbol spelling; if it is correct, the free data source "
            "may be rate limiting, retry in a minute."
        )
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        df.to_pickle(path)
    except Exception:
        pass  # cache is best effort, never fail the fetch over it
    return df


def _fetch_stock(symbol: str, period: str, days, interval: str):
    yf = lazy_import("yfinance")
    if days is not None and days != PERIOD_DAYS.get(period):
        period = f"{days}d"  # clamped intraday request
    return yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)


def _fetch_crypto(symbol: str, days, interval: str, exchange: str):
    pd = lazy_import("pandas")
    ex = ccxt_exchange(exchange)
    timeframe = CCXT_TIMEFRAMES[interval]
    now_ms = int(time.time() * 1000)
    since = CRYPTO_MAX_EPOCH_MS if days is None else now_ms - days * 86_400_000
    rows = []
    while True:
        batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        since = batch[-1][0] + 1
        if since >= now_ms or len(rows) > 60_000:
            break
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
    df.index.name = "Date"
    return df


def _fast_info_get(fi, *names):
    for name in names:
        try:
            value = getattr(fi, name)
        except (AttributeError, KeyError):
            try:
                value = fi[name]
            except Exception:
                continue
        if value is not None:
            return value
    return None


def fetch_quote(symbol: str, exchange: str = "binance") -> dict:
    symbol = normalize_symbol(symbol)
    if is_crypto(symbol):
        t = ccxt_exchange(exchange).fetch_ticker(symbol)
        return {"symbol": symbol, "price": t.get("last"), "change_pct": t.get("percentage"),
                "high": t.get("high"), "low": t.get("low"), "volume": t.get("baseVolume"),
                "source": exchange}
    yf = lazy_import("yfinance")
    fi = yf.Ticker(symbol).fast_info
    price = _fast_info_get(fi, "last_price", "lastPrice")
    prev = _fast_info_get(fi, "previous_close", "previousClose")
    change = (price / prev - 1) * 100 if price and prev else None
    return {"symbol": symbol, "price": price, "change_pct": change,
            "high": _fast_info_get(fi, "day_high", "dayHigh"),
            "low": _fast_info_get(fi, "day_low", "dayLow"),
            "volume": _fast_info_get(fi, "last_volume", "lastVolume"),
            "currency": _fast_info_get(fi, "currency"), "source": "yahoo"}


def resolve_costs(capital, profile, fees_bps, slippage_bps):
    """Input gate: capital and trading costs must be explicit, never assumed.
    Explicit bps flags override the matching profile field."""
    missing = []
    if capital is None or capital <= 0:
        missing.append("--capital (account size in the quote currency, must be > 0)")
    prof_fees = prof_slip = None
    if profile is not None:
        if profile not in COST_PROFILES:
            raise InputGateError(
                f"unknown cost profile {profile!r}. Available: "
                + ", ".join(f"{k} ({v['note']})" for k, v in COST_PROFILES.items()))
        prof_fees = COST_PROFILES[profile]["fees_bps"]
        prof_slip = COST_PROFILES[profile]["slippage_bps"]
    fees = fees_bps if fees_bps is not None else prof_fees
    slip = slippage_bps if slippage_bps is not None else prof_slip
    if fees is None:
        missing.append("--fees-bps (or a --profile)")
    if slip is None:
        missing.append("--slippage-bps (or a --profile)")
    if missing:
        raise InputGateError(
            "a backtest never runs on assumed numbers. Missing: "
            + "; ".join(missing)
            + ". Profiles available: " + ", ".join(COST_PROFILES) + ".")
    if fees < 0 or slip < 0:
        raise InputGateError("fees and slippage must be zero or positive")
    return float(capital), float(fees), float(slip)


def bps_to_rate(fees_bps: float, slippage_bps: float) -> float:
    """backtesting.py takes one per-trade commission rate; slippage is folded in."""
    return (fees_bps + slippage_bps) / 10_000.0


def parse_params(text: str) -> dict:
    """'fast=20,slow=50' to {'fast': 20, 'slow': 50}; ints when int-like."""
    if not text:
        return {}
    out = {}
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if "=" not in token:
            raise ValueError(f"bad parameter {token!r}, expected name=value")
        key, _, raw = token.partition("=")
        key = key.strip()
        raw = raw.strip()
        try:
            out[key] = int(raw)
        except ValueError:
            try:
                out[key] = float(raw)
            except ValueError:
                raise ValueError(f"parameter {key!r} has non-numeric value {raw!r}")
    return out


def validate_strategy_params(name: str, params: dict) -> dict:
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy {name!r}. Available: " + ", ".join(STRATEGIES))
    merged = dict(STRATEGIES[name]["params"])
    for key, value in params.items():
        if key not in merged:
            raise ValueError(f"strategy {name!r} has no parameter {key!r} "
                             f"(it takes: {', '.join(merged)})")
        merged[key] = value
    for key, value in merged.items():
        if not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"parameter {key!r} must be a positive number, got {value!r}")
        if key in ("fast", "slow", "period") and not isinstance(value, int):
            raise ValueError(f"parameter {key!r} is a bar count and must be an integer, "
                             f"got {value!r}")
    if name == "sma-cross" and merged["fast"] >= merged["slow"]:
        raise ValueError("sma-cross needs fast < slow "
                         f"(got fast={merged['fast']}, slow={merged['slow']})")
    if name == "rsi-meanrev":
        if not (0 < merged["low"] < merged["high"] < 100):
            raise ValueError("rsi-meanrev needs 0 < low < high < 100 "
                             f"(got low={merged['low']}, high={merged['high']})")
    return merged


def parse_indicator_tokens(text: str) -> list:
    """'rsi,sma:50,macd' to [('rsi', 14), ('sma', 50), ('macd', None)]."""
    out = []
    for token in text.split(","):
        token = token.strip().lower()
        if not token:
            continue
        kind, _, raw = token.partition(":")
        if kind not in INDICATORS:
            raise ValueError(f"unknown indicator {kind!r}. Available: " + ", ".join(INDICATORS))
        required, default, takes_param = INDICATORS[kind]
        if raw:
            if not takes_param:
                raise ValueError(f"indicator {kind!r} takes no parameter (got {token!r})")
            try:
                param = int(raw)
            except ValueError:
                raise ValueError(f"indicator parameter must be an integer (got {token!r})")
            if param <= 0:
                raise ValueError(f"indicator parameter must be positive (got {token!r})")
        elif required:
            raise ValueError(f"indicator {kind!r} needs a period, e.g. {kind}:50")
        else:
            param = default
        out.append((kind, param))
    if not out:
        raise ValueError("no indicators requested")
    return out


def print_json(payload) -> None:
    print(json.dumps(payload, indent=2, default=str))


def update_json(filepath, update_fn, default=None):
    """Atomically update a JSON file under an exclusive lock.

    The watchlist is written by both the CLI (watch.py add/remove) and the
    15 minute sweep (alert_sweep.py); this serializes them so neither side
    clobbers the other's change. MOM's utils.safe_json ships load_json and
    save_json but no locked read modify write, so the skill carries its own.
    A `.lock` sidecar is the mutex; the actual read and atomic write reuse
    utils.safe_json. POSIX only (fcntl); MOM's hosts are Linux and macOS.

    utils.safe_json is imported lazily so this module still imports cleanly
    under a plain interpreter with no BOT_DIR on sys.path (the pure logic
    tests); watch.py and alert_sweep.py insert BOT_DIR before calling this.
    """
    import fcntl

    from utils.safe_json import load_json, save_json

    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    lock_path = filepath.with_suffix(filepath.suffix + ".lock")
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            data = load_json(filepath, default)
            updated = update_fn(data)
            save_json(filepath, updated)
            return updated
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
