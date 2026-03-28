from __future__ import annotations

"""
Filter Discovery
================
Phase 2b — Discovers EXCLUSION filters: conditions that, when trades matching
them are removed, measurably improve overall strategy performance.

Complements entry_discovery.py (which finds inclusion conditions).
Filter discovery answers: "when should I NOT trade?"

Algorithm
---------
For each atomic condition C on the feature matrix:
  1. Split trades into: matching(C) vs not-matching(C)
  2. Compute metrics for "all trades" baseline
  3. Compute metrics for "all trades EXCEPT matching(C)"
  4. Score the exclusion by how much it improves PF, WR, avg profit

A condition is a good exclusion filter if removing it makes the remaining
trades substantially better.  We also check that enough trades remain after
the filter (min_remaining_trades).

Scoring
-------
Filter score = weighted blend of:
  - PF improvement: (pf_filtered - pf_baseline) / pf_baseline
  - WR improvement: wr_filtered - wr_baseline
  - Avg profit improvement: avg_profit_filtered - avg_profit_baseline
  - Volume penalty: penalise heavy filters that remove >60% of trades

Return value
------------
JSON-safe dict:
  top_filters          : list of ranked filter dicts
  baseline             : overall metrics before any filtering
  by_condition_type    : best filter per column type
  diagnostics          : {n_atoms, n_evaluated, ...}
"""

import itertools
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Re-use condition machinery from entry_discovery
from .entry_discovery import (
    Condition,
    _generate_atoms,
    _compute_baseline,
    DEFAULT_OPTIONS as _ENTRY_DEFAULTS,
)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OPTIONS: Dict[str, Any] = {
    "enabled":                  True,
    "min_trades":               20,          # min trades for analysis overall
    "min_remaining_trades":     15,          # min trades left after exclusion
    "max_filters":              200,         # cap on atoms to evaluate
    "top_n":                    20,
    "adx_thresholds":           [20, 25, 30],
    "pattern_score_thresholds": [1.0, 2.0, 3.0],
    "profit_col":               "profit_net",
    # Weights for the improvement score
    "weight_pf_lift":           0.45,
    "weight_wr_lift":           0.35,
    "weight_avg_profit_lift":   0.20,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        f = float(v)
        return default if not np.isfinite(f) else f
    except Exception:
        return default


def _metrics(profit: pd.Series) -> Dict[str, float]:
    """Compute PF, WR, avg_profit, net_profit for a series of trade profits."""
    n = int(profit.notna().sum())
    if n == 0:
        return {"n_trades": 0, "profit_factor": 0.0, "win_rate": 0.0,
                "avg_profit": 0.0, "net_profit": 0.0}
    p = profit.dropna()
    winners = p[p > 0]
    losers  = p[p <= 0]
    gw = float(winners.sum()) if len(winners) > 0 else 0.0
    gl = float(abs(losers.sum())) if len(losers) > 0 else 0.0
    pf = gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0)
    return {
        "n_trades":     n,
        "profit_factor": round(min(pf, 99.0), 4),
        "win_rate":     round(float(len(winners) / n), 4),
        "avg_profit":   round(float(p.mean()), 2),
        "net_profit":   round(float(p.sum()), 2),
    }


def _score_filter(
    baseline: Dict[str, float],
    filtered: Dict[str, float],
    n_removed: int,
    n_total: int,
    w_pf: float,
    w_wr: float,
    w_avg: float,
) -> float:
    """
    Score how much removing the matched trades improves the residual.
    Returns 0–100.  Negative when filtering makes things worse.
    """
    pf_base = _safe_float(baseline.get("profit_factor"), 1.0)
    wr_base = _safe_float(baseline.get("win_rate"), 0.5)
    ap_base = _safe_float(baseline.get("avg_profit"), 0.0)

    pf_filt = _safe_float(filtered.get("profit_factor"), 1.0)
    wr_filt = _safe_float(filtered.get("win_rate"), 0.5)
    ap_filt = _safe_float(filtered.get("avg_profit"), 0.0)

    # Relative improvement in PF (capped to prevent outliers)
    pf_lift = (pf_filt - pf_base) / max(pf_base, 0.01)
    pf_lift = max(-1.0, min(1.0, pf_lift))

    # Absolute improvement in WR
    wr_lift = wr_filt - wr_base
    wr_lift = max(-0.3, min(0.3, wr_lift))

    # Relative improvement in avg profit
    ap_lift = (ap_filt - ap_base) / (abs(ap_base) + 1.0)
    ap_lift = max(-1.0, min(1.0, ap_lift))

    # Raw improvement score
    raw = (
        w_pf  * pf_lift   / 1.0   # map [-1,1] → contribution
        + w_wr  * wr_lift  / 0.3
        + w_avg * ap_lift  / 1.0
    )

    # Volume penalty: heavy filters (>60% removed) get a discount
    removal_frac = n_removed / max(n_total, 1)
    if removal_frac > 0.60:
        penalty = (removal_frac - 0.60) / 0.40   # 0→1 as removal goes 60→100%
        raw *= (1.0 - 0.5 * penalty)

    # Map to 0–100 centred at 50 (50 = no improvement)
    score = 50.0 + raw * 50.0
    return round(max(0.0, min(100.0, score)), 2)


