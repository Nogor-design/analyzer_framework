"""
Cross-Family Optimizer — Ranks all entry signal families against each other.

Problem: Stage 1 runs all 8 families separately, but you have to visually scan
to find the global winner. This module ranks them objectively across multiple
metrics (profit factor, Sharpe, risk-adjusted returns, consistency).

Key functions:
- build_unified_leaderboard() — combine all families, rank by metric
- score_candidate() — compute composite score across metrics
- find_best_combos() — top N combinations across all families
- suggest_next_focus() — which families deserve deeper discovery
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import pandas as pd


class CandidateRank:
    """Ranked candidate with family, params, and scores."""

    def __init__(
        self,
        rank: int,
        family: str,
        signal_id: str,
        params: Dict[str, Any],
        n_trades: int,
        pf: float,
        sharpe: Optional[float] = None,
        win_rate: Optional[float] = None,
        avg_trade: Optional[float] = None,
        is_oos_degradation: Optional[float] = None,
        composite_score: float = 0.0,
        tf: int = 1,
    ):
        self.rank = rank
        self.family = family
        self.signal_id = signal_id
        self.params = params
        self.n_trades = n_trades
        self.pf = pf
        self.sharpe = sharpe
        self.win_rate = win_rate
        self.avg_trade = avg_trade
        self.is_oos_degradation = is_oos_degradation
        self.composite_score = composite_score
        self.tf = tf

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-safe dict."""
        return {
            "rank": self.rank,
            "family": self.family,
            "signal_id": self.signal_id,
            "params": self.params,
            "n_trades": self.n_trades,
            "pf": round(self.pf, 4) if self.pf else None,
            "sharpe": round(self.sharpe, 4) if self.sharpe else None,
            "win_rate": round(self.win_rate, 4) if self.win_rate else None,
            "avg_trade": round(self.avg_trade, 2) if self.avg_trade else None,
            "is_oos_degradation": round(self.is_oos_degradation, 4) if self.is_oos_degradation else None,
            "composite_score": round(self.composite_score, 4),
            "timeframe": self.tf,
        }

    def __repr__(self) -> str:
        return (
            f"Rank #{self.rank}: {self.family} (PF={self.pf:.2f}, "
            f"Score={self.composite_score:.2f}, {self.n_trades} trades)"
        )


def _safe_float(val: Any) -> Optional[float]:
    """Convert value to float or None."""
    if val is None or pd.isna(val):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def score_candidate(
    result: Dict[str, Any],
    weights: Dict[str, float] = None,
) -> float:
    """
    Compute composite score for a candidate using weighted metrics.

    Parameters
    ----------
    result       : result dict from sweep
    weights      : {metric: weight}
                   defaults: {"pf": 0.5, "sharpe": 0.3, "win_rate": 0.15, "robustness": 0.05}

    Returns
    -------
    Composite score (higher = better)
    """
    if weights is None:
        weights = {
            "pf": 0.50,
            "sharpe": 0.30,
            "win_rate": 0.15,
            "robustness": 0.05,
        }

    metrics = result.get("metrics", {})
    pf = _safe_float(metrics.get("profit_factor")) or 0.0
    sharpe = _safe_float(metrics.get("sharpe")) or 0.0
    win_rate = _safe_float(metrics.get("win_rate")) or 0.0
    is_oos_deg = _safe_float(result.get("is_oos_degradation")) or 0.0

    # Normalize to 0-1 range
    pf_norm = min(pf / 2.0, 1.0)  # 2.0 is "excellent"
    sharpe_norm = min(max(sharpe + 1, 0) / 4.0, 1.0)  # Sharpe range ~-1 to 3
    win_rate_norm = win_rate  # Already 0-1
    robustness_norm = max(1.0 - is_oos_deg, 0.0)  # Lower deg = more robust

    score = (
        weights.get("pf", 0.5) * pf_norm
        + weights.get("sharpe", 0.3) * sharpe_norm
        + weights.get("win_rate", 0.15) * win_rate_norm
        + weights.get("robustness", 0.05) * robustness_norm
    )

    return round(score, 4)


