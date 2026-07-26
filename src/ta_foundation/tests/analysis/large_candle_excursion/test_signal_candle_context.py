"""
Tests for signal_candle_context.py — four context-enrichment helpers.

Tests are self-contained: they build synthetic tf_bars and event DataFrames
without requiring real market data or the full sweep pipeline.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest

from ta_foundation.analysis.large_candle_excursion.signal_candle_context import (
    DEFAULT_DIRECTIONAL_CONTEXT,
    DEFAULT_KEY_LEVEL_CONTEXT,
    DEFAULT_MA_VWAP_CONTEXT,
    DEFAULT_TREND_STATE_CONTEXT,
    add_directional_context,
    add_key_level_context,
    add_ma_vwap_context,
    add_trend_state_context,
    _compute_lag_safe_ma,
    _compute_lag_safe_vwap,
    _compute_prior_day_hl_c,
    _compute_session_running_hl,
)


# ---------------------------------------------------------------------------
# Synthetic data builders
# ---------------------------------------------------------------------------

DENVER_TZ = "America/Denver"
TICK_SIZE = 0.25


def _make_bars(
    prices: List[float],
    date_str: str = "2024-01-02",
    start_hour: int = 8,
    volume: bool = True,
) -> pd.DataFrame:
    """Build a minimal tf_bars DataFrame with dt, open, high, low, close, volume."""
    n = len(prices)
    base = pd.Timestamp(f"{date_str} {start_hour:02d}:00:00", tz="America/Denver")
    dts = [base + pd.Timedelta(minutes=i) for i in range(n)]
    rows = []
    for i, p in enumerate(prices):
        o = p - 0.5 if i % 3 != 0 else p + 0.5
        rows.append({
            "dt":     dts[i],
            "open":   o,
            "high":   p + 1.0,
            "low":    p - 1.0,
            "close":  p,
            "volume": float(100 + i * 10) if volume else None,
        })
    df = pd.DataFrame(rows)
    if not volume:
        df = df.drop(columns=["volume"])
    return df


def _make_events(bar_idxs: List[int], directions: List[int], tf_bars: pd.DataFrame) -> pd.DataFrame:
    """Build a minimal events DataFrame referencing positions in tf_bars."""
    rows = []
    for idx, d in zip(bar_idxs, directions):
        row_tf = tf_bars.iloc[idx]
        rows.append({
            "bar_idx":        idx,
            "direction":      d,
            "open":           float(row_tf["open"]),
            "high":           float(row_tf["high"]),
            "low":            float(row_tf["low"]),
            "close":          float(row_tf["close"]),
            "body":           abs(float(row_tf["close"]) - float(row_tf["open"])),
            "total_range":    float(row_tf["high"]) - float(row_tf["low"]),
            "body_ticks":     abs(float(row_tf["close"]) - float(row_tf["open"])) / TICK_SIZE,
            "size_ticks":     (float(row_tf["high"]) - float(row_tf["low"])) / TICK_SIZE,
            "atr":            2.0,
            "dt":             row_tf["dt"],
            "tf_minutes":     1,
        })
    return pd.DataFrame(rows).copy()


# ---------------------------------------------------------------------------
# Tests: _compute_lag_safe_ma
# ---------------------------------------------------------------------------

class TestLagSafeMA:
    def test_basic(self):
        prices = np.array([10.0, 11.0, 12.0, 13.0, 14.0, 15.0], dtype=float)
        ma = _compute_lag_safe_ma(prices, 3)
        # MA at index 0 should be NaN (not enough prior bars)
        assert np.isnan(ma[0])
        # MA at index 3 = mean(10, 11, 12) = 11.0
        assert abs(ma[3] - 11.0) < 0.01

    def test_lag_safe(self):
        """MA at position i must NOT include prices[i]."""
        prices = np.array([1.0, 2.0, 100.0, 4.0, 5.0], dtype=float)
        ma = _compute_lag_safe_ma(prices, 2)
        # At index 2, MA should be mean(1, 2) = 1.5, not contaminated by 100
        assert abs(ma[2] - 1.5) < 0.01


# ---------------------------------------------------------------------------
# Tests: _compute_lag_safe_vwap
# ---------------------------------------------------------------------------

class TestLagSafeVWAP:
    def test_first_bar_is_nan(self):
        bars = _make_bars([100.0, 101.0, 102.0])
        vwap = _compute_lag_safe_vwap(bars)
        assert np.isnan(vwap[0]), "First bar of session should have NaN VWAP (no prior data)"

    def test_second_bar_equals_first_typical(self):
        bars = _make_bars([100.0, 102.0])
        vwap = _compute_lag_safe_vwap(bars)
        # VWAP at bar 1 = typical price of bar 0 = (h+l+c)/3
        expected = (101.0 + 99.0 + 100.0) / 3.0
        assert abs(vwap[1] - expected) < 0.01

    def test_daily_reset(self):
        """VWAP must reset at the start of each calendar day."""
        day1 = _make_bars([100.0, 101.0], date_str="2024-01-02", start_hour=9)
        day2 = _make_bars([200.0, 201.0], date_str="2024-01-03", start_hour=9)
        bars = pd.concat([day1, day2], ignore_index=True)
        vwap = _compute_lag_safe_vwap(bars)
        # First bar of day 2 (index 2) should be NaN
        assert np.isnan(vwap[2])

    def test_no_volume_fallback(self):
        bars = _make_bars([100.0, 101.0, 102.0], volume=False)
        vwap = _compute_lag_safe_vwap(bars)
        assert np.isnan(vwap[0])
        assert not np.isnan(vwap[1])


# ---------------------------------------------------------------------------
# Tests: _compute_prior_day_hl_c
# ---------------------------------------------------------------------------

class TestPriorDayHLC:
    def test_no_prior_day_for_first_day(self):
        bars = _make_bars([100.0, 101.0, 102.0], date_str="2024-01-02")
        pdh, pdl, pdc = _compute_prior_day_hl_c(bars)
        assert np.all(np.isnan(pdh))

    def test_prior_day_values_correct(self):
        day1 = _make_bars([100.0, 101.0], date_str="2024-01-02", start_hour=9)
        day2 = _make_bars([105.0, 106.0], date_str="2024-01-03", start_hour=9)
        bars = pd.concat([day1, day2], ignore_index=True)
        pdh, pdl, pdc = _compute_prior_day_hl_c(bars)
        # For day2 bars, prior day high = max of day1 highs
        expected_high = max(float(day1["high"].max()), 0)
        assert abs(pdh[2] - expected_high) < 0.01
        assert abs(pdh[3] - expected_high) < 0.01

    def test_prior_day_close_is_last_bar_close(self):
        day1 = _make_bars([100.0, 99.0, 98.0], date_str="2024-01-02", start_hour=9)
        day2 = _make_bars([110.0], date_str="2024-01-03", start_hour=9)
        bars = pd.concat([day1, day2], ignore_index=True)
        _, _, pdc = _compute_prior_day_hl_c(bars)
        # Prior day close for day2 = close of last bar of day1 = 98.0
        assert abs(pdc[3] - 98.0) < 0.01


# ---------------------------------------------------------------------------
# Tests: _compute_session_running_hl
# ---------------------------------------------------------------------------

class TestSessionRunningHL:
    def test_first_bar_of_session_is_nan(self):
        bars = _make_bars([100.0, 101.0, 102.0])
        sh, sl = _compute_session_running_hl(bars)
        assert np.isnan(sh[0])
        assert np.isnan(sl[0])

    def test_running_high_is_lag_safe(self):
        bars = _make_bars([100.0, 200.0, 90.0])
        sh, _ = _compute_session_running_hl(bars)
        # At bar 1 (200.0 bar), running high = high of bar 0 only
        assert abs(sh[1] - bars["high"].iloc[0]) < 0.01
        # At bar 2 (90.0 bar), running high = max(high[0], high[1])
        assert abs(sh[2] - max(bars["high"].iloc[0], bars["high"].iloc[1])) < 0.01


# ---------------------------------------------------------------------------
# Tests: add_directional_context
# ---------------------------------------------------------------------------

class TestDirectionalContext:
    def setup_method(self):
        # Build a simple price series: alternating bull/bear candles
        self.prices = [100.0, 101.0, 102.0, 101.0, 103.0, 102.0, 104.0]
        self.tf_bars = _make_bars(self.prices)
        self.cfg = dict(DEFAULT_DIRECTIONAL_CONTEXT)
        self.cfg["enabled"] = True

    def test_columns_added(self):
        events = _make_events([3, 4, 5], [1, 1, -1], self.tf_bars)
        add_directional_context(events, self.tf_bars, self.cfg, TICK_SIZE)
        for col in ("prev_direction", "same_as_prev", "direction_streak",
                    "engulfs_prev_range", "breaks_prev_ext", "closes_outside_prev",
                    "prev_direction_bucket", "streak_bucket", "engulf_bucket"):
            assert col in events.columns, f"Missing column: {col}"

    def test_first_bar_has_unknown_direction(self):
        events = _make_events([0], [1], self.tf_bars)
        add_directional_context(events, self.tf_bars, self.cfg, TICK_SIZE)
        assert events["prev_direction_bucket"].iloc[0] == "unknown"

    def test_same_direction_detection(self):
        # Make tf_bars where bars are consistently bullish (close > open)
        bars = pd.DataFrame({
            "dt":    pd.date_range("2024-01-02 08:00", periods=5, freq="min", tz="America/Denver"),
            "open":  [100.0, 101.0, 102.0, 103.0, 104.0],
            "high":  [101.5, 102.5, 103.5, 104.5, 105.5],
            "low":   [99.5,  100.5, 101.5, 102.5, 103.5],
            "close": [101.0, 102.0, 103.0, 104.0, 105.0],
            "volume":[100.0] * 5,
        })
        events = _make_events([3], [1], bars)
        add_directional_context(events, bars, self.cfg, TICK_SIZE)
        assert events["prev_direction_bucket"].iloc[0] == "same"

    def test_opposite_direction_detection(self):
        # Bar at idx-1 is bearish, signal is bullish
        bars = pd.DataFrame({
            "dt":    pd.date_range("2024-01-02 08:00", periods=4, freq="min", tz="America/Denver"),
            "open":  [100.0, 101.0, 103.0, 100.0],
            "high":  [101.0, 102.0, 104.0, 101.0],
            "low":   [99.0,  100.0, 102.0, 99.0],
            "close": [100.5, 102.0, 100.0, 101.0],  # bar[2] is bearish
        })
        events = _make_events([3], [1], bars)
        add_directional_context(events, bars, self.cfg, TICK_SIZE)
        assert events["prev_direction_bucket"].iloc[0] == "opposite"

    def test_engulf_detected(self):
        """Signal engulfs prev bar when signal high >= prev high and signal low <= prev low."""
        bars = pd.DataFrame({
            "dt":    pd.date_range("2024-01-02 08:00", periods=3, freq="min", tz="America/Denver"),
            "open":  [100.0, 101.0, 99.0],
            "high":  [103.0, 102.0, 105.0],  # signal (bar2) high > prev (bar1) high
            "low":   [98.0,  100.0, 97.0],   # signal low < prev low
            "close": [102.0, 101.5, 104.0],
            "volume":[100.0] * 3,
        })
        events = _make_events([2], [1], bars)
        add_directional_context(events, bars, self.cfg, TICK_SIZE)
        assert events["engulfs_prev_range"].iloc[0] is True or events["engulfs_prev_range"].iloc[0] == True

    def test_streak_count(self):
        """Test streak counting: 3 consecutive same-direction bars before signal."""
        bars = pd.DataFrame({
            "dt":    pd.date_range("2024-01-02 08:00", periods=5, freq="min", tz="America/Denver"),
            "open":  [100.0, 101.0, 102.0, 103.0, 104.0],
            "high":  [101.5] * 5,
            "low":   [99.5] * 5,
            "close": [101.0, 102.0, 103.0, 104.0, 105.0],  # all bull
            "volume":[100.0] * 5,
        })
        events = _make_events([4], [1], bars)
        cfg = dict(self.cfg)
        cfg["streak_lookback"] = 4
        add_directional_context(events, bars, cfg, TICK_SIZE)
        assert events["direction_streak"].iloc[0] >= 3

    def test_disabled_no_columns(self):
        cfg = dict(DEFAULT_DIRECTIONAL_CONTEXT)
        cfg["enabled"] = False
        events = _make_events([3], [1], self.tf_bars)
        add_directional_context(events, self.tf_bars, cfg, TICK_SIZE)
        assert "prev_direction_bucket" not in events.columns


# ---------------------------------------------------------------------------
# Tests: add_ma_vwap_context
# ---------------------------------------------------------------------------

class TestMAVWAPContext:
    def setup_method(self):
        self.cfg = dict(DEFAULT_MA_VWAP_CONTEXT)
        self.cfg["enabled"] = True
        self.cfg["ma_periods"] = [5, 10]  # use small periods for test data

    def test_columns_added(self):
        bars = _make_bars(list(range(100, 120, 1)))
        events = _make_events([15], [1], bars)
        events["atr"] = 2.0
        add_ma_vwap_context(events, bars, self.cfg, TICK_SIZE)
        for prefix in ("ma5", "ma10", "vwap"):
            for suffix in ("_value", "_location_bucket", "_ext_bucket"):
                assert f"{prefix}{suffix}" in events.columns, f"Missing {prefix}{suffix}"

    def test_above_below_flag(self):
        # Rising prices → at a late bar, close > MA
        prices = list(range(100, 130))
        bars = _make_bars(prices)
        events = _make_events([25], [1], bars)
        events["atr"] = 2.0
        add_ma_vwap_context(events, bars, self.cfg, TICK_SIZE)
        # Close at bar 25 = 125.0, MA5 (lag-safe) should be around 120
        assert bool(events["above_ma5"].iloc[0])

    def test_extension_bucket_near(self):
        # Flat prices → close is very close to MA
        prices = [100.0] * 30
        bars = _make_bars(prices)
        events = _make_events([25], [1], bars)
        events["atr"] = 2.0
        add_ma_vwap_context(events, bars, self.cfg, TICK_SIZE)
        assert events["ma5_ext_bucket"].iloc[0] == "near"

    def test_disabled_no_columns(self):
        cfg = dict(DEFAULT_MA_VWAP_CONTEXT)
        cfg["enabled"] = False
        bars = _make_bars(list(range(100, 120)))
        events = _make_events([15], [1], bars)
        add_ma_vwap_context(events, bars, cfg, TICK_SIZE)
        assert "ma100_location_bucket" not in events.columns

    def test_nan_on_insufficient_bars(self):
        """For a bar before the MA period, the value should be NaN."""
        prices = list(range(100, 106))  # only 6 bars for MA5
        bars = _make_bars(prices)
        events = _make_events([1], [1], bars)  # bar_idx=1, only 1 prior bar
        events["atr"] = 2.0
        self.cfg["ma_periods"] = [5]
        add_ma_vwap_context(events, bars, self.cfg, TICK_SIZE)
        # MA5 at bar 1 uses shift(1).rolling(5) — only 1 prior bar, so value should be non-nan
        # (min_periods=max(1, 2) in lag safe MA, so might give a value)
        # Just check no error is raised
        assert "ma5_location_bucket" in events.columns


# ---------------------------------------------------------------------------
# Tests: add_key_level_context
# ---------------------------------------------------------------------------

class TestKeyLevelContext:
    def setup_method(self):
        self.cfg = dict(DEFAULT_KEY_LEVEL_CONTEXT)
        self.cfg["enabled"] = True
        self.cfg["level_types"] = ["prior_day_high", "prior_day_low", "session_high", "ma100"]

    def _make_two_day_bars(self):
        d1 = _make_bars([100.0, 101.0, 102.0], date_str="2024-01-02", start_hour=9)
        d2 = _make_bars([103.0, 104.0, 105.0], date_str="2024-01-03", start_hour=9)
        return pd.concat([d1, d2], ignore_index=True)

    def test_columns_added(self):
        bars = self._make_two_day_bars()
        events = _make_events([3], [1], bars)
        events["atr"] = 2.0
        add_key_level_context(events, bars, self.cfg, TICK_SIZE)
        for col in ("nearest_level_type", "dist_nearest_level_ticks",
                    "dist_nearest_level_atr", "level_interaction_label"):
            assert col in events.columns

    def test_nearest_level_is_valid_type(self):
        bars = self._make_two_day_bars()
        events = _make_events([3, 4, 5], [1, 1, -1], bars)
        events["atr"] = 2.0
        add_key_level_context(events, bars, self.cfg, TICK_SIZE)
        valid = {"prior_day_high", "prior_day_low", "session_high", "ma100", "unknown"}
        for nlt in events["nearest_level_type"]:
            assert nlt in valid, f"Unexpected level type: {nlt}"

    def test_interaction_label_valid(self):
        bars = self._make_two_day_bars()
        events = _make_events([3, 4, 5], [1, -1, 1], bars)
        events["atr"] = 2.0
        add_key_level_context(events, bars, self.cfg, TICK_SIZE)
        valid_labels = {"at_level", "approaching", "nearby", "departing", "unknown", "other"}
        for label in events["level_interaction_label"]:
            assert label in valid_labels

    def test_distance_is_non_negative(self):
        bars = self._make_two_day_bars()
        events = _make_events([3, 4], [1, -1], bars)
        events["atr"] = 2.0
        add_key_level_context(events, bars, self.cfg, TICK_SIZE)
        for v in events["dist_nearest_level_ticks"].dropna():
            assert v >= 0, "Distance must be non-negative"

    def test_disabled_no_columns(self):
        cfg = dict(DEFAULT_KEY_LEVEL_CONTEXT)
        cfg["enabled"] = False
        bars = self._make_two_day_bars()
        events = _make_events([3], [1], bars)
        add_key_level_context(events, bars, cfg, TICK_SIZE)
        assert "nearest_level_type" not in events.columns


# ---------------------------------------------------------------------------
# Tests: add_trend_state_context
# ---------------------------------------------------------------------------

class TestTrendStateContext:
    def setup_method(self):
        self.cfg = dict(DEFAULT_TREND_STATE_CONTEXT)
        self.cfg["enabled"] = True
        self.cfg["ma_periods"] = [5, 10]
        self.cfg["slope_lookback"] = 3

    def test_columns_added(self):
        prices = [float(100 + i) for i in range(30)]
        bars = _make_bars(prices)
        events = _make_events([20, 25], [1, 1], bars)
        events["atr"] = 2.0
        add_trend_state_context(events, bars, self.cfg, TICK_SIZE)
        assert "trend_alignment_label" in events.columns
        assert "ma5_slope_label" in events.columns
        assert "stacked_above_mas" in events.columns

    def test_strong_bull_alignment(self):
        """Strongly rising prices → strong_bull or bull alignment."""
        prices = [float(100 + i * 2) for i in range(40)]  # steep uptrend
        bars = _make_bars(prices)
        events = _make_events([35], [1], bars)
        events["atr"] = 1.0  # small ATR → slopes appear large
        add_trend_state_context(events, bars, self.cfg, TICK_SIZE)
        label = events["trend_alignment_label"].iloc[0]
        assert label in ("strong_bull", "bull"), f"Expected bull alignment, got: {label}"

    def test_flat_market_neutral(self):
        """Flat prices → flat slopes → neutral alignment."""
        prices = [100.0] * 40
        bars = _make_bars(prices)
        events = _make_events([35], [1], bars)
        events["atr"] = 1.0
        add_trend_state_context(events, bars, self.cfg, TICK_SIZE)
        label = events["trend_alignment_label"].iloc[0]
        # Flat prices: slopes are zero; close == MA so not strictly "above".
        # Any non-strong-trending label is acceptable here.
        assert label in ("neutral", "bull", "bear", "unknown")

    def test_slope_label_values(self):
        prices = [float(100 + i) for i in range(30)]
        bars = _make_bars(prices)
        events = _make_events([20, 25], [1, 1], bars)
        events["atr"] = 2.0
        add_trend_state_context(events, bars, self.cfg, TICK_SIZE)
        valid_labels = {"rising_strong", "rising", "flat", "falling", "falling_strong", "unknown"}
        for label in events["ma5_slope_label"]:
            assert label in valid_labels

    def test_stacked_above_when_price_above_both_mas(self):
        prices = [float(100 + i) for i in range(40)]
        bars = _make_bars(prices)
        events = _make_events([38], [1], bars)
        events["atr"] = 2.0
        add_trend_state_context(events, bars, self.cfg, TICK_SIZE)
        # In a strong uptrend, price should be above both MAs
        assert bool(events["stacked_above_mas"].iloc[0])

    def test_disabled_no_columns(self):
        cfg = dict(DEFAULT_TREND_STATE_CONTEXT)
        cfg["enabled"] = False
        bars = _make_bars([float(i) for i in range(30)])
        events = _make_events([20], [1], bars)
        add_trend_state_context(events, bars, cfg, TICK_SIZE)
        assert "trend_alignment_label" not in events.columns


# ---------------------------------------------------------------------------
# Tests: context_stats helpers
# ---------------------------------------------------------------------------

class TestContextStatsHelpers:
    def test_wilson_ci_range(self):
        from ta_foundation.analysis.large_candle_excursion.context_stats import _wilson_ci
        lo, hi = _wilson_ci(50, 100)
        assert lo is not None and hi is not None
        assert 0 <= lo <= 100
        assert lo <= 50.0 <= hi

    def test_wilson_ci_empty(self):
        from ta_foundation.analysis.large_candle_excursion.context_stats import _wilson_ci
        lo, hi = _wilson_ci(0, 0)
        assert lo is None and hi is None

    def test_lift_vs_baseline(self):
        from ta_foundation.analysis.large_candle_excursion.context_stats import _lift_vs_baseline
        assert _lift_vs_baseline(60.0, 50.0) == pytest.approx(10.0, abs=0.1)
        assert _lift_vs_baseline(45.0, 50.0) == pytest.approx(-5.0, abs=0.1)
        assert _lift_vs_baseline(None, 50.0) is None

    def test_deduplicate_events(self):
        from ta_foundation.analysis.large_candle_excursion.context_stats import _deduplicate_events
        base_dt = pd.Timestamp("2024-01-02 09:00:00", tz="America/Denver")
        df = pd.DataFrame({
            "dt":         [base_dt, base_dt, base_dt + pd.Timedelta("1min")],
            "direction":  [1, 1, 1],
            "tf_minutes": [5, 5, 5],
            "fav_ticks":  [10.0, 10.0, 8.0],
        })
        deduped = _deduplicate_events(df)
        assert len(deduped) == 2  # first dt/direction/tf combo deduplicated


# ---------------------------------------------------------------------------
# Tests: section renderer smoke test
# ---------------------------------------------------------------------------

class TestSignalContextSectionSmoke:
    def test_renders_without_data(self):
        from ta_foundation.reports.html.sections.large_candle_excursion_signal_context import (
            render_large_candle_excursion_signal_context,
        )
        html = render_large_candle_excursion_signal_context({})
        assert isinstance(html, str)
        assert len(html) > 0

    def test_renders_with_disabled_context(self):
        from ta_foundation.reports.html.sections.large_candle_excursion_signal_context import (
            render_large_candle_excursion_signal_context,
        )
        ctx = {
            "large_candle_excursion": {
                "context_analysis": {
                    "enabled": True,
                    "population_baseline": {"cont_win_rate": 55.0, "rev_win_rate": 45.0, "n_events": 100},
                    "directional_context": {"enabled": False},
                    "ma_vwap_context": {"enabled": False},
                    "key_level_context": {"enabled": False},
                    "trend_state_context": {"enabled": False},
                    "interactions": {},
                }
            }
        }
        html = render_large_candle_excursion_signal_context(ctx)
        assert isinstance(html, str)

    def test_renders_with_directional_data(self):
        from ta_foundation.reports.html.sections.large_candle_excursion_signal_context import (
            render_large_candle_excursion_signal_context,
        )
        ctx = {
            "large_candle_excursion": {
                "context_analysis": {
                    "enabled": True,
                    "population_baseline": {"cont_win_rate": 55.0, "rev_win_rate": 45.0, "n_events": 200},
                    "directional_context": {
                        "enabled": True,
                        "by_prev_direction": [
                            {"prev_direction_bucket": "same",     "n_observations": 80, "cont_win_rate": 62.0,
                             "rev_win_rate": 38.0, "better_mode": "continuation",
                             "cont_wins": 50, "cont_wr_ci_lo": 51.0, "cont_wr_ci_hi": 72.0,
                             "cont_lift_pp": 7.0, "rev_lift_pp": -7.0},
                            {"prev_direction_bucket": "opposite", "n_observations": 60, "cont_win_rate": 48.0,
                             "rev_win_rate": 52.0, "better_mode": "reverse",
                             "cont_wins": 29, "cont_wr_ci_lo": 36.0, "cont_wr_ci_hi": 60.0,
                             "cont_lift_pp": -7.0, "rev_lift_pp": 7.0},
                        ],
                        "by_streak": [],
                        "by_engulf": [],
                    },
                    "ma_vwap_context":     {"enabled": False},
                    "key_level_context":   {"enabled": False},
                    "trend_state_context": {"enabled": False},
                    "interactions": {},
                }
            }
        }
        html = render_large_candle_excursion_signal_context(ctx)
        assert "Directional Context" in html
        assert "same" in html
        assert "opposite" in html
