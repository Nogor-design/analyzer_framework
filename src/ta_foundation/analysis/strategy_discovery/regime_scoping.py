from __future__ import annotations

"""
Regime-Scoped Candidate Discovery (step 3)
===========================================
The robustness gates ask "does this edge hold *across every regime*". That is
the right question for a durable edge, but it is the wrong question for
*discovery*: a real edge is usually conditional — a pattern that prints money in
a trending tape and bleeds in a range. Demanding cross-regime robustness throws
that candidate away. The per-candidate ``regime_breakdown`` already computed in
the sweep is a *report*; this module turns it into a *selection* mechanism.

Approach (post-hoc, downstream of the sweep — mirrors honest_execution and
trial_budget):

  1. Label each already-realised trade with the market regime in force at its
     entry (merge_asof backward against ``bars_with_regime``).
  2. Re-price each regime's trade subset under the honest fill model and apply
     the same absolute survival gate (``honest_execution``).
  3. The regimes that clear the gate are the candidate's *edge regimes*.
  4. Dual-track label:
       durable        — every regime tested clears the gate (no scoping needed).
       regime-limited — a strict subset clears it; emit a scoped variant that
                        trades only in those regimes.
       none           — no regime clears it.
  5. Re-validate the scoped variant honestly on the pooled edge-regime trades.

Selection bias: picking the best of N regimes is N extra trials. This module
reports ``n_regimes_evaluated`` so the caller can add it to the step-2
``trial_budget`` (``within_run_trials``) — hunting the best regime without that
correction is just overfitting on a smaller axis.

Scope: this is a haircut-and-partition on realised trades, not a re-simulation.
It cannot recover an edge that the sweep never sampled in a given regime, and a
regime with too few trades is reported as un-judgeable, not as a failure.

All outputs are JSON-safe — no DataFrames in the returned dict.
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ta_foundation.analysis.strategy_discovery.honest_execution import apply_honest_execution


DEFAULT_REGIME_SCOPING_CONFIG: Dict[str, Any] = {
    "enabled": True,
    # Which regime dimension to scope on. ``regime`` is the primary
    # trend_up / trend_down / range label; ``vol_regime`` and
    # ``trend_direction`` are also available on bars_with_regime.
    "regime_column": "regime",
    # A regime needs at least this many trades to be treated as a real
    # hypothesis. Below it the regime is un-judgeable, not failed, and is not
    # counted as a trial.
    "min_trades_per_regime": 30,
    # Need at least this many edge regimes for the candidate to pass.
    "min_edge_regimes": 1,
    # Merged over DEFAULT_HONEST_EXECUTION_CONFIG for the per-regime re-price.
    "honest_execution": {},
}


def _to_naive_utc(s: pd.Series) -> pd.Series:
    """Strip timezone for merge_asof: convert to tz-naive UTC."""
    s = pd.to_datetime(s)
    if s.dt.tz is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return s


def _label_trades_by_regime(
    trades: pd.DataFrame,
    bars_with_regime: pd.DataFrame,
    regime_column: str,
) -> pd.DataFrame:
    """Return ``trades`` with a ``_regime`` column joined by entry_time.

    Uses a backward merge_asof: each trade gets the regime of the most recent
    bar at or before its entry.
    """
    trades_work = trades.copy()
    trades_work["_entry_utc"] = _to_naive_utc(pd.to_datetime(trades_work["entry_time"]))
    trades_work = trades_work.sort_values("_entry_utc").reset_index(drop=True)

    bars_work = bars_with_regime[["dt", regime_column]].copy()
    bars_work["_dt_utc"] = _to_naive_utc(pd.to_datetime(bars_work["dt"]))
    bars_work = bars_work.sort_values("_dt_utc").reset_index(drop=True)

    merged = pd.merge_asof(
        trades_work,
        bars_work[["_dt_utc", regime_column]],
        left_on="_entry_utc",
        right_on="_dt_utc",
        direction="backward",
    )
    merged = merged.rename(columns={regime_column: "_regime"})
    return merged


def run_regime_scoping(
    trades: Optional[pd.DataFrame],
    *,
    bars_with_regime: Optional[pd.DataFrame],
    cost_model: Dict[str, Any],
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Partition ``trades`` by entry regime and find the edge regime(s).

    Parameters
    ----------
    trades           : trade DataFrame; must carry ``entry_time`` and a gross
                       ``profit`` column (the honest re-price needs gross P&L —
                       run ``prepare_trades_for_hardening`` first).
    bars_with_regime : bars with a ``dt`` column and the regime column named by
                       ``options['regime_column']``.
    cost_model       : dict with ``commission_per_side``, ``tick_value``,
                       ``slippage_ticks`` — passed straight to honest_execution.
    options          : merged over ``DEFAULT_REGIME_SCOPING_CONFIG``.

    Returns
    -------
    JSON-safe dict:
      enabled, regime_column, n_trades, n_unlabeled,
      n_regimes_evaluated      — count of regimes judged (the trial-budget feed),
      trial_budget_within_run_trials — alias of n_regimes_evaluated,
      per_regime               — {label: {n_trades, passed, honest}},
      skipped_regimes          — {label: reason} for un-judgeable regimes,
      edge_regimes, non_edge_regimes,
      track                    — "durable" | "regime-limited" | "none",
      scoped_variant           — {regimes, n_trades, honest, passed} or None,
      passed, issues.
    """
    cfg = {**DEFAULT_REGIME_SCOPING_CONFIG, **(options or {})}
    regime_column = str(cfg.get("regime_column", "regime"))
    min_per_regime = int(cfg.get("min_trades_per_regime", 30))
    min_edge_regimes = int(cfg.get("min_edge_regimes", 1))

    result: Dict[str, Any] = {
        "enabled": bool(cfg.get("enabled", True)),
        "regime_column": regime_column,
        "n_trades": 0,
        "n_unlabeled": 0,
        "n_regimes_evaluated": 0,
        "trial_budget_within_run_trials": 0,
        "per_regime": {},
        "skipped_regimes": {},
        "edge_regimes": [],
        "non_edge_regimes": [],
        "track": "none",
        "scoped_variant": None,
        "passed": None,
        "issues": [],
    }

    if not result["enabled"]:
        result["issues"].append("regime_scoping disabled in config")
        return result
    if trades is None or not isinstance(trades, pd.DataFrame) or len(trades) == 0:
        result["issues"].append("no trades provided")
        return result
    if "entry_time" not in trades.columns:
        result["issues"].append("'entry_time' column not found on trades")
        return result
    if "profit" not in trades.columns:
        result["issues"].append("'profit' column (gross P&L) not found on trades")
        return result
    if (
        bars_with_regime is None
        or not isinstance(bars_with_regime, pd.DataFrame)
        or len(bars_with_regime) == 0
    ):
        result["issues"].append("no bars_with_regime provided")
        return result
    if "dt" not in bars_with_regime.columns:
        result["issues"].append("bars_with_regime missing 'dt' column")
        return result
    if regime_column not in bars_with_regime.columns:
        result["issues"].append(
            f"regime column '{regime_column}' not found on bars_with_regime"
        )
        return result

    result["n_trades"] = int(len(trades))

    try:
        labelled = _label_trades_by_regime(trades, bars_with_regime, regime_column)
    except Exception as exc:  # pragma: no cover - defensive
        result["issues"].append(f"regime labelling failed: {type(exc).__name__}: {exc}")
        return result

    regime_series = labelled["_regime"]
    unlabelled_mask = regime_series.isna()
    result["n_unlabeled"] = int(unlabelled_mask.sum())
    if result["n_unlabeled"]:
        result["issues"].append(
            f"{result['n_unlabeled']} trades had no regime label (entry before first "
            "classified bar) — excluded from scoping"
        )

    honest_opts = cfg.get("honest_execution") or {}

    per_regime: Dict[str, Any] = {}
    skipped: Dict[str, str] = {}
    edge_regimes: List[str] = []
    non_edge_regimes: List[str] = []

    for label, grp in labelled[~unlabelled_mask].groupby("_regime"):
        label_str = str(label)
        n_regime_trades = int(len(grp))
        if n_regime_trades < min_per_regime:
            skipped[label_str] = (
                f"only {n_regime_trades} trades < min_trades_per_regime {min_per_regime}"
            )
            continue

        honest = apply_honest_execution(
            grp, cost_model=cost_model, options=honest_opts
        )
        regime_passed = bool(honest.get("passed", False))
        per_regime[label_str] = {
            "n_trades": n_regime_trades,
            "passed": regime_passed,
            "honest": honest,
        }
        if regime_passed:
            edge_regimes.append(label_str)
        else:
            non_edge_regimes.append(label_str)

    edge_regimes.sort()
    non_edge_regimes.sort()
    n_evaluated = len(per_regime)

    result["per_regime"] = per_regime
    result["skipped_regimes"] = skipped
    result["edge_regimes"] = edge_regimes
    result["non_edge_regimes"] = non_edge_regimes
    result["n_regimes_evaluated"] = n_evaluated
    result["trial_budget_within_run_trials"] = n_evaluated

    if n_evaluated == 0:
        result["track"] = "none"
        result["passed"] = False
        result["issues"].append(
            "no regime had enough trades to be judged — candidate un-scopeable"
        )
        return result

    n_edge = len(edge_regimes)
    if n_edge == 0:
        result["track"] = "none"
        result["passed"] = False
        result["issues"].append("no regime cleared the honest survival gate")
        return result

    # Pool the edge-regime trades and re-validate the scoped variant honestly.
    scoped_trades = labelled[labelled["_regime"].isin(edge_regimes)]
    scoped_honest = apply_honest_execution(
        scoped_trades, cost_model=cost_model, options=honest_opts
    )
    scoped_passed = bool(scoped_honest.get("passed", False))

    if n_edge == n_evaluated:
        result["track"] = "durable"
    else:
        result["track"] = "regime-limited"
        result["issues"].append(
            "edge is regime-limited — works in "
            f"{edge_regimes}, fails in {non_edge_regimes}; enroll in decay "
            "monitoring and size as a high-decay-risk bet"
        )

    result["scoped_variant"] = {
        "regimes": edge_regimes,
        "n_trades": int(len(scoped_trades)),
        "honest": scoped_honest,
        "passed": scoped_passed,
    }

    enough_edge_regimes = n_edge >= min_edge_regimes
    if not enough_edge_regimes:
        result["issues"].append(
            f"only {n_edge} edge regime(s) < min_edge_regimes {min_edge_regimes}"
        )
    if not scoped_passed:
        result["issues"].append(
            "scoped variant failed the honest survival gate on pooled edge-regime trades"
        )
    result["passed"] = bool(enough_edge_regimes and scoped_passed)
    return result