def build_unified_leaderboard(
    all_sweep_results: Dict[str, List[Dict[str, Any]]],
    min_trades: int = 20,
    top_n: int = 50,
    metric: str = "composite",
) -> List[CandidateRank]:
    """
    Build a unified ranking across all families.

    Parameters
    ----------
    all_sweep_results : {family: [results]}
                        e.g., {"candle": [...], "ma": [...], "orb": [...]}
    min_trades        : filter out results with fewer trades
    top_n             : return top N candidates
    metric            : "composite", "pf", "sharpe" — ranking metric

    Returns
    -------
    List of CandidateRank objects, sorted best-first
    """
    candidates: List[CandidateRank] = []

    for family, results in all_sweep_results.items():
        if not results:
            continue

        for result in results:
            n_trades = result.get("n_trades", 0)
            if n_trades < min_trades:
                continue

            metrics = result.get("metrics", {})
            pf = _safe_float(metrics.get("profit_factor")) or 0.0
            sharpe = _safe_float(metrics.get("sharpe"))
            win_rate = _safe_float(metrics.get("win_rate"))
            avg_trade = _safe_float(metrics.get("avg_trade"))
            is_oos_deg = _safe_float(result.get("is_oos_degradation"))

            # Compute composite score
            composite = score_candidate(result)

            candidate = CandidateRank(
                rank=0,  # Will be assigned later
                family=family,
                signal_id=result.get("signal_id", family),
                params=result.get("params", {}),
                n_trades=n_trades,
                pf=pf,
                sharpe=sharpe,
                win_rate=win_rate,
                avg_trade=avg_trade,
                is_oos_degradation=is_oos_deg,
                composite_score=composite,
                tf=result.get("tf", 1),
            )
            candidates.append(candidate)

    # Sort by metric
    if metric == "pf":
        candidates.sort(key=lambda c: c.pf, reverse=True)
    elif metric == "sharpe":
        candidates.sort(key=lambda c: c.sharpe or -float("inf"), reverse=True)
    else:  # composite (default)
        candidates.sort(key=lambda c: c.composite_score, reverse=True)

    # Assign ranks and truncate
    for i, cand in enumerate(candidates[:top_n], 1):
        cand.rank = i

    return candidates[:top_n]


def family_summary(
    leaderboard: List[CandidateRank],
) -> Dict[str, Dict[str, Any]]:
    """
    Summarize leaderboard by family.

    Parameters
    ----------
    leaderboard : list of CandidateRank

    Returns
    -------
    {family: {best_rank, count, avg_pf, avg_score}}
    """
    by_family: Dict[str, Dict[str, Any]] = {}

    for cand in leaderboard:
        fam = cand.family
        if fam not in by_family:
            by_family[fam] = {
                "count": 0,
                "best_rank": None,
                "best_pf": 0.0,
                "avg_pf": 0.0,
                "pfs": [],
                "scores": [],
            }

        by_family[fam]["count"] += 1
        if by_family[fam]["best_rank"] is None:
            by_family[fam]["best_rank"] = cand.rank
        by_family[fam]["best_pf"] = max(by_family[fam]["best_pf"], cand.pf)
        by_family[fam]["pfs"].append(cand.pf)
        by_family[fam]["scores"].append(cand.composite_score)

    # Compute averages
    for fam, info in by_family.items():
        if info["pfs"]:
            info["avg_pf"] = round(sum(info["pfs"]) / len(info["pfs"]), 4)
            info["avg_score"] = round(sum(info["scores"]) / len(info["scores"]), 4)
        info.pop("pfs")
        info.pop("scores")

    return by_family


