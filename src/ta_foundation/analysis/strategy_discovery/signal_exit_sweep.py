from __future__ import annotations

"""
Signal Corpus Exit Sweep
========================
Sweeps fixed stop / target combinations against the pattern signal corpus to
find exit parameters that maximise win-rate and profit factor on unexecuted
signal firings.

Unlike exit_discovery.py (which sweeps executed trades), this module operates
entirely on ``signal_feature_df`` — the full corpus of pattern signal firings
including those never executed by any uploaded strategy.

A signal firing has:
  - ret_ticks   : forward return at the primary horizon (20 bars by default)
  - mfe_ticks   : max favourable excursion over the horizon window
  - mae_ticks   : max adverse excursion (typically negative) over the horizon

Simulation logic (per signal, per stop/target pair):
  1. If |mae_ticks| >= stop_ticks  → stop hit → pnl = -stop_ticks
  2. Elif mfe_ticks >= target_ticks → target hit → pnl = +target_ticks
  3. Else                           → horizon exit → pnl = ret_ticks

This is an optimistic upper-bound simulation (ignores path order uncertainty),
but is widely used in MAE/MFE sweep analysis and gives a directionally correct
ranking of (stop, target) parameter pairs.

Entry point
-----------
  run_signal_exit_sweep(signal_feature_df, signal_rules, options) -> dict

Returns JSON-safe dict:
  per_rule_sweep    : list of {rule_str, best_stop, best_target, sweep_grid, score}
  overall_best      : {stop, target, win_rate, avg_ticks, profit_factor}
  baseline_params   : MAE/MFE percentile-derived stop/target (no rule filter)
  diagnostics       : {n_signals, n_rules, issues}
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .entry_discovery import Condition, _make_json_safe


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OPTIONS: Dict[str, Any] = {
    "enabled": True,
    # Stop ticks grid (absolute ticks)
    "stop_grid": [8, 12, 16, 20, 24, 32, 48, 64],
    # Target ticks grid
    "target_grid": [8, 12, 16, 20, 24, 32, 48, 64, 80, 96],
    # Minimum signals per rule/grid cell to include the result
    "min_signals": 20,
    # Top N (stop, target) combos to keep per rule
    "top_n_per_rule": 5,
    # Ranking weight: profit_factor vs win_rate vs avg_ticks
    "weight_pf": 0.45,
    "weight_wr": 0.35,
    "weight_avg": 0.20,
    # Minimum RR ratio to include a combo (target / stop)
    "min_rr": 1.0,
    # Max number of rules to sweep (keeps runtime reasonable)
    "max_rules": 10,
}


# ---------------------------------------------------------------------------
# Core simulation
# ---------------------------------------------------------------------------

def _simulate_fixed_exit(
    sub_df: pd.DataFrame,
    stop_ticks: int,
    target_ticks: int,
) -> Optional[Dict[str, Any]]:
    """
    Simulate a fixed stop / target exit on a subset of signal firings.

    Uses mfe_ticks and mae_ticks if available.  Falls back to ret_ticks only
    (in which case the sim uses a horizon-only approximation).

    Returns None if sub_df is empty.
    """
    n = len(sub_df)
    if n == 0:
        return None

    ret = pd.to_numeric(
        sub_df["ret_ticks"] if "ret_ticks" in sub_df.columns else pd.Series(dtype=float),
        errors="coerce",
    ).fillna(0.0)

    has_mfe = "mfe_ticks" in sub_df.columns
    has_mae = "mae_ticks" in sub_df.columns

    if has_mfe and has_mae:
        mfe = pd.to_numeric(sub_df["mfe_ticks"], errors="coerce").fillna(0.0)
        mae = pd.to_numeric(sub_df["mae_ticks"], errors="coerce").fillna(0.0)
        mae_abs = mae.abs()

        hit_stop   = mae_abs >= stop_ticks
        hit_target = (~hit_stop) & (mfe >= target_ticks)
        horizon    = ~hit_stop & ~hit_target

        pnl = pd.Series(0.0, index=sub_df.index)
        pnl[hit_stop]   = float(-stop_ticks)
        pnl[hit_target] = float(target_ticks)
        pnl[horizon]    = ret[horizon]
    else:
        # Fallback: threshold on ret_ticks only (noisier but still useful)
        pnl = ret.clip(-stop_ticks, target_ticks)

    winners = pnl[pnl > 0]
    losers  = pnl[pnl < 0]
    gross_profit = float(winners.sum())
    gross_loss   = float(losers.sum())

    pf = (gross_profit / abs(gross_loss)) if gross_loss != 0 else (
        999.0 if gross_profit > 0 else 0.0
    )
    win_rate = float(len(winners) / n) if n > 0 else 0.0
    avg_ticks = float(pnl.mean()) if n > 0 else 0.0

    return {
        "n": n,
        "win_rate": round(win_rate, 4),
        "avg_ticks": round(avg_ticks, 3),
        "profit_factor": round(pf, 3),
        "n_wins": int(len(winners)),
        "n_losses": int(len(losers)),
    }


def _score_combo(
    stats: Dict[str, Any],
    weight_pf: float,
    weight_wr: float,
    weight_avg: float,
) -> float:
    """Composite score for a (stop, target) combo."""
    pf  = min(float(stats.get("profit_factor") or 0.0), 5.0)
    wr  = float(stats.get("win_rate") or 0.0)
    avg = float(stats.get("avg_ticks") or 0.0)

    # Normalise to [0, 1]
    pf_norm  = min(max(pf - 1.0, 0.0) / 4.0, 1.0)    # 1.0 → 0, 5.0 → 1.0
    wr_norm  = max(wr, 0.0)
    avg_norm = min(max(avg / 20.0, 0.0), 1.0)          # 20 ticks → full score

    return weight_pf * pf_norm + weight_wr * wr_norm + weight_avg * avg_norm


# ---------------------------------------------------------------------------
# Baseline (no rule filter)
# ---------------------------------------------------------------------------

def _compute_baseline_params(
    signal_feature_df: pd.DataFrame,
    stop_grid: List[int],
    target_grid: List[int],
    min_signals: int,
    weight_pf: float,
    weight_wr: float,
    weight_avg: float,
    min_rr: float,
) -> Dict[str, Any]:
    """
    Sweep the full corpus (no rule filter) and return percentile-derived
    stop/target plus the best grid combo for baseline comparison.
    """
    result: Dict[str, Any] = {}

    # Percentile-derived (MAE p75 → stop, MFE p50 → target)
    if "mae_ticks" in signal_feature_df.columns:
        mae_abs = pd.to_numeric(signal_feature_df["mae_ticks"], errors="coerce").abs().dropna()
        if len(mae_abs) >= 5:
            result["mae_p50"]  = round(float(mae_abs.quantile(0.50)), 1)
            result["mae_p75"]  = round(float(mae_abs.quantile(0.75)), 1)
            result["stop_pct_ticks"] = int(round(mae_abs.quantile(0.75)))

    if "mfe_ticks" in signal_feature_df.columns:
        mfe = pd.to_numeric(signal_feature_df["mfe_ticks"], errors="coerce").dropna()
        mfe_pos = mfe[mfe > 0]
        if len(mfe_pos) >= 5:
            result["mfe_p50"] = round(float(mfe_pos.quantile(0.50)), 1)
            result["mfe_p75"] = round(float(mfe_pos.quantile(0.75)), 1)
            result["target_pct_ticks"] = int(round(mfe_pos.quantile(0.50)))

    # Sweep best grid combo
    best_combo = None
    best_score = -1.0
    for s in stop_grid:
        for t in target_grid:
            if t / max(s, 1) < min_rr:
                continue
            stats = _simulate_fixed_exit(signal_feature_df, s, t)
            if stats is None or stats["n"] < min_signals:
                continue
            sc = _score_combo(stats, weight_pf, weight_wr, weight_avg)
            if sc > best_score:
                best_score = sc
                best_combo = {"stop": s, "target": t, "score": round(sc, 4), **stats}

    if best_combo:
        result["best_grid_combo"] = best_combo

    return result


# ---------------------------------------------------------------------------
# Per-rule sweep
# ---------------------------------------------------------------------------

def _apply_rule_mask(
    signal_feature_df: pd.DataFrame,
    rule: Dict[str, Any],
) -> pd.Series:
    """Apply rule conditions to signal_feature_df and return boolean mask."""
    mask = pd.Series(True, index=signal_feature_df.index)
    for cond_dict in (rule.get("conditions") or []):
        try:
            cond = Condition(
                cond_dict["column"],
                cond_dict["op"],
                cond_dict["value"],
            )
            mask = mask & cond.apply(signal_feature_df)
        except Exception:
            pass
    return mask


def _sweep_rule(
    signal_feature_df: pd.DataFrame,
    rule: Dict[str, Any],
    stop_grid: List[int],
    target_grid: List[int],
    min_signals: int,
    top_n: int,
    weight_pf: float,
    weight_wr: float,
    weight_avg: float,
    min_rr: float,
) -> Optional[Dict[str, Any]]:
    """
    Sweep all (stop, target) grid combinations for signals matching this rule.
    Returns None if the rule selects too few signals.
    """
    mask = _apply_rule_mask(signal_feature_df, rule)
    sub = signal_feature_df[mask]
    n_rule = len(sub)

    if n_rule < min_signals:
        return None

    grid: List[Dict[str, Any]] = []
    for s in stop_grid:
        for t in target_grid:
            rr = t / max(s, 1)
            if rr < min_rr:
                continue
            stats = _simulate_fixed_exit(sub, s, t)
            if stats is None or stats["n"] < min_signals:
                continue
            sc = _score_combo(stats, weight_pf, weight_wr, weight_avg)
            grid.append({
                "stop": s,
                "target": t,
                "rr": round(rr, 2),
                "score": round(sc, 4),
                **stats,
            })

    if not grid:
        return None

    grid.sort(key=lambda x: x["score"], reverse=True)
    best = grid[0]

    return {
        "rule_str": rule.get("rule_str", ""),
        "n_signals": n_rule,
        "best_stop": best["stop"],
        "best_target": best["target"],
        "best_rr": best.get("rr"),
        "best_win_rate": best.get("win_rate"),
        "best_avg_ticks": best.get("avg_ticks"),
        "best_pf": best.get("profit_factor"),
        "best_score": best.get("score"),
        "top_combos": grid[:top_n],
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_signal_exit_sweep(
    signal_feature_df: pd.DataFrame,
    signal_rules: List[Dict[str, Any]],
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Sweep stop / target combinations against the signal corpus.

    Parameters
    ----------
    signal_feature_df : from entry_pattern_bridge.build_signal_feature_matrix()
                        Must have ret_ticks column; mfe_ticks / mae_ticks improve accuracy.
    signal_rules      : list of rule dicts from signal_entry_discovery.top_signal_rules
    options           : signal_exit_sweep config block

    Returns
    -------
    JSON-safe dict:
      per_rule_sweep  : per-rule best stop/target + top grid combos
      overall_best    : best single (stop, target) across all rules
      baseline_params : percentile-derived stop/target + best grid combo (no rule filter)
      diagnostics     : counts and issues
    """
    opts: Dict[str, Any] = {**DEFAULT_OPTIONS, **(options or {})}

    diag: Dict[str, Any] = {
        "n_signals": 0,
        "n_rules_swept": 0,
        "n_rules_found": 0,
        "issues": [],
    }

    empty = _make_json_safe({
        "per_rule_sweep": [],
        "overall_best": {},
        "baseline_params": {},
        "diagnostics": diag,
    })

    if not opts.get("enabled", True):
        diag["issues"].append("signal_exit_sweep disabled")
        return empty

    if signal_feature_df is None or not isinstance(signal_feature_df, pd.DataFrame) or len(signal_feature_df) == 0:
        diag["issues"].append("no signal_feature_df provided")
        return empty

    if "ret_ticks" not in signal_feature_df.columns:
        diag["issues"].append("ret_ticks column missing from signal_feature_df")
        return empty

    n_total = len(signal_feature_df)
    diag["n_signals"] = n_total

    stop_grid   = [int(x) for x in (opts.get("stop_grid") or DEFAULT_OPTIONS["stop_grid"])]
    target_grid = [int(x) for x in (opts.get("target_grid") or DEFAULT_OPTIONS["target_grid"])]
    min_signals = int(opts.get("min_signals", 20))
    top_n       = int(opts.get("top_n_per_rule", 5))
    weight_pf   = float(opts.get("weight_pf", 0.45))
    weight_wr   = float(opts.get("weight_wr", 0.35))
    weight_avg  = float(opts.get("weight_avg", 0.20))
    min_rr      = float(opts.get("min_rr", 1.0))
    max_rules   = int(opts.get("max_rules", 10))

    # Baseline (no rule filter)
    baseline_params = _compute_baseline_params(
        signal_feature_df, stop_grid, target_grid, min_signals,
        weight_pf, weight_wr, weight_avg, min_rr,
    )

    # Per-rule sweep
    rules_to_sweep = (signal_rules or [])[:max_rules]
    diag["n_rules_swept"] = len(rules_to_sweep)

    per_rule: List[Dict[str, Any]] = []
    for rule in rules_to_sweep:
        r_result = _sweep_rule(
            signal_feature_df, rule, stop_grid, target_grid,
            min_signals, top_n, weight_pf, weight_wr, weight_avg, min_rr,
        )
        if r_result is not None:
            per_rule.append(r_result)

    diag["n_rules_found"] = len(per_rule)

    # Best overall: pick the (stop, target) most often at top of per-rule grids
    # weighted by the rule's signal count
    combo_votes: Dict[Tuple[int, int], float] = {}
    combo_stats: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}

    for r in per_rule:
        n_sig = float(r.get("n_signals") or 1)
        for combo in (r.get("top_combos") or [])[:3]:
            key = (int(combo.get("stop", 0)), int(combo.get("target", 0)))
            w = float(combo.get("score", 0.0)) * n_sig
            combo_votes[key] = combo_votes.get(key, 0.0) + w
            combo_stats.setdefault(key, []).append(combo)

    overall_best: Dict[str, Any] = {}
    if combo_votes:
        best_key = max(combo_votes, key=lambda k: combo_votes[k])
        best_s, best_t = best_key
        # Average the stats across rules that voted for this combo
        stats_list = combo_stats.get(best_key, [])
        avg_wr  = float(np.mean([x.get("win_rate", 0) for x in stats_list]))
        avg_avg = float(np.mean([x.get("avg_ticks", 0) for x in stats_list]))
        avg_pf  = float(np.mean([x.get("profit_factor", 0) for x in stats_list]))
        overall_best = {
            "stop": best_s,
            "target": best_t,
            "rr": round(best_t / max(best_s, 1), 2),
            "n_rules_voting": len(stats_list),
            "avg_win_rate": round(avg_wr, 4),
            "avg_avg_ticks": round(avg_avg, 3),
            "avg_profit_factor": round(avg_pf, 3),
        }
    elif baseline_params.get("best_grid_combo"):
        # Fall back to baseline best
        bc = baseline_params["best_grid_combo"]
        overall_best = {
            "stop": bc.get("stop"),
            "target": bc.get("target"),
            "rr": round(bc.get("target", 0) / max(bc.get("stop", 1), 1), 2),
            "n_rules_voting": 0,
            "source": "baseline_fallback",
        }

    return _make_json_safe({
        "per_rule_sweep": per_rule,
        "overall_best": overall_best,
        "baseline_params": baseline_params,
        "diagnostics": diag,
    })
