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

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats


# ---------------------------------------------------------------------------
# Structured validation result
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    """Result for a single validation gate."""
    name: str
    passed: bool
    value: Any        # measured value (float, int, str, or None)
    threshold: Any    # threshold that was applied
    reason: str       # human-readable explanation


@dataclass
class ValidationResult:
    """
    Structured result returned by run_validation.

    gates        : list of GateResult, one per check applied
    passed       : True only if ALL applied gates passed
    issues       : list of failure strings (gates that failed + any errors)
    summary      : human-readable multi-line status string
    cost_normalized : trades DataFrame with profit_net column (excluded from metadata)
    wf_results   : raw output of compute_walk_forward
    t_test       : raw output of run_t_test
    monte_carlo  : raw output of run_monte_carlo
    gate_results : dict[gate_name, bool] for backwards compatibility
    """
    gates: List[GateResult] = field(default_factory=list)
    passed: bool = False
    issues: List[str] = field(default_factory=list)
    summary: str = ""
    cost_normalized: pd.DataFrame = field(default_factory=pd.DataFrame)
    wf_results: Dict[str, Any] = field(default_factory=dict)
    t_test: Dict[str, Any] = field(default_factory=dict)
    monte_carlo: Dict[str, Any] = field(default_factory=dict)
    gate_results: Dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe dict (excluding cost_normalized DataFrame)."""
        return {
            "passed": self.passed,
            "gates": [
                {
                    "name": g.name,
                    "passed": g.passed,
                    "value": g.value,
                    "threshold": g.threshold,
                    "reason": g.reason,
                }
                for g in self.gates
            ],
            "gate_results": self.gate_results,
            "wf_results": self.wf_results,
            "t_test": self.t_test,
            "monte_carlo": self.monte_carlo,
            "issues": self.issues,
            "summary": self.summary,
        }

    # ------------------------------------------------------------------
    # Dict-like access for backwards compatibility with code that treated
    # run_validation() as returning a plain dict.
    # ------------------------------------------------------------------

    def _as_full_dict(self) -> Dict[str, Any]:
        d = self.to_dict()
        d["cost_normalized"] = self.cost_normalized
        return d

    def get(self, key: str, default: Any = None) -> Any:
        return self._as_full_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._as_full_dict()[key]

    def __contains__(self, key: object) -> bool:
        return key in self._as_full_dict()

    def items(self):
        return self._as_full_dict().items()

    def keys(self):
        return self._as_full_dict().keys()

    def values(self):
        return self._as_full_dict().values()


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


def compute_walk_forward_rolling(
    trades: pd.DataFrame,
    profit_col: str = "profit_net",
    *,
    wf_config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Anchored expanding walk-forward across n_folds.

    Divides the OOS portion (1 - is_pct) into n_folds chunks.  Each fold uses
    all data up to that OOS chunk's start as IS and the chunk itself as OOS.

    Returns dict:
      n_folds, folds (list of per-fold dicts), oos_expectancy_across_folds,
      fold_sign_consistency (0.0–1.0), passed (bool), issues (list)
    """
    is_pct = float(wf_config.get("is_pct", 0.70))
    n_folds = int(wf_config.get("n_folds", 5))
    min_is_trades = int(wf_config.get("min_is_trades", 50))

    result: Dict[str, Any] = {
        "n_folds": n_folds,
        "folds": [],
        "oos_expectancy_across_folds": None,
        "fold_sign_consistency": None,
        "passed": False,
        "issues": [],
    }

    if trades is None or len(trades) == 0:
        result["issues"].append("no trades for rolling walk-forward")
        return result

    df = trades.copy()
    if "entry_time" in df.columns:
        df = df.sort_values("entry_time").reset_index(drop=True)

    if profit_col not in df.columns:
        result["issues"].append(f"profit column '{profit_col}' not found")
        return result

    n = len(df)
    initial_is_end = int(np.floor(n * is_pct))
    oos_pool = n - initial_is_end

    if oos_pool < n_folds:
        result["issues"].append(
            f"OOS pool ({oos_pool} trades) too small for {n_folds} folds"
        )
        return result

    fold_size = oos_pool // n_folds
    fold_results: List[Dict[str, Any]] = []

    for fold_idx in range(n_folds):
        oos_start = initial_is_end + fold_idx * fold_size
        oos_end = oos_start + fold_size if fold_idx < n_folds - 1 else n

        is_df = df.iloc[:oos_start]
        oos_df = df.iloc[oos_start:oos_end]

        if len(is_df) < min_is_trades or len(oos_df) < 1:
            continue

        is_returns = pd.to_numeric(is_df[profit_col], errors="coerce").dropna()
        oos_returns = pd.to_numeric(oos_df[profit_col], errors="coerce").dropna()

        is_exp = _compute_expectancy(is_returns)
        oos_exp = _compute_expectancy(oos_returns)

        fold_results.append({
            "fold": fold_idx + 1,
            "is_trades": len(is_df),
            "oos_trades": len(oos_df),
            "is_expectancy": is_exp,
            "oos_expectancy": oos_exp,
            "is_pf": _compute_profit_factor(is_returns),
            "oos_pf": _compute_profit_factor(oos_returns),
            "oos_positive": bool(oos_exp is not None and oos_exp > 0),
        })

    result["folds"] = fold_results

    if not fold_results:
        result["issues"].append("no folds had sufficient trades")
        return result

    n_positive = sum(1 for f in fold_results if f["oos_positive"])
    sign_consistency = n_positive / len(fold_results)
    result["fold_sign_consistency"] = _safe_float(sign_consistency)

    total_oos = sum(f["oos_trades"] for f in fold_results)
    if total_oos > 0:
        weighted_exp = sum(
            (f["oos_expectancy"] or 0.0) * f["oos_trades"]
            for f in fold_results
        ) / total_oos
        result["oos_expectancy_across_folds"] = _safe_float(weighted_exp)

    result["passed"] = bool(
        sign_consistency >= 0.6
        and result["oos_expectancy_across_folds"] is not None
        and result["oos_expectancy_across_folds"] > 0
    )

    return result


