from __future__ import annotations

"""
Forward Excursion Window
========================
For each qualifying candle event, scans 1-minute bars in a time-based
forward window and computes excursion metrics.

The forward window is TIME-BASED, not bar-count-based, so results are
comparable across multiple timeframes.

Forward window definition
-------------------------
  signal_close_time = event.dt + timedelta(minutes=tf_minutes)
  window = (signal_close_time, signal_close_time + timedelta(minutes=window_minutes)]
           (open-at-start, closed-at-end)

Excursion metrics (per event × window)
---------------------------------------
  max_high            max of bar highs in the forward window
  min_low             min of bar lows in the forward window
  max_up_pts          max_high - signal_close_price
  max_down_pts        signal_close_price - min_low
  fav_pts             favorable excursion (up for bullish, down for bearish)
  adv_pts             adverse excursion (down for bullish, up for bearish)
  fav_ticks           fav_pts / tick_size
  adv_ticks           adv_pts / tick_size
  time_to_max_fav_min minutes from signal_close_time to the bar that produced max favorable excursion
  time_to_max_adv_min minutes from signal_close_time to the bar that produced max adverse excursion
  n_bars_in_window    number of 1m bars found in the forward window
  forward_end_dt      actual end of forward window (close_time + window_minutes)
"""

from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dt_to_utc_ns(dt_series: pd.Series) -> np.ndarray:
    """Convert any datetime Series to UTC nanosecond integers for fast searchsorted."""
    s = pd.to_datetime(dt_series)
    if s.dt.tz is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return s.astype("datetime64[ns]").astype("int64").values


def _ts_to_utc_ns(ts: Any) -> int:
    """Convert a single Timestamp to UTC nanoseconds."""
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return int(t.value)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_forward_excursion(
    events: pd.DataFrame,
    bars_1m: pd.DataFrame,
    window_minutes_list: List[int],
    tf_minutes: int,
    tick_size: float = 0.25,
) -> pd.DataFrame:
    """
    Compute forward excursion metrics for each (event × window_minutes) combination.

    Parameters
    ----------
    events            : qualifying candle events from detect_large_candles().
                        Must have: dt, close, direction columns.
    bars_1m           : 1-minute bars with dt, high, low columns.
    window_minutes_list : list of forward window durations in minutes.
    tf_minutes        : timeframe of the signal bars in minutes (used to
                        compute signal close time = event.dt + tf_minutes).
    tick_size         : tick size for converting price to ticks.

    Returns
    -------
    DataFrame with one row per (event × window_minutes).
    All event columns are retained plus forward excursion columns.
    """
    if events is None or events.empty:
        return pd.DataFrame()
    if bars_1m is None or bars_1m.empty:
        return pd.DataFrame()

    # Sort and prepare 1m bars
    bars = bars_1m.sort_values("dt").reset_index(drop=True)
    bar_ns   = _dt_to_utc_ns(bars["dt"])          # nanosecond lookup array
    bar_high = bars["high"].values.astype(np.float64)
    bar_low  = bars["low"].values.astype(np.float64)
    bar_dt   = bars["dt"].values                   # for time_to_max computation

    tf_delta_ns = int(pd.Timedelta(minutes=tf_minutes).value)

    records: List[Dict] = []

    for _, ev in events.iterrows():
        ev_dt    = ev["dt"]
        ev_close = float(ev["close"])
        direction = int(ev["direction"])
        ev_dict  = ev.to_dict()

        # Signal close time = bar open time + tf duration
        close_ns  = _ts_to_utc_ns(ev_dt) + tf_delta_ns
        close_ts  = pd.Timestamp(close_ns, unit="ns", tz="UTC").tz_convert("America/Denver")

        for window_minutes in window_minutes_list:
            win_end_ns = close_ns + int(pd.Timedelta(minutes=window_minutes).value)
            win_end_ts = pd.Timestamp(win_end_ns, unit="ns", tz="UTC").tz_convert("America/Denver")

            # Find bar range: bars where close_ns < bar_dt_ns <= win_end_ns
            start_idx = int(np.searchsorted(bar_ns, close_ns,   side="right"))
            end_idx   = int(np.searchsorted(bar_ns, win_end_ns, side="right"))

            n_bars = end_idx - start_idx

            if n_bars <= 0:
                # No bars in window — record with NaN excursion
                rec = dict(ev_dict)
                rec.update({
                    "tf_minutes":             tf_minutes,
                    "window_minutes":         window_minutes,
                    "forward_end_dt":         win_end_ts.isoformat(),
                    "n_bars_in_window":       0,
                    "max_high":               None,
                    "min_low":                None,
                    "max_up_pts":             None,
                    "max_down_pts":           None,
                    "fav_pts":                None,
                    "adv_pts":                None,
                    "fav_ticks":              None,
                    "adv_ticks":              None,
                    "time_to_max_fav_min":    None,
                    "time_to_max_adv_min":    None,
                })
                records.append(rec)
                continue

            # Slice forward window
            w_high = bar_high[start_idx:end_idx]
            w_low  = bar_low[start_idx:end_idx]
            w_dt   = bar_dt[start_idx:end_idx]

            max_high    = float(np.nanmax(w_high))
            min_low     = float(np.nanmin(w_low))
            max_up_pts  = max(max_high - ev_close, 0.0)
            max_down_pts = max(ev_close - min_low, 0.0)

            if direction == 1:      # bullish: up is favorable
                fav_pts = max_up_pts
                adv_pts = max_down_pts
                max_fav_bar_idx = int(np.argmax(w_high))
                max_adv_bar_idx = int(np.argmin(w_low))
            else:                   # bearish: down is favorable
                fav_pts = max_down_pts
                adv_pts = max_up_pts
                max_fav_bar_idx = int(np.argmin(w_low))
                max_adv_bar_idx = int(np.argmax(w_high))

            fav_ticks = round(fav_pts / tick_size, 2)
            adv_ticks = round(adv_pts / tick_size, 2)

            # Time to max: minutes from close_time to the bar that set the extreme
            def _bar_dt_to_minutes(bar_ts: Any) -> float:
                t = pd.Timestamp(bar_ts)
                if t.tzinfo is None:
                    t = t.tz_localize("America/Denver")
                return float((t - close_ts).total_seconds() / 60.0)

            time_to_max_fav = round(_bar_dt_to_minutes(w_dt[max_fav_bar_idx]) + 1.0, 1)  # +1 → end of bar
            time_to_max_adv = round(_bar_dt_to_minutes(w_dt[max_adv_bar_idx]) + 1.0, 1)

            rec = dict(ev_dict)
            rec.update({
                "tf_minutes":             tf_minutes,
                "window_minutes":         window_minutes,
                "forward_end_dt":         win_end_ts.isoformat(),
                "n_bars_in_window":       n_bars,
                "max_high":               round(max_high, 4),
                "min_low":                round(min_low, 4),
                "max_up_pts":             round(max_up_pts, 4),
                "max_down_pts":           round(max_down_pts, 4),
                "fav_pts":                round(fav_pts, 4),
                "adv_pts":                round(adv_pts, 4),
                "fav_ticks":              fav_ticks,
                "adv_ticks":              adv_ticks,
                "time_to_max_fav_min":    time_to_max_fav,
                "time_to_max_adv_min":    time_to_max_adv,
            })
            records.append(rec)

    if not records:
        return pd.DataFrame()

    return pd.DataFrame(records).reset_index(drop=True)
