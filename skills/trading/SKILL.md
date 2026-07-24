# Trading

Market research, technical analysis, screening, and backtesting for stocks, ETFs, and crypto. Free data sources (Yahoo via yfinance, exchanges via ccxt, TradingView screener), TA-Lib indicators, backtesting.py engine, quantstats tearsheets, mplfinance charts.

**Scope (stages 1 and 3):** research, backtesting, and watchlist alerts. No order placement of any kind exists in this skill. Paper trading (Alpaca), a portfolio workbook, and a digest brief are later stages. Live trading, if ever added, requires explicit per order confirmation from the user and does not exist today.

## Dependencies

The finance stack (yfinance, ccxt, TA-Lib, backtesting, mplfinance, tradingview-screener, quantstats-lumi, alpaca-py) is declared in `deps.json` and self installs into the bot venv on first use, like the other heavy skills. Nothing extra to set up. Scripts run under the bot's own interpreter:

```bash
python skills/trading/scripts/<script>.py
```

Ad hoc analysis beyond the scripts: write a throwaway python snippet and run it the same way.

## Input gate (MANDATORY)

Backtests refuse to run without explicit capital and costs. Never assume or invent these; ask the user when missing. `--profile` names an agreed preset; explicit `--fees-bps` / `--slippage-bps` override it field by field.

| Profile | Fees | Slippage | Meaning |
|---|---|---|---|
| alpaca-us-equity | 0 bps | 5 bps | commission free US equities, slippage allowance |
| crypto-taker | 10 bps | 5 bps | typical 0.10% taker fee plus slippage |
| zero-cost | 0 | 0 | frictionless sanity check only, never a decision basis |

Slippage is folded into the per trade commission rate (backtesting.py has a single cost knob). Same discipline as the greek-engineer skill: missing input means ask, not derive.

## Commands

```bash
S=skills/trading/scripts

# Quotes and snapshots (stocks by ticker, crypto as BASE/QUOTE pairs)
python $S/market.py quote AAPL MSFT BTC/USDT
python $S/market.py snapshot NVDA
python $S/market.py history BTC/USDT --period 3mo --interval 4h --tail 15
python $S/market.py history AAPL --period 5y --csv /tmp/aapl.csv

# Indicators with plain language readout
python $S/indicators.py AAPL                                  # rsi, sma:50, sma:200, macd
python $S/indicators.py BTC/USDT --set rsi,bbands,atr,adx --period 6mo
# available: rsi[:n] sma:n ema:n macd bbands[:n] atr[:n] adx[:n] stoch obv

# Candlestick chart PNG (send with send_to_telegram.py --photo)
python $S/chart.py AAPL --period 6mo --sma 50,200
python $S/chart.py ETH/USDT --period 3mo --interval 4h

# Screener (TradingView)
python $S/screener.py list
python $S/screener.py run oversold-us-large --limit 20

# Watchlist ("look at TSLA" means: add it here for that user)
python $S/watch.py add TSLA --user <id>
python $S/watch.py add TSLA --user <id> --above 350 --below 290   # price levels
python $S/watch.py add BTC/USDT --user <id> --move-pct 3
python $S/watch.py list --user <id>
python $S/watch.py status --user <id>        # live readings + rule state
python $S/watch.py remove TSLA --user <id>
python $S/alert_sweep.py --user <id> --dry-run   # what would fire right now

# Backtests (input gate applies)
python $S/backtest.py --list-strategies
python $S/backtest.py AAPL --strategy sma-cross --capital 10000 --profile alpaca-us-equity
python $S/backtest.py BTC/USDT --strategy rsi-meanrev --params low=25,high=65 \
    --capital 5000 --profile crypto-taker --period 2y
python $S/backtest.py SPY --strategy sma-cross --params fast=50,slow=200 \
    --capital 10000 --profile alpaca-us-equity --period 10y --tearsheet
```

All data commands accept `--json` for machine readable output and `--exchange` to pick the ccxt exchange for crypto (default binance). `--user` on the watchlist scripts is scoped by `session_guard`: a bound session can only act on its own user (an admin session may act on others).

