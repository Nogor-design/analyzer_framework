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


def prev_day_ohlc(bars: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Previous-day OHLC levels broadcast onto each bar of the current day.

    Requires:
      - bars["dt"] tz-aware (America/Denver)
      - open/high/low/close columns

    Output columns (defaults):
      pd_open, pd_high, pd_low, pd_close
    """
    if bars is None or bars.empty:
        return bars
    req = {"dt", "open", "high", "low", "close"}
    if not req.issubset(set(bars.columns)):
        return bars

    out_prefix = str(params.get("out_prefix", "pd_"))

    dt = pd.to_datetime(bars["dt"], errors="coerce")
    day = dt.dt.date

    o = pd.to_numeric(bars["open"], errors="coerce")
    h = pd.to_numeric(bars["high"], errors="coerce")
    l = pd.to_numeric(bars["low"], errors="coerce")
    c = pd.to_numeric(bars["close"], errors="coerce")

    d = pd.DataFrame({"day": day, "open": o, "high": h, "low": l, "close": c})
    day_agg = d.groupby("day", dropna=False).agg(
        day_open=("open", "first"),
        day_high=("high", "max"),
        day_low=("low", "min"),
        day_close=("close", "last"),
    )
    prev = day_agg.shift(1)

    # Map prev-day values back onto each bar row by current day
    bars[f"{out_prefix}open"] = pd.Series(day).map(prev["day_open"])
    bars[f"{out_prefix}high"] = pd.Series(day).map(prev["day_high"])
    bars[f"{out_prefix}low"] = pd.Series(day).map(prev["day_low"])
    bars[f"{out_prefix}close"] = pd.Series(day).map(prev["day_close"])
    return bars


def prev_day_pivots(bars: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Classic floor pivots derived from previous-day OHLC (pd_*).
    Requires prev_day_ohlc to have been applied, or equivalent columns.

    Output columns (defaults):
      pd_pp, pd_r1, pd_s1, pd_r2, pd_s2
    """
    if bars is None or bars.empty:
        return bars

    in_prefix = str(params.get("in_prefix", "pd_"))
    out_prefix = str(params.get("out_prefix", "pd_"))

    h_col = f"{in_prefix}high"
    l_col = f"{in_prefix}low"
    c_col = f"{in_prefix}close"
    if not {h_col, l_col, c_col}.issubset(set(bars.columns)):
        return bars

    h = pd.to_numeric(bars[h_col], errors="coerce")
    l = pd.to_numeric(bars[l_col], errors="coerce")
    c = pd.to_numeric(bars[c_col], errors="coerce")

    pp = (h + l + c) / 3.0
    r1 = 2.0 * pp - l
    s1 = 2.0 * pp - h
    r2 = pp + (h - l)
    s2 = pp - (h - l)

    bars[f"{out_prefix}pp"] = pp
    bars[f"{out_prefix}r1"] = r1
    bars[f"{out_prefix}s1"] = s1
    bars[f"{out_prefix}r2"] = r2
    bars[f"{out_prefix}s2"] = s2
    return bars


def swing_points(bars: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Simple fractal swing points on bar highs/lows.
    A "swing high" at i means high[i] is the max in window [i-k, i+k].
    A "swing low" at i means low[i] is the min in window [i-k, i+k].

    Outputs:
      swing_high (price at swing, else NaN)
      swing_low  (price at swing, else NaN)
    """
    if bars is None or bars.empty:
        return bars
    req = {"high", "low"}
    if not req.issubset(set(bars.columns)):
        return bars

    k = int(params.get("k", 2))
    out_high = str(params.get("out_high", f"swing_high_k{k}"))
    out_low = str(params.get("out_low", f"swing_low_k{k}"))

    h = pd.to_numeric(bars["high"], errors="coerce")
    l = pd.to_numeric(bars["low"], errors="coerce")

    roll_max = h.rolling(window=2 * k + 1, center=True, min_periods=2 * k + 1).max()
    roll_min = l.rolling(window=2 * k + 1, center=True, min_periods=2 * k + 1).min()

    bars[out_high] = np.where(h == roll_max, h, np.nan)
    bars[out_low] = np.where(l == roll_min, l, np.nan)
    return bars


# Register defaults
DEFAULT_INDICATORS.register("ema", ema)
DEFAULT_INDICATORS.register("rsi", rsi)
DEFAULT_INDICATORS.register("atr", atr)
DEFAULT_INDICATORS.register("session_vwap", session_vwap)

# New: entry-context primitives
DEFAULT_INDICATORS.register("prev_day_ohlc", prev_day_ohlc)
DEFAULT_INDICATORS.register("prev_day_pivots", prev_day_pivots)
DEFAULT_INDICATORS.register("swing_points", swing_points)