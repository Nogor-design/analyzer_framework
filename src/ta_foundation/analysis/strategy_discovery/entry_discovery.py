from __future__ import annotations

"""
Entry Discovery
===============
Phase 2 — Systematically discovers rule-based entry conditions that produce
statistically meaningful subsets of profitable trades.

Design
------
A rule candidate is a CONJUNCTION of atomic conditions evaluated against the
feature matrix built by features.py.  Each condition tests one column:

  Condition type       Column(s)          Examples
  ──────────────────   ────────────────── ─────────────────────────────
  Categorical EQ       regime             regime == "trending"
                       session_label      session_label == "us_open"
                       pattern_verdict    pattern_verdict == "CONFIRMED"
  Boolean (pat_*)      pat_<key>          pat_ma_alignment == True
  Numeric threshold    adx                adx >= 25
                       pattern_score      pattern_score >= 2.0
                       atr                atr >= <p50>  (auto-threshold)
  Direction            direction          direction == 1 (Long only)

Rule generation
---------------
1. Generate all *atomic* conditions from the columns present in the feature df.
2. Enumerate conjunctions up to depth=2 (default), filtering any pair where
   both conditions target the same column (redundant).
3. Evaluate every candidate on the trades subset that satisfies all conditions.
4. Discard candidates with < min_trades (default 20) matching rows.
5. Score by a blend of profit factor, win rate, and avg net profit per trade.
6. Return the top-N ranked candidates in a JSON-safe dict.

Pattern Engine first-pass filter
---------------------------------
When the feature matrix has pat_* bool columns (from the trade_pattern_audit
bridge), they are included as Boolean conditions.  This gives the search a
direct hook into the pattern engine's signal library without reimplementing
detectors here.

Entry point: run_entry_discovery(pkg, options, feature_df) → dict
"""

import itertools
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

DEFAULT_OPTIONS: Dict[str, Any] = {
    "enabled":           True,
    "max_depth":         2,        # max conditions per rule conjunction
    "min_trades":        20,       # minimum matching trades for a candidate
    "max_candidates":    300,      # cap on total candidates evaluated
    "top_n":             25,       # top-N to return in results
    "adx_thresholds":    [20, 25, 30],
    "pattern_score_thresholds": [1.0, 2.0, 3.0],
    "profit_col":        "profit_net",
}

# Categorical columns to enumerate values for
_CAT_COLS = ["regime", "session_label", "pattern_verdict"]

# Numeric columns to threshold (thresholds are supplied via options or defaults)
_NUM_COLS = ["adx", "pattern_score"]

# Direction values
_DIRECTION_VALUES = [1.0, -1.0]


# ---------------------------------------------------------------------------
# Condition dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Condition:
    """A single atomic condition on one feature column."""
    column: str
    op: str       # "eq", "gte", "lte", "bool_true"
    value: Any    # comparison value

    def apply(self, df: pd.DataFrame) -> pd.Series:
        """Return a boolean Series: rows satisfying this condition."""
        if self.column not in df.columns:
            return pd.Series(False, index=df.index)
        col = df[self.column]
        try:
            if self.op == "eq":
                return col.astype(str) == str(self.value)
            if self.op == "gte":
                return pd.to_numeric(col, errors="coerce") >= float(self.value)
            if self.op == "lte":
                return pd.to_numeric(col, errors="coerce") <= float(self.value)
            if self.op == "bool_true":
                return col.astype(bool)
            return pd.Series(False, index=df.index)
        except Exception:
            return pd.Series(False, index=df.index)

    def description(self) -> str:
        if self.op == "bool_true":
            return f"{self.column} fired"
        op_sym = {"eq": "==", "gte": ">=", "lte": "<="}.get(self.op, self.op)
        return f"{self.column} {op_sym} {self.value}"


# ---------------------------------------------------------------------------
# Atom generation
# ---------------------------------------------------------------------------

