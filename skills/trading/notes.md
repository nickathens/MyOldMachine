# Notes: trading

Append-only learning notes from usage. Edge cases, gotchas, better approaches.

[2026-07-24] Tested-good versions of the stack: yfinance 1.5.2, ccxt 4.5.68, TA-Lib 0.7.1 (pip wheel bundles the C library, so no system `ta-lib` package is needed on the common platforms), backtesting.py 0.6.6, mplfinance 0.12.10b0, tradingview-screener 3.2.1, quantstats-lumi 1.1.5, alpaca-py 0.43.5, on pandas 3.x / numpy 2.x. deps.json is unpinned to match the other skills; if a future release breaks, pin here.

[2026-07-24] Crypto backtests must trade fractionally (backtesting.lib.FractionalBacktest), otherwise whole-unit sizing cancels every order the moment one unit costs more than the account (1 BTC > a 5000 account). Stock backtests use whole shares (backtesting.Backtest).

[2026-07-24] The screener's crypto preset uses the library's premade `tradingview_screener.crypto()` scanner; a bare `Query().set_markets('crypto')` returns zero rows in v3.

[2026-07-24] The watchlist sweep is a per user scheduler command job `tradewatch-<id>` (repeat `cron:*/15:8-23`), armed by watch.py on the first add via a direct job_meta insert and removed when the list empties. The running scheduler adopts the new row within 60s (its sync loop), no restart. On a failing sweep exit 1 the scheduler pings the job's user with the captured output, so the job is registered with the scheduler's native log capture, never `>> file` inside the command string.

[2026-07-24] Unknown ccxt exchange ids are a per-symbol ValueError, never SystemExit: one typo'd `--exchange` stored on an entry would otherwise escape the sweep's `except Exception` and crash the whole sweep every 15 minutes. watch.py also validates the exchange id at add time so the typo is refused up front.