def suggest_next_focus(
    leaderboard: List[CandidateRank],
    top_n_families: int = 3,
) -> Dict[str, Any]:
    """
    Suggest which families deserve deeper discovery in next stages.

    Parameters
    ----------
    leaderboard      : unified ranking
    top_n_families   : how many to recommend

    Returns
    -------
    {
        "focus_families": ["candle", "ma", "orb"],
        "skip_families": ["level", "bb"],
        "reasoning": "candle leads with 6 of top 10; ma is strong dark horse"
    }
    """
    summary = family_summary(leaderboard)

    # Rank families by presence in top 50 and average PF
    family_scores = []
    for fam, info in summary.items():
        # Score = count (presence) + avg_pf (quality)
        presence = info["count"]
        quality = info["avg_pf"]
        score = presence * 10 + quality
        family_scores.append((fam, score, info))

    family_scores.sort(key=lambda x: x[1], reverse=True)

    focus = [f[0] for f in family_scores[:top_n_families]]
    skip = [f[0] for f in family_scores[top_n_families:]]

    # Build reasoning
    top_by_count = sorted(family_scores, key=lambda x: x[2]["count"], reverse=True)[0]
    reasoning = (
        f"{top_by_count[0].upper()} dominates with {top_by_count[2]['count']} entries in top 50; "
        f"avg PF={top_by_count[2]['avg_pf']:.2f}. "
        f"Recommend deep-diving {', '.join(focus)} in next stages."
    )

    return {
        "focus_families": focus,
        "skip_families": skip,
        "reasoning": reasoning,
        "summary": summary,
    }


def print_leaderboard(
    leaderboard: List[CandidateRank],
    top_n: int = 20,
) -> str:
    """Format leaderboard as human-readable table."""
    lines = [
        "=" * 120,
        "UNIFIED CROSS-FAMILY LEADERBOARD",
        "=" * 120,
        "",
        f"{'Rank':<5} {'Family':<12} {'Signal':<20} {'PF':<8} {'Sharpe':<8} {'WR':<7} {'Trades':<7} {'Score':<8}",
        "-" * 120,
    ]

    for cand in leaderboard[:top_n]:
        rank_str = f"#{cand.rank}"
        pf_str = f"{cand.pf:.3f}" if cand.pf else "—"
        sharpe_str = f"{cand.sharpe:.2f}" if cand.sharpe else "—"
        wr_str = f"{cand.win_rate:.1%}" if cand.win_rate else "—"
        lines.append(
            f"{rank_str:<5} {cand.family:<12} {cand.signal_id:<20} "
            f"{pf_str:<8} {sharpe_str:<8} {wr_str:<7} {cand.n_trades:<7} {cand.composite_score:<8.3f}"
        )

    lines.append("=" * 120)
    return "\n".join(lines)


def print_family_summary(suggestion: Dict[str, Any]) -> str:
    """Format family summary as human-readable text."""
    lines = [
        "",
        "=" * 80,
        "FAMILY SUMMARY",
        "=" * 80,
        "",
        suggestion.get("reasoning", ""),
        "",
        "FOCUS FAMILIES (recommend deep-dive):",
    ]

    for fam in suggestion.get("focus_families", []):
        info = suggestion.get("summary", {}).get(fam, {})
        lines.append(
            f"  • {fam.upper():<15} — Best rank: #{info.get('best_rank')}, "
            f"Count: {info.get('count')}, Avg PF: {info.get('avg_pf'):.3f}"
        )

    lines.extend(["", "LOWER PRIORITY (or skip):", ""])
    for fam in suggestion.get("skip_families", []):
        info = suggestion.get("summary", {}).get(fam, {})
        lines.append(
            f"  • {fam.upper():<15} — Best rank: #{info.get('best_rank')}, "
            f"Count: {info.get('count')}, Avg PF: {info.get('avg_pf'):.3f}"
        )

    lines.append("=" * 80)
    return "\n".join(lines)
