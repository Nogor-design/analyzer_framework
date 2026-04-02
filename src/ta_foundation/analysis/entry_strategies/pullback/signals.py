from __future__ import annotations

"""
Pullback / Continuation Signal Detectors
==========================================
Three pullback-entry setups, all trend-following:

TREND_PULLBACK
  Identifies a short-term trend (N bars trending up/down) then waits for
  price to pull back by a fraction of the recent move before re-entering
  in the trend direction.

    1. Measure trend: close[t] - close[t - trend_bars] vs ATR threshold
    2. Measure retracement: (swing_high - close) / (swing_high - swing_low)
       must fall within [retrace_min, retrace_max]  (e.g. 0.30 – 0.65)
    3. Entry when current close is back in trend direction vs prior close

INSIDE_BAR_CONTINUATION
  Detects an inside bar (range contained within prior bar) after a trending
  move, and fires a continuation signal when price breaks the inside bar's
  extreme in the trend direction.

  Detection bar is the BREAK bar (the bar that breaks the inside bar high/low).
  The inside bar must have occurred in a trending context.

FAILED_BREAKOUT_REVERSAL
  A bar closes beyond a recent N-bar high/low (apparent breakout) then the
  NEXT bar closes back inside — signalling a failed breakout and entry in
  the opposite direction.

Output columns (all detectors):
  dt, direction, open, high, low, close, atr,
  trend_strength   (move / ATR over trend window)
  retrace_pct      (how deep the pullback was, 0–1)
"""

from typing import Any, Callable, Dict, List

import numpy as np
import pandas as pd


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _make_row(bar: pd.Series, direction: int, atr_val: float,
              trend_strength: float = np.nan,
              retrace_pct: float = np.nan) -> Dict[str, Any]:
    return {
        "dt":             bar["dt"],
        "direction":      direction,
        "open":           bar["open"],
        "high":           bar["high"],
        "low":            bar["low"],
        "close":          bar["close"],
        "atr":            atr_val,
        "trend_strength": trend_strength,
        "retrace_pct":    retrace_pct,
    }


def _collect(rows: List[Dict]) -> pd.DataFrame:
    return pd.DataFrame(rows).reset_index(drop=True) if rows else pd.DataFrame()


# ---------------------------------------------------------------------------
# Trend Pullback
# ---------------------------------------------------------------------------

