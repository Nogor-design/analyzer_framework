from __future__ import annotations

"""
Strategy Ranking
================
Phase 7 — Weighted composite score with weight-sensitivity check.

Reads from pkg.metadata["derived"]["strategy_discovery"] for each run and
produces a cross-run ranked table.

Scoring formula (from design doc section 5):
  quality = 0.30*RiskAdj + 0.25*Stability + 0.20*Robustness + 0.15*RegimeFit + 0.10*ExecutionFit
  deploy  = 0.45*Automatability + 0.25*Simplicity + 0.20*OperationalRisk + 0.10*MonitoringEase
  final   = 0.75*quality + 0.25*deploy - overfit_penalty - complexity_penalty

Weight sensitivity: perturb each quality/deploy weight by ±20%, recompute rankings.
Flag as ranking_fragile if any top-5 run changes position.

Entry point: run_ranking(packages) → dict (JSON-safe)
Attaches ranked list to each pkg.metadata["derived"]["strategy_discovery"]["ranking"]
and stores cross-run summary in the last package as ["ranking"]["cross_run"].
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Default scoring weights
# ---------------------------------------------------------------------------

QUALITY_WEIGHTS: Dict[str, float] = {
    "risk_adj":      0.30,
    "stability":     0.25,
    "robustness":    0.20,
    "regime_fit":    0.15,
    "execution_fit": 0.10,
}

DEPLOY_WEIGHTS: Dict[str, float] = {
    "automatability":   0.45,
    "simplicity":       0.25,
    "operational_risk": 0.20,
    "monitoring_ease":  0.10,
}

FINAL_WEIGHTS: Dict[str, float] = {
    "quality": 0.75,
    "deploy":  0.25,
}


# ---------------------------------------------------------------------------
# Hard-gate config
# ---------------------------------------------------------------------------
#
# Hard gates are exclusion filters applied before scoring. Any candidate that
# fails one or more enabled gates is routed to the ``rejected`` list with a
# structured reason; only survivors enter the ranked leaderboard. Each gate
# is opt-in by threshold — a value of ``None`` disables that gate.
#
# Defaults are intentionally gentle (statistical sanity, not fund-grade) so
# small backtests still produce a leaderboard. Real fund-grade reviewers
# should tighten these in YAML — for example:
#
#   strategy_discovery:
#     ranking_gates:
#       enabled: true
#       min_oos_trades: 150
#       min_profit_factor: 1.2
#       min_sharpe: 1.0
#       max_drawdown_pct: 25.0
#       require_sensitivity_class: "moderate"
#       require_validation_passed: true
#       require_permutation_passed: true
#       max_permutation_p: 0.05

DEFAULT_RANKING_GATES: Dict[str, Any] = {
    "enabled": True,
    "min_oos_trades": 30,         # statistical-sanity floor; fund-grade ≈ 150
    "min_profit_factor": 1.0,     # not unprofitable; fund-grade ≈ 1.2–1.5
    "min_sharpe": None,           # null = skip; fund-grade ≈ 1.0
    "max_drawdown_pct": None,     # null = skip; fund-grade ≈ 25.0
    "require_sensitivity_class": None,  # null | "moderate" | "robust"
    "require_validation_passed": False,
    "require_slippage_stress_passed": False,  # T8 — opt-in gate
    "require_permutation_passed": False,      # T9 — opt-in null-test gate
    "max_permutation_p": 0.05,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    if v is None:
        return default
    try:
        f = float(v)
        return default if not np.isfinite(f) else f
    except Exception:
        return default


def _extract_permutation_p_values(sd: Dict[str, Any]) -> Dict[str, float]:
    """Return metric -> empirical p-value from a permutation-test block."""
    block = sd.get("permutation_tests") or sd.get("permutation_null") or {}
    if not isinstance(block, dict):
        return {}

    p_values: Dict[str, float] = {}
    direct = _safe_float(block.get("p_value"))
    if direct is not None:
        metric = str(block.get("metric") or "permutation")
        p_values[metric] = direct

    for metric, payload in block.items():
        if not isinstance(payload, dict):
            continue
        p_val = _safe_float(payload.get("p_value"))
        if p_val is not None:
            p_values[str(metric)] = p_val

    return p_values


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _norm(v: Optional[float], lo: float, hi: float) -> float:
    """Normalize v from [lo, hi] → [0, 100]."""
    if v is None:
        return 50.0  # neutral default
    if hi == lo:
        return 50.0
    return _clamp((v - lo) / (hi - lo) * 100.0)


def _weighted(components: Dict[str, float], weights: Dict[str, float]) -> float:
    """Weighted sum of components using given weights. Components are 0–100."""
    total_w = sum(weights.values())
    if total_w == 0:
        return 0.0
    score = sum(components.get(k, 50.0) * w for k, w in weights.items())
    return _clamp(score / total_w)


# ---------------------------------------------------------------------------
# Component score calculators
# ---------------------------------------------------------------------------

def _score_risk_adj(evaluation: Dict[str, Any], risk_metrics: Optional[Dict[str, Any]] = None) -> float:
    """
    RiskAdj — normalized expectancy, PF, avg_trade / max_drawdown.
    When risk_metrics are available, blends in Sharpe, Sortino, and Calmar ratios
    which provide more complete risk-adjusted scoring.
    """
    pf       = _safe_float(evaluation.get("profit_factor"), 1.0)
    win_rate = _safe_float(evaluation.get("win_rate"), 0.5)
    avg_tr   = _safe_float(evaluation.get("avg_trade"), 0.0)
    max_dd   = _safe_float(evaluation.get("max_drawdown"), 1.0) or 1.0

    # PF: 1.0 → 0, 2.5 → 100
    pf_score  = _norm(pf, 1.0, 2.5)
    # Win rate: 0.40 → 0, 0.70 → 100
    wr_score  = _norm(win_rate, 0.40, 0.70)
    # Avg trade / max DD ratio (higher = healthier)
    ratio = (avg_tr / abs(max_dd)) if avg_tr is not None and max_dd else 0.0
    ratio_score = _norm(ratio, 0.0, 0.20)

    base_score = 0.50 * pf_score + 0.30 * wr_score + 0.20 * ratio_score

    # If risk_metrics are available, blend in Sharpe / Sortino / Calmar
    rm = risk_metrics or {}
    sharpe  = _safe_float(rm.get("sharpe_ratio"))
    sortino = _safe_float(rm.get("sortino_ratio"))
    calmar  = _safe_float(rm.get("calmar_ratio"))

    ra_scores = []
    if sharpe is not None:
        # Sharpe: < 0 → 0, 0.5 → ~33, 1.0 → 67, 2.0 → 100
        ra_scores.append(_norm(sharpe, 0.0, 2.0))
    if sortino is not None:
        # Sortino: < 0 → 0, 1.0 → 50, 2.5 → 100
        ra_scores.append(_norm(sortino, 0.0, 2.5))
    if calmar is not None:
        # Calmar: < 0 → 0, 0.5 → 50, 1.0 → 100
        ra_scores.append(_norm(calmar, 0.0, 1.0))

    if ra_scores:
        rm_score = float(np.mean(ra_scores))
        # Blend: 60% base (PF/WR/ratio), 40% risk-adjusted ratio scores
        return _clamp(0.60 * base_score + 0.40 * rm_score)

    return _clamp(base_score)


def _score_stability(validation: Dict[str, Any]) -> float:
    """
    Stability — OOS retention, WF fold consistency, and per-fold variance.

    Components (fold variance added in T7):
      retention_score (40%)  IS→OOS PF degradation (0% drop = 100, 20% = 0)
      oos_pf_score    (25%)  raw OOS PF (1.0 = 0, 2.0 = 100)
      ratio_score     (15%)  OOS_PF / IS_PF (1.0 = best)
      fold_var_score  (20%)  inverse of per-fold OOS expectancy std normalised
                             by the across-fold mean. Lower variance = higher
                             score. Falls back to a neutral 50 when fewer
                             than 2 folds were retained or expectancy is zero.
    """
    wf = validation.get("wf_results") or {}
    oos_deg = _safe_float(wf.get("oos_degradation"), 0.15)
    is_pf   = _safe_float(wf.get("is_pf"), 1.0)
    oos_pf  = _safe_float(wf.get("oos_pf"), 1.0)

    retention_score = _norm(-(oos_deg or 0.0), -0.20, 0.0)
    oos_pf_score = _norm(oos_pf, 1.0, 2.0)
    if is_pf and oos_pf and is_pf > 0:
        ratio = min(1.0, oos_pf / is_pf)
        ratio_score = _clamp(ratio * 100.0)
    else:
        ratio_score = 50.0

    # Fold variance score — read from rolling WF block (T7).
    fold_var_score = 50.0
    rolling = wf.get("rolling") or {}
    exp_std = _safe_float(rolling.get("oos_expectancy_std"), None)
    exp_mean = _safe_float(rolling.get("oos_expectancy_across_folds"), None)
    if exp_std is not None and exp_mean is not None and abs(exp_mean) > 1e-9:
        # Coefficient-of-variation-style penalty. CV ≤ 0.5 → 100, CV ≥ 2.0 → 0.
        cv = abs(exp_std / exp_mean)
        fold_var_score = _norm(-cv, -2.0, -0.5)

    return _clamp(
        0.40 * retention_score
        + 0.25 * oos_pf_score
        + 0.15 * ratio_score
        + 0.20 * fold_var_score
    )


def _score_robustness(validation: Dict[str, Any], exit_disc: Dict[str, Any]) -> float:
    """
    Robustness — Monte Carlo DD percentile rank, t-test p-value, exit quality.
    Low MC percentile = actual DD is not unusual = more robust.
    Low p-value = statistically significant edge.
    """
    mc   = validation.get("monte_carlo") or {}
    tt   = validation.get("t_test") or {}

    mc_pct = _safe_float(mc.get("dd_percentile_rank"), 50.0)  # lower = more robust
    p_val  = _safe_float(tt.get("p_value"), 0.10)

    # MC: actual DD at 95th pct → 0, at 0th pct → 100
    mc_score = _norm(100.0 - (mc_pct or 50.0), 5.0, 100.0)

    # p-value: 0.01 → 100, 0.05 → 60, 0.10+ → 0
    if p_val is not None:
        if p_val <= 0.01:
            p_score = 100.0
        elif p_val <= 0.05:
            p_score = _norm(0.05 - p_val, 0.0, 0.04) * 0.60 + 40.0
        else:
            p_score = 0.0
    else:
        p_score = 50.0

    # Exit quality proxy from exit discovery best combo
    ed_pf = None
    ranking = (exit_disc or {}).get("policy_ranking")
    if ranking and len(ranking) > 0:
        ed_pf = _safe_float(ranking[0].get("profit_factor"))
    exit_score = _norm(ed_pf, 1.0, 2.0) if ed_pf is not None else 50.0

    return _clamp(0.50 * mc_score + 0.25 * p_score + 0.25 * exit_score)


def _score_regime_fit(evaluation: Dict[str, Any]) -> float:
    """
    RegimeFit — how consistently the strategy performs across regimes.
    Penalizes strategies that only work in one regime and collapse in others.
    Coverage (how many regimes are profitable) + consistency (low PF variance).
    """
    by_regime = evaluation.get("by_regime") or {}
    if not by_regime:
        return 50.0  # neutral when no regime data

    pfs = []
    for reg_data in by_regime.values():
        if isinstance(reg_data, dict):
            pf = _safe_float(reg_data.get("profit_factor"))
            if pf is not None:
                pfs.append(pf)

    if not pfs:
        return 50.0

    n_winning = sum(1 for p in pfs if p >= 1.2)
    coverage = (n_winning / len(pfs)) * 100.0

    # Low variance across regimes = more consistent
    if len(pfs) > 1:
        pf_std = float(np.std(pfs))
        # std of 0 = 100, std of 0.5 = 0
        consistency = _norm(-pf_std, -0.5, 0.0)
    else:
        consistency = 50.0

    return _clamp(0.60 * coverage + 0.40 * consistency)


def _score_execution_fit(evaluation: Dict[str, Any]) -> float:
    """
    ExecutionFit — trade frequency, holding time practicality, session distribution.
    Ideal: 5–60 min holds, > 50 trades, reasonable session spread.
    """
    n_trades = _safe_float(evaluation.get("n_trades"), 0.0) or 0.0
    avg_dur  = _safe_float(evaluation.get("avg_duration_min"), 0.0)

    # Trade count: 50 = adequate, 200+ = ideal
    count_score = _norm(n_trades, 20.0, 200.0)

    # Holding time: 5–60 min ideal for futures day trading
    if avg_dur is not None:
        if 5.0 <= avg_dur <= 60.0:
            dur_score = 100.0
        elif avg_dur < 5.0:
            dur_score = _norm(avg_dur, 0.0, 5.0)
        else:
            dur_score = _norm(120.0 - avg_dur, 0.0, 60.0)
    else:
        dur_score = 50.0

    # Session spread: penalize if all trades concentrated in one session
    by_session = evaluation.get("by_session") or {}
    if len(by_session) >= 3:
        session_score = 80.0
    elif len(by_session) == 2:
        session_score = 60.0
    else:
        session_score = 40.0

    return _clamp(0.40 * count_score + 0.35 * dur_score + 0.25 * session_score)


def _score_automatability(evaluation: Dict[str, Any], classification: Optional[str] = None) -> float:
    """
    Automatability — automated > hybrid > semi_discretionary.
    In absence of classification data (Phase 3 not yet run), use neutral.
    """
    if classification == "automated":
        return 100.0
    if classification == "hybrid":
        return 65.0
    if classification == "semi_discretionary":
        return 25.0
    return 65.0  # assume hybrid-level when unknown (NT8 backtest implies automatable)


def _score_simplicity(evaluation: Dict[str, Any], exit_disc: Dict[str, Any]) -> float:
    """
    Simplicity — proxy from exit discovery combo count and evaluation trade count.
    Fewer exit families with clear winner = simpler.
    """
    sweep = (exit_disc or {}).get("sweep_summary") or {}
    n_combos = _safe_float(sweep.get("n_combos"), 0.0) or 0.0
    best_net = _safe_float(sweep.get("best_net_ticks"))
    ranking = (exit_disc or {}).get("policy_ranking") or []

    if not ranking:
        return 50.0

    # Clear winner: top combo significantly outperforms 2nd
    if len(ranking) >= 2:
        top_net  = _safe_float(ranking[0].get("net_ticks"), 0.0)
        next_net = _safe_float(ranking[1].get("net_ticks"), 0.0)
        if top_net and next_net and top_net > 0:
            clarity = _clamp((top_net - (next_net or 0)) / max(abs(top_net), 1) * 100.0)
        else:
            clarity = 50.0
    else:
        clarity = 75.0

    return _clamp(0.60 * clarity + 0.40 * 60.0)


def _score_operational_risk(evaluation: Dict[str, Any]) -> float:
    """
    OperationalRisk — session concentration and regime sensitivity.
    High variance = higher operational risk.
    Note: returns are inverted — higher raw score = lower risk = better.
    """
    by_session = evaluation.get("by_session") or {}
    session_pfs = []
    for s_data in by_session.values():
        if isinstance(s_data, dict):
            pf = _safe_float(s_data.get("profit_factor"))
            if pf is not None:
                session_pfs.append(pf)

    if len(session_pfs) > 1:
        pf_std = float(np.std(session_pfs))
        # std of 0 = 100 (low risk), std of 0.5+ = 0 (high risk)
        variance_score = _norm(-pf_std, -0.5, 0.0)
    else:
        variance_score = 60.0

    # avg_duration: very short holds = more execution risk (slippage matters more)
    avg_dur = _safe_float(evaluation.get("avg_duration_min"), 15.0) or 15.0
    if avg_dur < 3.0:
        dur_risk = 20.0   # scalp: high operational risk
    elif avg_dur < 8.0:
        dur_risk = 60.0
    else:
        dur_risk = 85.0

    return _clamp(0.60 * variance_score + 0.40 * dur_risk)


def _score_monitoring_ease(validation: Dict[str, Any], evaluation: Dict[str, Any]) -> float:
    """
    MonitoringEase — clarity of failure conditions. Proxy:
    high MC dd_percentile_rank = hard to tell if DD is normal → harder to monitor.
    Stable WF = easy to set alerting thresholds.
    """
    mc = validation.get("monte_carlo") or {}
    mc_pct = _safe_float(mc.get("dd_percentile_rank"), 50.0)

    # Low MC percentile = unusual DDs are easy to spot
    mc_score = _norm(100.0 - (mc_pct or 50.0), 0.0, 100.0)

    wf = validation.get("wf_results") or {}
    oos_deg = _safe_float(wf.get("oos_degradation"), 0.15)
    # Low degradation = more predictable behaviour
    deg_score = _norm(-(oos_deg or 0.0), -0.30, 0.0)

    return _clamp(0.50 * mc_score + 0.50 * deg_score)


# ---------------------------------------------------------------------------
# Overfit and complexity penalties
# ---------------------------------------------------------------------------

def _overfit_penalty(validation: Dict[str, Any]) -> float:
    """
    Additional penalty applied directly to final score.
    Conditions:
    - Not passed validation: –25
    - MC dd_percentile_rank > 80: –10
    - oos_degradation > 0.30: –5
    """
    penalty = 0.0

    if not validation.get("passed", False):
        penalty += 25.0

    mc = validation.get("monte_carlo") or {}
    mc_pct = _safe_float(mc.get("dd_percentile_rank"), 50.0)
    if mc_pct is not None and mc_pct > 80.0:
        penalty += 10.0

    wf = validation.get("wf_results") or {}
    oos_deg = _safe_float(wf.get("oos_degradation"), 0.0)
    if oos_deg is not None and oos_deg > 0.30:
        penalty += 5.0

    return penalty


def _complexity_penalty(evaluation: Dict[str, Any]) -> float:
    """
    Small penalty for very few trades (low statistical power).
    """
    n_trades = _safe_float(evaluation.get("n_trades"), 0.0) or 0.0
    if n_trades < 30:
        return 10.0
    if n_trades < 50:
        return 5.0
    return 0.0


# ---------------------------------------------------------------------------
# Per-run component and score computation
# ---------------------------------------------------------------------------

def evaluate_hard_gates(
    sd: Dict[str, Any],
    gates_config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Apply hard exclusion gates to a single strategy_discovery dict.

    Returns a list of failure dicts; an empty list means all enabled gates
    passed. Each failure dict has keys ``gate`` / ``value`` / ``threshold``
    / ``reason`` so reviewers can see exactly why a candidate was rejected.

    Each gate is opt-in by threshold — a ``None`` value in the config skips
    that gate entirely. ``enabled: false`` skips all gates.
    """
    cfg = {**DEFAULT_RANKING_GATES, **(gates_config or {})}
    if not cfg.get("enabled", True):
        return []

    failures: List[Dict[str, Any]] = []
    eval_for_gates = _select_ranking_evaluation(sd) or {}

    # Gate: minimum trade count on the eval block ranking will score against.
    min_trades = cfg.get("min_oos_trades")
    if min_trades is not None:
        n_trades = eval_for_gates.get("n_trades") or 0
        if n_trades < int(min_trades):
            failures.append({
                "gate": "min_oos_trades",
                "value": n_trades,
                "threshold": int(min_trades),
                "reason": f"OOS trade count {n_trades} below minimum {int(min_trades)}",
            })

    # Gate: minimum profit factor.
    min_pf = cfg.get("min_profit_factor")
    if min_pf is not None:
        pf = _safe_float(eval_for_gates.get("profit_factor"))
        if pf is None or pf < float(min_pf):
            failures.append({
                "gate": "min_profit_factor",
                "value": pf,
                "threshold": float(min_pf),
                "reason": (
                    "profit factor unavailable" if pf is None
                    else f"profit factor {pf:.3f} below minimum {float(min_pf):.3f}"
                ),
            })

    # Gate: minimum Sharpe (from risk_metrics).
    min_sharpe = cfg.get("min_sharpe")
    if min_sharpe is not None:
        sharpe = _safe_float((sd.get("risk_metrics") or {}).get("sharpe_ratio"))
        if sharpe is None or sharpe < float(min_sharpe):
            failures.append({
                "gate": "min_sharpe",
                "value": sharpe,
                "threshold": float(min_sharpe),
                "reason": (
                    "sharpe unavailable" if sharpe is None
                    else f"sharpe {sharpe:.3f} below minimum {float(min_sharpe):.3f}"
                ),
            })

    # Gate: maximum drawdown ceiling (percent of peak equity).
    max_dd = cfg.get("max_drawdown_pct")
    if max_dd is not None:
        dd_pct = _safe_float(
            (sd.get("drawdown_analysis") or {}).get("overall", {}).get("max_drawdown_pct")
        )
        if dd_pct is not None and dd_pct > float(max_dd):
            failures.append({
                "gate": "max_drawdown_pct",
                "value": dd_pct,
                "threshold": float(max_dd),
                "reason": f"max drawdown {dd_pct:.2f}% exceeds ceiling {float(max_dd):.2f}%",
            })

    # Gate: parameter-sensitivity classification. Reads parameter_sensitivity
    # summary counts and applies a strategy-level interpretation:
    #   "robust"   — every analysed sweep classified robust (n_fragile == 0
    #                AND n_moderate == 0)
    #   "moderate" — at most half of analysed sweeps are fragile
    req_sens = cfg.get("require_sensitivity_class")
    if req_sens:
        ps = sd.get("parameter_sensitivity") or {}
        summary = ps.get("summary") or {}
        n_total = int(summary.get("n_numeric_sweeps") or 0)
        n_fragile = int(summary.get("n_fragile") or 0)
        n_moderate = int(summary.get("n_moderate") or 0)
        req = str(req_sens).lower()

        if n_total == 0:
            failures.append({
                "gate": "require_sensitivity_class",
                "value": "none_analysed",
                "threshold": req,
                "reason": "parameter sensitivity not analysed — cannot satisfy classification gate",
            })
        elif req == "robust" and (n_fragile > 0 or n_moderate > 0):
            failures.append({
                "gate": "require_sensitivity_class",
                "value": {"fragile": n_fragile, "moderate": n_moderate, "total": n_total},
                "threshold": "robust",
                "reason": f"non-robust sweeps present (fragile={n_fragile}, moderate={n_moderate})",
            })
        elif req == "moderate" and n_fragile * 2 > n_total:
            failures.append({
                "gate": "require_sensitivity_class",
                "value": {"fragile": n_fragile, "total": n_total},
                "threshold": "moderate",
                "reason": f"fragile sweeps {n_fragile}/{n_total} exceed half the analysed set",
            })

    # Gate: require all validation gates to have passed.
    if cfg.get("require_validation_passed"):
        passed = bool((sd.get("validation") or {}).get("passed", False))
        if not passed:
            failures.append({
                "gate": "require_validation_passed",
                "value": False,
                "threshold": True,
                "reason": "validation gates did not all pass",
            })

    # Gate: require one-sided OOS permutation p-values to clear the configured
    # significance threshold. All available metric p-values must pass; the
    # worst p-value is reported so reviewers can see which metric failed.
    if cfg.get("require_permutation_passed"):
        threshold = float(cfg.get("max_permutation_p", 0.05))
        p_values = _extract_permutation_p_values(sd)
        reason = f"permutation_p>{threshold:g}"
        if not p_values:
            failures.append({
                "gate": "require_permutation_passed",
                "value": "not_run",
                "threshold": threshold,
                "reason": reason,
            })
        else:
            worst_metric, worst_p = max(p_values.items(), key=lambda item: item[1])
            if worst_p > threshold:
                failures.append({
                    "gate": "require_permutation_passed",
                    "value": {"metric": worst_metric, "p_value": worst_p},
                    "threshold": threshold,
                    "reason": reason,
                })

    # Gate: require the slippage / latency stress cell to pass (T8). When the
    # sweep was disabled or errored, we treat it as not satisfying the gate so
    # operators don't silently bypass execution-realism checks by leaving the
    # sweep off in YAML.
    if cfg.get("require_slippage_stress_passed"):
        ss = sd.get("slippage_stress") or {}
        if ss.get("error") or not ss.get("enabled"):
            failures.append({
                "gate": "require_slippage_stress_passed",
                "value": "not_run",
                "threshold": True,
                "reason": (
                    f"slippage stress not run: {ss.get('error') or 'disabled'}"
                ),
            })
        elif not bool(ss.get("passed", False)):
            cell = ss.get("stress_cell") or {}
            loss = cell.get("expectancy_loss_pct")
            threshold = ss.get("max_expectancy_loss_pct")
            failures.append({
                "gate": "require_slippage_stress_passed",
                "value": loss,
                "threshold": threshold,
                "reason": (
                    f"stress cell expectancy loss {loss:.2f}% exceeds "
                    f"max {float(threshold):.2f}%"
                    if loss is not None and threshold is not None
                    else "slippage stress cell did not pass"
                ),
            })

    return failures


