"""Watchlist and alert sweep tests (pure logic, no network, main venv).

watch.py and alert_sweep.py import finance libraries lazily, so the signal
engine, state machine, market hours gate, CLI mutators, and sweep
orchestration are all testable here with stubbed readings.
"""

import argparse
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "trading" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
# The video-watch skill ships skills/watch/scripts/watch.py under the same bare
# module name "watch". Under `unittest discover` whichever test imports "watch"
# first wins sys.modules, so a foreign copy could silently shadow the trading
# modules here (and alert_sweep's own internal `import watch`). Evict any cached
# same-named module not loaded from the trading scripts dir before importing;
# this mirrors runtime, where the script's own dir is sys.path[0].
for _name in ("watch", "alert_sweep", "market", "trading_common"):
    _mod = sys.modules.get(_name)
    if _mod is not None and not str(getattr(_mod, "__file__", "")).startswith(str(_SCRIPTS)):
        del sys.modules[_name]

import alert_sweep  # noqa: E402
import watch  # noqa: E402

UTC = timezone.utc
# Wednesday 2026-07-22 14:00 UTC = 10:00 New York (NYSE open).
OPEN_NOW = datetime(2026, 7, 22, 14, 0, tzinfo=UTC)
# Saturday.
SATURDAY = datetime(2026, 7, 25, 14, 0, tzinfo=UTC)


def readings(**kw):
    base = {"price": 100.0, "change_pct": 1.0, "rsi": 50.0,
            "macd": 1.0, "macd_signal": 0.5, "sma50": 105.0, "sma200": 95.0,
            "last_bar_date": "2026-07-22"}
    base.update(kw)
    return base


def entry(**kw):
    e = watch.new_symbol_entry("2026-07-20T00:00:00+00:00")
    e.update(kw)
    return e


