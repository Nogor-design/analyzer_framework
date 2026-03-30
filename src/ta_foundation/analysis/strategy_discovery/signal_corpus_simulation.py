from __future__ import annotations

"""
Signal Corpus P&L Simulation
==============================
Simulates equity curves for discovered signal rules by applying their
optimal stop/target parameters to the full signal corpus.

Unlike executed-trade analysis, every signal firing that matches a rule
is treated as a simulated trade — this removes execution bias completely.

Entry point
-----------
  run_signal_corpus_simulation(signal_feature_df, signal_rules, exit_sweep, options) -> dict

Returns JSON-safe dict:
  rule_simulations   : per-rule P&L stats + equity curve
  combined_sim       : combined (non-overlapping) equity curve across all rules
  diagnostics        : counts and issues

Simulation model (per signal matching a rule):
  pnl_i = +target_ticks  if mfe_ticks >= target AND |mae_ticks| < stop
  pnl_i = -stop_ticks    if |mae_ticks| >= stop
  pnl_i = ret_ticks      otherwise (horizon exit)

Equity curve starts at 0 and accumulates pnl in ticks.
Drawdown computed on ticks equity curve.
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
    "max_rules": 6,           # rules to simulate (sorted by best_score in sweep)
    "min_signals": 20,        # minimum signals per rule to simulate
    "equity_start": 0.0,      # starting equity in ticks
}


# ---------------------------------------------------------------------------
# Simulation kernel
# ---------------------------------------------------------------------------

def _simulate_rule(
    sub_df: pd.DataFrame,
    stop_ticks: int,
    target_ticks: int,
) -> pd.Series:
    """
    Simulate P&L series (in ticks) for each signal in sub_df under a fixed
    stop/target regime.  Returns a pd.Series aligned to sub_df.index,
    sorted by dt (bar time) for the equity curve.
    """
    if "dt" in sub_df.columns:
        sub_df = sub_df.sort_values("dt")

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
        pnl = ret.clip(-stop_ticks, target_ticks)

    return pnl


def _compute_stats(pnl: pd.Series) -> Dict[str, Any]:
    """Summary statistics for a P&L series (in ticks)."""
    n = len(pnl)
    if n == 0:
        return {"n": 0}

    winners = pnl[pnl > 0]
    losers  = pnl[pnl < 0]
    gross_profit = float(winners.sum())
    gross_loss   = float(losers.sum())

    pf = (gross_profit / abs(gross_loss)) if gross_loss != 0 else (
        999.0 if gross_profit > 0 else 0.0
    )
    win_rate = float(len(winners) / n)
    avg_ticks = float(pnl.mean())
    total_ticks = float(pnl.sum())

    # Max drawdown on cumulative equity curve
    equity = pnl.cumsum()
    running_max = equity.cummax()
    dd = equity - running_max
    max_dd = float(dd.min())

    return {
        "n": n,
        "win_rate": round(win_rate, 4),
        "avg_ticks": round(avg_ticks, 3),
        "total_ticks": round(total_ticks, 1),
        "profit_factor": round(pf, 3),
        "max_drawdown_ticks": round(max_dd, 1),
        "n_wins": int(len(winners)),
        "n_losses": int(len(losers)),
        "gross_profit_ticks": round(gross_profit, 1),
        "gross_loss_ticks": round(gross_loss, 1),
    }


def _equity_curve_points(
    pnl: pd.Series,
    sub_df: pd.DataFrame,
    equity_start: float,
) -> List[Dict[str, Any]]:
    """
    Build a compact equity curve as a list of {date, equity, pnl} dicts.
    Downsampled to at most 500 points for JSON size.
    """
    if len(pnl) == 0:
        return []

    equity = pnl.cumsum() + equity_start
    dates = []
    if "dt" in sub_df.columns:
        dt_col = pd.to_datetime(sub_df["dt"], errors="coerce")
        dates = [str(d)[:10] if pd.notna(d) else "" for d in dt_col.values]
    else:
        dates = [""] * len(pnl)

    # Downsample if too many points
    step = max(1, len(pnl) // 400)
    pts = []
    for i in range(0, len(pnl), step):
        pts.append({
            "i": i,
            "date": dates[i] if i < len(dates) else "",
            "equity": round(float(equity.iloc[i]), 2),
            "pnl": round(float(pnl.iloc[i]), 2),
        })
    # Always include last point
    if pts and pts[-1]["i"] != len(pnl) - 1:
        pts.append({
            "i": len(pnl) - 1,
            "date": dates[-1] if dates else "",
            "equity": round(float(equity.iloc[-1]), 2),
            "pnl": round(float(pnl.iloc[-1]), 2),
        })
    return pts


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_signal_corpus_simulation(
    signal_feature_df: pd.DataFrame,
    signal_rules: List[Dict[str, Any]],
    exit_sweep: Dict[str, Any],
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Simulate equity curves for each signal rule using corpus-optimised exit params.

    Parameters
    ----------
    signal_feature_df : from entry_pattern_bridge (one row per signal firing)
    signal_rules      : from signal_entry_discovery.top_signal_rules
    exit_sweep        : from signal_exit_sweep result dict
    options           : signal_corpus_simulation config block

    Returns
    -------
    JSON-safe dict with rule_simulations, combined_sim, diagnostics
    """
    opts: Dict[str, Any] = {**DEFAULT_OPTIONS, **(options or {})}

    diag: Dict[str, Any] = {
        "n_signals": 0,
        "n_rules": 0,
        "n_rules_simulated": 0,
        "issues": [],
    }

    empty = _make_json_safe({
        "rule_simulations": [],
        "combined_sim": {},
        "diagnostics": diag,
    })

    if not opts.get("enabled", True):
        diag["issues"].append("signal_corpus_simulation disabled")
        return empty

    if signal_feature_df is None or not isinstance(signal_feature_df, pd.DataFrame) or len(signal_feature_df) == 0:
        diag["issues"].append("no signal_feature_df")
        return empty

    if "ret_ticks" not in signal_feature_df.columns:
        diag["issues"].append("ret_ticks missing")
        return empty

    n_total = len(signal_feature_df)
    diag["n_signals"] = n_total

    max_rules   = int(opts.get("max_rules", 6))
    min_signals = int(opts.get("min_signals", 20))
    equity_start = float(opts.get("equity_start", 0.0))

    # Build per-rule best stop/target lookup from exit sweep
    per_rule_sweep = (exit_sweep or {}).get("per_rule_sweep") or []
    sweep_lookup: Dict[str, Dict[str, Any]] = {
        r.get("rule_str", ""): r for r in per_rule_sweep
    }
    overall_best = (exit_sweep or {}).get("overall_best") or {}
    fallback_stop   = int(overall_best.get("stop") or 60)
    fallback_target = int(overall_best.get("target") or 90)

    rules_to_sim = (signal_rules or [])[:max_rules]
    diag["n_rules"] = len(rules_to_sim)

    rule_simulations: List[Dict[str, Any]] = []

    # Sort df by time once
    if "dt" in signal_feature_df.columns:
        df = signal_feature_df.copy()
        df["_dt_sort"] = pd.to_datetime(df["dt"], errors="coerce")
        df = df.sort_values("_dt_sort").reset_index(drop=True)
    else:
        df = signal_feature_df.copy()

    for rule in rules_to_sim:
        rule_str = str(rule.get("rule_str") or "")

        # Apply rule conditions
        mask = pd.Series(True, index=df.index)
        for cond_dict in (rule.get("conditions") or []):
            try:
                cond = Condition(cond_dict["column"], cond_dict["op"], cond_dict["value"])
                mask = mask & cond.apply(df)
            except Exception:
                pass

        sub = df[mask]
        n_match = len(sub)

        if n_match < min_signals:
            continue

        # Get best stop/target for this rule
        sw = sweep_lookup.get(rule_str, {})
        stop_ticks   = int(sw.get("best_stop")   or fallback_stop)
        target_ticks = int(sw.get("best_target") or fallback_target)
        best_rr      = sw.get("best_rr") or round(target_ticks / max(stop_ticks, 1), 2)

        pnl = _simulate_rule(sub, stop_ticks, target_ticks)
        stats = _compute_stats(pnl)
        curve = _equity_curve_points(pnl, sub, equity_start)

        # Date range
        date_start = ""
        date_end   = ""
        if "dt" in sub.columns:
            dt_col = pd.to_datetime(sub["dt"], errors="coerce").dropna()
            if len(dt_col) > 0:
                date_start = str(dt_col.min())[:10]
                date_end   = str(dt_col.max())[:10]

        rule_simulations.append({
            "rule_str": rule_str,
            "stop_ticks": stop_ticks,
            "target_ticks": target_ticks,
            "rr": round(float(best_rr), 2),
            "date_start": date_start,
            "date_end": date_end,
            **stats,
            "equity_curve": curve,
        })
        diag["n_rules_simulated"] += 1

    # Combined simulation: all signals matching ANY of the top rules
    # Deduplicate by signal_id if available to avoid double-counting overlapping rules
    combined_sim: Dict[str, Any] = {}
    if rule_simulations:
        try:
            combined_mask = pd.Series(False, index=df.index)
            for rule in rules_to_sim[:len(rule_simulations)]:
                rule_mask = pd.Series(True, index=df.index)
                for cond_dict in (rule.get("conditions") or []):
                    try:
                        cond = Condition(cond_dict["column"], cond_dict["op"], cond_dict["value"])
                        rule_mask = rule_mask & cond.apply(df)
                    except Exception:
                        pass
                combined_mask = combined_mask | rule_mask

            combined_sub = df[combined_mask]

            # Deduplicate by signal_id if present
            if "signal_id" in combined_sub.columns:
                combined_sub = combined_sub.drop_duplicates(subset=["signal_id"])

            if len(combined_sub) >= min_signals:
                comb_pnl = _simulate_rule(combined_sub, fallback_stop, fallback_target)
                comb_stats = _compute_stats(comb_pnl)
                comb_curve = _equity_curve_points(comb_pnl, combined_sub, equity_start)
                combined_sim = {
                    "n_signals": len(combined_sub),
                    "stop_ticks": fallback_stop,
                    "target_ticks": fallback_target,
                    **comb_stats,
                    "equity_curve": comb_curve,
                }
        except Exception as exc:
            diag["issues"].append(f"combined simulation failed: {exc}")

    return _make_json_safe({
        "rule_simulations": rule_simulations,
        "combined_sim": combined_sim,
        "diagnostics": diag,
    })
