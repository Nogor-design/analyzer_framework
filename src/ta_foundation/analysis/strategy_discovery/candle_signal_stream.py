from __future__ import annotations

"""
Streaming candle-signal engine — a faithful Python mirror of the C#
StrategyDiscoveryFilter.SdfCandleFeatureEngine + ComputeCandleSignal.

Purpose: validate the *algorithm* the C# strategy runs WITHOUT NinjaTrader.
This processes bars one at a time exactly as the C# does (rolling buffers,
shift(1)-style prior-bar averages, simple-mean ATR, fillna(0)/fillna(1)
comparison semantics). A test compares its output to the vectorized
``candle/patterns.py`` detectors (the ground truth). If they agree, the C#
logic — written to this same spec — is correct modulo NinjaTrader wiring.

This is intentionally NOT vectorized: the point is to reproduce the streaming
control flow the C# uses, so a divergence here would surface a streaming bug in
the C# too.

Param names follow ``candle/patterns.py`` (body_multiplier, wick_to_body_max,
lookback, min_size_ticks, ...) because those are exactly the values the C#
StrategyDiscoveryFilter parameters get pinned to.
"""

from collections import deque
from math import isnan
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

NAN = float("nan")


def _nz0(v: float) -> float:
    """fillna(0): NaN compares as 0.0."""
    return 0.0 if isnan(v) else v


def _nz1(v: float) -> float:
    """fillna(1): NaN compares as 1.0."""
    return 1.0 if isnan(v) else v


class StreamingCandleEngine:
    """Mirror of SdfCandleFeatureEngine.Update (one bar at a time)."""

    def __init__(self, tick_size: float, roll_lookback: int, atr_period: int, extreme_lookback: int):
        self.tick_size = tick_size if tick_size > 0 else 0.25
        self.min_body_price = 1.0 * self.tick_size
        self.roll_lookback = max(1, int(roll_lookback))
        self.atr_period = max(1, int(atr_period))
        self.extreme_lookback = max(1, int(extreme_lookback))

        self._body = deque()
        self._range = deque()
        self._tr = deque()
        self._high = deque()
        self._low = deque()

        self._has_prev = False
        self._p_open = self._p_high = self._p_low = self._p_close = self._p_body = 0.0

        # exposed per-bar features
        self.cur_open = self.cur_high = self.cur_low = self.cur_close = 0.0
        self.has_prev = False
        self.body = self.upper_wick = self.lower_wick = self.total_range = 0.0
        self.is_bullish = False
        self.size_ticks = 0.0
        self.body_to_range = NAN
        self.upper_wick_to_body = NAN
        self.lower_wick_to_body = NAN
        self.size_vs_roll = NAN
        self.body_vs_roll = NAN
        self.size_vs_atr = NAN
        self.roll_high = NAN
        self.roll_low = NAN
        self.prev_open = self.prev_high = self.prev_low = self.prev_close = self.prev_body = 0.0

    @staticmethod
    def _mean(dq: deque) -> float:
        return (sum(dq) / len(dq)) if dq else NAN

    @staticmethod
    def _max(dq: deque) -> float:
        return max(dq) if dq else NAN

    @staticmethod
    def _min(dq: deque) -> float:
        return min(dq) if dq else NAN

    def update(self, o: float, h: float, l: float, c: float) -> None:
        self.cur_open, self.cur_high, self.cur_low, self.cur_close = o, h, l, c

        body = abs(c - o)
        max_oc = max(c, o)
        min_oc = min(c, o)
        upper_wick = max(0.0, h - max_oc)
        lower_wick = max(0.0, min_oc - l)
        rng = max(0.0, h - l)

        self.body, self.upper_wick, self.lower_wick, self.total_range = body, upper_wick, lower_wick, rng
        self.is_bullish = c >= o
        self.size_ticks = round(rng / self.tick_size, 2)

        self.body_to_range = min(1.0, max(0.0, body / rng)) if rng > 0 else NAN

        if body >= self.min_body_price:
            self.upper_wick_to_body = upper_wick / body
            self.lower_wick_to_body = lower_wick / body
        else:
            self.upper_wick_to_body = NAN
            self.lower_wick_to_body = NAN

        # ATR — simple mean of TR, current bar included.
        prev_close_for_tr = self._p_close if self._has_prev else c
        tr = max(h - l, abs(h - prev_close_for_tr), abs(l - prev_close_for_tr))
        self._tr.append(tr)
        while len(self._tr) > self.atr_period:
            self._tr.popleft()
        atr = self._mean(self._tr)
        self.size_vs_atr = (rng / atr) if (not isnan(atr) and atr > 0) else NAN

        # Rolling avg over PRIOR bars (buffers hold prior bars only at this point).
        avg_body = self._mean(self._body)
        avg_range = self._mean(self._range)
        self.body_vs_roll = (body / avg_body) if (not isnan(avg_body) and avg_body > 0) else NAN
        self.size_vs_roll = (rng / avg_range) if (not isnan(avg_range) and avg_range > 0) else NAN

        # Rolling extreme over PRIOR bars.
        self.roll_high = self._max(self._high)
        self.roll_low = self._min(self._low)

        self.has_prev = self._has_prev
        self.prev_open, self.prev_high, self.prev_low = self._p_open, self._p_high, self._p_low
        self.prev_close, self.prev_body = self._p_close, self._p_body

        # Push current bar AFTER computing features.
        self._body.append(body)
        while len(self._body) > self.roll_lookback:
            self._body.popleft()
        self._range.append(rng)
        while len(self._range) > self.roll_lookback:
            self._range.popleft()
        self._high.append(h)
        while len(self._high) > self.extreme_lookback:
            self._high.popleft()
        self._low.append(l)
        while len(self._low) > self.extreme_lookback:
            self._low.popleft()

        self._p_open, self._p_high, self._p_low, self._p_close, self._p_body = o, h, l, c, body
        self._has_prev = True


