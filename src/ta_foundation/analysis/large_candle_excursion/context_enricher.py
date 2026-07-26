from __future__ import annotations

"""
Context Enricher
================
Appends volume, candle-structure, and volatility-regime features to qualifying
large-candle events.  Computes bucket labels for each dimension.

Design
------
  - Structure features are computed from event OHLC columns (no bar lookup needed).
  - Volume and rolling-range features are computed from the tf_bars DataFrame
    (looked up by bar_idx).
  - ATR-normalised metrics reuse the 'atr' column already produced by the detector.
  - All additions are opt-in via the enabled flags in each sub-config.
  - Original event columns are never modified.

Added columns
-------------
Volume context (when volume_context.enabled):
  candle_volume          raw volume of the signal bar
  avg_volume             rolling avg volume (primary lookback)
  relative_volume        candle_volume / avg_volume
  vol_bucket             labeled bucket (string)

Candle structure (when candle_structure_context.enabled):
  upper_wick_ticks       (high - max(open,close)) / tick_size
  lower_wick_ticks       (min(open,close) - low) / tick_size
  body_to_range_ratio    body / total_range  (0..1)
  upper_wick_to_range_ratio
  lower_wick_to_range_ratio
  close_position_in_range (close - low) / (high - low)  (0..1)
  close_pos_bucket       labeled bucket (string)
  body_range_bucket      labeled bucket based on body_to_range_ratio
  wick_bucket            direction-aware wick structure label

Volatility context (when volatility_context.enabled):
  range_as_atr           total_range / atr  (NaN when atr unavailable)
  avg_range              rolling avg total_range (primary lookback)
  range_vs_avg_range     total_range / avg_range
  atr_bucket             labeled bucket for range_as_atr
  range_avg_bucket       labeled bucket for range_vs_avg_range
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ta_foundation.analysis.large_candle_excursion._bucket_utils import assign_labeled_bucket  # noqa: F401 (re-exported for back-compat)
from ta_foundation.analysis.large_candle_excursion.signal_candle_context import (
    DEFAULT_DIRECTIONAL_CONTEXT,
    DEFAULT_MA_VWAP_CONTEXT,
    DEFAULT_KEY_LEVEL_CONTEXT,
    DEFAULT_TREND_STATE_CONTEXT,
    DEFAULT_EXHAUSTION_CONTEXT,
    add_directional_context,
    add_ma_vwap_context,
    add_key_level_context,
    add_trend_state_context,
    add_exhaustion_context,
)


# ---------------------------------------------------------------------------
# Default configs (merged by sweep.py into its DEFAULT_CONFIG)
# ---------------------------------------------------------------------------

DEFAULT_VOL_CONTEXT: Dict[str, Any] = {
    "enabled": False,
    "lookbacks": [10, 20],
    "buckets": [
        {"label": "lt_0_8x",       "max": 0.8},
        {"label": "0_8x_to_1_2x",  "min": 0.8,  "max": 1.2},
        {"label": "1_2x_to_1_8x",  "min": 1.2,  "max": 1.8},
        {"label": "1_8x_to_2_5x",  "min": 1.8,  "max": 2.5},
        {"label": "ge_2_5x",       "min": 2.5},
    ],
}

DEFAULT_STRUCT_CONTEXT: Dict[str, Any] = {
    "enabled": False,
    "close_position_buckets": [
        {"label": "bottom_10pct",  "max": 0.10},
        {"label": "bottom_25pct",  "min": 0.10, "max": 0.25},
        {"label": "mid",           "min": 0.25, "max": 0.75},
        {"label": "top_25pct",     "min": 0.75, "max": 0.90},
        {"label": "top_10pct",     "min": 0.90},
    ],
    "body_to_range_buckets": [
        {"label": "small_body",    "max": 0.30},
        {"label": "balanced",      "min": 0.30, "max": 0.70},
        {"label": "large_body",    "min": 0.70},
    ],
    "wick_structure_mode": "simple",
}

DEFAULT_VOLAT_CONTEXT: Dict[str, Any] = {
    "enabled": False,
    "atr_periods": [14],
    "avg_range_lookbacks": [10, 20],
    "candle_range_as_atr_buckets": [
        {"label": "lt_1_0_atr",     "max": 1.0},
        {"label": "1_0_to_1_5_atr", "min": 1.0, "max": 1.5},
        {"label": "1_5_to_2_0_atr", "min": 1.5, "max": 2.0},
        {"label": "ge_2_0_atr",     "min": 2.0},
    ],
    "range_vs_avg_range_buckets": [
        {"label": "lt_1_5x",        "max": 1.5},
        {"label": "1_5x_to_2_0x",   "min": 1.5, "max": 2.0},
        {"label": "2_0x_to_3_0x",   "min": 2.0, "max": 3.0},
        {"label": "ge_3_0x",        "min": 3.0},
    ],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich_events_with_context(
    events: pd.DataFrame,
    tf_bars: pd.DataFrame,
    vol_cfg: Optional[Dict] = None,
    struct_cfg: Optional[Dict] = None,
    volat_cfg: Optional[Dict] = None,
    directional_cfg: Optional[Dict] = None,
    ma_vwap_cfg: Optional[Dict] = None,
    key_level_cfg: Optional[Dict] = None,
    trend_state_cfg: Optional[Dict] = None,
    exhaustion_cfg: Optional[Dict] = None,
    tick_size: float = 0.25,
) -> pd.DataFrame:
    """
    Enrich event rows with context features.

    Parameters
    ----------
    events          : output of detect_large_candles() — has bar_idx, OHLC, atr, …
    tf_bars         : resampled bars at the event's timeframe
    vol_cfg         : volume_context config dict
    struct_cfg      : candle_structure_context config dict
    volat_cfg       : volatility_context config dict
    directional_cfg : directional_context config dict
    ma_vwap_cfg     : ma_vwap_context config dict
    key_level_cfg   : key_level_context config dict
    trend_state_cfg : trend_state_context config dict
    exhaustion_cfg  : exhaustion_context config dict (derived — runs last)
    tick_size       : instrument tick size

    Returns
    -------
    Copy of events with new context columns appended.
    """
    if events is None or events.empty:
        return events

    out = events.copy().reset_index(drop=True)

    if (vol_cfg or {}).get("enabled"):
        _add_volume_features(out, tf_bars, vol_cfg or {}, tick_size)

    if (struct_cfg or {}).get("enabled"):
        _add_structure_features(out, struct_cfg or {}, tick_size)

    if (volat_cfg or {}).get("enabled"):
        _add_volatility_features(out, tf_bars, volat_cfg or {}, tick_size)

    if (directional_cfg or {}).get("enabled"):
        add_directional_context(out, tf_bars, directional_cfg or {}, tick_size)

    if (ma_vwap_cfg or {}).get("enabled"):
        add_ma_vwap_context(out, tf_bars, ma_vwap_cfg or {}, tick_size)

    if (key_level_cfg or {}).get("enabled"):
        add_key_level_context(out, tf_bars, key_level_cfg or {}, tick_size)

    if (trend_state_cfg or {}).get("enabled"):
        add_trend_state_context(out, tf_bars, trend_state_cfg or {}, tick_size)

    # Exhaustion context must run after the 4 primary modules (it reads their output)
    if (exhaustion_cfg or {}).get("enabled"):
        add_exhaustion_context(out, tf_bars, exhaustion_cfg or {}, tick_size)

    return out


# ---------------------------------------------------------------------------
# Volume features
# ---------------------------------------------------------------------------

def _add_volume_features(
    out: pd.DataFrame,
    tf_bars: pd.DataFrame,
    cfg: Dict,
    tick_size: float,
) -> None:
    """Mutates out in-place."""
    if "volume" not in (tf_bars.columns if tf_bars is not None else []):
        out["candle_volume"]   = np.nan
        out["avg_volume"]      = np.nan
        out["relative_volume"] = np.nan
        out["vol_bucket"]      = "unknown"
        return

    lookbacks   = [int(n) for n in cfg.get("lookbacks", [10])]
    primary_lb  = lookbacks[0]
    buckets     = cfg.get("buckets", DEFAULT_VOL_CONTEXT["buckets"])

    # Pre-compute rolling avg volume (lag-safe: shift 1 before rolling)
    vol_series = tf_bars["volume"].astype(float)
    rolling_avgs: Dict[int, np.ndarray] = {}
    for lb in lookbacks:
        rolling_avgs[lb] = vol_series.shift(1).rolling(lb, min_periods=1).mean().values

    bar_indices = out["bar_idx"].values.astype(int)
    n_bars = len(tf_bars)

    candle_vols = np.full(len(out), np.nan)
    avg_vols    = np.full(len(out), np.nan)
    rel_vols    = np.full(len(out), np.nan)

    for i, bar_idx in enumerate(bar_indices):
        if 0 <= bar_idx < n_bars:
            cv = float(vol_series.iloc[bar_idx])
            av = float(rolling_avgs[primary_lb][bar_idx])
            candle_vols[i] = cv
            avg_vols[i]    = av
            if av > 0:
                rel_vols[i] = cv / av

    out["candle_volume"]   = candle_vols
    out["avg_volume"]      = avg_vols
    out["relative_volume"] = rel_vols

    # Additional lookbacks
    for lb in lookbacks[1:]:
        avgs = np.full(len(out), np.nan)
        rels = np.full(len(out), np.nan)
        for i, bar_idx in enumerate(bar_indices):
            if 0 <= bar_idx < n_bars:
                av = float(rolling_avgs[lb][bar_idx])
                avgs[i] = av
                if av > 0 and not np.isnan(candle_vols[i]):
                    rels[i] = candle_vols[i] / av
        out[f"avg_volume_{lb}"]      = avgs
        out[f"relative_volume_{lb}"] = rels

    out["vol_bucket"] = [
        assign_labeled_bucket(v, buckets)
        for v in rel_vols
    ]


# ---------------------------------------------------------------------------
# Candle structure features
# ---------------------------------------------------------------------------

def _add_structure_features(
    out: pd.DataFrame,
    cfg: Dict,
    tick_size: float,
) -> None:
    """Computed entirely from event OHLC columns — no tf_bars lookup needed."""
    o = out["open"].values.astype(float)
    h = out["high"].values.astype(float)
    l = out["low"].values.astype(float)
    c = out["close"].values.astype(float)
    total_range = out["total_range"].values.astype(float)

    upper_body = np.maximum(o, c)
    lower_body = np.minimum(o, c)

    upper_wick = np.clip(h - upper_body, 0.0, None)
    lower_wick = np.clip(lower_body - l, 0.0, None)

    with np.errstate(divide="ignore", invalid="ignore"):
        body_to_range = np.where(total_range > 1e-9,
                                 np.abs(c - o) / total_range,
                                 np.nan)
        uw_to_range   = np.where(total_range > 1e-9, upper_wick / total_range, np.nan)
        lw_to_range   = np.where(total_range > 1e-9, lower_wick / total_range, np.nan)
        close_pos     = np.where(total_range > 1e-9, (c - l) / total_range,    np.nan)

    out["upper_wick_ticks"]         = (upper_wick / tick_size).round(2)
    out["lower_wick_ticks"]         = (lower_wick / tick_size).round(2)
    out["body_to_range_ratio"]      = body_to_range.round(3)
    out["upper_wick_to_range_ratio"]= uw_to_range.round(3)
    out["lower_wick_to_range_ratio"]= lw_to_range.round(3)
    out["close_position_in_range"]  = close_pos.round(3)

    # Close-position bucket
    cp_buckets = cfg.get("close_position_buckets", DEFAULT_STRUCT_CONTEXT["close_position_buckets"])
    out["close_pos_bucket"] = [
        assign_labeled_bucket(v, cp_buckets)
        for v in close_pos
    ]

    # Body-to-range bucket
    br_buckets = cfg.get("body_to_range_buckets", DEFAULT_STRUCT_CONTEXT["body_to_range_buckets"])
    out["body_range_bucket"] = [
        assign_labeled_bucket(v, br_buckets)
        for v in body_to_range
    ]

    # Direction-aware wick structure bucket
    directions = out["direction"].values
    wick_labels = []
    for i in range(len(out)):
        is_bull = directions[i] == 1
        cp  = close_pos[i]
        uwr = uw_to_range[i]
        lwr = lw_to_range[i]
        if np.isnan(cp) or np.isnan(uwr) or np.isnan(lwr):
            wick_labels.append("unknown")
            continue
        if is_bull:
            # Bullish: rejection wick = upper wick is large (failed to hold high)
            if uwr >= 0.30:
                label = "rejection_wick"
            elif cp >= 0.75 and uwr < 0.15:
                label = "strong_close"
            else:
                label = "mid_close"
        else:
            # Bearish: rejection wick = lower wick is large (failed to hold low)
            if lwr >= 0.30:
                label = "rejection_wick"
            elif cp <= 0.25 and lwr < 0.15:
                label = "strong_close"
            else:
                label = "mid_close"
        wick_labels.append(label)

    out["wick_bucket"] = wick_labels


# ---------------------------------------------------------------------------
# Volatility features
# ---------------------------------------------------------------------------

def _add_volatility_features(
    out: pd.DataFrame,
    tf_bars: pd.DataFrame,
    cfg: Dict,
    tick_size: float,
) -> None:
    """Mutates out in-place."""
    atr_buckets  = cfg.get("candle_range_as_atr_buckets",
                            DEFAULT_VOLAT_CONTEXT["candle_range_as_atr_buckets"])
    rar_buckets  = cfg.get("range_vs_avg_range_buckets",
                            DEFAULT_VOLAT_CONTEXT["range_vs_avg_range_buckets"])
    avg_lookbacks = [int(n) for n in cfg.get("avg_range_lookbacks", [10])]
    primary_lb    = avg_lookbacks[0]

    # range_as_atr = total_range / atr (both already in event row as price-unit values)
    total_range_arr = out["total_range"].values.astype(float)
    atr_arr         = out["atr"].values.astype(float) if "atr" in out.columns else np.full(len(out), np.nan)

    with np.errstate(divide="ignore", invalid="ignore"):
        range_as_atr = np.where(
            (atr_arr > 0) & ~np.isnan(atr_arr),
            total_range_arr / atr_arr,
            np.nan,
        )

    out["range_as_atr"] = range_as_atr.round(3)
    out["atr_bucket"]   = [assign_labeled_bucket(v, atr_buckets) for v in range_as_atr]

    # Rolling avg range from tf_bars
    if tf_bars is not None and not tf_bars.empty and "total_range" in tf_bars.columns:
        range_series = tf_bars["total_range"].astype(float)
        rolling_avg  = range_series.shift(1).rolling(primary_lb, min_periods=1).mean().values
        bar_indices  = out["bar_idx"].values.astype(int)
        n_bars       = len(tf_bars)

        avg_range  = np.full(len(out), np.nan)
        rvsr       = np.full(len(out), np.nan)  # range_vs_avg_range

        for i, bar_idx in enumerate(bar_indices):
            if 0 <= bar_idx < n_bars:
                av = float(rolling_avg[bar_idx])
                tr = total_range_arr[i]
                avg_range[i] = av
                if av > 0:
                    rvsr[i] = tr / av
    else:
        avg_range = np.full(len(out), np.nan)
        rvsr      = np.full(len(out), np.nan)

    out["avg_range"]              = avg_range.round(4)
    out["range_vs_avg_range"]     = rvsr.round(3)
    out["range_avg_bucket"]       = [assign_labeled_bucket(v, rar_buckets) for v in rvsr]