def _select_ranking_evaluation(sd: Dict[str, Any]) -> Dict[str, Any]:
    """
    Choose the evaluation block ranking should score against.

    Preference order:
      1. evaluation_oos    — concatenated OOS folds on the dev slice.
                             Unbiased estimate; the right thing to rank on.
      2. evaluation        — dev-slice fit. Used as a fallback when OOS is
                             unavailable (e.g., insufficient sample for the
                             rolling walk-forward to carve folds).

    A skipped/error stub for ``evaluation_oos`` is treated as missing so
    the fallback kicks in. ``evaluation_holdout`` is intentionally not used
    here — the holdout is a one-shot promotion gate, not a ranking input.
    """
    oos = sd.get("evaluation_oos")
    if isinstance(oos, dict) and "n_trades" in oos and not oos.get("error") and not oos.get("skipped"):
        return oos
    return sd.get("evaluation") or {}


def compute_components(sd: Dict[str, Any]) -> Dict[str, float]:
    """
    Compute all component scores (0–100 each) from a strategy_discovery dict.
    """
    validation     = sd.get("validation") or {}
    evaluation     = _select_ranking_evaluation(sd)
    exit_disc      = sd.get("exit_discovery") or {}
    classification = sd.get("classification") or {}
    risk_metrics   = sd.get("risk_metrics") or {}

    return {
        # Quality components
        "risk_adj":        _score_risk_adj(evaluation, risk_metrics),
        "stability":       _score_stability(validation),
        "robustness":      _score_robustness(validation, exit_disc),
        "regime_fit":      _score_regime_fit(evaluation),
        "execution_fit":   _score_execution_fit(evaluation),
        # Deploy components — automatability now uses classification label if available
        "automatability":   _score_automatability(evaluation, classification.get("label")),
        "simplicity":       _score_simplicity(evaluation, exit_disc),
        "operational_risk": _score_operational_risk(evaluation),
        "monitoring_ease":  _score_monitoring_ease(validation, evaluation),
        # Penalties
        "overfit_penalty":   _overfit_penalty(validation),
        "complexity_penalty": _complexity_penalty(evaluation),
    }


