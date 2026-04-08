from __future__ import annotations

"""
Tick-Path Reversal Analyzer
============================
For each qualifying candle event, traces the tick-by-tick price path within
the forward window and computes pre-reversal excursion.

A "reversal" in the favorable direction is defined as:
  price drops reversal_ticks × tick_size from its running peak  (for bullish)
  price rises reversal_ticks × tick_size from its running trough (for bearish)

Pre-reversal favorable excursion = furthest favorable move before the first
  reversal of reversal_ticks magnitude.

Pre-reversal adverse excursion = furthest adverse move before the first
  reversal of reversal_ticks magnitude in the adverse direction.

Uses tick data from MarketDataStore.ticks (column "last" for price, "dt" for time).

Output columns added to events DataFrame
-----------------------------------------
  tick_pre_reversal_fav_ticks   ticks traveled in favorable direction before reversal
  tick_pre_reversal_adv_ticks   ticks traveled in adverse direction before first reversal
  tick_reversal_occurred        True if a reversal was detected within the window
  tick_n_ticks_in_window        number of ticks found in the forward window
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_TICK_CONFIG: Dict[str, Any] = {
    "reversal_ticks": 8,     # reversal_ticks × tick_size = reversal threshold
    "min_swing_ticks": 2,    # ignore swings smaller than this
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dt_to_utc_ns(dt_series: pd.Series) -> np.ndarray:
    s = pd.to_datetime(dt_series)
    if s.dt.tz is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return s.astype("datetime64[ns]").astype("int64").values


def _ts_to_utc_ns(ts: Any) -> int:
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return int(t.value)


def _compute_pre_reversal_excursion(
    prices: np.ndarray,
    close_price: float,
    direction: int,
    tick_size: float,
    reversal_price: float,
) -> tuple[float, float, bool]:
    """
    Scan tick prices and compute pre-reversal excursion.

    Parameters
    ----------
    prices        : tick prices in forward window (chronological order)
    close_price   : price at the close of the signal candle
    direction     : 1 (bullish) | -1 (bearish)
    tick_size     : tick size for unit conversion
    reversal_price: price movement threshold for reversal detection (in price units)

    Returns
    -------
    (pre_reversal_fav_ticks, pre_reversal_adv_ticks, reversal_occurred)
    """
    if len(prices) == 0:
        return 0.0, 0.0, False

    running_peak = close_price
    running_trough = close_price
    pre_rev_fav = 0.0
    pre_rev_adv = 0.0
    reversal_occurred = False

    for p in prices:
        if direction == 1:  # bullish: favorable = up
            move_up   = p - close_price
            move_down = close_price - p
            running_peak = max(running_peak, p)

            if (running_peak - p) >= reversal_price:
                # Favorable reversal detected
                pre_rev_fav = max(running_peak - close_price, 0.0) / tick_size
                pre_rev_adv = max(close_price - running_trough, 0.0) / tick_size
                reversal_occurred = True
                break

            running_trough = min(running_trough, p)

        else:  # bearish: favorable = down
            running_trough = min(running_trough, p)

            if (p - running_trough) >= reversal_price:
                # Favorable reversal detected
                pre_rev_fav = max(close_price - running_trough, 0.0) / tick_size
                pre_rev_adv = max(running_peak - close_price, 0.0) / tick_size
                reversal_occurred = True
                break

            running_peak = max(running_peak, p)

    if not reversal_occurred:
        # No reversal — the entire window move counts
        if direction == 1:
            pre_rev_fav = max(running_peak - close_price, 0.0) / tick_size
            pre_rev_adv = max(close_price - running_trough, 0.0) / tick_size
        else:
            pre_rev_fav = max(close_price - running_trough, 0.0) / tick_size
            pre_rev_adv = max(running_peak - close_price, 0.0) / tick_size

    return round(pre_rev_fav, 2), round(pre_rev_adv, 2), reversal_occurred


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_tick_excursion(
    events_with_fwd: pd.DataFrame,
    ticks: pd.DataFrame,
    tf_minutes: int,
    tick_size: float = 0.25,
    config: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """
    Append tick-path reversal metrics to an events DataFrame.

    Parameters
    ----------
    events_with_fwd : events DataFrame from compute_forward_excursion()
                      Must have: dt, close, direction, window_minutes columns.
    ticks           : tick data with dt (tz-aware) and last (price) columns.
    tf_minutes      : signal bar timeframe in minutes (to compute close time).
    tick_size       : tick size for unit conversion.
    config          : optional tick analysis config dict.

    Returns
    -------
    events_with_fwd with four additional columns appended:
      tick_pre_reversal_fav_ticks
      tick_pre_reversal_adv_ticks
      tick_reversal_occurred
      tick_n_ticks_in_window
    """
    cfg = {**DEFAULT_TICK_CONFIG, **(config or {})}
    reversal_ticks = float(cfg.get("reversal_ticks", 8))
    reversal_price = reversal_ticks * tick_size

    if events_with_fwd is None or events_with_fwd.empty:
        return events_with_fwd

    if ticks is None or ticks.empty:
        df = events_with_fwd.copy()
        df["tick_pre_reversal_fav_ticks"] = np.nan
        df["tick_pre_reversal_adv_ticks"] = np.nan
        df["tick_reversal_occurred"]       = False
        df["tick_n_ticks_in_window"]       = 0
        return df

    # Prepare tick data
    tick_sorted = ticks.sort_values("dt").reset_index(drop=True)
    tick_ns     = _dt_to_utc_ns(tick_sorted["dt"])
    tick_prices = tick_sorted["last"].values.astype(np.float64)

    tf_delta_ns = int(pd.Timedelta(minutes=tf_minutes).value)

    pre_rev_fav_list:   List[float]   = []
    pre_rev_adv_list:   List[float]   = []
    reversal_list:      List[bool]    = []
    n_ticks_list:       List[int]     = []

    for _, ev in events_with_fwd.iterrows():
        ev_close     = float(ev["close"])
        direction    = int(ev["direction"])
        window_min   = int(ev.get("window_minutes", 15))

        close_ns  = _ts_to_utc_ns(ev["dt"]) + tf_delta_ns
        win_end_ns = close_ns + int(pd.Timedelta(minutes=window_min).value)

        start_idx = int(np.searchsorted(tick_ns, close_ns,   side="right"))
        end_idx   = int(np.searchsorted(tick_ns, win_end_ns, side="right"))

        w_prices = tick_prices[start_idx:end_idx]
        n_ticks  = len(w_prices)

        fav, adv, rev = _compute_pre_reversal_excursion(
            w_prices, ev_close, direction, tick_size, reversal_price
        )

        pre_rev_fav_list.append(fav)
        pre_rev_adv_list.append(adv)
        reversal_list.append(rev)
        n_ticks_list.append(n_ticks)

    df = events_with_fwd.copy()
    df["tick_pre_reversal_fav_ticks"] = pre_rev_fav_list
    df["tick_pre_reversal_adv_ticks"] = pre_rev_adv_list
    df["tick_reversal_occurred"]       = reversal_list
    df["tick_n_ticks_in_window"]       = n_ticks_list
    return df
