from __future__ import annotations

"""
Level-Based Signal Detectors
================================
Detects entries near key price levels derived from recent price structure.

SWING_LEVEL_TEST
  Identifies swing highs and lows (pivot points) from recent price history,
  then fires a signal when price tests that level (comes within tolerance)
  and shows a confirming candle (closes away from the level).

  Swing high: high[t] > max(high[t-n:t]) and high[t] > max(high[t+1:t+n])
  → lag-safe version: we only use confirmed pivots (right-side bars already
    printed), so the most recent pivot is at least `pivot_lookback` bars ago.

  Bull test: price approaches a prior SWING LOW from above and bounces.
  Bear test: price approaches a prior SWING HIGH from below and rejects.

CONSOLIDATION_BREAKOUT
  Detects a tight consolidation range (N bars with ATR-normalised range < threshold)
  then fires when price breaks out of that range.

  Different from BB squeeze in that it uses raw price range, not BB width,
  making it parameter-free with respect to BB settings.

ROUND_NUMBER_BOUNCE
  Price approaches a round-number level (configurable step, e.g. every 50
  or 100 points) and shows a bounce candle.

  Bull: price touched round number from above (low <= level), closed above it.
  Bear: price touched round number from below (high >= level), closed below it.

Output columns (all detectors):
  dt, direction, open, high, low, close, atr,
  level_price   (the level that was tested)
  level_type    (swing_high, swing_low, round_number, consol_high, consol_low)
  level_dist_ticks  (distance from close to level, in ticks)
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _make_row(bar: pd.Series, direction: int, atr_val: float,
              level_price: float, level_type: str, tick_size: float) -> Dict[str, Any]:
    dist = abs(bar["close"] - level_price)
    return {
        "dt":               bar["dt"],
        "direction":        direction,
        "open":             bar["open"],
        "high":             bar["high"],
        "low":              bar["low"],
        "close":            bar["close"],
        "atr":              atr_val,
        "level_price":      level_price,
        "level_type":       level_type,
        "level_dist_ticks": round(dist / tick_size, 2),
    }


def _collect(rows: List[Dict]) -> pd.DataFrame:
    return pd.DataFrame(rows).reset_index(drop=True) if rows else pd.DataFrame()


# ---------------------------------------------------------------------------
# Swing Level Detector
# ---------------------------------------------------------------------------

def _find_swing_pivots(
    bars: pd.DataFrame,
    pivot_lookback: int,
    end_idx: int,
    max_pivots: int,
) -> Tuple[List[float], List[float]]:
    """
    Return (swing_highs, swing_lows) confirmed before end_idx.
    A pivot high at bar i requires: high[i] = max(high[i-n:i+n+1])
    We only collect pivots at least pivot_lookback bars before end_idx (confirmed).
    """
    n = pivot_lookback
    highs: List[float] = []
    lows:  List[float] = []
    start = max(n, end_idx - max_pivots * n * 3)

    for i in range(start, end_idx - n):
        window_h = bars["high"].iloc[max(0, i-n): i+n+1]
        window_l = bars["low"].iloc[max(0, i-n):  i+n+1]
        bar_h    = bars["high"].iloc[i]
        bar_l    = bars["low"].iloc[i]

        if bar_h == window_h.max() and len(highs) < max_pivots:
            highs.append(float(bar_h))
        if bar_l == window_l.min() and len(lows) < max_pivots:
            lows.append(float(bar_l))

    return highs, lows


def detect_swing_level_test(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Price tests a confirmed swing high or low and bounces.

    Parameters
    ----------
    pivot_lookback      : int   bars each side to confirm pivot  default 5
    touch_atr_mult      : float within N×ATR of level = touch    default 0.5
    max_pivots          : int   how many recent pivots to track  default 5
    direction           : int   1 (test swing low=support), -1, 0  default 0
    atr_period          : int                                    default 14
    tick_size           : float                                  default 0.25
    require_close_away  : bool  bar must close away from level   default True
    min_close_atr       : float min close distance from level    default 0.2
    """
    pivot_lb     = int(params.get("pivot_lookback",     5))
    touch_mult   = float(params.get("touch_atr_mult",   0.5))
    max_pivots   = int(params.get("max_pivots",         5))
    direction_c  = int(params.get("direction",          0))
    atr_period   = int(params.get("atr_period",         14))
    tick_size    = float(params.get("tick_size",        0.25))
    req_close    = bool(params.get("require_close_away", True))
    min_close    = float(params.get("min_close_atr",    0.2))

    atr = _compute_atr(bars["high"], bars["low"], bars["close"], atr_period).shift(1)
    directions = [1, -1] if direction_c == 0 else [direction_c]
    rows: List[Dict] = []

    for i in range(pivot_lb * 3 + 1, len(bars)):
        bar     = bars.iloc[i]
        atr_val = float(atr.iloc[i])
        if np.isnan(atr_val) or atr_val <= 0:
            continue

        sw_highs, sw_lows = _find_swing_pivots(bars, pivot_lb, i, max_pivots)

        for d in directions:
            levels     = sw_lows if d == 1 else sw_highs
            level_type = "swing_low" if d == 1 else "swing_high"

            for level in levels:
                touch_zone = touch_mult * atr_val

                if d == 1:
                    # Bull: bar touched or pierced the level from above
                    touched = bar["low"] <= level + touch_zone
                    # Close must be above level
                    closed_away = bar["close"] > level + min_close * atr_val
                else:
                    # Bear: bar touched or pierced the level from below
                    touched = bar["high"] >= level - touch_zone
                    closed_away = bar["close"] < level - min_close * atr_val

                if not touched:
                    continue
                if req_close and not closed_away:
                    continue

                rows.append(_make_row(bar, d, atr_val, level, level_type, tick_size))
                break  # one signal per bar per direction

    return _collect(rows)