def compute_scores(
    components: Dict[str, float],
    quality_w: Dict[str, float] = QUALITY_WEIGHTS,
    deploy_w: Dict[str, float] = DEPLOY_WEIGHTS,
) -> Dict[str, float]:
    """
    Compute quality, deploy, and final score from components.
    """
    quality = _weighted(components, quality_w)
    deploy  = _weighted(components, deploy_w)
    final   = _clamp(
        FINAL_WEIGHTS["quality"] * quality
        + FINAL_WEIGHTS["deploy"] * deploy
        - components.get("overfit_penalty", 0.0)
        - components.get("complexity_penalty", 0.0)
    )
    return {
        "quality_score":  round(quality, 2),
        "deploy_score":   round(deploy, 2),
        "final_score":    round(final, 2),
    }


# ---------------------------------------------------------------------------
# Weight sensitivity check
# ---------------------------------------------------------------------------

def run_sensitivity_check(
    run_ids: List[str],
    all_components: Dict[str, Dict[str, float]],
    perturbation: float = 0.20,
) -> Dict[str, Any]:
    """
    Perturb each quality/deploy weight by ±20% (independently),
    recompute final scores, and record if any top-5 run changes rank.

    Returns:
      {
        ranking_stable: bool,
        ranking_fragile: bool,
        fragile_runs: list[str],
        perturbation_details: list[{weight, direction, rank_changes}]
      }
    """
    # Baseline ranking
    base_scores = {rid: compute_scores(all_components[rid]) for rid in run_ids}
    base_rank = {rid: rank for rank, (rid, _) in enumerate(
        sorted(base_scores.items(), key=lambda x: x[1]["final_score"], reverse=True), start=1
    )}
    top5_base = {rid for rid, r in base_rank.items() if r <= 5}

    all_fragile_runs = set()
    details = []

    for weight_key in list(QUALITY_WEIGHTS.keys()) + list(DEPLOY_WEIGHTS.keys()):
        for direction, delta in [("+20%", perturbation), ("-20%", -perturbation)]:
            # Build perturbed weights
            if weight_key in QUALITY_WEIGHTS:
                q_w = {k: v * (1 + delta) if k == weight_key else v
                       for k, v in QUALITY_WEIGHTS.items()}
                d_w = DEPLOY_WEIGHTS
            else:
                q_w = QUALITY_WEIGHTS
                d_w = {k: v * (1 + delta) if k == weight_key else v
                       for k, v in DEPLOY_WEIGHTS.items()}

            pert_scores = {rid: compute_scores(all_components[rid], q_w, d_w) for rid in run_ids}
            pert_rank = {rid: rank for rank, (rid, _) in enumerate(
                sorted(pert_scores.items(), key=lambda x: x[1]["final_score"], reverse=True), start=1
            )}
            top5_pert = {rid for rid, r in pert_rank.items() if r <= 5}

            moved_out = top5_base - top5_pert
            moved_in  = top5_pert - top5_base
            if moved_out or moved_in:
                all_fragile_runs.update(moved_out)
                details.append({
                    "weight": weight_key,
                    "direction": direction,
                    "moved_out_of_top5": sorted(moved_out),
                    "moved_into_top5": sorted(moved_in),
                })

    ranking_stable  = len(all_fragile_runs) == 0
    ranking_fragile = not ranking_stable

    return {
        "ranking_stable": ranking_stable,
        "ranking_fragile": ranking_fragile,
        "fragile_runs": sorted(all_fragile_runs),
        "perturbation_pct": perturbation * 100,
        "perturbation_details": details,
    }


