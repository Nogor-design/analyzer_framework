from __future__ import annotations

"""
Deflated Sharpe Ratio (DSR)
===========================
Implementation of López de Prado (2018) Deflated Sharpe Ratio.

The DSR deflates the observed Sharpe Ratio by accounting for:
  - Number of trials tested (selection bias / multiple-testing)
  - Length of the return series (estimation error)
  - Non-normality of returns (skewness, excess kurtosis)

Formula
-------
    DSR = Prob[ SR* <= SR_hat ]

where SR_hat is the observed Sharpe Ratio and SR* is the expected maximum
Sharpe Ratio across N independent trials with T observations:

    E[max SR] ≈ sqrt(V[SR]) * (
        (1 - euler_gamma) * z_inv(1 - 1/N) + euler_gamma * z_inv(1 - 1/(N*e))
    )

    V[SR] = (1/T) * (1 + (SR_hat^2/2) * (kurtosis - 1) - SR_hat * skewness)

    DSR = Phi(
        (SR_hat - E[max SR]) /
        sqrt(V[SR])
    )

where Phi is the standard normal CDF and euler_gamma ≈ 0.5772.

Reference
---------
López de Prado, M. (2018). Advances in Financial Machine Learning.
Chapter 8: Testing Set Overfitting.

Public API
----------
    compute_dsr(returns, n_trials, annualization_factor) -> DsrResult
    compute_dsr_gate(returns, n_trials, ...) -> dict  (for run_validation)
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

_EULER_GAMMA = 0.5772156649015328


@dataclass
class DsrResult:
    """Output of compute_dsr."""
    dsr: Optional[float]          # Deflated Sharpe Ratio (probability, 0-1)
    sr_hat: Optional[float]       # Observed annualised Sharpe Ratio
    sr_star: Optional[float]      # Expected maximum SR across N trials
    sr_variance: Optional[float]  # Variance of SR estimate
    skewness: Optional[float]
    excess_kurtosis: Optional[float]
    n_trials: int
    n_obs: int
    passed: bool                  # dsr >= dsr_threshold
    dsr_threshold: float
    issues: list

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dsr": _safe_float(self.dsr),
            "sr_hat": _safe_float(self.sr_hat),
            "sr_star": _safe_float(self.sr_star),
            "sr_variance": _safe_float(self.sr_variance),
            "skewness": _safe_float(self.skewness),
            "excess_kurtosis": _safe_float(self.excess_kurtosis),
            "n_trials": self.n_trials,
            "n_obs": self.n_obs,
            "passed": self.passed,
            "dsr_threshold": self.dsr_threshold,
            "issues": self.issues,
        }


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return None if not math.isfinite(f) else f
    except Exception:
        return None


def _expected_max_sr(n_trials: int, sr_variance: float) -> float:
    """
    Expected maximum Sharpe Ratio across n_trials independent trials.

    Uses the Extreme Value Theory approximation from López de Prado (2018):

        E[max SR] = sqrt(V[SR]) * (
            (1 - γ) * Φ⁻¹(1 - 1/N) + γ * Φ⁻¹(1 - 1/(N·e))
        )

    where γ is the Euler-Mascheroni constant.
    """
    if n_trials <= 0 or sr_variance <= 0:
        return 0.0

    std_sr = math.sqrt(sr_variance)
    e = math.e

    # Guard: avoid log(0) when n_trials = 1
    p1 = max(1.0 - 1.0 / n_trials, 1e-12)
    p2 = max(1.0 - 1.0 / (n_trials * e), 1e-12)

    z1 = float(scipy_stats.norm.ppf(p1))
    z2 = float(scipy_stats.norm.ppf(p2))

    return std_sr * ((1.0 - _EULER_GAMMA) * z1 + _EULER_GAMMA * z2)


def _sr_variance(sr_hat: float, n_obs: int, skewness: float, excess_kurtosis: float) -> float:
    """
    Variance of the Sharpe Ratio estimator (non-normal correction).

        V[SR] = (1/T) * (1 + (SR²/2)(κ-1) - SR·γ₃)

    where κ is excess kurtosis and γ₃ is skewness.
    Clamped to a small positive floor to avoid division by zero.
    """
    if n_obs <= 0:
        return 1.0
    t = float(n_obs)
    v = (1.0 / t) * (
        1.0
        + (sr_hat ** 2 / 2.0) * (excess_kurtosis - 1.0)
        - sr_hat * skewness
    )
    return max(v, 1e-12)


def compute_dsr(
    returns: pd.Series,
    n_trials: int,
    *,
    annualization_factor: float = 1.0,
    dsr_threshold: float = 0.95,
) -> DsrResult:
    """
    Compute the Deflated Sharpe Ratio.

    Parameters
    ----------
    returns
        Per-trade P&L series (not annualised).
    n_trials
        Total number of hypotheses tested (from DB or caller).
        Set to 1 for a single hypothesis — DSR collapses to a standard test.
    annualization_factor
        sqrt(periods_per_year) for annualising the SR.
        For trade-level P&L without a fixed holding period, pass 1.0 (no annualisation).
    dsr_threshold
        Minimum DSR probability to pass (default 0.95).

    Returns
    -------
    DsrResult
    """
    issues: list = []
    n_trials = max(int(n_trials), 1)

    valid = pd.to_numeric(returns, errors="coerce").dropna()
    n_obs = int(len(valid))

    if n_obs < 2:
        return DsrResult(
            dsr=None, sr_hat=None, sr_star=None, sr_variance=None,
            skewness=None, excess_kurtosis=None,
            n_trials=n_trials, n_obs=n_obs, passed=False,
            dsr_threshold=dsr_threshold,
            issues=[f"insufficient observations: {n_obs}"],
        )

    arr = valid.values.astype(float)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))

    if std == 0:
        issues.append("zero standard deviation — SR undefined")
        return DsrResult(
            dsr=None, sr_hat=None, sr_star=None, sr_variance=None,
            skewness=None, excess_kurtosis=None,
            n_trials=n_trials, n_obs=n_obs, passed=False,
            dsr_threshold=dsr_threshold,
            issues=issues,
        )

    sr_hat = float((mean / std) * annualization_factor)
    skewness = float(scipy_stats.skew(arr))
    excess_kurtosis = float(scipy_stats.kurtosis(arr, fisher=True))  # Fisher = excess

    var_sr = _sr_variance(sr_hat, n_obs, skewness, excess_kurtosis)
    sr_star = _expected_max_sr(n_trials, var_sr)

    # DSR: probability that the best observed SR is genuine
    if var_sr <= 0:
        issues.append("non-positive SR variance — DSR undefined")
        return DsrResult(
            dsr=None, sr_hat=sr_hat, sr_star=sr_star, sr_variance=var_sr,
            skewness=skewness, excess_kurtosis=excess_kurtosis,
            n_trials=n_trials, n_obs=n_obs, passed=False,
            dsr_threshold=dsr_threshold,
            issues=issues,
        )

    z = (sr_hat - sr_star) / math.sqrt(var_sr)
    dsr = float(scipy_stats.norm.cdf(z))

    passed = math.isfinite(dsr) and dsr >= dsr_threshold

    return DsrResult(
        dsr=_safe_float(dsr),
        sr_hat=_safe_float(sr_hat),
        sr_star=_safe_float(sr_star),
        sr_variance=_safe_float(var_sr),
        skewness=_safe_float(skewness),
        excess_kurtosis=_safe_float(excess_kurtosis),
        n_trials=n_trials,
        n_obs=n_obs,
        passed=passed,
        dsr_threshold=dsr_threshold,
        issues=issues,
    )


def compute_dsr_gate(
    trades: pd.DataFrame,
    n_trials: int,
    profit_col: str = "profit_net",
    *,
    annualization_factor: float = 1.0,
    dsr_threshold: float = 0.95,
) -> Dict[str, Any]:
    """
    Thin wrapper for use inside run_validation().

    Returns a dict matching the GateResult contract:
      passed, value (dsr), threshold (dsr_threshold), reason, plus full DsrResult fields.
    """
    if trades is None or len(trades) == 0 or profit_col not in trades.columns:
        return {
            "passed": False,
            "dsr": None,
            "issues": [f"no data or missing column '{profit_col}'"],
        }

    returns = pd.to_numeric(trades[profit_col], errors="coerce").dropna()
    result = compute_dsr(
        returns,
        n_trials=n_trials,
        annualization_factor=annualization_factor,
        dsr_threshold=dsr_threshold,
    )
    out = result.to_dict()
    out["reason"] = (
        f"DSR={result.dsr:.4f} vs threshold={dsr_threshold:.2f} "
        f"(SR_hat={result.sr_hat:.3f}, SR*={result.sr_star:.3f}, N={n_trials})"
        if result.dsr is not None else "DSR could not be computed"
    )
    return out
