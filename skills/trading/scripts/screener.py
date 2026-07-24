"""Market screener backed by TradingView's screener API (tradingview-screener).

Run under the bot venv: python skills/trading/scripts/screener.py run oversold-us-large
For anything beyond the presets, write a Query inline (see SKILL.md).
"""

import argparse

import trading_common as tc


def _equity_base(query_mod, extra_where, order_field, ascending=False):
    Query, col = query_mod.Query, query_mod.col
    where = [col("is_primary") == True, col("type") == "stock"]  # noqa: E712
    where.extend(extra_where(col))
    return (Query()
            .select("name", "close", "change", "volume", "relative_volume_10d_calc",
                    "market_cap_basic", "RSI")
            .where(*where)
            .order_by(order_field, ascending=ascending))


def build_preset(name: str):
    tvs = tc.lazy_import("tradingview_screener")
    if name == "oversold-us-large":
        return _equity_base(tvs, lambda c: [c("market_cap_basic") > 2e9, c("RSI") < 30], "market_cap_basic")
    if name == "overbought-us-large":
        return _equity_base(tvs, lambda c: [c("market_cap_basic") > 2e9, c("RSI") > 70], "market_cap_basic")
    if name == "volume-spike-us":
        return _equity_base(tvs, lambda c: [c("market_cap_basic") > 5e8,
                                            c("relative_volume_10d_calc") > 3],
                            "relative_volume_10d_calc")
    if name == "gainers-us-large":
        return _equity_base(tvs, lambda c: [c("market_cap_basic") > 2e9], "change")
    if name == "losers-us-large":
        return _equity_base(tvs, lambda c: [c("market_cap_basic") > 2e9], "change", ascending=True)
    if name == "crypto-top-volume":
        # the library's premade CEX crypto scanner: bare set_markets('crypto')
        # returns nothing in v3, this is the supported route
        return tvs.crypto()
    raise ValueError(f"unknown preset {name!r}. Available: " + ", ".join(PRESETS))


PRESETS = {
    "oversold-us-large": "US stocks, market cap over 2B, RSI under 30",
    "overbought-us-large": "US stocks, market cap over 2B, RSI over 70",
    "volume-spike-us": "US stocks over 500M trading 3x their 10 day average volume",
    "gainers-us-large": "US large caps, biggest gainers today",
    "losers-us-large": "US large caps, biggest losers today",
    "crypto-top-volume": "crypto by 24h volume on TradingView's crypto screener",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list available presets")
    p = sub.add_parser("run", help="run a preset")
    p.add_argument("preset")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.command == "list":
        for name, doc in PRESETS.items():
            print(f"{name:<22} {doc}")
        return

    if args.limit < 1:
        raise SystemExit("error: --limit must be at least 1")

    try:
        query = build_preset(args.preset).limit(args.limit)
        total, df = query.get_scanner_data()
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")
    except Exception as exc:
        raise SystemExit(f"error: screener query failed ({exc}). TradingView may be "
                         "rate limiting or the field set changed; retry in a minute.")

    if args.json:
        tc.print_json({"preset": args.preset, "total_matches": total,
                       "rows": df.to_dict(orient="records")})
        return
    print(f"{args.preset}: {total} matches, showing {len(df)}")
    readable = [c for c in ("ticker", "name", "close", "change", "currency",
                            "24h_close_change|5", "24h_vol|5", "volume",
                            "relative_volume_10d_calc", "market_cap_basic", "RSI")
                if c in df.columns]
    print(df[readable].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
