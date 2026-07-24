"""Technical indicators with a plain-language readout, computed via TA-Lib.

Run under the bot venv: python skills/trading/scripts/indicators.py AAPL
"""

import argparse
import math

import trading_common as tc

DEFAULT_SET = "rsi,sma:50,sma:200,macd"


def _last(series) -> float:
    return float(series[-1])


def _ok(value: float) -> bool:
    return not math.isnan(value)


def compute(df, tokens):
    """Returns list of dicts: {indicator, param, value(s), state} from the last bar."""
    talib = tc.lazy_import("talib")
    close = df["Close"].to_numpy(dtype=float)
    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    volume = df["Volume"].to_numpy(dtype=float)
    price = float(close[-1])
    out = []
    for kind, param in tokens:
        row = {"indicator": kind, "param": param}
        try:
            if kind == "rsi":
                v = _last(talib.RSI(close, timeperiod=param))
                state = "overbought" if v >= 70 else "oversold" if v <= 30 else "neutral"
                row.update(value=round(v, 2), state=state)
            elif kind in ("sma", "ema"):
                fn = talib.SMA if kind == "sma" else talib.EMA
                v = _last(fn(close, timeperiod=param))
                if _ok(v):
                    delta = (price / v - 1) * 100
                    row.update(value=round(v, 4),
                               state=f"price {abs(delta):.1f}% {'above' if delta >= 0 else 'below'}")
                else:
                    row.update(value=None, state="insufficient bars")
            elif kind == "macd":
                macd, signal, hist = talib.MACD(close)
                m, s = _last(macd), _last(signal)
                if not (_ok(m) and _ok(s)):
                    row.update(value=None, state="insufficient bars")
                    out.append(row)
                    continue
                trend = "bullish" if m > s else "bearish"
                direction = ""
                if len(hist) >= 2 and _ok(float(hist[-2])) and _ok(float(hist[-1])):
                    direction = ", momentum " + ("rising" if hist[-1] > hist[-2] else "falling")
                row.update(value={"macd": round(m, 4), "signal": round(s, 4)},
                           state=f"{trend} (macd vs signal){direction}")
            elif kind == "bbands":
                upper, _, lower = talib.BBANDS(close, timeperiod=param)
                u, lo = _last(upper), _last(lower)
                if _ok(u) and _ok(lo) and u != lo:
                    pct_b = (price - lo) / (u - lo)
                    state = ("above upper band" if pct_b > 1 else
                             "below lower band" if pct_b < 0 else f"%B {pct_b:.2f}")
                    row.update(value={"upper": round(u, 4), "lower": round(lo, 4)}, state=state)
                else:
                    row.update(value=None, state="insufficient bars")
            elif kind == "atr":
                v = _last(talib.ATR(high, low, close, timeperiod=param))
                row.update(value=round(v, 4),
                           state=f"{v / price * 100:.2f}% of price" if _ok(v) and price else "n/a")
            elif kind == "adx":
                v = _last(talib.ADX(high, low, close, timeperiod=param))
                state = ("strong trend" if v >= 40 else "trending" if v >= 25 else "weak / ranging") \
                    if _ok(v) else "insufficient bars"
                row.update(value=round(v, 2) if _ok(v) else None, state=state)
            elif kind == "stoch":
                slowk, slowd = talib.STOCH(high, low, close)
                k, d = _last(slowk), _last(slowd)
                if not (_ok(k) and _ok(d)):
                    row.update(value=None, state="insufficient bars")
                    out.append(row)
                    continue
                zone = "overbought" if k >= 80 else "oversold" if k <= 20 else "neutral"
                row.update(value={"k": round(k, 2), "d": round(d, 2)},
                           state=f"{zone}, k {'above' if k >= d else 'below'} d")
            elif kind == "obv":
                obv = talib.OBV(close, volume)
                v = _last(obv)
                if len(obv) > 20:
                    delta = v - float(obv[-21])
                    state = "rising (accumulation)" if delta > 0 else "falling (distribution)"
                else:
                    state = "insufficient bars"
                row.update(value=round(v, 0), state=state)
            value = row.get("value")
            if isinstance(value, float) and math.isnan(value):
                row.update(value=None, state="insufficient bars")
        except Exception as exc:
            row.update(value=None, state=f"error: {exc}")
        out.append(row)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("--set", dest="indicator_set", default=DEFAULT_SET,
                        help=f"comma list, e.g. rsi,sma:50,macd,bbands (default: {DEFAULT_SET})")
    parser.add_argument("--period", default="1y", choices=list(tc.PERIOD_DAYS))
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        tokens = tc.parse_indicator_tokens(args.indicator_set)
        df = tc.fetch_history(args.symbol, period=args.period, interval=args.interval,
                              exchange=args.exchange)
    except Exception as exc:
        raise SystemExit(f"error: {exc}")

    rows = compute(df, tokens)
    symbol = tc.normalize_symbol(args.symbol)
    price = float(df["Close"].iloc[-1])
    if args.json:
        tc.print_json({"symbol": symbol, "price": price, "as_of": str(df.index[-1]),
                       "period": args.period, "interval": args.interval, "indicators": rows})
        return
    print(f"{symbol}  {price:,.2f}  ({args.interval}, {args.period}, "
          f"as of {df.index[-1].date()})")
    for row in rows:
        label = row["indicator"] + (f"({row['param']})" if row["param"] else "")
        value = row["value"]
        if isinstance(value, dict):
            value = " ".join(f"{k}={v}" for k, v in value.items())
        print(f"  {label:<12} {str(value):<28} {row['state']}")


if __name__ == "__main__":
    main()
