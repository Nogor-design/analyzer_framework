from __future__ import annotations

import pandas as pd

from ta_foundation.analysis.entry_strategies.level.signals import (
    detect_failed_reference_breakout,
    detect_large_candle_origin_retest,
    detect_reference_level_sweep_reclaim,
    detect_vwap_continuation,
)


TZ = "America/Denver"


def test_reference_level_sweep_reclaim_high_emits_short() -> None:
    prev = pd.DataFrame(
        {
            "dt": pd.date_range("2026-01-05 07:30", periods=4, freq="1min", tz=TZ),
            "open": [100.0, 100.2, 100.4, 100.6],
            "high": [100.5, 101.0, 100.8, 100.7],
            "low": [99.5, 99.8, 100.0, 100.1],
            "close": [100.2, 100.4, 100.6, 100.3],
            "volume": [100, 100, 100, 100],
        }
    )
    overnight = pd.DataFrame(
        {
            "dt": pd.date_range("2026-01-06 06:00", periods=3, freq="1min", tz=TZ),
            "open": [100.2, 100.1, 100.0],
            "high": [100.4, 100.3, 100.2],
            "low": [99.8, 99.7, 99.6],
            "close": [100.1, 100.0, 99.9],
            "volume": [100, 100, 100],
        }
    )
    signal = pd.DataFrame(
        {
            "dt": [pd.Timestamp("2026-01-06 07:30", tz=TZ)],
            "open": [100.8],
            "high": [101.5],
            "low": [100.0],
            "close": [100.5],
            "volume": [100],
        }
    )
    bars = pd.concat([prev, overnight, signal], ignore_index=True)

    signals = detect_reference_level_sweep_reclaim(
        bars,
        {
            "levels": ["prior_high"],
            "direction": -1,
            "sweep_ticks": 1,
            "close_back_ticks": 1,
            "tick_size": 0.25,
            "min_atr_ticks": 0,
            "rth_start_hour": 7,
            "rth_start_minute": 30,
            "rth_end_hour": 16,
            "rth_end_minute": 0,
        },
    )

    assert len(signals) == 1
    sig = signals.iloc[0]
    assert int(sig["direction"]) == -1
    assert sig["level_type"] == "prior_high_sweep_reclaim"
    assert sig["swept_level_type"] == "prior_high"


def test_vwap_continuation_fires_after_reclaim() -> None:
    # Construct a single session where price opens above VWAP, dips below
    # briefly (cross to short side), reclaims (cross back up), holds for one
    # bar, then prints a strong continuation bar that closes well above VWAP.
    # All bars share equal volume so VWAP ≈ rolling mean of typical price.
    dt = pd.date_range("2026-01-06 07:30", periods=30, freq="1min", tz=TZ)
    closes = [
        100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0,
        100.0, 100.0, 100.0, 100.0, 100.0,
        99.0,    # cross down (close < VWAP ~ 100)
        100.5,   # cross back up — arm long
        100.5,   # hold
        102.5,   # continuation: close - open = +2.0 = 8 ticks
        102.5,
        102.5, 102.5, 102.5, 102.5, 102.5,
        102.5, 102.5, 102.5, 102.5, 102.5,
    ]
    opens = closes.copy()
    opens[18] = 100.5  # continuation bar opens at hold close, closes at 102.5
    highs = [c + 0.25 for c in closes]
    lows = [c - 0.25 for c in closes]
    bars = pd.DataFrame({
        "dt": dt,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [100] * 30,
    })

    signals = detect_vwap_continuation(
        bars,
        {
            "min_continuation_ticks": 4.0,
            "max_age_bars": 6,
            "min_hold_bars": 1,
            "min_dist_ticks": 1.0,
            "max_dist_ticks": 64.0,
            "min_atr_ticks": 0.0,
            "direction": 1,
            "tick_size": 0.25,
            "atr_period": 14,
        },
    )

    assert len(signals) >= 1
    sig = signals.iloc[0]
    assert int(sig["direction"]) == 1
    assert sig["level_type"] == "vwap_continuation_long"


