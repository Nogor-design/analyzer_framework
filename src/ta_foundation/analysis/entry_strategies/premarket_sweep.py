from __future__ import annotations

"""
Pre-Market Direction Predictor Sweep
======================================
Answers the question: "Do patterns in the pre-market window predict which
direction the NY open will move?"

Key architectural difference from ``candle_discovery``:
  Signal detection  → restricted to PRE-MARKET bars only (e.g. 06:00-07:29 Denver)
  Outcome simulation→ uses the FULL bar set so the simulator can scan forward
                      into RTH bars starting at the NY open (07:30 Denver).

This correctly models the real-world scenario:
  1. You observe a pattern on the last premarket bar (e.g. 07:29 bar).
  2. You enter at the NY open bell (07:30 bar's open) via ``next_open`` timing.
  3. The trade resolves using RTH bars — the pre-market data is NOT used for
     forward scanning.

Pipeline per combination:
  TF × pattern × param-combo × direction × timing-mode × outcome-config
    1. Resample full 1m bars to TF (for ATR context)
    2. Compute candle features on FULL resampled bars (ATR uses prior history)
    3. Filter enriched bars to the pre-market window → enriched_pm
    4. Compute premarket session features on enriched_pm (pm_direction, pm_range, …)
    5. Detect patterns on enriched_pm
    6. Emit entries (next_open = 07:30 bar)
    7. Simulate outcomes using FULL bars_1m (RTH forward-scan)
    8. Compute metrics, regime breakdown, filter discovery
    9. Store result

Results are returned as a JSON-safe dict compatible with
``pkg.metadata["derived"]["premarket_discovery"]``.

YAML config shape (``premarket_discovery`` block)
--------------------------------------------------
premarket_discovery:
  enabled: true
  min_trades: 15
  timeframes: [1, 5]

  # Pre-market window to scan for signals (America/Denver)
  signal_window:
    hour_from:   6
    minute_from: 0
    hour_to:     7
    minute_to:   29   # last 1m bar before 07:30 NY open

  # Only simulate outcomes in this window (optional; default = all RTH)
  outcome_window:
    hour_from: 7
    minute_from: 30
    hour_to: 9

  candle_features: { ... }   # same as candle_discovery
  patterns:        { ... }   # same as candle_discovery
  direction:       both
  entry_timing:    { ... }   # same as candle_discovery
  outcome:         { ... }   # same as candle_discovery
  filter_discovery:{ enabled: true, top_n: 10 }
"""

from itertools import product
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ta_foundation.marketdata.resample import ohlcv_resample_from_bars
from ta_foundation.analysis.entry_strategies.candle.features import compute_candle_features
from ta_foundation.analysis.entry_strategies.candle.patterns import (
    PATTERN_REGISTRY, detect_pattern,
)
from ta_foundation.analysis.entry_strategies.candle.signals import emit_entries
from ta_foundation.analysis.entry_strategies.premarket.features import (
    compute_premarket_session_features,
)
from ta_foundation.analysis.entry_strategies.outcome.simulator import simulate_outcomes
from ta_foundation.analysis.strategy_discovery.evaluation import (
    compute_evaluation_metrics,
    compute_regime_breakdown,
)
from ta_foundation.analysis.entry_strategies.validation import compute_is_oos_degradation


# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------

DEFAULT_PREMARKET_DISCOVERY_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "timeframes": [1, 5],
    "min_trades": 15,

    # Pre-market window: bars eligible to generate signals
    "signal_window": {
        "hour_from":   6,
        "minute_from": 0,
        "hour_to":     7,
        "minute_to":   29,   # 07:29 = last 1m bar before 07:30 NY open
    },

    "candle_features": {
        "size_lookbacks": [5, 10, 20],
        "atr_period": 14,
        "tick_size": 0.25,
        "min_body_ticks": 1,
    },

    "patterns": {
        "large_body":         {"enabled": True, "body_multiplier": [1.5, 2.0], "wick_to_body_max": [0.3, 0.5], "lookback": [10, 20]},
        "pin_bar_bullish":    {"enabled": True, "wick_to_body_min": [1.5, 2.0], "body_to_range_max": [0.30, 0.35]},
        "pin_bar_bearish":    {"enabled": True, "wick_to_body_min": [1.5, 2.0], "body_to_range_max": [0.30, 0.35]},
        "inside_bar":         {"enabled": True},
        "outside_bar":        {"enabled": True},
        "engulfing_bullish":  {"enabled": True, "engulf_ratio": [1.0, 1.2]},
        "engulfing_bearish":  {"enabled": True, "engulf_ratio": [1.0, 1.2]},
        "doji":               {"enabled": True, "body_to_range_max": [0.10, 0.15]},
        "clean_breakout_bar": {"enabled": True, "atr_mult": [1.5, 2.0], "body_to_range_min": [0.60, 0.70]},
    },

    "direction": "both",

    "entry_timing": {
        "next_open":     {"enabled": True},
        "break_extreme": {"enabled": True, "buffer_ticks": 1, "fill_timeout_bars": 3},
        "body_midpoint": {"enabled": True, "fill_timeout_bars": 5},
    },

    "outcome": {
        "atr":   {"enabled": True,  "target_mult": 1.5, "stop_mult": 1.0},
        "ticks": {"enabled": True,  "take_profit": [10, 15, 20, 30], "stop": [8, 10, 12, 15]},
        "max_bars_timeout": 10,
        "timeout_result": "loss",
        "tick_size":  0.25,
        "tick_value": 5.00,
        "commission_per_side": 2.09,
        "slippage_ticks": 1,
    },

    "filter_discovery": {"enabled": True, "top_n": 10},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: Dict, override: Dict) -> Dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _direction_values(direction_cfg: str) -> List[int]:
    if direction_cfg == "long_only":
        return [1]
    if direction_cfg == "short_only":
        return [-1]
    return [1, -1]


