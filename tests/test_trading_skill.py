"""Pure-logic tests for the trading skill core (no network, no finance libs).

Runs under the main venv: trading_common imports heavy libraries lazily,
so routing, gating, parsing and cache logic are testable anywhere.
"""

import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "trading" / "scripts"))

import market  # noqa: E402
import trading_common as tc  # noqa: E402

# The finance stack self installs on first use (deps.json), so CI and a fresh
# box run without it. Tests that build a real DataFrame skip there and run on
# a full install; the rest of the suite is pure logic and always runs.
try:
    import pandas  # noqa: F401
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


class TestSymbolRouting(unittest.TestCase):
    def test_slash_pairs_are_crypto(self):
        self.assertTrue(tc.is_crypto("BTC/USDT"))
        self.assertTrue(tc.is_crypto("ETH/EUR"))

    def test_plain_tickers_are_stocks(self):
        self.assertFalse(tc.is_crypto("AAPL"))
        self.assertFalse(tc.is_crypto("^GSPC"))

    def test_yahoo_style_crypto_routes_to_yahoo(self):
        self.assertFalse(tc.is_crypto("BTC-USD"))

    def test_normalize_uppercases_and_strips(self):
        self.assertEqual(tc.normalize_symbol("  eth/usdt "), "ETH/USDT")


class TestIntervalsAndPeriods(unittest.TestCase):
    def test_4h_rejected_for_stocks(self):
        with self.assertRaises(ValueError):
            tc.validate_interval("AAPL", "4h")

    def test_4h_allowed_for_crypto(self):
        tc.validate_interval("BTC/USDT", "4h")  # must not raise

    def test_unknown_interval_rejected_both_ways(self):
        with self.assertRaises(ValueError):
            tc.validate_interval("AAPL", "3h")
        with self.assertRaises(ValueError):
            tc.validate_interval("BTC/USDT", "3h")

    def test_weekly_maps_to_ccxt_spelling(self):
        self.assertEqual(tc.CCXT_TIMEFRAMES["1wk"], "1w")

    def test_unknown_period_rejected(self):
        with self.assertRaises(ValueError):
            tc.clamp_period_days("7y", "1d")

    def test_daily_period_not_clamped(self):
        days, note = tc.clamp_period_days("5y", "1d")
        self.assertEqual(days, 1825)
        self.assertIsNone(note)

    def test_intraday_period_clamped_with_note(self):
        days, note = tc.clamp_period_days("1y", "5m")
        self.assertEqual(days, 59)
        self.assertIn("clamped", note)

    def test_max_period_clamped_for_intraday(self):
        days, note = tc.clamp_period_days("max", "1h")
        self.assertEqual(days, 729)
        self.assertIsNotNone(note)


class TestCache(unittest.TestCase):
    def test_missing_path_is_stale(self):
        self.assertFalse(tc.cache_fresh(Path("/nonexistent/cache.pkl"), 3600))

    def test_fresh_and_stale_with_injected_clock(self):
        import tempfile
        with tempfile.NamedTemporaryFile() as f:
            path = Path(f.name)
            mtime = path.stat().st_mtime
            self.assertTrue(tc.cache_fresh(path, ttl_seconds=100, now=mtime + 50))
            self.assertFalse(tc.cache_fresh(path, ttl_seconds=100, now=mtime + 150))

    def test_ttl_by_interval(self):
        self.assertEqual(tc.ttl_for("1d"), 3600)
        self.assertEqual(tc.ttl_for("1wk"), 3600)
        self.assertEqual(tc.ttl_for("5m"), 300)

    def test_cache_path_flattens_pair_slashes(self):
        path = tc.cache_path("hist", "BTC/USDT", "1y", "1d")
        self.assertNotIn("/", path.name)


