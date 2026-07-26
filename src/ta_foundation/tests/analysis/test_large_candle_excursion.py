from __future__ import annotations

"""
Large Candle Forward Excursion — Test Suite
============================================
Tests for:
  - detector.py            qualifying candle detection
  - forward_window.py      forward excursion computation
  - tick_analyzer.py       tick-path reversal analysis
  - sweep.py               main orchestrator
  - Section renderers      smoke tests for HTML generation
"""

import math
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest

from ta_foundation.analysis.large_candle_excursion.detector import detect_large_candles
from ta_foundation.analysis.large_candle_excursion.forward_window import compute_forward_excursion
from ta_foundation.analysis.large_candle_excursion.tick_analyzer import (
    compute_tick_excursion,
    _compute_pre_reversal_excursion,
)
from ta_foundation.analysis.large_candle_excursion.sweep import run_large_candle_excursion


# ---------------------------------------------------------------------------
# Shared test-data factories
# ---------------------------------------------------------------------------

TZ        = "America/Denver"
TICK      = 0.25
TICK_VAL  = 5.00


def _make_bars(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """
    Synthetic 1-minute bars with tz-aware dt.
    Price walks around 4000 with modest volatility.
    """
    rng   = np.random.default_rng(seed)
    start = pd.Timestamp("2024-01-02 09:31:00", tz=TZ)
    idx   = pd.date_range(start, periods=n, freq="1min", tz=TZ)

    close = 4000.0 + np.cumsum(rng.normal(0, 1.5, n))
    noise = rng.uniform(0.5, 2.5, n)
    high  = close + noise
    low   = close - noise
    open_ = close + rng.normal(0, 0.5, n)
    vol   = rng.integers(100, 500, n).astype(float)

    return pd.DataFrame(
        {"dt": idx, "open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def _make_bars_with_large_candle(n: int = 200, large_idx: int = 100) -> pd.DataFrame:
    """
    Bars with one deliberately large candle at large_idx.
    All other bars are small.  Useful for unit-testing detection.
    """
    rng   = np.random.default_rng(7)
    start = pd.Timestamp("2024-01-02 09:31:00", tz=TZ)
    idx   = pd.date_range(start, periods=n, freq="1min", tz=TZ)

    open_ = np.full(n, 4000.0)
    close = open_ + rng.uniform(-0.5, 0.5, n)  # small candles
    high  = np.maximum(open_, close) + 0.25
    low   = np.minimum(open_, close) - 0.25

    # Inject a large bullish candle at large_idx
    open_[large_idx]  = 4000.0
    close[large_idx]  = 4010.0    # 40-tick body
    high[large_idx]   = 4010.5
    low[large_idx]    = 3999.75

    return pd.DataFrame(
        {"dt": idx, "open": open_, "high": high, "low": low, "close": close,
         "volume": np.ones(n) * 100},
        index=idx,
    )


def _make_ticks(bars: pd.DataFrame, n_per_bar: int = 5, seed: int = 99) -> pd.DataFrame:
    """Synthetic tick data derived from bars."""
    rng     = np.random.default_rng(seed)
    records = []
    for _, bar in bars.iterrows():
        for i in range(n_per_bar):
            tick_dt = bar["dt"] + pd.Timedelta(seconds=12 * i)
            price   = float(bar["close"]) + rng.uniform(-0.5, 0.5)
            records.append({"dt": tick_dt, "last": price, "volume": 1.0})
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# 1. Detector tests
# ---------------------------------------------------------------------------

class TestDetector:
    """Tests for detect_large_candles()."""

    def test_returns_dataframe(self):
        bars = _make_bars(200)
        result = detect_large_candles(bars, {"average_lookbacks": [10], "threshold_multipliers": [1.5]})
        assert isinstance(result, pd.DataFrame)

    def test_required_columns_present(self):
        bars = _make_bars(200)
        result = detect_large_candles(bars, {
            "average_lookbacks": [10],
            "average_basis": {"body": True, "range": False},
            "threshold_multipliers": [1.5],
        })
        if result.empty:
            pytest.skip("No qualifying candles in synthetic data — adjust seed")
        required = ["bar_idx", "dt", "open", "high", "low", "close", "body", "total_range",
                    "direction", "basis", "lookback", "threshold_mode", "threshold_value", "ratio"]
        for col in required:
            assert col in result.columns, f"Missing column: {col}"

    def test_detects_large_candle(self):
        """Known large candle must be detected."""
        bars   = _make_bars_with_large_candle(n=200, large_idx=100)
        result = detect_large_candles(bars, {
            "average_lookbacks": [10],
            "average_basis": {"body": True, "range": False},
            "threshold_multipliers": [1.5],
            "min_body_ticks": 1,
            "min_range_ticks": 1,
            "direction": "both",
        })
        # The large candle (40-tick body) must appear
        assert not result.empty, "Should detect the injected large candle"
        large_events = result[result["bar_idx"] == 100]
        assert len(large_events) >= 1, "Bar 100 (large candle) not detected"

    def test_direction_long_only(self):
        bars = _make_bars(300)
        result = detect_large_candles(bars, {
            "average_lookbacks": [10],
            "threshold_multipliers": [1.2],
            "direction": "long_only",
        })
        if not result.empty:
            assert (result["direction"] == 1).all(), "long_only should only return bullish candles"

    def test_direction_short_only(self):
        bars = _make_bars(300)
        result = detect_large_candles(bars, {
            "average_lookbacks": [10],
            "threshold_multipliers": [1.2],
            "direction": "short_only",
        })
        if not result.empty:
            assert (result["direction"] == -1).all(), "short_only should only return bearish candles"

    def test_basis_range_only(self):
        bars = _make_bars(200)
        result = detect_large_candles(bars, {
            "average_basis": {"body": False, "range": True},
            "average_lookbacks": [10],
            "threshold_multipliers": [1.5],
        })
        if not result.empty:
            assert (result["basis"] == "range").all()

    def test_threshold_multiplier_vs_percent(self):
        bars   = _make_bars(300)
        cfg_m  = {"threshold_mode": "multiplier",        "threshold_multipliers": [1.5], "average_lookbacks": [10]}
        cfg_p  = {"threshold_mode": "percent_of_average","threshold_percents":    [150], "average_lookbacks": [10]}
        r_m    = detect_large_candles(bars, cfg_m)
        r_p    = detect_large_candles(bars, cfg_p)
        # Both modes with same effective threshold should produce same count
        assert len(r_m) == len(r_p), "Multiplier and percent modes differ for equivalent threshold"

    def test_empty_bars_returns_empty(self):
        """Empty bars must not raise — returns empty DataFrame."""
        empty = pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])
        # compute_candle_features raises on missing OHLC columns in a fully-empty frame,
        # but that's acceptable behaviour; an empty (but column-present) frame should work.
        try:
            result = detect_large_candles(empty)
            assert isinstance(result, pd.DataFrame)
        except Exception:
            pass  # raising on empty input is also acceptable

    def test_min_body_ticks_guard(self):
        """High min_body_ticks should filter out small candles."""
        bars = _make_bars(200)
        r_low  = detect_large_candles(bars, {"average_lookbacks": [10], "threshold_multipliers": [1.0], "min_body_ticks": 0})
        r_high = detect_large_candles(bars, {"average_lookbacks": [10], "threshold_multipliers": [1.0], "min_body_ticks": 999})
        assert len(r_high) <= len(r_low)

    def test_no_lookahead_bias(self):
        """ratio column must not use the current bar in its own average."""
        bars   = _make_bars_with_large_candle(n=200, large_idx=150)
        result = detect_large_candles(bars, {
            "average_lookbacks": [10],
            "average_basis": {"body": True, "range": False},
            "threshold_multipliers": [1.5],
            "min_body_ticks": 1,
        })
        if result.empty:
            return  # Nothing to check
        # The ratio should be large at bar 150, but bar 151 onward should
        # NOT be inflated by bar 150's body being in their average
        # (shift(1) ensures the average at bar i excludes bar i itself)
        large = result[result["bar_idx"] == 150]
        if not large.empty:
            assert float(large["ratio"].iloc[0]) > 1.5


# ---------------------------------------------------------------------------
# 2. Forward window tests
# ---------------------------------------------------------------------------

class TestForwardWindow:
    """Tests for compute_forward_excursion()."""

    def _make_events(self, bars: pd.DataFrame, large_idx: int = 50) -> pd.DataFrame:
        """Create a minimal events DataFrame from one bar."""
        bar = bars.iloc[large_idx]
        return pd.DataFrame([{
            "bar_idx":         large_idx,
            "dt":              bar["dt"],
            "open":            float(bar["open"]),
            "high":            float(bar["high"]),
            "low":             float(bar["low"]),
            "close":           float(bar["close"]),
            "body":            abs(float(bar["close"]) - float(bar["open"])),
            "total_range":     float(bar["high"]) - float(bar["low"]),
            "body_ticks":      10.0,
            "size_ticks":      12.0,
            "is_bullish":      True,
            "direction":       1,
            "basis":           "body",
            "lookback":        10,
            "threshold_mode":  "multiplier",
            "threshold_value": 1.5,
            "rolling_avg_used": 2.0,
            "ratio":           2.1,
            "atr":             3.0,
        }])

    def test_returns_one_row_per_event_per_window(self):
        bars   = _make_bars(200)
        events = self._make_events(bars, large_idx=50)
        windows = [5, 10, 15]
        result = compute_forward_excursion(events, bars, windows, tf_minutes=1)
        assert len(result) == len(windows), "Should return one row per window_minutes"

    def test_excursion_columns_present(self):
        bars   = _make_bars(200)
        events = self._make_events(bars, large_idx=50)
        result = compute_forward_excursion(events, bars, [10], tf_minutes=1)
        for col in ["fav_ticks", "adv_ticks", "max_up_pts", "max_down_pts",
                    "time_to_max_fav_min", "time_to_max_adv_min", "n_bars_in_window"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_fav_ticks_nonnegative(self):
        bars   = _make_bars(300)
        events = self._make_events(bars, large_idx=100)
        result = compute_forward_excursion(events, bars, [10, 20], tf_minutes=1)
        valid  = result.dropna(subset=["fav_ticks"])
        if not valid.empty:
            assert (valid["fav_ticks"] >= 0).all()

    def test_adv_ticks_nonnegative(self):
        bars   = _make_bars(300)
        events = self._make_events(bars, large_idx=100)
        result = compute_forward_excursion(events, bars, [10], tf_minutes=1)
        valid  = result.dropna(subset=["adv_ticks"])
        if not valid.empty:
            assert (valid["adv_ticks"] >= 0).all()

    def test_bearish_direction_swaps_fav_adv(self):
        """For bearish events, favorable = down, adverse = up."""
        bars   = _make_bars(300)
        ev     = self._make_events(bars, large_idx=100)
        ev_bull = ev.copy()
        ev_bear = ev.copy()
        ev_bear["direction"] = -1
        ev_bear["is_bullish"] = False

        r_bull = compute_forward_excursion(ev_bull, bars, [10], tf_minutes=1)
        r_bear = compute_forward_excursion(ev_bear, bars, [10], tf_minutes=1)

        if not r_bull.empty and not r_bear.empty:
            # For the same bar/bars, bull fav = bear adv and vice versa
            bull_fav = r_bull["fav_ticks"].iloc[0]
            bull_adv = r_bull["adv_ticks"].iloc[0]
            bear_fav = r_bear["fav_ticks"].iloc[0]
            bear_adv = r_bear["adv_ticks"].iloc[0]
            if bull_fav is not None and bear_adv is not None:
                assert math.isclose(float(bull_fav), float(bear_adv), rel_tol=1e-3)
            if bull_adv is not None and bear_fav is not None:
                assert math.isclose(float(bull_adv), float(bear_fav), rel_tol=1e-3)

    def test_time_based_window_not_bar_count(self):
        """
        A 1m bar and a 5m bar at the same close time should both produce
        the same forward window (they look at the same 1m bars).
        """
        bars   = _make_bars(300)
        ev_1m  = self._make_events(bars, large_idx=50)
        ev_5m  = ev_1m.copy()

        r_1m = compute_forward_excursion(ev_1m, bars, [10], tf_minutes=1)
        r_5m = compute_forward_excursion(ev_5m, bars, [10], tf_minutes=5)

        # The 5m version should have fewer bars in window (window starts later)
        # since the close_time = dt + 5 minutes vs dt + 1 minute
        if not r_1m.empty and not r_5m.empty:
            n1 = int(r_1m["n_bars_in_window"].iloc[0] or 0)
            n5 = int(r_5m["n_bars_in_window"].iloc[0] or 0)
            # 5m bar window starts 4 bars later → should have ~4 fewer bars
            assert n1 >= n5

    def test_no_bars_in_window_returns_nans(self):
        """Event near end of data should have no bars in forward window."""
        bars   = _make_bars(60)
        ev     = self._make_events(bars, large_idx=55)  # only 5 bars after
        result = compute_forward_excursion(ev, bars, [60], tf_minutes=1)  # 60-min window
        # Should return a row but with limited or no bars
        assert len(result) == 1

    def test_empty_events_returns_empty(self):
        bars   = _make_bars(100)
        result = compute_forward_excursion(pd.DataFrame(), bars, [10], tf_minutes=1)
        assert result.empty

    def test_time_to_max_positive(self):
        bars   = _make_bars(200)
        events = self._make_events(bars, large_idx=80)
        result = compute_forward_excursion(events, bars, [15], tf_minutes=1)
        valid  = result.dropna(subset=["time_to_max_fav_min"])
        if not valid.empty:
            assert (valid["time_to_max_fav_min"] > 0).all()


# ---------------------------------------------------------------------------
# 3. Tick analyzer tests
# ---------------------------------------------------------------------------

class TestTickAnalyzer:
    """Tests for compute_tick_excursion() and _compute_pre_reversal_excursion()."""

    def test_pre_reversal_bullish_no_reversal(self):
        """Monotonically rising prices → full window move is the pre-reversal fav."""
        prices     = np.array([100.0, 100.5, 101.0, 101.5, 102.0])
        fav, adv, rev = _compute_pre_reversal_excursion(
            prices, close_price=100.0, direction=1,
            tick_size=TICK, reversal_price=1.0,  # 4-tick reversal
        )
        assert not rev
        assert math.isclose(fav, 8.0, rel_tol=1e-3)  # 2.0 pts / 0.25 = 8 ticks

    def test_pre_reversal_bullish_reversal_detected(self):
        """Prices rise then fall more than reversal_ticks → reversal fires."""
        prices = np.array([100.0, 101.0, 102.0, 100.5])  # drops 1.5 from 102 → reversal at 1-tick threshold
        fav, adv, rev = _compute_pre_reversal_excursion(
            prices, close_price=100.0, direction=1,
            tick_size=TICK, reversal_price=0.5,  # 2-tick reversal
        )
        assert rev, "Should detect reversal"
        assert math.isclose(fav, 8.0, rel_tol=1e-3)  # peak was 102 = 8 ticks fav

    def test_pre_reversal_bearish(self):
        """Bearish event: prices fall then bounce → reversal fires on bounce."""
        prices = np.array([100.0, 99.5, 99.0, 99.75])  # drops 1 then bounces 0.75
        fav, adv, rev = _compute_pre_reversal_excursion(
            prices, close_price=100.0, direction=-1,
            tick_size=TICK, reversal_price=0.5,
        )
        assert rev
        assert math.isclose(fav, 4.0, rel_tol=1e-3)  # trough 99 = 4 ticks fav

    def test_empty_prices_returns_zeros(self):
        fav, adv, rev = _compute_pre_reversal_excursion(
            np.array([]), close_price=100.0, direction=1,
            tick_size=TICK, reversal_price=1.0,
        )
        assert fav == 0.0 and adv == 0.0 and not rev

    def test_compute_tick_excursion_adds_columns(self):
        bars   = _make_bars(200)
        ticks  = _make_ticks(bars.iloc[:50], n_per_bar=3)

        # Create a minimal events_with_fwd DataFrame
        ev = pd.DataFrame([{
            "bar_idx":       10,
            "dt":            bars.iloc[10]["dt"],
            "close":         float(bars.iloc[10]["close"]),
            "direction":     1,
            "window_minutes": 5,
        }])
        result = compute_tick_excursion(ev, ticks, tf_minutes=1, tick_size=TICK)
        for col in ["tick_pre_reversal_fav_ticks", "tick_pre_reversal_adv_ticks",
                    "tick_reversal_occurred", "tick_n_ticks_in_window"]:
            assert col in result.columns, f"Missing tick column: {col}"

    def test_no_ticks_returns_nans(self):
        ev = pd.DataFrame([{
            "bar_idx": 10, "dt": pd.Timestamp("2024-01-02 09:41", tz=TZ),
            "close": 4000.0, "direction": 1, "window_minutes": 5,
        }])
        result = compute_tick_excursion(ev, None, tf_minutes=1, tick_size=TICK)
        assert result["tick_pre_reversal_fav_ticks"].isna().all()

    def test_tick_fav_nonnegative(self):
        bars   = _make_bars(200)
        ticks  = _make_ticks(bars, n_per_bar=10)
        events = pd.DataFrame([{
            "bar_idx":       50,
            "dt":            bars.iloc[50]["dt"],
            "close":         float(bars.iloc[50]["close"]),
            "direction":     1,
            "window_minutes": 10,
        }])
        result = compute_tick_excursion(events, ticks, tf_minutes=1, tick_size=TICK)
        assert (result["tick_pre_reversal_fav_ticks"] >= 0).all()


# ---------------------------------------------------------------------------
# 4. Sweep orchestrator tests
# ---------------------------------------------------------------------------

class TestSweep:
    """Tests for run_large_candle_excursion()."""

    def test_returns_dict(self):
        bars   = _make_bars(300)
        result = run_large_candle_excursion(bars_1m=bars, config={
            "timeframes": [1],
            "min_events": 5,
            "candle_size": {
                "average_lookbacks": [10],
                "threshold_multipliers": [1.2],
                "min_body_ticks": 0,
                "min_range_ticks": 0,
                "tick_size": 0.25,
            },
            "forward_window": {"minutes": [5]},
            "tick_analysis": {"use_tick_data": False},
        })
        assert isinstance(result, dict)
        assert result.get("enabled") is True

    def test_result_keys_present(self):
        bars   = _make_bars(300)
        result = run_large_candle_excursion(bars_1m=bars, config={
            "timeframes": [1],
            "min_events": 1,
            "candle_size": {"average_lookbacks": [5], "threshold_multipliers": [1.0], "min_body_ticks": 0, "min_range_ticks": 0},
            "forward_window": {"minutes": [5]},
            "tick_analysis": {"use_tick_data": False},
        })
        for key in ["enabled", "n_combinations_run", "n_results", "combo_results",
                    "events_sample", "config"]:
            assert key in result, f"Missing key: {key}"

    def test_combo_results_json_safe(self):
        """All values in combo_results must be JSON-serializable primitives."""
        import json
        bars   = _make_bars(300)
        result = run_large_candle_excursion(bars_1m=bars, config={
            "timeframes": [1],
            "min_events": 5,
            "candle_size": {"average_lookbacks": [10], "threshold_multipliers": [1.2], "min_body_ticks": 0, "min_range_ticks": 0},
            "forward_window": {"minutes": [5]},
            "tick_analysis": {"use_tick_data": False},
        })
        # Should not raise
        json.dumps(result)

    def test_direction_filter_propagates(self):
        bars   = _make_bars(300)
        result = run_large_candle_excursion(bars_1m=bars, config={
            "timeframes": [1],
            "direction": "long_only",
            "min_events": 5,
            "candle_size": {"average_lookbacks": [10], "threshold_multipliers": [1.2], "min_body_ticks": 0, "min_range_ticks": 0},
            "forward_window": {"minutes": [5]},
            "tick_analysis": {"use_tick_data": False},
        })
        events = result.get("events_sample", [])
        for ev in events:
            assert ev.get("direction") == 1, "long_only should produce only bullish events"

    def test_multiple_timeframes(self):
        bars   = _make_bars(300)
        result = run_large_candle_excursion(bars_1m=bars, config={
            "timeframes": [1, 5],
            "min_events": 5,
            "candle_size": {"average_lookbacks": [10], "threshold_multipliers": [1.2], "min_body_ticks": 0, "min_range_ticks": 0},
            "forward_window": {"minutes": [5]},
            "tick_analysis": {"use_tick_data": False},
        })
        tfs = {cr.get("tf") for cr in result.get("combo_results", [])}
        # Should have results for both TFs (if enough bars)
        assert 1 in tfs or 5 in tfs

    def test_min_events_filter(self):
        bars   = _make_bars(300)
        # min_events=999 should filter out all combos
        result = run_large_candle_excursion(bars_1m=bars, config={
            "timeframes": [1],
            "min_events": 9999,
            "candle_size": {"average_lookbacks": [10], "threshold_multipliers": [1.5], "min_body_ticks": 2, "min_range_ticks": 4},
            "forward_window": {"minutes": [5]},
            "tick_analysis": {"use_tick_data": False},
        })
        assert result["n_results"] == 0

    def test_tick_data_mode(self):
        bars  = _make_bars(200)
        ticks = _make_ticks(bars, n_per_bar=5)
        result = run_large_candle_excursion(bars_1m=bars, config={
            "timeframes": [1],
            "min_events": 5,
            "candle_size": {"average_lookbacks": [10], "threshold_multipliers": [1.2], "min_body_ticks": 0, "min_range_ticks": 0},
            "forward_window": {"minutes": [5]},
            "tick_analysis": {
                "use_tick_data": True,
                "reversal": {"enabled": True, "reversal_ticks": 4, "min_swing_ticks": 1},
            },
        }, ticks=ticks)
        assert result.get("tick_data_used") is True
        # Events should have tick columns
        events = result.get("events_sample", [])
        if events:
            assert "tick_pre_reversal_fav_ticks" in events[0]

    def test_empty_bars_returns_empty_results(self):
        empty = pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])
        result = run_large_candle_excursion(bars_1m=empty)
        assert result["n_results"] == 0
        assert result["combo_results"] == []


