from __future__ import annotations

"""
Signal Entry Discovery
======================
Pure market-based entry discovery operating on the pattern engine signal corpus.

Finds conditions on pattern signal firings where forward return (ret_ticks) is
above baseline. Operates on ``signal_feature_df`` (one row per pattern firing),
unlike ``entry_discovery.py`` which operates on executed trades.

Entry point: run_signal_entry_discovery(signal_feature_df, options) -> dict
"""

import itertools
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Reuse utilities from entry_discovery to avoid duplication
from .entry_discovery import Condition, _generate_candidates, _norm01, _make_json_safe


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OPTIONS: Dict[str, Any] = {
    "enabled": True,
    "max_depth": 2,
    "min_signals": 15,
    "max_candidates": 300,
    "top_n": 20,
    "profit_col": "ret_ticks",
    "adx_thresholds": [20, 25, 30],
    "corpus_win_rate_thresholds": [0.50, 0.55, 0.60, 0.65],
}

# Categorical columns to enumerate unique values for
_CAT_COLS = ["regime", "session_label", "vol_regime", "tod_bucket"]
# Signal-specific categorical columns
_SIGNAL_CAT_COLS = ["family", "structure"]


# ---------------------------------------------------------------------------
# Atom generation (signal-specific variant)
# ---------------------------------------------------------------------------

def _generate_signal_atoms(
    signal_feature_df: pd.DataFrame,
    adx_thresholds: List[float],
    corpus_win_rate_thresholds: List[float],
) -> List[Condition]:
    """
    Generate atomic conditions from signal_feature_df columns.
    Extends entry_discovery atoms with signal-specific categorical columns
    and corpus_win_rate thresholds.
    """
    atoms: List[Condition] = []

    # Categorical columns (regime, session, vol, tod + pattern family/structure)
    for col in _CAT_COLS + _SIGNAL_CAT_COLS:
        if col not in signal_feature_df.columns:
            continue
        unique_vals = signal_feature_df[col].dropna().astype(str).unique()
        for v in sorted(unique_vals):
            if v in ("", "nan", "None", "UNKNOWN", "UNK"):
                continue
            atoms.append(Condition(col, "eq", v))

    # Direction (1 = long, -1 = short)
    if "direction" in signal_feature_df.columns:
        for v in [1.0, -1.0]:
            atoms.append(Condition("direction", "eq", str(v)))

    # ADX thresholds
    if "adx" in signal_feature_df.columns:
        for thresh in adx_thresholds:
            atoms.append(Condition("adx", "gte", float(thresh)))

    # Corpus win_rate thresholds (pre-filter to high-quality patterns)
    if "corpus_win_rate" in signal_feature_df.columns:
        for thresh in corpus_win_rate_thresholds:
            atoms.append(Condition("corpus_win_rate", "gte", float(thresh)))

    # Auto-threshold for ATR (>= p50, >= p75)
    if "atr" in signal_feature_df.columns:
        atr_series = pd.to_numeric(signal_feature_df["atr"], errors="coerce").dropna()
        if len(atr_series) >= 5:
            p50 = float(atr_series.quantile(0.50))
            p75 = float(atr_series.quantile(0.75))
            atoms.append(Condition("atr", "gte", round(p50, 4)))
            atoms.append(Condition("atr", "gte", round(p75, 4)))

    return atoms


# ---------------------------------------------------------------------------
# Rule evaluation on signal corpus
# ---------------------------------------------------------------------------

def _compute_signal_baseline(
    signal_feature_df: pd.DataFrame,
    profit_col: str,
) -> Dict[str, Any]:
    """Overall stats across all signal firings — used for lift calculations."""
    profit = pd.to_numeric(signal_feature_df[profit_col], errors="coerce").dropna()
    n = len(profit)
    if n == 0:
        return {"n_signals": 0, "win_rate": 0.5, "avg_ticks": 0.0}
    winners = profit[profit > 0]
    win_rate = float(len(winners) / n)
    avg_ticks = float(profit.mean())

    n_patterns = 0
    if "pattern_id" in signal_feature_df.columns:
        n_patterns = int(signal_feature_df["pattern_id"].nunique())

    return {
        "n_signals": int(n),
        "n_patterns": n_patterns,
        "win_rate": round(win_rate, 4),
        "avg_ticks": round(avg_ticks, 2),
    }


