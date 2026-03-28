from __future__ import annotations

"""
Parameter Sensitivity Analysis
================================
Answers: "If I nudge this entry threshold by one unit, does the edge survive?"

For each numeric threshold condition in the top entry rules discovered by
entry_discovery.py, sweeps a ±grid of threshold values and measures how
PF / WR respond.  Rules whose edge is spread across many threshold values are
classified ROBUST; rules that peak at exactly one value are FRAGILE.

Algorithm
---------
1. Read top entry rules from pkg.metadata["derived"]["strategy_discovery"]["entry_discovery"].
2. For each condition with op in ("gte", "lte") and a numeric value, build a
   sweep grid: [v - 4Δ, ..., v - Δ, v, v + Δ, ..., v + 4Δ] where Δ is the
   natural step size for that column (auto-detected or configured).
3. For each point on the grid, apply the *same rule* but with the threshold
   replaced and evaluate PF / WR on the feature_df.
4. Compute:
   pf_range    — max(PF) - min(PF) across the sweep (lower = more robust)
   pf_slope    — linear slope of PF w.r.t. threshold (steeper = more fragile)
   peak_ratio  — PF at original threshold / mean PF across sweep
                 (close to 1.0 = flat = robust; >> 1.0 = peaked = fragile)
   sensitivity_score — 0–100 (0 = perfectly flat, 100 = extreme sensitivity)
5. Classify as "robust", "moderate", or "fragile".

Entry point: run_parameter_sensitivity(pkg, options, feature_df) → JSON-safe dict
Attaches to: discovery_block["parameter_sensitivity"]
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OPTIONS: Dict[str, Any] = {
    "enabled":         True,
    "top_n_rules":     5,         # how many top entry rules to analyse
    "n_steps":         4,         # sweep ±n_steps from original threshold
    "min_step_trades": 10,        # skip sweep points with < this many trades
    "profit_col":      "profit_net",
}

# Natural step sizes per column.  Columns not listed get auto-computed.
_DEFAULT_STEPS: Dict[str, float] = {
    "adx":           1.0,
    "pattern_score": 0.5,
    "atr":           0.05,    # expressed as fraction if percentile-normalised
}

# Thresholds for sensitivity classification
_ROBUST_PEAK_RATIO   = 1.15   # peak/mean < 1.15 → robust
_FRAGILE_PEAK_RATIO  = 1.40   # peak/mean > 1.40 → fragile  (else moderate)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_pf(profit: pd.Series) -> Optional[float]:
    w = float(profit[profit > 0].sum()) if len(profit[profit > 0]) else 0.0
    l = float(abs(profit[profit <= 0].sum())) if len(profit[profit <= 0]) else 0.0
    if l == 0:
        return None
    return round(w / l, 4)


def _safe_wr(profit: pd.Series) -> Optional[float]:
    n = len(profit)
    if n == 0:
        return None
    return round(float((profit > 0).sum() / n), 4)


def _auto_step(col: str, values: pd.Series) -> float:
    """Auto-detect a reasonable step size for a numeric column."""
    if col in _DEFAULT_STEPS:
        return _DEFAULT_STEPS[col]
    try:
        std = float(values.dropna().std())
        if std > 0:
            # Use ~5% of std, rounded to 1 sig-fig
            raw = std * 0.05
            mag = 10 ** np.floor(np.log10(max(abs(raw), 1e-9)))
            return round(raw / mag) * mag
        return 1.0
    except Exception:
        return 1.0


def _apply_conjunction(
    conditions: List[Any],   # List[Condition]
    df: pd.DataFrame,
) -> pd.Series:
    """AND of all conditions' boolean masks."""
    mask = pd.Series(True, index=df.index)
    for c in conditions:
        mask = mask & c.apply(df)
    return mask


def _sweep_threshold(
    conditions: List[Any],
    sweep_cond_idx: int,
    col: str,
    op: str,
    original_value: float,
    step: float,
    n_steps: int,
    feature_df: pd.DataFrame,
    profit_col: str,
    min_trades: int,
) -> List[Dict[str, Any]]:
    """
    Sweep threshold of conditions[sweep_cond_idx] across a grid.
    Returns list of {threshold, n_trades, pf, win_rate} dicts.
    """
    from .entry_discovery import Condition   # lazy import to avoid circularity

    results = []
    thresholds = [original_value + i * step for i in range(-n_steps, n_steps + 1)]

    for thr in thresholds:
        swept = list(conditions)
        swept[sweep_cond_idx] = Condition(col, op, thr)
        mask = _apply_conjunction(swept, feature_df)
        sub  = feature_df.loc[mask, profit_col].dropna()
        if len(sub) < min_trades:
            results.append({"threshold": round(thr, 6), "n_trades": int(mask.sum()),
                            "pf": None, "win_rate": None})
            continue
        results.append({
            "threshold": round(thr, 6),
            "n_trades":  int(mask.sum()),
            "pf":        _safe_pf(sub),
            "win_rate":  _safe_wr(sub),
        })
    return results


