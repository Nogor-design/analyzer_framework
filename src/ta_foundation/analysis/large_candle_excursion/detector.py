from __future__ import annotations

"""
Large Candle Detector
=====================
Identifies candles whose size (body or range) exceeds a configurable
threshold relative to a lag-safe rolling average.

Reuses compute_candle_features() from the existing candle module — all
look-ahead bias protections are inherited.

Qualifying candle conditions
----------------------------
  ratio = current_size / rolling_mean(prev_N_bars)
  qualifies when ratio >= threshold  (multiplier mode)
                  or ratio >= threshold / 100  (percent_of_average mode)

  Plus optional min/max tick-size guards.
  Plus direction filter: long_only (bullish candles), short_only, both.

Output
------
One row per qualifying (bar × lookback × basis × threshold) combination.
The same physical bar appears multiple times when multiple combos fire on it.
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ta_foundation.analysis.entry_strategies.candle.features import compute_candle_features


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_DETECTOR_CONFIG: Dict[str, Any] = {
    "average_lookbacks": [10, 20],
    "average_basis": {
        "body": True,
        "range": True,
    },
    "threshold_mode": "multiplier",      # "multiplier" | "percent_of_average"
    "threshold_multipliers": [1.5, 2.0],
    "threshold_percents": [150, 200],    # used only when threshold_mode = percent_of_average
    "min_body_ticks": 2,
    "min_range_ticks": 4,
    "max_body_ticks": None,              # None → no upper cap
    "max_range_ticks": None,
    "tick_size": 0.25,
    "atr_period": 14,
    "direction": "both",                 # "both" | "long_only" | "short_only"
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_large_candles(
    bars: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """
    Detect qualifying large candles from a bars DataFrame.

    Parameters
    ----------
    bars   : DataFrame with at minimum dt, open, high, low, close.
             Candle features are computed internally if not already present.
    config : Optional config dict merged with DEFAULT_DETECTOR_CONFIG.

    Returns
    -------
    DataFrame of qualifying candle events.  May be empty.
    Each row is a unique (bar × lookback × basis × threshold) combination.

    Columns
    -------
    bar_idx           positional index in *bars* (after feature computation)
    dt                bar open timestamp (tz-aware America/Denver)
    open/high/low/close
    body              abs(close - open)
    total_range       high - low
    body_ticks        body / tick_size
    size_ticks        total_range / tick_size
    is_bullish        True when close >= open
    direction         1 (bullish) | -1 (bearish)
    basis             "body" | "range" — which size metric triggered
    lookback          rolling window used for the average
    threshold_mode    "multiplier" | "percent_of_average"
    threshold_value   the raw threshold used (multiplier or fraction)
    rolling_avg_used  the rolling average value that was compared
    ratio             actual ratio (current_size / rolling_avg_used)
    atr               ATR-14 in price units (NaN if unavailable)
    """
    cfg: Dict[str, Any] = {**DEFAULT_DETECTOR_CONFIG, **(config or {})}

    tick_size       = float(cfg.get("tick_size", 0.25))
    direction_mode  = str(cfg.get("direction", "both"))
    lookbacks       = [int(n) for n in cfg.get("average_lookbacks", [10, 20])]
    basis_cfg       = cfg.get("average_basis", {"body": True, "range": True})
    threshold_mode  = str(cfg.get("threshold_mode", "multiplier"))
    min_body_ticks  = float(cfg.get("min_body_ticks", 2))
    min_range_ticks = float(cfg.get("min_range_ticks", 4))
    max_body_ticks  = cfg.get("max_body_ticks")
    max_range_ticks = cfg.get("max_range_ticks")

    if threshold_mode == "multiplier":
        thresholds = [float(v) for v in cfg.get("threshold_multipliers", [1.5, 2.0])]
    else:
        # percent_of_average: 150 means 1.5x
        thresholds = [float(v) / 100.0 for v in cfg.get("threshold_percents", [150, 200])]

    # Build feature config
    feat_cfg: Dict[str, Any] = {
        "size_lookbacks": lookbacks,
        "atr_period": int(cfg.get("atr_period", 14)),
        "tick_size": tick_size,
        "min_body_ticks": 1,
    }

    # Compute candle features if not already enriched
    check_col = f"body_vs_roll_{lookbacks[0]}"
    if check_col not in bars.columns:
        enriched = compute_candle_features(bars, feat_cfg)
    else:
        enriched = bars.copy().reset_index(drop=True)

    n = len(enriched)

    # Direction mask
    is_bull = enriched["is_bullish"].values.astype(bool)
    if direction_mode == "long_only":
        dir_mask = is_bull
    elif direction_mode == "short_only":
        dir_mask = ~is_bull
    else:
        dir_mask = np.ones(n, dtype=bool)

    # Determine which bases to test
    active_bases: List[str] = []
    if basis_cfg.get("body", True):
        active_bases.append("body")
    if basis_cfg.get("range", True):
        active_bases.append("range")

    event_records: List[Dict[str, Any]] = []

    for lookback in lookbacks:
        for basis in active_bases:
            ratio_col = f"body_vs_roll_{lookback}" if basis == "body" else f"size_vs_roll_{lookback}"
            if ratio_col not in enriched.columns:
                continue

            ratio_arr   = enriched[ratio_col].values.astype(float)
            ref_arr     = enriched["body"].values.astype(float) if basis == "body" else enriched["total_range"].values.astype(float)
            ticks_arr   = enriched["body_ticks"].values.astype(float) if basis == "body" else enriched["size_ticks"].values.astype(float)
            min_ticks   = min_body_ticks if basis == "body" else min_range_ticks
            max_ticks   = max_body_ticks if basis == "body" else max_range_ticks

            # Rolling average is derivable: ref / ratio (since ratio = ref / rolling_avg)
            with np.errstate(divide="ignore", invalid="ignore"):
                rolling_avg_arr = np.where(ratio_arr > 0, ref_arr / ratio_arr, np.nan)

            for threshold in thresholds:
                qual = (
                    (ratio_arr >= threshold)
                    & dir_mask
                    & ~np.isnan(ratio_arr)
                    & (ticks_arr >= min_ticks)
                )
                if max_ticks is not None:
                    qual &= ticks_arr <= float(max_ticks)

                for i in np.where(qual)[0]:
                    row = enriched.iloc[i]
                    is_b = bool(is_bull[i])
                    event_records.append({
                        "bar_idx":          int(i),
                        "dt":               row["dt"],
                        "open":             round(float(row["open"]), 4),
                        "high":             round(float(row["high"]), 4),
                        "low":              round(float(row["low"]), 4),
                        "close":            round(float(row["close"]), 4),
                        "body":             round(float(row["body"]), 4),
                        "total_range":      round(float(row["total_range"]), 4),
                        "body_ticks":       round(float(row["body_ticks"]), 2),
                        "size_ticks":       round(float(row["size_ticks"]), 2),
                        "is_bullish":       is_b,
                        "direction":        1 if is_b else -1,
                        "basis":            basis,
                        "lookback":         lookback,
                        "threshold_mode":   threshold_mode,
                        "threshold_value":  threshold,
                        "rolling_avg_used": round(float(rolling_avg_arr[i]), 4) if not np.isnan(rolling_avg_arr[i]) else None,
                        "ratio":            round(float(ratio_arr[i]), 3),
                        "atr":              round(float(row["atr"]), 4) if "atr" in enriched.columns and not np.isnan(float(row.get("atr", np.nan))) else None,
                    })

    if not event_records:
        return pd.DataFrame()

    return pd.DataFrame(event_records).reset_index(drop=True)