def _compute_family_breakdown(
    signal_feature_df: pd.DataFrame,
    profit_col: str,
    baseline: Dict[str, Any],
    min_signals: int = 5,
) -> List[Dict[str, Any]]:
    """Per-pattern-family stats from the signal corpus.

    Returns a list sorted by win_rate descending, one entry per family.
    Each entry has: family, n_signals, n_structures, win_rate, avg_ticks,
    win_rate_lift, selectivity, mfe_p50, mae_p50.
    """
    if "family" not in signal_feature_df.columns:
        return []
    if profit_col not in signal_feature_df.columns:
        return []

    base_wr = float(baseline.get("win_rate") or 0.5)
    base_n = int(baseline.get("n_signals") or 1)

    result = []
    for family, grp in signal_feature_df.groupby("family"):
        profit = pd.to_numeric(grp[profit_col], errors="coerce").dropna()
        n = len(profit)
        if n < min_signals:
            continue
        winners = profit[profit > 0]
        win_rate = float(len(winners) / n)
        avg_ticks = float(profit.mean())

        n_structures = 0
        if "structure" in grp.columns:
            n_structures = int(grp["structure"].nunique())

        mfe_p50: Optional[float] = None
        mae_p50: Optional[float] = None
        if "mfe_ticks" in grp.columns:
            mfe_vals = pd.to_numeric(grp["mfe_ticks"], errors="coerce").dropna()
            if len(mfe_vals) > 0:
                mfe_p50 = round(float(mfe_vals.quantile(0.5)), 2)
        if "mae_ticks" in grp.columns:
            mae_vals = pd.to_numeric(grp["mae_ticks"], errors="coerce").dropna()
            if len(mae_vals) > 0:
                mae_p50 = round(float(abs(mae_vals).quantile(0.5)), 2)

        result.append({
            "family": str(family),
            "n_signals": int(n),
            "n_structures": n_structures,
            "win_rate": round(win_rate, 4),
            "avg_ticks": round(avg_ticks, 2),
            "win_rate_lift": round(win_rate - base_wr, 4),
            "selectivity": round(n / max(1, base_n), 4),
            "mfe_p50": mfe_p50,
            "mae_p50": mae_p50,
        })

    result.sort(key=lambda r: r.get("win_rate", 0.0), reverse=True)
    return result


