from __future__ import annotations

"""
Bollinger Band Feature Engine
================================
Computes Bollinger Band features (all lag-safe — shifted to prior bar).

Columns added per (period, n_std) combination:
  bb_{p}_{s}_mid      — SMA middle band
  bb_{p}_{s}_upper    — upper band  (mid + n_std * stddev)
  bb_{p}_{s}_lower    — lower band  (mid - n_std * stddev)
  bb_{p}_{s}_width    — (upper - lower) / mid  — normalised bandwidth
  bb_{p}_{s}_pct_b    — %B: (close - lower) / (upper - lower)  in [0,1] typically
  bb_{p}_{s}_z        — (close - mid) / stddev — standard-score position
  bb_{p}_{s}_squeeze  — 1 if bandwidth < squeeze_threshold, else 0
  atr_{atr_period}    — ATR (shared across all BB combos)

%B interpretation:
  %B > 1.0  → price above upper band  (overbought / breakout)
  %B = 0.5  → price at middle band
  %B < 0.0  → price below lower band  (oversold / breakdown)
"""

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd


DEFAULT_BB_FEATURE_CONFIG: Dict[str, Any] = {
    "periods":           [20],
    "n_stds":            [2.0],
    "atr_period":        14,
    "tick_size":         0.25,
    "squeeze_threshold": 0.04,   # bandwidth < 4% of mid → squeeze
}


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def compute_bb_features(bars: pd.DataFrame, config: Dict[str, Any] = None) -> pd.DataFrame:
    """
    Append Bollinger Band features to bars and return the enriched DataFrame.

    Parameters
    ----------
    bars   : OHLCV DataFrame with columns [dt, open, high, low, close, volume]
    config : optional override for DEFAULT_BB_FEATURE_CONFIG

    Returns
    -------
    Copy of bars with BB feature columns appended (lag-safe, shifted by 1 bar).
    """
    cfg = {**DEFAULT_BB_FEATURE_CONFIG, **(config or {})}
    periods:       List[int]   = [int(p)   for p in cfg["periods"]]
    n_stds:        List[float] = [float(s) for s in cfg["n_stds"]]
    atr_period:    int         = int(cfg["atr_period"])
    squeeze_th:    float       = float(cfg["squeeze_threshold"])

    out = bars.copy()
    close = out["close"]
    high  = out["high"]
    low   = out["low"]

    # ATR (lagged)
    atr_raw = _compute_atr(high, low, close, atr_period)
    atr_col = f"atr_{atr_period}"
    out[atr_col] = atr_raw.shift(1)

    for period in periods:
        # Rolling stats (raw, unlagged)
        mid_raw    = close.rolling(period).mean()
        std_raw    = close.rolling(period).std(ddof=1)

        for n_std in n_stds:
            # Build suffix like "20_2.0" or "20_2"
            std_str = str(int(n_std)) if n_std == int(n_std) else str(n_std)
            pfx = f"bb_{period}_{std_str}"

            upper_raw = mid_raw + n_std * std_raw
            lower_raw = mid_raw - n_std * std_raw
            band_w    = (upper_raw - lower_raw) / mid_raw.replace(0, np.nan)

            # Lag everything by 1 bar
            mid   = mid_raw.shift(1)
            upper = upper_raw.shift(1)
            lower = lower_raw.shift(1)
            std_l = std_raw.shift(1)
            bw    = band_w.shift(1)

            safe_range = (upper - lower).replace(0, np.nan)
            safe_std   = std_l.replace(0, np.nan)

            out[f"{pfx}_mid"]    = mid
            out[f"{pfx}_upper"]  = upper
            out[f"{pfx}_lower"]  = lower
            out[f"{pfx}_width"]  = bw.round(6)
            out[f"{pfx}_pct_b"]  = ((close - lower) / safe_range).round(4)
            out[f"{pfx}_z"]      = ((close - mid) / safe_std).round(4)
            out[f"{pfx}_squeeze"] = (bw < squeeze_th).astype(int)

    return out


def bb_feature_column_names(config: Dict[str, Any] = None) -> List[str]:
    """Return list of column names added by compute_bb_features()."""
    cfg = {**DEFAULT_BB_FEATURE_CONFIG, **(config or {})}
    periods = [int(p) for p in cfg["periods"]]
    n_stds  = [float(s) for s in cfg["n_stds"]]
    cols    = [f"atr_{cfg['atr_period']}"]
    for p in periods:
        for s in n_stds:
            ss = str(int(s)) if s == int(s) else str(s)
            pfx = f"bb_{p}_{ss}"
            cols += [f"{pfx}_mid", f"{pfx}_upper", f"{pfx}_lower",
                     f"{pfx}_width", f"{pfx}_pct_b", f"{pfx}_z", f"{pfx}_squeeze"]
    return cols
