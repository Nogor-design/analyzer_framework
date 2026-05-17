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

VWAP_RECLAIM_REJECT
  Intraday VWAP reaction. Long fires when price pierces below VWAP and closes
  back above it; short fires when price pierces above VWAP and closes back
  below it. This catches the common "failed push away from value" setup.

PRIOR_SESSION_LEVEL_REACTION
  Tests previous RTH high/low/close and overnight high/low. Long fires on a
  support reaction; short fires on a resistance rejection. These are among the
  most durable intraday futures reference levels.

REFERENCE_LEVEL_SWEEP_RECLAIM
  Price sweeps beyond prior RTH high/low or overnight high/low by a configurable
  number of ticks, then closes back through the level. This is the reference
  level version of a failed auction/trapped participants setup.

LIQUIDITY_SWEEP_FAILURE
  Price sweeps a recent high/low by a configurable number of ticks, then closes
  back inside the prior range. This models failed auctions/traps, not simple
  breakouts.

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
# VWAP Reclaim / Reject
# ---------------------------------------------------------------------------

def _session_date_series(bars: pd.DataFrame) -> pd.Series:
    dt = pd.to_datetime(bars["dt"])
    if dt.dt.tz is not None:
        dt = dt.dt.tz_convert("America/Denver")
    return dt.dt.date


def _compute_session_vwap(bars: pd.DataFrame) -> pd.Series:
    typical = (bars["high"].astype(float) + bars["low"].astype(float) + bars["close"].astype(float)) / 3.0
    if "volume" in bars.columns:
        volume = pd.to_numeric(bars["volume"], errors="coerce").fillna(0.0)
        volume = volume.where(volume > 0, 1.0)
    else:
        volume = pd.Series(1.0, index=bars.index)
    session_date = _session_date_series(bars)
    pv = typical * volume
    cum_pv = pv.groupby(session_date).cumsum()
    cum_v = volume.groupby(session_date).cumsum().replace(0, np.nan)
    return cum_pv / cum_v