def _generate_atoms(
    feature_df: pd.DataFrame,
    adx_thresholds: List[float],
    pattern_score_thresholds: List[float],
) -> List[Condition]:
    """
    Generate all atomic conditions from the feature matrix columns.
    Each atom corresponds to one testable condition on one feature.
    """
    atoms: List[Condition] = []

    # Categorical columns
    for col in _CAT_COLS:
        if col not in feature_df.columns:
            continue
        unique_vals = feature_df[col].dropna().astype(str).unique()
        for v in sorted(unique_vals):
            if v in ("", "nan", "None"):
                continue
            atoms.append(Condition(col, "eq", v))

    # Direction (numeric equality)
    if "direction" in feature_df.columns:
        for v in _DIRECTION_VALUES:
            atoms.append(Condition("direction", "eq", str(v)))

    # Numeric ADX thresholds
    if "adx" in feature_df.columns:
        for thresh in adx_thresholds:
            atoms.append(Condition("adx", "gte", float(thresh)))

    # pattern_score thresholds
    if "pattern_score" in feature_df.columns:
        for thresh in pattern_score_thresholds:
            atoms.append(Condition("pattern_score", "gte", float(thresh)))

    # Auto-threshold for ATR (>= p50, i.e. "elevated volatility")
    if "atr" in feature_df.columns:
        atr_series = pd.to_numeric(feature_df["atr"], errors="coerce").dropna()
        if len(atr_series) >= 5:
            p50 = float(atr_series.quantile(0.50))
            p75 = float(atr_series.quantile(0.75))
            atoms.append(Condition("atr", "gte", round(p50, 4)))
            atoms.append(Condition("atr", "gte", round(p75, 4)))

    # Boolean pattern columns (pat_*)
    pat_cols = [c for c in feature_df.columns if str(c).startswith("pat_")]
    for col in pat_cols:
        # Only add as atom if the pattern fires for at least 5% of trades
        fired_rate = float(feature_df[col].astype(bool).mean())
        if fired_rate >= 0.05:
            atoms.append(Condition(col, "bool_true", True))

    return atoms


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------

def _generate_candidates(
    atoms: List[Condition],
    max_depth: int,
    max_candidates: int,
) -> List[Tuple[Condition, ...]]:
    """
    Generate all unique condition conjunctions up to max_depth.

    Rules:
    - Depth 1: each single atom
    - Depth 2: all pairs of atoms from DIFFERENT columns
    - Depth 3: all triples of atoms from 3 DIFFERENT columns (only when max_depth=3)
    - Cap at max_candidates total
    """
    candidates: List[Tuple[Condition, ...]] = []

    # Depth 1
    for atom in atoms:
        candidates.append((atom,))
        if len(candidates) >= max_candidates:
            return candidates

    # Depth 2+
    for depth in range(2, min(max_depth + 1, 4)):
        for combo in itertools.combinations(atoms, depth):
            # All conditions must target different columns
            cols_used = [c.column for c in combo]
            if len(cols_used) != len(set(cols_used)):
                continue
            candidates.append(combo)
            if len(candidates) >= max_candidates:
                return candidates

    return candidates


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------

def _apply_conjunction(
    feature_df: pd.DataFrame,
    conditions: Tuple[Condition, ...],
) -> pd.Series:
    """Return boolean Series: rows matching ALL conditions."""
    mask = pd.Series(True, index=feature_df.index)
    for cond in conditions:
        mask = mask & cond.apply(feature_df)
    return mask


def _evaluate_rule(
    feature_df: pd.DataFrame,
    mask: pd.Series,
    profit_col: str,
    baseline: Dict[str, float],
) -> Optional[Dict[str, Any]]:
    """
    Evaluate a rule against the trade subset it selects.

    Returns None if insufficient trades.
    """
    sub = feature_df[mask]
    n = len(sub)
    if n < 1:
        return None

    profit = pd.to_numeric(sub[profit_col], errors="coerce").dropna()
    n_valid = len(profit)
    if n_valid < 1:
        return None

    winners = profit[profit > 0]
    losers  = profit[profit <= 0]
    win_rate   = float(len(winners) / n_valid) if n_valid > 0 else 0.0
    gross_win  = float(winners.sum()) if len(winners) > 0 else 0.0
    gross_loss = float(abs(losers.sum())) if len(losers) > 0 else 0.0
    pf         = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    avg_profit = float(profit.mean())
    net_profit = float(profit.sum())

    # Selectivity: fraction of all trades captured
    selectivity = n_valid / max(1, baseline.get("n_trades_total", n_valid))

    # Lift in win rate vs baseline
    base_wr = baseline.get("win_rate", 0.5)
    wr_lift = win_rate - base_wr

    # Score: blend of PF, WR lift, average profit
    # Cap PF at 5 to prevent inf from dominating
    pf_capped = min(pf, 5.0) if np.isfinite(pf) else 0.0
    score = (
        0.40 * _norm01(pf_capped, 0.0, 3.0)        # PF contribution
        + 0.30 * _norm01(wr_lift, -0.2, 0.3)         # WR lift vs baseline
        + 0.20 * _norm01(min(n_valid, 200), 20, 200) # volume: more trades = better
        + 0.10 * _norm01(selectivity, 0.0, 0.5)       # selectivity bonus (up to 50%)
    ) * 100.0

    return {
        "n_trades":    n_valid,
        "win_rate":    round(win_rate, 4),
        "profit_factor": round(pf, 4) if np.isfinite(pf) else None,
        "avg_profit":  round(avg_profit, 2),
        "net_profit":  round(net_profit, 2),
        "wr_lift":     round(wr_lift, 4),
        "selectivity": round(selectivity, 4),
        "score":       round(score, 2),
    }


