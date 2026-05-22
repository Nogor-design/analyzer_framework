from __future__ import annotations

"""
Entry Strategy Module Tests
============================
Covers the key units of the entry strategy discovery engine:

  - validation.py         IS/OOS walk-forward split
  - candle/features.py    candle anatomy feature engine
  - candle/patterns.py    pattern detectors
  - candle/signals.py     entry timing emitter
  - outcome/simulator.py  forward outcome simulator
  - ma/features.py        moving average feature engine
  - bb/features.py        Bollinger Band feature engine
  - ranking.py            sweep result ranking / tiering
  - _sweep_base.py        _expand_params, _deep_merge helpers
"""

from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Shared test-data factories
# ---------------------------------------------------------------------------

TZ = "America/Denver"
TICK = 0.25
TICK_VAL = 5.00


def _make_bars(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """
    Generate synthetic 1-minute OHLCV bars with tz-aware index.
    Price walks around 4000 with modest volatility.
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2024-01-02 09:31:00", tz=TZ)
    idx   = pd.date_range(start, periods=n, freq="1min", tz=TZ)

    close = 4000.0 + np.cumsum(rng.normal(0, 2, n))
    noise = rng.uniform(0.5, 3.0, n)
    high  = close + noise
    low   = close - noise
    open_ = close + rng.normal(0, 0.5, n)
    vol   = rng.integers(100, 500, n)

    return pd.DataFrame(
        {"dt": idx, "open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def _make_trades(n: int = 60, seed: int = 7) -> pd.DataFrame:
    """
    Synthetic trades DataFrame matching the simulator output contract.
    Profits are biased positive so both IS and OOS have non-zero gross_loss.
    """
    rng   = np.random.default_rng(seed)
    start = pd.Timestamp("2024-01-02 09:31:00", tz=TZ)
    times = pd.date_range(start, periods=n, freq="5min", tz=TZ)

    profits = rng.choice([-50, -30, 20, 40, 60, 80], size=n, p=[0.15, 0.15, 0.15, 0.2, 0.2, 0.15])
    return pd.DataFrame({
        "entry_time": times,
        "profit_net": profits.astype(float),
        "market_pos": rng.choice(["Long", "Short"], size=n),
    })


def _make_sweep_result(
    n_trades: int = 50,
    profit_factor: float = 1.4,
    win_rate: float = 0.55,
    is_oos_degradation: float | None = None,
    regime_breakdown: dict | None = None,
    session_breakdown: dict | None = None,
    filter_results: list | None = None,
) -> Dict[str, Any]:
    return {
        "pattern_id":       "test_pattern",
        "tf":               5,
        "direction_mode":   "long",
        "entry_timing":     "next_open",
        "outcome_mode":     "atr_1.5x1.0",
        "mtf_mode":         "independent_5m",
        "n_trades":         n_trades,
        "fill_rate":        0.8,
        "metrics": {
            "n_trades":      n_trades,
            "profit_factor": profit_factor,
            "win_rate":      win_rate,
            "expectancy":    80.0,
        },
        "regime_breakdown":  regime_breakdown or {},
        "session_breakdown": session_breakdown or {},
        "filter_results":    filter_results or [],
        "is_oos_degradation": is_oos_degradation,
    }


# ===========================================================================
# 1. validation.py
# ===========================================================================

class TestIsOosValidation:
    from ta_foundation.analysis.entry_strategies.validation import (
        compute_is_oos_degradation,
        compute_is_oos_full,
    )

    def test_basic_degradation_returns_float(self):
        from ta_foundation.analysis.entry_strategies.validation import compute_is_oos_degradation
        trades = _make_trades(n=60)
        result = compute_is_oos_degradation(trades)
        # Should return a float (or None if data doesn't produce two-sided PF)
        assert result is None or isinstance(result, float)

    def test_sufficient_trades_produces_value(self):
        """With biased profitable trades we should get a real degradation value."""
        from ta_foundation.analysis.entry_strategies.validation import compute_is_oos_degradation
        rng = np.random.default_rng(0)
        n   = 100
        start = pd.Timestamp("2024-01-02 09:31:00", tz=TZ)
        times = pd.date_range(start, periods=n, freq="5min", tz=TZ)
        # Mix of wins and losses guarantees non-zero gross_loss on both halves
        profits = np.where(rng.random(n) > 0.35, 50.0, -30.0)
        trades  = pd.DataFrame({"entry_time": times, "profit_net": profits})
        result  = compute_is_oos_degradation(trades)
        assert result is not None
        assert isinstance(result, float)

    def test_insufficient_trades_returns_none(self):
        from ta_foundation.analysis.entry_strategies.validation import compute_is_oos_degradation
        trades = _make_trades(n=5)   # only 5 rows — below 2 × min_trades_per_half=10
        assert compute_is_oos_degradation(trades) is None

    def test_missing_entry_time_returns_none(self):
        from ta_foundation.analysis.entry_strategies.validation import compute_is_oos_degradation
        trades = _make_trades(n=60).drop(columns=["entry_time"])
        assert compute_is_oos_degradation(trades) is None

    def test_none_df_returns_none(self):
        from ta_foundation.analysis.entry_strategies.validation import compute_is_oos_degradation
        assert compute_is_oos_degradation(None) is None

    def test_full_returns_triple(self):
        from ta_foundation.analysis.entry_strategies.validation import compute_is_oos_full
        rng = np.random.default_rng(1)
        n   = 80
        start = pd.Timestamp("2024-01-02 09:31:00", tz=TZ)
        times = pd.date_range(start, periods=n, freq="5min", tz=TZ)
        profits = np.where(rng.random(n) > 0.30, 50.0, -30.0)
        trades  = pd.DataFrame({"entry_time": times, "profit_net": profits})
        pf_is, pf_oos, deg = compute_is_oos_full(trades)
        assert pf_is  is not None and pf_is  > 0
        assert pf_oos is not None and pf_oos > 0
        assert deg    is not None

    def test_degradation_formula(self):
        """Manually verify the (pf_is - pf_oos) / pf_is formula."""
        from ta_foundation.analysis.entry_strategies.validation import compute_is_oos_full
        # Create trades where IS is very good (3:1 W:L) and OOS is mediocre (1:1)
        n  = 80
        start = pd.Timestamp("2024-01-02 09:31:00", tz=TZ)
        times = pd.date_range(start, periods=n, freq="5min", tz=TZ)
        is_n  = int(n * 0.70)   # 56 trades
        oos_n = n - is_n        # 24 trades
        # IS: 75% win rate on +50/-30  → PF = (42*50)/(14*30) = 5.0
        is_profits  = [50.0] * int(is_n  * 0.75) + [-30.0] * (is_n  - int(is_n  * 0.75))
        # OOS: 50% win rate on +50/-30 → PF = (12*50)/(12*30) ≈ 1.67
        oos_profits = [50.0] * int(oos_n * 0.50) + [-30.0] * (oos_n - int(oos_n * 0.50))
        profits = is_profits + oos_profits
        trades  = pd.DataFrame({"entry_time": times, "profit_net": profits})
        pf_is, pf_oos, deg = compute_is_oos_full(trades)
        assert pf_is  is not None
        assert pf_oos is not None
        assert deg    is not None
        assert abs(deg - (pf_is - pf_oos) / pf_is) < 1e-4   # formula matches (4dp rounding)

    def test_custom_fraction_and_min_trades(self):
        from ta_foundation.analysis.entry_strategies.validation import compute_is_oos_degradation
        rng = np.random.default_rng(3)
        n   = 60
        start = pd.Timestamp("2024-01-02 09:31:00", tz=TZ)
        times = pd.date_range(start, periods=n, freq="5min", tz=TZ)
        profits = np.where(rng.random(n) > 0.35, 50.0, -30.0)
        trades  = pd.DataFrame({"entry_time": times, "profit_net": profits})
        # With 60 trades and 0.5/0.5 split, 30 in each half — above min_trades_per_half=5
        result = compute_is_oos_degradation(trades, is_fraction=0.50, min_trades_per_half=5)
        assert result is not None


# ===========================================================================
# 2. candle/features.py
# ===========================================================================

class TestCandleFeatures:
    def _enrich(self, n: int = 100):
        from ta_foundation.analysis.entry_strategies.candle.features import compute_candle_features
        return compute_candle_features(_make_bars(n), {})

    def test_output_has_anatomy_columns(self):
        enriched = self._enrich()
        for col in ("body", "upper_wick", "lower_wick", "total_range", "is_bullish"):
            assert col in enriched.columns, f"Missing column: {col}"

    def test_output_has_atr_column(self):
        enriched = self._enrich()
        assert "atr" in enriched.columns

    def test_output_has_rolling_size_columns(self):
        from ta_foundation.analysis.entry_strategies.candle.features import compute_candle_features
        enriched = compute_candle_features(_make_bars(100), {"size_lookbacks": [5, 10]})
        assert "size_vs_roll_5"  in enriched.columns
        assert "size_vs_roll_10" in enriched.columns

    def test_output_has_ratio_columns(self):
        enriched = self._enrich()
        for col in ("body_to_range", "upper_wick_to_range", "lower_wick_to_range"):
            assert col in enriched.columns

    def test_no_negative_ranges(self):
        enriched = self._enrich()
        valid = enriched["total_range"].dropna()
        assert (valid >= 0).all()

    def test_row_count_preserved(self):
        bars     = _make_bars(150)
        enriched = self._enrich(150)
        assert len(enriched) == len(bars)

    def test_lag_safety_first_row_atr_finite(self):
        """ATR is computed with min_periods=1 so even the first bar should be finite."""
        enriched = self._enrich(50)
        assert np.isfinite(enriched["atr"].iloc[0])


# ===========================================================================
# 3. candle/patterns.py
# ===========================================================================

class TestCandlePatterns:
    def _enriched(self, n: int = 200):
        from ta_foundation.analysis.entry_strategies.candle.features import compute_candle_features
        return compute_candle_features(_make_bars(n), {})

    def test_detect_known_pattern_returns_dataframe(self):
        from ta_foundation.analysis.entry_strategies.candle.patterns import detect_pattern
        enriched = self._enriched()
        result   = detect_pattern("engulfing_bullish", enriched, {"direction": 1})
        assert isinstance(result, pd.DataFrame)

    def test_detect_doji(self):
        from ta_foundation.analysis.entry_strategies.candle.patterns import detect_pattern
        enriched = self._enriched()
        result   = detect_pattern("doji", enriched, {"direction": 0, "body_to_range_max": 0.15})
        assert isinstance(result, pd.DataFrame)

    def test_detect_large_body(self):
        from ta_foundation.analysis.entry_strategies.candle.patterns import detect_pattern
        enriched = self._enriched()
        result   = detect_pattern("large_body", enriched, {
            "direction": 0, "body_multiplier": 1.5,
            "wick_to_body_max": 0.5, "lookback": 10,
        })
        assert isinstance(result, pd.DataFrame)

    def test_unknown_pattern_returns_empty(self):
        from ta_foundation.analysis.entry_strategies.candle.patterns import detect_pattern
        enriched = self._enriched()
        result   = detect_pattern("no_such_pattern_xyz", enriched, {})
        assert result is None or result.empty

    def test_direction_filter_long_only(self):
        from ta_foundation.analysis.entry_strategies.candle.patterns import detect_pattern
        enriched = self._enriched(300)
        result   = detect_pattern("engulfing_bullish", enriched, {"direction": 1})
        if result is not None and not result.empty:
            assert (result["direction"] == 1).all()

    def test_direction_filter_short_only(self):
        from ta_foundation.analysis.entry_strategies.candle.patterns import detect_pattern
        enriched = self._enriched(300)
        result   = detect_pattern("engulfing_bearish", enriched, {"direction": -1})
        if result is not None and not result.empty:
            assert (result["direction"] == -1).all()

    def test_signals_have_atr_column(self):
        from ta_foundation.analysis.entry_strategies.candle.patterns import detect_pattern
        enriched = self._enriched(300)
        result   = detect_pattern("large_body", enriched, {
            "direction": 0, "body_multiplier": 1.5,
            "wick_to_body_max": 0.5, "lookback": 10,
        })
        if result is not None and not result.empty:
            assert "atr" in result.columns


# ===========================================================================
# 4. candle/signals.py — emit_entries
# ===========================================================================

class TestEmitEntries:
    def _signals(self, n: int = 300):
        """Use inside_bar — fires on ~10–20% of bars so guaranteed signals on n=300."""
        from ta_foundation.analysis.entry_strategies.candle.features import compute_candle_features
        from ta_foundation.analysis.entry_strategies.candle.patterns import detect_pattern
        bars     = _make_bars(n)
        enriched = compute_candle_features(bars, {})
        sigs     = detect_pattern("inside_bar", enriched, {"direction": 0})
        return sigs, bars

    def test_next_open_produces_pending(self):
        from ta_foundation.analysis.entry_strategies.candle.signals import emit_entries
        from ta_foundation.marketdata.resample import ohlcv_resample_from_bars
        signals, bars = self._signals()
        if signals is None or signals.empty:
            pytest.skip("No signals detected in synthetic data")
        bars_next = ohlcv_resample_from_bars(bars, "1m")
        pending   = emit_entries(signals, "next_open", bars=bars_next, params={"tick_size": TICK})
        assert pending is not None and not pending.empty
        assert "timing_mode"  in pending.columns
        assert "entry_price"  in pending.columns
        assert "direction"    in pending.columns
        assert "signal_atr"   in pending.columns

    def test_break_extreme_produces_pending(self):
        from ta_foundation.analysis.entry_strategies.candle.signals import emit_entries
        signals, _ = self._signals()
        if signals is None or signals.empty:
            pytest.skip("No signals detected in synthetic data")
        pending = emit_entries(signals, "break_extreme", bars=None,
                               params={"tick_size": TICK, "buffer_ticks": 1})
        assert pending is not None and not pending.empty
        assert "limit_price" in pending.columns

    def test_body_midpoint_produces_pending(self):
        from ta_foundation.analysis.entry_strategies.candle.signals import emit_entries
        signals, _ = self._signals()
        if signals is None or signals.empty:
            pytest.skip("No signals detected in synthetic data")
        pending = emit_entries(signals, "body_midpoint", bars=None,
                               params={"tick_size": TICK})
        assert pending is not None and not pending.empty

    def test_next_open_entry_price_not_nan(self):
        from ta_foundation.analysis.entry_strategies.candle.signals import emit_entries
        from ta_foundation.marketdata.resample import ohlcv_resample_from_bars
        signals, bars = self._signals()
        if signals is None or signals.empty:
            pytest.skip("No signals detected in synthetic data")
        bars_next = ohlcv_resample_from_bars(bars, "1m")
        pending   = emit_entries(signals, "next_open", bars=bars_next, params={"tick_size": TICK})
        if pending is not None and not pending.empty:
            assert pending["entry_price"].notna().any()


# ===========================================================================
# 5. outcome/simulator.py
# ===========================================================================

class TestOutcomeSimulator:
    def _pending(self, n: int = 400):
        from ta_foundation.analysis.entry_strategies.candle.features import compute_candle_features
        from ta_foundation.analysis.entry_strategies.candle.patterns import detect_pattern
        from ta_foundation.analysis.entry_strategies.candle.signals import emit_entries
        from ta_foundation.marketdata.resample import ohlcv_resample_from_bars

        bars     = _make_bars(n)
        enriched = compute_candle_features(bars, {})
        # inside_bar fires frequently on synthetic data — no risk of zero signals
        sigs     = detect_pattern("inside_bar", enriched, {"direction": 0})
        if sigs is None or sigs.empty:
            return None, bars
        bars_next = ohlcv_resample_from_bars(bars, "1m")
        pending   = emit_entries(sigs, "next_open", bars=bars_next, params={"tick_size": TICK})
        return pending, bars

    def test_simulate_produces_trades(self):
        from ta_foundation.analysis.entry_strategies.outcome.simulator import simulate_outcomes
        pending, bars = self._pending()
        if pending is None or pending.empty:
            pytest.skip("No pending entries")
        outcome_cfg = {
            "atr":   {"enabled": True, "target_mult": 1.5, "stop_mult": 1.0},
            "ticks": {"enabled": False},
            "max_bars_timeout": 20,
            "timeout_result": "loss",
            "tick_size": TICK,
            "tick_value": TICK_VAL,
            "commission_per_side": 2.09,
            "slippage_ticks": 1,
        }
        trades = simulate_outcomes(pending, bars, outcome_cfg)
        assert trades is not None and not trades.empty

    def test_trade_columns_present(self):
        from ta_foundation.analysis.entry_strategies.outcome.simulator import simulate_outcomes
        pending, bars = self._pending()
        if pending is None or pending.empty:
            pytest.skip("No pending entries")
        outcome_cfg = {
            "atr":   {"enabled": True, "target_mult": 1.5, "stop_mult": 1.0},
            "ticks": {"enabled": False},
            "max_bars_timeout": 20, "timeout_result": "loss",
            "tick_size": TICK, "tick_value": TICK_VAL,
            "commission_per_side": 2.09, "slippage_ticks": 1,
        }
        trades = simulate_outcomes(pending, bars, outcome_cfg)
        if trades is None or trades.empty:
            pytest.skip("Simulator produced no trades")
        for col in ("entry_time", "exit_time", "profit_net", "market_pos", "result", "outcome_mode"):
            assert col in trades.columns, f"Missing: {col}"

    def test_outcome_mode_tag_present(self):
        from ta_foundation.analysis.entry_strategies.outcome.simulator import simulate_outcomes
        pending, bars = self._pending()
        if pending is None or pending.empty:
            pytest.skip("No pending entries")
        outcome_cfg = {
            "atr":   {"enabled": True, "target_mult": 1.5, "stop_mult": 1.0},
            "ticks": {"enabled": False},
            "max_bars_timeout": 20, "timeout_result": "loss",
            "tick_size": TICK, "tick_value": TICK_VAL,
            "commission_per_side": 2.09, "slippage_ticks": 1,
        }
        trades = simulate_outcomes(pending, bars, outcome_cfg)
        if trades is None or trades.empty:
            pytest.skip("Simulator produced no trades")
        assert trades["outcome_mode"].str.startswith("atr_").any()

    def test_ticks_mode_produces_outcome_mode_tag(self):
        from ta_foundation.analysis.entry_strategies.outcome.simulator import simulate_outcomes
        pending, bars = self._pending()
        if pending is None or pending.empty:
            pytest.skip("No pending entries")
        outcome_cfg = {
            "atr":   {"enabled": False},
            "ticks": {"enabled": True, "take_profit": [20], "stop": [10]},
            "max_bars_timeout": 20, "timeout_result": "loss",
            "tick_size": TICK, "tick_value": TICK_VAL,
            "commission_per_side": 2.09, "slippage_ticks": 1,
        }
        trades = simulate_outcomes(pending, bars, outcome_cfg)
        if trades is None or trades.empty:
            pytest.skip("Simulator produced no trades")
        assert trades["outcome_mode"].str.startswith("ticks_").any()


class TestFillBarExposure:
    """Regression: a limit entry is live exposure on the bar it fills on.
    A fill bar that dips to the stop must be a loss, not silently skipped
    (Phase 0 fix -- the old code scanned only from entry_bar_idx + 1)."""

    def _scenario(self, fill_bar_low: float):
        from ta_foundation.analysis.entry_strategies.outcome.simulator import simulate_outcomes

        start = pd.Timestamp("2026-04-14 07:40", tz="America/Denver")
        dts = [start + pd.Timedelta(minutes=k) for k in range(5)]
        # A long limit at 100.0 fills on bar 1 (high 101 >= 100). Bars 2+
        # run up through the 110 target. Only bar 1's low varies.
        bars = pd.DataFrame({
            "dt":     dts,
            "open":   [100, 100, 100, 115, 115],
            "high":   [100, 101, 116, 116, 116],
            "low":    [100, fill_bar_low, 100, 114, 114],
            "close":  [100, 100, 115, 115, 115],
            "volume": [1] * 5,
        })
        pending = pd.DataFrame([{
            "dt": dts[0], "direction": 1, "timing_mode": "body_midpoint",
            "limit_price": 100.0, "fill_timeout_bars": 3,
        }])
        cfg = {
            "atr": {"enabled": False},
            "ticks": {"enabled": True, "take_profit": [40], "stop": [10]},
            "max_bars_timeout": 10, "timeout_result": "loss",
            "tick_size": 0.25, "tick_value": 5.0,
            "commission_per_side": 0.0, "slippage_ticks": 0,
        }
        return simulate_outcomes(pending, bars, cfg)

    def test_fill_bar_dip_to_stop_is_a_loss(self):
        # fill bar low 97.0 is below the stop (100 - 10*0.25 = 97.5)
        trades = self._scenario(fill_bar_low=97.0)
        assert not trades.empty
        assert trades.iloc[0]["result"] == "loss"
        assert trades.iloc[0]["profit_ticks"] == -10.0

    def test_fill_bar_without_dip_reaches_target(self):
        # fill bar low 99.0 stays above the stop -> trade survives to target
        trades = self._scenario(fill_bar_low=99.0)
        assert not trades.empty
        assert trades.iloc[0]["result"] == "win"
        assert trades.iloc[0]["profit_ticks"] == 40.0

    def _next_open_scenario(self, entry_bar_low: float):
        from ta_foundation.analysis.entry_strategies.outcome.simulator import simulate_outcomes

        start = pd.Timestamp("2026-04-14 07:40", tz="America/Denver")
        dts = [start + pd.Timedelta(minutes=k) for k in range(5)]
        # signal on bar 0; next_open entry at bar 1's open (100.0). Only bar 1's
        # low varies; bars 2+ run up through the 110 target.
        bars = pd.DataFrame({
            "dt":     dts,
            "open":   [100, 100, 100, 115, 115],
            "high":   [100, 101, 116, 116, 116],
            "low":    [100, entry_bar_low, 100, 114, 114],
            "close":  [100, 100, 115, 115, 115],
            "volume": [1] * 5,
        })
        pending = pd.DataFrame([{
            "dt": dts[0], "direction": 1, "timing_mode": "next_open",
            "entry_price": 100.0,
        }])
        cfg = {
            "atr": {"enabled": False},
            "ticks": {"enabled": True, "take_profit": [40], "stop": [10]},
            "max_bars_timeout": 10, "timeout_result": "loss",
            "tick_size": 0.25, "tick_value": 5.0,
            "commission_per_side": 0.0, "slippage_ticks": 0,
        }
        return simulate_outcomes(pending, bars, cfg)

    def test_next_open_entry_bar_is_scanned(self):
        # A next_open entry is in at the entry bar's open -> that whole bar is
        # exposure. The fast path scans from signal+1 (= the entry bar), so a
        # dip to the stop on the entry bar must be a loss, not skipped.
        trades = self._next_open_scenario(entry_bar_low=97.0)
        assert not trades.empty
        assert trades.iloc[0]["result"] == "loss"
        assert trades.iloc[0]["profit_ticks"] == -10.0

    def test_next_open_without_dip_reaches_target(self):
        trades = self._next_open_scenario(entry_bar_low=99.0)
        assert not trades.empty
        assert trades.iloc[0]["result"] == "win"
        assert trades.iloc[0]["profit_ticks"] == 40.0

    def _atr_scenario(self, fill_bar_low: float):
        from ta_foundation.analysis.entry_strategies.outcome.simulator import simulate_outcomes

        start = pd.Timestamp("2026-04-14 07:40", tz="America/Denver")
        dts = [start + pd.Timedelta(minutes=k) for k in range(5)]
        # body_midpoint limit at 100.0 fills on bar 1; ATR stop = 100 - 1.0*2.0
        # = 98.0, target = 100 + 4.0*2.0 = 108.0; bars 2+ run past the target.
        bars = pd.DataFrame({
            "dt":     dts,
            "open":   [100, 100, 100, 110, 110],
            "high":   [100, 101, 116, 116, 116],
            "low":    [100, fill_bar_low, 100, 109, 109],
            "close":  [100, 100, 112, 112, 112],
            "volume": [1] * 5,
        })
        pending = pd.DataFrame([{
            "dt": dts[0], "direction": 1, "timing_mode": "body_midpoint",
            "limit_price": 100.0, "fill_timeout_bars": 3, "signal_atr": 2.0,
        }])
        cfg = {
            "atr": {"enabled": True, "target_mult": 4.0, "stop_mult": 1.0},
            "ticks": {"enabled": False},
            "max_bars_timeout": 10, "timeout_result": "loss",
            "tick_size": 0.25, "tick_value": 5.0,
            "commission_per_side": 0.0, "slippage_ticks": 0,
        }
        return simulate_outcomes(pending, bars, cfg)

    def test_atr_fill_bar_dip_to_stop_is_a_loss(self):
        # the ATR outcome path had the same fill-bar-skip bug as the tick path
        trades = self._atr_scenario(fill_bar_low=97.0)
        assert not trades.empty
        assert trades.iloc[0]["result"] == "loss"
        assert trades.iloc[0]["profit_ticks"] == -8.0

    def test_atr_fill_bar_without_dip_reaches_target(self):
        trades = self._atr_scenario(fill_bar_low=99.0)
        assert not trades.empty
        assert trades.iloc[0]["result"] == "win"


# ===========================================================================
# 6. ma/features.py
# ===========================================================================

class TestMAFeatures:
    def _enrich(self, n: int = 200):
        from ta_foundation.analysis.entry_strategies.ma.features import compute_ma_features
        return compute_ma_features(_make_bars(n), {
            "periods":    [9, 20],
            "ma_types":   ["ema", "sma"],
            "atr_period": 14,
            "tick_size":  TICK,
        })

    def test_ema_column_present(self):
        enriched = self._enrich()
        assert "ma_9_ema" in enriched.columns
        assert "ma_20_ema" in enriched.columns

    def test_sma_column_present(self):
        enriched = self._enrich()
        assert "ma_9_sma" in enriched.columns

    def test_dist_slope_above_columns(self):
        enriched = self._enrich()
        for suffix in ("_dist", "_slope", "_above"):
            col = f"ma_20_ema{suffix}"
            assert col in enriched.columns, f"Missing: {col}"

    def test_atr_column_present(self):
        enriched = self._enrich()
        assert "atr_14" in enriched.columns

    def test_above_is_integer_valued(self):
        enriched = self._enrich()
        vals = enriched["ma_20_ema_above"].dropna().unique()
        for v in vals:
            assert v in (-1, 0, 1), f"Unexpected above value: {v}"

    def test_row_count_preserved(self):
        bars     = _make_bars(150)
        enriched = self._enrich(150)
        assert len(enriched) == len(bars)

    def test_no_look_ahead_on_above(self):
        """
        Shift-1 guarantee: ma_above at row i should equal sign(close[i-1] - ma[i-1]).
        We can't test the exact formula without recomputing, but we can verify the column
        is not identical to an unshifted calculation for the first bar.
        """
        enriched = self._enrich(100)
        # First bar's MA close is NaN (can't compute EMA from 0 bars), so above=0
        assert enriched["ma_20_ema_above"].iloc[0] == 0


# ===========================================================================
# 7. bb/features.py
# ===========================================================================

class TestBBFeatures:
    def _enrich(self, n: int = 200):
        from ta_foundation.analysis.entry_strategies.bb.features import compute_bb_features
        return compute_bb_features(_make_bars(n), {
            "periods":           [20],
            "n_stds":            [2.0],
            "atr_period":        14,
            "squeeze_threshold": 0.04,
            "tick_size":         TICK,
        })

    def _prefix(self) -> str:
        """Actual column prefix: float n_std 2.0 → '2' (dot stripped)."""
        enriched = self._enrich()
        # Discover actual prefix by finding a column starting with bb_20_
        for col in enriched.columns:
            if col.startswith("bb_20_") and col.endswith("_mid"):
                return col[:-4]   # strip trailing "_mid"
        raise AssertionError("No bb_20_*_mid column found")

    def test_band_columns_present(self):
        enriched = self._enrich()
        pfx = self._prefix()
        for suffix in ("_mid", "_upper", "_lower"):
            col = pfx + suffix
            assert col in enriched.columns, f"Missing: {col}"

    def test_pct_b_and_z_columns(self):
        enriched = self._enrich()
        pfx = self._prefix()
        for suffix in ("_pct_b", "_z", "_width", "_squeeze"):
            col = pfx + suffix
            assert col in enriched.columns, f"Missing: {col}"

    def test_upper_above_lower(self):
        enriched = self._enrich()
        pfx   = self._prefix()
        upper = pfx + "_upper"
        lower = pfx + "_lower"
        valid = enriched[[upper, lower]].dropna()
        assert (valid[upper] >= valid[lower]).all()

    def test_squeeze_is_boolean_like(self):
        enriched = self._enrich()
        pfx  = self._prefix()
        vals = enriched[pfx + "_squeeze"].dropna().unique()
        for v in vals:
            assert v in (True, False, 0, 1)

    def test_row_count_preserved(self):
        bars     = _make_bars(150)
        enriched = self._enrich(150)
        assert len(enriched) == len(bars)


# ===========================================================================
# 8. ranking.py
# ===========================================================================

class TestRankSweepResults:
    from ta_foundation.analysis.entry_strategies.ranking import rank_sweep_results

    def _rank(self, results):
        from ta_foundation.analysis.entry_strategies.ranking import rank_sweep_results
        return rank_sweep_results(results)

    def test_returns_five_tier_keys(self):
        results = [_make_sweep_result() for _ in range(5)]
        ranked  = self._rank(results)
        assert set(ranked.keys()) == {
            "best_raw", "best_filtered", "most_robust",
            "likely_overfit", "most_useful_filters",
        }

    def test_best_raw_requires_min_trades(self):
        low_trades = _make_sweep_result(n_trades=5)    # below default min_trades_raw=20
        ok_trades  = _make_sweep_result(n_trades=30)
        ranked     = self._rank([low_trades, ok_trades])
        labels     = [r["n_trades"] for r in ranked["best_raw"]]
        assert 5  not in labels
        assert 30 in labels

    def test_best_raw_sorted_descending(self):
        results = [
            _make_sweep_result(n_trades=30, profit_factor=1.2),
            _make_sweep_result(n_trades=30, profit_factor=2.5),
            _make_sweep_result(n_trades=30, profit_factor=1.8),
        ]
        ranked = self._rank(results)
        scores = [r["_score"] for r in ranked["best_raw"]]
        assert scores == sorted(scores, reverse=True)

    def test_most_robust_excluded_when_high_degradation(self):
        robust_cfg = {
            "min_trades_robust": 40,
            "robust_degradation_max": 0.15,
            "robust_min_regimes": 1,
            "robust_min_sessions": 1,
        }
        multi_regime = {
            "trending": {"n_trades": 20, "profit_factor": 1.4},
            "ranging":  {"n_trades": 15, "profit_factor": 1.2},
        }
        multi_sess = {
            "morning": {"n_trades": 20, "profit_factor": 1.4},
            "midday":  {"n_trades": 15, "profit_factor": 1.2},
        }
        bad  = _make_sweep_result(n_trades=60, is_oos_degradation=0.50,
                                   regime_breakdown=multi_regime,
                                   session_breakdown=multi_sess)
        good = _make_sweep_result(n_trades=60, is_oos_degradation=0.05,
                                   regime_breakdown=multi_regime,
                                   session_breakdown=multi_sess)
        from ta_foundation.analysis.entry_strategies.ranking import rank_sweep_results
        ranked = rank_sweep_results([bad, good], config=robust_cfg)
        robust_degs = [r.get("is_oos_degradation") for r in ranked["most_robust"]]
        assert 0.50 not in robust_degs
        assert 0.05 in robust_degs

    def test_likely_overfit_high_pf_few_trades(self):
        high_pf_few = _make_sweep_result(n_trades=10, profit_factor=3.0)
        ranked      = self._rank([high_pf_few])
        flags_list  = [r["_overfit_flags"] for r in ranked["likely_overfit"]]
        assert any("high_PF" in f for f in flags_list)

    def test_most_useful_filters_aggregates(self):
        cond = "session == morning"
        r1 = _make_sweep_result(n_trades=30, profit_factor=1.3,
                                 filter_results=[{"condition_str": cond, "pf_after": 2.0}])
        r2 = _make_sweep_result(n_trades=30, profit_factor=1.1,
                                 filter_results=[{"condition_str": cond, "pf_after": 1.8}])
        ranked = self._rank([r1, r2])
        conditions = [f["condition"] for f in ranked["most_useful_filters"]]
        assert cond in conditions
        match = next(f for f in ranked["most_useful_filters"] if f["condition"] == cond)
        assert match["n_patterns_improved"] == 2

    def test_empty_results_returns_empty_tiers(self):
        ranked = self._rank([])
        for tier in ranked.values():
            assert tier == []

    def test_best_filtered_requires_filter_results(self):
        no_filter = _make_sweep_result(n_trades=30, filter_results=[])
        with_filter = _make_sweep_result(n_trades=30, profit_factor=1.2,
                                          filter_results=[{"condition_str": "x", "pf_after": 1.8}])
        ranked = self._rank([no_filter, with_filter])
        assert len(ranked["best_filtered"]) == 1


# ===========================================================================
# 9. _sweep_base helpers
# ===========================================================================

class TestSweepBaseHelpers:
    def test_expand_params_single_combo(self):
        from ta_foundation.analysis.entry_strategies._sweep_base import _expand_params
        cfg    = {"period": 20, "ma_type": "ema"}
        combos = _expand_params(cfg)
        assert combos == [{"period": 20, "ma_type": "ema"}]

    def test_expand_params_two_list_values(self):
        from ta_foundation.analysis.entry_strategies._sweep_base import _expand_params
        cfg    = {"period": [9, 20], "ma_type": ["ema", "sma"]}
        combos = _expand_params(cfg)
        assert len(combos) == 4   # 2 × 2
        periods = {c["period"] for c in combos}
        types   = {c["ma_type"] for c in combos}
        assert periods == {9, 20}
        assert types   == {"ema", "sma"}

    def test_expand_params_ignores_enabled(self):
        from ta_foundation.analysis.entry_strategies._sweep_base import _expand_params
        cfg    = {"enabled": True, "period": [9, 20]}
        combos = _expand_params(cfg)
        # "enabled" must not appear as a param key
        for c in combos:
            assert "enabled" not in c

    def test_deep_merge_scalar_override(self):
        from ta_foundation.analysis.entry_strategies._sweep_base import _deep_merge
        base     = {"a": 1, "b": 2}
        override = {"b": 99}
        result   = _deep_merge(base, override)
        assert result == {"a": 1, "b": 99}

    def test_deep_merge_nested(self):
        from ta_foundation.analysis.entry_strategies._sweep_base import _deep_merge
        base     = {"outer": {"a": 1, "b": 2}, "x": 10}
        override = {"outer": {"b": 99}, "y": 20}
        result   = _deep_merge(base, override)
        assert result["outer"] == {"a": 1, "b": 99}   # nested merge
        assert result["x"] == 10
        assert result["y"] == 20

    def test_deep_merge_does_not_mutate_base(self):
        from ta_foundation.analysis.entry_strategies._sweep_base import _deep_merge
        base     = {"a": 1, "nested": {"x": 1}}
        override = {"nested": {"x": 99}}
        _deep_merge(base, override)
        assert base["nested"]["x"] == 1   # base unchanged

    def test_deep_merge_list_not_merged(self):
        """Lists in override replace, not extend, base lists."""
        from ta_foundation.analysis.entry_strategies._sweep_base import _deep_merge
        base     = {"periods": [9, 20, 50]}
        override = {"periods": [100]}
        result   = _deep_merge(base, override)
        assert result["periods"] == [100]
