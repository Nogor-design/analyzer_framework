from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    return s.ewm(span=int(period), adjust=False, min_periods=int(period)).mean()


def atr_wilder(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Wilder ATR using EWM(alpha=1/period) on True Range.
    Requires columns: high, low, close
    """
    h = pd.to_numeric(bars.get("high"), errors="coerce")
    l = pd.to_numeric(bars.get("low"), errors="coerce")
    c = pd.to_numeric(bars.get("close"), errors="coerce")
    prev_c = c.shift(1)

    tr = pd.concat([(h - l).abs(), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / int(period), adjust=False, min_periods=int(period)).mean()


def ema_slope(series: pd.Series, lookback: int = 3) -> pd.Series:
    """
    Simple slope proxy: difference over N bars.
    """
    s = pd.to_numeric(series, errors="coerce")
    return s - s.shift(int(lookback))


def session_vwap(bars: pd.DataFrame, price_col: str = "close", vol_col: str = "volume") -> pd.Series:
    """
    Pragmatic VWAP anchored by local calendar date (America/Denver assumed already).
    bars requires: dt, price_col, vol_col.
    """
    dt = pd.to_datetime(bars.get("dt"), errors="coerce")
    px = pd.to_numeric(bars.get(price_col), errors="coerce")
    vol = pd.to_numeric(bars.get(vol_col), errors="coerce")

    day = dt.dt.date
    pv = px * vol
    out = (pv.groupby(day).cumsum() / vol.groupby(day).cumsum()).replace([np.inf, -np.inf], np.nan)
    return out