Gotchas learned live: a chart SMA overlay needs at least that many bars inside the window (sma 200 on a 6mo chart cannot draw; use `--period 2y`). Crypto backtests trade fractionally (FractionalBacktest) since one BTC can cost more than the account; stock backtests trade whole shares.

## Watchlist alerts (stage 3)

When the user says "look at X" or "watch X", run `watch.py add X --user <id>`, report the current standing from its output (plus `indicators.py` if depth is wanted), and the sweeps take it from there. The first add for a user arms a per user scheduler command job `tradewatch-<id>` that runs `alert_sweep.py` every 15 minutes, 08:00 to 23:45 local time; the job is removed automatically when that user's watchlist empties. Stocks are only evaluated during the NYSE session (weekdays 09:30 to 16:00 New York, DST aware); crypto runs whenever the sweep runs. Signals, all on daily bars: RSI(14) crossing 30 (buy side) or 70 (sell side) with 35/65 rearm hysteresis, MACD signal line crosses, SMA50/200 golden and death crosses, a day move beyond `--move-pct` (default 5%, once per direction per day), and explicit one shot `--above` / `--below` price levels. State transitions always progress but a 4 hour per rule cooldown gates delivery, so intraday chop cannot spam. New symbols seed silently on first sweep: the add conversation is where current standing gets reported, alerts only ever mean something changed.

State lives in `data/users/<id>/trading/watchlist.json` (all writes go through a file lock), fired alerts append to `alerts.jsonl`, the job logs to `sweep.log` (via the scheduler's log capture). Watchlist ceiling is 15 symbols, data-source politeness. A level that is already true is refused at add time; levels alert on crossings; re-adding an identical armed level does not stack a duplicate. A crypto add validates the ccxt exchange id up front (a typo'd id used to be accepted silently and then poisoned every sweep). Blindness policy: if every symbol in a sweep fails to produce readings, the first such sweep is a quiet blip; the second consecutive one exits 1 so the scheduler pings the failure with the sweep's output, throttled to one ping per 4 hours of sustained blindness (state in `sweep_health.json`). Alerts are delayed free data, research not advice, and never an execution quality feed.

## Symbol routing

Slash means crypto via ccxt (`BTC/USDT`, `ETH/EUR`). No slash goes to Yahoo (`AAPL`, `SPY`, `^GSPC`, `BTC-USD`). Intervals: `5m 15m 1h 4h 1d 1wk` (4h is crypto only). Periods: `5d 1mo 3mo 6mo 1y 2y 5y 10y max`. Intraday history is capped by the free source (5m/15m: 60 days, 1h: 2 years); scripts clamp and say so.

## Analyst desk protocol

When the user asks "should I look at X" or wants a view, do not answer from memory. Run the desk:

1. `market.py snapshot` + `indicators.py` for the current technical state
2. `screener.py` if the question is "what looks interesting"
3. `backtest.py` if a rule or timing idea is on the table
4. Then argue it both ways, bull case and bear case with the risk gate last, dialectic discipline, from the numbers just fetched
5. Findings are research, not advice. Say what the data shows and what it cannot show.

## Data caveats

- yfinance scrapes Yahoo unofficially: politeness matters, history is cached (~/.cache/trading-skill, 1h TTL daily, 5min intraday). Rate limit errors mean wait, not retry loops.
- TradingView screener is an unofficial API: presets can break if fields change upstream.
- Everything is delayed or best effort free data. Never present it as an execution quality feed.

## Outputs

Charts: `~/.local/share/trading-skill/charts/`. Tearsheets: `~/.local/share/trading-skill/reports/`. Both durable. Send copies to Telegram rather than moving them.

## Staging roadmap

- Stage 2: Alpaca paper trading (needs the user's paper keys in .env), portfolio workbook + trade journal via the spreadsheet stack, vectorbt parameter sweeps.
- Stage 3 (watchlist + alert sweeps, per user job `tradewatch-<id>`): shipped. Still open: a morning market brief in the daily digest.
- Live orders: separate deliberate decision after a month of paper results, per order confirmation, hard caps.

## Tests

`python -m unittest tests.test_trading_skill tests.test_trading_watch` from the repo root (pure logic, no network, runs under any interpreter). These are part of the repo's CI suite.