def _ge(a: float, b: float) -> bool:
    """>= that is False when either side is NaN (matches C#/pandas)."""
    return (not isnan(a)) and (not isnan(b)) and a >= b


def _le(a: float, b: float) -> bool:
    return (not isnan(a)) and (not isnan(b)) and a <= b


def compute_candle_signal(
    e: StreamingCandleEngine,
    structure: str,
    params: Dict[str, Any],
) -> Tuple[bool, bool]:
    """Mirror of ComputeCandleSignal: returns (long, short) for one bar."""
    p = params or {}
    min_size = float(p.get("min_size_ticks", 4))
    max_size = float(p.get("max_size_ticks", 200))
    in_bounds = e.size_ticks >= min_size and e.size_ticks <= max_size
    min_size_ok = e.size_ticks >= min_size

    long_sig = short_sig = False

    if structure == "large_body":
        large = _nz0(e.body_vs_roll) >= float(p.get("body_multiplier", 1.5))
        small = (_nz0(e.upper_wick_to_body) <= float(p.get("wick_to_body_max", 0.5))
                 and _nz0(e.lower_wick_to_body) <= float(p.get("wick_to_body_max", 0.5)))
        base = large and small and in_bounds
        long_sig = base and e.is_bullish
        short_sig = base and not e.is_bullish

    elif structure == "pin_bar_bullish":
        long_sig = (
            _nz0(e.lower_wick_to_body) >= float(p.get("wick_to_body_min", 1.5))
            and _nz0(e.upper_wick_to_body) <= float(p.get("upper_wick_to_body_max", 0.5))
            and _nz1(e.body_to_range) <= float(p.get("body_to_range_max", 0.35))
            and in_bounds
        )

    elif structure == "pin_bar_bearish":
        short_sig = (
            _nz0(e.upper_wick_to_body) >= float(p.get("wick_to_body_min", 1.5))
            and _nz0(e.lower_wick_to_body) <= float(p.get("lower_wick_to_body_max", 0.5))
            and _nz1(e.body_to_range) <= float(p.get("body_to_range_max", 0.35))
            and in_bounds
        )

    elif structure == "inside_bar":
        inside = (e.has_prev and e.cur_high < e.prev_high and e.cur_low > e.prev_low
                  and e.size_ticks >= float(p.get("min_size_ticks", 2)))
        long_sig = inside and e.is_bullish
        short_sig = inside and not e.is_bullish

    elif structure == "outside_bar":
        outside = (e.has_prev and e.cur_high > e.prev_high and e.cur_low < e.prev_low and min_size_ok)
        long_sig = outside and e.is_bullish
        short_sig = outside and not e.is_bullish

    elif structure == "engulfing_bullish":
        pb = e.prev_body if e.has_prev else 0.0
        long_sig = (
            e.is_bullish and e.has_prev
            and e.cur_open <= e.prev_close
            and e.cur_close >= e.prev_open
            and e.body >= pb * float(p.get("engulf_ratio", 1.0))
            and min_size_ok
        )

    elif structure == "engulfing_bearish":
        pb = e.prev_body if e.has_prev else 0.0
        short_sig = (
            (not e.is_bullish) and e.has_prev
            and e.cur_open >= e.prev_close
            and e.cur_close <= e.prev_open
            and e.body >= pb * float(p.get("engulf_ratio", 1.0))
            and min_size_ok
        )

    elif structure == "doji":
        doji = _nz1(e.body_to_range) <= float(p.get("body_to_range_max", 0.15)) and min_size_ok
        long_sig = doji
        short_sig = doji

    elif structure == "clean_breakout_bar":
        large = _nz0(e.size_vs_atr) >= float(p.get("atr_mult", 1.5))
        clean = _nz0(e.body_to_range) >= float(p.get("body_to_range_min", 0.60))
        base = large and clean and min_size_ok
        long_sig = base and e.is_bullish and _ge(e.cur_high, e.roll_high)
        short_sig = base and (not e.is_bullish) and _le(e.cur_low, e.roll_low)

    elif structure == "n_bar_breakout":
        # Strict close beyond the prior N-bar channel (mirrors C# NbarBreakout).
        long_sig = e.has_prev and (not isnan(e.roll_high)) and e.cur_close > e.roll_high
        short_sig = e.has_prev and (not isnan(e.roll_low)) and e.cur_close < e.roll_low

    else:
        raise ValueError(f"Unknown structure '{structure}'")

    return long_sig, short_sig


def stream_signals(
    bars: pd.DataFrame,
    structure: str,
    params: Optional[Dict[str, Any]] = None,
    tick_size: float = 0.25,
    atr_period: int = 14,
) -> List[Tuple[Any, int]]:
    """
    Run the streaming engine over *bars* and return [(dt, direction), ...].

    direction: 1 (long), -1 (short), 0 (both — doji). Mirrors what the C#
    ComputeCandleSignal would produce on the same series.
    """
    params = dict(params or {})
    lookback = int(params.get("lookback", 20))
    extreme = int(params.get("extreme_lookback", 20))
    eng = StreamingCandleEngine(tick_size, lookback, atr_period, extreme)

    work = bars.sort_values("dt").reset_index(drop=True)
    out: List[Tuple[Any, int]] = []
    for row in work.itertuples(index=False):
        eng.update(float(row.open), float(row.high), float(row.low), float(row.close))
        long_sig, short_sig = compute_candle_signal(eng, structure, params)
        if long_sig and short_sig:
            out.append((row.dt, 0))
        elif long_sig:
            out.append((row.dt, 1))
        elif short_sig:
            out.append((row.dt, -1))
    return out