def detect_trend_pullback(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Trend-pullback re-entry.

    Parameters
    ----------
    trend_bars       : int   bars to measure trend over          default 10
    min_trend_atr    : float min trend move in ATR multiples     default 1.0
    retrace_min      : float min retracement of trend move       default 0.25
    retrace_max      : float max retracement (too deep = reversal) default 0.65
    direction        : int   1, -1, or 0 (both)                 default 0
    atr_period       : int                                       default 14
    tick_size        : float                                     default 0.25
    confirm_close    : bool  current bar must close in trend dir default True
    """
    trend_bars  = int(params.get("trend_bars",    10))
    min_trend   = float(params.get("min_trend_atr", 1.0))
    retrace_min = float(params.get("retrace_min",  0.25))
    retrace_max = float(params.get("retrace_max",  0.65))
    direction_c = int(params.get("direction",      0))
    atr_period  = int(params.get("atr_period",     14))
    tick_size   = float(params.get("tick_size",    0.25))
    confirm_cls = bool(params.get("confirm_close", True))

    atr = _compute_atr(bars["high"], bars["low"], bars["close"], atr_period).shift(1)
    directions = [1, -1] if direction_c == 0 else [direction_c]
    rows: List[Dict] = []

    for i in range(trend_bars + 2, len(bars)):
        bar     = bars.iloc[i]
        atr_val = float(atr.iloc[i])
        if np.isnan(atr_val) or atr_val <= 0:
            continue

        # Swing high/low over the last trend_bars bars (not including current)
        window = bars.iloc[i - trend_bars: i]
        sw_high = float(window["high"].max())
        sw_low  = float(window["low"].min())
        sw_range = sw_high - sw_low
        if sw_range <= 0:
            continue

        anchor_close = float(bars["close"].iloc[i - trend_bars])
        cur_close    = float(bar["close"])
        prev_close   = float(bars["close"].iloc[i - 1])

        for d in directions:
            if d == 1:
                # Bull trend: close moved up from anchor
                trend_move = anchor_close - sw_low  # from swing low to start
                if anchor_close > sw_low:
                    # actually measure: how much did close rise over trend window
                    pass
                trend_move = float(bars["close"].iloc[i-1]) - float(bars["close"].iloc[i - trend_bars])
                if trend_move < min_trend * atr_val:
                    continue
                # Retracement: how far has price pulled back from the recent high
                retrace = (sw_high - cur_close) / sw_range
                if not (retrace_min <= retrace <= retrace_max):
                    continue
                # Confirm close: current bar closes higher than previous
                if confirm_cls and cur_close <= prev_close:
                    continue
            else:
                # Bear trend: close moved down from anchor
                trend_move = float(bars["close"].iloc[i - trend_bars]) - float(bars["close"].iloc[i-1])
                if trend_move < min_trend * atr_val:
                    continue
                retrace = (cur_close - sw_low) / sw_range
                if not (retrace_min <= retrace <= retrace_max):
                    continue
                if confirm_cls and cur_close >= prev_close:
                    continue

            strength = trend_move / atr_val
            rows.append(_make_row(bar, d, atr_val, strength, retrace))

    return _collect(rows)


# ---------------------------------------------------------------------------
# Inside Bar Continuation
# ---------------------------------------------------------------------------

def detect_inside_bar_continuation(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Inside-bar breakout continuation.

    Parameters
    ----------
    trend_bars         : int   trend context window              default 8
    min_trend_atr      : float min trend strength to qualify     default 0.8
    direction          : int   1, -1, or 0 (both)               default 0
    atr_period         : int                                     default 14
    tick_size          : float                                   default 0.25
    inside_bar_lookback: int   check for IB in last N bars       default 3
    """
    trend_bars  = int(params.get("trend_bars",           8))
    min_trend   = float(params.get("min_trend_atr",      0.8))
    direction_c = int(params.get("direction",            0))
    atr_period  = int(params.get("atr_period",           14))
    tick_size   = float(params.get("tick_size",          0.25))
    ib_lookback = int(params.get("inside_bar_lookback",  3))

    atr = _compute_atr(bars["high"], bars["low"], bars["close"], atr_period).shift(1)
    directions = [1, -1] if direction_c == 0 else [direction_c]
    rows: List[Dict] = []

    for i in range(trend_bars + ib_lookback + 1, len(bars)):
        bar     = bars.iloc[i]
        atr_val = float(atr.iloc[i])
        if np.isnan(atr_val) or atr_val <= 0:
            continue

        # Trend context: close moved trend_bars bars ago
        trend_move = float(bars["close"].iloc[i-1]) - float(bars["close"].iloc[i - trend_bars])

        # Check for inside bar in the last ib_lookback bars
        ib_found = False
        ib_high  = np.nan
        ib_low   = np.nan
        for j in range(1, ib_lookback + 1):
            idx = i - j
            if idx < 1:
                break
            cur_h = float(bars["high"].iloc[idx])
            cur_l = float(bars["low"].iloc[idx])
            prv_h = float(bars["high"].iloc[idx - 1])
            prv_l = float(bars["low"].iloc[idx - 1])
            if cur_h <= prv_h and cur_l >= prv_l:
                ib_found = True
                ib_high  = cur_h
                ib_low   = cur_l
                break

        if not ib_found:
            continue

        for d in directions:
            if d == 1:
                if trend_move < min_trend * atr_val:
                    continue
                # Bull continuation: current bar breaks above inside bar high
                if bar["close"] > ib_high:
                    rows.append(_make_row(bar, 1, atr_val, trend_move / atr_val))
            else:
                if trend_move > -(min_trend * atr_val):
                    continue
                # Bear continuation: current bar breaks below inside bar low
                if bar["close"] < ib_low:
                    rows.append(_make_row(bar, -1, atr_val, trend_move / atr_val))

    return _collect(rows)


# ---------------------------------------------------------------------------
# Failed Breakout Reversal
# ---------------------------------------------------------------------------

def detect_failed_breakout(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Price breaks an N-bar high/low then immediately reverses back inside.
    Entry in the reversal direction.

    Parameters
    ----------
    lookback         : int   N-bar channel to break              default 20
    direction        : int   1 (failed bear break → long), -1, 0 default 0
    atr_period       : int                                       default 14
    tick_size        : float                                     default 0.25
    min_breach_ticks : float min initial breach size             default 1.0
    """
    lookback      = int(params.get("lookback",          20))
    direction_c   = int(params.get("direction",         0))
    atr_period    = int(params.get("atr_period",        14))
    tick_size     = float(params.get("tick_size",       0.25))
    min_breach    = float(params.get("min_breach_ticks", 1.0))

    atr = _compute_atr(bars["high"], bars["low"], bars["close"], atr_period).shift(1)

    # Rolling channel (lagged so current bar excluded from its own channel)
    roll_high = bars["high"].shift(1).rolling(lookback).max()
    roll_low  = bars["low"].shift(1).rolling(lookback).min()

    directions = [1, -1] if direction_c == 0 else [direction_c]
    rows: List[Dict] = []

    for i in range(lookback + 2, len(bars)):
        bar      = bars.iloc[i]
        prev_bar = bars.iloc[i - 1]
        atr_val  = float(atr.iloc[i])
        rh       = roll_high.iloc[i - 1]   # channel at previous bar
        rl       = roll_low.iloc[i - 1]

        if np.isnan(rh) or np.isnan(rl) or np.isnan(atr_val):
            continue

        for d in directions:
            if d == 1:
                # Failed bear break: prev bar closed below low, current closes back above low
                prev_breach = prev_bar["close"] < rl and (rl - prev_bar["close"]) >= min_breach * tick_size
                reversal    = bar["close"] > rl
                if prev_breach and reversal:
                    rows.append(_make_row(bar, 1, atr_val))
            else:
                # Failed bull break: prev bar closed above high, current closes back below high
                prev_breach = prev_bar["close"] > rh and (prev_bar["close"] - rh) >= min_breach * tick_size
                reversal    = bar["close"] < rh
                if prev_breach and reversal:
                    rows.append(_make_row(bar, -1, atr_val))

    return _collect(rows)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PULLBACK_SIGNAL_REGISTRY: Dict[str, Callable] = {
    "trend_pullback":            detect_trend_pullback,
    "inside_bar_continuation":   detect_inside_bar_continuation,
    "failed_breakout":           detect_failed_breakout,
}

PULLBACK_SIGNAL_LABELS: Dict[str, str] = {
    "trend_pullback":            "Trend Pullback Re-Entry",
    "inside_bar_continuation":   "Inside Bar Continuation",
    "failed_breakout":           "Failed Breakout Reversal",
}


def detect_pullback_signal(
    signal_id: str,
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    fn = PULLBACK_SIGNAL_REGISTRY.get(signal_id)
    if fn is None:
        raise ValueError(f"Unknown pullback signal: {signal_id!r}")
    return fn(bars, params)