def test_failed_reference_breakout_high_emits_short() -> None:
    # Build a prior RTH day with a clear high near 101, then on the next day
    # have a confirmed breakout above 101, followed by a close back below 101.
    prev = pd.DataFrame({
        "dt": pd.date_range("2026-01-05 07:30", periods=4, freq="1min", tz=TZ),
        "open":  [100.0, 100.2, 100.4, 100.6],
        "high":  [100.5, 101.0, 100.8, 100.7],
        "low":   [99.5,  99.8,  100.0, 100.1],
        "close": [100.2, 100.4, 100.6, 100.3],
        "volume": [100, 100, 100, 100],
    })
    # No overnight gap data needed for prior_high test, but include some to
    # populate the reference-level table.
    overnight = pd.DataFrame({
        "dt": pd.date_range("2026-01-06 06:00", periods=3, freq="1min", tz=TZ),
        "open":  [100.5, 100.5, 100.5],
        "high":  [100.7, 100.7, 100.7],
        "low":   [100.3, 100.3, 100.3],
        "close": [100.5, 100.5, 100.5],
        "volume": [100, 100, 100],
    })
    # Day 2 RTH: bar 0 breaks above 101 (close 101.5), bar 1 fails back below.
    day2 = pd.DataFrame({
        "dt": pd.date_range("2026-01-06 07:30", periods=4, freq="1min", tz=TZ),
        "open":  [101.0, 101.4, 100.4, 100.2],
        "high":  [101.7, 101.6, 101.0, 100.5],
        "low":   [100.8, 100.2, 100.0, 100.0],
        "close": [101.5, 100.3, 100.5, 100.4],
        "volume": [100, 100, 100, 100],
    })
    bars = pd.concat([prev, overnight, day2], ignore_index=True)

    signals = detect_failed_reference_breakout(
        bars,
        {
            "levels": ["prior_high"],
            "confirmation_ticks": 1.0,
            "max_fail_bars": 8,
            "fail_close_ticks": 1.0,
            "direction": -1,
            "min_atr_ticks": 0.0,
            "tick_size": 0.25,
            "rth_start_hour": 7,
            "rth_start_minute": 30,
            "rth_end_hour": 16,
            "rth_end_minute": 0,
        },
    )

    assert len(signals) == 1
    sig = signals.iloc[0]
    assert int(sig["direction"]) == -1
    assert sig["level_type"] == "prior_high_failed_breakout"
    assert sig["broken_level_type"] == "prior_high"
    assert int(sig["breakout_bars_ago"]) == 1


def test_large_candle_origin_retest_emits_long() -> None:
    # 25 quiet bars (body ~ 0.25), then a large bullish candle (body 2.5),
    # then a bar that retests the origin (open of the large candle) and
    # closes back above.
    quiet_n = 25
    quiet_dt = pd.date_range("2026-01-06 07:00", periods=quiet_n, freq="1min", tz=TZ)
    quiet = pd.DataFrame({
        "dt": quiet_dt,
        "open":  [100.0] * quiet_n,
        "high":  [100.25] * quiet_n,
        "low":   [99.75] * quiet_n,
        "close": [100.25] * quiet_n,  # body = 0.25 = 1 tick
        "volume": [100] * quiet_n,
    })
    large_dt = pd.Timestamp("2026-01-06 07:25", tz=TZ)
    large = pd.DataFrame({
        "dt": [large_dt],
        "open":  [100.0],
        "high":  [102.6],
        "low":   [99.9],
        "close": [102.5],   # body = 2.5 → 10 ticks, >> 1.6 * avg ~ 0.4
        "volume": [100],
    })
    retest_dt = pd.date_range("2026-01-06 07:26", periods=2, freq="1min", tz=TZ)
    retest = pd.DataFrame({
        "dt": retest_dt,
        "open":  [102.0, 101.0],
        "high":  [102.0, 101.5],
        "low":   [100.5, 100.0],   # bar 1 touches origin = 100.0
        "close": [101.0, 101.0],   # closes 1.0 above origin = 4 ticks
        "volume": [100, 100],
    })
    bars = pd.concat([quiet, large, retest], ignore_index=True)

    signals = detect_large_candle_origin_retest(
        bars,
        {
            "avg_lookback": 20,
            "large_body_mult": 1.6,
            "min_body_ticks": 6.0,
            "max_retest_bars": 12,
            "touch_ticks": 4.0,
            "min_close_ticks": 2.0,
            "direction": 1,
            "min_atr_ticks": 0.0,
            "tick_size": 0.25,
            "atr_period": 14,
        },
    )

    assert len(signals) == 1
    sig = signals.iloc[0]
    assert int(sig["direction"]) == 1
    assert sig["level_type"] == "large_candle_origin_long"
    assert int(sig["bars_since_origin"]) >= 1
    assert abs(float(sig["level_price"]) - 100.0) < 1e-9
