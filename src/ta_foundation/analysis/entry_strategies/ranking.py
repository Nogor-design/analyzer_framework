from __future__ import annotations

"""
Sweep Result Ranking
=====================
Classifies SweepResult dicts into five discovery tiers.

Tiers
-----
  best_raw          Top performers on raw signals before filtering.
                    Score = 0.4*PF + 0.3*WR + 0.2*norm_expectancy + 0.1*fill_rate
                    Minimum n_trades >= min_trades_raw.

  best_filtered     After applying the top filter_discovery result per entry,
                    sorted by filtered PF improvement.

  most_robust       Smallest IS→OOS degradation AND works across >= 2 regimes
                    AND >= 2 sessions.  Requires n_trades >= min_trades_robust.

  likely_overfit    High raw PF but suspicious: few trades, single-session,
                    single-regime, or high IS/OOS degradation.

  most_useful_filters
                    Filter conditions that improve PF across the most entry types.
                    One row per unique filter condition string.
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Config / thresholds
# ---------------------------------------------------------------------------

DEFAULT_RANKING_CONFIG: Dict[str, Any] = {
    "min_trades_raw":    20,
    "min_trades_robust": 40,
    "overfit_pf_min":    2.0,       # PF above this triggers overfit checks
    "overfit_trades_max":25,        # n_trades below this (with high PF) → overfit
    "overfit_degradation_max": 0.30,# IS→OOS drop beyond this → overfit
    "robust_degradation_max":  0.15,
    "robust_min_regimes":      2,
    "robust_min_sessions":     2,
    "top_n_each_tier":         20,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        f = float(v)
        return f if np.isfinite(f) else default
    except Exception:
        return default


def _raw_score(r: Dict[str, Any]) -> float:
    """Composite score for raw ranking."""
    m   = r.get("metrics", {})
    pf  = _safe(m.get("profit_factor"), 0.0)
    wr  = _safe(m.get("win_rate"), 0.0)
    exp = _safe(m.get("expectancy"), 0.0)
    fr  = _safe(r.get("fill_rate"), 1.0)
    # Normalise expectancy by a rough constant (100 = typical good trade in dollars)
    norm_exp = min(max(exp / 100.0, -1.0), 3.0)
    return 0.4 * pf + 0.3 * wr + 0.2 * norm_exp + 0.1 * fr


def _n_active_regimes(r: Dict[str, Any]) -> int:
    bk = r.get("regime_breakdown", {})
    return sum(1 for v in bk.values() if isinstance(v, dict) and v.get("n_trades", 0) >= 5)


def _n_active_sessions(r: Dict[str, Any]) -> int:
    bk = r.get("session_breakdown", {})
    return sum(1 for v in bk.values() if isinstance(v, dict) and v.get("n_trades", 0) >= 5)


def _top_filter_pf(r: Dict[str, Any]) -> Optional[float]:
    """PF of the best filtered subset, if available."""
    filters = r.get("filter_results", [])
    if not filters:
        return None
    top = filters[0]
    return top.get("pf_after") or top.get("profit_factor_after")


def _top_filter_label(r: Dict[str, Any]) -> str:
    filters = r.get("filter_results", [])
    if not filters:
        return ""
    f = filters[0]
    return str(f.get("condition_str") or f.get("label") or f.get("condition") or "")


def _result_label(r: Dict[str, Any]) -> str:
    return (
        f"{r.get('pattern_id','?')} | {r.get('tf','?')}m | "
        f"{r.get('direction_mode','?')} | {r.get('entry_timing','?')} | "
        f"{r.get('outcome_mode','?')} | {r.get('mtf_mode','independent')}"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def rank_sweep_results(
    sweep_results: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Classify a list of SweepResult dicts into the five discovery tiers.

    Parameters
    ----------
    sweep_results : output of run_candle_discovery()["sweep_results"] (and mtf_* lists)
    config        : optional ranking config (merged with DEFAULT_RANKING_CONFIG)

    Returns
    -------
    Dict with keys:
      best_raw, best_filtered, most_robust, likely_overfit, most_useful_filters
    Each value is a list of annotated dicts sorted by descending quality score.
    """
    cfg = {**DEFAULT_RANKING_CONFIG, **(config or {})}
    min_raw      = int(cfg["min_trades_raw"])
    min_robust   = int(cfg["min_trades_robust"])
    top_n        = int(cfg["top_n_each_tier"])
    overfit_pf   = float(cfg["overfit_pf_min"])
    overfit_nt   = int(cfg["overfit_trades_max"])
    overfit_deg  = float(cfg["overfit_degradation_max"])
    robust_deg   = float(cfg["robust_degradation_max"])
    robust_reg   = int(cfg["robust_min_regimes"])
    robust_sess  = int(cfg["robust_min_sessions"])

    all_results = [r for r in sweep_results if isinstance(r, dict)]

    # ----------------------------------------------------------------
    # Tier 1: Best Raw
    # ----------------------------------------------------------------
    best_raw = []
    for r in all_results:
        m  = r.get("metrics", {})
        nt = int(m.get("n_trades", 0))
        pf = _safe(m.get("profit_factor"))
        if nt < min_raw or pf <= 0:
            continue
        score = _raw_score(r)
        best_raw.append({**r, "_score": score, "_label": _result_label(r), "_tier": "best_raw"})

    best_raw.sort(key=lambda x: x["_score"], reverse=True)
    best_raw = best_raw[:top_n]

    # ----------------------------------------------------------------
    # Tier 2: Best Filtered
    # ----------------------------------------------------------------
    best_filtered = []
    for r in all_results:
        m  = r.get("metrics", {})
        nt = int(m.get("n_trades", 0))
        if nt < min_raw:
            continue
        raw_pf      = _safe(m.get("profit_factor"))
        filtered_pf = _top_filter_pf(r)
        if filtered_pf is None or raw_pf <= 0:
            continue
        pf_improvement = filtered_pf - raw_pf
        best_filtered.append({
            **r,
            "_score":          pf_improvement,
            "_label":          _result_label(r),
            "_tier":           "best_filtered",
            "_raw_pf":         raw_pf,
            "_filtered_pf":    filtered_pf,
            "_filter_label":   _top_filter_label(r),
            "_pf_improvement": round(pf_improvement, 4),
        })

    best_filtered.sort(key=lambda x: x["_score"], reverse=True)
    best_filtered = best_filtered[:top_n]

    # ----------------------------------------------------------------
    # Tier 3: Most Robust
    # ----------------------------------------------------------------
    most_robust = []
    for r in all_results:
        m  = r.get("metrics", {})
        nt = int(m.get("n_trades", 0))
        pf = _safe(m.get("profit_factor"))
        if nt < min_robust or pf < 1.0:
            continue

        deg = _safe(r.get("is_oos_degradation"), 0.0)
        if r.get("is_oos_degradation") is not None and deg > robust_deg:
            continue

        n_reg  = _n_active_regimes(r)
        n_sess = _n_active_sessions(r)
        if n_reg < robust_reg or n_sess < robust_sess:
            continue

        robustness_score = pf * (1.0 - deg) * (n_reg / max(robust_reg, 1))
        most_robust.append({
            **r,
            "_score":           robustness_score,
            "_label":           _result_label(r),
            "_tier":            "most_robust",
            "_n_regimes":       n_reg,
            "_n_sessions":      n_sess,
            "_is_oos_deg":      deg,
        })

    most_robust.sort(key=lambda x: x["_score"], reverse=True)
    most_robust = most_robust[:top_n]

    # ----------------------------------------------------------------
    # Tier 4: Likely Overfit
    # ----------------------------------------------------------------
    likely_overfit = []
    for r in all_results:
        m  = r.get("metrics", {})
        nt = int(m.get("n_trades", 0))
        pf = _safe(m.get("profit_factor"))
        flags: List[str] = []

        if pf >= overfit_pf and nt < overfit_nt:
            flags.append(f"high_PF ({pf:.2f}) with few trades ({nt})")

        n_reg  = _n_active_regimes(r)
        n_sess = _n_active_sessions(r)
        if nt >= min_raw and n_reg <= 1 and n_sess <= 1:
            flags.append(f"single regime+session only")

        deg = r.get("is_oos_degradation")
        if deg is not None and _safe(deg) > overfit_deg:
            flags.append(f"IS→OOS degradation {_safe(deg):.0%}")

        if flags:
            likely_overfit.append({
                **r,
                "_score":        pf,
                "_label":        _result_label(r),
                "_tier":         "likely_overfit",
                "_overfit_flags": "; ".join(flags),
            })

    likely_overfit.sort(key=lambda x: x["_score"], reverse=True)
    likely_overfit = likely_overfit[:top_n]

    # ----------------------------------------------------------------
    # Tier 5: Most Useful Filters
    # ----------------------------------------------------------------
    filter_stats: Dict[str, Dict[str, Any]] = {}

    for r in all_results:
        m  = r.get("metrics", {})
        raw_pf = _safe(m.get("profit_factor"))
        if raw_pf <= 0:
            continue
        for f in r.get("filter_results", []):
            label    = str(f.get("condition_str") or f.get("label") or f.get("condition") or "")
            filt_pf  = _safe(f.get("pf_after") or f.get("profit_factor_after"))
            if not label or filt_pf <= 0:
                continue
            pf_lift = filt_pf - raw_pf
            if label not in filter_stats:
                filter_stats[label] = {"n_improved": 0, "total_pf_lift": 0.0, "condition": label}
            if pf_lift > 0:
                filter_stats[label]["n_improved"]    += 1
                filter_stats[label]["total_pf_lift"] += pf_lift

    most_useful_filters = []
    for label, stats in filter_stats.items():
        n  = stats["n_improved"]
        tl = stats["total_pf_lift"]
        if n == 0:
            continue
        most_useful_filters.append({
            "condition":       label,
            "n_patterns_improved": n,
            "total_pf_lift":   round(tl, 4),
            "avg_pf_lift":     round(tl / n, 4),
            "_score":          n * (tl / n),   # n_improved * avg_pf_lift
            "_tier":           "most_useful_filters",
        })

    most_useful_filters.sort(key=lambda x: x["_score"], reverse=True)
    most_useful_filters = most_useful_filters[:top_n]

    return {
        "best_raw":            best_raw,
        "best_filtered":       best_filtered,
        "most_robust":         most_robust,
        "likely_overfit":      likely_overfit,
        "most_useful_filters": most_useful_filters,
    }