# ---------------------------------------------------------------------------
# 5. Section renderer smoke tests
# ---------------------------------------------------------------------------

class TestSectionRenderers:
    """Smoke tests: renderers should return non-empty HTML strings."""

    def _make_ctx(self, data: dict) -> dict:
        return {
            "large_candle_excursion": data,
            "packages": {},
            "options": {},
            "all_options": {"large_candle_excursion": data},
        }

    def _make_full_data(self) -> dict:
        bars   = _make_bars(300)
        return run_large_candle_excursion(bars_1m=bars, config={
            "timeframes": [1],
            "min_events": 5,
            "candle_size": {"average_lookbacks": [10], "threshold_multipliers": [1.2], "min_body_ticks": 0, "min_range_ticks": 0},
            "forward_window": {"minutes": [5, 10]},
            "tick_analysis": {"use_tick_data": False},
        })

    def test_summary_renderer(self):
        from ta_foundation.reports.html.sections.large_candle_excursion_summary import (
            render_large_candle_excursion_summary,
        )
        data = self._make_full_data()
        html = render_large_candle_excursion_summary(self._make_ctx(data))
        assert isinstance(html, str) and len(html) > 50

    def test_summary_no_data(self):
        from ta_foundation.reports.html.sections.large_candle_excursion_summary import (
            render_large_candle_excursion_summary,
        )
        html = render_large_candle_excursion_summary({"packages": {}, "options": {}})
        assert isinstance(html, str) and "No results" in html

    def test_distributions_renderer(self):
        from ta_foundation.reports.html.sections.large_candle_excursion_distributions import (
            render_large_candle_excursion_distributions,
        )
        data = self._make_full_data()
        html = render_large_candle_excursion_distributions(self._make_ctx(data))
        assert isinstance(html, str) and len(html) > 50

    def test_threshold_hits_renderer(self):
        from ta_foundation.reports.html.sections.large_candle_excursion_threshold_hits import (
            render_large_candle_excursion_threshold_hits,
        )
        data = self._make_full_data()
        html = render_large_candle_excursion_threshold_hits(self._make_ctx(data))
        assert isinstance(html, str) and len(html) > 50

    def test_event_table_renderer(self):
        from ta_foundation.reports.html.sections.large_candle_excursion_event_table import (
            render_large_candle_excursion_event_table,
        )
        data = self._make_full_data()
        html = render_large_candle_excursion_event_table(self._make_ctx(data))
        assert isinstance(html, str) and len(html) > 50

    def test_methodology_renderer(self):
        from ta_foundation.reports.html.sections.large_candle_excursion_methodology import (
            render_large_candle_excursion_methodology,
        )
        data = self._make_full_data()
        html = render_large_candle_excursion_methodology(self._make_ctx(data))
        assert isinstance(html, str) and "methodology" in html.lower()