class TestInputGate(unittest.TestCase):
    def test_profile_satisfies_gate(self):
        capital, fees, slip = tc.resolve_costs(10_000, "alpaca-us-equity", None, None)
        self.assertEqual((capital, fees, slip), (10_000.0, 0.0, 5.0))

    def test_explicit_bps_satisfy_gate(self):
        capital, fees, slip = tc.resolve_costs(5_000, None, 10, 5)
        self.assertEqual((capital, fees, slip), (5_000.0, 10.0, 5.0))

    def test_missing_capital_blocked(self):
        with self.assertRaises(tc.InputGateError) as ctx:
            tc.resolve_costs(None, "crypto-taker", None, None)
        self.assertIn("--capital", str(ctx.exception))

    def test_zero_or_negative_capital_blocked(self):
        with self.assertRaises(tc.InputGateError):
            tc.resolve_costs(0, "crypto-taker", None, None)
        with self.assertRaises(tc.InputGateError):
            tc.resolve_costs(-100, "crypto-taker", None, None)

    def test_missing_costs_blocked(self):
        with self.assertRaises(tc.InputGateError) as ctx:
            tc.resolve_costs(10_000, None, None, None)
        self.assertIn("--fees-bps", str(ctx.exception))
        self.assertIn("--slippage-bps", str(ctx.exception))

    def test_half_costs_blocked(self):
        with self.assertRaises(tc.InputGateError):
            tc.resolve_costs(10_000, None, 10, None)
        with self.assertRaises(tc.InputGateError):
            tc.resolve_costs(10_000, None, None, 5)

    def test_unknown_profile_lists_options(self):
        with self.assertRaises(tc.InputGateError) as ctx:
            tc.resolve_costs(10_000, "nonsense", None, None)
        self.assertIn("alpaca-us-equity", str(ctx.exception))

    def test_explicit_flag_overrides_profile_field(self):
        _, fees, slip = tc.resolve_costs(10_000, "alpaca-us-equity", 2.5, None)
        self.assertEqual((fees, slip), (2.5, 5.0))

    def test_negative_costs_blocked(self):
        with self.assertRaises(tc.InputGateError):
            tc.resolve_costs(10_000, None, -1, 5)

    def test_bps_to_rate(self):
        self.assertAlmostEqual(tc.bps_to_rate(0, 5), 0.0005)
        self.assertAlmostEqual(tc.bps_to_rate(10, 5), 0.0015)


class TestParamParsing(unittest.TestCase):
    def test_ints_and_floats(self):
        self.assertEqual(tc.parse_params("fast=20,slow=50"), {"fast": 20, "slow": 50})
        self.assertEqual(tc.parse_params("low=30.5"), {"low": 30.5})

    def test_empty_is_empty(self):
        self.assertEqual(tc.parse_params(""), {})

    def test_whitespace_tolerated(self):
        self.assertEqual(tc.parse_params(" fast = 20 , slow = 50 "), {"fast": 20, "slow": 50})

    def test_missing_equals_rejected(self):
        with self.assertRaises(ValueError):
            tc.parse_params("fast")

    def test_non_numeric_rejected(self):
        with self.assertRaises(ValueError):
            tc.parse_params("fast=abc")


class TestStrategyValidation(unittest.TestCase):
    def test_defaults_fill(self):
        merged = tc.validate_strategy_params("sma-cross", {})
        self.assertEqual(merged, {"fast": 20, "slow": 50})

    def test_partial_override(self):
        merged = tc.validate_strategy_params("rsi-meanrev", {"low": 25})
        self.assertEqual(merged["low"], 25)
        self.assertEqual(merged["high"], 70)

    def test_unknown_strategy_lists_names(self):
        with self.assertRaises(ValueError) as ctx:
            tc.validate_strategy_params("momentum", {})
        self.assertIn("sma-cross", str(ctx.exception))

    def test_unknown_param_rejected(self):
        with self.assertRaises(ValueError):
            tc.validate_strategy_params("sma-cross", {"speed": 3})

    def test_fast_must_be_below_slow(self):
        with self.assertRaises(ValueError):
            tc.validate_strategy_params("sma-cross", {"fast": 50, "slow": 50})
        with self.assertRaises(ValueError):
            tc.validate_strategy_params("sma-cross", {"fast": 60, "slow": 50})

    def test_rsi_bounds_enforced(self):
        with self.assertRaises(ValueError):
            tc.validate_strategy_params("rsi-meanrev", {"low": 70, "high": 30})
        with self.assertRaises(ValueError):
            tc.validate_strategy_params("rsi-meanrev", {"high": 120})

    def test_nonpositive_param_rejected(self):
        with self.assertRaises(ValueError):
            tc.validate_strategy_params("sma-cross", {"fast": -5})

    def test_bar_counts_must_be_integers(self):
        with self.assertRaises(ValueError):
            tc.validate_strategy_params("sma-cross", {"fast": 20.5})
        with self.assertRaises(ValueError):
            tc.validate_strategy_params("rsi-meanrev", {"period": 14.5})

    def test_rsi_thresholds_may_be_floats(self):
        merged = tc.validate_strategy_params("rsi-meanrev", {"low": 27.5})
        self.assertEqual(merged["low"], 27.5)


