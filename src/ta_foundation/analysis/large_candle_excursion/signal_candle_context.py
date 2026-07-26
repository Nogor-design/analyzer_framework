from __future__ import annotations

"""
Signal Candle Context Helpers
==============================
Four independent context-enrichment modules for large-candle event rows.
Each function mutates the events DataFrame in-place and is opt-in via config flag.

Modules
-------
add_directional_context   — pre-candle direction, streak, engulf, range break
add_ma_vwap_context       — MA100/200 and VWAP location, distance, extension buckets
add_key_level_context     — nearest key level, distance, interaction label
add_trend_state_context   — MA slopes, stacking, trend alignment label

Design contracts
----------------
- All helpers follow the same signature:
    fn(out: pd.DataFrame, tf_bars: pd.DataFrame, cfg: Dict, tick_size: float) -> None
  and mutate *out* in-place without touching *tf_bars*.
- All bar lookups use positional integer bar_idx (consistent with context_enricher.py).
- NaN / out-of-bounds values always yield "unknown" bucket labels.
- tf_bars must have columns: dt, open, high, low, close.
  volume is optional; used only by VWAP computation.
"""

from typing import Any, Dict, List, Optional

import math

import numpy as np
import pandas as pd

from ta_foundation.analysis.large_candle_excursion._bucket_utils import assign_labeled_bucket


# ---------------------------------------------------------------------------
# Default configurations
# ---------------------------------------------------------------------------

DEFAULT_DIRECTIONAL_CONTEXT: Dict[str, Any] = {
    "enabled": False,
    "streak_lookback": 5,   # increased from 3 for better streak detection
    "prev_direction_buckets": [
        {"label": "same",     "min": 0.5},
        {"label": "opposite", "max": 0.5},
    ],
    "streak_buckets": [
        {"label": "streak_1",      "min": 1.0, "max": 2.0},
        {"label": "streak_2",      "min": 2.0, "max": 3.0},
        {"label": "streak_3_plus", "min": 3.0},
    ],
}

DEFAULT_MA_VWAP_CONTEXT: Dict[str, Any] = {
    "enabled": False,
    "ma_periods": [100, 200],
    "include_vwap": True,
    "at_level_atr_threshold": 0.10,   # within this ATR distance = "near"
    # abs(dist_atr) extension buckets (unsigned)
    "extension_buckets": [
        {"label": "near",     "max": 0.25},
        {"label": "moderate", "min": 0.25, "max": 0.75},
        {"label": "extended", "min": 0.75},
    ],
    # signed distance buckets (dist_atr: negative = below, positive = above)
    "signed_location_buckets": [
        {"label": "far_below",  "max": -1.0},
        {"label": "below",      "min": -1.0, "max": -0.15},
        {"label": "near",       "min": -0.15, "max": 0.15},
        {"label": "above",      "min": 0.15,  "max": 1.0},
        {"label": "far_above",  "min": 1.0},
    ],
}

DEFAULT_KEY_LEVEL_CONTEXT: Dict[str, Any] = {
    "enabled": False,
    "level_types": [
        "prior_day_high",
        "prior_day_low",
        "prior_day_close",
        "session_high",
        "session_low",
        "overnight_high",
        "overnight_low",
        "vwap",
        "ma100",
        "ma200",
    ],
    "rth_start_hour": 7,
    "rth_start_minute": 30,
    "rth_end_hour": 16,
    # abs distance / ATR interaction buckets
    "interaction_atr_buckets": [
        {"label": "at_level",    "max": 0.10},
        {"label": "approaching", "min": 0.10, "max": 0.30},
        {"label": "nearby",      "min": 0.30, "max": 0.60},
        {"label": "departing",   "min": 0.60},
    ],
}

DEFAULT_TREND_STATE_CONTEXT: Dict[str, Any] = {
    "enabled": False,
    "slope_lookback": 5,
    "ma_periods": [100, 200],
    "slope_strong_threshold_atr": 0.04,
    "slope_flat_threshold_atr": 0.01,
}

# ---------------------------------------------------------------------------
# Exhaustion / Extension context — derived from other context outputs
# ---------------------------------------------------------------------------

DEFAULT_EXHAUSTION_CONTEXT: Dict[str, Any] = {
    "enabled": False,
    # ATR thresholds for "extended" classification
    "extended_vwap_atr": 0.75,    # |dist_vwap_atr| > this → extended from VWAP
    "extended_ma_atr":   0.75,    # |dist_ma_atr| > this → extended from MA
    # Streak threshold for streak exhaustion label
    "exhaustion_streak_min": 3,
    # Level approach threshold for extension_into_level
    "level_approach_max_atr": 0.30,
}


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------

