"""Candlestick chart PNG via mplfinance, ready to send to Telegram.

Run under the bot venv: python skills/trading/scripts/chart.py AAPL --sma 50,200
"""

import argparse
from datetime import datetime

import trading_common as tc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("--period", default="6mo", choices=list(tc.PERIOD_DAYS))
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--sma", help="comma list of SMA overlays, e.g. 50,200")
    parser.add_argument("--no-volume", action="store_true")
    parser.add_argument("--out", help="output PNG path (default: skill chart dir)")
    parser.add_argument("--exchange", default="binance")
    args = parser.parse_args()

    mav = None
    if args.sma:
        try:
            mav = tuple(int(x) for x in args.sma.split(",") if x.strip())
        except ValueError:
            raise SystemExit(f"error: --sma expects integers, got {args.sma!r}")
        if not mav or any(x <= 0 for x in mav):
            raise SystemExit(f"error: --sma expects positive integers, got {args.sma!r}")

    try:
        df = tc.fetch_history(args.symbol, period=args.period, interval=args.interval,
                              exchange=args.exchange)
    except Exception as exc:
        raise SystemExit(f"error: {exc}")

    symbol = tc.normalize_symbol(args.symbol)
    if args.out:
        out = args.out
    else:
        tc.CHART_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = str(tc.CHART_DIR / f"{symbol.replace('/', '-')}_{args.period}_{stamp}.png")

    mpf = tc.lazy_import("mplfinance")
    kwargs = {
        "type": "candle",
        "style": "nightclouds",
        "volume": not args.no_volume,
        "title": f"{symbol}  {args.period} {args.interval}",
        "figsize": (12, 7),
        "savefig": {"fname": out, "dpi": 150, "bbox_inches": "tight"},
    }
    if mav:
        kwargs["mav"] = mav
    mpf.plot(df, **kwargs)
    print(out)


if __name__ == "__main__":
    main()
