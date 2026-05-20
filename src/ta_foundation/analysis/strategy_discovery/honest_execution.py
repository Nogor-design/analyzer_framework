from __future__ import annotations

"""
Honest Execution Re-Pricing (fill-realism gate)
================================================
The outcome simulator credits a win whenever a 1-minute bar's high/low *touches*
the target and books the exit *at* that level. On 1m futures a wick brushing a
price is not a fill — you do not get the wick. Stop exits slip through their
level and break/limit entries fill worse than their trigger. The net effect is
systematically inflated win sizes.

This module re-prices already-realised trades with a pessimistic fill model and
applies an **absolute survival gate**: the re-priced profit factor and
expectancy must independently clear floors. That is the key difference from
``slippage_stress`` — that module asks "does the edge *degrade* less than X%
from the most-optimistic cell" (a relative test a fat edge passes trivially);
this module asks "is the edge *still genuinely profitable* once fills are
honest" (an absolute test).

The haircut is deliberately asymmetric: target exits ("wins") take a larger
haircut than entries, because missing-the-wick is the dominant inflation in a
touch-fill backtest.

Scope: this is a haircut on realised trades, not a re-simulation. It cannot
reclassify a phantom win that never would have filled at all — that needs full
tick-replay outcome resolution, a separate follow-up. It also does not account
for selection bias across a parameter sweep — that is a separate gate.
"""

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


DEFAULT_HONEST_EXECUTION_CONFIG: Dict[str, Any] = {
    "enabled": True,
    # Pessimistic fill haircut, in ticks per trade, on top of commission.
    # These are a defensible floor for 1m index futures — tune per instrument.
    "entry_slippage_ticks": 1.0,        # every entry fills worse than the signal
    "win_exit_haircut_ticks": 2.0,      # target exits: you miss the wick
    "stop_exit_slippage_ticks": 2.0,    # stop exits slip through the level
    "timeout_exit_slippage_ticks": 3.0, # market exit on timeout
    # Absolute survival gate, evaluated on the honest (re-priced) trades.
    "min_profit_factor": 1.10,
    "min_expectancy": 0.0,
    "min_trades": 30,
}