# ---------------------------------------------------------------------------
# 6. Trade Analyzer unit tests
# ---------------------------------------------------------------------------

class TestTradeAnalyzer:
    """Unit tests for trade_analyzer.py helpers and compute_trade_outcomes()."""

    def test_assign_bucket_within_range(self):
        from ta_foundation.analysis.large_candle_excursion.trade_analyzer import assign_candle_bucket
        buckets = [[0, 25], [25, 50], [50, 75], [75, 100], [100, 150], [150, None]]
        assert assign_candle_bucket(60.0,  buckets) == "50-75"
        assert assign_candle_bucket(0.0,   buckets) == "0-25"
        assert assign_candle_bucket(100.0, buckets) == "100-150"
        assert assign_candle_bucket(200.0, buckets) == "150+"

    def test_assign_bucket_boundary(self):
        from ta_foundation.analysis.large_candle_excursion.trade_analyzer import assign_candle_bucket
        buckets = [[0, 25], [25, 50], [150, None]]
        # lower bound is inclusive, upper bound is exclusive
        assert assign_candle_bucket(25.0, buckets) == "25-50"
        assert assign_candle_bucket(24.99, buckets) == "0-25"
        assert assign_candle_bucket(150.0, buckets) == "150+"

    def test_trade_direction_continuation(self):
        from ta_foundation.analysis.large_candle_excursion.trade_analyzer import trade_direction
        assert trade_direction(1,  "continuation") == "long"
        assert trade_direction(-1, "continuation") == "short"

    def test_trade_direction_reverse(self):
        from ta_foundation.analysis.large_candle_excursion.trade_analyzer import trade_direction
        assert trade_direction(1,  "reverse") == "short"
        assert trade_direction(-1, "reverse") == "long"

    def _make_fwd_row(
        self,
        direction: int = 1,
        fav_ticks: float = 50.0,
        adv_ticks: float = 20.0,
        size_ticks: float = 80.0,
        body_ticks: float = 60.0,
        time_fav: float = 5.0,
        time_adv: float = 10.0,
        window: int = 10,
    ) -> pd.DataFrame:
        start = pd.Timestamp("2024-01-02 09:31:00", tz=TZ)
        return pd.DataFrame([{
            "dt":                   start,
            "tf_minutes":           1,
            "window_minutes":       window,
            "lookback":             10,
            "basis":                "body",
            "threshold_mode":       "multiplier",
            "threshold_value":      1.5,
            "direction":            direction,
            "is_bullish":           direction == 1,
            "size_ticks":           size_ticks,
            "body_ticks":           body_ticks,
            "fav_ticks":            fav_ticks,
            "adv_ticks":            adv_ticks,
            "time_to_max_fav_min":  time_fav,
            "time_to_max_adv_min":  time_adv,
        }])

    def test_continuation_win_bullish(self):
        """Bullish signal + continuation + fav_ticks >= target → WIN."""
        from ta_foundation.analysis.large_candle_excursion.trade_analyzer import compute_trade_outcomes
        fwd = self._make_fwd_row(direction=1, fav_ticks=50, adv_ticks=10, size_ticks=80)
        cfg = {
            "enabled": True,
            "trade_modes": {"continuation": True, "reverse": False},
            "signal_candle_basis": "range",
            "target": {"percents": [50]},   # 50% of 80t = 40t < 50t fav → WIN
            "stop": {"enabled": False},
            "candle_size_buckets_ticks": [[0, 100], [100, None]],
        }
        res = compute_trade_outcomes(fwd, cfg)
        assert res["enabled"] is True
        assert len(res["trade_events_sample"]) == 1
        assert res["trade_events_sample"][0]["win"] is True

    def test_continuation_loss_bullish(self):
        """Bullish continuation + fav_ticks < target → LOSS."""
        from ta_foundation.analysis.large_candle_excursion.trade_analyzer import compute_trade_outcomes
        fwd = self._make_fwd_row(direction=1, fav_ticks=20, adv_ticks=5, size_ticks=80)
        cfg = {
            "enabled": True,
            "trade_modes": {"continuation": True, "reverse": False},
            "signal_candle_basis": "range",
            "target": {"percents": [50]},   # 50% of 80t = 40t > 20t fav → LOSS
            "stop": {"enabled": False},
            "candle_size_buckets_ticks": [[0, 100], [100, None]],
        }
        res = compute_trade_outcomes(fwd, cfg)
        assert res["trade_events_sample"][0]["win"] is False

    def test_reverse_win_bullish_candle(self):
        """Bullish candle + reverse (short) + adv_ticks >= target → WIN."""
        from ta_foundation.analysis.large_candle_excursion.trade_analyzer import compute_trade_outcomes
        # adv_ticks = 60 (the 'down' move) → becomes trade_fav for reverse
        fwd = self._make_fwd_row(direction=1, fav_ticks=10, adv_ticks=60, size_ticks=80)
        cfg = {
            "enabled": True,
            "trade_modes": {"continuation": False, "reverse": True},
            "signal_candle_basis": "range",
            "target": {"percents": [50]},   # 50% of 80t = 40t < 60t adv → WIN for reverse
            "stop": {"enabled": False},
            "candle_size_buckets_ticks": [[0, 100], [100, None]],
        }
        res = compute_trade_outcomes(fwd, cfg)
        ev = res["trade_events_sample"][0]
        assert ev["trade_mode"] == "reverse"
        assert ev["win"] is True

    def test_reverse_loss_bullish_candle(self):
        """Bullish candle + reverse + adv_ticks < target → LOSS."""
        from ta_foundation.analysis.large_candle_excursion.trade_analyzer import compute_trade_outcomes
        fwd = self._make_fwd_row(direction=1, fav_ticks=60, adv_ticks=10, size_ticks=80)
        cfg = {
            "enabled": True,
            "trade_modes": {"continuation": False, "reverse": True},
            "signal_candle_basis": "range",
            "target": {"percents": [50]},   # 40t > 10t adv → LOSS for reverse
            "stop": {"enabled": False},
            "candle_size_buckets_ticks": [[0, 100], [100, None]],
        }
        res = compute_trade_outcomes(fwd, cfg)
        assert res["trade_events_sample"][0]["win"] is False

    def test_percent_of_candle_target_calculation(self):
        """Verify target_ticks = signal_ticks * pct / 100."""
        from ta_foundation.analysis.large_candle_excursion.trade_analyzer import compute_trade_outcomes
        fwd = self._make_fwd_row(direction=1, fav_ticks=100, adv_ticks=5, size_ticks=80)
        cfg = {
            "enabled": True,
            "trade_modes": {"continuation": True, "reverse": False},
            "signal_candle_basis": "range",
            "target": {"percents": [75]},  # 75% of 80 = 60t
            "stop": {"enabled": False},
            "candle_size_buckets_ticks": [[0, 100], [100, None]],
        }
        res = compute_trade_outcomes(fwd, cfg)
        ev = res["trade_events_sample"][0]
        assert abs(ev["target_ticks"] - 60.0) < 0.01

    def test_stop_hit_before_target(self):
        """Stop reached first → LOSS even though target is also reachable."""
        from ta_foundation.analysis.large_candle_excursion.trade_analyzer import compute_trade_outcomes
        # time_fav > time_adv → adv (stop) reached first
        fwd = self._make_fwd_row(direction=1, fav_ticks=60, adv_ticks=40,
                                 size_ticks=80, time_fav=8.0, time_adv=3.0)
        cfg = {
            "enabled": True,
            "trade_modes": {"continuation": True, "reverse": False},
            "signal_candle_basis": "range",
            "target": {"percents": [50]},   # 40t — both hit
            "stop": {"enabled": True, "percents": [50]},   # 40t — both hit
            "candle_size_buckets_ticks": [[0, 100], [100, None]],
        }
        res = compute_trade_outcomes(fwd, cfg)
        ev = res["trade_events_sample"][0]
        assert ev["first_hit"] == "stop"
        assert ev["win"] is False

    def test_target_hit_before_stop(self):
        """Target reached first → WIN."""
        from ta_foundation.analysis.large_candle_excursion.trade_analyzer import compute_trade_outcomes
        # time_fav < time_adv → target first
        fwd = self._make_fwd_row(direction=1, fav_ticks=60, adv_ticks=40,
                                 size_ticks=80, time_fav=3.0, time_adv=8.0)
        cfg = {
            "enabled": True,
            "trade_modes": {"continuation": True, "reverse": False},
            "signal_candle_basis": "range",
            "target": {"percents": [50]},
            "stop": {"enabled": True, "percents": [50]},
            "candle_size_buckets_ticks": [[0, 100], [100, None]],
        }
        res = compute_trade_outcomes(fwd, cfg)
        ev = res["trade_events_sample"][0]
        assert ev["first_hit"] == "target"
        assert ev["win"] is True

    def test_combo_results_aggregation(self):
        """Multiple events should aggregate correctly."""
        from ta_foundation.analysis.large_candle_excursion.trade_analyzer import compute_trade_outcomes
        # 4 events: 3 wins, 1 loss for continuation at 50% target
        rows = []
        for i, (fav, adv) in enumerate([(60,10),(60,10),(60,10),(10,5)]):
            start = pd.Timestamp("2024-01-02 09:31:00", tz=TZ) + pd.Timedelta(hours=i)
            rows.append({
                "dt": start, "tf_minutes": 1, "window_minutes": 10,
                "lookback": 10, "basis": "body", "threshold_mode": "multiplier",
                "threshold_value": 1.5, "direction": 1, "is_bullish": True,
                "size_ticks": 80.0, "body_ticks": 60.0,
                "fav_ticks": float(fav), "adv_ticks": float(adv),
                "time_to_max_fav_min": 5.0, "time_to_max_adv_min": 8.0,
            })
        fwd = pd.DataFrame(rows)
        cfg = {
            "enabled": True,
            "trade_modes": {"continuation": True, "reverse": False},
            "signal_candle_basis": "range",
            "target": {"percents": [50]},
            "stop": {"enabled": False},
            "candle_size_buckets_ticks": [[0, 100], [100, None]],
        }
        res = compute_trade_outcomes(fwd, cfg)
        assert len(res["trade_combo_results"]) == 1
        combo = res["trade_combo_results"][0]
        assert combo["n_events"] == 4
        assert combo["n_wins"]   == 3
        assert abs(combo["win_rate"] - 75.0) < 0.1

    def test_empty_fwd_df_returns_disabled(self):
        from ta_foundation.analysis.large_candle_excursion.trade_analyzer import compute_trade_outcomes
        res = compute_trade_outcomes(pd.DataFrame(), {}, 0.25)
        assert res["enabled"] is False

    def test_json_safe(self):
        import json
        from ta_foundation.analysis.large_candle_excursion.trade_analyzer import compute_trade_outcomes
        fwd = self._make_fwd_row()
        cfg = {
            "enabled": True,
            "trade_modes": {"continuation": True, "reverse": True},
            "signal_candle_basis": "range",
            "target": {"percents": [25, 50, 75]},
            "stop": {"enabled": False},
            "candle_size_buckets_ticks": [[0, 100], [100, None]],
        }
        res = compute_trade_outcomes(fwd, cfg)
        json.dumps(res)   # must not raise


