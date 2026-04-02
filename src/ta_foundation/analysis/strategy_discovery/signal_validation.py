from __future__ import annotations

"""
Signal Rule Walk-Forward Validation
====================================
Validates discovered signal entry rules on held-out signal data.

Unlike trade-based walk-forward (validation.py) which requires executed trades,
this module operates entirely on the pattern engine signal corpus
(signal_feature_df — one row per signal firing).

The validation splits signal_feature_df into IS/OOS windows by bar-time,
re-discovers rules on IS data, then scores the same rules on OOS data.
Rules that degrade gracefully across multiple folds are flagged as "stable".

Entry point
-----------
  run_signal_validation(signal_feature_df, options) -> dict

Returns a JSON-safe dict:
  fold_results      : per-fold IS/OOS signal stats + rule IS vs OOS metrics
  rule_consistency  : cross-fold rule summary (how many folds each rule held up)
  summary           : overall assessment (n_consistent, avg_degradation, verdict)
  diagnostics       : counts, issues
"""

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .signal_entry_discovery import run_signal_entry_discovery, _evaluate_signal_rule
from .entry_discovery import _make_json_safe


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OPTIONS: Dict[str, Any] = {
    "enabled": True,
    "n_folds": 5,
    "is_pct": 0.70,                 # fraction of each window used as IS
    "wf_type": "rolling",           # rolling | anchored
    "min_is_signals": 50,           # minimum IS signals per fold to run discovery
    "min_oos_signals": 20,          # minimum OOS signals per fold to evaluate
    "min_rules_per_fold": 3,        # minimum rules to trust a fold
    # Per-rule verdict thresholds (degradation = (IS_wr - OOS_wr) / IS_wr)
    "stable_threshold": 0.05,       # degradation ≤ this → "stable"  (alias: degradation_threshold uses moderate value)
    "moderate_threshold": 0.20,     # degradation ≤ this → "moderate"
    # Aliases accepted from YAML for backward compatibility
    "degradation_threshold": 0.15,  # legacy alias → treated as moderate_threshold if moderate_threshold absent
    "top_n_rules": 10,              # rules discovered per fold (keep smaller for cross-fold matching)
    "min_folds_for_stable": 2,      # alias: min_folds_consistent — rule must appear consistently in ≥N folds
    "min_folds_consistent": 2,      # preferred YAML key (same effect)
    "pass_min_stable": 2,           # ≥N stable rules → overall verdict PASS
}


# ---------------------------------------------------------------------------
# Time-split helpers
# ---------------------------------------------------------------------------

def _rolling_splits(
    n: int,
    n_folds: int,
    is_pct: float,
) -> List[tuple]:
    """
    Rolling IS/OOS splits over n sorted rows.
    Returns list of (is_end_exclusive, oos_end_exclusive) index pairs.
    Each fold covers a different contiguous window.
    """
    if n_folds < 1 or n < 4:
        return []
    window = n // n_folds
    if window < 4:
        return []
    splits = []
    for i in range(n_folds):
        start = i * window
        end = start + window if i < n_folds - 1 else n
        is_end = start + max(1, int((end - start) * is_pct))
        splits.append((start, is_end, end))
    return splits


def _anchored_splits(
    n: int,
    n_folds: int,
    is_pct: float,
) -> List[tuple]:
    """
    Anchored IS/OOS splits: IS always starts at 0, OOS window advances.
    """
    if n_folds < 1 or n < 4:
        return []
    oos_size = n // (n_folds + 1)
    if oos_size < 2:
        return []
    splits = []
    for i in range(n_folds):
        oos_start = oos_size * (i + 1)
        oos_end = min(n, oos_start + oos_size)
        is_end = oos_start
        if is_end < max(10, int(n * is_pct * 0.5)):
            continue
        splits.append((0, is_end, oos_end))
    return splits


# ---------------------------------------------------------------------------
# Rule fingerprint (for cross-fold matching)
# ---------------------------------------------------------------------------

def _rule_fingerprint(rule: Dict[str, Any]) -> str:
    """Canonical string key for a rule — used to match across folds."""
    conds = sorted(
        (str(c.get("column", "")), str(c.get("op", "")), str(c.get("value", "")))
        for c in (rule.get("conditions") or [])
    )
    return "|".join(f"{col}:{op}:{val}" for col, op, val in conds)