def _filter_to_window(bars: pd.DataFrame, window: Dict[str, Any]) -> pd.DataFrame:
    """
    Filter bars to a time-of-day window.  Uses America/Denver hour/minute.
    Returns filtered copy with reset index.
    """
    if bars is None or bars.empty or not window:
        return bars

    dt_s = pd.to_datetime(bars["dt"] if "dt" in bars.columns else bars.index)
    if dt_s.dt.tz is not None:
        local = dt_s.dt.tz_convert("America/Denver")
    else:
        local = dt_s

    hour   = local.dt.hour
    minute = local.dt.minute

    h_from = int(window.get("hour_from", 0))
    m_from = int(window.get("minute_from", 0))
    h_to   = int(window.get("hour_to", 23))
    m_to   = int(window.get("minute_to", 59))

    if m_from > 0:
        start = (hour > h_from) | ((hour == h_from) & (minute >= m_from))
    else:
        start = hour >= h_from

    if m_to < 59:
        end = (hour < h_to) | ((hour == h_to) & (minute <= m_to))
    else:
        end = hour <= h_to

    mask = start & end
    return bars[mask.values].reset_index(drop=True)


def _expand_pattern_params(pat_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    list_keys   = {k: v for k, v in pat_cfg.items() if isinstance(v, list) and k != "enabled"}
    scalar_keys = {k: v for k, v in pat_cfg.items() if not isinstance(v, list) and k != "enabled"}
    if not list_keys:
        return [{**scalar_keys}]
    keys   = list(list_keys.keys())
    values = [list_keys[k] for k in keys]
    combos = []
    for combo in product(*values):
        p = {**scalar_keys}
        for k, v in zip(keys, combo):
            p[k] = v
        combos.append(p)
    return combos


def _try_filter_discovery(trades_df: pd.DataFrame, filter_cfg: Dict[str, Any]) -> List[Dict]:
    if not filter_cfg.get("enabled", False):
        return []
    try:
        from ta_foundation.analysis.strategy_discovery.filter_discovery import run_filter_discovery
        top_n = int(filter_cfg.get("top_n", 10))
        results = run_filter_discovery(trades_df, options={"top_n": top_n})
        if results and isinstance(results, list):
            return results[:top_n]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Single combination runner
# ---------------------------------------------------------------------------

def _run_combo(
    enriched_pm: pd.DataFrame,      # premarket-filtered enriched bars (signals from here)
    bars_1m_full: pd.DataFrame,      # full 1m bars (outcome simulation scans forward here)
    bars_tf: pd.DataFrame,           # premarket+nearby resampled bars at tf (for next_open lookup)
    pattern_id: str,
    params: Dict[str, Any],
    direction: int,
    timing_mode: str,
    timing_cfg: Dict[str, Any],
    outcome_cfg: Dict[str, Any],
    bars_with_regime: Optional[pd.DataFrame],
    tf_minutes: int,
    min_trades: int,
    filter_cfg: Dict[str, Any],
) -> Optional[List[Dict[str, Any]]]:
    """Run one full pipeline combination and return a list of SweepResult dicts."""

    # 1. Detect signals on premarket bars
    detect_params = {**params, "direction": direction}
    signals_df = detect_pattern(pattern_id, enriched_pm, detect_params)
    if signals_df is None or signals_df.empty:
        return None

    n_signals = len(signals_df)

    # 2. Emit entries
    timing_params = {**timing_cfg, "tick_size": outcome_cfg.get("tick_size", 0.25)}

    # For next_open timing we need the next bar after the signal bar.
    # We pass the FULL resampled bars (not just PM) so the lookup can find
    # the 07:30 bar that immediately follows the last PM bar.
    bars_for_timing = bars_tf if timing_mode == "next_open" else None

    try:
        pending = emit_entries(signals_df, timing_mode, bars=bars_for_timing, params=timing_params)
    except Exception:
        return None

    if pending is None or pending.empty:
        return None

    # 3. Simulate outcomes using the FULL 1m bar set
    #    (outcome simulator scans forward into RTH bars from the entry bar)
    trades_df = simulate_outcomes(pending, bars_1m_full, outcome_cfg)
    if trades_df is None or trades_df.empty:
        return None

    all_results = []

    for om, group in trades_df.groupby("outcome_mode"):
        if len(group) < min_trades:
            continue

        # 4. Session label
        if "entry_time" in group.columns and "session_label" not in group.columns:
            try:
                from ta_foundation.analysis.session_constants import session_label_from_hour
                entry_dt = pd.to_datetime(group["entry_time"])
                if entry_dt.dt.tz is not None:
                    local_dt = entry_dt.dt.tz_convert("America/Denver")
                else:
                    local_dt = entry_dt
                group = group.copy()
                group["session_label"] = local_dt.dt.hour.map(session_label_from_hour)
            except Exception:
                pass

        # 5. Evaluation metrics
        try:
            metrics = compute_evaluation_metrics(group, profit_col="profit_net")
        except Exception:
            metrics = {}

        # 6. Regime breakdown
        regime_bk: Dict[str, Any] = {}
        if bars_with_regime is not None:
            try:
                regime_bk = compute_regime_breakdown(group, bars_with_regime, profit_col="profit_net")
            except Exception:
                pass

        # 7. Session breakdown
        session_bk: Dict[str, Any] = {}
        if "session_label" in group.columns:
            try:
                for sess, sg in group.groupby("session_label"):
                    profits = pd.to_numeric(sg["profit_net"], errors="coerce").dropna()
                    if len(profits) == 0:
                        continue
                    n_w = int((profits > 0).sum())
                    gp  = float(profits[profits > 0].sum())
                    gl  = abs(float(profits[profits < 0].sum()))
                    session_bk[str(sess)] = {
                        "n_trades":     len(profits),
                        "win_rate":     round(n_w / len(profits), 4),
                        "net_profit":   round(float(profits.sum()), 2),
                        "profit_factor": round(gp / gl, 4) if gl > 0 else None,
                    }
            except Exception:
                pass

        # 8. Pre-market feature breakdown
        # Compute PF split by pm_session_direction to show directional edge
        pm_breakdown: Dict[str, Any] = {}
        pm_dir_col = "pm_session_direction"
        if pm_dir_col in group.columns:
            try:
                for pm_dir, pg in group.groupby(pm_dir_col):
                    profits = pd.to_numeric(pg["profit_net"], errors="coerce").dropna()
                    if len(profits) == 0:
                        continue
                    n_w = int((profits > 0).sum())
                    gp  = float(profits[profits > 0].sum())
                    gl  = abs(float(profits[profits < 0].sum()))
                    label = "pm_bull" if int(pm_dir) == 1 else "pm_bear"
                    pm_breakdown[label] = {
                        "n_trades":      len(profits),
                        "win_rate":      round(n_w / len(profits), 4),
                        "net_profit":    round(float(profits.sum()), 2),
                        "profit_factor": round(gp / gl, 4) if gl > 0 else None,
                    }
            except Exception:
                pass

        fill_rate = round(len(group) / n_signals, 4) if n_signals > 0 else 0.0
        filter_results = _try_filter_discovery(group, filter_cfg)
        params_key = "|".join(f"{k}={v}" for k, v in sorted(params.items()))

        result: Dict[str, Any] = {
            "tf":                 tf_minutes,
            "pattern_id":         pattern_id,
            "params":             params,
            "params_key":         params_key,
            "direction_mode":     "long" if direction == 1 else "short",
            "entry_timing":       timing_mode,
            "outcome_mode":       str(om),
            "mtf_mode":           "premarket_signal",
            "n_signals":          n_signals,
            "n_trades":           len(group),
            "fill_rate":          fill_rate,
            "metrics":            metrics,
            "regime_breakdown":   regime_bk,
            "session_breakdown":  session_bk,
            "pm_breakdown":       pm_breakdown,
            "filter_results":     filter_results,
            "is_oos_degradation": compute_is_oos_degradation(group),
        }
        all_results.append(result)

    return all_results if all_results else None


# ---------------------------------------------------------------------------
# Main sweep entry point
# ---------------------------------------------------------------------------

def run_premarket_predictor(
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Run the pre-market direction predictor sweep.

    Detects candle patterns in the pre-market window and measures whether
    entering at the NY open in the same direction (or opposite) is profitable.

    Parameters
    ----------
    bars_1m          : Full 1-minute bars including premarket AND RTH.
                       Must have (dt, open, high, low, close) with tz-aware
                       America/Denver datetimes.
    config           : ``premarket_discovery`` config block from YAML.
    bars_with_regime : Optional bars with regime columns for breakdown.

    Returns
    -------
    JSON-safe dict:
      {
        "sweep_results":       [...],
        "n_combinations_run":  int,
        "n_results":           int,
        "signal_window":       {...},
      }
    """
    cfg = _deep_merge(DEFAULT_PREMARKET_DISCOVERY_CONFIG, config or {})

    timeframes:     List[int] = [int(tf) for tf in cfg["timeframes"]]
    min_trades:     int       = int(cfg["min_trades"])
    direction_cfg:  str       = str(cfg.get("direction", "both"))
    feature_cfg:    Dict      = cfg.get("candle_features", {})
    pattern_cfgs:   Dict      = cfg.get("patterns", {})
    timing_cfgs:    Dict      = cfg.get("entry_timing", {})
    outcome_cfg:    Dict      = cfg.get("outcome", {})
    filter_cfg:     Dict      = cfg.get("filter_discovery", {})
    signal_window:  Dict      = cfg.get("signal_window", DEFAULT_PREMARKET_DISCOVERY_CONFIG["signal_window"])

    directions         = _direction_values(direction_cfg)
    enabled_timings    = [tm for tm, tc in timing_cfgs.items() if tc.get("enabled", True)]

    sweep_results:   List[Dict[str, Any]] = []
    n_combinations_run = 0

    tick_size = float(outcome_cfg.get("tick_size", 0.25))

    for tf in timeframes:
        # Resample FULL bars to TF — used for:
        #   (a) candle feature computation (needs prior history for ATR)
        #   (b) next_open entry timing lookup (needs the 07:30 bar that follows the PM signal)
        if tf == 1:
            bars_tf_full = bars_1m.copy()
        else:
            bars_tf_full = ohlcv_resample_from_bars(bars_1m, f"{tf}m")

        if bars_tf_full is None or bars_tf_full.empty:
            continue

        # Compute candle features on the FULL bars (gives proper ATR, rolling averages)
        feat_cfg_merged = {**feature_cfg, "tick_size": tick_size}
        try:
            enriched_full = compute_candle_features(bars_tf_full, feat_cfg_merged)
        except Exception:
            continue

        # Filter to premarket window for SIGNAL DETECTION only
        enriched_pm = _filter_to_window(enriched_full, signal_window)
        if enriched_pm is None or enriched_pm.empty:
            continue

        # Compute premarket session-level features
        pm_feat_cfg = {
            "tick_size":      tick_size,
            "pm_start_hour":  signal_window.get("hour_from", 6),
            "pm_start_minute":signal_window.get("minute_from", 0),
            "pm_end_hour":    signal_window.get("hour_to", 7),
            "pm_end_minute":  signal_window.get("minute_to", 29),
        }
        try:
            enriched_pm = compute_premarket_session_features(enriched_pm, pm_feat_cfg)
        except Exception:
            pass  # non-fatal; session features are bonus context for filter_discovery

        for pattern_id, pat_cfg in pattern_cfgs.items():
            if not pat_cfg.get("enabled", True):
                continue
            if pattern_id not in PATTERN_REGISTRY:
                continue

            param_combos = _expand_pattern_params(pat_cfg)

            for params in param_combos:
                for direction in directions:
                    for timing_mode in enabled_timings:
                        timing_cfg_item = timing_cfgs.get(timing_mode, {})
                        n_combinations_run += 1

                        results = _run_combo(
                            enriched_pm=enriched_pm,
                            bars_1m_full=bars_1m,
                            bars_tf=bars_tf_full,
                            pattern_id=pattern_id,
                            params=params,
                            direction=direction,
                            timing_mode=timing_mode,
                            timing_cfg=timing_cfg_item,
                            outcome_cfg=outcome_cfg,
                            bars_with_regime=bars_with_regime,
                            tf_minutes=tf,
                            min_trades=min_trades,
                            filter_cfg=filter_cfg,
                        )
                        if results:
                            sweep_results.extend(results)

    return {
        "sweep_results":      sweep_results,
        "n_combinations_run": n_combinations_run,
        "n_results":          len(sweep_results),
        "signal_window":      signal_window,
    }