def _norm01(v: float, lo: float, hi: float) -> float:
    """Linearly map v into [0, 1] given expected range [lo, hi]. Clamps outside."""
    if hi <= lo:
        return 0.5
    return float(max(0.0, min(1.0, (v - lo) / (hi - lo))))


# ---------------------------------------------------------------------------
# Baseline stats
# ---------------------------------------------------------------------------

def _compute_baseline(feature_df: pd.DataFrame, profit_col: str) -> Dict[str, float]:
    """Overall stats across all trades — used for lift calculations."""
    profit = pd.to_numeric(feature_df[profit_col], errors="coerce").dropna()
    n = len(profit)
    if n == 0:
        return {"n_trades_total": 0, "win_rate": 0.5, "profit_factor": 1.0, "avg_profit": 0.0}
    winners = profit[profit > 0]
    losers  = profit[profit <= 0]
    win_rate = float(len(winners) / n)
    gross_win  = float(winners.sum()) if len(winners) > 0 else 0.0
    gross_loss = float(abs(losers.sum())) if len(losers) > 0 else 0.0
    pf = gross_win / gross_loss if gross_loss > 0 else (2.0 if gross_win > 0 else 1.0)
    return {
        "n_trades_total": n,
        "win_rate":        round(win_rate, 4),
        "profit_factor":   round(pf, 4),
        "avg_profit":      round(float(profit.mean()), 2),
    }


# ---------------------------------------------------------------------------
# Results post-processing
# ---------------------------------------------------------------------------

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

