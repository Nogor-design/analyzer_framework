from __future__ import annotations

"""
MA Feature Engine
==================
Computes moving average features for the MA cross/pullback strategy.

Features added to each bar (all lag-safe — use only prior-bar data):
  ma_{period}_{type}        : moving average value (ema or sma)
  ma_{period}_{type}_dist   : (close - ma) / atr — normalised distance
  ma_{period}_{type}_slope  : (ma[t-1] - ma[t-2]) / atr — slope direction/magnitude
  ma_{period}_{type}_above  : 1 if close > ma, -1 if close < ma (trend side)
  atr_{atr_period}          : ATR used for normalisation

All computations are shifted so bar[i] features only use bar[i-1] and earlier data.
"""

from typing import Any, Dict, List

import numpy as np
import pandas as pd


DEFAULT_MA_FEATURE_CONFIG: Dict[str, Any] = {
    "periods":     [9, 20, 50],
    "ma_types":    ["ema", "sma"],
    "atr_period":  14,
    "tick_size":   0.25,
}


def _compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _compute_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def compute_ma_features(bars: pd.DataFrame, config: Dict[str, Any] = None) -> pd.DataFrame:
    """
    Append MA features to bars and return the enriched DataFrame.

    Parameters
    ----------
    bars   : OHLCV bars with columns [dt, open, high, low, close, volume]
    config : optional override for DEFAULT_MA_FEATURE_CONFIG

    Returns
    -------
    Copy of bars with MA feature columns appended.
    """
    cfg = {**DEFAULT_MA_FEATURE_CONFIG, **(config or {})}
    periods:    List[int] = [int(p) for p in cfg["periods"]]
    ma_types:   List[str] = [str(t).lower() for t in cfg["ma_types"]]
    atr_period: int       = int(cfg["atr_period"])

    out = bars.copy()
    close = out["close"]
    high  = out["high"]
    low   = out["low"]

    # ATR (lag-safe: use shift(1) for features)
    atr_raw = _compute_atr(high, low, close, atr_period)
    atr_col = f"atr_{atr_period}"
    out[atr_col] = atr_raw.shift(1)

    safe_atr = atr_raw.shift(1).replace(0, np.nan)

    for period in periods:
        for ma_type in ma_types:
            col = f"ma_{period}_{ma_type}"

            if ma_type == "ema":
                ma_raw = _compute_ema(close, period)
            else:
                ma_raw = _compute_sma(close, period)

            # Lag-safe: shift(1) so bar[i] sees ma value up to bar[i-1]
            ma_lagged = ma_raw.shift(1)
            out[col] = ma_lagged

            # Normalised distance: (close - ma) / atr
            out[f"{col}_dist"] = ((close - ma_lagged) / safe_atr).round(4)

            # Slope: (ma[t-1] - ma[t-2]) / atr  — tells direction and steepness
            out[f"{col}_slope"] = ((ma_lagged - ma_lagged.shift(1)) / safe_atr).round(4)

            # Which side of MA: +1 (close > ma), -1 (close < ma), 0 (on ma/NaN)
            above_raw = np.sign(close - ma_lagged).fillna(0).astype(int)
            out[f"{col}_above"] = above_raw

    return out


def ma_feature_column_names(config: Dict[str, Any] = None) -> List[str]:
    """Return list of column names added by compute_ma_features()."""
    cfg = {**DEFAULT_MA_FEATURE_CONFIG, **(config or {})}
    periods  = [int(p) for p in cfg["periods"]]
    ma_types = [str(t).lower() for t in cfg["ma_types"]]
    atr_col  = f"atr_{cfg['atr_period']}"

    cols = [atr_col]
    for period in periods:
        for ma_type in ma_types:
            col = f"ma_{period}_{ma_type}"
            cols += [col, f"{col}_dist", f"{col}_slope", f"{col}_above"]
    return cols
