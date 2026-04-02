from __future__ import annotations

"""
Entry Signal Emitter
=====================
Converts a patterns signals DataFrame into a pending-entries DataFrame
for one of three timing modes:

  next_open      — entry at the open of the bar immediately following the signal.
                   Guaranteed fill; entry_price is known immediately.

  break_extreme  — entry via stop-limit order just beyond the signal bar's
                   high (long) or low (short).  Whether the order fills is
                   determined by the outcome simulator.

  body_midpoint  — entry via limit order at the midpoint of the signal bar's body.
                   Models a retracement entry for better R:R.  Fill determined
                   by simulator.

Output columns (all timing modes)
----------------------------------
  signal_dt       datetime of the signal bar
  direction       1 (long) or -1 (short)
  timing_mode     "next_open" | "break_extreme" | "body_midpoint"
  entry_price     filled price (next_open only; NaN for limit modes)
  limit_price     limit/stop-limit trigger price (NaN for next_open)
  signal_open     signal bar open
  signal_high     signal bar high
  signal_low      signal bar low
  signal_close    signal bar close
  signal_body     signal bar body (price units)
  signal_atr      ATR at signal bar (for outcome simulator)
  + all candle feature columns carried forward from patterns output
"""

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _add_standard_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Rename / alias OHLC columns to signal_* for clarity downstream."""
    rename = {}
    for col, alias in (
        ("dt",    "signal_dt"),
        ("open",  "signal_open"),
        ("high",  "signal_high"),
        ("low",   "signal_low"),
        ("close", "signal_close"),
        ("body",  "signal_body"),
        ("atr",   "signal_atr"),
    ):
        if col in df.columns and alias not in df.columns:
            rename[col] = alias
    return df.rename(columns=rename)


def _build_lookup(bars: pd.DataFrame) -> pd.DataFrame:
    """
    Build a sorted lookup table from the full bars DataFrame.
    Returns a DataFrame with reset index for positional access.
    """
    return bars.sort_values("dt").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Public API — one function per timing mode
# ---------------------------------------------------------------------------

def emit_next_open(
    signals_df: pd.DataFrame,
    bars: pd.DataFrame,
    params: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """
    Entry at the open of the bar immediately following the signal bar.

    Parameters
    ----------
    signals_df : output of detect_pattern() — must have 'dt', 'direction' columns
    bars       : full bars DataFrame for the same timeframe (must have 'dt', 'open')
    params     : unused; accepted for uniform call signature

    Returns
    -------
    Pending-entries DataFrame.  entry_price is the next bar's open (float).
    Rows where no next bar exists are dropped.
    """
    if signals_df is None or signals_df.empty:
        return pd.DataFrame()
    if "dt" not in signals_df.columns or "dt" not in bars.columns:
        return pd.DataFrame()

    lookup = _build_lookup(bars[["dt", "open"]].copy())
    dt_to_idx = {row["dt"]: i for i, row in lookup.iterrows()}

    out = _add_standard_cols(signals_df.copy())
    sig_dt_col = "signal_dt" if "signal_dt" in out.columns else "dt"

    entry_prices: list = []
    entry_times:  list = []
    valid_rows:   list = []

    for _, row in out.iterrows():
        sig_dt = row[sig_dt_col]
        idx = dt_to_idx.get(sig_dt)
        if idx is None:
            # Try fuzzy match: nearest bar at-or-before signal dt
            bar_dts = lookup["dt"]
            candidates = bar_dts[bar_dts <= sig_dt]
            if candidates.empty:
                continue
            idx = candidates.index[-1]
        next_idx = idx + 1
        if next_idx >= len(lookup):
            continue  # no next bar (signal at end of data)
        next_bar = lookup.iloc[next_idx]
        entry_prices.append(float(next_bar["open"]))
        entry_times.append(next_bar["dt"])
        valid_rows.append(row)

    if not valid_rows:
        return pd.DataFrame()

    result = pd.DataFrame(valid_rows).reset_index(drop=True)
    result["entry_price"]  = entry_prices
    result["entry_time"]   = entry_times
    result["limit_price"]  = np.nan
    result["timing_mode"]  = "next_open"
    result["fill_bars"]    = 0
    return result


def emit_break_extreme(
    signals_df: pd.DataFrame,
    params: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """
    Entry via stop-limit order just beyond the signal bar's extreme.

    Long:  limit_price = signal_bar.high + buffer_ticks * tick_size
    Short: limit_price = signal_bar.low  - buffer_ticks * tick_size

    Parameters
    ----------
    signals_df   : output of detect_pattern()
    params       : dict with keys:
                     tick_size       float  default 0.25
                     buffer_ticks    float  default 1.0
                     fill_timeout_bars int  default 3

    Returns
    -------
    Pending-entries DataFrame.  entry_price = NaN (filled by simulator).
    """
    if signals_df is None or signals_df.empty:
        return pd.DataFrame()

    p = params or {}
    tick_size    = float(p.get("tick_size", 0.25))
    buffer_ticks = float(p.get("buffer_ticks", 1.0))
    buffer_price = buffer_ticks * tick_size

    out = _add_standard_cols(signals_df.copy())

    high_col = "signal_high" if "signal_high" in out.columns else "high"
    low_col  = "signal_low"  if "signal_low"  in out.columns else "low"

    limit_prices = np.where(
        out["direction"] == 1,
        out[high_col].astype(float) + buffer_price,
        out[low_col].astype(float)  - buffer_price,
    )

    out["limit_price"]        = limit_prices
    out["entry_price"]        = np.nan
    out["entry_time"]         = pd.NaT
    out["timing_mode"]        = "break_extreme"
    out["fill_bars"]          = np.nan
    out["fill_timeout_bars"]  = int(p.get("fill_timeout_bars", 3))
    return out.reset_index(drop=True)


def emit_body_midpoint(
    signals_df: pd.DataFrame,
    params: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """
    Entry via limit order at the midpoint of the signal bar's body.

    Midpoint = (open + close) / 2  regardless of candle direction.
    For a long entry, this means buying a dip back into the body.
    For a short entry, this means selling a rally back into the body.

    Parameters
    ----------
    signals_df   : output of detect_pattern()
    params       : dict with keys:
                     fill_timeout_bars int  default 5

    Returns
    -------
    Pending-entries DataFrame.  entry_price = NaN (filled by simulator).
    """
    if signals_df is None or signals_df.empty:
        return pd.DataFrame()

    p = params or {}

    out = _add_standard_cols(signals_df.copy())

    open_col  = "signal_open"  if "signal_open"  in out.columns else "open"
    close_col = "signal_close" if "signal_close" in out.columns else "close"

    midpoints = (out[open_col].astype(float) + out[close_col].astype(float)) / 2.0

    out["limit_price"]       = midpoints
    out["entry_price"]       = np.nan
    out["entry_time"]        = pd.NaT
    out["timing_mode"]       = "body_midpoint"
    out["fill_bars"]         = np.nan
    out["fill_timeout_bars"] = int(p.get("fill_timeout_bars", 5))
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Convenience dispatcher
# ---------------------------------------------------------------------------

def emit_entries(
    signals_df: pd.DataFrame,
    timing_mode: str,
    bars: Optional[pd.DataFrame] = None,
    params: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """
    Dispatch to the correct emitter for *timing_mode*.

    Parameters
    ----------
    signals_df  : output of detect_pattern()
    timing_mode : "next_open" | "break_extreme" | "body_midpoint"
    bars        : required for "next_open" mode
    params      : mode-specific params

    Returns
    -------
    Pending-entries DataFrame.
    """
    if timing_mode == "next_open":
        if bars is None:
            raise ValueError("emit_entries: 'bars' is required for next_open timing mode")
        return emit_next_open(signals_df, bars, params)
    elif timing_mode == "break_extreme":
        return emit_break_extreme(signals_df, params)
    elif timing_mode == "body_midpoint":
        return emit_body_midpoint(signals_df, params)
    else:
        raise ValueError(f"emit_entries: unknown timing_mode '{timing_mode}'")