def add_args(symbol, tmp, **kw):
    # exchange=None mirrors the CLI default (argparse default is None)
    defaults = dict(symbol=symbol, user=1, above=None, below=None, move_pct=None,
                    note=None, exchange=None, json=False, base_dir=tmp)
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class TestWatchlistCrud(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name
        patcher = mock.patch.object(watch.tc, "fetch_quote",
                                    return_value={"price": 100.0})
        self.quote = patcher.start()
        self.addCleanup(patcher.stop)

    def test_add_creates_entry_with_defaults(self):
        rc = watch.cmd_add(add_args("tsla", self.base))
        self.assertEqual(rc, 0)
        data = watch.load_watchlist(1, self.base)
        self.assertIn("TSLA", data["symbols"])
        self.assertEqual(data["symbols"]["TSLA"]["move_pct"], watch.DEFAULT_MOVE_PCT)
        self.assertIsNone(data["symbols"]["TSLA"]["state"]["rsi_oversold"]["armed"])

    def test_add_again_updates_not_duplicates(self):
        watch.cmd_add(add_args("TSLA", self.base))
        rc = watch.cmd_add(add_args("TSLA", self.base, move_pct=3.0, note="earnings"))
        self.assertEqual(rc, 0)
        data = watch.load_watchlist(1, self.base)
        self.assertEqual(len(data["symbols"]), 1)
        self.assertEqual(data["symbols"]["TSLA"]["move_pct"], 3.0)
        self.assertEqual(data["symbols"]["TSLA"]["note"], "earnings")

    def test_ceiling_enforced(self):
        def apply(data):
            for i in range(watch.MAX_SYMBOLS):
                data["symbols"][f"S{i}"] = entry()
        watch.mutate_watchlist(1, apply, self.base)
        rc = watch.cmd_add(add_args("ONEMORE", self.base))
        self.assertEqual(rc, 1)
        self.assertNotIn("ONEMORE", watch.load_watchlist(1, self.base)["symbols"])

    def test_remove(self):
        watch.cmd_add(add_args("TSLA", self.base))
        rc = watch.cmd_remove(argparse.Namespace(symbol="TSLA", user=1, base_dir=self.base))
        self.assertEqual(rc, 0)
        self.assertEqual(watch.load_watchlist(1, self.base)["symbols"], {})

    def test_remove_missing_errors(self):
        rc = watch.cmd_remove(argparse.Namespace(symbol="NOPE", user=1, base_dir=self.base))
        self.assertEqual(rc, 1)

    def test_level_already_true_rejected_and_nothing_written(self):
        rc = watch.cmd_add(add_args("TSLA", self.base, above=90.0))
        self.assertEqual(rc, 1)
        self.assertEqual(watch.load_watchlist(1, self.base)["symbols"], {})

    def test_level_below_already_true_rejected(self):
        rc = watch.cmd_add(add_args("TSLA", self.base, below=110.0))
        self.assertEqual(rc, 1)

    def test_valid_levels_stored_armed(self):
        rc = watch.cmd_add(add_args("TSLA", self.base, above=120.0, below=80.0))
        self.assertEqual(rc, 0)
        levels = watch.load_watchlist(1, self.base)["symbols"]["TSLA"]["levels"]
        self.assertEqual([(lv["kind"], lv["price"]) for lv in levels],
                         [("above", 120.0), ("below", 80.0)])
        self.assertTrue(all(lv["triggered_at"] is None for lv in levels))

    def test_quote_failure_still_adds_symbol(self):
        self.quote.side_effect = RuntimeError("network down")
        rc = watch.cmd_add(add_args("TSLA", self.base))
        self.assertEqual(rc, 0)
        self.assertIn("TSLA", watch.load_watchlist(1, self.base)["symbols"])

    def test_quote_failure_level_stored_unverified(self):
        self.quote.side_effect = RuntimeError("network down")
        rc = watch.cmd_add(add_args("TSLA", self.base, above=120.0))
        self.assertEqual(rc, 0)
        levels = watch.load_watchlist(1, self.base)["symbols"]["TSLA"]["levels"]
        self.assertEqual(len(levels), 1)

    def test_quote_failure_note_surfaced_even_without_levels(self):
        self.quote.side_effect = RuntimeError("network down")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = watch.cmd_add(add_args("TSLA", self.base))
        self.assertEqual(rc, 0)
        self.assertIn("could not fetch", buf.getvalue())

    def test_duplicate_armed_level_not_stacked(self):
        watch.cmd_add(add_args("TSLA", self.base, above=120.0))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = watch.cmd_add(add_args("TSLA", self.base, above=120.0))
        self.assertEqual(rc, 0)
        levels = watch.load_watchlist(1, self.base)["symbols"]["TSLA"]["levels"]
        self.assertEqual(len(levels), 1)
        self.assertIn("already armed", buf.getvalue())

    def test_same_price_level_can_rearm_after_trigger(self):
        watch.cmd_add(add_args("TSLA", self.base, above=120.0))

        def consume(data):
            data["symbols"]["TSLA"]["levels"][0]["triggered_at"] = "2026-07-22T00:00:00+00:00"
        watch.mutate_watchlist(1, consume, self.base)
        watch.cmd_add(add_args("TSLA", self.base, above=120.0))
        levels = watch.load_watchlist(1, self.base)["symbols"]["TSLA"]["levels"]
        self.assertEqual(len(levels), 2)
        self.assertIsNone(levels[1]["triggered_at"])

    def test_unknown_crypto_exchange_refused_and_nothing_written(self):
        with mock.patch.object(watch.tc, "ccxt_exchange",
                               side_effect=ValueError("unknown exchange 'krakenn'")):
            rc = watch.cmd_add(add_args("BTC/USDT", self.base, exchange="krakenn"))
        self.assertEqual(rc, 1)
        self.assertEqual(watch.load_watchlist(1, self.base)["symbols"], {})

    def test_exchange_updates_including_back_to_binance(self):
        with mock.patch.object(watch.tc, "ccxt_exchange", return_value=object()):
            watch.cmd_add(add_args("BTC/USDT", self.base, exchange="kraken"))
            data = watch.load_watchlist(1, self.base)
            self.assertEqual(data["symbols"]["BTC/USDT"]["exchange"], "kraken")
            watch.cmd_add(add_args("BTC/USDT", self.base))  # no flag: kept
            data = watch.load_watchlist(1, self.base)
            self.assertEqual(data["symbols"]["BTC/USDT"]["exchange"], "kraken")
            watch.cmd_add(add_args("BTC/USDT", self.base, exchange="binance"))
            data = watch.load_watchlist(1, self.base)
            self.assertEqual(data["symbols"]["BTC/USDT"]["exchange"], "binance")


class TestSignalEngine(unittest.TestCase):
    def evaluate(self, cfg, r, now=OPEN_NOW, today="2026-07-22", bar_guard=True):
        return watch.evaluate_symbol(cfg, r, now, today, require_bar_today=bar_guard)

    def test_first_evaluation_seeds_silently(self):
        cfg = entry()
        alerts = self.evaluate(cfg, readings(rsi=25.0, change_pct=0.5))
        self.assertEqual(alerts, [])
        self.assertFalse(cfg["state"]["rsi_oversold"]["armed"])
        self.assertTrue(cfg["state"]["rsi_overbought"]["armed"])
        self.assertEqual(cfg["state"]["macd"]["side"], "bull")
        self.assertEqual(cfg["state"]["sma"]["side"], "bull")

    def test_rsi_oversold_fires_on_cross(self):
        cfg = entry()
        self.evaluate(cfg, readings(rsi=45.0))
        alerts = self.evaluate(cfg, readings(rsi=29.0))
        self.assertEqual([a["rule"] for a in alerts], ["rsi_oversold"])
        self.assertEqual(alerts[0]["side"], "buy side")
        self.assertFalse(cfg["state"]["rsi_oversold"]["armed"])

    def test_rsi_no_refire_while_still_low(self):
        cfg = entry()
        self.evaluate(cfg, readings(rsi=45.0))
        self.evaluate(cfg, readings(rsi=29.0))
        alerts = self.evaluate(cfg, readings(rsi=27.0))
        self.assertEqual(alerts, [])

    def test_rsi_rearms_and_refires_after_cooldown(self):
        cfg = entry()
        self.evaluate(cfg, readings(rsi=45.0))
        self.evaluate(cfg, readings(rsi=29.0))
        self.evaluate(cfg, readings(rsi=36.0))  # rearm, no alert
        self.assertTrue(cfg["state"]["rsi_oversold"]["armed"])
        later = OPEN_NOW + timedelta(hours=5)
        alerts = watch.evaluate_symbol(cfg, readings(rsi=28.0), later,
                                       "2026-07-22", require_bar_today=True)
        self.assertEqual([a["rule"] for a in alerts], ["rsi_oversold"])

    def test_rsi_midband_does_not_rearm(self):
        cfg = entry()
        self.evaluate(cfg, readings(rsi=45.0))
        self.evaluate(cfg, readings(rsi=29.0))
        self.evaluate(cfg, readings(rsi=33.0))  # under the 35 rearm line
        self.assertFalse(cfg["state"]["rsi_oversold"]["armed"])

    def test_cooldown_suppresses_alert_but_state_progresses(self):
        cfg = entry()
        self.evaluate(cfg, readings(rsi=45.0))
        self.evaluate(cfg, readings(rsi=29.0))       # fires, cooldown starts
        self.evaluate(cfg, readings(rsi=36.0))       # rearm
        soon = OPEN_NOW + timedelta(hours=1)
        alerts = watch.evaluate_symbol(cfg, readings(rsi=28.0), soon,
                                       "2026-07-22", require_bar_today=True)
        self.assertEqual(alerts, [])                 # suppressed by cooldown
        self.assertFalse(cfg["state"]["rsi_oversold"]["armed"])  # still disarmed

    def test_rsi_overbought_fires_sell_side(self):
        cfg = entry()
        self.evaluate(cfg, readings(rsi=50.0))
        alerts = self.evaluate(cfg, readings(rsi=71.0))
        self.assertEqual([(a["rule"], a["side"]) for a in alerts],
                         [("rsi_overbought", "sell side")])

    def test_macd_flip_fires_and_cooldown_flip_back_is_silent(self):
        cfg = entry()
        self.evaluate(cfg, readings(macd=1.0, macd_signal=0.5))   # seed bull
        alerts = self.evaluate(cfg, readings(macd=0.4, macd_signal=0.5))
        self.assertEqual([(a["rule"], a["side"]) for a in alerts],
                         [("macd_cross", "sell side")])
        soon = OPEN_NOW + timedelta(minutes=30)
        alerts = watch.evaluate_symbol(cfg, readings(macd=0.6, macd_signal=0.5),
                                       soon, "2026-07-22", require_bar_today=True)
        self.assertEqual(alerts, [])                  # chop swallowed
        self.assertEqual(cfg["state"]["macd"]["side"], "bull")  # state truthful

    def test_sma_death_cross_fires(self):
        cfg = entry()
        self.evaluate(cfg, readings(sma50=105.0, sma200=95.0))
        alerts = self.evaluate(cfg, readings(sma50=94.0, sma200=95.0))
        self.assertEqual([(a["rule"], a["side"]) for a in alerts],
                         [("sma_cross", "sell side")])

    def test_sma_missing_skips_without_state_change(self):
        cfg = entry()
        self.evaluate(cfg, readings())
        before = dict(cfg["state"]["sma"])
        alerts = self.evaluate(cfg, readings(sma200=None))
        self.assertEqual(alerts, [])
        self.assertEqual(cfg["state"]["sma"], before)

    def test_day_move_once_per_direction_per_day(self):
        cfg = entry()
        alerts = self.evaluate(cfg, readings(change_pct=6.0))
        self.assertEqual([a["rule"] for a in alerts], ["day_move"])
        self.assertEqual(self.evaluate(cfg, readings(change_pct=7.0)), [])
        alerts = self.evaluate(cfg, readings(change_pct=-6.0))
        self.assertEqual([a["rule"] for a in alerts], ["day_move"])
        alerts = watch.evaluate_symbol(cfg, readings(change_pct=6.0),
                                       OPEN_NOW + timedelta(days=1),
                                       "2026-07-23", require_bar_today=False)
        self.assertEqual([a["rule"] for a in alerts], ["day_move"])

    def test_day_move_respects_custom_threshold(self):
        cfg = entry(move_pct=3.0)
        alerts = self.evaluate(cfg, readings(change_pct=3.5))
        self.assertEqual([a["rule"] for a in alerts], ["day_move"])
        cfg2 = entry(move_pct=3.0)
        self.assertEqual(self.evaluate(cfg2, readings(change_pct=2.5)), [])

    def test_day_move_stock_needs_bar_dated_today(self):
        cfg = entry()
        alerts = self.evaluate(cfg, readings(change_pct=6.0,
                                             last_bar_date="2026-07-18"))
        self.assertEqual(alerts, [])

    def test_day_move_crypto_ignores_bar_date(self):
        cfg = entry()
        alerts = self.evaluate(cfg, readings(change_pct=6.0, last_bar_date=None),
                               bar_guard=False)
        self.assertEqual([a["rule"] for a in alerts], ["day_move"])

    def test_level_above_fires_once(self):
        cfg = entry(levels=[{"kind": "above", "price": 100.0,
                             "set_at": "x", "triggered_at": None}])
        alerts = self.evaluate(cfg, readings(price=101.0))
        self.assertEqual([a["rule"] for a in alerts], ["level"])
        self.assertIsNotNone(cfg["levels"][0]["triggered_at"])
        self.assertEqual(self.evaluate(cfg, readings(price=150.0)), [])

    def test_level_below_fires(self):
        cfg = entry(levels=[{"kind": "below", "price": 90.0,
                             "set_at": "x", "triggered_at": None}])
        self.assertEqual(self.evaluate(cfg, readings(price=95.0)), [])
        alerts = self.evaluate(cfg, readings(price=89.5))
        self.assertEqual([a["rule"] for a in alerts], ["level"])

    def test_all_readings_missing_is_safe(self):
        cfg = entry()
        r = {k: None for k in readings()}
        self.assertEqual(self.evaluate(cfg, r), [])
        self.assertIsNone(cfg["state"]["rsi_oversold"]["armed"])
        self.assertIsNone(cfg["state"]["macd"]["side"])

    def test_state_survives_json_round_trip(self):
        cfg = entry()
        self.evaluate(cfg, readings(rsi=45.0))
        self.evaluate(cfg, readings(rsi=29.0))
        cfg2 = json.loads(json.dumps(cfg))
        alerts = watch.evaluate_symbol(cfg2, readings(rsi=27.0), OPEN_NOW,
                                       "2026-07-22", require_bar_today=True)
        self.assertEqual(alerts, [])
        self.assertFalse(cfg2["state"]["rsi_oversold"]["armed"])


class TestMarketHours(unittest.TestCase):
    def test_summer_open_and_close_edges(self):
        wed = datetime(2026, 7, 22, tzinfo=UTC)
        self.assertFalse(watch.stock_market_open(wed.replace(hour=13, minute=29)))
        self.assertTrue(watch.stock_market_open(wed.replace(hour=13, minute=30)))
        self.assertTrue(watch.stock_market_open(wed.replace(hour=19, minute=59)))
        self.assertFalse(watch.stock_market_open(wed.replace(hour=20, minute=0)))

    def test_winter_shifts_with_dst(self):
        wed = datetime(2026, 1, 21, tzinfo=UTC)
        self.assertFalse(watch.stock_market_open(wed.replace(hour=14, minute=29)))
        self.assertTrue(watch.stock_market_open(wed.replace(hour=14, minute=30)))
        self.assertFalse(watch.stock_market_open(wed.replace(hour=21, minute=0)))

    def test_weekend_closed(self):
        self.assertFalse(watch.stock_market_open(SATURDAY))
        sunday = datetime(2026, 7, 26, 17, 0, tzinfo=UTC)
        self.assertFalse(watch.stock_market_open(sunday))

    def test_today_marker_crypto_utc_stock_et(self):
        # 02:30 UTC on the 23rd is 22:30 ET on the 22nd: crypto rolls on UTC,
        # stock rolls on the NYSE (ET) day. Both zones are explicit so this is
        # deterministic on any host (CI runs in UTC).
        t = datetime(2026, 7, 23, 2, 30, tzinfo=UTC)
        self.assertEqual(watch.today_marker("BTC/USDT", t), "2026-07-23")
        self.assertEqual(watch.today_marker("AAPL", t), "2026-07-22")


class TestComposeMessage(unittest.TestCase):
    def all_messages(self):
        cfg = entry(levels=[{"kind": "above", "price": 100.5,
                             "set_at": "x", "triggered_at": None}])
        watch.evaluate_symbol(cfg, readings(rsi=45.0), OPEN_NOW, "2026-07-22", True)
        alerts = watch.evaluate_symbol(
            cfg, readings(rsi=29.0, macd=0.4, macd_signal=0.5, sma50=94.0,
                          sma200=95.0, change_pct=-6.0, price=101.0),
            OPEN_NOW + timedelta(hours=5), "2026-07-22", True)
        self.assertEqual(len(alerts), 5)  # rsi, macd, sma, day_move, level
        return [watch.compose_message("TSLA", a, readings(price=101.0, change_pct=-6.0),
                                      is_crypto=False) for a in alerts]

    def test_messages_name_symbol_and_side(self):
        msgs = self.all_messages()
        self.assertTrue(all(m.startswith("Watchlist TSLA:") for m in msgs))
        self.assertTrue(any("buy side situation" in m for m in msgs))
        self.assertTrue(any("sell side situation" in m for m in msgs))

    def test_messages_respect_glyph_rules(self):
        for msg in self.all_messages():
            for glyph in ("—", "–", "→", "->", "<", ">"):
                self.assertNotIn(glyph, msg)

    def test_crypto_message_says_24h(self):
        alert = {"rule": "rsi_oversold", "side": "buy side", "text": "RSI(14) test."}
        msg = watch.compose_message("BTC/USDT", alert,
                                    readings(price=65000.0, change_pct=-3.0), True)
        self.assertIn("in 24h", msg)
        self.assertIn("down 3.0%", msg)


class TestSweep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name
        self.sent = []
        patcher = mock.patch.object(alert_sweep, "send_alert",
                                    side_effect=lambda uid, text: self.sent.append(text) or True)
        self.send = patcher.start()
        self.addCleanup(patcher.stop)

    def seed_symbol(self, symbol="AAPL", **entry_kw):
        def apply(data):
            data["symbols"][symbol] = entry(**entry_kw)
        watch.mutate_watchlist(1, apply, self.base)

    def sweep_with(self, readings_map, now=OPEN_NOW, dry_run=False):
        def fake_fetch(symbol, exchange="binance"):
            value = readings_map[symbol]
            if isinstance(value, Exception):
                raise value
            return value
        with mock.patch.object(watch, "fetch_readings", side_effect=fake_fetch) as fr:
            delivered, self.healthy = alert_sweep.sweep(1, dry_run=dry_run,
                                                        base_dir=self.base, now=now)
        return delivered, fr

    def test_seed_sweep_sends_nothing_but_persists_state(self):
        self.seed_symbol()
        delivered, _ = self.sweep_with({"AAPL": readings(rsi=45.0)})
        self.assertEqual(delivered, 0)
        state = watch.load_watchlist(1, self.base)["symbols"]["AAPL"]["state"]
        self.assertTrue(state["rsi_oversold"]["armed"])

    def test_cross_after_seed_delivers_and_logs(self):
        self.seed_symbol()
        self.sweep_with({"AAPL": readings(rsi=45.0)})
        delivered, _ = self.sweep_with({"AAPL": readings(rsi=29.0)},
                                       now=OPEN_NOW + timedelta(minutes=15))
        self.assertEqual(delivered, 1)
        self.assertIn("RSI(14) crossed under 30", self.sent[0])
        log = watch.alerts_log_path(1, self.base).read_text().strip().splitlines()
        self.assertEqual(len(log), 1)
        self.assertEqual(json.loads(log[0])["rule"], "rsi_oversold")

    def test_stock_skipped_when_market_closed(self):
        self.seed_symbol()
        delivered, fetches = self.sweep_with({"AAPL": readings()}, now=SATURDAY)
        self.assertEqual(delivered, 0)
        fetches.assert_not_called()

    def test_crypto_evaluated_on_weekend(self):
        self.seed_symbol("BTC/USDT")
        _, fetches = self.sweep_with({"BTC/USDT": readings()}, now=SATURDAY)
        fetches.assert_called_once()

    def test_failed_send_keeps_old_state_for_retry(self):
        self.seed_symbol()
        self.sweep_with({"AAPL": readings(rsi=45.0)})
        self.send.side_effect = lambda uid, text: False
        delivered, _ = self.sweep_with({"AAPL": readings(rsi=29.0)},
                                       now=OPEN_NOW + timedelta(minutes=15))
        self.assertEqual(delivered, 0)
        state = watch.load_watchlist(1, self.base)["symbols"]["AAPL"]["state"]
        self.assertTrue(state["rsi_oversold"]["armed"])  # not consumed
        self.send.side_effect = lambda uid, text: self.sent.append(text) or True
        delivered, _ = self.sweep_with({"AAPL": readings(rsi=29.0)},
                                       now=OPEN_NOW + timedelta(minutes=30))
        self.assertEqual(delivered, 1)

    def test_dry_run_changes_nothing(self):
        self.seed_symbol()
        before = json.dumps(watch.load_watchlist(1, self.base), sort_keys=True)
        delivered, _ = self.sweep_with({"AAPL": readings(rsi=45.0)}, dry_run=True)
        self.assertEqual(delivered, 0)
        self.send.assert_not_called()
        after = json.dumps(watch.load_watchlist(1, self.base), sort_keys=True)
        self.assertEqual(before, after)

    def test_symbol_added_mid_sweep_survives(self):
        self.seed_symbol("AAPL")

        def fetch_and_inject(symbol, exchange="binance"):
            def inject(data):
                data["symbols"]["NVDA"] = entry()
            watch.mutate_watchlist(1, inject, self.base)
            return readings(rsi=45.0)

        with mock.patch.object(watch, "fetch_readings", side_effect=fetch_and_inject):
            alert_sweep.sweep(1, base_dir=self.base, now=OPEN_NOW)
        symbols = watch.load_watchlist(1, self.base)["symbols"]
        self.assertIn("NVDA", symbols)
        self.assertIn("AAPL", symbols)
        self.assertTrue(symbols["AAPL"]["state"]["rsi_oversold"]["armed"])

    def test_symbol_removed_mid_sweep_stays_removed(self):
        self.seed_symbol("AAPL")

        def fetch_and_remove(symbol, exchange="binance"):
            def drop(data):
                del data["symbols"]["AAPL"]
            watch.mutate_watchlist(1, drop, self.base)
            return readings(rsi=45.0)

        with mock.patch.object(watch, "fetch_readings", side_effect=fetch_and_remove):
            alert_sweep.sweep(1, base_dir=self.base, now=OPEN_NOW)
        self.assertNotIn("AAPL", watch.load_watchlist(1, self.base)["symbols"])

    def test_one_bad_symbol_does_not_block_others(self):
        self.seed_symbol("AAPL")
        self.seed_symbol("MSFT")
        delivered, _ = self.sweep_with({"AAPL": RuntimeError("rate limited"),
                                        "MSFT": readings(rsi=45.0)})
        self.assertEqual(delivered, 0)
        self.assertTrue(self.healthy)  # partial failure still counts as alive
        state = watch.load_watchlist(1, self.base)["symbols"]["MSFT"]["state"]
        self.assertTrue(state["rsi_oversold"]["armed"])  # MSFT still evaluated

    def test_all_symbols_failing_reports_unhealthy(self):
        self.seed_symbol("AAPL")
        self.seed_symbol("MSFT")
        self.sweep_with({"AAPL": RuntimeError("down"), "MSFT": RuntimeError("down")})
        self.assertFalse(self.healthy)

    def test_missing_finance_stack_does_not_crash_sweep(self):
        # fetch_readings raises SystemExit (a BaseException) when talib is
        # absent (lazy_import). The sweep must catch it like a data outage
        # rather than let it escape and crash every 15 minutes. Without the
        # guard this test errors (SystemExit propagates out of sweep()).
        self.seed_symbol("AAPL")
        self.seed_symbol("BTC/USDT")

        def missing_stack(symbol, exchange="binance"):
            raise SystemExit("talib is not available yet")

        with mock.patch.object(watch, "fetch_readings", side_effect=missing_stack):
            delivered, healthy = alert_sweep.sweep(1, base_dir=self.base, now=OPEN_NOW)
        self.assertEqual(delivered, 0)
        self.assertFalse(healthy)  # every symbol failed -> blindness throttle applies

    def test_market_closed_everywhere_is_healthy(self):
        self.seed_symbol("AAPL")
        self.sweep_with({"AAPL": readings()}, now=SATURDAY)
        self.assertTrue(self.healthy)  # nothing attempted, nothing failed

    def test_empty_watchlist_is_a_quiet_noop(self):
        delivered, healthy = alert_sweep.sweep(1, base_dir=self.base, now=OPEN_NOW)
        self.assertEqual(delivered, 0)
        self.assertTrue(healthy)


class TestHealthGate(unittest.TestCase):
    """Blindness policy: first all-fail sweep quiet, second consecutive exits 1,
    then throttled to one failing exit per 4h; healthy resets the record."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name

    def gate(self, healthy, now):
        return alert_sweep.health_exit_code(1, healthy, now, self.base)

    def test_single_blip_stays_quiet(self):
        self.assertEqual(self.gate(False, OPEN_NOW), 0)

    def test_second_consecutive_failure_exits_1(self):
        self.gate(False, OPEN_NOW)
        self.assertEqual(self.gate(False, OPEN_NOW + timedelta(minutes=15)), 1)

    def test_ping_throttled_to_four_hours(self):
        self.gate(False, OPEN_NOW)
        self.assertEqual(self.gate(False, OPEN_NOW + timedelta(minutes=15)), 1)
        for minutes in range(30, 241, 15):                  # sustained outage
            self.assertEqual(
                self.gate(False, OPEN_NOW + timedelta(minutes=minutes)), 0)
        self.assertEqual(
            self.gate(False, OPEN_NOW + timedelta(minutes=255)), 1)

    def test_healthy_resets_the_record(self):
        self.gate(False, OPEN_NOW)
        self.gate(True, OPEN_NOW + timedelta(minutes=15))
        self.assertEqual(self.gate(False, OPEN_NOW + timedelta(minutes=30)), 0)

    def test_flapping_source_pings_at_most_once_per_window(self):
        self.gate(False, OPEN_NOW)
        self.assertEqual(self.gate(False, OPEN_NOW + timedelta(minutes=15)), 1)
        self.gate(True, OPEN_NOW + timedelta(minutes=30))    # brief recovery
        self.gate(False, OPEN_NOW + timedelta(minutes=45))
        self.assertEqual(self.gate(False, OPEN_NOW + timedelta(minutes=60)), 0)

    def test_gap_between_failures_is_not_consecutive(self):
        self.gate(False, OPEN_NOW)
        self.assertEqual(self.gate(False, OPEN_NOW + timedelta(hours=2)), 0)

    def test_healthy_streak_never_writes_state(self):
        self.assertEqual(self.gate(True, OPEN_NOW), 0)
        self.assertFalse(alert_sweep.health_path(1, self.base).exists())

    def test_corrupt_timestamps_do_not_crash(self):
        path = alert_sweep.health_path(1, self.base)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"last_all_fail": "garbage", "last_ping": 5}')
        self.assertEqual(self.gate(False, OPEN_NOW), 0)   # treated as fresh blip
        self.assertEqual(self.gate(False, OPEN_NOW + timedelta(minutes=15)), 1)


class TestSweepJobRegistration(unittest.TestCase):
    """The per user sweep is armed on first add and torn down when empty."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name

    def _rows(self):
        db = watch.scheduler_db_path(self.base)
        if not db.exists():
            return []
        conn = sqlite3.connect(str(db))
        try:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM job_meta").fetchall()]
        finally:
            conn.close()

    def test_ensure_inserts_a_command_job(self):
        self.assertEqual(watch.ensure_sweep_job(7, self.base), "created")
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["name"], "tradewatch-7")
        self.assertEqual(row["user_id"], 7)
        self.assertEqual(row["job_type"], "command")
        self.assertEqual(row["repeat"], watch.SWEEP_REPEAT)
        self.assertEqual(row["timeout_seconds"], watch.SWEEP_TIMEOUT_SECONDS)
        self.assertEqual(row["notify"], 0)
        self.assertIn("alert_sweep.py --user 7", row["command"])
        self.assertTrue(row["log_file"].endswith("sweep.log"))

    def test_ensure_is_idempotent(self):
        self.assertEqual(watch.ensure_sweep_job(7, self.base), "created")
        self.assertEqual(watch.ensure_sweep_job(7, self.base), "exists")  # already armed
        self.assertEqual(len(self._rows()), 1)

    def test_ensure_reports_error_when_db_unreachable(self):
        # A scheduler DB that cannot be reached must read as "error", not be
        # conflated with the ordinary "exists" case (the bug Jarvis flagged).
        with mock.patch.object(watch.sqlite3, "connect",
                               side_effect=sqlite3.OperationalError("db locked")):
            self.assertEqual(watch.ensure_sweep_job(7, self.base), "error")
        self.assertEqual(self._rows(), [])  # nothing written

    def test_add_warns_when_arming_fails(self):
        # When arming genuinely fails the add must not claim it is watching
        # over a sweep that will never run; it surfaces a warning instead.
        with mock.patch.object(watch.tc, "fetch_quote", return_value={"price": 100.0}), \
                mock.patch.object(watch, "ensure_sweep_job", return_value="error"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = watch.cmd_add(add_args("TSLA", self.base))
        self.assertEqual(rc, 0)  # the symbol is still added to the list
        self.assertIn("could not arm the background sweep", buf.getvalue())

    def test_remove_deletes_only_that_users_job(self):
        watch.ensure_sweep_job(7, self.base)
        watch.ensure_sweep_job(8, self.base)
        self.assertTrue(watch.remove_sweep_job(7, self.base))
        self.assertEqual({r["name"] for r in self._rows()}, {"tradewatch-8"})

    def test_remove_when_no_db_is_a_noop(self):
        self.assertFalse(watch.remove_sweep_job(7, self.base))  # DB never created

    def test_add_arms_and_last_remove_disarms(self):
        with mock.patch.object(watch.tc, "fetch_quote", return_value={"price": 100.0}):
            watch.cmd_add(add_args("TSLA", self.base))
        self.assertEqual({r["name"] for r in self._rows()}, {"tradewatch-1"})
        watch.cmd_remove(argparse.Namespace(symbol="TSLA", user=1, base_dir=self.base))
        self.assertEqual(self._rows(), [])

    def test_remove_keeps_job_while_symbols_remain(self):
        with mock.patch.object(watch.tc, "fetch_quote", return_value={"price": 100.0}):
            watch.cmd_add(add_args("TSLA", self.base))
            watch.cmd_add(add_args("NVDA", self.base))
        watch.cmd_remove(argparse.Namespace(symbol="TSLA", user=1, base_dir=self.base))
        self.assertEqual({r["name"] for r in self._rows()}, {"tradewatch-1"})


class TestUserDirResolution(unittest.TestCase):
    def test_base_dir_override_used_for_tests(self):
        self.assertEqual(watch.trading_dir(5, "/tmp/x"),
                         Path("/tmp/x/users/5/trading"))

    def test_default_resolves_through_core_users(self):
        sentinel = Path("/data/users/5")
        with mock.patch.object(watch, "resolve_user_dir", return_value=sentinel) as rud:
            result = watch.trading_dir(5)
        rud.assert_called_once_with(5)
        self.assertEqual(result, sentinel / "trading")


class TestModuleBinding(unittest.TestCase):
    def test_imports_bound_to_the_trading_scripts(self):
        # Guards the skills/watch bare-name collision: the modules under test
        # must be the trading copies, not the video-watch skill's watch.py.
        for mod in (watch, alert_sweep):
            resolved = str(Path(mod.__file__).resolve()).replace("\\", "/")
            self.assertIn("skills/trading/scripts", resolved)
        self.assertTrue(hasattr(watch, "ensure_sweep_job"))  # trading-only symbol


if __name__ == "__main__":
    unittest.main()
