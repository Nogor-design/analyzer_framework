from __future__ import annotations

"""
Candle Discovery Sweep Orchestrator
=====================================
Top-level entry point for the candle pattern entry discovery engine.

Pipeline for each combination:
  TF × pattern × param-combo × direction × timing-mode × outcome-config

  1. Resample 1m bars to target TF using existing ohlcv_resample_from_bars()
  2. Compute candle features  (candle.features)
  3. Detect pattern signals   (candle.patterns)
  4. Emit entry timing        (candle.signals)
  5. Simulate forward outcomes (outcome.simulator)
  6. Build feature matrix      (strategy_discovery.features — existing)
  7. Compute evaluation metrics (strategy_discovery.evaluation — existing)
  8. Run filter discovery      (strategy_discovery.filter_discovery — existing)
  9. Store SweepResult

Then runs MTF confluence and hierarchical modes using stored per-TF signals.

Results are returned as a JSON-safe list of dicts compatible with
pkg.metadata["derived"]["candle_discovery"].
"""

from itertools import product
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ta_foundation.marketdata.resample import ohlcv_resample_from_bars
from ta_foundation.analysis.entry_strategies.candle.features import compute_candle_features
from ta_foundation.analysis.entry_strategies.candle.patterns import (
    PATTERN_REGISTRY, detect_pattern,
)
from ta_foundation.analysis.entry_strategies.candle.signals import emit_entries
from ta_foundation.analysis.entry_strategies.candle.mtf import (
    apply_confluence_filter,
    apply_hierarchical_filter,
    label_independent,
)
from ta_foundation.analysis.entry_strategies.outcome.simulator import simulate_outcomes
from ta_foundation.analysis.strategy_discovery.evaluation import (
    compute_evaluation_metrics,
    compute_regime_breakdown,
)
from ta_foundation.analysis.entry_strategies.hardening import attach_hardening_metadata
from ta_foundation.analysis.entry_strategies.validation import compute_is_oos_degradation


# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------

DEFAULT_CANDLE_DISCOVERY_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "timeframes": [1, 5],
    "min_trades": 20,

    "candle_features": {
        "size_lookbacks": [5, 10, 20],
        "atr_period": 14,
        "tick_size": 0.25,
        "min_body_ticks": 1,
    },

    "patterns": {
        "large_body":         {"enabled": True,  "body_multiplier": [1.5, 2.0], "wick_to_body_max": [0.3, 0.5], "lookback": [10, 20]},
        "pin_bar_bullish":    {"enabled": True,  "wick_to_body_min": [1.5, 2.0], "body_to_range_max": [0.30, 0.35]},
        "pin_bar_bearish":    {"enabled": True,  "wick_to_body_min": [1.5, 2.0], "body_to_range_max": [0.30, 0.35]},
        "inside_bar":         {"enabled": True},
        "outside_bar":        {"enabled": True},
        "engulfing_bullish":  {"enabled": True,  "engulf_ratio": [1.0, 1.2]},
        "engulfing_bearish":  {"enabled": True,  "engulf_ratio": [1.0, 1.2]},
        "doji":               {"enabled": True,  "body_to_range_max": [0.10, 0.15]},
        "clean_breakout_bar": {"enabled": True,  "atr_mult": [1.5, 2.0], "body_to_range_min": [0.60, 0.70]},
    },

    "direction": "both",  # "long_only" | "short_only" | "both"

    "entry_timing": {
        "next_open":     {"enabled": True},
        "break_extreme": {"enabled": True, "buffer_ticks": 1, "fill_timeout_bars": 3},
        "body_midpoint": {"enabled": True, "fill_timeout_bars": 5},
    },

    "outcome": {
        "atr":   {"enabled": True,  "target_mult": 1.5, "stop_mult": 1.0},
        "ticks": {"enabled": True,  "take_profit": [30, 60, 100], "stop": [30, 40, 50]},
        "max_bars_timeout": 20,
        "timeout_result": "loss",
        "tick_size":  0.25,
        "tick_value": 5.00,
        "commission_per_side": 2.09,
        "slippage_ticks": 1,
    },

    "mtf": {
        "confluence":    {"enabled": True,  "min_agreement": 2},
        "hierarchical":  {"enabled": True,  "context_tf": 5, "entry_tf": 1, "context_strength_min": 0.0},
    },

    "filter_discovery": {"enabled": True, "top_n": 10},
}