def _make_json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if not np.isfinite(v) else v
    if isinstance(obj, float):
        return None if not np.isfinite(obj) else obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_filter_discovery(
    pkg: Any,
    options: Dict[str, Any],
    *,
    feature_df: Optional[pd.DataFrame] = None,
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Discover exclusion filters that improve strategy performance.

    Parameters
    ----------
    pkg           : AnalysisPackage
    options       : strategy_discovery.filter_discovery config block
    feature_df    : prebuilt feature matrix; if None reads from pkg.assets
    bars_with_regime : optional for on-the-fly feature build fallback

    Returns
    -------
    JSON-safe dict:
      top_filters          : ranked list of filter dicts
      baseline             : overall metrics (before any filtering)
      by_condition_type    : {col_bucket: best_filter_dict}
      diagnostics          : {n_atoms, n_evaluated, n_valid, ...}
    """
    opts: Dict[str, Any] = {**DEFAULT_OPTIONS, **(options or {})}

    if not opts.get("enabled", True):
        return {"skipped": True}

    min_trades      = int(opts.get("min_trades", 20))
    min_remaining   = int(opts.get("min_remaining_trades", 15))
    max_filters     = int(opts.get("max_filters", 200))
    top_n           = int(opts.get("top_n", 20))
    profit_col      = str(opts.get("profit_col", "profit_net"))
    adx_thresh      = [float(x) for x in (opts.get("adx_thresholds") or [20, 25, 30])]
    ps_thresh       = [float(x) for x in (opts.get("pattern_score_thresholds") or [1.0, 2.0, 3.0])]
    w_pf            = float(opts.get("weight_pf_lift", 0.45))
    w_wr            = float(opts.get("weight_wr_lift", 0.35))
    w_avg           = float(opts.get("weight_avg_profit_lift", 0.20))

    diag: Dict[str, Any] = {
        "n_atoms":     0,
        "n_evaluated": 0,
        "n_valid":     0,
        "profit_col_used": profit_col,
        "issues":      [],
    }

    # ---- resolve feature matrix ----
    if feature_df is None or not isinstance(feature_df, pd.DataFrame) or len(feature_df) == 0:
        pkg_assets = getattr(pkg, "assets", {}) or {}
        sd_assets  = pkg_assets.get("strategy_discovery") or {}
        feature_df = sd_assets.get("feature_matrix") if isinstance(sd_assets, dict) else None

    if feature_df is None or not isinstance(feature_df, pd.DataFrame) or len(feature_df) == 0:
        try:
            from .features import build_feature_matrix
            trades = getattr(pkg, "trades", None)
            if trades is not None and len(trades) > 0:
                audit_df = None
                pkg_assets2 = getattr(pkg, "assets", {}) or {}
                aa = pkg_assets2.get("trade_pattern_audit") or {}
                if isinstance(aa, dict):
                    audit_df = aa.get("audit_df")
                feature_df = build_feature_matrix(
                    trades,
                    bars_with_regime=bars_with_regime,
                    audit_df=audit_df if isinstance(audit_df, pd.DataFrame) else None,
                )
            else:
                diag["issues"].append("no trades available")
                return _make_json_safe({"top_filters": [], "baseline": {}, "diagnostics": diag})
        except Exception as exc:
            diag["issues"].append(f"feature matrix build failed: {exc}")
            return _make_json_safe({"top_filters": [], "baseline": {}, "diagnostics": diag})

    # ---- resolve profit column ----
    if profit_col not in feature_df.columns:
        if "profit" in feature_df.columns:
            profit_col = "profit"
            diag["profit_col_used"] = profit_col
        else:
            diag["issues"].append(f"profit column '{profit_col}' not found")
            return _make_json_safe({"top_filters": [], "baseline": {}, "diagnostics": diag})

    profit_all = pd.to_numeric(feature_df[profit_col], errors="coerce")
    n_total = int(profit_all.notna().sum())

    if n_total < min_trades:
        diag["issues"].append(f"insufficient trades: {n_total} < {min_trades}")
        return _make_json_safe({"top_filters": [], "baseline": {}, "diagnostics": diag})

    baseline = _compute_baseline(feature_df, profit_col)

    # ---- generate atoms ----
    atoms = _generate_atoms(feature_df, adx_thresh, ps_thresh)
    diag["n_atoms"] = len(atoms)

    if not atoms:
        diag["issues"].append("no condition atoms generated")
        return _make_json_safe({
            "top_filters": [], "baseline": baseline, "diagnostics": diag
        })

    # Cap atoms to max_filters
    atoms_to_eval = atoms[:max_filters]

    # ---- evaluate each atom as an exclusion filter ----
    results: List[Dict[str, Any]] = []
    diag["n_evaluated"] = len(atoms_to_eval)

    for cond in atoms_to_eval:
        match_mask = cond.apply(feature_df)
        n_match    = int(match_mask.sum())
        n_remain   = n_total - n_match

        if n_remain < min_remaining:
            continue   # too many removed — would leave too few trades

        if n_match == 0:
            continue   # condition never fires

        profit_excl = profit_all[~match_mask]
        filtered    = _metrics(profit_excl)
        matched_met = _metrics(profit_all[match_mask])

        score = _score_filter(
            baseline, filtered,
            n_removed=n_match,
            n_total=n_total,
            w_pf=w_pf, w_wr=w_wr, w_avg=w_avg,
        )

        # Only keep filters that actually improve things (score > 50)
        results.append({
            "condition":          {"column": cond.column, "op": cond.op,
                                   "value": cond.value,
                                   "description": cond.description()},
            "filter_str":         f"EXCLUDE when {cond.description()}",
            "score":              score,
            "n_removed":          n_match,
            "removal_pct":        round(n_match / max(n_total, 1) * 100, 1),
            "n_remaining":        n_remain,
            # Filtered (after exclusion) metrics
            "pf_after":           filtered.get("profit_factor"),
            "wr_after":           filtered.get("win_rate"),
            "avg_profit_after":   filtered.get("avg_profit"),
            "net_profit_after":   filtered.get("net_profit"),
            # Baseline deltas
            "pf_delta":           round(
                (filtered.get("profit_factor", 0) or 0)
                - (baseline.get("profit_factor", 0) or 0), 4
            ),
            "wr_delta":           round(
                (filtered.get("win_rate", 0) or 0)
                - (baseline.get("win_rate", 0) or 0), 4
            ),
            # Excluded trades profile (what we're removing)
            "excluded_profile": {
                "pf":           matched_met.get("profit_factor"),
                "wr":           matched_met.get("win_rate"),
                "avg_profit":   matched_met.get("avg_profit"),
                "n_trades":     n_match,
            },
        })

    diag["n_valid"] = len(results)

    # ---- rank: best filters first (highest score) ----
    results.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    top_filters = results[:top_n]
    for i, r in enumerate(top_filters, start=1):
        r["rank"] = i

    # ---- summary stats ----
    if results:
        best = results[0]
        diag["best_score"]     = best.get("score")
        diag["best_filter_str"] = best.get("filter_str")
        diag["best_pf_delta"]  = best.get("pf_delta")
        diag["best_wr_delta"]  = best.get("wr_delta")
        diag["best_n_removed"] = best.get("n_removed")

    # Count improvement-yielding filters
    n_improving = sum(1 for r in results if (r.get("score") or 0) > 50)
    diag["n_improving_filters"] = n_improving

    # ---- group by condition column type ----
    by_type: Dict[str, Dict[str, Any]] = {}
    for r in top_filters[:15]:
        col = r["condition"]["column"]
        bucket = "pattern" if col.startswith("pat_") else col
        if bucket not in by_type:
            by_type[bucket] = {
                "best_filter_str": r.get("filter_str"),
                "score":           r.get("score"),
                "pf_delta":        r.get("pf_delta"),
                "wr_delta":        r.get("wr_delta"),
                "n_removed":       r.get("n_removed"),
            }

    return _make_json_safe({
        "top_filters":       top_filters,
        "baseline":          baseline,
        "by_condition_type": by_type,
        "diagnostics":       diag,
    })