# ---------------------------------------------------------------------------
# Grade and tier helpers
# ---------------------------------------------------------------------------

def _grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    if score >= 35:
        return "D"
    return "F"


def _confidence_tier(score: float, passed: bool) -> str:
    if not passed:
        return "rejected"
    if score >= 75:
        return "high"
    if score >= 55:
        return "moderate"
    return "low"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_ranking(
    packages: Dict[str, Any],
    gates_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute cross-run ranking from all packages' strategy_discovery metadata.

    Hard exclusion gates (``gates_config``, defaults in
    ``DEFAULT_RANKING_GATES``) are applied first. Candidates that fail any
    enabled gate are routed to the ``rejected`` list with structured
    rejection reasons; only survivors enter the ranked leaderboard and
    contribute to the weight-sensitivity check.

    Attaches per-run ranking scores to
    ``pkg.metadata["derived"]["strategy_discovery"]["ranking"]``. Returns a
    JSON-safe cross-run summary dict containing both ``ranked`` and
    ``rejected`` lists.
    """
    run_scores: List[Dict[str, Any]] = []
    rejected_rows: List[Dict[str, Any]] = []
    all_components: Dict[str, Dict[str, float]] = {}
    valid_run_ids: List[str] = []

    for run_id, pkg in (packages or {}).items():
        if str(run_id).startswith("__"):
            continue

        meta = getattr(pkg, "metadata", None)
        if not isinstance(meta, dict):
            continue

        sd = meta.get("derived", {}).get("strategy_discovery")
        if not isinstance(sd, dict):
            continue

        # Hard-gate check up front. Failures route the run to ``rejected``
        # with structured reasons; survivors continue to scoring.
        gate_failures = evaluate_hard_gates(sd, gates_config)

        components = compute_components(sd)
        scores = compute_scores(components)

        validation = sd.get("validation") or {}
        # Display metrics on the leaderboard come from the same evaluation
        # block that drove the score, so reviewers see the numbers ranking
        # actually used (OOS when available; dev-slice as fallback).
        evaluation = _select_ranking_evaluation(sd)
        evaluation_dev = sd.get("evaluation") or {}
        evaluation_oos = sd.get("evaluation_oos") or {}
        evaluation_holdout = sd.get("evaluation_holdout") or {}
        holdout_partition = sd.get("holdout_partition") or {}
        ranking_source = (
            "oos" if (
                isinstance(evaluation_oos, dict)
                and evaluation_oos.get("n_trades") is not None
                and not evaluation_oos.get("error")
                and not evaluation_oos.get("skipped")
            ) else "dev"
        )

        classification = sd.get("classification") or {}
        row: Dict[str, Any] = {
            "run_id": str(run_id),
            "quality_score":  scores["quality_score"],
            "deploy_score":   scores["deploy_score"],
            "final_score":    scores["final_score"],
            "grade":          _grade(scores["final_score"]),
            "validation_passed": bool(validation.get("passed", False)),
            "confidence_tier": _confidence_tier(scores["final_score"], bool(validation.get("passed", False))),
            "components": {k: round(v, 2) for k, v in components.items()},
            # Provenance: which evaluation block ranking scored against.
            "ranking_source":   ranking_source,
            "holdout_enabled":  bool(holdout_partition.get("enabled", False)),
            # Key metrics for quick display — sourced from ranking_source above.
            "profit_factor":   evaluation.get("profit_factor"),
            "win_rate":        evaluation.get("win_rate"),
            "n_trades":        evaluation.get("n_trades"),
            "net_profit":      evaluation.get("net_profit"),
            "max_drawdown":    evaluation.get("max_drawdown"),
            # Side-by-side IS vs OOS vs HOLDOUT for at-a-glance degradation review.
            "dev_profit_factor":      evaluation_dev.get("profit_factor"),
            "dev_n_trades":           evaluation_dev.get("n_trades"),
            "dev_net_profit":         evaluation_dev.get("net_profit"),
            "oos_profit_factor":      evaluation_oos.get("profit_factor"),
            "oos_n_trades":           evaluation_oos.get("n_trades"),
            "oos_net_profit":         evaluation_oos.get("net_profit"),
            "holdout_profit_factor":  evaluation_holdout.get("profit_factor"),
            "holdout_n_trades":       evaluation_holdout.get("n_trades"),
            "holdout_net_profit":     evaluation_holdout.get("net_profit"),
            "holdout_win_rate":       evaluation_holdout.get("win_rate"),
            "classification":  classification.get("label"),
            "automation_score": classification.get("automation_score"),
            "oos_degradation": (sd.get("validation") or {}).get("wf_results", {}).get("oos_degradation"),
            "mc_dd_percentile": (sd.get("validation") or {}).get("monte_carlo", {}).get("dd_percentile_rank"),
            "t_test_p_value":  (sd.get("validation") or {}).get("t_test", {}).get("p_value"),
            # Risk-adjusted metrics (from risk_metrics module)
            "sharpe_ratio":    (sd.get("risk_metrics") or {}).get("sharpe_ratio"),
            "sortino_ratio":   (sd.get("risk_metrics") or {}).get("sortino_ratio"),
            "calmar_ratio":    (sd.get("risk_metrics") or {}).get("calmar_ratio"),
            "omega_ratio":     (sd.get("risk_metrics") or {}).get("omega_ratio"),
            "annualized_return_pct": (sd.get("risk_metrics") or {}).get("annualized_return_pct"),
            # Drawdown metrics (from drawdown_analysis module)
            "max_dd_pct":      (sd.get("drawdown_analysis") or {}).get("overall", {}).get("max_drawdown_pct"),
            "ulcer_index":     (sd.get("drawdown_analysis") or {}).get("overall", {}).get("ulcer_index"),
            "recovery_factor": (sd.get("drawdown_analysis") or {}).get("overall", {}).get("recovery_factor"),
            "max_consec_losses": (sd.get("drawdown_analysis") or {}).get("streaks", {}).get("max_consecutive_losses"),
            # Cohort / decay (from cohort_analysis module)
            "decay_score":     (sd.get("cohort_analysis") or {}).get("decay_score"),
            "trend_class":     (sd.get("cohort_analysis") or {}).get("trend", {}).get("classification"),
            # Position sizing (from position_sizing module)
            "kelly_full":      (sd.get("position_sizing") or {}).get("kelly_full"),
            "kelly_recommended_fok": (sd.get("position_sizing") or {}).get("recommendation", {}).get("recommended_fraction_of_kelly"),
        }

        # Decorate the row with hard-gate outcome so leaderboard renderers
        # can display rejection reasons even on the ``rejected`` table.
        row["hard_gates_passed"] = (len(gate_failures) == 0)
        row["rejection_reasons"] = list(gate_failures)
        if gate_failures:
            row["confidence_tier"] = "rejected"

        if gate_failures:
            rejected_rows.append(row)
        else:
            run_scores.append(row)
            all_components[str(run_id)] = components
            valid_run_ids.append(str(run_id))

        # Attach per-run ranking back to pkg metadata
        sd.setdefault("ranking", {}).update({
            "quality_score":   scores["quality_score"],
            "deploy_score":    scores["deploy_score"],
            "final_score":     scores["final_score"],
            "grade":           _grade(scores["final_score"]),
            "confidence_tier": "rejected" if gate_failures else _confidence_tier(
                scores["final_score"], bool(validation.get("passed", False))
            ),
            "components":      {k: round(v, 2) for k, v in components.items()},
            "ranking_source":  ranking_source,
            "holdout_enabled": bool(holdout_partition.get("enabled", False)),
            "hard_gates_passed": (len(gate_failures) == 0),
            "rejection_reasons": list(gate_failures),
        })

    # When every run was rejected, surface the rejected list anyway so
    # reviewers can see *why* the leaderboard is empty.
    if not run_scores:
        return {
            "ranked": [],
            "rejected": rejected_rows,
            "sensitivity": {"note": "no survivors — sensitivity N/A"},
            "diagnostics": {
                "n_runs":     len(rejected_rows),
                "n_survivors": 0,
                "n_rejected":  len(rejected_rows),
            },
        }

    # Sort survivors by final_score descending, assign rank.
    run_scores.sort(key=lambda r: r["final_score"], reverse=True)
    for i, row in enumerate(run_scores, start=1):
        row["rank"] = i

    # Weight sensitivity check (only meaningful with >= 2 runs)
    sensitivity: Dict[str, Any] = {}
    if len(valid_run_ids) >= 2:
        try:
            sensitivity = run_sensitivity_check(valid_run_ids, all_components)
        except Exception as exc:
            sensitivity = {"error": str(exc)}
    else:
        sensitivity = {"ranking_stable": True, "ranking_fragile": False, "note": "single run — sensitivity N/A"}

    # Attach stability flag back to each run's ranking metadata
    fragile_runs = set(sensitivity.get("fragile_runs") or [])
    for run_id, pkg in (packages or {}).items():
        if str(run_id).startswith("__"):
            continue
        meta = getattr(pkg, "metadata", None)
        if not isinstance(meta, dict):
            continue
        sd = meta.get("derived", {}).get("strategy_discovery", {})
        if isinstance(sd, dict) and "ranking" in sd:
            sd["ranking"]["ranking_stable"]  = str(run_id) not in fragile_runs
            sd["ranking"]["ranking_fragile"] = str(run_id) in fragile_runs

    cross_run = {
        "ranked": run_scores,
        "rejected": rejected_rows,
        "sensitivity": sensitivity,
        "diagnostics": {
            "n_runs":              len(run_scores) + len(rejected_rows),
            "n_survivors":         len(run_scores),
            "n_rejected":          len(rejected_rows),
            "n_passed_validation": sum(1 for r in run_scores if r["validation_passed"]),
            "n_high_confidence":   sum(1 for r in run_scores if r["confidence_tier"] == "high"),
        },
    }

    # Attach cross-run summary to the first non-system package for renderer access
    for run_id, pkg in (packages or {}).items():
        if str(run_id).startswith("__"):
            continue
        meta = getattr(pkg, "metadata", None)
        if isinstance(meta, dict):
            sd = meta.get("derived", {}).get("strategy_discovery")
            if isinstance(sd, dict):
                sd["cross_run_ranking"] = cross_run
                break

    return cross_run