def run_entry_discovery(
    pkg: Any,
    options: Dict[str, Any],
    *,
    feature_df: Optional[pd.DataFrame] = None,
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Discover rule-based entry conditions for a single strategy package.

    Parameters
    ----------
    pkg           : AnalysisPackage
    options       : strategy_discovery.entry_discovery config block
    feature_df    : prebuilt feature matrix (from features.build_feature_matrix);
                    if None, the function reads from pkg.assets
    bars_with_regime : optional bars with regime columns for on-the-fly feature build

    Returns
    -------
    JSON-safe dict:
      top_rules         : list of top-N rule dicts (conditions + stats)
      baseline          : overall trade stats
      diagnostics       : {n_atoms, n_candidates, n_valid_candidates, ...}
    """
    # Merge options with defaults
    opts: Dict[str, Any] = {**DEFAULT_OPTIONS, **(options or {})}

    if not opts.get("enabled", True):
        return {"skipped": True}

    max_depth   = int(opts.get("max_depth", 2))
    min_trades  = int(opts.get("min_trades", 20))
    max_cands   = int(opts.get("max_candidates", 300))
    top_n       = int(opts.get("top_n", 25))
    profit_col  = str(opts.get("profit_col", "profit_net"))
    adx_thresh  = [float(x) for x in (opts.get("adx_thresholds") or [20, 25, 30])]
    ps_thresh   = [float(x) for x in (opts.get("pattern_score_thresholds") or [1.0, 2.0, 3.0])]

    diag: Dict[str, Any] = {
        "n_atoms":             0,
        "n_candidates":        0,
        "n_valid_candidates":  0,
        "profit_col_used":     profit_col,
        "issues":              [],
    }

    # ---- get feature matrix ----
    if feature_df is None or not isinstance(feature_df, pd.DataFrame) or len(feature_df) == 0:
        # Try to read from pkg.assets["strategy_discovery"]["feature_matrix"]
        pkg_assets = getattr(pkg, "assets", {}) or {}
        sd_assets  = pkg_assets.get("strategy_discovery") or {}
        feature_df = sd_assets.get("feature_matrix") if isinstance(sd_assets, dict) else None

    if feature_df is None or not isinstance(feature_df, pd.DataFrame) or len(feature_df) == 0:
        # Build on-the-fly from raw trades + bars_with_regime
        try:
            from .features import build_feature_matrix
            trades = getattr(pkg, "trades", None)
            if trades is not None and len(trades) > 0:
                # Pull audit_df if available
                audit_df = None
                pkg_assets2 = getattr(pkg, "assets", {}) or {}
                audit_assets = pkg_assets2.get("trade_pattern_audit") or {}
                if isinstance(audit_assets, dict):
                    audit_df = audit_assets.get("audit_df")
                feature_df = build_feature_matrix(
                    trades,
                    bars_with_regime=bars_with_regime,
                    audit_df=audit_df if isinstance(audit_df, pd.DataFrame) else None,
                )
            else:
                diag["issues"].append("no trades available")
                return _make_json_safe({"top_rules": [], "baseline": {}, "diagnostics": diag})
        except Exception as exc:
            diag["issues"].append(f"feature matrix build failed: {exc}")
            return _make_json_safe({"top_rules": [], "baseline": {}, "diagnostics": diag})

    # ---- resolve profit column ----
    if profit_col not in feature_df.columns:
        if "profit" in feature_df.columns:
            profit_col = "profit"
            diag["profit_col_used"] = profit_col
        else:
            diag["issues"].append(f"profit column '{profit_col}' not found")
            return _make_json_safe({"top_rules": [], "baseline": {}, "diagnostics": diag})

    # Guard: need minimum trades
    profit_series = pd.to_numeric(feature_df[profit_col], errors="coerce").dropna()
    if len(profit_series) < min_trades:
        diag["issues"].append(f"insufficient trades: {len(profit_series)} < {min_trades}")
        return _make_json_safe({"top_rules": [], "baseline": {}, "diagnostics": diag})

    # ---- baseline ----
    baseline = _compute_baseline(feature_df, profit_col)

    # ---- generate atoms ----
    atoms = _generate_atoms(feature_df, adx_thresh, ps_thresh)
    diag["n_atoms"] = len(atoms)

    if not atoms:
        diag["issues"].append("no condition atoms generated — feature matrix may be sparse")
        return _make_json_safe({"top_rules": [], "baseline": baseline, "diagnostics": diag})

    # ---- generate candidates ----
    candidates = _generate_candidates(atoms, max_depth=max_depth, max_candidates=max_cands)
    diag["n_candidates"] = len(candidates)

    # ---- evaluate candidates ----
    results: List[Dict[str, Any]] = []
    for conditions in candidates:
        mask = _apply_conjunction(feature_df, conditions)
        n_match = int(mask.sum())
        if n_match < min_trades:
            continue

        stats = _evaluate_rule(feature_df, mask, profit_col, baseline)
        if stats is None:
            continue

        results.append({
            "conditions":  [{"column": c.column, "op": c.op, "value": c.value,
                             "description": c.description()} for c in conditions],
            "rule_str":    " AND ".join(c.description() for c in conditions),
            "n_conditions": len(conditions),
            **stats,
        })

    diag["n_valid_candidates"] = len(results)

    # ---- rank ----
    results.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    top_rules = results[:top_n]
    for i, r in enumerate(top_rules, start=1):
        r["rank"] = i

    # ---- summary stats ----
    if results:
        best = results[0]
        diag["best_score"]        = best.get("score")
        diag["best_rule_str"]     = best.get("rule_str")
        diag["best_n_trades"]     = best.get("n_trades")
        diag["best_profit_factor"] = best.get("profit_factor")
        diag["best_win_rate"]     = best.get("win_rate")

    # ---- group top rules by dominant condition type ----
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for r in top_rules[:10]:
        first_cond_col = r["conditions"][0]["column"] if r["conditions"] else "unknown"
        # Bucket pat_* into "pattern"
        col_bucket = "pattern" if first_cond_col.startswith("pat_") else first_cond_col
        by_type.setdefault(col_bucket, []).append({
            "rule_str": r.get("rule_str"),
            "score":    r.get("score"),
            "pf":       r.get("profit_factor"),
            "wr":       r.get("win_rate"),
            "n_trades": r.get("n_trades"),
        })

    return _make_json_safe({
        "top_rules":  top_rules,
        "baseline":   baseline,
        "by_condition_type": by_type,
        "diagnostics": diag,
    })