# ---------------------------------------------------------------------------
# Per-fold evaluation
# ---------------------------------------------------------------------------

def _evaluate_rule_on_oos(
    signal_feature_df: pd.DataFrame,
    oos_idx: pd.Index,
    rule: Dict[str, Any],
    baseline: Dict[str, Any],
    profit_col: str,
    min_oos_signals: int,
) -> Optional[Dict[str, Any]]:
    """
    Apply a discovered IS rule to the OOS subset and return evaluation metrics.
    Returns None if OOS subset is too small.
    """
    from .entry_discovery import Condition

    conditions = [
        Condition(c["column"], c["op"], c["value"])
        for c in (rule.get("conditions") or [])
    ]
    if not conditions:
        return None

    oos_df = signal_feature_df.loc[oos_idx]
    mask = pd.Series(True, index=oos_df.index)
    for cond in conditions:
        mask = mask & cond.apply(oos_df)

    n_match = int(mask.sum())
    if n_match < min_oos_signals:
        return None

    stats = _evaluate_signal_rule(oos_df, mask, profit_col, baseline)
    if stats is None:
        return None

    return stats


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_signal_validation(
    signal_feature_df: pd.DataFrame,
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Walk-forward validation on pattern signal corpus.

    Splits signal_feature_df by bar-time into IS/OOS windows, re-discovers
    rules on IS, scores them on OOS, and identifies rules that hold up
    consistently across multiple folds.

    Parameters
    ----------
    signal_feature_df : from entry_pattern_bridge.build_signal_feature_matrix()
    options           : signal_validation config block

    Returns
    -------
    JSON-safe dict: fold_results, rule_consistency, summary, diagnostics
    """
    opts: Dict[str, Any] = {**DEFAULT_OPTIONS, **(options or {})}

    diag: Dict[str, Any] = {
        "n_signals_total": 0,
        "n_folds_attempted": 0,
        "n_folds_completed": 0,
        "issues": [],
    }

    empty = _make_json_safe({
        "fold_results": [],
        "rule_consistency": [],
        "summary": {"verdict": "SKIP", "n_consistent_rules": 0},
        "diagnostics": diag,
    })

    if not opts.get("enabled", True):
        diag["issues"].append("signal_validation disabled")
        return empty

    if signal_feature_df is None or not isinstance(signal_feature_df, pd.DataFrame):
        diag["issues"].append("no signal_feature_df provided")
        return empty

    # Require dt and a profit column
    if "dt" not in signal_feature_df.columns:
        diag["issues"].append("signal_feature_df missing dt column")
        return empty

    profit_col = str(opts.get("profit_col", "ret_ticks"))
    if profit_col not in signal_feature_df.columns:
        for fb in ["ret_ticks", "profit", "profit_net"]:
            if fb in signal_feature_df.columns:
                profit_col = fb
                break
        else:
            diag["issues"].append("no profit column found in signal_feature_df")
            return empty

    # Sort by time
    df = signal_feature_df.copy()
    df["_dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["_dt"]).sort_values("_dt").reset_index(drop=True)
    n = len(df)
    diag["n_signals_total"] = n

    if n < 40:
        diag["issues"].append(f"insufficient signals for validation: {n} < 40")
        return empty

    # Build time splits
    n_folds = int(opts.get("n_folds", 5))
    is_pct = float(opts.get("is_pct", 0.70))
    wf_type = str(opts.get("wf_type", "rolling")).lower()

    if wf_type == "anchored":
        splits = _anchored_splits(n, n_folds, is_pct)
    else:
        splits = _rolling_splits(n, n_folds, is_pct)

    diag["n_folds_attempted"] = len(splits)

    if not splits:
        diag["issues"].append("could not build IS/OOS splits")
        return empty

    min_is = int(opts.get("min_is_signals", 50))
    min_oos = int(opts.get("min_oos_signals", 20))
    top_n = int(opts.get("top_n_rules", 10))
    stable_thresh   = float(opts.get("stable_threshold", 0.05))
    moderate_thresh = float(
        opts.get("moderate_threshold")
        or opts.get("degradation_threshold", 0.20)
    )
    min_folds_stable = int(
        opts.get("min_folds_consistent")
        or opts.get("min_folds_for_stable", 2)
    )
    pass_min_stable = int(opts.get("pass_min_stable", 2))
    min_rules = int(opts.get("min_rules_per_fold", 3))

    # Discovery options: same as signal_entry_discovery but smaller top_n for speed
    disc_opts: Dict[str, Any] = {
        "enabled": True,
        "max_depth": int(opts.get("max_depth", 2)),
        "min_signals": int(opts.get("min_signals_per_rule", 15)),
        "max_candidates": int(opts.get("max_candidates", 200)),
        "top_n": top_n,
        "profit_col": profit_col,
        "adx_thresholds": opts.get("adx_thresholds") or [20, 25, 30],
        "corpus_win_rate_thresholds": opts.get("corpus_win_rate_thresholds") or [0.50, 0.55, 0.60],
    }

    fold_results: List[Dict[str, Any]] = []
    # Track per-rule cross-fold metrics: fingerprint -> list of fold stats
    rule_cross_fold: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    # Store canonical rule dict per fingerprint for display
    rule_canonical: Dict[str, Dict[str, Any]] = {}

    for fold_idx, (start_i, is_end_i, oos_end_i) in enumerate(splits):
        is_idx = df.index[start_i:is_end_i]
        oos_idx = df.index[is_end_i:oos_end_i]

        n_is = len(is_idx)
        n_oos = len(oos_idx)

        fold_info: Dict[str, Any] = {
            "fold": fold_idx + 1,
            "is_start": str(df.loc[is_idx[0], "_dt"])[:10] if n_is > 0 else None,
            "is_end": str(df.loc[is_idx[-1], "_dt"])[:10] if n_is > 0 else None,
            "oos_start": str(df.loc[oos_idx[0], "_dt"])[:10] if n_oos > 0 else None,
            "oos_end": str(df.loc[oos_idx[-1], "_dt"])[:10] if n_oos > 0 else None,
            "n_is_signals": n_is,
            "n_oos_signals": n_oos,
            "n_rules_found": 0,
            "rules": [],
            "skipped": False,
            "skip_reason": None,
        }

        if n_is < min_is:
            fold_info["skipped"] = True
            fold_info["skip_reason"] = f"IS signals {n_is} < {min_is}"
            fold_results.append(fold_info)
            continue
        if n_oos < min_oos:
            fold_info["skipped"] = True
            fold_info["skip_reason"] = f"OOS signals {n_oos} < {min_oos}"
            fold_results.append(fold_info)
            continue

        is_df = df.loc[is_idx]

        # Discover rules on IS
        disc_result = run_signal_entry_discovery(is_df, disc_opts, profit_col=profit_col)
        is_rules = disc_result.get("top_signal_rules") or []
        is_baseline = disc_result.get("baseline") or {}

        if len(is_rules) < min_rules:
            fold_info["skipped"] = True
            fold_info["skip_reason"] = f"only {len(is_rules)} IS rules (< {min_rules})"
            fold_results.append(fold_info)
            continue

        fold_info["n_rules_found"] = len(is_rules)
        oos_baseline = {
            "n_signals": n_oos,
            "win_rate": float(
                (pd.to_numeric(df.loc[oos_idx, profit_col], errors="coerce") > 0).mean()
            ),
            "avg_ticks": float(
                pd.to_numeric(df.loc[oos_idx, profit_col], errors="coerce").mean()
            ),
        }

        fold_rules: List[Dict[str, Any]] = []
        for rule in is_rules:
            fp = _rule_fingerprint(rule)
            if fp not in rule_canonical:
                rule_canonical[fp] = {
                    "rule_str": rule.get("rule_str", ""),
                    "conditions": rule.get("conditions") or [],
                    "n_conditions": rule.get("n_conditions", 0),
                }

            oos_stats = _evaluate_rule_on_oos(
                df, oos_idx, rule, oos_baseline, profit_col, min_oos
            )

            is_wr = float(rule.get("win_rate") or 0.5)
            oos_wr = float(oos_stats.get("win_rate") or 0.0) if oos_stats else None
            degradation = None
            if oos_wr is not None and is_wr > 0:
                degradation = round((is_wr - oos_wr) / is_wr, 4)

            consistent = (
                oos_wr is not None
                and degradation is not None
                and degradation <= moderate_thresh
                and oos_wr > 0.50
            )

            rule_entry: Dict[str, Any] = {
                "rule_str": rule.get("rule_str", ""),
                "is_win_rate": round(is_wr, 4),
                "is_n_signals": rule.get("n_signals"),
                "is_score": rule.get("score"),
                "oos_win_rate": round(oos_wr, 4) if oos_wr is not None else None,
                "oos_n_signals": oos_stats.get("n_signals") if oos_stats else None,
                "oos_avg_ticks": round(oos_stats.get("avg_ticks", 0), 2) if oos_stats else None,
                "degradation": degradation,
                "consistent": consistent,
            }
            fold_rules.append(rule_entry)

            cross_entry: Dict[str, Any] = {
                "fold": fold_idx + 1,
                "is_wr": is_wr,
                "oos_wr": oos_wr,
                "degradation": degradation,
                "consistent": consistent,
                "is_n": rule.get("n_signals"),
                "oos_n": oos_stats.get("n_signals") if oos_stats else None,
            }
            rule_cross_fold[fp].append(cross_entry)

        fold_info["rules"] = fold_rules
        fold_results.append(fold_info)
        diag["n_folds_completed"] += 1

    # ---------------------------------------------------------------------------
    # Cross-fold rule consistency
    # ---------------------------------------------------------------------------
    rule_consistency: List[Dict[str, Any]] = []

    for fp, fold_stats in rule_cross_fold.items():
        n_folds_found = len(fold_stats)
        n_consistent = sum(1 for f in fold_stats if f.get("consistent"))

        is_wrs = [f["is_wr"] for f in fold_stats if f.get("is_wr") is not None]
        oos_wrs = [f["oos_wr"] for f in fold_stats if f.get("oos_wr") is not None]
        degs = [f["degradation"] for f in fold_stats if f.get("degradation") is not None]

        avg_is_wr = round(float(np.mean(is_wrs)), 4) if is_wrs else None
        avg_oos_wr = round(float(np.mean(oos_wrs)), 4) if oos_wrs else None
        avg_deg = round(float(np.mean(degs)), 4) if degs else None

        if avg_deg is not None and avg_deg <= stable_thresh and n_consistent >= min_folds_stable:
            verdict = "stable"
        elif avg_deg is not None and avg_deg <= moderate_thresh and n_consistent >= 1:
            verdict = "moderate"
        else:
            verdict = "fragile"

        canonical = rule_canonical.get(fp, {})
        rule_consistency.append({
            "rule_str": canonical.get("rule_str", fp[:80]),
            "conditions": canonical.get("conditions") or [],
            "n_folds_found": n_folds_found,
            "n_folds_consistent": n_consistent,
            "avg_is_win_rate": avg_is_wr,
            "avg_oos_win_rate": avg_oos_wr,
            "avg_degradation": avg_deg,
            "verdict": verdict,
        })

    # Sort: stable first, then by avg_oos_wr descending
    verdict_order = {"stable": 0, "moderate": 1, "fragile": 2}
    rule_consistency.sort(
        key=lambda r: (verdict_order.get(r.get("verdict", "fragile"), 2),
                       -(r.get("avg_oos_win_rate") or 0))
    )

    # ---------------------------------------------------------------------------
    # Overall summary
    # ---------------------------------------------------------------------------
    n_stable = sum(1 for r in rule_consistency if r["verdict"] == "stable")
    n_moderate = sum(1 for r in rule_consistency if r["verdict"] == "moderate")
    all_degs = [r["avg_degradation"] for r in rule_consistency if r.get("avg_degradation") is not None]
    overall_avg_deg = round(float(np.mean(all_degs)), 4) if all_degs else None

    if diag["n_folds_completed"] == 0:
        verdict = "SKIP"
    elif n_stable >= pass_min_stable:
        verdict = "PASS"
    elif n_stable + n_moderate >= pass_min_stable:
        verdict = "WARN"
    else:
        verdict = "FAIL"

    summary = {
        "verdict": verdict,
        "n_consistent_rules": n_stable,
        "n_moderate_rules": n_moderate,
        "n_fragile_rules": len(rule_consistency) - n_stable - n_moderate,
        "n_folds_completed": diag["n_folds_completed"],
        "avg_wr_degradation": overall_avg_deg,
        "assessment": (
            f"{n_stable} stable rule{'s' if n_stable != 1 else ''}, "
            f"{n_moderate} moderate across {diag['n_folds_completed']} folds"
        ),
    }

    return _make_json_safe({
        "fold_results": fold_results,
        "rule_consistency": rule_consistency[:20],
        "summary": summary,
        "diagnostics": diag,
    })
