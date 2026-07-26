from __future__ import annotations

"""
Pre-Market Session Features
============================
Computes session-level statistics for each day's pre-market window and
merges them onto each bar within that window.

These are *context* features — they describe the CHARACTER of the pre-market
session (trend direction, range size, position within range, momentum) rather
than individual bar anatomy.  The candle anatomy features (body, wicks, ATR,
etc.) are computed separately by ``candle.features.compute_candle_features``.

Usage
-----
    from ta_foundation.analysis.entry_strategies.premarket.features import (
        compute_premarket_session_features,
    )

    # enriched already has candle features (body, atr, etc.)
    enriched_pm = compute_premarket_session_features(enriched_pm_bars)

Added columns (same value for every bar within a day's premarket window)
------------------------------------------------------------------------
  pm_session_direction   1 = PM was net bullish, -1 = net bearish
                         (last PM bar close vs first PM bar open)

  pm_range_ticks         Full PM range in ticks: max(high) - min(low)
                         across all premarket bars this day.

  pm_range_vs_atr        pm_range / ATR of the LAST premarket bar.
                         > 1.0 = premarket already moved more than 1 ATR.

  pm_close_vs_range      Position of the last PM bar's close within PM range.
                         0.0 = closed at the PM low; 1.0 = at the PM high.
                         > 0.7 = bullish close; < 0.3 = bearish close.

  pm_bull_bars_pct       Fraction of PM bars that were bullish (close >= open).
                         0.5 = balanced; > 0.7 = bullish trend; < 0.3 = bearish.

  pm_n_bars              Count of bars in this day's premarket window.

  pm_momentum_bars       Signed count of consecutive same-direction bars at the
                         END of the premarket window.  E.g. +3 = last 3 bars were
                         all bullish; -2 = last 2 bars were all bearish.
                         Large absolute value = strong momentum going into open.

  pm_compression         1 if pm_range_ticks < pm_atr_avg (tight, coiled),
                         0 otherwise.  Coiled ranges often produce explosive opens.

  pm_atr_avg             Average ATR across the premarket bars (in ticks).
                         Lower = quieter premarket; higher = volatile premarket.
"""

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_PM_FEATURE_CONFIG: Dict[str, Any] = {
    "tick_size": 0.25,
    "pm_start_hour": 6,
    "pm_start_minute": 0,
    "pm_end_hour": 7,
    "pm_end_minute": 29,    # last premarket minute bar (07:29 = last 1m bar before 07:30)
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _local_hour_minute(dt_series: pd.Series):
    """Return (hour Series, minute Series) in America/Denver time."""
    s = pd.to_datetime(dt_series)
    if s.dt.tz is not None:
        local = s.dt.tz_convert("America/Denver")
    else:
        local = s
    return local.dt.hour, local.dt.minute


def _is_pm_bar(dt_series: pd.Series, cfg: Dict[str, Any]) -> pd.Series:
    """Boolean mask: True if the bar falls within the PM window."""
    hour, minute = _local_hour_minute(dt_series)
    sh, sm = cfg["pm_start_hour"], cfg["pm_start_minute"]
    eh, em = cfg["pm_end_hour"],   cfg["pm_end_minute"]

    if sm > 0:
        start_mask = (hour > sh) | ((hour == sh) & (minute >= sm))
    else:
        start_mask = hour >= sh

    if em < 59:
        end_mask = (hour < eh) | ((hour == eh) & (minute <= em))
    else:
        end_mask = hour <= eh

    return start_mask & end_mask


def _calendar_date(dt_series: pd.Series) -> pd.Series:
    """Return date string 'YYYY-MM-DD' in Denver time for grouping."""
    s = pd.to_datetime(dt_series)
    if s.dt.tz is not None:
        local = s.dt.tz_convert("America/Denver")
    else:
        local = s
    return local.dt.date.astype(str)


# ---------------------------------------------------------------------------
# Per-day aggregation
# ---------------------------------------------------------------------------

def _compute_day_stats(group: pd.DataFrame, tick_size: float) -> Dict[str, Any]:
    """
    Compute session-level stats for one day's premarket bars.

    Parameters
    ----------
    group     : rows from one calendar day's premarket window, sorted by dt.
    tick_size : instrument tick size.

    Returns
    -------
    dict of feature values (scalars).
    """
    if group.empty:
        return {}

    high  = group["high"].astype(float)
    low   = group["low"].astype(float)
    close = group["close"].astype(float)
    opens = group["open"].astype(float)
    is_bull = (close >= opens)

    pm_high = float(high.max())
    pm_low  = float(low.min())
    pm_range_price = max(pm_high - pm_low, 0.0)
    pm_range_ticks = pm_range_price / tick_size

    # Session direction: last close vs first open
    first_open  = float(opens.iloc[0])
    last_close  = float(close.iloc[-1])
    pm_direction = 1 if last_close >= first_open else -1

    # Close position within range
    if pm_range_price > 0:
        pm_close_vs_range = (last_close - pm_low) / pm_range_price
    else:
        pm_close_vs_range = 0.5

    # Bullish bar fraction
    n = len(group)
    pm_bull_bars_pct = float(is_bull.sum()) / n

    # Consecutive momentum at end of session
    directions = np.where(is_bull, 1, -1)
    last_dir = directions[-1]
    count = 0
    for d in reversed(directions):
        if d == last_dir:
            count += 1
        else:
            break
    pm_momentum_bars = count * last_dir

    # ATR average (use existing atr column if present, else compute from range)
    if "atr" in group.columns:
        atr_vals = pd.to_numeric(group["atr"], errors="coerce").dropna()
        pm_atr_avg = float(atr_vals.mean() / tick_size) if len(atr_vals) > 0 else 0.0
    else:
        bar_ranges = (high - low)
        pm_atr_avg = float(bar_ranges.mean() / tick_size) if len(bar_ranges) > 0 else 0.0

    pm_range_vs_atr = pm_range_ticks / pm_atr_avg if pm_atr_avg > 0 else 0.0
    pm_compression  = 1 if pm_range_ticks < pm_atr_avg else 0

    return {
        "pm_session_direction":  pm_direction,
        "pm_range_ticks":        round(pm_range_ticks, 2),
        "pm_range_vs_atr":       round(pm_range_vs_atr, 3),
        "pm_close_vs_range":     round(float(pm_close_vs_range), 3),
        "pm_bull_bars_pct":      round(pm_bull_bars_pct, 3),
        "pm_n_bars":             n,
        "pm_momentum_bars":      int(pm_momentum_bars),
        "pm_compression":        pm_compression,
        "pm_atr_avg":            round(pm_atr_avg, 2),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_premarket_session_features(
    bars: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """
    Compute pre-market session-level features and attach them to each bar.

    Parameters
    ----------
    bars   : DataFrame of premarket bars (must have dt, open, high, low, close).
             Typically produced by slicing the full 1m bars to the PM window.
    config : Optional override dict (merged with DEFAULT_PM_FEATURE_CONFIG).

    Returns
    -------
    Copy of *bars* with premarket session feature columns added.
    All added columns have prefix ``pm_``.
    """
    cfg: Dict[str, Any] = {**DEFAULT_PM_FEATURE_CONFIG, **(config or {})}
    tick_size = float(cfg["tick_size"])

    if bars is None or bars.empty:
        return bars

    required = ("dt", "open", "high", "low", "close")
    for col in required:
        if col not in bars.columns:
            raise ValueError(
                f"compute_premarket_session_features: required column '{col}' not found"
            )

    df = bars.copy().reset_index(drop=True)

    # Feature column names — initialize to NaN
    feat_cols = [
        "pm_session_direction", "pm_range_ticks", "pm_range_vs_atr",
        "pm_close_vs_range", "pm_bull_bars_pct", "pm_n_bars",
        "pm_momentum_bars", "pm_compression", "pm_atr_avg",
    ]
    for col in feat_cols:
        df[col] = np.nan

    # Group by calendar date and compute stats
    dates = _calendar_date(df["dt"])
    df["_pm_date"] = dates

    for day, idx in df.groupby("_pm_date").groups.items():
        day_bars = df.loc[idx].sort_values("dt")
        stats = _compute_day_stats(day_bars, tick_size)
        if not stats:
            continue
        for col, val in stats.items():
            df.loc[idx, col] = val

    df.drop(columns=["_pm_date"], inplace=True)

    return df


# ---------------------------------------------------------------------------
# Convenience: list added column names
# ---------------------------------------------------------------------------

PM_FEATURE_COLUMNS = [
    "pm_session_direction",
    "pm_range_ticks",
    "pm_range_vs_atr",
    "pm_close_vs_range",
    "pm_bull_bars_pct",
    "pm_n_bars",
    "pm_momentum_bars",
    "pm_compression",
    "pm_atr_avg",
]