# ---------------------------------------------------------------------------
# Param-grid generation
# ---------------------------------------------------------------------------

def _expand_pattern_params(pattern_id: str, pattern_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Expand list-valued params in pattern_cfg into all combinations.
    Scalar values are kept as-is.  Returns a list of flat param dicts.
    """
    list_keys   = {k: v for k, v in pattern_cfg.items() if isinstance(v, list) and k != "enabled"}
    scalar_keys = {k: v for k, v in pattern_cfg.items() if not isinstance(v, list) and k != "enabled"}

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


def _direction_values(direction_cfg: str) -> List[int]:
    if direction_cfg == "long_only":
        return [1]
    if direction_cfg == "short_only":
        return [-1]
    return [1, -1]   # both


# ---------------------------------------------------------------------------
# Trial-grid sizing — feeds the hardening selection-bias correction
# ---------------------------------------------------------------------------

def _count_outcome_modes(outcome_cfg: Dict[str, Any]) -> int:
    """Outcome-mode cells the simulator expands per signal combo.

    ATR contributes exactly one mode (``target_mult``/``stop_mult`` are
    scalars); the tick grid contributes ``len(take_profit) * len(stop)``.
    Mirrors the enabled-checks in ``outcome.simulator.simulate_outcomes``.
    """
    n = 0
    atr_cfg = outcome_cfg.get("atr", {}) or {}
    if atr_cfg.get("enabled", True):
        n += 1
    tick_cfg = outcome_cfg.get("ticks", {}) or {}
    if tick_cfg.get("enabled", True):
        tp = tick_cfg.get("take_profit", [30, 60, 100])
        sl = tick_cfg.get("stop", [30, 40, 50])
        n_tp = len(tp) if isinstance(tp, (list, tuple)) else 1
        n_sl = len(sl) if isinstance(sl, (list, tuple)) else 1
        n += n_tp * n_sl
    return max(1, n)


def _count_signal_combos(cfg: Dict[str, Any]) -> int:
    """Independent (non-MTF) signal combos: tf x pattern-params x dir x timing."""
    timeframes = [int(tf) for tf in cfg.get("timeframes", [])]
    directions = _direction_values(str(cfg.get("direction", "both")))
    timing_cfgs = cfg.get("entry_timing", {}) or {}
    n_timings = sum(1 for tc in timing_cfgs.values() if tc.get("enabled", True))
    n_param_combos = 0
    for pattern_id, pat_cfg in (cfg.get("patterns", {}) or {}).items():
        if not pat_cfg.get("enabled", True):
            continue
        if pattern_id not in PATTERN_REGISTRY:
            continue
        n_param_combos += len(_expand_pattern_params(pattern_id, pat_cfg))
    return len(timeframes) * n_param_combos * len(directions) * n_timings


def _compute_trial_grid_size(cfg: Dict[str, Any]) -> int:
    """Total candidate cells the sweep evaluates — the within-run trial count.

    Independent combos are exact from config. The MTF confluence/hierarchical
    passes depend on which signals are actually detected, so they are bounded
    above by the pattern-key grid. An upper bound is the safe direction for a
    selection-bias correction: better to over-count trials than under-count.
    """
    outcome_modes = _count_outcome_modes(cfg.get("outcome", {}) or {})
    independent = _count_signal_combos(cfg) * outcome_modes

    timeframes = [int(tf) for tf in cfg.get("timeframes", [])]
    directions = _direction_values(str(cfg.get("direction", "both")))
    timing_cfgs = cfg.get("entry_timing", {}) or {}
    n_timings = sum(1 for tc in timing_cfgs.values() if tc.get("enabled", True))
    n_pattern_keys = len(directions) * sum(
        1
        for pid, pc in (cfg.get("patterns", {}) or {}).items()
        if pc.get("enabled", True) and pid in PATTERN_REGISTRY
    )

    mtf_cfg = cfg.get("mtf", {}) or {}
    mtf_upper = 0
    conf_cfg = mtf_cfg.get("confluence", {}) or {}
    if conf_cfg.get("enabled", True) and len(timeframes) >= 2:
        mtf_upper += n_pattern_keys * n_timings * outcome_modes
    hier_cfg = mtf_cfg.get("hierarchical", {}) or {}
    if hier_cfg.get("enabled", True):
        ctx_tf = int(hier_cfg.get("context_tf", 5))
        ent_tf = int(hier_cfg.get("entry_tf", 1))
        if ctx_tf in timeframes and ent_tf in timeframes:
            mtf_upper += n_pattern_keys * n_timings * outcome_modes

    return max(1, independent + mtf_upper)


def _inject_grid_size_into_hardening(
    hardening_cfg: Dict[str, Any], grid_size: int
) -> Dict[str, Any]:
    """Auto-populate ``trial_budget.within_run_trials`` with the sweep grid size.

    The sweep knows how many parameter cells it evaluated, so the selection-bias
    correction no longer has to be opted into by hand. An explicit
    ``within_run_trials`` already in config is left untouched.
    """
    hc = dict(hardening_cfg or {})
    tb = dict(hc.get("trial_budget") or {})
    if "within_run_trials" not in tb:
        tb["within_run_trials"] = int(grid_size)
    hc["trial_budget"] = tb
    return hc


# ---------------------------------------------------------------------------
# Filter discovery (optional, wraps existing module)
# ---------------------------------------------------------------------------

def _try_filter_discovery(trades_df: pd.DataFrame, filter_cfg: Dict[str, Any]) -> List[Dict]:
    if not filter_cfg.get("enabled", False):
        return []
    try:
        from ta_foundation.analysis.strategy_discovery.filter_discovery import (
            run_filter_discovery,
        )
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

def _run_single_combo(
    enriched_tf: pd.DataFrame,
    bars_1m: pd.DataFrame,
    pattern_id: str,
    params: Dict[str, Any],
    direction: int,
    timing_mode: str,
    timing_cfg: Dict[str, Any],
    outcome_cfg: Dict[str, Any],
    bars_with_regime: Optional[pd.DataFrame],
    mtf_label: str,
    tf_minutes: int,
    min_trades: int,
    filter_cfg: Dict[str, Any],
    hardening_cfg: Dict[str, Any],
    bars_tf: Optional[pd.DataFrame] = None,
) -> Optional[Dict[str, Any]]:
    """Run one full pipeline combination and return a SweepResult dict or None.

    Parameters
    ----------
    bars_tf : pre-resampled bars at tf_minutes resolution.  When provided,
              used directly for next_open timing (avoids resampling per combo).
              Falls back to resampling bars_1m if None.
    """

    # 1. Detect signals
    detect_params = {**params, "direction": direction}
    signals_df = detect_pattern(pattern_id, enriched_tf, detect_params)
    if signals_df is None or signals_df.empty:
        return None

    n_signals = len(signals_df)

    # 2. Emit entries
    timing_params = {**timing_cfg, "tick_size": outcome_cfg.get("tick_size", 0.25)}
    if timing_mode == "next_open":
        # Use pre-resampled bars if available to avoid per-combo resample overhead
        bars_for_timing = bars_tf if bars_tf is not None else ohlcv_resample_from_bars(bars_1m, f"{tf_minutes}m")
    else:
        bars_for_timing = None
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

    # Split by outcome_mode and process each separately
    all_results = []
    for om, group in trades_df.groupby("outcome_mode"):
        if len(group) < min_trades:
            continue

        # 4. Add session label if not present
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

        # 5. Compute evaluation metrics (existing)
        try:
            metrics = compute_evaluation_metrics(group, profit_col="profit_net")
        except Exception:
            metrics = {}

        # 6. Compute regime breakdown (existing)
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
                for sess, sess_grp in group.groupby("session_label"):
                    profits = pd.to_numeric(sess_grp["profit_net"], errors="coerce").dropna()
                    if len(profits) == 0:
                        continue
                    n_w = int((profits > 0).sum())
                    gp  = float(profits[profits > 0].sum())
                    gl  = abs(float(profits[profits < 0].sum()))
                    session_bk[str(sess)] = {
                        "n_trades": len(profits),
                        "win_rate": round(n_w / len(profits), 4),
                        "net_profit": round(float(profits.sum()), 2),
                        "profit_factor": round(gp / gl, 4) if gl > 0 else None,
                    }
            except Exception:
                pass

        # 8. Fill rate
        fill_rate = round(len(group) / n_signals, 4) if n_signals > 0 else 0.0

        # 9. Filter discovery
        filter_results = _try_filter_discovery(group, filter_cfg)

        # 10. Params key (hashable string for grouping)
        params_key = "|".join(f"{k}={v}" for k, v in sorted(params.items()))

        result: Dict[str, Any] = {
            "tf":               tf_minutes,
            "pattern_id":       pattern_id,
            "params":           params,
            "params_key":       params_key,
            "direction_mode":   "long" if direction == 1 else "short",
            "entry_timing":     timing_mode,
            "outcome_mode":     str(om),
            "mtf_mode":         mtf_label,
            "n_signals":        n_signals,
            "n_trades":         len(group),
            "fill_rate":        fill_rate,
            "metrics":          metrics,
            "regime_breakdown": regime_bk,
            "session_breakdown":session_bk,
            "filter_results":   filter_results,
            "is_oos_degradation": compute_is_oos_degradation(group),
        }
        attach_hardening_metadata(
            result, group, outcome_cfg, hardening_cfg, bars_with_regime=bars_with_regime
        )
        all_results.append(result)

    return all_results if all_results else None


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def run_candle_discovery(
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Run the full candle pattern discovery sweep.

    Parameters
    ----------
    bars_1m          : 1-minute bars from MarketDataStore
                       (columns: dt, open, high, low, close, volume)
    config           : candle_discovery config block from YAML (merged with defaults)
    bars_with_regime : optional bars with regime/adx/atr columns for regime breakdown

    Returns
    -------
    JSON-safe dict:
      {
        "sweep_results": [...],   list of SweepResult dicts
        "n_combinations_run": int,
        "n_results": int,
        "mtf_confluence_results": [...],
        "mtf_hierarchical_results": [...],
      }
    """
    cfg = _deep_merge(DEFAULT_CANDLE_DISCOVERY_CONFIG, config or {})

    timeframes:    List[int]  = [int(tf) for tf in cfg["timeframes"]]
    min_trades:    int        = int(cfg["min_trades"])
    direction_cfg: str        = str(cfg.get("direction", "both"))
    feature_cfg:   Dict       = cfg.get("candle_features", {})
    pattern_cfgs:  Dict       = cfg.get("patterns", {})
    timing_cfgs:   Dict       = cfg.get("entry_timing", {})
    outcome_cfg:   Dict       = cfg.get("outcome", {})
    mtf_cfg:       Dict       = cfg.get("mtf", {})
    filter_cfg:    Dict       = cfg.get("filter_discovery", {})
    hardening_cfg: Dict       = cfg.get("hardening", {})

    # Auto-populate the trial budget: the sweep knows its own grid size, so the
    # hardening selection-bias correction reflects the real search instead of
    # being inert at n=1.
    trial_grid_size = _compute_trial_grid_size(cfg)
    hardening_cfg = _inject_grid_size_into_hardening(hardening_cfg, trial_grid_size)

    directions     = _direction_values(direction_cfg)
    enabled_timings = [tm for tm, tc in timing_cfgs.items() if tc.get("enabled", True)]

    sweep_results:   List[Dict[str, Any]] = []
    signals_by_tf:   Dict[int, Dict[str, pd.DataFrame]] = {}  # {tf: {pattern_id: signals_df}}
    bars_tf_cache:   Dict[int, pd.DataFrame] = {}             # {tf: resampled bars}
    n_combinations_run = 0

    for tf in timeframes:
        # Resample to TF (cached — avoids re-resampling per combo)
        if tf == 1:
            bars_tf = bars_1m.copy()
        else:
            bars_tf = ohlcv_resample_from_bars(bars_1m, f"{tf}m")
        bars_tf_cache[tf] = bars_tf

        if bars_tf is None or bars_tf.empty:
            continue

        # Compute candle features
        feat_cfg_merged = {**feature_cfg, "tick_size": outcome_cfg.get("tick_size", 0.25)}
        try:
            enriched = compute_candle_features(bars_tf, feat_cfg_merged)
        except Exception:
            continue

        signals_by_tf[tf] = {}

        for pattern_id, pat_cfg in pattern_cfgs.items():
            if not pat_cfg.get("enabled", True):
                continue
            if pattern_id not in PATTERN_REGISTRY:
                continue

            param_combos = _expand_pattern_params(pattern_id, pat_cfg)

            for params in param_combos:
                for direction in directions:
                    # Detect once (shared across timing modes)
                    detect_params = {**params, "direction": direction}
                    signals_df = detect_pattern(pattern_id, enriched, detect_params)
                    if signals_df is None or signals_df.empty:
                        continue

                    # Store signals for MTF modes (use first param combo for now)
                    sig_key = f"{pattern_id}_dir{direction}"
                    if sig_key not in signals_by_tf[tf]:
                        signals_by_tf[tf][sig_key] = signals_df

                    for timing_mode in enabled_timings:
                        timing_cfg = timing_cfgs.get(timing_mode, {})
                        n_combinations_run += 1

                        results = _run_single_combo(
                            enriched_tf=enriched,
                            bars_1m=bars_1m,
                            pattern_id=pattern_id,
                            params=params,
                            direction=direction,
                            timing_mode=timing_mode,
                            timing_cfg=timing_cfg,
                            outcome_cfg=outcome_cfg,
                            bars_with_regime=bars_with_regime,
                            mtf_label=f"independent_{tf}m",
                            tf_minutes=tf,
                            min_trades=min_trades,
                            filter_cfg=filter_cfg,
                            hardening_cfg=hardening_cfg,
                            bars_tf=bars_tf,
                        )
                        if results:
                            sweep_results.extend(results)

    # ------------------------------------------------------------------
    # MTF Confluence
    # ------------------------------------------------------------------
    mtf_confluence_results: List[Dict[str, Any]] = []
    conf_cfg = mtf_cfg.get("confluence", {})
    if conf_cfg.get("enabled", True) and len(signals_by_tf) >= 2:
        min_agreement = int(conf_cfg.get("min_agreement", 2))
        # Group signals by (pattern_id, direction) across TFs
        all_pattern_keys = set()
        for tf_sigs in signals_by_tf.values():
            all_pattern_keys.update(tf_sigs.keys())

        for sig_key in all_pattern_keys:
            by_tf = {tf: signals_by_tf[tf][sig_key]
                     for tf in signals_by_tf if sig_key in signals_by_tf[tf]}
            if len(by_tf) < 2:
                continue

            filtered = apply_confluence_filter(by_tf, min_agreement=min_agreement)
            if filtered is None or filtered.empty:
                continue

            # Parse pattern/direction from sig_key for labelling
            parts = sig_key.rsplit("_dir", 1)
            pat_id  = parts[0]
            dir_val = int(parts[1]) if len(parts) > 1 else 0

            for timing_mode in enabled_timings:
                timing_cfg_item = timing_cfgs.get(timing_mode, {})
                n_combinations_run += 1

                _min_tf = min(by_tf.keys())
                results = _run_single_combo(
                    enriched_tf=filtered,   # already has candle feature cols
                    bars_1m=bars_1m,
                    pattern_id=pat_id,
                    params={},
                    direction=dir_val,
                    timing_mode=timing_mode,
                    timing_cfg=timing_cfg_item,
                    outcome_cfg=outcome_cfg,
                    bars_with_regime=bars_with_regime,
                    mtf_label=f"confluence_{min_agreement}of{len(by_tf)}",
                    tf_minutes=_min_tf,
                    min_trades=min_trades,
                    filter_cfg=filter_cfg,
                    hardening_cfg=hardening_cfg,
                    bars_tf=bars_tf_cache.get(_min_tf),
                )
                if results:
                    mtf_confluence_results.extend(results)

    # ------------------------------------------------------------------
    # MTF Hierarchical
    # ------------------------------------------------------------------
    mtf_hierarchical_results: List[Dict[str, Any]] = []
    hier_cfg = mtf_cfg.get("hierarchical", {})
    if hier_cfg.get("enabled", True):
        ctx_tf  = int(hier_cfg.get("context_tf", 5))
        ent_tf  = int(hier_cfg.get("entry_tf", 1))
        str_min = float(hier_cfg.get("context_strength_min", 0.0))

        ctx_sigs = signals_by_tf.get(ctx_tf, {})
        ent_sigs = signals_by_tf.get(ent_tf, {})

        if ctx_sigs and ent_sigs:
            for sig_key in ent_sigs:
                # Match context signals with same key
                ctx_df = ctx_sigs.get(sig_key)
                ent_df = ent_sigs[sig_key]
                if ctx_df is None or ctx_df.empty or ent_df is None or ent_df.empty:
                    continue

                filtered = apply_hierarchical_filter(
                    ctx_df, ent_df,
                    context_tf_minutes=ctx_tf,
                    entry_tf_minutes=ent_tf,
                    context_strength_min=str_min,
                )
                if filtered is None or filtered.empty:
                    continue

                parts = sig_key.rsplit("_dir", 1)
                pat_id  = parts[0]
                dir_val = int(parts[1]) if len(parts) > 1 else 0

                for timing_mode in enabled_timings:
                    timing_cfg_item = timing_cfgs.get(timing_mode, {})
                    n_combinations_run += 1

                    results = _run_single_combo(
                        enriched_tf=filtered,
                        bars_1m=bars_1m,
                        pattern_id=pat_id,
                        params={},
                        direction=dir_val,
                        timing_mode=timing_mode,
                        timing_cfg=timing_cfg_item,
                        outcome_cfg=outcome_cfg,
                        bars_with_regime=bars_with_regime,
                        mtf_label=f"hierarchical_{ctx_tf}m_{ent_tf}m",
                        tf_minutes=ent_tf,
                        min_trades=min_trades,
                        filter_cfg=filter_cfg,
                        hardening_cfg=hardening_cfg,
                        bars_tf=bars_tf_cache.get(ent_tf),
                    )
                    if results:
                        mtf_hierarchical_results.extend(results)

    return {
        "sweep_results":             sweep_results,
        "mtf_confluence_results":    mtf_confluence_results,
        "mtf_hierarchical_results":  mtf_hierarchical_results,
        "n_combinations_run":        n_combinations_run,
        "n_results":                 len(sweep_results) + len(mtf_confluence_results) + len(mtf_hierarchical_results),
        "trial_grid_size":           trial_grid_size,
    }


# ---------------------------------------------------------------------------
# Deep-merge helper
# ---------------------------------------------------------------------------

def _deep_merge(base: Dict, override: Dict) -> Dict:
    """Recursively merge override into base (override wins on scalar conflicts)."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
