from __future__ import annotations

"""
Strategy Validation
===================
Centralized hard-gate module. Candidates that fail any check are rejected
before evaluation/ranking.

Checks performed:
  1. Minimum trade counts (IS + OOS)
  2. Walk-forward split and IS/OOS metric comparison
  3. Statistical significance (t-test on trade returns)
  4. Monte Carlo trade-sequence test (actual DD vs shuffled DD distribution)
  5. Transaction cost normalization
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats


DEFAULT_WF_CONFIG: Dict[str, Any] = {
    "wf_type": "rolling",
    "is_pct": 0.70,
    "min_oos_trades": 20,
    "min_is_trades": 50,
    "n_folds": 5,
    "degradation_threshold": 0.20,
}

DEFAULT_COST_MODEL: Dict[str, Any] = {
    "commission_per_side": 2.09,
    "slippage_ticks": 1,
    "tick_value": 5.00,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> Optional[float]:
    """Return float or None for non-finite / unconvertible values."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if not np.isfinite(f) else f
    except Exception:
        return None


def _compute_profit_factor(returns: pd.Series) -> Optional[float]:
    """Profit factor: sum of wins / abs(sum of losses). Returns None if no losses."""
    valid = pd.to_numeric(returns, errors="coerce").dropna()
    if len(valid) == 0:
        return None
    gross_profit = float(valid[valid > 0].sum())
    gross_loss = float(valid[valid < 0].sum())
    if gross_loss == 0:
        return None  # infinite PF — avoid misleading consumers
    return _safe_float(gross_profit / abs(gross_loss))


def _compute_expectancy(returns: pd.Series) -> Optional[float]:
    """Average trade P&L."""
    valid = pd.to_numeric(returns, errors="coerce").dropna()
    if len(valid) == 0:
        return None
    return _safe_float(float(valid.mean()))


def _compute_win_rate(returns: pd.Series) -> Optional[float]:
    """Fraction of trades with profit > 0."""
    valid = pd.to_numeric(returns, errors="coerce").dropna()
    if len(valid) == 0:
        return None
    return _safe_float(float((valid > 0).mean()))


def _compute_max_drawdown(cumulative_pnl: pd.Series) -> float:
    """
    Max drawdown on a cumulative P&L series.
    Uses running-max minus current approach.
    """
    arr = pd.to_numeric(cumulative_pnl, errors="coerce").fillna(0).values
    if len(arr) == 0:
        return 0.0
    peak = np.maximum.accumulate(arr)
    drawdowns = peak - arr
    return float(np.max(drawdowns))


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def apply_cost_model(
    trades: pd.DataFrame,
    cost_model: Dict[str, Any],
) -> pd.DataFrame:
    """
    Return a copy of trades with a 'profit_net' column reflecting
    commission + slippage costs per round-trip.

    cost_per_rt = 2 * commission_per_side + 2 * slippage_ticks * tick_value
    profit_net = profit - cost_per_rt  (if profit column exists and is not NaN)
    """
    out = trades.copy()

    commission_per_side = float(cost_model.get("commission_per_side", 2.09))
    slippage_ticks = float(cost_model.get("slippage_ticks", 1))
    tick_value = float(cost_model.get("tick_value", 5.00))

    cost_per_rt = 2.0 * commission_per_side + 2.0 * slippage_ticks * tick_value

    if "profit" in out.columns:
        profit = pd.to_numeric(out["profit"], errors="coerce")
        out["profit_net"] = profit - cost_per_rt
    else:
        out["profit_net"] = np.nan

    return out


