from __future__ import annotations

"""
Large Candle Forward Excursion — Sweep Orchestrator
=====================================================
Runs the full large-candle forward-excursion analysis pipeline.

For each combination of:
  timeframe × lookback × basis × threshold × forward_window_minutes

  1. Resample 1m bars to the target timeframe.
  2. Detect qualifying large candles  (detector.py)
  3. Compute forward excursion on 1m bars  (forward_window.py)
  4. Optionally append tick-path reversal metrics  (tick_analyzer.py)
  5. Aggregate per-combo statistics.

Results are returned as a JSON-safe dict suitable for storage under
pkg.metadata["derived"]["large_candle_excursion"].
"""

from itertools import product
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ta_foundation.marketdata.resample import ohlcv_resample_from_bars
from ta_foundation.analysis.large_candle_excursion.detector import detect_large_candles
from ta_foundation.analysis.large_candle_excursion.forward_window import compute_forward_excursion
from ta_foundation.analysis.large_candle_excursion.tick_analyzer import compute_tick_excursion
from ta_foundation.analysis.large_candle_excursion.trade_analyzer import (
    compute_trade_outcomes,
    DEFAULT_TRADE_CONFIG,
)
from ta_foundation.analysis.large_candle_excursion.context_enricher import (
    enrich_events_with_context,
    DEFAULT_VOL_CONTEXT,
    DEFAULT_STRUCT_CONTEXT,
    DEFAULT_VOLAT_CONTEXT,
)
from ta_foundation.analysis.large_candle_excursion.signal_candle_context import (
    DEFAULT_DIRECTIONAL_CONTEXT,
    DEFAULT_MA_VWAP_CONTEXT,
    DEFAULT_KEY_LEVEL_CONTEXT,
    DEFAULT_TREND_STATE_CONTEXT,
    DEFAULT_EXHAUSTION_CONTEXT,
)
from ta_foundation.analysis.large_candle_excursion.context_stats import (
    compute_context_analysis,
    compute_time_segment_analysis,
)
from ta_foundation.analysis.large_candle_excursion.session_classifier import add_session_bucket_to_events
from ta_foundation.analysis.large_candle_excursion.target_curve import build_target_curves, curves_to_dict


# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "timeframes": [1, 5],
    "direction": "both",     # "both" | "long_only" | "short_only"
    "min_events": 20,

    "candle_size": {
        "average_lookbacks":   [10, 20],
        "average_basis": {
            "body": True,
            "range": True,
        },
        "threshold_mode":          "multiplier",
        "threshold_multipliers":   [1.5, 2.0, 2.5],
        "threshold_percents":      [150, 200],
        "min_body_ticks":          2,
        "min_range_ticks":         4,
        "max_body_ticks":          None,
        "max_range_ticks":         None,
        "tick_size":               0.25,
        "atr_period":              14,
    },

    "forward_window": {
        "minutes": [5, 10, 15, 30],
    },

    "tick_analysis": {
        "use_tick_data": False,
        "reversal": {
            "enabled":        True,
            "reversal_ticks": 8,
            "min_swing_ticks": 2,
        },
    },

    "trade_analysis": DEFAULT_TRADE_CONFIG,

    "session_analysis": {
        "enabled": True,
        # Custom bucket boundaries are not yet supported at runtime;
        # use this key to toggle session tagging on/off.
    },

    "target_curves": {
        "enabled":                    True,
        "plateau_tolerance":          3.0,   # pp within peak WR = plateau
        "plateau_min_width":          3,     # steps required for "runner" classification
        "scalp_max_target_pct":       50.0,  # peak target% at/below which "scalp" applies
        "scalp_min_edge_decay":       0.20,  # minimum WR decay fraction for "scalp"
        "edge_decay_full_penalty_pp": 50.0,  # pp decay that maxes out edge_decay_penalty
        "fine_sweep": {
            "enabled": False,
            "target_percents": [],
            "focus_target_pct": 20.0,
            "stable_neighbor_drop_pp": 3.0,
            "micro_scalp_max_target_pct": 25.0,
            "micro_scalp_max_plateau_width": 1,
            "micro_scalp_min_edge_decay_penalty": 0.70,
        },
        "time_split_validation": {
            "enabled": False,
            "stable_drop_pp": 8.0,
            "stable_time_split_stability": 0.65,
            "fragile_time_split_stability": 0.40,
        },
    },

    "volume_context":           DEFAULT_VOL_CONTEXT,
    "candle_structure_context": DEFAULT_STRUCT_CONTEXT,
    "volatility_context":       DEFAULT_VOLAT_CONTEXT,
    "directional_context":      DEFAULT_DIRECTIONAL_CONTEXT,
    "ma_vwap_context":          DEFAULT_MA_VWAP_CONTEXT,
    "key_level_context":        DEFAULT_KEY_LEVEL_CONTEXT,
    "trend_state_context":      DEFAULT_TREND_STATE_CONTEXT,
    "exhaustion_context":       DEFAULT_EXHAUSTION_CONTEXT,

    "output": {
        "include_event_table":         True,
        "max_events_sample":           2000,
        "include_methodology":         True,
        "include_distribution_tables": True,
        "include_direction_breakdown": True,
        "include_timeframe_breakdown": True,
        "include_basis_breakdown":     True,
    },
}