class TestIndicatorTokens(unittest.TestCase):
    def test_defaults_and_params(self):
        self.assertEqual(tc.parse_indicator_tokens("rsi,sma:50,macd"),
                         [("rsi", 14), ("sma", 50), ("macd", None)])

    def test_rsi_custom_period(self):
        self.assertEqual(tc.parse_indicator_tokens("rsi:7"), [("rsi", 7)])

    def test_sma_requires_period(self):
        with self.assertRaises(ValueError):
            tc.parse_indicator_tokens("sma")

    def test_paramless_indicator_rejects_param(self):
        with self.assertRaises(ValueError):
            tc.parse_indicator_tokens("macd:5")

    def test_unknown_indicator_rejected(self):
        with self.assertRaises(ValueError):
            tc.parse_indicator_tokens("vwap")

    def test_bad_param_rejected(self):
        with self.assertRaises(ValueError):
            tc.parse_indicator_tokens("sma:fast")
        with self.assertRaises(ValueError):
            tc.parse_indicator_tokens("sma:0")

    def test_empty_set_rejected(self):
        with self.assertRaises(ValueError):
            tc.parse_indicator_tokens("")

    def test_case_insensitive(self):
        self.assertEqual(tc.parse_indicator_tokens("RSI,SMA:200"), [("rsi", 14), ("sma", 200)])


def _tiny_df():
    pd = tc.lazy_import("pandas")
    idx = pd.to_datetime(["2026-07-01", "2026-07-02"])
    return pd.DataFrame({"Open": [1.0, 2.0], "High": [1.0, 2.0], "Low": [1.0, 2.0],
                         "Close": [1.0, 2.0], "Volume": [10.0, 20.0]}, index=idx)


@unittest.skipUnless(HAS_PANDAS, "pandas not installed (finance stack self installs on use)")
class TestJsonPurity(unittest.TestCase):
    """--json stdout must parse as JSON alone; meta lines go to stderr."""

    def test_history_json_stdout_is_pure_json(self):
        args = argparse.Namespace(symbol="AAPL", period="1y", interval="1d",
                                  exchange="binance", json=True, csv=None, tail=10)
        out = io.StringIO()
        with mock.patch.object(market.tc, "fetch_history", return_value=_tiny_df()), \
             contextlib.redirect_stdout(out), \
             contextlib.redirect_stderr(io.StringIO()):
            market.cmd_history(args)
        rows = json.loads(out.getvalue())
        self.assertEqual(len(rows), 2)

    def test_history_text_mode_header_stays_on_stdout(self):
        args = argparse.Namespace(symbol="AAPL", period="1y", interval="1d",
                                  exchange="binance", json=False, csv=None, tail=10)
        out = io.StringIO()
        with mock.patch.object(market.tc, "fetch_history", return_value=_tiny_df()), \
             contextlib.redirect_stdout(out):
            market.cmd_history(args)
        self.assertIn("2 bars", out.getvalue())

    def test_clamp_note_goes_to_stderr_not_stdout(self):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(tc, "CACHE_DIR", Path(tmp)), \
             mock.patch.object(tc, "_fetch_stock", return_value=_tiny_df()), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            tc.fetch_history("AAPL", period="1y", interval="5m", use_cache=False)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("clamped", err.getvalue())


class TestScreenerCli(unittest.TestCase):
    def test_nonpositive_limit_refused_before_any_network(self):
        script = (Path(__file__).resolve().parent.parent
                  / "skills" / "trading" / "scripts" / "screener.py")
        proc = subprocess.run(
            [sys.executable, str(script), "run", "oversold-us-large", "--limit", "0"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("at least 1", proc.stderr)


class TestExchangeResolution(unittest.TestCase):
    def test_unknown_exchange_is_value_error_not_system_exit(self):
        fake_ccxt = mock.Mock(spec=[])  # no attributes at all
        with mock.patch.object(tc, "lazy_import", return_value=fake_ccxt):
            with self.assertRaises(ValueError) as ctx:
                tc.ccxt_exchange("krakenn")
        self.assertIn("unknown exchange", str(ctx.exception))

    def test_known_exchange_instantiated(self):
        instance = object()
        fake_ccxt = mock.Mock()
        fake_ccxt.kraken = mock.Mock(return_value=instance)
        with mock.patch.object(tc, "lazy_import", return_value=fake_ccxt):
            self.assertIs(tc.ccxt_exchange("kraken"), instance)


class TestRegistries(unittest.TestCase):
    def test_cost_profiles_present(self):
        self.assertEqual(set(tc.COST_PROFILES),
                         {"alpaca-us-equity", "crypto-taker", "zero-cost"})

    def test_strategies_present(self):
        self.assertEqual(set(tc.STRATEGIES), {"sma-cross", "rsi-meanrev"})

    def test_common_module_has_no_heavy_imports(self):
        for module in ("yfinance", "ccxt", "talib", "pandas", "backtesting"):
            self.assertNotIn(module, dir(tc))


if __name__ == "__main__":
    unittest.main()