def compute_walk_forward(
    trades: pd.DataFrame,
    profit_col: str = "profit_net",
    *,
    wf_config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Single rolling walk-forward split.

    Sorts trades by entry_time, splits IS/OOS by is_pct.
    Computes PF, win_rate, expectancy, n_trades for IS and OOS.

    Returns dict:
      is_trades, oos_trades, is_pf, oos_pf, is_expectancy, oos_expectancy,
      oos_degradation (1 - oos_pf/is_pf), passed_degradation (bool)
    """
    is_pct = float(wf_config.get("is_pct", 0.70))
    min_is_trades = int(wf_config.get("min_is_trades", 50))
    min_oos_trades = int(wf_config.get("min_oos_trades", 20))
    degradation_threshold = float(wf_config.get("degradation_threshold", 0.20))

    result: Dict[str, Any] = {
        "is_trades": 0,
        "oos_trades": 0,
        "is_pf": None,
        "oos_pf": None,
        "is_expectancy": None,
        "oos_expectancy": None,
        "is_win_rate": None,
        "oos_win_rate": None,
        "oos_degradation": None,
        "passed_degradation": False,
        "passed_min_counts": False,
        "issues": [],
    }

    if trades is None or len(trades) == 0:
        result["issues"].append("no trades for walk-forward")
        return result

    # Sort by entry_time if available
    df = trades.copy()
    if "entry_time" in df.columns:
        df = df.sort_values("entry_time").reset_index(drop=True)

    if profit_col not in df.columns:
        result["issues"].append(f"profit column '{profit_col}' not found")
        return result

    n = len(df)
    split_idx = int(np.floor(n * is_pct))
    is_df = df.iloc[:split_idx]
    oos_df = df.iloc[split_idx:]

    n_is = len(is_df)
    n_oos = len(oos_df)
    result["is_trades"] = n_is
    result["oos_trades"] = n_oos

    # Check minimum trade counts
    passed_min = n_is >= min_is_trades and n_oos >= min_oos_trades
    result["passed_min_counts"] = passed_min
    if n_is < min_is_trades:
        result["issues"].append(f"IS trades {n_is} < min_is_trades {min_is_trades}")
    if n_oos < min_oos_trades:
        result["issues"].append(f"OOS trades {n_oos} < min_oos_trades {min_oos_trades}")

    is_returns = pd.to_numeric(is_df[profit_col], errors="coerce").dropna()
    oos_returns = pd.to_numeric(oos_df[profit_col], errors="coerce").dropna()

    is_pf = _compute_profit_factor(is_returns)
    oos_pf = _compute_profit_factor(oos_returns)

    result["is_pf"] = is_pf
    result["oos_pf"] = oos_pf
    result["is_expectancy"] = _compute_expectancy(is_returns)
    result["oos_expectancy"] = _compute_expectancy(oos_returns)
    result["is_win_rate"] = _compute_win_rate(is_returns)
    result["oos_win_rate"] = _compute_win_rate(oos_returns)

    # Degradation: only meaningful when both PFs are known and IS PF > 0
    if is_pf is not None and oos_pf is not None and is_pf > 0:
        degradation = 1.0 - (oos_pf / is_pf)
        result["oos_degradation"] = _safe_float(degradation)
        result["passed_degradation"] = bool(degradation <= degradation_threshold)
    else:
        result["oos_degradation"] = None
        result["passed_degradation"] = False
        result["issues"].append("degradation check skipped — IS or OOS profit factor unavailable")

    return result


def run_t_test(
    trades: pd.DataFrame,
    profit_col: str = "profit_net",
) -> Dict[str, Any]:
    """
    One-sample t-test: trade returns vs zero.
    Returns: t_stat, p_value, passed (p < 0.05), n_valid
    """
    result: Dict[str, Any] = {
        "t_stat": None,
        "p_value": None,
        "passed": False,
        "n_valid": 0,
        "issues": [],
    }

    if trades is None or len(trades) == 0:
        result["issues"].append("no trades for t-test")
        return result

    if profit_col not in trades.columns:
        result["issues"].append(f"profit column '{profit_col}' not found")
        return result

    returns = pd.to_numeric(trades[profit_col], errors="coerce").dropna()
    n_valid = int(len(returns))
    result["n_valid"] = n_valid

    if n_valid < 2:
        result["issues"].append(f"insufficient valid trades for t-test: {n_valid}")
        return result

    try:
        t_stat, p_value = scipy_stats.ttest_1samp(returns.values, popmean=0.0)
        result["t_stat"] = _safe_float(t_stat)
        result["p_value"] = _safe_float(p_value)
        result["passed"] = bool(p_value < 0.05 and t_stat > 0)
    except Exception as exc:
        result["issues"].append(f"t-test failed: {exc}")

    return result


def run_monte_carlo(
    trades: pd.DataFrame,
    profit_col: str = "profit_net",
    *,
    n_simulations: int = 1000,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Monte Carlo trade-sequence shuffle test.

    Shuffles trade order N times, computes max drawdown for each shuffle.
    Returns:
      actual_max_dd, mc_dd_p95, dd_percentile_rank (0-100),
      passed (actual_max_dd < mc_dd_p95)

    Max drawdown computed on cumulative P&L series.
    """
    result: Dict[str, Any] = {
        "actual_max_dd": None,
        "mc_dd_p95": None,
        "dd_percentile_rank": None,
        "passed": False,
        "n_simulations": n_simulations,
        "issues": [],
    }

    if trades is None or len(trades) == 0:
        result["issues"].append("no trades for Monte Carlo")
        return result

    if profit_col not in trades.columns:
        result["issues"].append(f"profit column '{profit_col}' not found")
        return result

    returns = pd.to_numeric(trades[profit_col], errors="coerce").dropna().values
    n_valid = len(returns)
    result["n_simulations_actual"] = n_simulations

    if n_valid < 2:
        result["issues"].append(f"insufficient valid trades for Monte Carlo: {n_valid}")
        return result

    # Actual max drawdown (in original trade sequence)
    actual_cumulative = np.cumsum(returns)
    actual_max_dd = _compute_max_drawdown(pd.Series(actual_cumulative))
    result["actual_max_dd"] = _safe_float(actual_max_dd)

    # Shuffled max drawdowns
    rng = np.random.default_rng(seed)
    sim_dds: List[float] = []
    for _ in range(n_simulations):
        shuffled = rng.permutation(returns)
        cum = np.cumsum(shuffled)
        sim_dds.append(_compute_max_drawdown(pd.Series(cum)))

    sim_arr = np.array(sim_dds)
    mc_dd_p95 = float(np.percentile(sim_arr, 95))
    result["mc_dd_p95"] = _safe_float(mc_dd_p95)

    # Percentile rank of actual DD within simulated distribution
    # Higher rank = actual DD is worse than most simulations
    dd_pct_rank = float(100.0 * np.mean(sim_arr <= actual_max_dd))
    result["dd_percentile_rank"] = _safe_float(dd_pct_rank)

    # Passed: actual DD is below the 95th percentile of shuffled DDs
    result["passed"] = bool(actual_max_dd < mc_dd_p95)

    return result


def run_validation(
    trades: pd.DataFrame,
    *,
    wf_config: Optional[Dict[str, Any]] = None,
    cost_model: Optional[Dict[str, Any]] = None,
    n_mc_simulations: int = 1000,
) -> Dict[str, Any]:
    """
    Run all validation checks. Returns comprehensive result dict.

    Returns
    -------
    dict with:
      passed              : bool — True only if ALL hard gates pass
      cost_normalized     : DataFrame with profit_net column
      wf_results          : from compute_walk_forward
      t_test              : from run_t_test
      monte_carlo         : from run_monte_carlo
      issues              : list of failure reason strings
      summary             : human-readable pass/fail summary
    """
    resolved_wf_config = {**DEFAULT_WF_CONFIG, **(wf_config or {})}
    resolved_cost_model = {**DEFAULT_COST_MODEL, **(cost_model or {})}

    all_issues: List[str] = []

    # Step 1: Apply cost model
    if trades is None or not isinstance(trades, pd.DataFrame) or len(trades) == 0:
        return {
            "passed": False,
            "cost_normalized": pd.DataFrame(),
            "wf_results": {},
            "t_test": {},
            "monte_carlo": {},
            "issues": ["no trades provided"],
            "summary": "FAILED — no trades provided",
        }

    cost_normalized = apply_cost_model(trades, resolved_cost_model)

    # Step 2: Walk-forward
    wf_results = compute_walk_forward(
        cost_normalized,
        profit_col="profit_net",
        wf_config=resolved_wf_config,
    )
    if wf_results.get("issues"):
        all_issues.extend(wf_results["issues"])

    # Step 3: T-test
    t_test = run_t_test(cost_normalized, profit_col="profit_net")
    if t_test.get("issues"):
        all_issues.extend(t_test["issues"])

    # Step 4: Monte Carlo
    monte_carlo = run_monte_carlo(
        cost_normalized,
        profit_col="profit_net",
        n_simulations=n_mc_simulations,
    )
    if monte_carlo.get("issues"):
        all_issues.extend(monte_carlo["issues"])

    # Hard gate evaluation
    gate_results: Dict[str, bool] = {
        "min_counts": bool(wf_results.get("passed_min_counts", False)),
        "degradation": bool(wf_results.get("passed_degradation", False)),
        "t_test": bool(t_test.get("passed", False)),
        "monte_carlo": bool(monte_carlo.get("passed", False)),
    }

    passed = all(gate_results.values())

    # Human-readable summary
    gate_lines = [
        f"  min_counts:  {'PASS' if gate_results['min_counts'] else 'FAIL'}"
        f"  (IS={wf_results.get('is_trades', 0)}, OOS={wf_results.get('oos_trades', 0)})",
        f"  degradation: {'PASS' if gate_results['degradation'] else 'FAIL'}"
        f"  (oos_degradation={wf_results.get('oos_degradation')})",
        f"  t_test:      {'PASS' if gate_results['t_test'] else 'FAIL'}"
        f"  (p={t_test.get('p_value')}, t={t_test.get('t_stat')})",
        f"  monte_carlo: {'PASS' if gate_results['monte_carlo'] else 'FAIL'}"
        f"  (actual_dd={monte_carlo.get('actual_max_dd')}, p95={monte_carlo.get('mc_dd_p95')})",
    ]
    status = "PASSED" if passed else "FAILED"
    summary = f"{status} — all hard gates\n" + "\n".join(gate_lines)

    # Collect failures as issues
    for gate, result in gate_results.items():
        if not result:
            all_issues.append(f"hard gate failed: {gate}")

    return {
        "passed": passed,
        "cost_normalized": cost_normalized,
        "wf_results": wf_results,
        "t_test": t_test,
        "monte_carlo": monte_carlo,
        "issues": all_issues,
        "summary": summary,
        "gate_results": gate_results,
    }
