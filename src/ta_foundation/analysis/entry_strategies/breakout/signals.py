from __future__ import annotations

"""
Price Breakout Signal Detectors
==================================
Detects two classic price-level breakout patterns:

N_BAR_BREAKOUT
  Price breaks above the highest high (or below the lowest low) of the
  preceding N bars.  The "donchian channel" entry.

    Bull: close[t] > max(high[t-lookback : t-1])
    Bear: close[t] < min(low[t-lookback  : t-1])

  Lag-safe: uses only prior-bar data (rolling max/min are pre-shifted).

VOLATILITY_BREAKOUT
  Price moves more than `atr_mult × ATR` from the prior bar's close
  in a single bar.  Captures expansion bars / volatility explosions.

    Bull: close[t] - close[t-1] > atr_mult × atr[t-1]
    Bear: close[t-1] - close[t] > atr_mult × atr[t-1]

  Optional: require bar to also close in the top/bottom `body_zone_pct`
  of its own range (confirms momentum, filters dojis).

Both detectors optionally filter out signals that occur during a squeeze
(ATR below squeeze_atr_pct of its own N-bar rolling average) — those
are consolidation bars, not breakouts.

Output columns (both detectors):
  dt, direction, open, high, low, close, atr,
  breakout_level   (the N-bar high/low that was breached, or prior close)
  breakout_ticks   (breach distance in ticks)
"""

from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd


DEFAULT_BREAKOUT_CONFIG: Dict[str, Any] = {
    "atr_period":  14,
    "tick_size":   0.25,
}


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _enrich_atr(bars: pd.DataFrame, atr_period: int) -> pd.Series:
    """Return lagged ATR series aligned to bars index."""
    return _compute_atr(bars["high"], bars["low"], bars["close"], atr_period).shift(1)


def _make_row(bar: pd.Series, direction: int, atr_val: float,
              breakout_level: float, tick_size: float) -> Dict[str, Any]:
    breach     = abs(bar["close"] - breakout_level)
    return {
        "dt":              bar["dt"],
        "direction":       direction,
        "open":            bar["open"],
        "high":            bar["high"],
        "low":             bar["low"],
        "close":           bar["close"],
        "atr":             atr_val,
        "breakout_level":  breakout_level,
        "breakout_ticks":  round(breach / tick_size, 2),
    }


def _collect(rows: List[Dict]) -> pd.DataFrame:
    return pd.DataFrame(rows).reset_index(drop=True) if rows else pd.DataFrame()


# ---------------------------------------------------------------------------
# N-Bar Breakout
# ---------------------------------------------------------------------------

