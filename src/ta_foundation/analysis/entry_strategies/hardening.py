from __future__ import annotations

"""
Hardening metadata for entry-strategy discovery sweeps.

The family sweep engines produce already-simulated trade groups. This module
adapts those rows into the strategy_discovery validation stack so funnel
results carry the same walk-forward, Monte Carlo, OOS, and slippage-stress
evidence as the hardened orchestrator.
"""

import json
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from ta_foundation.analysis.strategy_discovery.evaluation import compute_evaluation_metrics
from ta_foundation.analysis.strategy_discovery.holdout import partition_trades
from ta_foundation.analysis.strategy_discovery.honest_execution import apply_honest_execution
from ta_foundation.analysis.strategy_discovery.slippage_stress import run_slippage_stress
from ta_foundation.analysis.strategy_discovery.trial_budget import compute_trial_budget
from ta_foundation.analysis.strategy_discovery.validation import (
    DEFAULT_COST_MODEL,
    DEFAULT_WF_CONFIG,
    extract_oos_pool,
    run_validation,
)


DEFAULT_HARDENING_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "n_mc_simulations": 100,
    "n_hypotheses_tested": 1,
    "min_t_stat_abs": 0.0,
    "require_slippage_stress_passed": True,
    "require_honest_execution_passed": True,
    "wf_config": {},
    "slippage_stress": {},
    "honest_execution": {},
    "trial_budget": {},
    "holdout": {
        "enabled": False,
        "is_frac": 0.60,
        "val_frac": 0.20,
        "time_col": "entry_time",
        "min_dev_trades": 50,
        "min_holdout_trades": 20,
        "require_holdout_passed": False,
        "min_profit_factor": 1.0,
    },
}


