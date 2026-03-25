from __future__ import annotations

import numpy as np
import pandas as pd

from ta_foundation.analysis.indicators.registry import DEFAULT_INDICATORS


def _parse_hhmm(s: str) -> tuple[int, int]:
    s = (s or "").strip()
    if not s or ":" not in s:
        return (0, 0)
    hh, mm = s.split(":", 1)
    return (int(hh), int(mm))


def _minutes_since_midnight(dt: pd.Series) -> pd.Series:
    return (dt.dt.hour.astype("int64") * 60 + dt.dt.minute.astype("int64")).astype("int64")


def _session_day_date(dt: pd.Series, session_start: str, globex_start: str) -> pd.Series:
    """
    Futures session_day:
      - if time >= globex_start (e.g., 16:00) => belongs to NEXT calendar day (session_day = date+1)
      - else                                 => belongs to current calendar day

    Returns python date objects (dt assumed tz-aware already).
    """
    sh, sm = _parse_hhmm(session_start)
    gh, gm = _parse_hhmm(globex_start)
    session_start_min = sh * 60 + sm
    globex_start_min = gh * 60 + gm

    dt = pd.to_datetime(dt, errors="coerce")
    mins = _minutes_since_midnight(dt)
    cal_day = dt.dt.floor("D")

    # After 16:00, shift to next day; otherwise same day.
    sess_day = cal_day.where(mins < globex_start_min, cal_day + pd.Timedelta(days=1))
    return pd.to_datetime(sess_day).dt.date


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
    out_col = str(params.get("out", "vwap_session"))
    price_col = str(params.get("price_col", "close"))
    vol_col = str(params.get("vol_col", "volume"))

    if "dt" not in bars.columns or price_col not in bars.columns or vol_col not in bars.columns:
        return bars

    dt = pd.to_datetime(bars["dt"], errors="coerce")
    px = pd.to_numeric(bars[price_col], errors="coerce")
    vol = pd.to_numeric(bars[vol_col], errors="coerce")

    day = dt.dt.date
    pv = px * vol
    bars[out_col] = (pv.groupby(day).cumsum() / vol.groupby(day).cumsum()).replace([np.inf, -np.inf], np.nan)
    return bars


def prev_day_ohlc(bars: pd.DataFrame, params: dict) -> pd.DataFrame:
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

    bars[f"{out_prefix}open"] = pd.Series(day).map(prev["day_open"])
    bars[f"{out_prefix}high"] = pd.Series(day).map(prev["day_high"])
    bars[f"{out_prefix}low"] = pd.Series(day).map(prev["day_low"])
    bars[f"{out_prefix}close"] = pd.Series(day).map(prev["day_close"])
    return bars


def prev_day_pivots(bars: pd.DataFrame, params: dict) -> pd.DataFrame:
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
    if bars is None or bars.empty:
        return bars
    if not {"high", "low"}.issubset(set(bars.columns)):
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


def opening_range(bars: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Opening Range High/Low for first N minutes after session_start of each futures session_day.

    Defaults (Denver time):
      session_start: "07:30"
      globex_start:  "16:00"
      minutes: 15

    Outputs:
      or_high, or_low (broadcast onto all bars of the same session_day)
    """
    if bars is None or bars.empty:
        return bars
    if not {"dt", "high", "low"}.issubset(set(bars.columns)):
        return bars

    session_start = str(params.get("session_start", "07:30"))
    globex_start = str(params.get("globex_start", "16:00"))
    minutes = int(params.get("minutes", 15))
    out_prefix = str(params.get("out_prefix", "or_"))

    sh, sm = _parse_hhmm(session_start)
    start_min = sh * 60 + sm

    dt = pd.to_datetime(bars["dt"], errors="coerce")
    mins = _minutes_since_midnight(dt)
    sess_day = _session_day_date(dt, session_start=session_start, globex_start=globex_start)

    h = pd.to_numeric(bars["high"], errors="coerce")
    l = pd.to_numeric(bars["low"], errors="coerce")

    in_or = (mins >= start_min) & (mins < (start_min + minutes))

    tmp = pd.DataFrame({"session_day": sess_day, "high": h, "low": l, "in_or": in_or})
    or_agg = tmp[tmp["in_or"]].groupby("session_day", dropna=False).agg(
        or_high=("high", "max"),
        or_low=("low", "min"),
    )

    bars[f"{out_prefix}high"] = pd.Series(sess_day).map(or_agg["or_high"])
    bars[f"{out_prefix}low"] = pd.Series(sess_day).map(or_agg["or_low"])
    return bars


def overnight_levels(bars: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Overnight High/Low for each futures session_day.

    Defaults (Denver time):
      globex_start:  "16:00"
      session_start: "07:30"

    Overnight window is:
      time >= globex_start OR time < session_start

    Outputs:
      on_high, on_low (broadcast onto all bars with that session_day)
    """
    if bars is None or bars.empty:
        return bars
    if not {"dt", "high", "low"}.issubset(set(bars.columns)):
        return bars

    session_start = str(params.get("session_start", "07:30"))
    globex_start = str(params.get("globex_start", "16:00"))
    out_prefix = str(params.get("out_prefix", "on_"))

    sh, sm = _parse_hhmm(session_start)
    gh, gm = _parse_hhmm(globex_start)
    session_start_min = sh * 60 + sm
    globex_start_min = gh * 60 + gm

    dt = pd.to_datetime(bars["dt"], errors="coerce")
    mins = _minutes_since_midnight(dt)
    sess_day = _session_day_date(dt, session_start=session_start, globex_start=globex_start)

    h = pd.to_numeric(bars["high"], errors="coerce")
    l = pd.to_numeric(bars["low"], errors="coerce")

    is_overnight = (mins >= globex_start_min) | (mins < session_start_min)

    tmp = pd.DataFrame({"session_day": sess_day, "high": h, "low": l, "is_overnight": is_overnight})
    on_agg = tmp[tmp["is_overnight"]].groupby("session_day", dropna=False).agg(
        on_high=("high", "max"),
        on_low=("low", "min"),
    )

    bars[f"{out_prefix}high"] = pd.Series(sess_day).map(on_agg["on_high"])
    bars[f"{out_prefix}low"] = pd.Series(sess_day).map(on_agg["on_low"])
    return bars


# Register defaults
DEFAULT_INDICATORS.register("ema", ema)
DEFAULT_INDICATORS.register("rsi", rsi)
DEFAULT_INDICATORS.register("atr", atr)
DEFAULT_INDICATORS.register("session_vwap", session_vwap)

DEFAULT_INDICATORS.register("prev_day_ohlc", prev_day_ohlc)
DEFAULT_INDICATORS.register("prev_day_pivots", prev_day_pivots)
DEFAULT_INDICATORS.register("swing_points", swing_points)

DEFAULT_INDICATORS.register("opening_range", opening_range)
DEFAULT_INDICATORS.register("overnight_levels", overnight_levels)