# ---------------------------------------------------------------------------
# Consolidation Breakout
# ---------------------------------------------------------------------------

def detect_consolidation_breakout(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Tight-range consolidation followed by a price breakout.

    Parameters
    ----------
    consol_bars          : int   bars to measure consolidation   default 8
    max_range_atr        : float max range/ATR to be consolidating default 0.8
    direction            : int                                   default 0
    atr_period           : int                                   default 14
    tick_size            : float                                 default 0.25
    require_close_beyond : bool                                  default True
    min_breach_atr       : float min breach beyond consol range  default 0.3
    """
    consol_bars   = int(params.get("consol_bars",          8))
    max_range_atr = float(params.get("max_range_atr",      0.8))
    direction_c   = int(params.get("direction",            0))
    atr_period    = int(params.get("atr_period",           14))
    tick_size     = float(params.get("tick_size",          0.25))
    req_close     = bool(params.get("require_close_beyond", True))
    min_breach    = float(params.get("min_breach_atr",     0.3))

    atr = _compute_atr(bars["high"], bars["low"], bars["close"], atr_period).shift(1)
    directions = [1, -1] if direction_c == 0 else [direction_c]
    rows: List[Dict] = []

    for i in range(consol_bars + 1, len(bars)):
        bar     = bars.iloc[i]
        atr_val = float(atr.iloc[i])
        if np.isnan(atr_val) or atr_val <= 0:
            continue

        # Consolidation window = bars before current bar
        window = bars.iloc[i - consol_bars: i]
        w_high = float(window["high"].max())
        w_low  = float(window["low"].min())
        w_range = w_high - w_low

        if w_range > max_range_atr * atr_val:
            continue  # not consolidating

        for d in directions:
            if d == 1:
                level    = w_high
                breached = (bar["close"] > level) if req_close else (bar["high"] > level)
                breach   = bar["close"] - level if req_close else bar["high"] - level
            else:
                level    = w_low
                breached = (bar["close"] < level) if req_close else (bar["low"] < level)
                breach   = level - bar["close"] if req_close else level - bar["low"]

            if breached and breach >= min_breach * atr_val:
                rows.append(_make_row(bar, d, atr_val, level,
                                      "consol_high" if d == 1 else "consol_low", tick_size))

    return _collect(rows)


# ---------------------------------------------------------------------------
# Round Number Bounce
# ---------------------------------------------------------------------------

def detect_round_number_bounce(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Price approaches a round-number level and bounces.

    Parameters
    ----------
    level_step          : float  round number interval (e.g. 50, 100)  default 50.0
    touch_ticks         : float  how close constitutes a touch          default 4.0
    direction           : int    1, -1, or 0                           default 0
    atr_period          : int                                           default 14
    tick_size           : float                                         default 0.25
    require_close_away  : bool   close must be away from level          default True
    min_close_ticks     : float  min close distance from level          default 2.0
    """
    level_step   = float(params.get("level_step",         50.0))
    touch_ticks  = float(params.get("touch_ticks",        4.0))
    direction_c  = int(params.get("direction",            0))
    atr_period   = int(params.get("atr_period",           14))
    tick_size    = float(params.get("tick_size",          0.25))
    req_close    = bool(params.get("require_close_away",  True))
    min_close_t  = float(params.get("min_close_ticks",    2.0))

    atr = _compute_atr(bars["high"], bars["low"], bars["close"], atr_period).shift(1)
    touch_dist  = touch_ticks * tick_size
    min_close_d = min_close_t * tick_size
    directions  = [1, -1] if direction_c == 0 else [direction_c]
    rows: List[Dict] = []

    for i in range(1, len(bars)):
        bar     = bars.iloc[i]
        atr_val = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else np.nan
        close   = float(bar["close"])
        low     = float(bar["low"])
        high    = float(bar["high"])

        # Find nearest round number to the bar's range
        mid = (high + low) / 2
        nearest_level = round(mid / level_step) * level_step

        for d in directions:
            if d == 1:
                # Bull bounce: low touched level, close above
                touched    = low <= nearest_level + touch_dist
                closed_away = close >= nearest_level + min_close_d
            else:
                # Bear bounce: high touched level, close below
                touched    = high >= nearest_level - touch_dist
                closed_away = close <= nearest_level - min_close_d

            if not touched:
                continue
            if req_close and not closed_away:
                continue

            rows.append(_make_row(bar, d, atr_val if not np.isnan(atr_val) else 0.0,
                                  nearest_level, "round_number", tick_size))

    return _collect(rows)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

LEVEL_SIGNAL_REGISTRY: Dict[str, Callable] = {
    "swing_level_test":          detect_swing_level_test,
    "consolidation_breakout":    detect_consolidation_breakout,
    "round_number_bounce":       detect_round_number_bounce,
}

LEVEL_SIGNAL_LABELS: Dict[str, str] = {
    "swing_level_test":          "Swing High/Low Level Test",
    "consolidation_breakout":    "Consolidation Range Breakout",
    "round_number_bounce":       "Round Number Level Bounce",
}


def detect_level_signal(
    signal_id: str,
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    fn = LEVEL_SIGNAL_REGISTRY.get(signal_id)
    if fn is None:
        raise ValueError(f"Unknown level signal: {signal_id!r}")
    return fn(bars, params)