# Tick thresholds used in hit-rate calculations
_HIT_THRESHOLDS = [3, 5, 8, 10, 15, 20, 30]
# Percentile points for excursion distributions
_PERCENTILE_POINTS = [10, 25, 50, 75, 90]


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _safe_round(v: float, digits: int = 2) -> Optional[float]:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return round(float(v), digits)


def _agg_ticks(series: pd.Series) -> Dict[str, Any]:
    """Return summary stats dict for a ticks series."""
    s = series.dropna()
    if s.empty:
        return {"mean": None, "median": None, "max": None, "std": None}
    return {
        "mean":   _safe_round(float(s.mean())),
        "median": _safe_round(float(s.median())),
        "max":    _safe_round(float(s.max())),
        "std":    _safe_round(float(s.std())),
    }


def _hit_rates(series: pd.Series) -> Dict[str, float]:
    s = series.dropna()
    n = len(s)
    if n == 0:
        return {f"pct_ge_{t}t": 0.0 for t in _HIT_THRESHOLDS}
    return {f"pct_ge_{t}t": round(float((s >= t).sum() / n * 100), 1) for t in _HIT_THRESHOLDS}


def _percentiles(series: pd.Series) -> Dict[str, Optional[float]]:
    s = series.dropna()
    if s.empty:
        return {f"p{p}": None for p in _PERCENTILE_POINTS}
    return {f"p{p}": _safe_round(float(np.percentile(s, p))) for p in _PERCENTILE_POINTS}


def _compute_combo_stats(
    df: pd.DataFrame,
    tf: int,
    lookback: int,
    basis: str,
    threshold_mode: str,
    threshold_value: float,
    window_minutes: int,
    direction_mode: str,
    use_tick_data: bool,
) -> Dict[str, Any]:
    """Aggregate statistics for one (tf, lookback, basis, threshold, window) combo."""
    n_events  = len(df)
    n_bullish = int((df["direction"] == 1).sum())
    n_bearish = n_events - n_bullish

    fav_s = df["fav_ticks"].dropna()
    adv_s = df["adv_ticks"].dropna()
    ttf_s = df["time_to_max_fav_min"].dropna()
    tta_s = df["time_to_max_adv_min"].dropna()

    pct_fav_dominant = round(float((fav_s > adv_s).mean() * 100), 1) if len(fav_s) > 0 else 0.0
    pct_fav_first = (
        round(float((ttf_s.values < tta_s.values).mean() * 100), 1)
        if len(ttf_s) > 0 and len(tta_s) > 0 and len(ttf_s) == len(tta_s)
        else 0.0
    )

    result: Dict[str, Any] = {
        "tf":               tf,
        "lookback":         lookback,
        "basis":            basis,
        "threshold_mode":   threshold_mode,
        "threshold_value":  threshold_value,
        "window_minutes":   window_minutes,
        "direction":        direction_mode,
        "n_events":         n_events,
        "n_bullish":        n_bullish,
        "n_bearish":        n_bearish,
        "fav_ticks":        _agg_ticks(fav_s),
        "adv_ticks":        _agg_ticks(adv_s),
        "pct_fav_dominant": pct_fav_dominant,   # % where fav_ticks > adv_ticks
        "pct_fav_first":    pct_fav_first,       # % where max fav reached before max adv
        "hit_rates_fav":    _hit_rates(fav_s),
        "hit_rates_adv":    _hit_rates(adv_s),
        "percentiles_fav":  _percentiles(fav_s),
        "percentiles_adv":  _percentiles(adv_s),
        "time_to_max_fav":  _agg_ticks(ttf_s),
        "time_to_max_adv":  _agg_ticks(tta_s),
    }

    if use_tick_data and "tick_pre_reversal_fav_ticks" in df.columns:
        result["tick_pre_reversal_fav"] = _agg_ticks(df["tick_pre_reversal_fav_ticks"].dropna())
        result["tick_pre_reversal_adv"] = _agg_ticks(df["tick_pre_reversal_adv_ticks"].dropna())
        n_rev = int(df["tick_reversal_occurred"].sum()) if "tick_reversal_occurred" in df.columns else 0
        result["tick_pct_reversal"] = round(n_rev / n_events * 100, 1) if n_events > 0 else 0.0

    return result