def detect_vwap_reclaim_reject(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    VWAP reclaim/reject entry.

    Parameters
    ----------
    max_dist_ticks      : float max close distance from VWAP after reclaim default 16
    min_pierce_ticks    : float min wick pierce through VWAP              default 2
    direction           : int   1, -1, or 0                              default 0
    atr_period          : int                                             default 14
    tick_size           : float                                           default 0.25
    min_dist_ticks      : float min close distance from VWAP after reclaim default 0
    min_atr_ticks       : float skip dead sessions below this ATR         default 4
    """
    max_dist_t = float(params.get("max_dist_ticks", 16.0))
    min_pierce_t = float(params.get("min_pierce_ticks", 2.0))
    min_dist_t = float(params.get("min_dist_ticks", 0.0))
    direction_c = int(params.get("direction", 0))
    atr_period = int(params.get("atr_period", 14))
    tick_size = float(params.get("tick_size", 0.25))
    min_atr_t = float(params.get("min_atr_ticks", 4.0))

    atr = _compute_atr(bars["high"], bars["low"], bars["close"], atr_period).shift(1)
    vwap = _compute_session_vwap(bars).shift(1)
    directions = [1, -1] if direction_c == 0 else [direction_c]
    rows: List[Dict] = []

    for i in range(2, len(bars)):
        bar = bars.iloc[i]
        vw = float(vwap.iloc[i]) if not np.isnan(vwap.iloc[i]) else np.nan
        atr_val = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else np.nan
        if np.isnan(vw) or np.isnan(atr_val) or atr_val < min_atr_t * tick_size:
            continue

        close = float(bar["close"])
        dist_ticks = abs(close - vw) / tick_size
        if dist_ticks < min_dist_t or dist_ticks > max_dist_t:
            continue

        for d in directions:
            if d == 1:
                pierced = float(bar["low"]) <= vw - min_pierce_t * tick_size
                reclaimed = close > vw
                if pierced and reclaimed:
                    rows.append(_make_row(bar, d, atr_val, vw, "vwap_reclaim", tick_size))
            else:
                pierced = float(bar["high"]) >= vw + min_pierce_t * tick_size
                rejected = close < vw
                if pierced and rejected:
                    rows.append(_make_row(bar, d, atr_val, vw, "vwap_reject", tick_size))

    return _collect(rows)


def detect_vwap_distance_fade(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    VWAP distance fade entry. Detects mean-reversion when overextended.

    Parameters
    ----------
    min_dist_ticks      : float min close distance from VWAP to qualify   default 40
    max_dist_ticks      : float cap on overextension                      default 150
    reversal_ticks      : float min distance closed back toward VWAP      default 4
    direction           : int   1, -1, or 0                              default 0
    atr_period          : int                                             default 14
    tick_size           : float                                           default 0.25
    min_atr_ticks       : float skip dead sessions below this ATR         default 4
    """
    min_dist_t = float(params.get("min_dist_ticks", 40.0))
    max_dist_t = float(params.get("max_dist_ticks", 150.0))
    reversal_t = float(params.get("reversal_ticks", 4.0))
    direction_c = int(params.get("direction", 0))
    atr_period = int(params.get("atr_period", 14))
    tick_size = float(params.get("tick_size", 0.25))
    min_atr_t = float(params.get("min_atr_ticks", 4.0))

    atr = _compute_atr(bars["high"], bars["low"], bars["close"], atr_period).shift(1)
    vwap = _compute_session_vwap(bars).shift(1)
    directions = [1, -1] if direction_c == 0 else [direction_c]
    rows: List[Dict] = []

    for i in range(2, len(bars)):
        bar = bars.iloc[i]
        prev_bar = bars.iloc[i-1]
        vw = float(vwap.iloc[i])
        atr_val = float(atr.iloc[i])

        if np.isnan(vw) or np.isnan(atr_val) or atr_val < min_atr_t * tick_size:
            continue

        close = float(bar["close"])

        for d in directions:
            if d == 1:
                # Long fade (price is below VWAP, looking for reversal up)
                dist_below = vw - float(prev_bar["close"])
                if dist_below < min_dist_t * tick_size or dist_below > max_dist_t * tick_size:
                    continue

                # Reversal: close >= prev_close + reversal_ticks
                if close >= float(prev_bar["close"]) + reversal_t * tick_size:
                    rows.append(_make_row(bar, d, atr_val, vw, "vwap_dist_fade_long", tick_size))
            else:
                # Short fade (price is above VWAP, looking for reversal down)
                dist_above = float(prev_bar["close"]) - vw
                if dist_above < min_dist_t * tick_size or dist_above > max_dist_t * tick_size:
                    continue

                # Reversal: close <= prev_close - reversal_ticks
                if close <= float(prev_bar["close"]) - reversal_t * tick_size:
                    rows.append(_make_row(bar, d, atr_val, vw, "vwap_dist_fade_short", tick_size))

    return _collect(rows)


# ---------------------------------------------------------------------------
# Prior Session / Overnight Level Reaction
# ---------------------------------------------------------------------------

def _session_minutes(bars: pd.DataFrame) -> pd.Series:
    dt = pd.to_datetime(bars["dt"])
    if dt.dt.tz is not None:
        dt = dt.dt.tz_convert("America/Denver")
    return dt.dt.hour * 60 + dt.dt.minute


def _reference_levels_by_day(
    bars: pd.DataFrame,
    *,
    rth_start_minute: int,
    rth_end_minute: int,
) -> Dict[Any, Dict[str, float]]:
    session_date = _session_date_series(bars)
    minute = _session_minutes(bars)
    tmp = bars.copy()
    tmp["_session_date"] = session_date
    tmp["_minute"] = minute
    unique_days = sorted(tmp["_session_date"].dropna().unique())
    refs: Dict[Any, Dict[str, float]] = {}

    for idx, day in enumerate(unique_days):
        if idx == 0:
            continue
        prev_day = unique_days[idx - 1]
        prev_rth = tmp[
            (tmp["_session_date"] == prev_day)
            & (tmp["_minute"] >= rth_start_minute)
            & (tmp["_minute"] < rth_end_minute)
        ]
        cur_overnight = tmp[
            (tmp["_session_date"] == day)
            & (tmp["_minute"] < rth_start_minute)
        ]
        day_refs: Dict[str, float] = {}
        if not prev_rth.empty:
            day_refs["prior_high"] = float(prev_rth["high"].max())
            day_refs["prior_low"] = float(prev_rth["low"].min())
            day_refs["prior_close"] = float(prev_rth.iloc[-1]["close"])
        if not cur_overnight.empty:
            day_refs["overnight_high"] = float(cur_overnight["high"].max())
            day_refs["overnight_low"] = float(cur_overnight["low"].min())
        if day_refs:
            refs[day] = day_refs
    return refs


def detect_prior_session_level_reaction(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Reactions at prior RTH and overnight reference levels.

    Parameters
    ----------
    levels              : list[str] prior_high/prior_low/prior_close/overnight_high/overnight_low
    touch_ticks         : float     touch tolerance                         default 8
    min_close_ticks     : float     close-away distance                     default 2
    direction           : int       1, -1, or 0                             default 0
    rth_start_hour/minute, rth_end_hour/minute                              default 7:30-16:00
    """
    selected = params.get("levels") or [
        "prior_high", "prior_low", "prior_close", "overnight_high", "overnight_low"
    ]
    if isinstance(selected, str):
        selected = [selected]
    touch_ticks = float(params.get("touch_ticks", 8.0))
    min_close_ticks = float(params.get("min_close_ticks", 2.0))
    direction_c = int(params.get("direction", 0))
    atr_period = int(params.get("atr_period", 14))
    tick_size = float(params.get("tick_size", 0.25))
    rth_start = int(params.get("rth_start_hour", 7)) * 60 + int(params.get("rth_start_minute", 30))
    rth_end = int(params.get("rth_end_hour", 16)) * 60 + int(params.get("rth_end_minute", 0))

    refs = _reference_levels_by_day(bars, rth_start_minute=rth_start, rth_end_minute=rth_end)
    session_date = _session_date_series(bars)
    minute = _session_minutes(bars)
    atr = _compute_atr(bars["high"], bars["low"], bars["close"], atr_period).shift(1)
    touch_dist = touch_ticks * tick_size
    min_close = min_close_ticks * tick_size
    directions = [1, -1] if direction_c == 0 else [direction_c]
    rows: List[Dict] = []

    for i in range(1, len(bars)):
        day = session_date.iloc[i]
        if minute.iloc[i] < rth_start or minute.iloc[i] >= rth_end:
            continue
        day_refs = refs.get(day) or {}
        if not day_refs:
            continue
        bar = bars.iloc[i]
        atr_val = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else 0.0
        close = float(bar["close"])

        for level_name in selected:
            if level_name not in day_refs:
                continue
            level = float(day_refs[level_name])
            for d in directions:
                if d == 1:
                    touched = float(bar["low"]) <= level + touch_dist
                    away = close >= level + min_close
                    support_like = level_name.endswith("low") or level_name == "prior_close"
                    if touched and away and support_like:
                        rows.append(_make_row(bar, d, atr_val, level, level_name, tick_size))
                        break
                else:
                    touched = float(bar["high"]) >= level - touch_dist
                    away = close <= level - min_close
                    resistance_like = level_name.endswith("high") or level_name == "prior_close"
                    if touched and away and resistance_like:
                        rows.append(_make_row(bar, d, atr_val, level, level_name, tick_size))
                        break

    return _collect(rows)


# ---------------------------------------------------------------------------
# Prior / Overnight Level Sweep Reclaim
# ---------------------------------------------------------------------------

def detect_reference_level_sweep_reclaim(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Failed breakout through prior/overnight reference levels.

    Parameters
    ----------
    levels              : list[str] prior_high/prior_low/prior_close/overnight_high/overnight_low
    sweep_ticks         : float     minimum pierce beyond level
    close_back_ticks    : float     minimum close back through level
    direction           : int       1, -1, or 0
    rth_start_hour/minute, rth_end_hour/minute
    """
    selected = params.get("levels") or [
        "prior_high", "prior_low", "overnight_high", "overnight_low"
    ]
    if isinstance(selected, str):
        selected = [selected]
    sweep_ticks = float(params.get("sweep_ticks", 2.0))
    close_back_ticks = float(params.get("close_back_ticks", 1.0))
    direction_c = int(params.get("direction", 0))
    atr_period = int(params.get("atr_period", 14))
    tick_size = float(params.get("tick_size", 0.25))
    min_atr_ticks = float(params.get("min_atr_ticks", 4.0))
    rth_start = int(params.get("rth_start_hour", 7)) * 60 + int(params.get("rth_start_minute", 30))
    rth_end = int(params.get("rth_end_hour", 16)) * 60 + int(params.get("rth_end_minute", 0))

    refs = _reference_levels_by_day(bars, rth_start_minute=rth_start, rth_end_minute=rth_end)
    session_date = _session_date_series(bars)
    minute = _session_minutes(bars)
    atr = _compute_atr(bars["high"], bars["low"], bars["close"], atr_period).shift(1)
    sweep_dist = sweep_ticks * tick_size
    close_back = close_back_ticks * tick_size
    directions = [1, -1] if direction_c == 0 else [direction_c]
    rows: List[Dict] = []

    for i in range(1, len(bars)):
        day = session_date.iloc[i]
        if minute.iloc[i] < rth_start or minute.iloc[i] >= rth_end:
            continue
        day_refs = refs.get(day) or {}
        if not day_refs:
            continue
        bar = bars.iloc[i]
        atr_val = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else np.nan
        if np.isnan(atr_val) or atr_val < min_atr_ticks * tick_size:
            continue
        close = float(bar["close"])
        high = float(bar["high"])
        low = float(bar["low"])

        for level_name in selected:
            if level_name not in day_refs:
                continue
            level = float(day_refs[level_name])

            for d in directions:
                if d == -1 and (level_name.endswith("high") or level_name == "prior_close"):
                    swept = high >= level + sweep_dist
                    reclaimed = close <= level - close_back
                    if swept and reclaimed:
                        row = _make_row(bar, d, atr_val, level, f"{level_name}_sweep_reclaim", tick_size)
                        row["swept_level_type"] = level_name
                        rows.append(row)
                        break
                elif d == 1 and (level_name.endswith("low") or level_name == "prior_close"):
                    swept = low <= level - sweep_dist
                    reclaimed = close >= level + close_back
                    if swept and reclaimed:
                        row = _make_row(bar, d, atr_val, level, f"{level_name}_sweep_reclaim", tick_size)
                        row["swept_level_type"] = level_name
                        rows.append(row)
                        break

    return _collect(rows)


# ---------------------------------------------------------------------------
# Liquidity Sweep Failure
# ---------------------------------------------------------------------------

def detect_liquidity_sweep_failure(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Failed auction: sweep recent high/low, then close back inside the range.
    """
    lookback = int(params.get("lookback", 20))
    sweep_ticks = float(params.get("sweep_ticks", 2.0))
    close_back_ticks = float(params.get("close_back_ticks", 1.0))
    direction_c = int(params.get("direction", 0))
    atr_period = int(params.get("atr_period", 14))
    tick_size = float(params.get("tick_size", 0.25))
    min_atr_ticks = float(params.get("min_atr_ticks", 4.0))

    atr = _compute_atr(bars["high"], bars["low"], bars["close"], atr_period).shift(1)
    prior_high = bars["high"].shift(1).rolling(lookback).max()
    prior_low = bars["low"].shift(1).rolling(lookback).min()
    directions = [1, -1] if direction_c == 0 else [direction_c]
    rows: List[Dict] = []

    for i in range(lookback + 1, len(bars)):
        bar = bars.iloc[i]
        atr_val = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else np.nan
        if np.isnan(atr_val) or atr_val < min_atr_ticks * tick_size:
            continue
        ph = prior_high.iloc[i]
        pl = prior_low.iloc[i]
        if np.isnan(ph) or np.isnan(pl):
            continue
        close = float(bar["close"])

        for d in directions:
            if d == 1:
                swept = float(bar["low"]) <= pl - sweep_ticks * tick_size
                failed = close >= pl + close_back_ticks * tick_size
                if swept and failed:
                    rows.append(_make_row(bar, d, atr_val, float(pl), "sweep_low_failure", tick_size))
            else:
                swept = float(bar["high"]) >= ph + sweep_ticks * tick_size
                failed = close <= ph - close_back_ticks * tick_size
                if swept and failed:
                    rows.append(_make_row(bar, d, atr_val, float(ph), "sweep_high_failure", tick_size))

    return _collect(rows)


# ---------------------------------------------------------------------------
# VWAP Continuation After Reclaim
# ---------------------------------------------------------------------------

def detect_vwap_continuation(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Continuation entry after a confirmed VWAP reclaim.

    Distinct from ``vwap_reclaim_reject``: that family fires *on* the reclaim
    bar (long when price pierces below VWAP then closes back above). This
    family fires *after* a confirmed reclaim, on a subsequent continuation
    bar where price has stayed on the reclaim side and now expands further
    away in the reclaim direction.

    A cross is detected when the previous bar's close was on one side of
    VWAP and the current bar's close is on the other side. After the cross,
    the detector arms for ``max_age_bars``. A continuation bar fires when:
      - close is on the same side as the cross,
      - close - open displacement is at least ``min_continuation_ticks``
        in the same direction,
      - distance from VWAP is within [min_dist_ticks, max_dist_ticks],
      - at least ``min_hold_bars`` bars have elapsed since the cross (so
        the reclaim has been held, not whipsawed).
    Only one continuation signal fires per cross event.

    Parameters
    ----------
    min_continuation_ticks : float close-open displacement floor    default 4
    max_age_bars           : int   bars after cross to remain armed default 6
    min_hold_bars          : int   bars to wait after cross         default 1
    min_dist_ticks         : float min close distance from VWAP     default 1
    max_dist_ticks         : float max close distance from VWAP     default 24
    min_atr_ticks          : float regime guard                     default 4
    direction              : int   1, -1, or 0                      default 0
    atr_period             : int                                    default 14
    tick_size              : float                                  default 0.25
    """
    min_cont_t = float(params.get("min_continuation_ticks", 4.0))
    max_age = int(params.get("max_age_bars", 6))
    min_hold = int(params.get("min_hold_bars", 1))
    min_dist_t = float(params.get("min_dist_ticks", 1.0))
    max_dist_t = float(params.get("max_dist_ticks", 24.0))
    min_atr_t = float(params.get("min_atr_ticks", 4.0))
    direction_c = int(params.get("direction", 0))
    atr_period = int(params.get("atr_period", 14))
    tick_size = float(params.get("tick_size", 0.25))

    atr = _compute_atr(bars["high"], bars["low"], bars["close"], atr_period).shift(1)
    vwap = _compute_session_vwap(bars).shift(1)
    session_date = _session_date_series(bars)
    directions = [1, -1] if direction_c == 0 else [direction_c]
    rows: List[Dict] = []

    last_close = bars["close"].shift(1)
    # Per-session armed state: tracks the most recent cross awaiting continuation.
    armed_dir = 0          # +1 if armed long (close crossed up through VWAP), -1 short, 0 idle
    armed_bar_idx = -1
    armed_session = None

    for i in range(1, len(bars)):
        vw = float(vwap.iloc[i]) if not np.isnan(vwap.iloc[i]) else np.nan
        atr_val = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else np.nan
        if np.isnan(vw) or np.isnan(atr_val) or atr_val < min_atr_t * tick_size:
            continue

        cur_session = session_date.iloc[i]
        if cur_session != armed_session:
            armed_dir = 0
            armed_bar_idx = -1
            armed_session = cur_session

        prev_c = last_close.iloc[i]
        if np.isnan(prev_c):
            continue
        prev_c = float(prev_c)
        cur_c = float(bars.iloc[i]["close"])

        # Detect close-side cross of VWAP and arm.
        if prev_c <= vw and cur_c > vw:
            armed_dir = 1
            armed_bar_idx = i
            continue
        if prev_c >= vw and cur_c < vw:
            armed_dir = -1
            armed_bar_idx = i
            continue

        if armed_dir == 0:
            continue

        age = i - armed_bar_idx
        if age < min_hold or age > max_age:
            if age > max_age:
                armed_dir = 0
            continue

        bar = bars.iloc[i]
        bar_open = float(bar["open"])
        displacement_ticks = (cur_c - bar_open) / tick_size
        dist_from_vwap_ticks = abs(cur_c - vw) / tick_size
        if dist_from_vwap_ticks < min_dist_t or dist_from_vwap_ticks > max_dist_t:
            continue

        if armed_dir == 1 and 1 in directions:
            held = cur_c > vw
            continuation = displacement_ticks >= min_cont_t
            if held and continuation:
                rows.append(_make_row(bar, 1, atr_val, vw, "vwap_continuation_long", tick_size))
                armed_dir = 0
        elif armed_dir == -1 and -1 in directions:
            held = cur_c < vw
            continuation = (-displacement_ticks) >= min_cont_t
            if held and continuation:
                rows.append(_make_row(bar, -1, atr_val, vw, "vwap_continuation_short", tick_size))
                armed_dir = 0
        # If price re-crossed back through VWAP during the armed window the
        # cross-branch above already overwrote armed_dir, so no extra handling
        # is needed here.

    return _collect(rows)


# ---------------------------------------------------------------------------
# Failed Reference-Level Breakout (multi-bar)
# ---------------------------------------------------------------------------

def detect_failed_reference_breakout(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Failed breakout of a prior RTH or overnight reference level.

    Distinct from ``reference_sweep_reclaim``, which is a single-bar
    sweep-then-close-back pattern. This detector requires a *confirmed*
    breakout — a bar that closes beyond the level by ``confirmation_ticks``
    — followed within ``max_fail_bars`` by a bar that closes back through
    the level by at least ``fail_close_ticks``. The failure bar is the
    signal bar.

    Parameters
    ----------
    levels               : list[str] prior_high/prior_low/overnight_high/overnight_low
    confirmation_ticks   : float close-beyond confirmation     default 2
    max_fail_bars        : int   bars within which failure must occur  default 8
    fail_close_ticks     : float close-back distance through level     default 1
    direction            : int   1, -1, or 0                  default 0
    min_atr_ticks        : float regime guard                 default 4
    """
    selected = params.get("levels") or [
        "prior_high", "prior_low", "overnight_high", "overnight_low"
    ]
    if isinstance(selected, str):
        selected = [selected]
    conf_ticks = float(params.get("confirmation_ticks", 2.0))
    max_fail_bars = int(params.get("max_fail_bars", 8))
    fail_close_ticks = float(params.get("fail_close_ticks", 1.0))
    direction_c = int(params.get("direction", 0))
    atr_period = int(params.get("atr_period", 14))
    tick_size = float(params.get("tick_size", 0.25))
    min_atr_ticks = float(params.get("min_atr_ticks", 4.0))
    rth_start = int(params.get("rth_start_hour", 7)) * 60 + int(params.get("rth_start_minute", 30))
    rth_end = int(params.get("rth_end_hour", 16)) * 60 + int(params.get("rth_end_minute", 0))

    refs = _reference_levels_by_day(bars, rth_start_minute=rth_start, rth_end_minute=rth_end)
    session_date = _session_date_series(bars)
    minute = _session_minutes(bars)
    atr = _compute_atr(bars["high"], bars["low"], bars["close"], atr_period).shift(1)
    conf_dist = conf_ticks * tick_size
    fail_dist = fail_close_ticks * tick_size
    directions = [1, -1] if direction_c == 0 else [direction_c]
    rows: List[Dict] = []

    # Per-day, per-level breakout state: stores the bar index where a confirmed
    # breakout occurred and the breakout direction. A failure within the window
    # consumes the state.
    # Key: (session_date, level_name, breakout_dir)  Value: breakout_bar_idx
    armed: Dict[Tuple[Any, str, int], int] = {}

    for i in range(1, len(bars)):
        day = session_date.iloc[i]
        if minute.iloc[i] < rth_start or minute.iloc[i] >= rth_end:
            continue
        day_refs = refs.get(day) or {}
        if not day_refs:
            continue
        bar = bars.iloc[i]
        atr_val = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else np.nan
        if np.isnan(atr_val) or atr_val < min_atr_ticks * tick_size:
            continue
        close = float(bar["close"])

        for level_name in selected:
            if level_name not in day_refs:
                continue
            level = float(day_refs[level_name])

            # Breakout up = close above level; breakout down = close below level.
            broke_up = close >= level + conf_dist
            broke_down = close <= level - conf_dist

            key_up = (day, level_name, 1)
            key_down = (day, level_name, -1)

            if broke_up and key_up not in armed:
                armed[key_up] = i
            if broke_down and key_down not in armed:
                armed[key_down] = i

            # Check for failure of an existing up-breakout (signal direction = -1).
            if -1 in directions and key_up in armed:
                bo_idx = armed[key_up]
                age = i - bo_idx
                if age > max_fail_bars:
                    del armed[key_up]
                elif age >= 1 and close <= level - fail_dist:
                    row = _make_row(bar, -1, atr_val, level,
                                    f"{level_name}_failed_breakout", tick_size)
                    row["broken_level_type"] = level_name
                    row["breakout_bars_ago"] = age
                    rows.append(row)
                    del armed[key_up]
                    continue

            # Check for failure of an existing down-breakout (signal direction = +1).
            if 1 in directions and key_down in armed:
                bo_idx = armed[key_down]
                age = i - bo_idx
                if age > max_fail_bars:
                    del armed[key_down]
                elif age >= 1 and close >= level + fail_dist:
                    row = _make_row(bar, 1, atr_val, level,
                                    f"{level_name}_failed_breakout", tick_size)
                    row["broken_level_type"] = level_name
                    row["breakout_bars_ago"] = age
                    rows.append(row)
                    del armed[key_down]

    return _collect(rows)


# ---------------------------------------------------------------------------
# Large Candle Origin Retest
# ---------------------------------------------------------------------------

def detect_large_candle_origin_retest(
    bars: pd.DataFrame,
    params: Dict[str, Any],
) -> pd.DataFrame:
    """
    Continuation entry on a retest of a large candle's origin.

    A "large" candle qualifies when its body (|close - open|) is at least
    ``large_body_mult`` times the rolling average body over
    ``avg_lookback`` prior bars, and at least ``min_body_ticks`` in
    absolute size. The origin is the candle's *open* — for a bullish
    large candle the origin sits at the bottom of the body, and a long
    continuation fires when a subsequent bar within ``max_retest_bars``
    touches the origin (low <= origin + touch_ticks) and closes back
    above it by at least ``min_close_ticks``. Bearish is the mirror.

    Parameters
    ----------
    avg_lookback       : int   bars for rolling avg body       default 20
    large_body_mult    : float multiplier for "large"          default 1.8
    min_body_ticks     : float absolute body floor             default 8
    max_retest_bars    : int   retest window after large bar   default 12
    touch_ticks        : float touch tolerance at origin       default 4
    min_close_ticks    : float close-away requirement          default 2
    direction          : int   1, -1, or 0                     default 0
    min_atr_ticks      : float regime guard                    default 4
    """
    avg_lookback = int(params.get("avg_lookback", 20))
    large_mult = float(params.get("large_body_mult", 1.8))
    min_body_ticks = float(params.get("min_body_ticks", 8.0))
    max_retest_bars = int(params.get("max_retest_bars", 12))
    touch_ticks = float(params.get("touch_ticks", 4.0))
    min_close_ticks = float(params.get("min_close_ticks", 2.0))
    direction_c = int(params.get("direction", 0))
    atr_period = int(params.get("atr_period", 14))
    tick_size = float(params.get("tick_size", 0.25))
    min_atr_ticks = float(params.get("min_atr_ticks", 4.0))

    body = (bars["close"] - bars["open"]).abs()
    # Lag-safe average: exclude current bar.
    avg_body = body.shift(1).rolling(avg_lookback, min_periods=avg_lookback).mean()
    atr = _compute_atr(bars["high"], bars["low"], bars["close"], atr_period).shift(1)
    touch_dist = touch_ticks * tick_size
    min_close_dist = min_close_ticks * tick_size
    min_body_size = min_body_ticks * tick_size
    directions = [1, -1] if direction_c == 0 else [direction_c]
    rows: List[Dict] = []

    # Track active large-candle origins awaiting retest. Each entry:
    # (origin_price, candle_dir, large_bar_idx). At most one active origin per
    # direction at a time — a newer large candle replaces an older one.
    active_long: Optional[Tuple[float, int]] = None   # (origin, bar_idx) for bullish
    active_short: Optional[Tuple[float, int]] = None  # (origin, bar_idx) for bearish

    for i in range(avg_lookback + 1, len(bars)):
        bar = bars.iloc[i]
        atr_val = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else np.nan
        if np.isnan(atr_val) or atr_val < min_atr_ticks * tick_size:
            continue
        avg = float(avg_body.iloc[i]) if not np.isnan(avg_body.iloc[i]) else np.nan
        if np.isnan(avg) or avg <= 0:
            continue

        bar_open = float(bar["open"])
        bar_close = float(bar["close"])
        bar_high = float(bar["high"])
        bar_low = float(bar["low"])
        b = abs(bar_close - bar_open)

        # Check retest of an active long origin (bullish large candle).
        if 1 in directions and active_long is not None:
            origin, bar_idx = active_long
            age = i - bar_idx
            if age > max_retest_bars:
                active_long = None
            elif age >= 1:
                touched = bar_low <= origin + touch_dist
                away = bar_close >= origin + min_close_dist
                if touched and away:
                    row = _make_row(bar, 1, atr_val, origin, "large_candle_origin_long", tick_size)
                    row["bars_since_origin"] = age
                    rows.append(row)
                    active_long = None

        # Check retest of an active short origin (bearish large candle).
        if -1 in directions and active_short is not None:
            origin, bar_idx = active_short
            age = i - bar_idx
            if age > max_retest_bars:
                active_short = None
            elif age >= 1:
                touched = bar_high >= origin - touch_dist
                away = bar_close <= origin - min_close_dist
                if touched and away:
                    row = _make_row(bar, -1, atr_val, origin, "large_candle_origin_short", tick_size)
                    row["bars_since_origin"] = age
                    rows.append(row)
                    active_short = None

        # After scoring, check if *this* bar is itself a new large candle.
        if b >= max(min_body_size, large_mult * avg):
            if bar_close > bar_open:
                active_long = (bar_open, i)
            elif bar_close < bar_open:
                active_short = (bar_open, i)

    return _collect(rows)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

LEVEL_SIGNAL_REGISTRY: Dict[str, Callable] = {
    "swing_level_test":          detect_swing_level_test,
    "consolidation_breakout":    detect_consolidation_breakout,
    "round_number_bounce":       detect_round_number_bounce,
    "vwap_reclaim_reject":       detect_vwap_reclaim_reject,
    "vwap_distance_fade":        detect_vwap_distance_fade,
    "vwap_continuation":         detect_vwap_continuation,
    "prior_session_reaction":    detect_prior_session_level_reaction,
    "reference_sweep_reclaim":   detect_reference_level_sweep_reclaim,
    "failed_reference_breakout": detect_failed_reference_breakout,
    "liquidity_sweep_failure":   detect_liquidity_sweep_failure,
    "large_candle_origin_retest": detect_large_candle_origin_retest,
}

LEVEL_SIGNAL_LABELS: Dict[str, str] = {
    "swing_level_test":          "Swing High/Low Level Test",
    "consolidation_breakout":    "Consolidation Range Breakout",
    "round_number_bounce":       "Round Number Level Bounce",
    "vwap_reclaim_reject":       "VWAP Reclaim / Reject",
    "vwap_distance_fade":        "VWAP Distance Overextension Fade",
    "vwap_continuation":         "VWAP Continuation After Reclaim",
    "prior_session_reaction":    "Prior / Overnight Level Reaction",
    "reference_sweep_reclaim":   "Prior / Overnight Sweep Reclaim",
    "failed_reference_breakout": "Failed Prior / Overnight Breakout",
    "liquidity_sweep_failure":   "Liquidity Sweep Failure",
    "large_candle_origin_retest": "Large Candle Origin Retest",
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
