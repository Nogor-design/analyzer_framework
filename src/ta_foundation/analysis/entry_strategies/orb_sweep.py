from __future__ import annotations

"""
ORB Discovery Sweep
====================
Sweep over ORB parameter combinations and evaluate outcomes.

YAML config shape
------------------
orb_discovery:
  enabled: true
  min_trades: 15

  orb:
    orb_minutes:          [15, 30, 60]
    session_open_hour:    9
    session_open_minute:  30
    direction:            [0, 1, -1]
    min_range_ticks:      [4, 8]
    require_close_beyond: [true, false]

  entry_timing:
    next_open:     {enabled: true}
    break_extreme: {enabled: true, buffer_ticks: 1, fill_timeout_bars: 3}

  outcome:
    atr:   {enabled: true, target_mult: 1.5, stop_mult: 1.0}
    ticks: {enabled: true, take_profit: [30, 60, 100], stop: [30, 40, 50]}
    ...

  filter_discovery: {enabled: true, top_n: 10}
"""

from itertools import product
from typing import Any, Dict, List, Optional

import pandas as pd

from ta_foundation.analysis.entry_strategies.orb.signals import detect_orb, DEFAULT_ORB_CONFIG
from ta_foundation.analysis.entry_strategies.candle.signals import emit_entries
from ta_foundation.analysis.entry_strategies.outcome.simulator import simulate_outcomes
from ta_foundation.analysis.strategy_discovery.evaluation import (
    compute_evaluation_metrics,
    compute_regime_breakdown,
)
from ta_foundation.analysis.entry_strategies.validation import compute_is_oos_degradation


# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------

DEFAULT_ORB_DISCOVERY_CONFIG: Dict[str, Any] = {
    "enabled":    True,
    "min_trades": 15,

    "orb": {
        "orb_minutes":           [15, 30, 60],
        "session_open_hour":     9,
        "session_open_minute":   30,
        "session_close_hour":    16,
        "direction":             [0],
        "min_range_ticks":       [4, 8],
        "tick_size":             0.25,
        "atr_period":            14,
        "require_close_beyond":  [True, False],
        "one_signal_per_side":   True,
    },

    "entry_timing": {
        "next_open":     {"enabled": True},
        "break_extreme": {"enabled": True, "buffer_ticks": 1, "fill_timeout_bars": 3},
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
# Helpers (shared pattern with other sweeps)
# ---------------------------------------------------------------------------

def _deep_merge(base: Dict, override: Dict) -> Dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _expand_params(orb_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    list_keys   = {k: v for k, v in orb_cfg.items() if isinstance(v, list)}
    scalar_keys = {k: v for k, v in orb_cfg.items() if not isinstance(v, list)}

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


def _try_filter_discovery(trades_df: pd.DataFrame, filter_cfg: Dict) -> List[Dict]:
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


def _run_single_combo(
    bars_1m: pd.DataFrame,
    params: Dict[str, Any],
    timing_mode: str,
    timing_cfg: Dict[str, Any],
    outcome_cfg: Dict[str, Any],
    bars_with_regime: Optional[pd.DataFrame],
    min_trades: int,
    filter_cfg: Dict[str, Any],
) -> Optional[List[Dict[str, Any]]]:

    # 1. Detect ORB signals
    signals_df = detect_orb(bars_1m, params)
    if signals_df is None or signals_df.empty:
        return None

    n_signals = len(signals_df)

    # 2. Emit entries
    timing_params = {**timing_cfg, "tick_size": outcome_cfg.get("tick_size", 0.25)}
    try:
        pending = emit_entries(signals_df, timing_mode, bars=bars_1m, params=timing_params)
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
        if "entry_time" in group.columns and "session_label" not in group.columns:
            group["session_label"] = _session_label(group["entry_time"])

        # Metrics
        try:
            metrics = compute_evaluation_metrics(group, profit_col="profit_net")
        except Exception:
            metrics = {}

        regime_bk: Dict[str, Any] = {}
        if bars_with_regime is not None:
            try:
                regime_bk = compute_regime_breakdown(group, bars_with_regime, profit_col="profit_net")
            except Exception:
                pass

        session_bk: Dict[str, Any] = {}
        if "session_label" in group.columns:
            try:
                for sess, sgrp in group.groupby("session_label"):
                    profits = pd.to_numeric(sgrp["profit_net"], errors="coerce").dropna()
                    if not len(profits):
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

        direction_val  = int(params.get("direction", 0))
        dir_label      = {1: "long", -1: "short", 0: "both"}.get(direction_val, str(direction_val))
        fill_rate      = round(len(group) / n_signals, 4) if n_signals > 0 else 0.0
        filter_results = _try_filter_discovery(group, filter_cfg)
        params_key     = "|".join(f"{k}={v}" for k, v in sorted(params.items()))

        all_results.append({
            "strategy_type":    "orb",
            "signal_id":        "orb",
            "pattern_id":       f"ORB_{params.get('orb_minutes', 30)}m",
            "tf":               1,
            "params":           params,
            "params_key":       params_key,
            "direction_mode":   dir_label,
            "entry_timing":     timing_mode,
            "outcome_mode":     str(om),
            "mtf_mode":         "independent_1m",
            "n_signals":        n_signals,
            "n_trades":         len(group),
            "fill_rate":        fill_rate,
            "metrics":          metrics,
            "regime_breakdown": regime_bk,
            "session_breakdown": session_bk,
            "filter_results":   filter_results,
            "is_oos_degradation": compute_is_oos_degradation(group),
        })

    return all_results if all_results else None


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def run_orb_discovery(
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Run the ORB discovery sweep.

    Parameters
    ----------
    bars_1m          : 1-minute OHLCV bars (tz-aware America/Denver recommended)
    config           : orb_discovery YAML block (merged with defaults)
    bars_with_regime : optional bars with regime columns

    Returns
    -------
    JSON-safe dict: {sweep_results, n_combinations_run, n_results}
    """
    cfg = _deep_merge(DEFAULT_ORB_DISCOVERY_CONFIG, config or {})

    min_trades  = int(cfg["min_trades"])
    orb_cfg     = cfg.get("orb", {})
    timing_cfgs = cfg.get("entry_timing", {})
    outcome_cfg = cfg.get("outcome", {})
    filter_cfg  = cfg.get("filter_discovery", {})

    enabled_timings = [tm for tm, tc in timing_cfgs.items() if tc.get("enabled", True)]
    param_combos    = _expand_params(orb_cfg)

    sweep_results:      List[Dict[str, Any]] = []
    n_combinations_run: int = 0

    for params in param_combos:
        for timing_mode in enabled_timings:
            timing_cfg_item = timing_cfgs.get(timing_mode, {})
            n_combinations_run += 1

            results = _run_single_combo(
                bars_1m=bars_1m,
                params=params,
                timing_mode=timing_mode,
                timing_cfg=timing_cfg_item,
                outcome_cfg=outcome_cfg,
                bars_with_regime=bars_with_regime,
                min_trades=min_trades,
                filter_cfg=filter_cfg,
            )
            if results:
                sweep_results.extend(results)

    return {
        "sweep_results":      sweep_results,
        "n_combinations_run": n_combinations_run,
        "n_results":          len(sweep_results),
    }