def _evaluate_signal_rule(
    signal_feature_df: pd.DataFrame,
    mask: pd.Series,
    profit_col: str,
    baseline: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Evaluate a rule against the signal subset it selects."""
    sub = signal_feature_df[mask]
    n = len(sub)
    if n < 1:
        return None

    profit = pd.to_numeric(sub[profit_col], errors="coerce").dropna()
    n_valid = len(profit)
    if n_valid < 1:
        return None

    winners = profit[profit > 0]
    win_rate = float(len(winners) / n_valid)
    avg_ticks = float(profit.mean())
    base_wr = float(baseline.get("win_rate") or 0.5)
    wr_lift = win_rate - base_wr
    selectivity = n_valid / max(1, baseline.get("n_signals") or n_valid)

    # MFE / MAE percentiles if available
    mfe_p50: Optional[float] = None
    mae_p50: Optional[float] = None
    if "mfe_ticks" in sub.columns:
        mfe_vals = pd.to_numeric(sub["mfe_ticks"], errors="coerce").dropna()
        if len(mfe_vals) > 0:
            mfe_p50 = float(mfe_vals.quantile(0.50))
    if "mae_ticks" in sub.columns:
        mae_vals = pd.to_numeric(sub["mae_ticks"], errors="coerce").dropna()
        if len(mae_vals) > 0:
            mae_p50 = float(abs(mae_vals).quantile(0.50))

    # Score: emphasise win-rate lift, volume, selectivity
    score = (
        0.50 * _norm01(wr_lift, -0.10, 0.30)
        + 0.30 * _norm01(min(n_valid, 200), 15, 200)
        + 0.20 * _norm01(selectivity, 0.0, 0.50)
    ) * 100.0

    return {
        "n_signals": n_valid,
        "win_rate": round(win_rate, 4),
        "avg_ticks": round(avg_ticks, 2),
        "win_rate_lift": round(wr_lift, 4),
        "selectivity": round(selectivity, 4),
        "mfe_p50": round(mfe_p50, 2) if mfe_p50 is not None else None,
        "mae_p50": round(mae_p50, 2) if mae_p50 is not None else None,
        "score": round(score, 2),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_signal_entry_discovery(
    signal_feature_df: pd.DataFrame,
    options: Dict[str, Any],
    profit_col: str = "ret_ticks",
) -> Dict[str, Any]:
    """
    Pure market-based entry discovery from pattern signal corpus.

    Finds conditions on signal firings where forward return (ret_ticks) beats
    the corpus baseline.  Does NOT require executed trades.

    Parameters
    ----------
    signal_feature_df : from entry_pattern_bridge.build_signal_feature_matrix()
    options           : signal_entry_discovery config block
    profit_col        : target column (default: ret_ticks)

    Returns
    -------
    JSON-safe dict:
      top_signal_rules  : ranked list of rule dicts
      baseline          : overall corpus stats
      signal_corpus     : corpus summary (n_signals, n_patterns, baseline)
      diagnostics       : atom/candidate counts, issues
    """
    opts: Dict[str, Any] = {**DEFAULT_OPTIONS, **(options or {})}

    if not opts.get("enabled", True):
        return {"skipped": True}

    if signal_feature_df is None or not isinstance(signal_feature_df, pd.DataFrame) or len(signal_feature_df) == 0:
        return {"skipped": True, "reason": "no signal data"}

    max_depth   = int(opts.get("max_depth", 2))
    min_signals = int(opts.get("min_signals", 15))
    max_cands   = int(opts.get("max_candidates", 300))
    top_n       = int(opts.get("top_n", 20))
    profit_col  = str(opts.get("profit_col", profit_col))
    adx_thresh  = [float(x) for x in (opts.get("adx_thresholds") or [20, 25, 30])]
    cwr_thresh  = [float(x) for x in (opts.get("corpus_win_rate_thresholds") or [0.50, 0.55, 0.60, 0.65])]

    diag: Dict[str, Any] = {
        "n_atoms": 0,
        "n_candidates": 0,
        "n_valid_candidates": 0,
        "profit_col_used": profit_col,
        "n_signals": int(len(signal_feature_df)),
        "n_patterns": 0,
        "primary_horizon": opts.get("primary_horizon", 20),
        "issues": [],
    }

    if "pattern_id" in signal_feature_df.columns:
        diag["n_patterns"] = int(signal_feature_df["pattern_id"].nunique())

    # Resolve profit column
    if profit_col not in signal_feature_df.columns:
        for fb in ["ret_ticks", "profit", "profit_net"]:
            if fb in signal_feature_df.columns:
                profit_col = fb
                diag["profit_col_used"] = profit_col
                break
        else:
            diag["issues"].append(f"profit column '{profit_col}' not found")
            return _make_json_safe({"top_signal_rules": [], "baseline": {}, "diagnostics": diag})

    profit_series = pd.to_numeric(signal_feature_df[profit_col], errors="coerce").dropna()
    if len(profit_series) < min_signals:
        diag["issues"].append(f"insufficient signals: {len(profit_series)} < {min_signals}")
        return _make_json_safe({"top_signal_rules": [], "baseline": {}, "diagnostics": diag})

    # Baseline
    baseline = _compute_signal_baseline(signal_feature_df, profit_col)

    # Generate atoms
    atoms = _generate_signal_atoms(signal_feature_df, adx_thresh, cwr_thresh)
    diag["n_atoms"] = len(atoms)

    if not atoms:
        diag["issues"].append("no atoms generated — signal_feature_df columns may be sparse")
        return _make_json_safe({"top_signal_rules": [], "baseline": baseline, "diagnostics": diag})

    # Generate candidates (reuse entry_discovery utility)
    candidates = _generate_candidates(atoms, max_depth=max_depth, max_candidates=max_cands)
    diag["n_candidates"] = len(candidates)

    # Evaluate
    results: List[Dict[str, Any]] = []
    for conditions in candidates:
        mask = pd.Series(True, index=signal_feature_df.index)
        for cond in conditions:
            mask = mask & cond.apply(signal_feature_df)
        n_match = int(mask.sum())
        if n_match < min_signals:
            continue

        stats = _evaluate_signal_rule(signal_feature_df, mask, profit_col, baseline)
        if stats is None:
            continue

        results.append({
            "conditions": [
                {"column": c.column, "op": c.op, "value": c.value, "description": c.description()}
                for c in conditions
            ],
            "rule_str": " AND ".join(c.description() for c in conditions),
            "n_conditions": len(conditions),
            **stats,
        })

    diag["n_valid_candidates"] = len(results)

    # Rank
    results.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    top_rules = results[:top_n]
    for i, r in enumerate(top_rules, start=1):
        r["rank"] = i

    # Summary in diagnostics
    if results:
        best = results[0]
        diag["best_score"] = best.get("score")
        diag["best_rule_str"] = best.get("rule_str")
        diag["best_n_signals"] = best.get("n_signals")
        diag["best_win_rate"] = best.get("win_rate")

    family_breakdown = _compute_family_breakdown(
        signal_feature_df, profit_col, baseline, min_signals=min_signals
    )

    return _make_json_safe({
        "top_signal_rules": top_rules,
        "baseline": baseline,
        "signal_corpus": {
            "n_signals": diag["n_signals"],
            "n_patterns": diag["n_patterns"],
            "corpus_baseline": baseline,
        },
        "family_breakdown": family_breakdown,
        "diagnostics": diag,
    })