def detect_n_bar_breakout(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Donchian-channel breakout: close exceeds the N-bar high/low.

    Parameters
    ----------
    lookback             : int   bars to look back for high/low   default 20
    direction            : int   1, -1, or 0 (both)              default 0
    require_close_beyond : bool  close must exceed, not just wick default True
    min_breach_ticks     : float minimum breach size in ticks     default 1.0
    atr_period           : int   ATR period                       default 14
    tick_size            : float tick size                        default 0.25
    squeeze_atr_pct      : float if ATR < this % of rolling mean, skip (consolidation)
                                 0.0 = disabled                  default 0.0
    squeeze_atr_lookback : int   rolling mean window for squeeze  default 20
    """
    lookback       = int(params.get("lookback",             20))
    direction_cfg  = int(params.get("direction",            0))
    req_close      = bool(params.get("require_close_beyond", True))
    min_breach     = float(params.get("min_breach_ticks",   1.0))
    atr_period     = int(params.get("atr_period",           14))
    tick_size      = float(params.get("tick_size",          0.25))
    sq_atr_pct     = float(params.get("squeeze_atr_pct",    0.0))
    sq_atr_lb      = int(params.get("squeeze_atr_lookback", 20))

    if len(bars) < lookback + 2:
        return pd.DataFrame()

    atr = _enrich_atr(bars, atr_period)

    # Rolling N-bar high/low of prior bars (shift(1) so current bar excluded)
    roll_high = bars["high"].shift(1).rolling(lookback).max()
    roll_low  = bars["low"].shift(1).rolling(lookback).min()

    # Squeeze filter: ATR below fraction of its own rolling mean
    if sq_atr_pct > 0:
        atr_mean = atr.rolling(sq_atr_lb).mean()
        in_squeeze = atr < atr_mean * sq_atr_pct
    else:
        in_squeeze = pd.Series(False, index=bars.index)

    directions = [1, -1] if direction_cfg == 0 else [direction_cfg]
    rows: List[Dict] = []

    for i in range(lookback + 1, len(bars)):
        if in_squeeze.iloc[i]:
            continue

        bar     = bars.iloc[i]
        atr_val = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else np.nan
        rh      = roll_high.iloc[i]
        rl      = roll_low.iloc[i]

        if np.isnan(rh) or np.isnan(rl):
            continue

        for d in directions:
            if d == 1:
                level   = rh
                breached = (bar["close"] > level) if req_close else (bar["high"] > level)
                breach   = bar["close"] - level if req_close else bar["high"] - level
            else:
                level   = rl
                breached = (bar["close"] < level) if req_close else (bar["low"] < level)
                breach   = level - bar["close"] if req_close else level - bar["low"]

            if breached and breach >= min_breach * tick_size:
                rows.append(_make_row(bar, d, atr_val, level, tick_size))

    return _collect(rows)


# ---------------------------------------------------------------------------
# Volatility Breakout
# ---------------------------------------------------------------------------

def detect_volatility_breakout(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    ATR-based single-bar expansion breakout.

    Parameters
    ----------
    atr_mult          : float  move must be > atr_mult × ATR     default 1.5
    direction         : int    1, -1, or 0                       default 0
    atr_period        : int                                       default 14
    tick_size         : float                                     default 0.25
    body_zone_pct     : float  close must be in top/bottom N% of bar's range
                               0.0 = disabled                    default 0.0
    min_atr_ticks     : float  min ATR in ticks (skip tiny-ATR bars)  default 4.0
    """
    atr_mult      = float(params.get("atr_mult",       1.5))
    direction_cfg = int(params.get("direction",         0))
    atr_period    = int(params.get("atr_period",        14))
    tick_size     = float(params.get("tick_size",       0.25))
    body_zone     = float(params.get("body_zone_pct",   0.0))
    min_atr_ticks = float(params.get("min_atr_ticks",  4.0))

    atr = _enrich_atr(bars, atr_period)
    prev_close = bars["close"].shift(1)
    directions = [1, -1] if direction_cfg == 0 else [direction_cfg]
    rows: List[Dict] = []

    for i in range(1, len(bars)):
        bar      = bars.iloc[i]
        atr_val  = float(atr.iloc[i])
        prev_c   = float(prev_close.iloc[i])

        if np.isnan(atr_val) or atr_val < min_atr_ticks * tick_size:
            continue

        bar_range = bar["high"] - bar["low"]
        if bar_range <= 0:
            continue

        for d in directions:
            if d == 1:
                move = bar["close"] - prev_c
            else:
                move = prev_c - bar["close"]

            if move < atr_mult * atr_val:
                continue

            if body_zone > 0:
                if d == 1:
                    close_zone = (bar["close"] - bar["low"]) / bar_range
                    if close_zone < (1.0 - body_zone):
                        continue
                else:
                    close_zone = (bar["high"] - bar["close"]) / bar_range
                    if close_zone < (1.0 - body_zone):
                        continue

            rows.append(_make_row(bar, d, atr_val, prev_c, tick_size))

    return _collect(rows)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

BREAKOUT_SIGNAL_REGISTRY: Dict[str, Callable] = {
    "n_bar_breakout":       detect_n_bar_breakout,
    "volatility_breakout":  detect_volatility_breakout,
}

BREAKOUT_SIGNAL_LABELS: Dict[str, str] = {
    "n_bar_breakout":       "N-Bar Donchian Breakout",
    "volatility_breakout":  "Volatility Expansion Breakout",
}


def detect_breakout_signal(
    signal_id: str,
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    fn = BREAKOUT_SIGNAL_REGISTRY.get(signal_id)
    if fn is None:
        raise ValueError(f"Unknown breakout signal: {signal_id!r}")
    return fn(bars, params)