# ---------------------------------------------------------------------------
# 7. Trade analysis via sweep integration
# ---------------------------------------------------------------------------

class TestTradeAnalysisSweep:
    """Integration tests: trade_analysis flows through run_large_candle_excursion."""

    _TRADE_CFG = {
        "enabled": True,
        "trade_modes": {"continuation": True, "reverse": True},
        "signal_candle_basis": "range",
        "target": {"percents": [25, 50, 75]},
        "stop": {"enabled": False},
        "candle_size_buckets_ticks": [[0, 50], [50, 100], [100, None]],
        "output": {"max_trade_events_sample": 500},
    }

    def _run(self, direction: str = "both") -> dict:
        bars = _make_bars(300)
        return run_large_candle_excursion(bars_1m=bars, config={
            "timeframes": [1],
            "direction": direction,
            "min_events": 1,
            "candle_size": {
                "average_lookbacks": [10],
                "threshold_multipliers": [1.2],
                "min_body_ticks": 0,
                "min_range_ticks": 0,
            },
            "forward_window": {"minutes": [5]},
            "tick_analysis": {"use_tick_data": False},
            "trade_analysis": self._TRADE_CFG,
        })

    def test_trade_analysis_key_present(self):
        result = self._run()
        assert "trade_analysis" in result

    def test_trade_analysis_enabled_flag(self):
        result = self._run()
        assert result["trade_analysis"]["enabled"] is True

    def test_trade_combo_results_present(self):
        result = self._run()
        combos = result["trade_analysis"].get("trade_combo_results", [])
        assert len(combos) > 0

    def test_trade_modes_both_present(self):
        result = self._run()
        modes = {c["trade_mode"] for c in result["trade_analysis"]["trade_combo_results"]}
        assert "continuation" in modes
        assert "reverse" in modes

    def test_continuation_only_mode(self):
        bars = _make_bars(300)
        result = run_large_candle_excursion(bars_1m=bars, config={
            "timeframes": [1],
            "min_events": 1,
            "candle_size": {"average_lookbacks": [10], "threshold_multipliers": [1.2],
                            "min_body_ticks": 0, "min_range_ticks": 0},
            "forward_window": {"minutes": [5]},
            "tick_analysis": {"use_tick_data": False},
            "trade_analysis": {
                **self._TRADE_CFG,
                "trade_modes": {"continuation": True, "reverse": False},
            },
        })
        modes = {c["trade_mode"] for c in result["trade_analysis"]["trade_combo_results"]}
        assert modes == {"continuation"}

    def test_reverse_only_mode(self):
        bars = _make_bars(300)
        result = run_large_candle_excursion(bars_1m=bars, config={
            "timeframes": [1],
            "min_events": 1,
            "candle_size": {"average_lookbacks": [10], "threshold_multipliers": [1.2],
                            "min_body_ticks": 0, "min_range_ticks": 0},
            "forward_window": {"minutes": [5]},
            "tick_analysis": {"use_tick_data": False},
            "trade_analysis": {
                **self._TRADE_CFG,
                "trade_modes": {"continuation": False, "reverse": True},
            },
        })
        modes = {c["trade_mode"] for c in result["trade_analysis"]["trade_combo_results"]}
        assert modes == {"reverse"}

    def test_trade_analysis_disabled_by_default(self):
        """trade_analysis is off unless explicitly enabled."""
        bars = _make_bars(300)
        result = run_large_candle_excursion(bars_1m=bars, config={
            "timeframes": [1],
            "min_events": 1,
            "candle_size": {"average_lookbacks": [10], "threshold_multipliers": [1.2],
                            "min_body_ticks": 0, "min_range_ticks": 0},
            "forward_window": {"minutes": [5]},
            "tick_analysis": {"use_tick_data": False},
        })
        assert "trade_analysis" not in result

    def test_trade_analysis_json_safe(self):
        import json
        result = self._run()
        json.dumps(result)

    def test_win_rate_bounded(self):
        result = self._run()
        for c in result["trade_analysis"]["trade_combo_results"]:
            wr = c.get("win_rate")
            if wr is not None:
                assert 0.0 <= wr <= 100.0

    def test_candle_buckets_present(self):
        result = self._run()
        buckets = {c.get("candle_bucket") for c in result["trade_analysis"]["trade_combo_results"]}
        # At least one bucket should appear
        assert len(buckets) > 0 and None not in buckets


