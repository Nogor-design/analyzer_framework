from __future__ import annotations

"""
Candidate Context Profiler (step 4 — adaptive supervisor, Version 0)
=====================================================================
Step 4's adaptive supervisor must answer, per candidate, *where* an edge lives:
which regimes, sessions, and directions it works in, fails in, or simply has
too few trades to judge. This module is the offline Version 0 of that — no live
influence, no LLM — and it is deliberately a *reader*, not a new analysis path.

Per the discovery-realism plan, this profiler "overlaps step 3's regime_scoping
— unify them, don't build two regime-analysis paths." So it consumes the
breakdowns the sweep already computes — ``regime_breakdown`` (from
``compute_regime_breakdown``), ``metrics.by_session`` / ``metrics.by_direction``
(from ``compute_evaluation_metrics``), and the hardening ``regime_scoping``
verdict — and classifies each context cell rather than recomputing anything.

For each candidate it emits a context profile, classifying every context cell:

    strong   — enough trades and a genuine edge (clears the profit-factor bar).
    marginal — net-positive, but below the strong profit-factor bar.
    weak     — enough trades, but net-negative.
    unknown  — too few trades in that context to judge (information, not failure).

Across candidates it aggregates a context matrix: for each regime / session /
direction value, how many candidates rate it strong vs weak. That is the
"regime/session performance matrix" — it shows which contexts this market
rewards independent of any single candidate, which is the raw material the
later (Version 1+) supervisor needs to decide *suppress / size-down / allow*.

All outputs are JSON-safe — no DataFrames in the returned dict.
"""

from typing import Any, Dict, List, Optional


DEFAULT_CONTEXT_PROFILE_CONFIG: Dict[str, Any] = {
    "enabled": True,
    # A context cell with fewer trades than this is un-judgeable, not failed.
    "min_trades_per_context": 20,
    # Net-positive cells at/above this profit factor are "strong"; below it,
    # "marginal".
    "strong_profit_factor": 1.3,
    # Per-candidate warnings.
    "oos_degradation_warn": 0.40,
    "thin_sample_warn": 50,
}

# Context dimensions and where each is read from a sweep-result dict.
_REGIME_DIMENSIONS = ("regime", "vol_regime", "trend_direction")
_METRIC_DIMENSIONS = ("session", "direction")
_DIMENSIONS = _REGIME_DIMENSIONS + _METRIC_DIMENSIONS
_BUCKETS = ("strong", "marginal", "weak", "unknown")


def _cells_for_dimension(result: Dict[str, Any], dim: str) -> Dict[str, Any]:
    """Return ``{label: cell}`` for one context dimension of a sweep result."""
    if dim in _REGIME_DIMENSIONS:
        rb = result.get("regime_breakdown") or {}
        cells = rb.get(f"by_{dim}")
        return cells if isinstance(cells, dict) else {}
    metrics = result.get("metrics") or {}
    cells = metrics.get(f"by_{dim}")
    return cells if isinstance(cells, dict) else {}


def _classify_cell(cell: Any, *, min_trades: int, strong_pf: float) -> str:
    """Classify one context cell as strong / marginal / weak / unknown."""
    if not isinstance(cell, dict):
        return "unknown"
    n = int(cell.get("n_trades") or 0)
    if n < min_trades:
        return "unknown"
    pf = cell.get("profit_factor")
    avg = cell.get("avg_trade")
    net = cell.get("net_profit")
    if avg is not None:
        positive = float(avg) > 0
    elif net is not None:
        positive = float(net) > 0
    else:
        return "unknown"
    if not positive:
        return "weak"
    # Net-positive. ``pf is None`` means no losing trades — unambiguously strong.
    if pf is None or float(pf) >= strong_pf:
        return "strong"
    return "marginal"


def _candidate_key(result: Dict[str, Any]) -> str:
    """A readable identifier for one candidate sweep result."""
    parts = [
        str(result.get("pattern_id") or result.get("signal_id") or "candidate"),
        str(result.get("direction_mode") or ""),
        str(result.get("entry_timing") or ""),
        str(result.get("outcome_mode") or ""),
    ]
    params_key = result.get("params_key")
    if params_key:
        parts.append(str(params_key))
    return "|".join(p for p in parts if p)


