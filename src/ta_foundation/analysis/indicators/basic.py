from __future__ import annotations

import numpy as np
import pandas as pd

from ta_foundation.analysis.indicators.registry import DEFAULT_INDICATORS


def ema(bars: pd.DataFrame, params: dict) -> pd.DataFrame:
    period = int(params.get("period", 20))
    col = str(params.get("col", "close"))
    out_col = str(params.get("out", f"ema_{period}_{col}"))

    if col not in bars.columns:
        return bars
    s = pd.to_numeric(bars[col], errors="coerce")
    bars[out_col] = s.ewm(span=period, adjust=False, min_periods=period).mean()
    return bars


def rsi(bars: pd.DataFrame, params: dict) -> pd.DataFrame:
    period = int(params.get("period", 14))
    col = str(params.get("col", "close"))
    out_col = str(params.get("out", f"rsi_{period}_{col}"))

    if col not in bars.columns:
        return bars
    s = pd.to_numeric(bars[col], errors="coerce")
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    # Wilder-style smoothing via EWM alpha=1/period
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    bars[out_col] = 100.0 - (100.0 / (1.0 + rs))
    return bars


def atr(bars: pd.DataFrame, params: dict) -> pd.DataFrame:
    period = int(params.get("period", 14))
    out_col = str(params.get("out", f"atr_{period}"))

    req = {"high", "low", "close"}
    if not req.issubset(set(bars.columns)):
        return bars

    h = pd.to_numeric(bars["high"], errors="coerce")
    l = pd.to_numeric(bars["low"], errors="coerce")
    c = pd.to_numeric(bars["close"], errors="coerce")
    prev_c = c.shift(1)

    tr = pd.concat([(h - l).abs(), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    bars[out_col] = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return bars


def session_vwap(bars: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Session VWAP anchored by local date (America/Denver).
    This is a pragmatic default; you can later refine to actual futures session boundaries.
    """
    out_col = str(params.get("out", "vwap_session"))
    price_col = str(params.get("price_col", "close"))
    vol_col = str(params.get("vol_col", "volume"))

    if "dt" not in bars.columns or price_col not in bars.columns or vol_col not in bars.columns:
        return bars

    dt = pd.to_datetime(bars["dt"], errors="coerce")
    px = pd.to_numeric(bars[price_col], errors="coerce")
    vol = pd.to_numeric(bars[vol_col], errors="coerce")

    # Local date buckets (tz-aware dt assumed America/Denver already)
    day = dt.dt.date
    pv = px * vol

    bars[out_col] = (pv.groupby(day).cumsum() / vol.groupby(day).cumsum()).replace([np.inf, -np.inf], np.nan)
    return bars


# Register defaults
DEFAULT_INDICATORS.register("ema", ema)
DEFAULT_INDICATORS.register("rsi", rsi)
DEFAULT_INDICATORS.register("atr", atr)
DEFAULT_INDICATORS.register("session_vwap", session_vwap)