def attach_hardening_metadata(
    result: Dict[str, Any],
    trades: pd.DataFrame,
    outcome_cfg: Dict[str, Any],
    hardening_cfg: Optional[Dict[str, Any]] = None,
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Attach validation metadata to one sweep result dict in-place."""
    metadata = build_hardening_metadata(trades, outcome_cfg, hardening_cfg, bars_with_regime)
    result["hardening"] = metadata
    result["hardening_passed"] = metadata.get("passed")
    result["validation"] = metadata.get("validation")
    result["evaluation_oos"] = metadata.get("evaluation_oos")
    result["holdout_partition"] = metadata.get("holdout_partition")
    result["evaluation_holdout"] = metadata.get("evaluation_holdout")
    result["slippage_stress"] = metadata.get("slippage_stress")
    result["honest_execution"] = metadata.get("honest_execution")
    result["trial_budget"] = metadata.get("trial_budget")

    wf = (metadata.get("validation") or {}).get("wf_results") or {}
    deg = wf.get("oos_degradation")
    if deg is not None:
        result["hardening_is_oos_degradation"] = deg
    return result


def build_hardening_metadata(
    trades: pd.DataFrame,
    outcome_cfg: Dict[str, Any],
    hardening_cfg: Optional[Dict[str, Any]] = None,
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    cfg = _deep_merge(DEFAULT_HARDENING_CONFIG, hardening_cfg or {})
    enabled = bool(cfg.get("enabled", True))
    out: Dict[str, Any] = {
        "enabled": enabled,
        "passed": None,
        "validation": None,
        "evaluation_oos": None,
        "holdout_partition": None,
        "evaluation_holdout": None,
        "slippage_stress": None,
        "honest_execution": None,
        "trial_budget": None,
        "issues": [],
    }
    if not enabled:
        out["issues"].append("hardening disabled in config")
        return out

    try:
        from ta_foundation.analysis.strategy_discovery.evaluation import compute_regime_breakdown

        cost_model = _cost_model_from_outcome(outcome_cfg)
        prepared = prepare_trades_for_hardening(trades, outcome_cfg)
        holdout_meta, dev_trades, holdout_trades = _split_holdout(prepared, cfg.get("holdout") or {})
        out["holdout_partition"] = _json_safe(holdout_meta)
        validation_trades = dev_trades if holdout_meta.get("enabled") else prepared
        wf_config = _deep_merge(DEFAULT_WF_CONFIG, cfg.get("wf_config") or {})

        # Selection-bias accounting: when a trial budget is configured, the
        # effective trial count drives BOTH the Bonferroni t-test correction
        # and the Deflated Sharpe Ratio gate. Absent a budget, fall back to the
        # legacy n_hypotheses_tested key and leave the DSR gate dormant.
        tb_cfg = cfg.get("trial_budget")
        if tb_cfg:
            budget = compute_trial_budget(tb_cfg)
            effective_n = budget.effective_trials
            dsr_trials = budget.effective_trials
            out["trial_budget"] = _json_safe(budget.to_dict())
        else:
            effective_n = max(1, int(cfg.get("n_hypotheses_tested", 1)))
            dsr_trials = None

        validation = run_validation(
            validation_trades,
            wf_config=wf_config,
            cost_model=cost_model,
            n_mc_simulations=int(cfg.get("n_mc_simulations", 100)),
            n_hypotheses_tested=effective_n,
            dsr_n_trials=dsr_trials,
            min_t_stat_abs=float(cfg.get("min_t_stat_abs", 0.0)),
        )
        validation_dict = _json_safe(validation.to_dict())
        # P0-CUMULATIVE audit trail: when the CLI promoted the YAML's
        # n_hypotheses_tested to a cumulative-effective value, surface both
        # numbers so downstream readers can see what bar was actually applied.
        yaml_n = cfg.get("yaml_n_hypotheses_tested")
        if yaml_n is not None:
            t_test = validation_dict.get("t_test") if isinstance(validation_dict, dict) else None
            if isinstance(t_test, dict):
                t_test["yaml_n_hypotheses_tested"] = int(yaml_n)
                t_test["cumulative_family"] = cfg.get("cumulative_family")
                t_test["cumulative_decay_factor"] = cfg.get("cumulative_decay_factor")
        out["validation"] = validation_dict
        out["issues"].extend(validation_dict.get("issues") or [])

        oos_pool = extract_oos_pool(validation.cost_normalized, wf_config=wf_config)
        oos_eval = compute_evaluation_metrics(oos_pool, profit_col="profit_net")
        if bars_with_regime is not None:
            try:
                oos_eval["by_regime"] = compute_regime_breakdown(oos_pool, bars_with_regime, profit_col="profit_net")
            except Exception:
                pass
        out["evaluation_oos"] = _json_safe(oos_eval)

        holdout_eval = _evaluate_holdout(holdout_trades, cfg.get("holdout") or {}, bars_with_regime)
        out["evaluation_holdout"] = _json_safe(holdout_eval)

        stress = run_slippage_stress(
            validation_trades,
            baseline_cost_model=cost_model,
            options=cfg.get("slippage_stress") or {},
        )
        out["slippage_stress"] = _json_safe(stress)

        honest = apply_honest_execution(
            validation_trades,
            cost_model=cost_model,
            options=cfg.get("honest_execution") or {},
        )
        out["honest_execution"] = _json_safe(honest)

        stress_required = bool(cfg.get("require_slippage_stress_passed", True))
        stress_passed = bool(stress.get("passed", False)) if stress_required else True
        honest_required = bool(cfg.get("require_honest_execution_passed", True))
        honest_passed = bool(honest.get("passed", False)) if honest_required else True
        holdout_required = bool((cfg.get("holdout") or {}).get("require_holdout_passed", False))
        holdout_passed = bool((holdout_eval or {}).get("passed", False)) if holdout_required else True
        out["passed"] = bool(
            validation.passed and stress_passed and honest_passed and holdout_passed
        )
        if stress_required and not stress_passed:
            out["issues"].append("hard gate failed: slippage_stress")
        if honest_required and not honest_passed:
            out["issues"].append("hard gate failed: honest_execution")
        if holdout_required and not holdout_passed:
            out["issues"].append("hard gate failed: holdout")
    except Exception as exc:
        out["passed"] = False
        out["issues"].append(f"hardening failed: {type(exc).__name__}: {exc}")
    return out


def _split_holdout(
    trades: pd.DataFrame,
    holdout_cfg: Dict[str, Any],
) -> tuple[Dict[str, Any], pd.DataFrame, Optional[pd.DataFrame]]:
    """Return metadata plus dev/holdout slices for optional locked validation."""
    enabled = bool(holdout_cfg.get("enabled", False))
    meta: Dict[str, Any] = {
        "enabled": False,
        "requested": enabled,
        "reason": None,
    }
    if not enabled:
        meta["reason"] = "holdout disabled in config"
        return meta, trades, None

    time_col = str(holdout_cfg.get("time_col", "entry_time"))
    if trades is None or len(trades) == 0:
        meta["reason"] = "no trades available"
        return meta, trades, None
    if time_col not in trades.columns:
        meta["reason"] = f"time column '{time_col}' not found"
        return meta, trades, None

    part = partition_trades(
        trades,
        is_frac=float(holdout_cfg.get("is_frac", 0.60)),
        val_frac=float(holdout_cfg.get("val_frac", 0.20)),
        time_col=time_col,
    )
    dev_trades = pd.concat([part.is_df, part.val_df], ignore_index=True)
    holdout_trades = part.holdout_df
    min_dev = int(holdout_cfg.get("min_dev_trades", 50))
    min_holdout = int(holdout_cfg.get("min_holdout_trades", 20))

    meta.update(part.to_dict())
    if len(dev_trades) < min_dev or len(holdout_trades) < min_holdout:
        meta["reason"] = (
            "sample too small for holdout split "
            f"(dev={len(dev_trades)} < {min_dev} or holdout={len(holdout_trades)} < {min_holdout})"
        )
        return meta, trades, None

    meta["enabled"] = True
    meta["reason"] = "active"
    meta["dev_trades"] = int(len(dev_trades))
    return meta, dev_trades, holdout_trades


def _evaluate_holdout(
    holdout_trades: Optional[pd.DataFrame],
    holdout_cfg: Dict[str, Any],
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Optional[Dict[str, Any]]:
    if holdout_trades is None or len(holdout_trades) == 0:
        return None

    profit_col = "profit_net" if "profit_net" in holdout_trades.columns else "profit"
    metrics = compute_evaluation_metrics(holdout_trades, profit_col=profit_col)
    
    if bars_with_regime is not None:
        try:
            from ta_foundation.analysis.strategy_discovery.evaluation import compute_regime_breakdown
            metrics["by_regime"] = compute_regime_breakdown(holdout_trades, bars_with_regime, profit_col=profit_col)
        except Exception:
            pass

    pf = metrics.get("profit_factor")
    min_pf = float(holdout_cfg.get("min_profit_factor", 1.0))
    n_trades = int(metrics.get("n_trades") or len(holdout_trades))
    min_holdout = int(holdout_cfg.get("min_holdout_trades", 20))
    metrics["passed"] = bool(
        n_trades >= min_holdout
        and pf is not None
        and float(pf) >= min_pf
    )
    metrics["thresholds"] = {
        "min_trades": min_holdout,
        "min_profit_factor": min_pf,
    }
    return metrics


def prepare_trades_for_hardening(
    trades: pd.DataFrame,
    outcome_cfg: Dict[str, Any],
) -> pd.DataFrame:
    """Return trades with a gross ``profit`` column for validation modules."""
    out = trades.copy()
    if "profit" in out.columns:
        return out

    cost_model = _cost_model_from_outcome(outcome_cfg)
    cost_per_rt = (
        2.0 * float(cost_model.get("commission_per_side", 2.09))
        + 2.0 * float(cost_model.get("slippage_ticks", 1))
        * float(cost_model.get("tick_value", 5.0))
    )

    if "profit_net" in out.columns:
        out["profit"] = pd.to_numeric(out["profit_net"], errors="coerce") + cost_per_rt
    elif "profit_ticks" in out.columns:
        tick_value = float(cost_model.get("tick_value", 5.0))
        out["profit"] = pd.to_numeric(out["profit_ticks"], errors="coerce") * tick_value
    return out


def _cost_model_from_outcome(outcome_cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **DEFAULT_COST_MODEL,
        "commission_per_side": outcome_cfg.get("commission_per_side", DEFAULT_COST_MODEL["commission_per_side"]),
        "slippage_ticks": outcome_cfg.get("slippage_ticks", DEFAULT_COST_MODEL["slippage_ticks"]),
        "tick_value": outcome_cfg.get("tick_value", DEFAULT_COST_MODEL["tick_value"]),
    }


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _json_safe(value: Any) -> Any:
    """Normalize numpy/pandas scalars and NaN before sidecar serialization."""
    scrubbed = _scrub(value)
    return json.loads(json.dumps(scrubbed, allow_nan=False))


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return f if np.isfinite(f) else None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if value is pd.NA:
        return None
    return value