def build_candidate_context_profile(
    result: Dict[str, Any],
    *,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Profile one candidate: classify every context cell and collect warnings.

    Parameters
    ----------
    result  : one sweep-result dict — carries ``regime_breakdown``, ``metrics``
              (with ``by_session`` / ``by_direction``), ``hardening``,
              ``n_trades`` and ``is_oos_degradation``.
    options : merged over ``DEFAULT_CONTEXT_PROFILE_CONFIG``.

    Returns
    -------
    JSON-safe dict: candidate_key, n_trades, profit_factor, regime_track,
    strong / marginal / weak / unknown context lists, and warnings.
    """
    cfg = {**DEFAULT_CONTEXT_PROFILE_CONFIG, **(options or {})}
    min_trades = int(cfg["min_trades_per_context"])
    strong_pf = float(cfg["strong_profit_factor"])

    buckets: Dict[str, List[str]] = {b: [] for b in _BUCKETS}
    for dim in _DIMENSIONS:
        for label, cell in sorted(
            _cells_for_dimension(result, dim).items(), key=lambda kv: str(kv[0])
        ):
            verdict = _classify_cell(cell, min_trades=min_trades, strong_pf=strong_pf)
            buckets[verdict].append(f"{dim}={label}")

    warnings: List[str] = []
    n_trades = int(result.get("n_trades") or 0)
    if n_trades < int(cfg["thin_sample_warn"]):
        warnings.append(f"thin overall sample ({n_trades} trades)")
    oos = result.get("is_oos_degradation")
    if oos is not None:
        try:
            if float(oos) > float(cfg["oos_degradation_warn"]):
                warnings.append(f"high out-of-sample degradation ({round(float(oos), 3)})")
        except (TypeError, ValueError):
            pass

    hardening = result.get("hardening") or {}
    if hardening.get("passed") is False:
        warnings.append("fails the hardening gate stack")
    if (hardening.get("honest_execution") or {}).get("passed") is False:
        warnings.append("fails the honest-execution fill-realism gate")
    regime_scoping = hardening.get("regime_scoping") or {}
    track = regime_scoping.get("track")
    if track == "regime-limited":
        warnings.append("edge is regime-limited — works only in a subset of regimes")
    elif track == "none":
        warnings.append("no regime cleared the honest survival gate")

    metrics = result.get("metrics") or {}
    return {
        "candidate_key": _candidate_key(result),
        "n_trades": n_trades,
        "profit_factor": metrics.get("profit_factor"),
        "regime_track": track,
        "strong_contexts": buckets["strong"],
        "marginal_contexts": buckets["marginal"],
        "weak_contexts": buckets["weak"],
        "unknown_contexts": buckets["unknown"],
        "warnings": warnings,
    }


def profile_candidates(
    sweep_results: Optional[List[Dict[str, Any]]],
    *,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Profile a whole sweep: per-candidate profiles + a cross-candidate matrix.

    Parameters
    ----------
    sweep_results : list of sweep-result dicts (e.g. a family sweep's
                    ``sweep_results``).
    options       : merged over ``DEFAULT_CONTEXT_PROFILE_CONFIG``.

    Returns
    -------
    JSON-safe dict:
      n_candidates, profiles (per-candidate),
      context_matrix  — {dimension: {label: {strong, marginal, weak, unknown}}},
      summary         — counts plus ``best_contexts`` (contexts the market most
                        rewards, by strong-minus-weak across candidates).
    """
    cfg = {**DEFAULT_CONTEXT_PROFILE_CONFIG, **(options or {})}

    out: Dict[str, Any] = {
        "enabled": bool(cfg.get("enabled", True)),
        "n_candidates": 0,
        "profiles": [],
        "context_matrix": {},
        "summary": {},
        "issues": [],
    }
    if not out["enabled"]:
        out["issues"].append("candidate_context_profile disabled in config")
        return out
    if not sweep_results or not isinstance(sweep_results, list):
        out["issues"].append("no sweep results provided")
        return out

    profiles = [
        build_candidate_context_profile(r, options=cfg)
        for r in sweep_results
        if isinstance(r, dict)
    ]
    out["profiles"] = profiles
    out["n_candidates"] = len(profiles)
    if not profiles:
        out["issues"].append("no usable candidate dicts in sweep results")
        return out

    # Context matrix: per dimension/label, how many candidates rate it each way.
    matrix: Dict[str, Dict[str, Dict[str, int]]] = {}
    for prof in profiles:
        for bucket in _BUCKETS:
            for ctx in prof[f"{bucket}_contexts"]:
                dim, _, label = ctx.partition("=")
                cell = matrix.setdefault(dim, {}).setdefault(
                    label, {b: 0 for b in _BUCKETS}
                )
                cell[bucket] += 1
    out["context_matrix"] = matrix

    # best_contexts: where the market most consistently rewards trading —
    # highest (strong - weak) candidate count, ties broken by strong count.
    ranked: List[Dict[str, Any]] = []
    for dim, labels in matrix.items():
        for label, counts in labels.items():
            net = counts["strong"] - counts["weak"]
            if net > 0:
                ranked.append(
                    {
                        "context": f"{dim}={label}",
                        "net_strong": net,
                        "strong": counts["strong"],
                        "weak": counts["weak"],
                    }
                )
    ranked.sort(key=lambda r: (r["net_strong"], r["strong"]), reverse=True)

    track_counts: Dict[str, int] = {}
    for prof in profiles:
        track = prof.get("regime_track")
        if track:
            track_counts[track] = track_counts.get(track, 0) + 1

    out["summary"] = {
        "n_candidates": len(profiles),
        "n_with_a_strong_context": sum(1 for p in profiles if p["strong_contexts"]),
        "n_with_warnings": sum(1 for p in profiles if p["warnings"]),
        "regime_track_counts": track_counts,
        "best_contexts": ranked[:5],
    }
    return out