def _sensitivity_score(sweep: List[Dict[str, Any]], original_value: float) -> Dict[str, Any]:
    """
    Compute sensitivity metrics from a threshold sweep.

    Returns:
      pf_at_original, pf_mean, pf_range, pf_slope, peak_ratio,
      sensitivity_score (0–100), classification
    """
    valid = [s for s in sweep if s.get("pf") is not None]
    if len(valid) < 2:
        return {
            "pf_at_original": None, "pf_mean": None, "pf_range": None,
            "pf_slope": None, "peak_ratio": None,
            "sensitivity_score": None, "classification": "unknown",
        }

    thresholds = np.array([s["threshold"] for s in valid], dtype=float)
    pf_vals    = np.array([s["pf"] for s in valid], dtype=float)

    # Find PF at (or nearest to) original threshold
    nearest_idx = int(np.argmin(np.abs(thresholds - original_value)))
    pf_at_orig  = float(pf_vals[nearest_idx])
    pf_mean     = float(np.mean(pf_vals))
    pf_range    = float(np.max(pf_vals) - np.min(pf_vals))

    # Linear slope: how fast does PF change per unit threshold?
    if len(thresholds) >= 2:
        try:
            slope = float(np.polyfit(thresholds, pf_vals, 1)[0])
        except Exception:
            slope = 0.0
    else:
        slope = 0.0

    # Peak ratio: is the original threshold at the maximum?
    peak_ratio = pf_at_orig / pf_mean if pf_mean > 0 else 1.0
    peak_ratio = float(np.clip(peak_ratio, 0.0, 10.0))

    # Sensitivity score 0–100: combination of normalised range + abs slope
    range_score = float(np.clip(pf_range / max(pf_mean, 0.01) / 0.50, 0.0, 1.0)) * 60.0
    ratio_score = float(np.clip((peak_ratio - 1.0) / 0.50, 0.0, 1.0)) * 40.0
    sens_score  = int(round(range_score + ratio_score))

    # Classify
    if peak_ratio < _ROBUST_PEAK_RATIO:
        cls = "robust"
    elif peak_ratio > _FRAGILE_PEAK_RATIO:
        cls = "fragile"
    else:
        cls = "moderate"

    return {
        "pf_at_original":    round(pf_at_orig, 4),
        "pf_mean":           round(pf_mean, 4),
        "pf_range":          round(pf_range, 4),
        "pf_slope":          round(slope, 6),
        "peak_ratio":        round(peak_ratio, 3),
        "sensitivity_score": sens_score,
        "classification":    cls,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_parameter_sensitivity(
    pkg: Any,
    options: Dict[str, Any],
    *,
    feature_df: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Analyse threshold sensitivity for numeric conditions in top entry rules.

    Returns JSON-safe dict:
      rule_sweeps   : [{rule_id, rule_desc, conditions, sweeps: [{col, op, original_value,
                        step, threshold_sweep, ...sensitivity_metrics}]}]
      summary       : {n_rules_analysed, n_robust, n_moderate, n_fragile}
      diagnostics   : {n_trades, profit_col_used, top_n_rules, issues}
    """
    opts: Dict[str, Any] = {**DEFAULT_OPTIONS, **(options or {})}

    if not opts.get("enabled", True):
        return {"skipped": True}

    top_n_rules    = int(opts.get("top_n_rules", 5))
    n_steps        = int(opts.get("n_steps", 4))
    min_trad       = int(opts.get("min_step_trades", 10))
    profit_col     = str(opts.get("profit_col", "profit_net"))

    diag: Dict[str, Any] = {
        "n_trades":        0,
        "profit_col_used": profit_col,
        "top_n_rules":     top_n_rules,
        "issues":          [],
    }

    # ---- resolve feature_df ----
    if feature_df is None or not isinstance(feature_df, pd.DataFrame) or len(feature_df) == 0:
        pkg_assets = getattr(pkg, "assets", {}) or {}
        sd_assets  = pkg_assets.get("strategy_discovery") or {}
        feature_df = sd_assets.get("feature_matrix") if isinstance(sd_assets, dict) else None

    if feature_df is None or not isinstance(feature_df, pd.DataFrame) or len(feature_df) == 0:
        diag["issues"].append("feature_df not available")
        return _empty(diag)

    if profit_col not in feature_df.columns:
        if "profit" in feature_df.columns:
            profit_col = "profit"
            diag["profit_col_used"] = profit_col
        else:
            diag["issues"].append(f"profit column '{profit_col}' not found in feature_df")
            return _empty(diag)

    diag["n_trades"] = int(len(feature_df))

    # ---- read top entry rules ----
    meta = getattr(pkg, "metadata", None)
    if not isinstance(meta, dict):
        diag["issues"].append("no metadata on package")
        return _empty(diag)

    sd = meta.get("derived", {}).get("strategy_discovery") or {}
    entry_disc = sd.get("entry_discovery") or {}
    top_rules  = entry_disc.get("top_rules") or []

    if not top_rules:
        diag["issues"].append("no entry rules found — run entry_discovery first")
        return _empty(diag)

    top_rules = top_rules[:top_n_rules]

    # ---- import Condition ----
    try:
        from .entry_discovery import Condition
    except ImportError as exc:
        diag["issues"].append(f"cannot import Condition: {exc}")
        return _empty(diag)

    # ---- sweep each rule ----
    rule_sweeps: List[Dict[str, Any]] = []
    n_robust = n_moderate = n_fragile = 0

    for rule in top_rules:
        rule_conditions_raw = rule.get("conditions") or []
        rule_desc = rule.get("description") or rule.get("rule_id") or "?"

        # Reconstruct Condition objects from JSON-serialised rule
        parsed_conds: List[Condition] = []
        for rc in rule_conditions_raw:
            try:
                parsed_conds.append(Condition(
                    column=str(rc["column"]),
                    op=str(rc["op"]),
                    value=rc["value"],
                ))
            except Exception:
                continue

        if not parsed_conds:
            continue

        # Find numeric threshold conditions eligible for sweep
        sweeps_for_rule: List[Dict[str, Any]] = []
        for cond_idx, cond in enumerate(parsed_conds):
            if cond.op not in ("gte", "lte"):
                continue
            try:
                original_value = float(cond.value)
            except (TypeError, ValueError):
                continue

            # Determine step size
            col_vals = pd.to_numeric(feature_df[cond.column], errors="coerce") \
                if cond.column in feature_df.columns else pd.Series(dtype=float)
            step = _auto_step(cond.column, col_vals)

            # Run sweep
            thr_sweep = _sweep_threshold(
                conditions=parsed_conds,
                sweep_cond_idx=cond_idx,
                col=cond.column,
                op=cond.op,
                original_value=original_value,
                step=step,
                n_steps=n_steps,
                feature_df=feature_df,
                profit_col=profit_col,
                min_trades=min_trad,
            )

            sens = _sensitivity_score(thr_sweep, original_value)

            cls = sens.get("classification", "unknown")
            if cls == "robust":
                n_robust += 1
            elif cls == "fragile":
                n_fragile += 1
            elif cls == "moderate":
                n_moderate += 1

            sweeps_for_rule.append({
                "column":         cond.column,
                "op":             cond.op,
                "original_value": original_value,
                "step":           step,
                "threshold_sweep": thr_sweep,
                **sens,
            })

        if not sweeps_for_rule:
            # Rule has no numeric conditions — still report it
            sweeps_for_rule.append({"note": "no numeric threshold conditions in this rule"})

        rule_sweeps.append({
            "rule_id":    rule.get("rule_id", str(len(rule_sweeps))),
            "rule_desc":  rule_desc,
            "score":      rule.get("score"),
            "pf":         rule.get("pf"),
            "wr":         rule.get("win_rate"),
            "conditions": rule_conditions_raw,
            "sweeps":     sweeps_for_rule,
        })

    n_analysed = sum(
        1 for r in rule_sweeps
        for s in r["sweeps"]
        if s.get("classification") in ("robust", "moderate", "fragile")
    )

    return _make_json_safe({
        "rule_sweeps": rule_sweeps,
        "summary": {
            "n_rules_analysed":  len(rule_sweeps),
            "n_numeric_sweeps":  n_analysed,
            "n_robust":          n_robust,
            "n_moderate":        n_moderate,
            "n_fragile":         n_fragile,
        },
        "diagnostics": diag,
    })


def _empty(diag: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "rule_sweeps": [],
        "summary": {"n_rules_analysed": 0, "n_numeric_sweeps": 0,
                    "n_robust": 0, "n_moderate": 0, "n_fragile": 0},
        "diagnostics": diag,
    }


# ---------------------------------------------------------------------------
# JSON safety
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
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, float):
        return None if not np.isfinite(obj) else obj
    return obj