def _event_to_record(row: pd.Series, use_tick_data: bool) -> Dict[str, Any]:
    """Convert one event row to a JSON-safe record for the events sample."""
    def _v(col: str) -> Any:
        v = row.get(col)
        if v is None:
            return None
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return None if np.isnan(v) else float(v)
        if isinstance(v, pd.Timestamp):
            return v.isoformat()
        return v

    rec: Dict[str, Any] = {
        "dt":               _v("dt") if not isinstance(row.get("dt"), pd.Timestamp) else row["dt"].isoformat(),
        "tf_minutes":       _v("tf_minutes"),
        "window_minutes":   _v("window_minutes"),
        "forward_end_dt":   _v("forward_end_dt"),
        "direction":        _v("direction"),
        "direction_label":  "Bull" if _v("direction") == 1 else "Bear",
        "open":             _v("open"),
        "high":             _v("high"),
        "low":              _v("low"),
        "close":            _v("close"),
        "body":             _v("body"),
        "total_range":      _v("total_range"),
        "body_ticks":       _v("body_ticks"),
        "size_ticks":       _v("size_ticks"),
        "basis":            _v("basis"),
        "lookback":         _v("lookback"),
        "threshold_mode":   _v("threshold_mode"),
        "threshold_value":  _v("threshold_value"),
        "rolling_avg_used": _v("rolling_avg_used"),
        "ratio":            _v("ratio"),
        "n_bars_in_window": _v("n_bars_in_window"),
        "max_up_pts":       _v("max_up_pts"),
        "max_down_pts":     _v("max_down_pts"),
        "fav_ticks":        _v("fav_ticks"),
        "adv_ticks":        _v("adv_ticks"),
        "time_to_max_fav_min": _v("time_to_max_fav_min"),
        "time_to_max_adv_min": _v("time_to_max_adv_min"),
    }
    if use_tick_data:
        rec["tick_pre_reversal_fav_ticks"] = _v("tick_pre_reversal_fav_ticks")
        rec["tick_pre_reversal_adv_ticks"] = _v("tick_pre_reversal_adv_ticks")
        rec["tick_reversal_occurred"]       = _v("tick_reversal_occurred")

    # Context columns — serialize if present (safe: all are str or numeric)
    _CONTEXT_COLS = [
        "session_bucket", "candle_bucket",
        # directional
        "prev_direction_bucket", "streak_bucket", "engulf_bucket",
        "directional_context_label",
        "prev_direction", "same_as_prev", "direction_streak",
        # volume / structure / volatility raw features
        "candle_volume", "avg_volume", "relative_volume", "vol_bucket",
        "upper_wick_ticks", "lower_wick_ticks",
        "body_to_range_ratio", "upper_wick_to_range_ratio", "lower_wick_to_range_ratio",
        "close_position_in_range", "close_pos_bucket", "body_range_bucket", "wick_bucket",
        "range_as_atr", "avg_range", "range_vs_avg_range", "atr_bucket", "range_avg_bucket",
        # MA/VWAP
        "ma100_location_bucket", "ma200_location_bucket", "vwap_location_bucket",
        "ma100_ext_bucket", "ma200_ext_bucket", "vwap_ext_bucket",
        "ma100_signed_bucket", "ma200_signed_bucket", "vwap_signed_bucket",
        "above_ma100", "above_ma200", "above_vwap",
        "dist_ma100_atr", "dist_ma200_atr", "dist_vwap_atr",
        # key level
        "nearest_level_type", "level_interaction_label",
        "dist_nearest_level_ticks", "dist_nearest_level_atr",
        # trend state
        "trend_alignment_label", "ma100_slope_label", "ma200_slope_label",
        "stacked_above_mas", "stacked_below_mas",
        # exhaustion / extension (derived)
        "exhaustion_label",
        # early-path bar-level features (from forward_window.py)
        "early_fav_1bar_ticks", "early_fav_2bar_ticks", "early_fav_3bar_ticks",
        "early_adv_1bar_ticks", "early_adv_2bar_ticks", "early_adv_3bar_ticks",
        "did_price_reclaim_signal_midpoint",
        "did_price_break_signal_extreme_again",
        "time_to_first_pullback_bars",
        "first_pullback_size_ticks",
    ]
    for col in _CONTEXT_COLS:
        v = row.get(col)
        if v is not None:
            if isinstance(v, (np.integer,)):
                rec[col] = int(v)
            elif isinstance(v, (np.floating,)):
                rec[col] = None if np.isnan(v) else float(v)
            elif isinstance(v, (bool, np.bool_)):
                rec[col] = bool(v)
            elif isinstance(v, pd.Timestamp):
                rec[col] = v.isoformat()
            else:
                rec[col] = v

    return rec


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_large_candle_excursion(
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
    ticks: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Run the large-candle forward-excursion analysis.

    Parameters
    ----------
    bars_1m : 1-minute bars with dt, open, high, low, close columns.
              Must be tz-aware America/Denver.  Apply session filter before calling.
    config  : analysis config dict (merged with DEFAULT_CONFIG).
    ticks   : optional tick data DataFrame (dt, last columns) for tick-path analysis.
              Required only when config["tick_analysis"]["use_tick_data"] = True.

    Returns
    -------
    JSON-safe dict stored under pkg.metadata["derived"]["large_candle_excursion"].

    Keys
    ----
    enabled            : True
    n_combinations_run : total parameter combinations attempted
    n_results          : combinations that produced >= min_events
    combo_results      : list of per-combination aggregated stats
    events_sample      : list of individual event records (capped)
    config             : effective config used
    """
    cfg: Dict[str, Any] = _deep_merge(DEFAULT_CONFIG, config or {})

    timeframes: List[int]      = [int(tf) for tf in cfg.get("timeframes", [1, 5])]
    direction_mode: str        = str(cfg.get("direction", "both"))
    min_events: int            = int(cfg.get("min_events", 20))
    window_minutes_list: List[int] = [int(w) for w in cfg.get("forward_window", {}).get("minutes", [5, 10, 15, 30])]
    output_cfg: Dict           = cfg.get("output", {})
    max_sample: int            = int(output_cfg.get("max_events_sample", 2000))

    candle_cfg: Dict           = cfg.get("candle_size", {})
    tick_size: float           = float(candle_cfg.get("tick_size", 0.25))

    tick_cfg: Dict             = cfg.get("tick_analysis", {})
    use_tick_data: bool        = bool(tick_cfg.get("use_tick_data", False)) and ticks is not None

    # Build detector config from candle_size section + top-level direction
    detector_cfg: Dict[str, Any] = {
        **candle_cfg,
        "direction": direction_mode,
    }

    # Tick reversal config
    reversal_cfg: Dict         = tick_cfg.get("reversal", {})
    tick_reversal_cfg: Dict    = {
        "reversal_ticks": float(reversal_cfg.get("reversal_ticks", 8)),
        "min_swing_ticks": float(reversal_cfg.get("min_swing_ticks", 2)),
    }

    # Session analysis config
    session_cfg: Dict          = cfg.get("session_analysis", {})
    run_session_analysis: bool = bool(session_cfg.get("enabled", True))

    # Trade analysis config
    trade_cfg_raw: Dict        = cfg.get("trade_analysis", {})
    trade_cfg: Dict            = _deep_merge(DEFAULT_TRADE_CONFIG, trade_cfg_raw)
    run_trade_analysis: bool   = bool(trade_cfg.get("enabled", False))

    # Target curves config (requires trade_analysis.enabled)
    tc_cfg: Dict               = cfg.get("target_curves", {})
    run_target_curves: bool    = bool(tc_cfg.get("enabled", True)) and run_trade_analysis

    # Context configs
    vol_cfg:        Dict = _deep_merge(DEFAULT_VOL_CONTEXT,           cfg.get("volume_context", {}))
    struct_cfg:     Dict = _deep_merge(DEFAULT_STRUCT_CONTEXT,        cfg.get("candle_structure_context", {}))
    volat_cfg:      Dict = _deep_merge(DEFAULT_VOLAT_CONTEXT,         cfg.get("volatility_context", {}))
    dir_cfg:        Dict = _deep_merge(DEFAULT_DIRECTIONAL_CONTEXT,   cfg.get("directional_context", {}))
    ma_vwap_cfg:    Dict = _deep_merge(DEFAULT_MA_VWAP_CONTEXT,       cfg.get("ma_vwap_context", {}))
    key_level_cfg:  Dict = _deep_merge(DEFAULT_KEY_LEVEL_CONTEXT,     cfg.get("key_level_context", {}))
    trend_cfg:      Dict = _deep_merge(DEFAULT_TREND_STATE_CONTEXT,   cfg.get("trend_state_context", {}))
    exhaustion_cfg: Dict = _deep_merge(DEFAULT_EXHAUSTION_CONTEXT,    cfg.get("exhaustion_context", {}))
    any_context: bool = (
        vol_cfg.get("enabled", False) or
        struct_cfg.get("enabled", False) or
        volat_cfg.get("enabled", False) or
        dir_cfg.get("enabled", False) or
        ma_vwap_cfg.get("enabled", False) or
        key_level_cfg.get("enabled", False) or
        trend_cfg.get("enabled", False) or
        exhaustion_cfg.get("enabled", False)
    )

    # Time segment analysis config — pre-computed so the inner loop can gate correctly
    time_seg_cfg: Dict  = cfg.get("time_segment_analysis", {})
    run_time_seg: bool  = bool(time_seg_cfg.get("enabled", False))

    combo_results:  List[Dict] = []
    all_events:     List[Dict] = []
    all_fwd_dfs:    List       = []        # collected for trade analysis + context + time segments
    n_combinations_run: int    = 0

    for tf in timeframes:
        # Resample to target timeframe
        if tf == 1:
            tf_bars = bars_1m.copy().reset_index(drop=True)
        else:
            tf_bars = ohlcv_resample_from_bars(bars_1m, f"{tf}m")

        if tf_bars is None or tf_bars.empty:
            continue

        # Detect qualifying events for all combos in one pass
        events_all = detect_large_candles(tf_bars, detector_cfg)

        if events_all is None or events_all.empty:
            print(f"[large_candle_excursion] TF={tf}m: no qualifying candles detected.")
            continue

        # Add session bucket (configurable; enabled by default)
        if run_session_analysis:
            events_all = add_session_bucket_to_events(events_all)

        # Enrich events with all enabled context modules
        if any_context:
            events_all = enrich_events_with_context(
                events_all, tf_bars,
                vol_cfg, struct_cfg, volat_cfg,
                dir_cfg, ma_vwap_cfg, key_level_cfg, trend_cfg,
                exhaustion_cfg=exhaustion_cfg,
                tick_size=tick_size,
            )

        # Add candle_bucket from size_ticks so interaction tables work
        if "size_ticks" in events_all.columns:
            size_buckets = trade_cfg.get("candle_size_buckets_ticks",
                                         DEFAULT_TRADE_CONFIG["candle_size_buckets_ticks"])
            from ta_foundation.analysis.large_candle_excursion.trade_analyzer import assign_candle_bucket
            events_all["candle_bucket"] = [
                assign_candle_bucket(float(v), size_buckets)
                if v is not None and not (isinstance(v, float) and np.isnan(v)) else "unknown"
                for v in events_all["size_ticks"]
            ]

        # Group by the combo dimensions to avoid redundant forward scans
        combo_groups = events_all.groupby(
            ["lookback", "basis", "threshold_mode", "threshold_value"],
            sort=False,
        )

        for (lookback, basis, threshold_mode, threshold_value), group in combo_groups:
            if len(group) < min_events:
                continue

            # Compute forward excursion for all window_minutes at once
            fwd_df = compute_forward_excursion(
                group.reset_index(drop=True),
                bars_1m,
                window_minutes_list,
                tf_minutes=tf,
                tick_size=tick_size,
            )

            if fwd_df is None or fwd_df.empty:
                continue

            # Collect for trade analysis, context stats, and time segment analysis
            if run_trade_analysis or any_context or run_time_seg:
                all_fwd_dfs.append(fwd_df)

            # Optionally append tick-path metrics
            if use_tick_data and reversal_cfg.get("enabled", True):
                fwd_df = compute_tick_excursion(
                    fwd_df,
                    ticks,
                    tf_minutes=tf,
                    tick_size=tick_size,
                    config=tick_reversal_cfg,
                )

            # Aggregate per window_minutes
            for window_minutes in window_minutes_list:
                n_combinations_run += 1

                w_df = fwd_df[fwd_df["window_minutes"] == window_minutes].copy()
                if len(w_df) < min_events:
                    continue

                stats = _compute_combo_stats(
                    w_df,
                    tf=tf,
                    lookback=int(lookback),
                    basis=str(basis),
                    threshold_mode=str(threshold_mode),
                    threshold_value=float(threshold_value),
                    window_minutes=window_minutes,
                    direction_mode=direction_mode,
                    use_tick_data=use_tick_data,
                )
                combo_results.append(stats)

                # Collect events for sample (cap at max_sample total)
                if len(all_events) < max_sample:
                    for _, row in w_df.iterrows():
                        if len(all_events) >= max_sample:
                            break
                        all_events.append(_event_to_record(row, use_tick_data))

    # Run trade outcome analysis + context stats across collected forward-excursion rows
    combined_fwd: Optional[pd.DataFrame] = None
    if all_fwd_dfs:
        combined_fwd = pd.concat(all_fwd_dfs, ignore_index=True)

    trade_analysis_result: Dict = {"enabled": False}
    if run_trade_analysis and combined_fwd is not None:
        trade_analysis_result = compute_trade_outcomes(combined_fwd, trade_cfg, tick_size)

    # Build target curves from trade combo results (requires trade_analysis + enabled)
    target_curves_result: Dict = {"enabled": False}
    if run_target_curves and isinstance(trade_analysis_result, dict):
        raw_combos = trade_analysis_result.get("combo_results") or trade_analysis_result.get("trade_combo_results", [])
        if raw_combos:
            curves = build_target_curves(raw_combos, config=tc_cfg)
            target_curves_result = {
                "enabled": True,
                "n_setups": len(curves),
                "curves": curves_to_dict(curves),
                "config": {k: tc_cfg[k] for k in tc_cfg if k != "enabled"},
            }

    context_analysis_result: Dict = {"enabled": False}
    if any_context and combined_fwd is not None:
        context_analysis_result = compute_context_analysis(
            combined_fwd, vol_cfg, struct_cfg, volat_cfg,
            trade_cfg if run_trade_analysis else None,
            directional_cfg=dir_cfg,
            ma_vwap_cfg=ma_vwap_cfg,
            key_level_cfg=key_level_cfg,
            trend_state_cfg=trend_cfg,
            exhaustion_cfg=exhaustion_cfg,
        )

    # Time segment analysis — user-defined overlapping windows
    time_segment_result: Dict = {"enabled": False}
    if run_time_seg and combined_fwd is not None:
        time_segment_result = compute_time_segment_analysis(
            combined_fwd,
            time_seg_cfg,
            trade_cfg=trade_cfg if run_trade_analysis else None,
        )

    print(
        f"[large_candle_excursion] {n_combinations_run} combos run, "
        f"{len(combo_results)} results, {len(all_events)} events sampled."
    )

    result: Dict[str, Any] = {
        "enabled":            True,
        "n_combinations_run": n_combinations_run,
        "n_results":          len(combo_results),
        "combo_results":      combo_results,
        "events_sample":      all_events,
        "tick_data_used":     use_tick_data,
        "config":             _json_safe_config(cfg),
    }
    if run_trade_analysis:
        result["trade_analysis"] = trade_analysis_result
    if run_target_curves or run_trade_analysis:
        result["target_curves"] = target_curves_result
    if any_context:
        result["context_analysis"] = context_analysis_result
    if run_time_seg:
        result["time_segment_analysis"] = time_segment_result
    return result


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _deep_merge(base: Dict, override: Dict) -> Dict:
    """Recursively merge override into base (override wins at leaf level)."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _json_safe_config(cfg: Dict) -> Dict:
    """Strip non-serializable objects from config dict for storage in metadata."""
    out: Dict = {}
    for k, v in cfg.items():
        if isinstance(v, dict):
            out[k] = _json_safe_config(v)
        elif isinstance(v, (list, tuple)):
            out[k] = [_json_safe_value(x) for x in v]
        else:
            out[k] = _json_safe_value(v)
    return out


def _json_safe_value(v: Any) -> Any:
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, dict):
        return _json_safe_config(v)
    return v
