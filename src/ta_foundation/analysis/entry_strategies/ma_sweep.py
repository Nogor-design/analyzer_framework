from __future__ import annotations

"""
MA Cross/Pullback Discovery Sweep
===================================
Entry point for the MA strategy discovery engine.

For each combination of:
  signal_type × period × ma_type × direction × timing_mode × outcome_config

  1. Resample 1m bars to target TF (optional; defaults to 1m and 5m)
  2. Compute MA features           (ma.features)
  3. Detect MA signals             (ma.signals)
  4. Emit entry timing             (candle.signals — reused)
  5. Simulate forward outcomes     (outcome.simulator — reused)
  6. Compute evaluation metrics    (strategy_discovery.evaluation — reused)
  7. Run filter discovery          (optional — reused)
  8. Store result dict

Results are returned as a JSON-safe dict compatible with
pkg.metadata["derived"]["ma_discovery"].

YAML config shape (top-level block)
-------------------------------------
ma_discovery:
  enabled: true
  timeframes: [1, 5]
  min_trades: 20

  signals:
    ma_cross:
      enabled: true
      period: [9, 20, 50]
      ma_type: [ema, sma]
      direction: 0          # 0=both, 1=long, -1=short
      min_slope: [0.0, 0.05]
      min_atr_dist_before: [0.0]

    ma_pullback:
      enabled: true
      period: [20, 50]
      ma_type: [ema]
      direction: 0
      trend_lookback: [10, 20]
      trend_bars_min: [6]
      touch_atr_mult: [0.5, 1.0]
      require_slope: true

  entry_timing:
    next_open:     {enabled: true}
    break_extreme: {enabled: true, buffer_ticks: 1, fill_timeout_bars: 3}
    body_midpoint: {enabled: true, fill_timeout_bars: 5}

  outcome:
    atr:   {enabled: true, target_mult: 1.5, stop_mult: 1.0}
    ticks: {enabled: true, take_profit: [30, 60, 100], stop: [30, 40, 50]}
    max_bars_timeout: 20
    timeout_result: loss
    tick_size: 0.25
    tick_value: 5.00
    commission_per_side: 2.09
    slippage_ticks: 1

  filter_discovery: {enabled: true, top_n: 10}
"""

from itertools import product
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ta_foundation.marketdata.resample import ohlcv_resample_from_bars
from ta_foundation.analysis.entry_strategies.ma.features import (
    compute_ma_features,
    DEFAULT_MA_FEATURE_CONFIG,
)
from ta_foundation.analysis.entry_strategies.ma.signals import (
    MA_SIGNAL_REGISTRY,
    detect_ma_signal,
)
from ta_foundation.analysis.entry_strategies.candle.signals import emit_entries
from ta_foundation.analysis.entry_strategies.outcome.simulator import (
    count_outcome_modes,
    simulate_outcomes,
)
from ta_foundation.analysis.strategy_discovery.evaluation import (
    compute_evaluation_metrics,
    compute_regime_breakdown,
)
from ta_foundation.analysis.entry_strategies.hardening import (
    attach_hardening_metadata,
    inject_trial_grid_size,
)
from ta_foundation.analysis.entry_strategies.validation import compute_is_oos_degradation


# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------

