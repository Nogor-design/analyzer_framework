from __future__ import annotations

"""
Shared sweep runner used by breakout_sweep, pullback_sweep, and level_sweep.

All three strategy families share identical pipeline logic:
  detect signals → emit entries → simulate outcomes → metrics → store result

This module provides `run_generic_sweep()` so each family only needs to
supply its signal registry and default config.
"""

from itertools import product
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from ta_foundation.marketdata.resample import ohlcv_resample_from_bars
from ta_foundation.analysis.entry_strategies.candle.signals import emit_entries
from ta_foundation.analysis.entry_strategies.outcome.simulator import simulate_outcomes
from ta_foundation.analysis.strategy_discovery.evaluation import (
    compute_evaluation_metrics,
    compute_regime_breakdown,
)
from ta_foundation.analysis.entry_strategies.validation import compute_is_oos_degradation


def _deep_merge(base: Dict, override: Dict) -> Dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _expand_params(signal_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    list_keys   = {k: v for k, v in signal_cfg.items()
                   if isinstance(v, list) and k != "enabled"}
    scalar_keys = {k: v for k, v in signal_cfg.items()
                   if not isinstance(v, list) and k != "enabled"}
    if not list_keys:
        return [{**scalar_keys}]
    keys   = list(list_keys.keys())
    values = [list_keys[k] for k in keys]
    return [{**scalar_keys, **dict(zip(keys, combo))} for combo in product(*values)]


def _try_filter_discovery(trades_df: pd.DataFrame, filter_cfg: Dict) -> List[Dict]:
    if not filter_cfg.get("enabled", False):
        return []
    try:
        from ta_foundation.analysis.strategy_discovery.filter_discovery import run_filter_discovery
        top_n   = int(filter_cfg.get("top_n", 10))
        results = run_filter_discovery(trades_df, options={"top_n": top_n})
        return results[:top_n] if results else []
    except Exception:
        return []


def _session_label(entry_time: pd.Series) -> pd.Series:
    try:
        from ta_foundation.analysis.session_constants import session_label_from_hour
        dt    = pd.to_datetime(entry_time)
        local = dt.dt.tz_convert("America/Denver") if dt.dt.tz is not None else dt
        return local.dt.hour.map(session_label_from_hour)
    except Exception:
        return pd.Series("unknown", index=entry_time.index)


def _run_single_combo(
    bars_tf: pd.DataFrame,
    bars_1m: pd.DataFrame,
    signal_fn: Callable,
    signal_id: str,
    strategy_type: str,
    params: Dict[str, Any],
    timing_mode: str,
    timing_cfg: Dict[str, Any],
    outcome_cfg: Dict[str, Any],
    bars_with_regime: Optional[pd.DataFrame],
    tf_minutes: int,
    min_trades: int,
    filter_cfg: Dict[str, Any],
) -> Optional[List[Dict[str, Any]]]:

    signals_df = signal_fn(bars_tf, params)
    if signals_df is None or signals_df.empty:
        return None

    n_signals     = len(signals_df)
    timing_params = {**timing_cfg, "tick_size": outcome_cfg.get("tick_size", 0.25)}
    bars_for_next = ohlcv_resample_from_bars(bars_1m, f"{tf_minutes}m") if timing_mode == "next_open" else None

    try:
        pending = emit_entries(signals_df, timing_mode, bars=bars_for_next, params=timing_params)
    except Exception:
        return None

    if pending is None or pending.empty:
        return None

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
                        "n_trades":      len(profits),
                        "win_rate":      round(n_w / len(profits), 4),
                        "net_profit":    round(float(profits.sum()), 2),
                        "profit_factor": round(gp / gl, 4) if gl > 0 else None,
                    }
            except Exception:
                pass

        direction_val = int(params.get("direction", 0))
        dir_label     = {1: "long", -1: "short", 0: "both"}.get(direction_val, str(direction_val))
        params_key    = "|".join(f"{k}={v}" for k, v in sorted(params.items()))

        all_results.append({
            "strategy_type":     strategy_type,
            "signal_id":         signal_id,
            "pattern_id":        signal_id,
            "tf":                tf_minutes,
            "params":            params,
            "params_key":        params_key,
            "direction_mode":    dir_label,
            "entry_timing":      timing_mode,
            "outcome_mode":      str(om),
            "mtf_mode":          f"independent_{tf_minutes}m",
            "n_signals":         n_signals,
            "n_trades":          len(group),
            "fill_rate":         round(len(group) / n_signals, 4) if n_signals > 0 else 0.0,
            "metrics":           metrics,
            "regime_breakdown":  regime_bk,
            "session_breakdown": session_bk,
            "filter_results":    _try_filter_discovery(group, filter_cfg),
            "is_oos_degradation": compute_is_oos_degradation(group),
        })

    return all_results if all_results else None


def run_generic_sweep(
    bars_1m: pd.DataFrame,
    strategy_type: str,
    signal_registry: Dict[str, Callable],
    config: Dict[str, Any],
    default_config: Dict[str, Any],
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Generic sweep runner for any bars-based signal family.

    Parameters
    ----------
    bars_1m          : 1m OHLCV bars
    strategy_type    : label stored in results ('breakout', 'pullback', 'level')
    signal_registry  : {signal_id: callable(bars, params) -> signals_df}
    config           : user YAML config block
    default_config   : default config for this strategy family
    bars_with_regime : optional regime bars

    Returns
    -------
    {sweep_results, n_combinations_run, n_results}
    """
    cfg = _deep_merge(default_config, config)

    timeframes      = [int(tf) for tf in cfg.get("timeframes", [1])]
    min_trades      = int(cfg.get("min_trades", 20))
    signal_cfgs     = cfg.get("signals", {})
    timing_cfgs     = cfg.get("entry_timing", {})
    outcome_cfg     = cfg.get("outcome", {})
    filter_cfg      = cfg.get("filter_discovery", {})
    enabled_timings = [tm for tm, tc in timing_cfgs.items() if tc.get("enabled", True)]

    sweep_results:      List[Dict[str, Any]] = []
    n_combinations_run: int = 0

    for tf in timeframes:
        bars_tf = bars_1m.copy() if tf == 1 else ohlcv_resample_from_bars(bars_1m, f"{tf}m")
        if bars_tf is None or bars_tf.empty:
            continue

        for signal_id, sig_cfg in signal_cfgs.items():
            if not sig_cfg.get("enabled", True):
                continue
            signal_fn = signal_registry.get(signal_id)
            if signal_fn is None:
                continue

            for params in _expand_params(sig_cfg):
                # Inject tick_size / atr_period from outcome/features cfg
                params.setdefault("tick_size",  outcome_cfg.get("tick_size", 0.25))
                params.setdefault("atr_period", cfg.get("atr_period", 14))

                for timing_mode in enabled_timings:
                    timing_cfg_item = timing_cfgs.get(timing_mode, {})
                    n_combinations_run += 1

                    results = _run_single_combo(
                        bars_tf=bars_tf,
                        bars_1m=bars_1m,
                        signal_fn=signal_fn,
                        signal_id=signal_id,
                        strategy_type=strategy_type,
                        params=params,
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
    }
