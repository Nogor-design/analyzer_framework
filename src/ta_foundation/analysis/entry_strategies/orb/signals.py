from __future__ import annotations

"""
Opening Range Breakout (ORB) Signal Detector
=============================================
Defines the opening range for each trading session and emits a signal when
price breaks above (bullish) or below (bearish) that range.

ALGORITHM
---------
For each trading day:
  1. Define the opening range as the high/low of the first `orb_minutes`
     minutes after `session_open_hour:session_open_minute` (Denver time).
  2. After the range is set:
     - Bull breakout: bar closes above orb_high (direction = 1)
     - Bear breakout: bar closes below orb_low  (direction = -1)
  3. Only one signal per side per day (first breakout wins).
  4. Optional: require range to be at least `min_range_ticks` wide.
  5. Optional: require price to have not already exceeded the range before
     the designated range-building window ends (wick filter).

INPUT
-----
bars_1m : DataFrame with columns [dt, open, high, low, close, volume]
          dt must be tz-aware (America/Denver) or tz-naive (treated as Denver).

OUTPUT
------
DataFrame with columns:
  dt              — signal bar datetime
  direction       — 1 or -1
  open/high/low/close — signal bar OHLC
  orb_high        — opening range high
  orb_low         — opening range low
  orb_range_ticks — range width in ticks
  orb_range_atr_pct — range width as % of ATR (if atr_col available)
  day_label       — YYYY-MM-DD string for grouping
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


DEFAULT_ORB_CONFIG: Dict[str, Any] = {
    "orb_minutes":          30,       # minutes to build opening range
    "session_open_hour":    9,        # Denver time
    "session_open_minute":  30,       # Denver time (9:30 = NY open)
    "session_close_hour":   16,       # ignore signals after this hour
    "direction":            0,        # 0=both, 1=long, -1=short
    "min_range_ticks":      4,        # require range > N ticks to avoid noise
    "tick_size":            0.25,
    "atr_period":           14,       # for range normalisation
    "require_close_beyond": True,     # bar close must exceed range (not just wick)
    "one_signal_per_side":  True,     # only first breakout per direction per day
}


def detect_orb(
    bars_1m: pd.DataFrame,
    params: Dict[str, Any] = None,
) -> pd.DataFrame:
    """
    Detect Opening Range Breakout signals on 1m bars.

    Parameters
    ----------
    bars_1m : 1-minute bars with tz-aware or tz-naive 'dt' column
    params  : override keys from DEFAULT_ORB_CONFIG

    Returns
    -------
    signals DataFrame (see module docstring)
    """
    cfg = {**DEFAULT_ORB_CONFIG, **(params or {})}

    orb_mins       = int(cfg["orb_minutes"])
    open_h         = int(cfg["session_open_hour"])
    open_m         = int(cfg["session_open_minute"])
    close_h        = int(cfg["session_close_hour"])
    direction_cfg  = int(cfg["direction"])
    min_range_t    = float(cfg["min_range_ticks"])
    tick_sz        = float(cfg["tick_size"])
    atr_period     = int(cfg["atr_period"])
    req_close      = bool(cfg["require_close_beyond"])
    one_per_side   = bool(cfg["one_signal_per_side"])

    # Normalise timestamps to Denver-local tz-naive for grouping
    dt_series = pd.to_datetime(bars_1m["dt"])
    if dt_series.dt.tz is not None:
        local_dt = dt_series.dt.tz_convert("America/Denver").dt.tz_localize(None)
    else:
        local_dt = dt_series

    bars = bars_1m.copy()
    bars["_local_dt"] = local_dt
    bars["_date"]     = local_dt.dt.date
    bars["_hour"]     = local_dt.dt.hour
    bars["_minute"]   = local_dt.dt.minute

    # Compute ATR for normalisation
    prev_close = bars["close"].shift(1)
    tr = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - prev_close).abs(),
        (bars["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    bars["_atr"] = tr.ewm(span=atr_period, adjust=False).mean()

    directions_to_check = [1, -1] if direction_cfg == 0 else [direction_cfg]

    rows: List[Dict] = []

    for date, day_bars in bars.groupby("_date"):
        # ---- Build opening range ----
        open_total_min = open_h * 60 + open_m
        day_bars = day_bars.sort_values("_local_dt")

        range_bars = day_bars[
            (day_bars["_hour"] * 60 + day_bars["_minute"] >= open_total_min) &
            (day_bars["_hour"] * 60 + day_bars["_minute"] <  open_total_min + orb_mins)
        ]

        if range_bars.empty:
            continue

        orb_high  = float(range_bars["high"].max())
        orb_low   = float(range_bars["low"].min())
        orb_range = orb_high - orb_low

        if orb_range < min_range_t * tick_sz:
            continue

        range_end_dt = range_bars["_local_dt"].max()

        # Average ATR at end of range window (for normalisation)
        atr_val = float(range_bars["_atr"].iloc[-1]) if not range_bars.empty else np.nan
        orb_range_ticks    = round(orb_range / tick_sz, 2)
        orb_range_atr_pct  = round(orb_range / atr_val, 4) if atr_val and atr_val > 0 else np.nan

        # ---- Scan for breakout bars ----
        signal_bars = day_bars[
            (day_bars["_local_dt"] > range_end_dt) &
            (day_bars["_hour"] < close_h)
        ]

        fired: Dict[int, bool] = {1: False, -1: False}

        for _, bar in signal_bars.iterrows():
            for d in directions_to_check:
                if one_per_side and fired[d]:
                    continue

                if d == 1:
                    # Bullish breakout
                    breached = bar["close"] > orb_high if req_close else bar["high"] > orb_high
                else:
                    # Bearish breakout
                    breached = bar["close"] < orb_low if req_close else bar["low"] < orb_low

                if breached:
                    rows.append({
                        "dt":               bar["dt"],
                        "direction":        d,
                        "open":             bar["open"],
                        "high":             bar["high"],
                        "low":              bar["low"],
                        "close":            bar["close"],
                        "orb_high":         orb_high,
                        "orb_low":          orb_low,
                        "orb_range_ticks":  orb_range_ticks,
                        "orb_range_atr_pct": orb_range_atr_pct,
                        "day_label":        str(date),
                    })
                    fired[d] = True

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).reset_index(drop=True)