DEFAULT_MA_DISCOVERY_CONFIG: Dict[str, Any] = {
    "enabled":    True,
    "timeframes": [1, 5],
    "min_trades": 20,

    "ma_features": {
        "atr_period": 14,
        "tick_size":  0.25,
    },

    "signals": {
        "ma_cross": {
            "enabled":              True,
            "period":               [9, 20, 50],
            "ma_type":              ["ema", "sma"],
            "direction":            [0],
            "min_slope":            [0.0, 0.05],
            "min_atr_dist_before":  [0.0],
        },
        "ma_pullback": {
            "enabled":         True,
            "period":          [20, 50],
            "ma_type":         ["ema"],
            "direction":       [0],
            "trend_lookback":  [10, 20],
            "trend_bars_min":  [6],
            "touch_atr_mult":  [0.5, 1.0],
            "require_slope":   [True],
        },
    },

    "entry_timing": {
        "next_open":     {"enabled": True},
        "break_extreme": {"enabled": True, "buffer_ticks": 1, "fill_timeout_bars": 3},
        "body_midpoint": {"enabled": True, "fill_timeout_bars": 5},
    },

    "outcome": {
        "atr":   {"enabled": True,  "target_mult": 1.5, "stop_mult": 1.0},
        "ticks": {"enabled": True,  "take_profit": [30, 60, 100], "stop": [30, 40, 50]},
        "max_bars_timeout": 20,
        "timeout_result":   "loss",
        "tick_size":        0.25,
        "tick_value":       5.00,
        "commission_per_side": 2.09,
        "slippage_ticks":   1,
    },

    "filter_discovery": {"enabled": True, "top_n": 10},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: Dict, override: Dict) -> Dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _expand_params(signal_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Expand list-valued params into all combinations."""
    list_keys   = {k: v for k, v in signal_cfg.items()
                   if isinstance(v, list) and k != "enabled"}
    scalar_keys = {k: v for k, v in signal_cfg.items()
                   if not isinstance(v, list) and k != "enabled"}

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
        top_n   = int(filter_cfg.get("top_n", 10))
        results = run_filter_discovery(trades_df, options={"top_n": top_n})
        if results and isinstance(results, list):
            return results[:top_n]
    except Exception:
        pass
    return []


def _session_label(entry_time: pd.Series) -> pd.Series:
    try:
        from ta_foundation.analysis.session_constants import session_label_from_hour
        dt = pd.to_datetime(entry_time)
        if dt.dt.tz is not None:
            local = dt.dt.tz_convert("America/Denver")
        else:
            local = dt
        return local.dt.hour.map(session_label_from_hour)
    except Exception:
        return pd.Series("unknown", index=entry_time.index)


# ---------------------------------------------------------------------------
# Single combination runner
# ---------------------------------------------------------------------------

def _run_single_combo(
    enriched_tf: pd.DataFrame,
    bars_1m: pd.DataFrame,
    signal_id: str,
    params: Dict[str, Any],
    timing_mode: str,
    timing_cfg: Dict[str, Any],
    outcome_cfg: Dict[str, Any],
    bars_with_regime: Optional[pd.DataFrame],
    tf_minutes: int,
    min_trades: int,
    filter_cfg: Dict[str, Any],
    hardening_cfg: Dict[str, Any],
) -> Optional[List[Dict[str, Any]]]:

    # 1. Detect signals
    signals_df = detect_ma_signal(signal_id, enriched_tf, params)
    if signals_df is None or signals_df.empty:
        return None

    n_signals = len(signals_df)

    # 2. Emit entries (reuse candle signals infrastructure)
    timing_params = {**timing_cfg, "tick_size": outcome_cfg.get("tick_size", 0.25)}
    bars_for_timing = ohlcv_resample_from_bars(bars_1m, f"{tf_minutes}m") if timing_mode == "next_open" else None
    try:
        pending = emit_entries(signals_df, timing_mode, bars=bars_for_timing, params=timing_params)
    except Exception:
        return None

    if pending is None or pending.empty:
        return None

    # 3. Simulate outcomes
    trades_df = simulate_outcomes(pending, bars_1m, outcome_cfg)
    if trades_df is None or trades_df.empty:
        return None

    all_results: List[Dict[str, Any]] = []

    for om, group in trades_df.groupby("outcome_mode"):
        if len(group) < min_trades:
            continue

        group = group.copy()

        # Session label
        if "entry_time" in group.columns and "session_label" not in group.columns:
            group["session_label"] = _session_label(group["entry_time"])

        # Evaluation metrics
        try:
            metrics = compute_evaluation_metrics(group, profit_col="profit_net")
        except Exception:
            metrics = {}

        # Regime breakdown
        regime_bk: Dict[str, Any] = {}
        if bars_with_regime is not None:
            try:
                regime_bk = compute_regime_breakdown(group, bars_with_regime, profit_col="profit_net")
            except Exception:
                pass

        # Session breakdown
        session_bk: Dict[str, Any] = {}
        if "session_label" in group.columns:
            try:
                for sess, sess_grp in group.groupby("session_label"):
                    profits = pd.to_numeric(sess_grp["profit_net"], errors="coerce").dropna()
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

        fill_rate      = round(len(group) / n_signals, 4) if n_signals > 0 else 0.0
        filter_results = _try_filter_discovery(group, filter_cfg)
        params_key     = "|".join(f"{k}={v}" for k, v in sorted(params.items()))

        direction_val = int(params.get("direction", 0))
        dir_label     = {1: "long", -1: "short", 0: "both"}.get(direction_val, str(direction_val))

        result = {
            "strategy_type":    "ma",
            "signal_id":        signal_id,
            "tf":               tf_minutes,
            "params":           params,
            "params_key":       params_key,
            "direction_mode":   dir_label,
            "entry_timing":     timing_mode,
            "outcome_mode":     str(om),
            "mtf_mode":         f"independent_{tf_minutes}m",
            "n_signals":        n_signals,
            "n_trades":         len(group),
            "fill_rate":        fill_rate,
            "metrics":          metrics,
            "regime_breakdown": regime_bk,
            "session_breakdown": session_bk,
            "filter_results":   filter_results,
            "is_oos_degradation": compute_is_oos_degradation(group),
        }
        attach_hardening_metadata(result, group, outcome_cfg, hardening_cfg)
        all_results.append(result)

    return all_results if all_results else None


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def _compute_trial_grid_size(cfg: Dict[str, Any]) -> int:
    """Total candidate cells the sweep evaluates — the within-run trial count.

    tf × signal-param-combos × entry-timings × outcome modes.
    """
    timeframes = [int(tf) for tf in cfg.get("timeframes", [1])]
    timing_cfgs = cfg.get("entry_timing", {}) or {}
    n_timings = sum(1 for tc in timing_cfgs.values() if tc.get("enabled", True))
    n_param = sum(
        len(_expand_params(sc))
        for sid, sc in (cfg.get("signals", {}) or {}).items()
        if sc.get("enabled", True) and sid in MA_SIGNAL_REGISTRY
    )
    n_combos = len(timeframes) * n_param * n_timings
    return max(1, n_combos * count_outcome_modes(cfg.get("outcome", {}) or {}))


def run_ma_discovery(
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Run the MA cross/pullback discovery sweep.

    Parameters
    ----------
    bars_1m          : 1-minute OHLCV bars from MarketDataStore
    config           : ma_discovery YAML block (merged with defaults)
    bars_with_regime : optional bars with regime columns for regime breakdown

    Returns
    -------
    JSON-safe dict:
      {
        "sweep_results":      [...],
        "n_combinations_run": int,
        "n_results":          int,
      }
    """
    cfg = _deep_merge(DEFAULT_MA_DISCOVERY_CONFIG, config or {})

    timeframes    = [int(tf) for tf in cfg["timeframes"]]
    min_trades    = int(cfg["min_trades"])
    ma_feat_cfg   = cfg.get("ma_features", {})
    signal_cfgs   = cfg.get("signals", {})
    timing_cfgs   = cfg.get("entry_timing", {})
    outcome_cfg   = cfg.get("outcome", {})
    filter_cfg    = cfg.get("filter_discovery", {})
    hardening_cfg = cfg.get("hardening", {})

    enabled_timings = [tm for tm, tc in timing_cfgs.items() if tc.get("enabled", True)]

    # Auto-populate the trial budget from the sweep's own grid size so the
    # hardening selection-bias correction is live instead of inert at n=1.
    trial_grid_size = _compute_trial_grid_size(cfg)
    hardening_cfg = inject_trial_grid_size(hardening_cfg, trial_grid_size)

    sweep_results:      List[Dict[str, Any]] = []
    n_combinations_run: int = 0

    for tf in timeframes:
        # Resample
        if tf == 1:
            bars_tf = bars_1m.copy()
        else:
            bars_tf = ohlcv_resample_from_bars(bars_1m, f"{tf}m")

        if bars_tf is None or bars_tf.empty:
            continue

        # Compute MA features (all periods/types needed for this TF)
        all_periods  = set()
        all_ma_types = set()
        for sig_id, sig_cfg in signal_cfgs.items():
            if not sig_cfg.get("enabled", True):
                continue
            periods  = sig_cfg.get("period",   [20])
            ma_types = sig_cfg.get("ma_type",  ["ema"])
            all_periods.update(periods if isinstance(periods, list) else [periods])
            all_ma_types.update(ma_types if isinstance(ma_types, list) else [ma_types])

        feat_cfg = {
            **DEFAULT_MA_FEATURE_CONFIG,
            **ma_feat_cfg,
            "periods":  sorted(all_periods),
            "ma_types": sorted(all_ma_types),
            "tick_size": outcome_cfg.get("tick_size", 0.25),
        }
        try:
            enriched = compute_ma_features(bars_tf, feat_cfg)
        except Exception:
            continue

        # Loop signals
        for signal_id, sig_cfg in signal_cfgs.items():
            if not sig_cfg.get("enabled", True):
                continue
            if signal_id not in MA_SIGNAL_REGISTRY:
                continue

            param_combos = _expand_params(sig_cfg)

            for params in param_combos:
                for timing_mode in enabled_timings:
                    timing_cfg_item = timing_cfgs.get(timing_mode, {})
                    n_combinations_run += 1

                    results = _run_single_combo(
                        enriched_tf=enriched,
                        bars_1m=bars_1m,
                        signal_id=signal_id,
                        params=params,
                        timing_mode=timing_mode,
                        timing_cfg=timing_cfg_item,
                        outcome_cfg=outcome_cfg,
                        bars_with_regime=bars_with_regime,
                        tf_minutes=tf,
                        min_trades=min_trades,
                        filter_cfg=filter_cfg,
                        hardening_cfg=hardening_cfg,
                    )
                    if results:
                        sweep_results.extend(results)

    return {
        "sweep_results":      sweep_results,
        "n_combinations_run": n_combinations_run,
        "n_results":          len(sweep_results),
        "trial_grid_size":    trial_grid_size,
    }