def _safe_finite(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except Exception:
        return None
    return f if np.isfinite(f) else None


def _metrics(net: pd.Series) -> Dict[str, Any]:
    """Profit factor / expectancy / win rate / net profit for a net-P&L series."""
    valid = pd.to_numeric(net, errors="coerce").dropna()
    n = int(len(valid))
    if n == 0:
        return {
            "n_trades": 0, "profit_factor": None, "expectancy": None,
            "net_profit": None, "win_rate": None,
        }
    gross_profit = float(valid[valid > 0].sum())
    gross_loss = float(valid[valid < 0].sum())
    pf = None if gross_loss == 0 else _safe_finite(gross_profit / abs(gross_loss))
    return {
        "n_trades": n,
        "profit_factor": pf,
        "expectancy": _safe_finite(valid.mean()),
        "net_profit": _safe_finite(valid.sum()),
        "win_rate": _safe_finite(float((valid > 0).mean())),
    }


def apply_honest_execution(
    trades: Optional[pd.DataFrame],
    *,
    cost_model: Dict[str, Any],
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Re-price ``trades`` under a pessimistic fill model and apply the gate.

    Parameters
    ----------
    trades       : trade DataFrame; must carry a ``profit`` column (gross dollar
                   P&L, before costs). A ``result`` column ("win"/"loss"/
                   "timeout") selects the per-exit haircut; if absent, the exit
                   type is inferred from the sign of ``profit``.
    cost_model   : dict with ``commission_per_side``, ``tick_value``, and the
                   pipeline's optimistic ``slippage_ticks`` (used only to
                   compute the baseline for comparison).
    options      : merged over ``DEFAULT_HONEST_EXECUTION_CONFIG``.

    Returns
    -------
    JSON-safe dict: baseline vs honest metrics, the haircut applied, the
    absolute gate thresholds, and ``passed``.
    """
    cfg = {**DEFAULT_HONEST_EXECUTION_CONFIG, **(options or {})}

    result: Dict[str, Any] = {
        "enabled": bool(cfg.get("enabled", True)),
        "n_trades": 0,
        "haircut_ticks": None,
        "baseline": None,
        "honest": None,
        "edge_retained_pct": None,
        "gate": {
            "min_profit_factor": float(cfg.get("min_profit_factor", 1.10)),
            "min_expectancy": float(cfg.get("min_expectancy", 0.0)),
            "min_trades": int(cfg.get("min_trades", 30)),
        },
        "passed": False,
        "issues": [],
    }

    if not result["enabled"]:
        result["issues"].append("honest_execution disabled in config")
        return result
    if trades is None or not isinstance(trades, pd.DataFrame) or len(trades) == 0:
        result["issues"].append("no trades provided")
        return result
    if "profit" not in trades.columns:
        result["issues"].append("'profit' column (gross P&L) not found on trades")
        return result

    tick_value = float(cost_model.get("tick_value", 5.0))
    commission_per_side = float(cost_model.get("commission_per_side", 2.09))
    if tick_value <= 0:
        result["issues"].append("tick_value must be positive")
        return result

    gross = pd.to_numeric(trades["profit"], errors="coerce")
    finite = gross.notna()
    gross = gross[finite]
    n = int(len(gross))
    if n == 0:
        result["issues"].append("no finite gross profit values")
        return result
    result["n_trades"] = n

    entry_slip = float(cfg["entry_slippage_ticks"])
    win_haircut = float(cfg["win_exit_haircut_ticks"])
    stop_slip = float(cfg["stop_exit_slippage_ticks"])
    timeout_slip = float(cfg["timeout_exit_slippage_ticks"])
    result["haircut_ticks"] = {
        "entry": entry_slip,
        "win_exit": win_haircut,
        "stop_exit": stop_slip,
        "timeout_exit": timeout_slip,
    }

    # Pick the per-trade exit haircut from the resolved outcome.
    if "result" in trades.columns:
        res = trades["result"].astype(str).str.lower()[finite]
    else:
        res = pd.Series(np.where(gross > 0, "win", "loss"), index=gross.index)
        result["issues"].append(
            "no 'result' column; exit type inferred from P&L sign"
        )
    exit_slip = pd.Series(stop_slip, index=gross.index)
    exit_slip = exit_slip.mask(res.eq("win"), win_haircut)
    exit_slip = exit_slip.mask(res.eq("timeout"), timeout_slip)

    # Baseline: the optimistic cost model the pipeline uses today.
    base_slip_ticks = float(cost_model.get("slippage_ticks", 1.0))
    baseline_cost = 2.0 * commission_per_side + 2.0 * base_slip_ticks * tick_value
    baseline_net = gross - baseline_cost

    # Honest: per-trade pessimistic entry + (asymmetric) exit haircut.
    friction_ticks = entry_slip + exit_slip
    honest_net = gross - friction_ticks * tick_value - 2.0 * commission_per_side

    baseline = _metrics(baseline_net)
    honest = _metrics(honest_net)
    result["baseline"] = baseline
    result["honest"] = honest

    b_exp = baseline.get("expectancy")
    h_exp = honest.get("expectancy")
    if b_exp not in (None, 0) and h_exp is not None:
        result["edge_retained_pct"] = _safe_finite(h_exp / b_exp * 100.0)

    gate = result["gate"]
    h_pf = honest.get("profit_factor")
    pf_ok = h_pf is not None and h_pf >= gate["min_profit_factor"]
    exp_ok = h_exp is not None and h_exp > gate["min_expectancy"]
    n_ok = n >= gate["min_trades"]

    if not n_ok:
        result["issues"].append(
            f"only {n} trades < min_trades {gate['min_trades']}"
        )
    if not pf_ok:
        result["issues"].append(
            f"honest profit_factor {h_pf} < min {gate['min_profit_factor']}"
        )
    if not exp_ok:
        result["issues"].append(
            f"honest expectancy {h_exp} <= min {gate['min_expectancy']}"
        )

    result["passed"] = bool(pf_ok and exp_ok and n_ok)
    return result