def _get_bars_dt_series(tf_bars: pd.DataFrame) -> pd.Series:
    """Return the dt series from tf_bars regardless of whether it is a column or index."""
    if "dt" in tf_bars.columns:
        return tf_bars["dt"]
    if isinstance(tf_bars.index, pd.DatetimeIndex):
        return tf_bars.index.to_series().reset_index(drop=True)
    return pd.Series([pd.NaT] * len(tf_bars))


def _compute_lag_safe_ma(close_vals: np.ndarray, period: int) -> np.ndarray:
    """Rolling mean with shift(1) — MA at position i is mean of [i-period, i-1]."""
    s = pd.Series(close_vals)
    return s.shift(1).rolling(period, min_periods=max(1, period // 2)).mean().values


def _compute_lag_safe_vwap(tf_bars: pd.DataFrame) -> np.ndarray:
    """
    Daily-reset VWAP, lag-safe: vwap[i] = cumulative TVOL[0..i-1] / cumVOL[0..i-1].
    Falls back to equal-weight typical price when volume is unavailable.
    """
    dt_series = _get_bars_dt_series(tf_bars)
    dates = dt_series.dt.date.values

    high   = tf_bars["high"].values.astype(float)
    low    = tf_bars["low"].values.astype(float)
    close  = tf_bars["close"].values.astype(float)
    typical = (high + low + close) / 3.0

    if "volume" in tf_bars.columns:
        vol = tf_bars["volume"].values.astype(float)
        vol = np.where(np.isnan(vol) | (vol <= 0), 1.0, vol)
    else:
        vol = np.ones(len(tf_bars))

    vwap_arr = np.full(len(tf_bars), np.nan)
    unique_dates = np.unique(dates[~pd.isnull(dates)])

    for d in unique_dates:
        mask = dates == d
        idxs = np.where(mask)[0]
        t = typical[idxs]
        v = vol[idxs]
        # cumsum then shift right by 1 for lag safety
        cum_tv = np.concatenate([[0.0], np.cumsum(t * v)[:-1]])
        cum_v  = np.concatenate([[0.0], np.cumsum(v)[:-1]])
        with np.errstate(divide="ignore", invalid="ignore"):
            day_vwap = np.where(cum_v > 0, cum_tv / cum_v, np.nan)
        vwap_arr[idxs] = day_vwap

    return vwap_arr


def _compute_session_running_hl(tf_bars: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """
    Lag-safe running session high/low: at bar i, value = max/min of high/low of
    bars 0..i-1 within the same calendar date.
    Returns (sess_high, sess_low) arrays indexed positionally.
    """
    dt_series = _get_bars_dt_series(tf_bars)
    dates = dt_series.dt.date.values
    high  = tf_bars["high"].values.astype(float)
    low   = tf_bars["low"].values.astype(float)

    sess_high = np.full(len(tf_bars), np.nan)
    sess_low  = np.full(len(tf_bars), np.nan)

    unique_dates = np.unique(dates[~pd.isnull(dates)])
    for d in unique_dates:
        idxs = np.where(dates == d)[0]
        h = high[idxs]
        l = low[idxs]
        cummax = pd.Series(h).expanding().max().shift(1).values
        cummin = pd.Series(l).expanding().min().shift(1).values
        sess_high[idxs] = cummax
        sess_low[idxs]  = cummin

    return sess_high, sess_low


def _compute_prior_day_hl_c(tf_bars: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each bar, compute prior calendar day's high, low, last-close."""
    dt_series = _get_bars_dt_series(tf_bars)
    dates = dt_series.dt.date.values
    high  = tf_bars["high"].values.astype(float)
    low   = tf_bars["low"].values.astype(float)
    close = tf_bars["close"].values.astype(float)

    unique_dates = sorted(set(dates[~pd.isnull(dates)]))
    # Build per-date OHLC summary
    day_summary: Dict[Any, Dict[str, float]] = {}
    for d in unique_dates:
        idxs = np.where(dates == d)[0]
        day_summary[d] = {
            "high":  float(high[idxs].max()),
            "low":   float(low[idxs].min()),
            "close": float(close[idxs[-1]]),  # last close of day
        }

    # For each bar, find prior day's values
    prior_high  = np.full(len(tf_bars), np.nan)
    prior_low   = np.full(len(tf_bars), np.nan)
    prior_close = np.full(len(tf_bars), np.nan)

    sorted_dates = sorted(day_summary.keys())
    for i_d, d in enumerate(sorted_dates):
        if i_d == 0:
            continue  # no prior day
        prev_d = sorted_dates[i_d - 1]
        prev   = day_summary[prev_d]
        idxs   = np.where(dates == d)[0]
        prior_high[idxs]  = prev["high"]
        prior_low[idxs]   = prev["low"]
        prior_close[idxs] = prev["close"]

    return prior_high, prior_low, prior_close


def _compute_overnight_hl(
    tf_bars: pd.DataFrame,
    rth_start_hour: int,
    rth_start_minute: int,
    rth_end_hour: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    For each RTH bar, compute that day's pre-market (pre-RTH) high/low.
    'Overnight' is defined as bars on the same calendar date before RTH start.
    """
    dt_series = _get_bars_dt_series(tf_bars)
    dates   = dt_series.dt.date.values
    hours   = dt_series.dt.hour.values
    minutes = dt_series.dt.minute.values
    high    = tf_bars["high"].values.astype(float)
    low     = tf_bars["low"].values.astype(float)

    is_pre_rth = (hours < rth_start_hour) | (
        (hours == rth_start_hour) & (minutes < rth_start_minute)
    )

    oh_high = np.full(len(tf_bars), np.nan)
    oh_low  = np.full(len(tf_bars), np.nan)

    unique_dates = np.unique(dates[~pd.isnull(dates)])
    for d in unique_dates:
        date_mask = dates == d
        pre_mask  = date_mask & is_pre_rth
        rth_mask  = date_mask & (~is_pre_rth)
        if pre_mask.any() and rth_mask.any():
            oh_high[rth_mask] = float(high[pre_mask].max())
            oh_low[rth_mask]  = float(low[pre_mask].min())

    return oh_high, oh_low


# ---------------------------------------------------------------------------
# 1. Pre-candle directional context
# ---------------------------------------------------------------------------

def add_directional_context(
    out: pd.DataFrame,
    tf_bars: pd.DataFrame,
    cfg: Dict[str, Any],
    tick_size: float,
) -> None:
    """
    Append directional lead-in features for each signal candle.

    Added columns
    -------------
    prev_direction          1 (bull) | -1 (bear) | 0 (doji) for bar at bar_idx-1
    same_as_prev            bool: signal direction matches prev candle
    direction_streak        count of consecutive same-direction candles ending before signal (1–N)
    engulfs_prev_range      bool: signal range fully engulfs prev bar range
    breaks_prev_ext         bool: signal close beyond prev bar extreme in signal direction
    closes_outside_prev     bool: signal close outside prev bar range in any direction
    prev_direction_bucket   "same" | "opposite" | "unknown"
    streak_bucket           "streak_1" | "streak_2" | "streak_3_plus" | "unknown"
    engulf_bucket           "engulf" | "no_engulf" | "unknown"
    """
    if not cfg.get("enabled", False):
        return

    n       = len(out)
    n_bars  = len(tf_bars)
    streak_lb = int(cfg.get("streak_lookback", 3))
    streak_buckets = cfg.get("streak_buckets", DEFAULT_DIRECTIONAL_CONTEXT["streak_buckets"])

    bar_idxs      = out["bar_idx"].values.astype(int)
    sig_direction = out["direction"].values.astype(int)

    tf_open  = tf_bars["open"].values.astype(float)
    tf_high  = tf_bars["high"].values.astype(float)
    tf_low   = tf_bars["low"].values.astype(float)
    tf_close = tf_bars["close"].values.astype(float)
    tf_dir   = np.sign(tf_close - tf_open).astype(int)  # 1 bull, -1 bear, 0 doji

    prev_direction = np.zeros(n, dtype=int)
    same_as_prev   = np.full(n, False)
    streak         = np.zeros(n, dtype=int)
    engulfs        = np.full(n, False)
    breaks_ext     = np.full(n, False)
    closes_outside = np.full(n, False)

    for i, idx in enumerate(bar_idxs):
        if idx < 1 or idx >= n_bars:
            prev_direction[i] = 0
            continue

        # Previous candle metrics
        p_dir = int(tf_dir[idx - 1])
        p_hi  = tf_high[idx - 1]
        p_lo  = tf_low[idx - 1]

        prev_direction[i] = p_dir
        sig_dir = int(sig_direction[i])
        same_as_prev[i]   = (p_dir != 0 and p_dir == sig_dir)

        # Direction streak (how many consecutive same-direction bars before signal)
        count = 0
        for back in range(1, min(streak_lb + 1, idx + 1)):
            if int(tf_dir[idx - back]) == sig_dir:
                count += 1
            else:
                break
        streak[i] = count

        # Engulf: signal range (high-low) fully covers prev bar range
        s_hi = tf_high[idx]
        s_lo = tf_low[idx]
        engulfs[i] = (s_hi >= p_hi) and (s_lo <= p_lo)

        # Breaks prev extreme in signal direction
        s_close = tf_close[idx]
        if sig_dir == 1:
            breaks_ext[i]     = s_close > p_hi
            closes_outside[i] = s_close > p_hi
        elif sig_dir == -1:
            breaks_ext[i]     = s_close < p_lo
            closes_outside[i] = s_close < p_lo
        else:
            breaks_ext[i]     = False
            closes_outside[i] = (s_close > p_hi) or (s_close < p_lo)

    out["prev_direction"]     = prev_direction
    out["same_as_prev"]       = same_as_prev
    out["direction_streak"]   = streak
    out["engulfs_prev_range"] = engulfs
    out["breaks_prev_ext"]    = breaks_ext
    out["closes_outside_prev"]= closes_outside

    # Bucket: prev direction relative to signal
    prev_dir_bucket = np.where(
        prev_direction == 0, "unknown",
        np.where(same_as_prev, "same", "opposite"),
    )
    out["prev_direction_bucket"] = prev_dir_bucket

    # Bucket: streak count
    out["streak_bucket"] = [
        assign_labeled_bucket(float(s), streak_buckets) if s > 0 else "unknown"
        for s in streak
    ]

    # Bucket: engulf
    out["engulf_bucket"] = np.where(
        engulfs, "engulf",
        np.where(bar_idxs < 1, "unknown", "no_engulf"),
    )

    # Composite directional context label — most behaviorally distinctive condition
    # Priority: streak_3plus > streak_2 > same_1 > engulfing_expansion >
    #           reversal_after_streak > closes_outside_prev > opposite_1
    dir_ctx_labels = []
    for i, idx in enumerate(bar_idxs):
        if idx < 1 or idx >= n_bars:
            dir_ctx_labels.append("unknown")
            continue
        streak_v = int(streak[i])
        engulf_v = bool(engulfs[i])
        closes_v = bool(closes_outside[i])
        sig_dir  = int(sig_direction[i])

        if streak_v >= 3:
            dir_ctx_labels.append("same_streak_3plus")
        elif streak_v == 2:
            dir_ctx_labels.append("same_streak_2")
        elif streak_v == 1:
            dir_ctx_labels.append("same_1")
        elif engulf_v:
            dir_ctx_labels.append("engulfing_expansion")
        else:
            # Reversal — check if signal is opposing a prior streak
            opp_streak = 0
            for back in range(1, min(streak_lb + 1, idx + 1)):
                if int(tf_dir[idx - back]) == -sig_dir:
                    opp_streak += 1
                else:
                    break
            if opp_streak >= 2:
                dir_ctx_labels.append("reversal_after_streak")
            elif closes_v:
                dir_ctx_labels.append("closes_outside_prev")
            else:
                dir_ctx_labels.append("opposite_1")

    out["directional_context_label"] = dir_ctx_labels


# ---------------------------------------------------------------------------
# 2. MA / VWAP location context
# ---------------------------------------------------------------------------

def add_ma_vwap_context(
    out: pd.DataFrame,
    tf_bars: pd.DataFrame,
    cfg: Dict[str, Any],
    tick_size: float,
) -> None:
    """
    Append MA100, MA200, and VWAP location features.

    Added columns (for each indicator: ma100, ma200, vwap)
    -------------------------------------------------------
    {name}_value            indicator value at bar (lag-safe)
    above_{name}            bool: close > indicator
    dist_{name}_ticks       signed distance (close - value) / tick_size
    dist_{name}_atr         signed distance / ATR (ATR-normalized)
    {name}_ext_bucket       "near" | "moderate" | "extended" based on abs(dist_atr)
    {name}_location_bucket  "above_{name}" | "at_{name}" | "below_{name}"
    """
    if not cfg.get("enabled", False):
        return

    n_bars   = len(tf_bars)
    n_events = len(out)
    ma_periods   = [int(p) for p in cfg.get("ma_periods", [100, 200])]
    include_vwap = bool(cfg.get("include_vwap", True))
    at_thr       = float(cfg.get("at_level_atr_threshold", 0.05))
    ext_buckets  = cfg.get("extension_buckets", DEFAULT_MA_VWAP_CONTEXT["extension_buckets"])

    close_arr = tf_bars["close"].values.astype(float)
    bar_idxs  = out["bar_idx"].values.astype(int)
    atr_arr   = out["atr"].values.astype(float) if "atr" in out.columns else np.ones(n_events)

    # Precompute MA arrays
    ma_arrays: Dict[str, np.ndarray] = {}
    for p in ma_periods:
        ma_arrays[f"ma{p}"] = _compute_lag_safe_ma(close_arr, p)

    # Precompute VWAP array
    if include_vwap:
        ma_arrays["vwap"] = _compute_lag_safe_vwap(tf_bars)

    event_close = out["close"].values.astype(float)

    for name, arr in ma_arrays.items():
        values   = np.full(n_events, np.nan)
        dists_t  = np.full(n_events, np.nan)
        dists_a  = np.full(n_events, np.nan)
        above    = np.full(n_events, False)

        for i, idx in enumerate(bar_idxs):
            if not (0 <= idx < n_bars):
                continue
            val = arr[idx]
            if np.isnan(val):
                continue
            c   = event_close[i]
            atr = float(atr_arr[i]) if not math.isnan(float(atr_arr[i])) else 0.0
            d   = c - val
            values[i]  = val
            dists_t[i] = round(d / tick_size, 2)
            dists_a[i] = round(d / atr, 4) if atr > 0 else np.nan
            above[i]   = c > val

        out[f"{name}_value"]        = values
        out[f"above_{name}"]        = above
        out[f"dist_{name}_ticks"]   = dists_t
        out[f"dist_{name}_atr"]     = dists_a

        # Extension bucket (based on abs ATR-normalized distance)
        abs_atr = np.abs(dists_a)
        out[f"{name}_ext_bucket"] = [
            assign_labeled_bucket(v, ext_buckets) if not np.isnan(v) else "unknown"
            for v in abs_atr
        ]

        # Location bucket (coarse: above/at/below)
        loc_labels = []
        for i in range(n_events):
            if np.isnan(values[i]):
                loc_labels.append("unknown")
            elif abs(dists_a[i]) <= at_thr if not np.isnan(dists_a[i]) else False:
                loc_labels.append(f"at_{name}")
            elif above[i]:
                loc_labels.append(f"above_{name}")
            else:
                loc_labels.append(f"below_{name}")
        out[f"{name}_location_bucket"] = loc_labels

        # Signed location bucket (5-level: far_below/below/near/above/far_above)
        signed_loc_buckets = cfg.get(
            "signed_location_buckets",
            DEFAULT_MA_VWAP_CONTEXT["signed_location_buckets"],
        )
        out[f"{name}_signed_bucket"] = [
            assign_labeled_bucket(float(v), signed_loc_buckets) if not np.isnan(v) else "unknown"
            for v in dists_a
        ]


# ---------------------------------------------------------------------------
# 3. Key level interaction context
# ---------------------------------------------------------------------------

def add_key_level_context(
    out: pd.DataFrame,
    tf_bars: pd.DataFrame,
    cfg: Dict[str, Any],
    tick_size: float,
) -> None:
    """
    Identify the nearest key level and classify the signal candle's interaction.

    Added columns
    -------------
    nearest_level_type        string name of the closest level
    dist_nearest_level_ticks  abs distance to nearest level in ticks
    dist_nearest_level_atr    abs distance / ATR
    level_interaction_label   "at_level" | "approaching" | "nearby" | "departing" | "unknown"
    prior_day_high/low/close  raw level values (for transparency)
    session_high/low          running session H/L
    overnight_high/low        pre-market H/L for today's session
    """
    if not cfg.get("enabled", False):
        return

    n_bars    = len(tf_bars)
    n_events  = len(out)
    tick_size = float(tick_size)

    configured_levels = set(cfg.get("level_types", DEFAULT_KEY_LEVEL_CONTEXT["level_types"]))
    int_buckets = cfg.get("interaction_atr_buckets", DEFAULT_KEY_LEVEL_CONTEXT["interaction_atr_buckets"])
    rth_sh = int(cfg.get("rth_start_hour", 7))
    rth_sm = int(cfg.get("rth_start_minute", 30))
    rth_eh = int(cfg.get("rth_end_hour", 16))

    bar_idxs    = out["bar_idx"].values.astype(int)
    event_close = out["close"].values.astype(float)
    atr_arr     = out["atr"].values.astype(float) if "atr" in out.columns else np.ones(n_events)

    # Compute all needed level arrays (indexed to tf_bars position)
    level_arrays: Dict[str, np.ndarray] = {}

    if "prior_day_high" in configured_levels or "prior_day_low" in configured_levels or "prior_day_close" in configured_levels:
        pdh, pdl, pdc = _compute_prior_day_hl_c(tf_bars)
        if "prior_day_high"  in configured_levels: level_arrays["prior_day_high"]  = pdh
        if "prior_day_low"   in configured_levels: level_arrays["prior_day_low"]   = pdl
        if "prior_day_close" in configured_levels: level_arrays["prior_day_close"] = pdc

    if "session_high" in configured_levels or "session_low" in configured_levels:
        sh, sl = _compute_session_running_hl(tf_bars)
        if "session_high" in configured_levels: level_arrays["session_high"] = sh
        if "session_low"  in configured_levels: level_arrays["session_low"]  = sl

    if "overnight_high" in configured_levels or "overnight_low" in configured_levels:
        oh, ol = _compute_overnight_hl(tf_bars, rth_sh, rth_sm, rth_eh)
        if "overnight_high" in configured_levels: level_arrays["overnight_high"] = oh
        if "overnight_low"  in configured_levels: level_arrays["overnight_low"]  = ol

    close_vals = tf_bars["close"].values.astype(float)
    if "vwap"  in configured_levels: level_arrays["vwap"]  = _compute_lag_safe_vwap(tf_bars)
    if "ma100" in configured_levels: level_arrays["ma100"] = _compute_lag_safe_ma(close_vals, 100)
    if "ma200" in configured_levels: level_arrays["ma200"] = _compute_lag_safe_ma(close_vals, 200)

    # Store raw level values on event rows (for transparency and downstream use)
    for lname, larr in level_arrays.items():
        raw = np.full(n_events, np.nan)
        for i, idx in enumerate(bar_idxs):
            if 0 <= idx < n_bars:
                raw[i] = larr[idx]
        out[f"level_{lname}"] = raw

    # Find nearest level and compute interaction
    nearest_type     = np.full(n_events, "unknown", dtype=object)
    dist_nearest_t   = np.full(n_events, np.nan)
    dist_nearest_atr = np.full(n_events, np.nan)

    for i, idx in enumerate(bar_idxs):
        if not (0 <= idx < n_bars):
            continue
        c   = event_close[i]
        atr = float(atr_arr[i])
        if math.isnan(atr) or atr <= 0:
            atr = 1.0

        min_dist  = float("inf")
        min_name  = "unknown"
        for lname, larr in level_arrays.items():
            val = larr[idx]
            if np.isnan(val):
                continue
            d = abs(c - val)
            if d < min_dist:
                min_dist = d
                min_name = lname

        if min_name != "unknown":
            nearest_type[i]     = min_name
            dist_nearest_t[i]   = round(min_dist / tick_size, 2)
            dist_nearest_atr[i] = round(min_dist / atr, 4)

    out["nearest_level_type"]        = nearest_type
    out["dist_nearest_level_ticks"]  = dist_nearest_t
    out["dist_nearest_level_atr"]    = dist_nearest_atr

    out["level_interaction_label"] = [
        assign_labeled_bucket(float(v), int_buckets) if not np.isnan(v) else "unknown"
        for v in dist_nearest_atr
    ]


# ---------------------------------------------------------------------------
# 4. Trend state context
# ---------------------------------------------------------------------------

def add_trend_state_context(
    out: pd.DataFrame,
    tf_bars: pd.DataFrame,
    cfg: Dict[str, Any],
    tick_size: float,
) -> None:
    """
    Append trend state features based on MA slopes and price stacking.

    Added columns
    -------------
    ma{p}_slope_atr         slope of MA{p} per bar, ATR-normalized
    ma{p}_slope_label       "rising_strong" | "rising" | "flat" | "falling" | "falling_strong"
    price_above_ma{p}       bool
    stacked_above_mas       bool: close > all configured MAs and MAs ordered bullishly
    stacked_below_mas       bool: close < all configured MAs and MAs ordered bearishly
    trend_alignment_label   "strong_bull" | "bull" | "neutral" | "bear" | "strong_bear"
    """
    if not cfg.get("enabled", False):
        return

    n_bars      = len(tf_bars)
    n_events    = len(out)
    slope_lb    = int(cfg.get("slope_lookback", 5))
    ma_periods  = sorted([int(p) for p in cfg.get("ma_periods", [100, 200])])
    strong_thr  = float(cfg.get("slope_strong_threshold_atr", 0.04))
    flat_thr    = float(cfg.get("slope_flat_threshold_atr", 0.01))

    close_vals  = tf_bars["close"].values.astype(float)
    bar_idxs    = out["bar_idx"].values.astype(int)
    event_close = out["close"].values.astype(float)
    atr_arr     = out["atr"].values.astype(float) if "atr" in out.columns else np.ones(n_events)

    # Precompute MA arrays
    ma_arrays: Dict[int, np.ndarray] = {}
    for p in ma_periods:
        ma_arrays[p] = _compute_lag_safe_ma(close_vals, p)

    # Compute per-MA features
    ma_slopes: Dict[int, np.ndarray] = {}
    for p, ma_arr in ma_arrays.items():
        slopes = np.full(n_events, np.nan)
        above  = np.full(n_events, False)
        labels = []
        for i, idx in enumerate(bar_idxs):
            if not (0 <= idx < n_bars):
                labels.append("unknown")
                continue
            val = ma_arr[idx]
            atr = float(atr_arr[i])
            if np.isnan(val):
                labels.append("unknown")
                continue
            above[i] = event_close[i] > val
            # Slope: (ma[idx] - ma[idx - slope_lb]) / slope_lb, ATR-normalized
            prev_idx = max(0, idx - slope_lb)
            prev_val = ma_arr[prev_idx]
            if not np.isnan(prev_val) and (atr > 0 and not math.isnan(atr)):
                per_bar = (val - prev_val) / max(1, idx - prev_idx)
                slope_norm = per_bar / atr
                slopes[i] = round(slope_norm, 6)
                abs_s = abs(slope_norm)
                if slope_norm > 0:
                    labels.append("rising_strong" if abs_s >= strong_thr else ("rising" if abs_s >= flat_thr else "flat"))
                else:
                    labels.append("falling_strong" if abs_s >= strong_thr else ("falling" if abs_s >= flat_thr else "flat"))
            else:
                labels.append("unknown")

        ma_slopes[p] = slopes
        out[f"ma{p}_slope_atr"]     = slopes
        out[f"ma{p}_slope_label"]   = labels
        out[f"price_above_ma{p}"]   = above

    # Stacking: price relative to all MAs AND MA ordering
    if len(ma_periods) >= 2:
        fast_p = ma_periods[0]
        slow_p = ma_periods[1]
        fast_arr = ma_arrays[fast_p]
        slow_arr = ma_arrays[slow_p]

        stacked_above = np.full(n_events, False)
        stacked_below = np.full(n_events, False)
        alignment_labels = []

        for i, idx in enumerate(bar_idxs):
            if not (0 <= idx < n_bars):
                alignment_labels.append("unknown")
                continue
            fast_v = fast_arr[idx]
            slow_v = slow_arr[idx]
            c = event_close[i]
            if np.isnan(fast_v) or np.isnan(slow_v):
                alignment_labels.append("unknown")
                continue

            above_fast = c > fast_v
            above_slow = c > slow_v
            fast_above_slow = fast_v > slow_v

            stacked_above[i] = above_fast and above_slow and fast_above_slow
            stacked_below[i] = (not above_fast) and (not above_slow) and (not fast_above_slow)

            # Trend alignment
            fast_sl = ma_slopes[fast_p][i]
            slow_sl = ma_slopes[slow_p][i]
            if np.isnan(fast_sl) or np.isnan(slow_sl):
                alignment_labels.append("neutral")
            elif stacked_above[i] and fast_sl > flat_thr and slow_sl > flat_thr:
                alignment_labels.append("strong_bull" if fast_sl >= strong_thr else "bull")
            elif stacked_below[i] and fast_sl < -flat_thr and slow_sl < -flat_thr:
                alignment_labels.append("strong_bear" if abs(fast_sl) >= strong_thr else "bear")
            elif above_fast and above_slow:
                alignment_labels.append("bull")
            elif (not above_fast) and (not above_slow):
                alignment_labels.append("bear")
            else:
                alignment_labels.append("neutral")

        out["stacked_above_mas"]     = stacked_above
        out["stacked_below_mas"]     = stacked_below
        out["trend_alignment_label"] = alignment_labels
    else:
        # Single MA — simple labeling
        p = ma_periods[0]
        alignment_labels = []
        for i in range(n_events):
            above = bool(out[f"price_above_ma{p}"].iloc[i]) if f"price_above_ma{p}" in out.columns else False
            sl = float(ma_slopes.get(p, np.full(n_events, np.nan))[i])
            if math.isnan(sl):
                alignment_labels.append("neutral")
            elif above and sl > flat_thr:
                alignment_labels.append("strong_bull" if sl >= strong_thr else "bull")
            elif not above and sl < -flat_thr:
                alignment_labels.append("strong_bear" if abs(sl) >= strong_thr else "bear")
            else:
                alignment_labels.append("neutral")
        out["trend_alignment_label"] = alignment_labels


# ---------------------------------------------------------------------------
# 5. Exhaustion / Extension context (derived — must run last)
# ---------------------------------------------------------------------------

def add_exhaustion_context(
    out: pd.DataFrame,
    tf_bars: pd.DataFrame,
    cfg: Dict[str, Any],
    tick_size: float,
) -> None:
    """
    Derive exhaustion/extension labels from already-enriched context columns.

    Must be called AFTER add_directional_context, add_ma_vwap_context,
    add_key_level_context, and add_trend_state_context.

    Added columns
    -------------
    exhaustion_label    Categorical label describing the candle's exhaustion/extension type:

      "streak_up_exhaustion"       — bull large candle after 3+ consecutive bull bars
      "streak_down_exhaustion"     — bear large candle after 3+ consecutive bear bars
      "extension_into_resistance"  — bull large candle approaching a known high / MA
      "extension_into_support"     — bear large candle approaching a known low / MA
      "extended_above_vwap"        — bull signal while price is >0.75 ATR above VWAP
      "extended_below_vwap"        — bear signal while price is >0.75 ATR below VWAP
      "none"                       — no exhaustion pattern detected
    """
    if not cfg.get("enabled", False):
        return

    n             = len(out)
    ext_vwap_thr  = float(cfg.get("extended_vwap_atr", DEFAULT_EXHAUSTION_CONTEXT["extended_vwap_atr"]))
    exh_streak    = int(cfg.get("exhaustion_streak_min", DEFAULT_EXHAUSTION_CONTEXT["exhaustion_streak_min"]))
    level_thr     = float(cfg.get("level_approach_max_atr", DEFAULT_EXHAUSTION_CONTEXT["level_approach_max_atr"]))

    # Resistance-type and support-type level names
    _RESISTANCE_LEVELS = {"prior_day_high", "session_high", "overnight_high", "ma100", "ma200"}
    _SUPPORT_LEVELS    = {"prior_day_low",  "session_low",  "overnight_low",  "ma100", "ma200"}

    directions   = out["direction"].values.astype(int) if "direction" in out.columns else np.zeros(n, int)
    streaks      = out["direction_streak"].values.astype(int) if "direction_streak" in out.columns else np.zeros(n, int)

    vwap_dists   = out["dist_vwap_atr"].values.astype(float) if "dist_vwap_atr" in out.columns else np.full(n, np.nan)
    level_dists  = out["dist_nearest_level_atr"].values.astype(float) if "dist_nearest_level_atr" in out.columns else np.full(n, np.nan)
    level_types  = out["nearest_level_type"].values if "nearest_level_type" in out.columns else np.full(n, "unknown", dtype=object)

    labels = []
    for i in range(n):
        direction = int(directions[i])
        streak_v  = int(streaks[i])
        vwap_d    = float(vwap_dists[i])
        level_d   = float(level_dists[i])
        ltype     = str(level_types[i])

        # Priority 1: Streak exhaustion — signal continues an extended run
        if streak_v >= exh_streak:
            labels.append("streak_up_exhaustion" if direction == 1 else "streak_down_exhaustion")
            continue

        # Priority 2: Extension into key level — price approaching a natural turning point
        if not math.isnan(level_d) and level_d <= level_thr:
            if direction == 1 and ltype in _RESISTANCE_LEVELS:
                labels.append("extension_into_resistance")
                continue
            if direction == -1 and ltype in _SUPPORT_LEVELS:
                labels.append("extension_into_support")
                continue

        # Priority 3: Extended from VWAP (signed distance)
        if not math.isnan(vwap_d):
            if vwap_d >= ext_vwap_thr:
                labels.append("extended_above_vwap")
                continue
            if vwap_d <= -ext_vwap_thr:
                labels.append("extended_below_vwap")
                continue

        labels.append("none")

    out["exhaustion_label"] = labels
