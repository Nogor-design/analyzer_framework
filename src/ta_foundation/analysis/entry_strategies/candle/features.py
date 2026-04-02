from __future__ import annotations

"""
Candle Feature Engine
=====================
Appends candle anatomy and comparison statistics to a bars DataFrame.
All computations are lag-safe — no look-ahead bias.

Added columns
-------------
Anatomy (price units):
  body, upper_wick, lower_wick, total_range
  is_bullish                  close > open
  size_ticks, body_ticks      anatomy / tick_size

Ratios:
  body_to_range               body / total_range          (0..1)
  upper_wick_to_range         upper_wick / total_range
  lower_wick_to_range         lower_wick / total_range
  upper_wick_to_body          upper_wick / body           (NaN when body < min_body_price)
  lower_wick_to_body          lower_wick / body

Rolling-average comparison (one pair per n in size_lookbacks):
  size_vs_roll_{n}            total_range / rolling_mean_{n}(total_range, shift 1)
  body_vs_roll_{n}            body        / rolling_mean_{n}(body, shift 1)

Session-average comparison (running mean within each calendar day, shift 1):
  size_vs_session             total_range / session_running_mean(total_range)
  body_vs_session             body        / session_running_mean(body)

ATR-normalised:
  atr                         ATR-14 in price units
  size_vs_atr                 total_range / atr
  body_vs_atr                 body        / atr
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "size_lookbacks": [5, 10, 20],
    "atr_period": 14,
    "tick_size": 0.25,
    "min_body_ticks": 1,       # body < this → wick_to_body columns become NaN
    "session_start_hour": 7,   # America/Denver hour that begins a new session
}

# Columns that must exist on the input bars DataFrame
_REQUIRED_COLS = ("open", "high", "low", "close")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """True Average Range computed with Wilder rolling mean."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _session_running_mean(
    series: pd.Series,
    dt_series: pd.Series,
) -> pd.Series:
    """
    Running mean of *series* within each calendar day (America/Denver), shifted
    by 1 bar so each value uses only data prior to the current bar.

    Returns a pd.Series aligned to series.index.
    """
    dt = pd.to_datetime(dt_series)
    if dt.dt.tz is not None:
        local_dt = dt.dt.tz_convert("America/Denver")
    else:
        local_dt = dt

    session_date = local_dt.dt.date.astype(str)
    result = pd.Series(np.nan, index=series.index, dtype=float)

    for _day, grp_idx in series.groupby(session_date).groups.items():
        grp = series.loc[grp_idx]
        # expanding().mean() at position i = mean of positions [0..i]
        # shift(1)          at position i = mean of positions [0..i-1]  ← lag-safe
        exp_mean = grp.expanding().mean().shift(1)
        result.loc[grp_idx] = exp_mean.values

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_candle_features(
    bars: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """
    Append candle feature columns to *bars*.  Returns a copy.

    Parameters
    ----------
    bars   : DataFrame with at minimum columns dt, open, high, low, close.
    config : Optional override dict (merged with DEFAULT_CONFIG).

    Returns
    -------
    Copy of *bars* with all feature columns appended.
    """
    cfg: Dict[str, Any] = {**DEFAULT_CONFIG, **(config or {})}

    tick_size      = float(cfg["tick_size"])
    min_body_price = float(cfg["min_body_ticks"]) * tick_size
    lookbacks: List[int] = [int(n) for n in cfg["size_lookbacks"]]
    atr_period     = int(cfg["atr_period"])

    for col in _REQUIRED_COLS:
        if col not in bars.columns:
            raise ValueError(
                f"compute_candle_features: required column '{col}' not found in bars"
            )

    df = bars.copy().reset_index(drop=True)

    o = df["open"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)

    # ------------------------------------------------------------------
    # 1. Anatomy
    # ------------------------------------------------------------------
    body        = (c - o).abs().clip(lower=0.0)
    upper_wick  = (h - pd.concat([c, o], axis=1).max(axis=1)).clip(lower=0.0)
    lower_wick  = (pd.concat([c, o], axis=1).min(axis=1) - l).clip(lower=0.0)
    total_range = (h - l).clip(lower=0.0)

    df["is_bullish"]   = (c >= o)        # >= treats doji as bullish (neutral behaviour)
    df["body"]         = body
    df["upper_wick"]   = upper_wick
    df["lower_wick"]   = lower_wick
    df["total_range"]  = total_range
    df["size_ticks"]   = (total_range / tick_size).round(2)
    df["body_ticks"]   = (body / tick_size).round(2)

    # ------------------------------------------------------------------
    # 2. Range-based ratios  (0 .. 1)
    # ------------------------------------------------------------------
    safe_range = total_range.where(total_range > 0, other=np.nan)

    df["body_to_range"]       = (body / safe_range).clip(0.0, 1.0)
    df["upper_wick_to_range"] = (upper_wick / safe_range).clip(0.0, 1.0)
    df["lower_wick_to_range"] = (lower_wick / safe_range).clip(0.0, 1.0)

    # ------------------------------------------------------------------
    # 3. Body-based ratios  (NaN when body is too small)
    # ------------------------------------------------------------------
    safe_body = body.where(body >= min_body_price, other=np.nan)

    df["upper_wick_to_body"] = upper_wick / safe_body
    df["lower_wick_to_body"] = lower_wick / safe_body

    # ------------------------------------------------------------------
    # 4. Rolling-average comparison (one pair per lookback)
    # ------------------------------------------------------------------
    for n in lookbacks:
        # shift(1): exclude the current bar from its own average (lag-safe)
        avg_range = total_range.rolling(n, min_periods=1).mean().shift(1)
        avg_body  = body.rolling(n, min_periods=1).mean().shift(1)

        safe_avg_range = avg_range.where(avg_range > 0, other=np.nan)
        safe_avg_body  = avg_body.where(avg_body > 0, other=np.nan)

        df[f"size_vs_roll_{n}"] = total_range / safe_avg_range
        df[f"body_vs_roll_{n}"] = body / safe_avg_body

    # ------------------------------------------------------------------
    # 5. Session-average comparison
    # ------------------------------------------------------------------
    if "dt" in df.columns:
        try:
            sess_avg_range = _session_running_mean(total_range, df["dt"])
            sess_avg_body  = _session_running_mean(body, df["dt"])

            safe_sess_range = sess_avg_range.where(sess_avg_range > 0, other=np.nan)
            safe_sess_body  = sess_avg_body.where(sess_avg_body > 0, other=np.nan)

            df["size_vs_session"] = total_range / safe_sess_range
            df["body_vs_session"] = body / safe_sess_body
        except Exception:
            df["size_vs_session"] = np.nan
            df["body_vs_session"] = np.nan
    else:
        df["size_vs_session"] = np.nan
        df["body_vs_session"] = np.nan

    # ------------------------------------------------------------------
    # 6. ATR-normalised
    # ------------------------------------------------------------------
    atr = _compute_atr(h, l, c, atr_period)
    df["atr"] = atr
    safe_atr = atr.where(atr > 0, other=np.nan)

    df["size_vs_atr"] = total_range / safe_atr
    df["body_vs_atr"] = body / safe_atr

    return df


# ---------------------------------------------------------------------------
# Convenience: list all feature column names that will be added
# ---------------------------------------------------------------------------

def feature_column_names(config: Optional[Dict[str, Any]] = None) -> List[str]:
    """
    Return the list of column names that compute_candle_features() will append,
    given a config dict.  Useful for building feature schemas without running
    the full computation.
    """
    cfg: Dict[str, Any] = {**DEFAULT_CONFIG, **(config or {})}
    lookbacks: List[int] = [int(n) for n in cfg["size_lookbacks"]]

    cols = [
        "is_bullish",
        "body", "upper_wick", "lower_wick", "total_range",
        "size_ticks", "body_ticks",
        "body_to_range", "upper_wick_to_range", "lower_wick_to_range",
        "upper_wick_to_body", "lower_wick_to_body",
    ]
    for n in lookbacks:
        cols += [f"size_vs_roll_{n}", f"body_vs_roll_{n}"]
    cols += [
        "size_vs_session", "body_vs_session",
        "atr", "size_vs_atr", "body_vs_atr",
    ]
    return cols
