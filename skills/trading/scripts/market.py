"""Quotes, snapshots and OHLCV history for stocks (yfinance) and crypto (ccxt).

Run under the bot venv (the finance stack self installs via deps.json):
  python skills/trading/scripts/market.py ...
"""

import argparse
import sys

import trading_common as tc


def cmd_quote(args):
    rows = []
    for symbol in args.symbols:
        try:
            rows.append(tc.fetch_quote(symbol, exchange=args.exchange))
        except Exception as exc:
            rows.append({"symbol": tc.normalize_symbol(symbol), "error": str(exc)})
    if args.json:
        tc.print_json(rows)
        return
    for q in rows:
        if "error" in q:
            print(f"{q['symbol']:<12} ERROR: {q['error']}")
            continue
        change = f"{q['change_pct']:+.2f}%" if q.get("change_pct") is not None else "n/a"
        price = f"{q['price']:,.2f}" if q.get("price") is not None else "n/a"
        print(f"{q['symbol']:<12} {price:>12}  {change:>8}  ({q['source']})")


def cmd_snapshot(args):
    symbol = tc.normalize_symbol(args.symbol)
    quote = tc.fetch_quote(symbol, exchange=args.exchange)
    payload = dict(quote)
    if not tc.is_crypto(symbol):
        yf = tc.lazy_import("yfinance")
        info = yf.Ticker(symbol).info or {}
        payload.update({
            "name": info.get("longName"), "sector": info.get("sector"),
            "market_cap": info.get("marketCap"), "trailing_pe": info.get("trailingPE"),
            "week52_high": info.get("fiftyTwoWeekHigh"), "week52_low": info.get("fiftyTwoWeekLow"),
            "avg_volume": info.get("averageVolume"), "dividend_yield": info.get("dividendYield"),
        })
    if args.json:
        tc.print_json(payload)
        return
    print(f"{payload.get('name') or symbol} ({symbol})")
    for key in ("price", "change_pct", "high", "low", "volume", "market_cap",
                "trailing_pe", "week52_high", "week52_low", "avg_volume",
                "dividend_yield", "sector", "currency", "source"):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, float):
            value = f"{value:,.2f}"
        elif isinstance(value, int) and abs(value) >= 10_000:
            value = f"{value:,}"
        print(f"  {key:<14} {value}")


def cmd_history(args):
    df = tc.fetch_history(args.symbol, period=args.period, interval=args.interval,
                          exchange=args.exchange)
    # with --json, stdout carries only the JSON payload
    meta_out = sys.stderr if args.json else sys.stdout
    print(f"{tc.normalize_symbol(args.symbol)}  {args.period} {args.interval}  "
          f"{len(df)} bars  {df.index[0].date()} to {df.index[-1].date()}", file=meta_out)
    if args.csv:
        df.to_csv(args.csv)
        print(f"written: {args.csv}", file=meta_out)
    if args.json:
        tail = df.tail(args.tail)
        tc.print_json([{"date": str(idx), **{c: float(row[c]) for c in df.columns}}
                       for idx, row in tail.iterrows()])
    else:
        print(df.tail(args.tail).round(4).to_string())


def main():
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--exchange", default="binance", help="ccxt exchange id for crypto pairs")
    shared.add_argument("--json", action="store_true")

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("quote", parents=[shared], help="live price for one or more symbols")
    p.add_argument("symbols", nargs="+")
    p.set_defaults(func=cmd_quote)

    p = sub.add_parser("snapshot", parents=[shared], help="fuller picture of one symbol")
    p.add_argument("symbol")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("history", parents=[shared], help="OHLCV bars")
    p.add_argument("symbol")
    p.add_argument("--period", default="1y", choices=list(tc.PERIOD_DAYS))
    p.add_argument("--interval", default="1d")
    p.add_argument("--tail", type=int, default=10, help="rows to print")
    p.add_argument("--csv", help="also write full history to this CSV path")
    p.set_defaults(func=cmd_history)

    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        raise SystemExit(f"error: {exc}")


if __name__ == "__main__":
    main()
