"""
Offline validation of the C# entry algorithm.

The streaming engine in candle_signal_stream.py is a line-for-line Python mirror
of StrategyDiscoveryFilter.cs (SdfCandleFeatureEngine + ComputeCandleSignal).
This test asserts it fires on exactly the same bars as the VECTORIZED
candle/patterns.py detectors — the discovery ground truth.

If this passes, the entry MATH the C# runs is correct (shift(1) rolling
averages, simple-mean ATR, NaN/fillna semantics). The only thing the live
NinjaTrader parity run then has to confirm is the C#→NT wiring (indicator
hookup, Time/timezone) — not the algorithm.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ta_foundation.analysis.entry_strategies.candle.features import compute_candle_features
from ta_foundation.analysis.entry_strategies.candle.patterns import detect_pattern
from ta_foundation.analysis.strategy_discovery.candle_signal_stream import stream_signals

TICK = 0.25
ATR_PERIOD = 14
LOOKBACK = 20

# Candle structures that exist in the vectorized PATTERN_REGISTRY. n_bar_breakout
# is the breakout family (no vectorized candle detector), so it is not
# cross-checked here.
CANDLE_STRUCTURES = [
    "large_body",
    "pin_bar_bullish",
    "pin_bar_bearish",
    "inside_bar",
    "outside_bar",
    "engulfing_bullish",
    "engulfing_bearish",
    "doji",
    "clean_breakout_bar",
]


def _random_bars(n=800, seed=11):
    rng = np.random.default_rng(seed)
    walk = 20000 + np.cumsum(rng.normal(0, 5, n))
    opens = walk + rng.normal(0, 3, n)
    closes = walk + rng.normal(0, 3, n)
    highs = np.maximum(opens, closes) + np.abs(rng.normal(0, 4, n))
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0, 4, n))
    # Snap to tick grid so size_ticks rounding is deterministic on both sides.
    def snap(a):
        return np.round(a / TICK) * TICK
    idx = pd.date_range("2026-01-05 06:30", periods=n, freq="1min", tz="America/Denver")
    return pd.DataFrame({
        "dt": idx,
        "open": snap(opens),
        "high": snap(highs),
        "low": snap(lows),
        "close": snap(closes),
        "volume": rng.integers(50, 500, n),
    })


def _vectorized_signal_dts(bars, structure):
    cfg = {"tick_size": TICK, "atr_period": ATR_PERIOD, "size_lookbacks": [5, 10, LOOKBACK]}
    enriched = compute_candle_features(bars, cfg)
    sig = detect_pattern(structure, enriched, params={})
    if sig is None or sig.empty:
        return set()
    return set(pd.to_datetime(sig["dt"]).tolist())


def _stream_signal_dts(bars, structure):
    rows = stream_signals(bars, structure, params={}, tick_size=TICK, atr_period=ATR_PERIOD)
    return {dt for dt, _ in rows}


def test_streaming_matches_vectorized_for_every_candle_structure():
    bars = _random_bars()
    total = 0
    for structure in CANDLE_STRUCTURES:
        vec = _vectorized_signal_dts(bars, structure)
        stream = _stream_signal_dts(bars, structure)
        total += len(vec)
        missing = sorted(vec - stream)
        extra = sorted(stream - vec)
        assert not missing and not extra, (
            f"{structure}: streaming twin diverged from vectorized detector. "
            f"missing={missing[:5]} extra={extra[:5]} "
            f"(vec={len(vec)}, stream={len(stream)})"
        )
    # Sanity: the test isn't vacuous — patterns actually fired.
    assert total > 0


def test_engulfing_and_large_body_actually_fire():
    """Guard against a silently-empty corpus masking a parity bug."""
    bars = _random_bars()
    assert _vectorized_signal_dts(bars, "engulfing_bullish")
    assert _vectorized_signal_dts(bars, "large_body")


def test_parity_holds_across_multiple_seeds():
    for seed in (1, 2, 3, 99):
        bars = _random_bars(n=500, seed=seed)
        for structure in CANDLE_STRUCTURES:
            vec = _vectorized_signal_dts(bars, structure)
            stream = _stream_signal_dts(bars, structure)
            assert vec == stream, f"seed={seed} structure={structure} diverged"
