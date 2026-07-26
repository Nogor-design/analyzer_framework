from __future__ import annotations

"""
Opening Range Signal Detector
=============================
Defines the opening range for each trading session and emits either classic
breakout signals or failed-breakout reclaim signals.

ALGORITHM
---------
For each trading day:
  1. Define the opening range as the high/low of the first `orb_minutes`
     minutes after `session_open_hour:session_open_minute` (Denver time).
  2. For ``signal_type: breakout`` after the range is set:
     - Bull breakout: bar closes above orb_high (direction = 1)
     - Bear breakout: bar closes below orb_low  (direction = -1)
  3. For ``signal_type: failure_reclaim``:
     - Bearish fade: price sweeps above orb_high, then closes back inside
       the range within `max_reclaim_bars` (direction = -1)
     - Bullish fade: price sweeps below orb_low, then closes back inside
       the range within `max_reclaim_bars` (direction = 1)
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
    "signal_type":          "breakout",  # breakout | failure_reclaim
    "min_sweep_ticks":      1.0,      # failure_reclaim: minimum pierce outside OR
    "close_back_ticks":     0.0,      # failure_reclaim: close this far back inside
    "max_reclaim_bars":     3,        # failure_reclaim: bars allowed after sweep
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
    signal_type    = str(cfg.get("signal_type", "breakout")).lower()
    min_sweep_t    = float(cfg.get("min_sweep_ticks", 1.0))
    close_back_t   = float(cfg.get("close_back_ticks", 0.0))
    max_reclaim    = int(cfg.get("max_reclaim_bars", 3))

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

        # ---- Scan for signal bars ----
        signal_bars = day_bars[
            (day_bars["_local_dt"] > range_end_dt) &
            (day_bars["_hour"] < close_h)
        ].reset_index(drop=True)

        fired: Dict[int, bool] = {1: False, -1: False}

        if signal_type == "breakout":
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
                        rows.append(_orb_row(
                            bar, d, orb_high, orb_low, orb_range_ticks,
                            orb_range_atr_pct, str(date), signal_type,
                        ))
                        fired[d] = True
        elif signal_type == "failure_reclaim":
            rows.extend(_detect_failure_reclaims(
                signal_bars=signal_bars,
                directions_to_check=directions_to_check,
                fired=fired,
                one_per_side=one_per_side,
                orb_high=orb_high,
                orb_low=orb_low,
                orb_range_ticks=orb_range_ticks,
                orb_range_atr_pct=orb_range_atr_pct,
                day_label=str(date),
                tick_size=tick_sz,
                min_sweep_ticks=min_sweep_t,
                close_back_ticks=close_back_t,
                max_reclaim_bars=max_reclaim,
                signal_type=signal_type,
            ))
        else:
            raise ValueError(f"Unknown ORB signal_type: {signal_type!r}")

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).reset_index(drop=True)


def _orb_row(
    bar: pd.Series,
    direction: int,
    orb_high: float,
    orb_low: float,
    orb_range_ticks: float,
    orb_range_atr_pct: float,
    day_label: str,
    signal_type: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    row = {
        "dt":                bar["dt"],
        "direction":         direction,
        "open":              bar["open"],
        "high":              bar["high"],
        "low":               bar["low"],
        "close":             bar["close"],
        "orb_high":          orb_high,
        "orb_low":           orb_low,
        "orb_range_ticks":   orb_range_ticks,
        "orb_range_atr_pct": orb_range_atr_pct,
        "day_label":         day_label,
        "signal_type":       signal_type,
    }
    if extra:
        row.update(extra)
    return row


def _detect_failure_reclaims(
    *,
    signal_bars: pd.DataFrame,
    directions_to_check: List[int],
    fired: Dict[int, bool],
    one_per_side: bool,
    orb_high: float,
    orb_low: float,
    orb_range_ticks: float,
    orb_range_atr_pct: float,
    day_label: str,
    tick_size: float,
    min_sweep_ticks: float,
    close_back_ticks: float,
    max_reclaim_bars: int,
    signal_type: str,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    sweep_dist = min_sweep_ticks * tick_size
    close_back = close_back_ticks * tick_size

    for i, bar in signal_bars.iterrows():
        possible: List[tuple[int, str, float]] = []
        if -1 in directions_to_check and not (one_per_side and fired[-1]):
            if float(bar["high"]) >= orb_high + sweep_dist:
                possible.append((-1, "orb_high", orb_high))
        if 1 in directions_to_check and not (one_per_side and fired[1]):
            if float(bar["low"]) <= orb_low - sweep_dist:
                possible.append((1, "orb_low", orb_low))

        for direction, swept_side, level in possible:
            end_i = min(i + max_reclaim_bars, len(signal_bars) - 1)
            for j in range(i, end_i + 1):
                reclaim_bar = signal_bars.iloc[j]
                close = float(reclaim_bar["close"])
                reclaimed = (
                    close <= orb_high - close_back
                    if direction == -1
                    else close >= orb_low + close_back
                )
                if not reclaimed:
                    continue

                rows.append(_orb_row(
                    reclaim_bar,
                    direction,
                    orb_high,
                    orb_low,
                    orb_range_ticks,
                    orb_range_atr_pct,
                    day_label,
                    signal_type,
                    {
                        "swept_side": swept_side,
                        "swept_level": level,
                        "bars_to_reclaim": int(j - i),
                    },
                ))
                fired[direction] = True
                break

            if one_per_side and fired[direction]:
                continue

    return rows