def run_t_test(
    trades: pd.DataFrame,
    profit_col: str = "profit_net",
    *,
    n_hypotheses_tested: int = 1,
    min_t_stat_abs: float = 0.0,
) -> Dict[str, Any]:
    """
    One-sample t-test: trade returns vs zero.

    Applies Bonferroni correction when n_hypotheses_tested > 1.
    corrected_alpha = 0.05 / n_hypotheses_tested
    required_t_stat = max(t_critical(corrected_alpha, df=n-1), min_t_stat_abs)

    Returns: t_stat, p_value, corrected_alpha, required_t_stat,
             n_hypotheses_tested, passed, n_valid
    """
    n_hyp = max(int(n_hypotheses_tested), 1)
    corrected_alpha = 0.05 / n_hyp

    result: Dict[str, Any] = {
        "t_stat": None,
        "p_value": None,
        "corrected_alpha": corrected_alpha,
        "required_t_stat": None,
        "n_hypotheses_tested": n_hyp,
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

    df_degrees = max(n_valid - 1, 1)
    required_t = float(scipy_stats.t.ppf(1.0 - corrected_alpha / 2.0, df=df_degrees))
    required_t = max(required_t, float(min_t_stat_abs))
    result["required_t_stat"] = _safe_float(required_t)

    try:
        t_stat, p_value = scipy_stats.ttest_1samp(returns.values, popmean=0.0)
        result["t_stat"] = _safe_float(t_stat)
        result["p_value"] = _safe_float(p_value)
        result["passed"] = bool(
            t_stat is not None
            and np.isfinite(float(t_stat))
            and float(t_stat) > 0
            and float(t_stat) >= required_t
        )
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


_SENSITIVITY_CLASS_ORDER = {"fragile": 0, "moderate": 1, "robust": 2}


def run_validation(
    trades: pd.DataFrame,
    *,
    wf_config: Optional[Dict[str, Any]] = None,
    cost_model: Optional[Dict[str, Any]] = None,
    n_mc_simulations: int = 1000,
    # --- Bonferroni / multiple-testing correction ---
    n_hypotheses_tested: int = 1,
    min_t_stat_abs: float = 0.0,
    # --- rolling walk-forward ---
    use_rolling_wf: bool = True,
    # --- optional extended gate inputs ---
    fold_sign_consistency: Optional[float] = None,
    fold_sign_consistency_min: float = 0.6,
    session_concentration_cap: Optional[float] = None,
    session_concentration_max: float = 0.70,
    regime_dispersion_min: Optional[int] = None,
    regime_dispersion_count: Optional[int] = None,
    sensitivity_class_min: Optional[str] = None,
    sensitivity_class_observed: Optional[str] = None,
    # --- DSR gate ---
    dsr_n_trials: Optional[int] = None,
    dsr_threshold: float = 0.95,
    dsr_annualization_factor: float = 1.0,
    # --- regime stratification gate ---
    regime_breakdown: Optional[Dict[str, Any]] = None,
    min_per_regime_expectancy: Optional[float] = None,
    # --- persistence ---
    db_path: Optional[str] = None,
    experiment_id: Optional[int] = None,
) -> ValidationResult:
    """
    Run all validation checks and return a structured ``ValidationResult``.

    Core gates (always applied):
      min_counts         IS/OOS minimum trade count check
      degradation        IS→OOS profit factor degradation ≤ threshold
      t_test             one-sample t-test on net returns vs zero
      monte_carlo        actual max-DD below 95th pct of shuffled DDs

    Optional extended gates (applied only when their input value is provided):
      fold_sign_consistency   fraction of CV folds with same-sign mean return;
                              pass if value ≥ fold_sign_consistency_min (default 0.6)
      session_concentration   fraction of trades in the single busiest session;
                              pass if value ≤ session_concentration_max (default 0.70)
      regime_dispersion       number of distinct regimes represented in trades;
                              pass if value ≥ regime_dispersion_min (default 2)
      sensitivity_class       parameter sensitivity classification (fragile/moderate/robust);
                              pass if observed class ≥ sensitivity_class_min rank
      dsr                     Deflated Sharpe Ratio (López de Prado 2018);
                              applied when dsr_n_trials is provided (or auto-read from DB).
                              pass if DSR ≥ dsr_threshold (default 0.95)
      min_per_regime_expectancy  applied when regime_breakdown dict is provided;
                              pass if every regime with ≥1 trade has avg_trade ≥ threshold

    Parameters
    ----------
    trades                       : trade DataFrame (requires profit column)
    wf_config                    : walk-forward config overrides
    cost_model                   : cost model overrides
    n_mc_simulations             : Monte Carlo simulation count
    n_hypotheses_tested          : total hypotheses tested so far (for Bonferroni correction).
                                   corrected_alpha = 0.05 / n_hypotheses_tested.
                                   Default 1 preserves prior p<0.05 behavior.
    min_t_stat_abs               : enforce a minimum |t| regardless of alpha correction.
                                   Pass 3.0 to require t > 3 as per the target framework.
    use_rolling_wf               : if True, run compute_walk_forward_rolling() and auto-wire
                                   fold_sign_consistency from the result (unless caller
                                   provides fold_sign_consistency explicitly).
    fold_sign_consistency        : measured value (0-1) from oos_stats (overrides rolling WF)
    fold_sign_consistency_min    : minimum passing value (default 0.6)
    session_concentration_cap    : measured max-session fraction (0-1)
    session_concentration_max    : maximum allowed (default 0.70)
    regime_dispersion_min        : minimum distinct regimes required (default 2)
    regime_dispersion_count      : measured count of distinct regimes
    sensitivity_class_min        : minimum class required (``"moderate"`` or ``"robust"``)
    sensitivity_class_observed   : observed class string (``"fragile"``/``"moderate"``/``"robust"``)

    Returns
    -------
    ValidationResult — use ``.to_dict()`` for JSON-safe metadata storage
    """
    resolved_wf_config = {**DEFAULT_WF_CONFIG, **(wf_config or {})}
    resolved_cost_model = {**DEFAULT_COST_MODEL, **(cost_model or {})}

    all_issues: List[str] = []
    gates: List[GateResult] = []

    # -----------------------------------------------------------------------
    # Guard: empty trades
    # -----------------------------------------------------------------------
    if trades is None or not isinstance(trades, pd.DataFrame) or len(trades) == 0:
        result = ValidationResult(
            passed=False,
            issues=["no trades provided"],
            summary="FAILED — no trades provided",
        )
        return result

    # -----------------------------------------------------------------------
    # Step 1: Apply cost model
    # -----------------------------------------------------------------------
    cost_normalized = apply_cost_model(trades, resolved_cost_model)

    # -----------------------------------------------------------------------
    # Step 2: Walk-forward (single split)
    # -----------------------------------------------------------------------
    wf_results = compute_walk_forward(
        cost_normalized,
        profit_col="profit_net",
        wf_config=resolved_wf_config,
    )
    if wf_results.get("issues"):
        all_issues.extend(wf_results["issues"])

    # -----------------------------------------------------------------------
    # Step 2b: Rolling walk-forward (multi-fold) — auto-wires fold_sign_consistency
    # -----------------------------------------------------------------------
    rolling_wf_results: Dict[str, Any] = {}
    if use_rolling_wf:
        rolling_wf_results = compute_walk_forward_rolling(
            cost_normalized,
            profit_col="profit_net",
            wf_config=resolved_wf_config,
        )
        if rolling_wf_results.get("issues"):
            all_issues.extend(rolling_wf_results["issues"])
        # Auto-wire fold_sign_consistency unless caller provided an explicit value
        if fold_sign_consistency is None and rolling_wf_results.get("fold_sign_consistency") is not None:
            fold_sign_consistency = rolling_wf_results["fold_sign_consistency"]

    # -----------------------------------------------------------------------
    # Step 3: T-test (Bonferroni-corrected)
    # -----------------------------------------------------------------------
    t_test = run_t_test(
        cost_normalized,
        profit_col="profit_net",
        n_hypotheses_tested=n_hypotheses_tested,
        min_t_stat_abs=min_t_stat_abs,
    )
    if t_test.get("issues"):
        all_issues.extend(t_test["issues"])

    # -----------------------------------------------------------------------
    # Step 4: Monte Carlo
    # -----------------------------------------------------------------------
    monte_carlo = run_monte_carlo(
        cost_normalized,
        profit_col="profit_net",
        n_simulations=n_mc_simulations,
    )
    if monte_carlo.get("issues"):
        all_issues.extend(monte_carlo["issues"])

    # -----------------------------------------------------------------------
    # Core gates
    # -----------------------------------------------------------------------
    n_is = wf_results.get("is_trades", 0)
    n_oos = wf_results.get("oos_trades", 0)
    min_is = int(resolved_wf_config.get("min_is_trades", 50))
    min_oos = int(resolved_wf_config.get("min_oos_trades", 20))
    gates.append(GateResult(
        name="min_counts",
        passed=bool(wf_results.get("passed_min_counts", False)),
        value={"is": n_is, "oos": n_oos},
        threshold={"min_is": min_is, "min_oos": min_oos},
        reason=f"IS={n_is}/{min_is}, OOS={n_oos}/{min_oos}",
    ))

    deg = wf_results.get("oos_degradation")
    deg_thresh = float(resolved_wf_config.get("degradation_threshold", 0.20))
    gates.append(GateResult(
        name="degradation",
        passed=bool(wf_results.get("passed_degradation", False)),
        value=deg,
        threshold=deg_thresh,
        reason=f"oos_degradation={deg} vs max={deg_thresh}",
    ))

    t_stat = t_test.get("t_stat")
    p_val = t_test.get("p_value")
    req_t = t_test.get("required_t_stat")
    corr_alpha = t_test.get("corrected_alpha", 0.05)
    gates.append(GateResult(
        name="t_test",
        passed=bool(t_test.get("passed", False)),
        value={"t_stat": t_stat, "p_value": p_val},
        threshold={"corrected_alpha": corr_alpha, "required_t_stat": req_t,
                   "n_hypotheses_tested": n_hypotheses_tested},
        reason=f"t={t_stat}, p={p_val}, required_t={req_t} (alpha={corr_alpha:.4f})",
    ))

    actual_dd = monte_carlo.get("actual_max_dd")
    mc_p95 = monte_carlo.get("mc_dd_p95")
    gates.append(GateResult(
        name="monte_carlo",
        passed=bool(monte_carlo.get("passed", False)),
        value={"actual_max_dd": actual_dd, "mc_dd_p95": mc_p95},
        threshold={"percentile": 95},
        reason=f"actual_dd={actual_dd} vs p95={mc_p95}",
    ))

    # -----------------------------------------------------------------------
    # Optional extended gates
    # -----------------------------------------------------------------------

    # Gate: fold sign consistency
    if fold_sign_consistency is not None:
        fsc_val = float(fold_sign_consistency)
        fsc_passed = fsc_val >= fold_sign_consistency_min
        gates.append(GateResult(
            name="fold_sign_consistency",
            passed=fsc_passed,
            value=fsc_val,
            threshold=fold_sign_consistency_min,
            reason=f"sign_consistency={fsc_val:.3f} vs min={fold_sign_consistency_min:.3f}",
        ))
        if not fsc_passed:
            all_issues.append(f"hard gate failed: fold_sign_consistency ({fsc_val:.3f} < {fold_sign_consistency_min:.3f})")

    # Gate: session concentration
    if session_concentration_cap is not None:
        sc_val = float(session_concentration_cap)
        sc_passed = sc_val <= session_concentration_max
        gates.append(GateResult(
            name="session_concentration",
            passed=sc_passed,
            value=sc_val,
            threshold=session_concentration_max,
            reason=f"max_session_frac={sc_val:.3f} vs cap={session_concentration_max:.3f}",
        ))
        if not sc_passed:
            all_issues.append(f"hard gate failed: session_concentration ({sc_val:.3f} > {session_concentration_max:.3f})")

    # Gate: regime dispersion
    if regime_dispersion_count is not None:
        disp_min = int(regime_dispersion_min) if regime_dispersion_min is not None else 2
        disp_val = int(regime_dispersion_count)
        disp_passed = disp_val >= disp_min
        gates.append(GateResult(
            name="regime_dispersion",
            passed=disp_passed,
            value=disp_val,
            threshold=disp_min,
            reason=f"distinct_regimes={disp_val} vs min={disp_min}",
        ))
        if not disp_passed:
            all_issues.append(f"hard gate failed: regime_dispersion ({disp_val} < {disp_min})")

    # Gate: parameter sensitivity class
    if sensitivity_class_observed is not None and sensitivity_class_min is not None:
        obs_rank = _SENSITIVITY_CLASS_ORDER.get(str(sensitivity_class_observed).lower(), -1)
        min_rank = _SENSITIVITY_CLASS_ORDER.get(str(sensitivity_class_min).lower(), 0)
        sens_passed = obs_rank >= min_rank
        gates.append(GateResult(
            name="sensitivity_class",
            passed=sens_passed,
            value=sensitivity_class_observed,
            threshold=sensitivity_class_min,
            reason=f"sensitivity={sensitivity_class_observed} vs min={sensitivity_class_min}",
        ))
        if not sens_passed:
            all_issues.append(f"hard gate failed: sensitivity_class ({sensitivity_class_observed} < {sensitivity_class_min})")

    # Gate: minimum per-regime expectancy
    # Applied when regime_breakdown is provided and min_per_regime_expectancy is set.
    if regime_breakdown and min_per_regime_expectancy is not None:
        min_exp = float(min_per_regime_expectancy)
        failing_regimes = []
        for label, rdata in regime_breakdown.items():
            if not isinstance(rdata, dict):
                continue
            n = rdata.get("n_trades", 0)
            avg = rdata.get("avg_trade")
            if n > 0 and (avg is None or float(avg) < min_exp):
                failing_regimes.append(label)
        mre_passed = len(failing_regimes) == 0
        mre_value = {k: v.get("avg_trade") for k, v in regime_breakdown.items() if isinstance(v, dict)}
        gates.append(GateResult(
            name="min_per_regime_expectancy",
            passed=mre_passed,
            value=mre_value,
            threshold=min_exp,
            reason=(
                f"all regimes ≥ {min_exp}" if mre_passed
                else f"regimes below threshold: {failing_regimes}"
            ),
        ))
        if not mre_passed:
            all_issues.append(
                f"hard gate failed: min_per_regime_expectancy "
                f"(failing regimes: {failing_regimes})"
            )

    # Gate: Deflated Sharpe Ratio
    # Enabled when dsr_n_trials is provided, or auto-read from DB registry.
    _dsr_n_trials = dsr_n_trials
    if _dsr_n_trials is None and db_path is not None:
        try:
            from ta_foundation.persistence.db import ExperimentRegistry
            _dsr_n_trials = ExperimentRegistry(db_path).count_experiments()
        except Exception:
            pass

    if _dsr_n_trials is not None:
        from ta_foundation.analysis.statistics.dsr import compute_dsr_gate
        dsr_result = compute_dsr_gate(
            cost_normalized,
            n_trials=_dsr_n_trials,
            profit_col="profit_net",
            annualization_factor=dsr_annualization_factor,
            dsr_threshold=dsr_threshold,
        )
        dsr_passed = bool(dsr_result.get("passed", False))
        dsr_val = dsr_result.get("dsr")
        gates.append(GateResult(
            name="dsr",
            passed=dsr_passed,
            value=dsr_val,
            threshold=dsr_threshold,
            reason=dsr_result.get("reason", f"DSR={dsr_val} vs threshold={dsr_threshold}"),
        ))
        if not dsr_passed:
            all_issues.append(f"hard gate failed: dsr ({dsr_val} < {dsr_threshold})")

    # -----------------------------------------------------------------------
    # Composite pass/fail
    # -----------------------------------------------------------------------
    gate_results: Dict[str, bool] = {g.name: g.passed for g in gates}
    passed = all(g.passed for g in gates)

    # Collect core gate failures
    core_gates = {"min_counts", "degradation", "t_test", "monte_carlo"}
    for g in gates:
        if g.name in core_gates and not g.passed:
            all_issues.append(f"hard gate failed: {g.name}")

    # Human-readable summary
    gate_lines = [
        f"  {g.name:<28} {'PASS' if g.passed else 'FAIL'}  ({g.reason})"
        for g in gates
    ]
    status = "PASSED" if passed else "FAILED"
    summary = f"{status} — {len(gates)} gates\n" + "\n".join(gate_lines)

    vr = ValidationResult(
        gates=gates,
        passed=passed,
        issues=all_issues,
        summary=summary,
        cost_normalized=cost_normalized,
        wf_results={**wf_results, "rolling": rolling_wf_results},
        t_test=t_test,
        monte_carlo=monte_carlo,
        gate_results=gate_results,
    )

    if db_path is not None:
        try:
            from ta_foundation.persistence.db import ExperimentRegistry
            reg = ExperimentRegistry(db_path)
            reg.record_validation(
                experiment_id=experiment_id,
                validation_result=vr,
                n_hypotheses_at_time=n_hypotheses_tested,
            )
        except Exception:
            pass  # DB write is best-effort; never block a validation run

    return vr
