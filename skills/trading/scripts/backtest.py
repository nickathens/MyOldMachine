"""Backtests over the strategy library, with honest costs and optional tearsheet.

Run under the bot venv:
  python skills/trading/scripts/backtest.py AAPL --strategy sma-cross \
      --capital 10000 --profile alpaca-us-equity

INPUT GATE: capital and costs (fees + slippage, or a named profile) are
required. A backtest never runs on assumed numbers.
"""

import argparse
import sys
from datetime import datetime

import trading_common as tc

STAT_KEYS = [
    "Return [%]", "Buy & Hold Return [%]", "Return (Ann.) [%]", "CAGR [%]",
    "Sharpe Ratio", "Sortino Ratio", "Max. Drawdown [%]", "Max. Drawdown Duration",
    "# Trades", "Win Rate [%]", "Profit Factor", "Expectancy [%]",
    "Exposure Time [%]", "Equity Final [$]", "Commissions [$]",
]


def build_strategy(name: str, params: dict):
    talib = tc.lazy_import("talib")
    backtesting = tc.lazy_import("backtesting")
    lib = tc.lazy_import("backtesting.lib")

    if name == "sma-cross":
        class SmaCross(backtesting.Strategy):
            fast = params["fast"]
            slow = params["slow"]

            def init(self):
                self.f = self.I(talib.SMA, self.data.Close, self.fast)
                self.s = self.I(talib.SMA, self.data.Close, self.slow)

            def next(self):
                if lib.crossover(self.f, self.s):
                    self.buy()
                elif lib.crossover(self.s, self.f):
                    self.position.close()
        return SmaCross

    if name == "rsi-meanrev":
        class RsiMeanRev(backtesting.Strategy):
            period = params["period"]
            low = params["low"]
            high = params["high"]

            def init(self):
                self.rsi = self.I(talib.RSI, self.data.Close, self.period)

            def next(self):
                if not self.position and self.rsi[-1] < self.low:
                    self.buy()
                elif self.position and self.rsi[-1] > self.high:
                    self.position.close()
        return RsiMeanRev

    raise ValueError(f"unknown strategy {name!r}")


def write_tearsheet(stats, df, symbol, strategy, out_path):
    import logging
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    qs = tc.lazy_import("quantstats_lumi")
    equity = stats["_equity_curve"]["Equity"]
    returns = equity.pct_change().dropna()
    benchmark = df["Close"].pct_change().dropna()
    # quantstats assumes tz-naive daily series
    if returns.index.tz is not None:
        returns.index = returns.index.tz_localize(None)
    if benchmark.index.tz is not None:
        benchmark.index = benchmark.index.tz_localize(None)
    qs.reports.html(returns, benchmark=benchmark, output=out_path,
                    title=f"{symbol} {strategy}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbol", nargs="?")
    parser.add_argument("--strategy", help="see --list-strategies")
    parser.add_argument("--params", default="", help="e.g. fast=20,slow=50")
    parser.add_argument("--capital", type=float, help="account size, required")
    parser.add_argument("--profile", help="named cost profile, see SKILL.md")
    parser.add_argument("--fees-bps", type=float, help="per trade fees in basis points")
    parser.add_argument("--slippage-bps", type=float, help="per trade slippage in basis points")
    parser.add_argument("--period", default="5y", choices=list(tc.PERIOD_DAYS))
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--tearsheet", nargs="?", const="auto",
                        help="write an HTML tearsheet (optional output path)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list-strategies", action="store_true")
    args = parser.parse_args()

    if args.list_strategies:
        for name, spec in tc.STRATEGIES.items():
            defaults = ", ".join(f"{k}={v}" for k, v in spec["params"].items())
            print(f"{name:<14} defaults: {defaults:<32} {spec['doc']}")
        return

    if not args.symbol or not args.strategy:
        raise SystemExit("error: SYMBOL and --strategy are required (or use --list-strategies)")

    try:
        capital, fees_bps, slippage_bps = tc.resolve_costs(
            args.capital, args.profile, args.fees_bps, args.slippage_bps)
    except tc.InputGateError as exc:
        print(f"INPUT GATE: {exc}")
        raise SystemExit(2)

    try:
        params = tc.validate_strategy_params(args.strategy, tc.parse_params(args.params))
        df = tc.fetch_history(args.symbol, period=args.period, interval=args.interval,
                              exchange=args.exchange)
    except Exception as exc:
        raise SystemExit(f"error: {exc}")

    symbol = tc.normalize_symbol(args.symbol)
    commission = tc.bps_to_rate(fees_bps, slippage_bps)
    backtesting = tc.lazy_import("backtesting")
    strategy_cls = build_strategy(args.strategy, params)
    if tc.is_crypto(symbol):
        # whole-unit trading cancels every order once price > capital (1 BTC
        # costs more than the account); crypto must trade fractionally
        lib = tc.lazy_import("backtesting.lib")
        bt = lib.FractionalBacktest(df, strategy_cls, cash=capital, commission=commission,
                                    exclusive_orders=True, finalize_trades=True)
    else:
        bt = backtesting.Backtest(df, strategy_cls, cash=capital, commission=commission,
                                  exclusive_orders=True, finalize_trades=True)
    stats = bt.run()

    param_text = ", ".join(f"{k}={v}" for k, v in params.items())
    header = {
        "symbol": symbol, "strategy": args.strategy, "params": params,
        "period": args.period, "interval": args.interval, "bars": len(df),
        "start": str(df.index[0].date()), "end": str(df.index[-1].date()),
        "capital": capital, "fees_bps": fees_bps, "slippage_bps": slippage_bps,
        "commission_rate": commission,
    }
    if args.json:
        payload = dict(header)
        payload["stats"] = {k: stats[k] for k in STAT_KEYS if k in stats.index}
        tc.print_json(payload)
    else:
        print(f"{symbol}  {args.strategy} ({param_text})  {args.period} {args.interval}  "
              f"{len(df)} bars  {header['start']} to {header['end']}")
        print(f"capital {capital:,.0f}  fees {fees_bps:g} bps  slippage {slippage_bps:g} bps "
              f"(combined {commission * 100:.3f}% per trade)")
        for key in STAT_KEYS:
            if key in stats.index:
                value = stats[key]
                if isinstance(value, float):
                    value = f"{value:,.2f}"
                print(f"  {key:<24} {value}")
        edge = stats["Return [%]"] - stats["Buy & Hold Return [%]"]
        print(f"  vs buy & hold            {edge:+.2f} pp")

    trades = int(stats["# Trades"])
    if trades == 0:
        # stderr so --json stdout stays parseable
        print("note: the strategy never triggered in this window, treat the stats as empty",
              file=sys.stderr)
        return

    if args.tearsheet:
        if args.interval != "1d":
            raise SystemExit("error: tearsheets need daily bars (--interval 1d)")
        if args.tearsheet == "auto":
            tc.REPORT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = str(tc.REPORT_DIR / f"{symbol.replace('/', '-')}_{args.strategy}_{stamp}.html")
        else:
            out = args.tearsheet
        print("tearsheet:", write_tearsheet(stats, df, symbol, args.strategy, out))


if __name__ == "__main__":
    main()