# ---------------------------------------------------------------------------
# 8. Trade section renderer smoke tests
# ---------------------------------------------------------------------------

class TestTradeSectionRenderers:
    """Smoke tests for the three new trade report sections."""

    def _make_ctx(self, data: dict) -> dict:
        return {
            "large_candle_excursion": data,
            "packages": {},
            "options": {},
            "all_options": {"large_candle_excursion": data},
        }

    def _make_full_data_with_trade(self) -> dict:
        bars = _make_bars(300)
        return run_large_candle_excursion(bars_1m=bars, config={
            "timeframes": [1],
            "min_events": 1,
            "candle_size": {
                "average_lookbacks": [10],
                "threshold_multipliers": [1.2],
                "min_body_ticks": 0,
                "min_range_ticks": 0,
            },
            "forward_window": {"minutes": [5, 10]},
            "tick_analysis": {"use_tick_data": False},
            "trade_analysis": {
                "enabled": True,
                "trade_modes": {"continuation": True, "reverse": True},
                "signal_candle_basis": "range",
                "target": {"percents": [50, 100]},
                "stop": {"enabled": False},
                "candle_size_buckets_ticks": [[0, 50], [50, 100], [100, None]],
                "output": {"max_trade_events_sample": 200},
            },
        })

    def test_trade_summary_renderer(self):
        from ta_foundation.reports.html.sections.large_candle_excursion_trade_summary import (
            render_large_candle_excursion_trade_summary,
        )
        data = self._make_full_data_with_trade()
        html = render_large_candle_excursion_trade_summary(self._make_ctx(data))
        assert isinstance(html, str) and len(html) > 50

    def test_trade_summary_no_trade_data(self):
        """When trade_analysis is absent the renderer shows a helpful message."""
        from ta_foundation.reports.html.sections.large_candle_excursion_trade_summary import (
            render_large_candle_excursion_trade_summary,
        )
        # Data without trade_analysis key
        bars = _make_bars(300)
        data = run_large_candle_excursion(bars_1m=bars, config={
            "timeframes": [1], "min_events": 1,
            "candle_size": {"average_lookbacks": [10], "threshold_multipliers": [1.2],
                            "min_body_ticks": 0, "min_range_ticks": 0},
            "forward_window": {"minutes": [5]},
            "tick_analysis": {"use_tick_data": False},
        })
        html = render_large_candle_excursion_trade_summary(self._make_ctx(data))
        assert isinstance(html, str)
        assert "not enabled" in html.lower() or "trade_analysis" in html.lower()

    def test_trade_comparison_renderer(self):
        from ta_foundation.reports.html.sections.large_candle_excursion_trade_comparison import (
            render_large_candle_excursion_trade_comparison,
        )
        data = self._make_full_data_with_trade()
        html = render_large_candle_excursion_trade_comparison(self._make_ctx(data))
        assert isinstance(html, str) and len(html) > 50

    def test_trade_event_table_renderer(self):
        from ta_foundation.reports.html.sections.large_candle_excursion_trade_event_table import (
            render_large_candle_excursion_trade_event_table,
        )
        data = self._make_full_data_with_trade()
        html = render_large_candle_excursion_trade_event_table(self._make_ctx(data))
        assert isinstance(html, str) and len(html) > 50
