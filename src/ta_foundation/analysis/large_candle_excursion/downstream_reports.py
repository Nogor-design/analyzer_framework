from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import math

from ta_foundation.analysis.large_candle_excursion.reversal_size_analysis import (
    compute_reversal_size_analysis,
    DEFAULT_REVERSAL_SIZE_CONFIG,
)


DEFAULT_FINDINGS_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "source_report": "large_candle_excursion",
    "min_events": 30,
    "ranking": {
        "primary_metric": "composite_score",
        "require_min_win_rate": 0.50,
        "penalize_low_sample": True,
        "penalize_complexity": True,
    },
    # Scoring weights — must sum to ≤ 1.0 (sparse_penalty is subtracted separately)
    "scoring_weights": {
        "win_rate":        0.40,
        "sample_score":    0.20,
        "expectancy_score":0.20,
        "stability":       0.15,
        "simplicity":      0.05,
    },
    # Sample-size-based thresholds for fragility warnings and sparse penalty
    "min_sample_thresholds": {
        "fragility_low":          60,    # N below → high-severity fragility warning
        "sparse_penalty_below":   80,    # N below → sparse penalty applied in scoring
        "suspicious_strength_n":  80,    # combined with suspicious_strength_wr
        "suspicious_strength_wr": 70.0,  # WR% at or above → "suspicious strength" flag
    },
    # Optional penalty when a setup's edge is concentrated in a single session
    "session_concentration_penalty": {
        "enabled": False,
        "penalty_per_session_pct": 0.05,  # score penalty applied if only 1 session dominates
    },
    # Promotion ladder thresholds
    "promotion": {
        "candidate": {
            "min_score": 0.52,
            "min_n": 80,
            "min_plateau": 2,
            "max_edge_decay_penalty": 0.80,
        },
        "strategy_test_ready": {
            "min_score": 0.57,
            "min_n": 120,
            "min_plateau": 2,
            "max_edge_decay_penalty": 0.50,
        },
    },
    # Family grouping — group by (trade_mode, direction, tf, candle_bucket)
    "family_grouping": {
        "top_n_families": 20,
    },
    # Additional fragility detection thresholds
    "fragility_thresholds": {
        "plateau_fragile_below": 1,       # plateau_width < this → "fragile (no plateau)"
        "edge_decay_fragile_above": 0.70, # edge_decay_penalty > this → flag
        "session_concentration_pct": 80.0,# if top session has this % of events → flag
        "max_variants_per_family": 5,     # more variants in a family → overrepresentation flag
    },
    "interactions": {
        "min_events": 50,
        "min_edge_pp": 4.0,
        "min_score": 0.48,
        "min_stability": 0.45,
        "max_complexity_penalty": 0.12,
        "attempted_top_n": 12,
    },
    "neighbor_analysis": {
        "enabled": True,
        "top_setups": 6,
        "max_neighbors": 6,
        "target_neighbor_values": [15, 25, 35, 50],
    },
    "time_split": {
        "enabled": True,
        "n_splits": 3,
    },
    "output": {
        "top_n_discoveries":          25,
        "top_n_strategy_cards":       10,
        "include_executive_summary":  True,
        "include_fragility_warnings": True,
        "include_suggested_next_tests": True,
        "max_next_tests":             10,
    },
    # Reversal size analysis — move-size tiers, large-move probability, runner potential
    "reversal_size_analysis": DEFAULT_REVERSAL_SIZE_CONFIG,
}

DEFAULT_DISCOVERY_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "source_report": "large_candle_excursion",
    "objective": {
        "primary_metric": "composite_score",
        "min_events": 30,
        "min_win_rate": 0.50,
    },
    "stages": {
        "broad_scan": {"enabled": True, "top_n_to_keep": 50},
        "refinement": {
            "enabled": True,
            "top_n_parents": 20,
            "refinement_rules": {
                "target_percent_step": 10,
                "threshold_multiplier_step": 0.25,
            },
        },
        "interaction_chaining": {
            "enabled": True,
            "top_n_parents": 10,
            "max_chain_depth": 3,
            "min_incremental_improvement": 0.03,
            "min_remaining_events": 50,
            "min_score": 0.45,
            "attempted_top_n": 15,
        },
        "robustness_validation": {
            "enabled": True,
            "time_splits": 3,
            "require_neighbor_stability": True,
            "require_out_of_sample_check": False,
        },
    },
    "neighbor_analysis": {
        "enabled": True,
        "top_setups": 10,
        "max_neighbors": 8,
        "target_neighbor_values": [15, 25, 35, 50],
    },
    "output": {"include_diagnostics": True, "top_n_final_discoveries": 25},
}


@dataclass
class ScoredSetup:
    record: Dict[str, Any]
    score: float


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        if math.isnan(f):
            return default
        return f
    except Exception:
        return default


def _extract_lce(source: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], Optional[str]]:
    if not source:
        return {}, "source analytics missing"
    if not source.get("enabled", False):
        return {}, "source analytics present but disabled"
    return source, None


def _extract_time_value(event: Dict[str, Any]) -> str:
    for k in ("event_dt", "signal_dt", "entry_dt", "entry_time", "dt", "timestamp"):
        v = event.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def _build_trade_candidates(lce: Dict[str, Any], min_events: int, min_win_rate_pct: float) -> List[Dict[str, Any]]:
    ta = lce.get("trade_analysis") or {}
    combos = ta.get("trade_combo_results") or []
    if not combos:
        return []

    out: List[Dict[str, Any]] = []
    for c in combos:
        n = int(c.get("n_events") or 0)
        wr = _safe_float(c.get("win_rate"), default=-1.0)
        if n < min_events:
            continue
        if wr < min_win_rate_pct:
            continue

        avg_fav = _safe_float(c.get("avg_trade_fav_ticks"))
        avg_adv = _safe_float(c.get("avg_trade_adv_ticks"))
        expectancy_ticks = avg_fav - avg_adv
        rec = {
            **c,
            "expectancy_ticks": round(expectancy_ticks, 3),
            "win_rate_frac": round(wr / 100.0, 6),
            "setup_definition": (
                f"{c.get('trade_mode', '?')} | tf={c.get('tf_minutes', '?')}m | "
                f"bucket={c.get('candle_bucket', '?')} | target={c.get('target_percent', '?')}%"
            ),
            "tradability": {
                "avg_target_ticks": c.get("avg_target_ticks"),
                "median_target_ticks": c.get("median_target_ticks"),
                "avg_favorable_excursion": c.get("avg_trade_fav_ticks"),
                "median_favorable_excursion": c.get("median_trade_fav_ticks"),
                "avg_adverse_excursion": c.get("avg_trade_adv_ticks"),
                "median_adverse_excursion": c.get("median_trade_adv_ticks"),
                "expectancy_ticks": round(expectancy_ticks, 3),
            },
        }
        out.append(rec)
    return out


def _neighbor_stability(candidates: List[Dict[str, Any]], row: Dict[str, Any]) -> float:
    peers = [
        c
        for c in candidates
        if c.get("trade_mode") == row.get("trade_mode")
        and c.get("direction") == row.get("direction")
        and c.get("candle_bucket") == row.get("candle_bucket")
        and c.get("tf_minutes") == row.get("tf_minutes")
        and c.get("lookback") == row.get("lookback")
        and c.get("threshold_value") == row.get("threshold_value")
    ]
    if len(peers) <= 1:
        return 0.5

    target = _safe_float(row.get("target_percent"), 0.0)
    wr = _safe_float(row.get("win_rate"), 0.0)
    close_peers = sorted(peers, key=lambda p: abs(_safe_float(p.get("target_percent"), 0.0) - target))[:5]
    diffs = [abs(wr - _safe_float(p.get("win_rate"), wr)) for p in close_peers if p is not row]
    if not diffs:
        return 0.5
    avg_diff = sum(diffs) / len(diffs)
    return _clamp01(1.0 - avg_diff / 25.0)


def _score_candidates(
    candidates: List[Dict[str, Any]],
    penalize_low_sample: bool,
    penalize_complexity: bool,
    scoring_weights: Optional[Dict[str, float]] = None,
    min_sample_thresholds: Optional[Dict[str, Any]] = None,
) -> List[ScoredSetup]:
    w = scoring_weights or {}
    w_wr   = float(w.get("win_rate",         0.40))
    w_samp = float(w.get("sample_score",     0.20))
    w_exp  = float(w.get("expectancy_score", 0.20))
    w_stab = float(w.get("stability",        0.15))
    w_simp = float(w.get("simplicity",       0.05))

    st = min_sample_thresholds or {}
    sparse_below = float(st.get("sparse_penalty_below", 80))

    scored: List[ScoredSetup] = []
    for c in candidates:
        wr = _safe_float(c.get("win_rate"), 0.0) / 100.0
        n = max(1.0, _safe_float(c.get("n_events"), 1.0))
        expectancy = _safe_float(c.get("expectancy_ticks"), 0.0)
        exp_score = _clamp01((expectancy + 8.0) / 20.0)
        sample_score = _clamp01(math.log10(n) / 2.5)
        stability = _neighbor_stability(candidates, c)

        simplicity = 1.0
        if penalize_complexity:
            if c.get("stop_percent") is not None:
                simplicity -= 0.10
            if str(c.get("candle_bucket", "")).endswith("+"):
                simplicity -= 0.05
            simplicity = _clamp01(simplicity)

        sparse_penalty = 0.0
        if penalize_low_sample and n < sparse_below:
            sparse_penalty = (sparse_below - n) / (sparse_below * 2.5)

        score = (
            w_wr   * wr
            + w_samp * sample_score
            + w_exp  * exp_score
            + w_stab * stability
            + w_simp * simplicity
            - sparse_penalty
        )
        score = round(score, 6)

        scored_row = dict(c)
        scored_row["composite_score"] = score
        scored_row["stability_score"] = round(stability, 4)
        scored_row["sample_score"] = round(sample_score, 4)
        scored_row["simplicity_score"] = round(simplicity, 4)
        scored_row["sparse_penalty"] = round(sparse_penalty, 4)
        scored.append(ScoredSetup(record=scored_row, score=score))

    scored.sort(key=lambda s: (-s.score, -_safe_float(s.record.get("n_events"), 0.0)))
    return scored


def _strong_context_effects(context_analysis: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    def _top(rows: List[Dict[str, Any]], key_name: str) -> List[Dict[str, Any]]:
        enriched: List[Dict[str, Any]] = []
        for r in rows or []:
            cont = r.get("cont_win_rate")
            rev = r.get("rev_win_rate")
            edge = None
            if cont is not None and rev is not None:
                edge = round(abs(_safe_float(cont) - _safe_float(rev)), 3)
            enriched.append({**r, "edge_abs": edge, "bucket": r.get(key_name)})
        enriched.sort(key=lambda x: (-_safe_float(x.get("edge_abs"), -1.0), -_safe_float(x.get("n_observations"), 0.0)))
        return enriched[:5]

    vol = (context_analysis.get("volume_context") or {}).get("by_vol_bucket") or []
    struct = (context_analysis.get("structure_context") or {}).get("by_close_pos") or []
    volat = (context_analysis.get("volatility_context") or {}).get("by_atr_bucket") or []

    return {
        "volume": _top(vol, "vol_bucket"),
        "structure": _top(struct, "close_pos_bucket"),
        "volatility": _top(volat, "atr_bucket"),
    }


def _evaluate_interaction_candidates(interactions: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    min_events = int(cfg.get("min_events", 50))
    min_edge = float(cfg.get("min_edge_pp", 4.0))
    min_score = float(cfg.get("min_score", 0.48))
    min_stability = float(cfg.get("min_stability", 0.45))
    max_complexity = float(cfg.get("max_complexity_penalty", 0.12))

    attempted: List[Dict[str, Any]] = []
    passed: List[Dict[str, Any]] = []
    for row in interactions:
        n = int(row.get("n_observations") or 0)
        cont = _safe_float(row.get("cont_win_rate"), 0.0)
        rev = _safe_float(row.get("rev_win_rate"), 0.0)
        edge = abs(cont - rev)
        complexity_penalty = 0.08 if str(row.get("candle_bucket", "")).endswith("+") else 0.03
        sample_score = _clamp01(math.log10(max(1, n)) / 2.5)
        stability = _clamp01(1.0 - abs(edge - 12.0) / 20.0)
        score = _clamp01(0.45 * (edge / 20.0) + 0.30 * sample_score + 0.25 * stability - complexity_penalty)

        rejection = ""
        if n < min_events:
            rejection = "low sample size"
        elif edge < min_edge:
            rejection = "insufficient improvement"
        elif score < min_score:
            rejection = "low score"
        elif stability < min_stability:
            rejection = "instability"
        elif complexity_penalty > max_complexity:
            rejection = "complexity penalty"

        condition = f"vol={row.get('vol_bucket', 'na')} & size={row.get('candle_bucket', 'na')}"
        rec = {
            **row,
            "condition_combination": condition,
            "event_count": n,
            "win_rate": max(cont, rev),
            "edge_abs": round(edge, 3),
            "score": round(score, 6),
            "stability": round(stability, 6),
            "complexity_penalty": round(complexity_penalty, 6),
            "rejection_reason": rejection,
        }
        attempted.append(rec)
        if not rejection:
            passed.append(rec)

    attempted.sort(key=lambda x: (-_safe_float(x.get("score"), -1.0), -_safe_float(x.get("event_count"), 0.0)))
    passed.sort(key=lambda x: (-_safe_float(x.get("score"), -1.0), -_safe_float(x.get("event_count"), 0.0)))
    attempted_top_n = int(cfg.get("attempted_top_n", 12))
    return {"passed": passed[:5], "attempted": attempted[:attempted_top_n], "n_attempted": len(attempted), "n_passed": len(passed)}


def _canonical_next_test(text: str) -> str:
    s = " ".join((text or "").strip().lower().split())
    for token in ("test", "tests", "around", "strongest", "best", "setup", "setups"):
        s = s.replace(token, "")
    return " ".join(s.split())


def _dedupe_rank_next_tests(next_tests: List[str], max_n: int) -> Tuple[List[str], List[Dict[str, Any]]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for text in next_tests:
        clean = (text or "").strip()
        if not clean:
            continue
        key = _canonical_next_test(clean)
        grp = "target_refinement"
        if "split" in key or "segment" in key:
            grp = "time_split_validation"
        elif "bucket" in key or "rvol" in key:
            grp = "context_granularity"
        elif "chain" in key:
            grp = "chaining_check"
        rec = buckets.setdefault(key, {"recommendation": clean, "count": 0, "group": grp})
        rec["count"] += 1

    ordered = sorted(buckets.values(), key=lambda x: (-int(x["count"]), str(x["group"]), str(x["recommendation"])))
    top = ordered[:max_n]
    return [str(r["recommendation"]) for r in top], top


def _setup_match_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        row.get("trade_mode"),
        row.get("direction"),
        row.get("tf_minutes"),
        row.get("lookback"),
        row.get("basis"),
        row.get("threshold_mode"),
        row.get("threshold_value"),
        row.get("candle_bucket"),
    )


def _neighbor_analysis(candidates: List[Dict[str, Any]], top_rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not cfg.get("enabled", True):
        return []
    target_values = [int(v) for v in (cfg.get("target_neighbor_values") or [15, 25, 35, 50])]
    max_neighbors = int(cfg.get("max_neighbors", 6))
    out: List[Dict[str, Any]] = []
    for row in top_rows[: int(cfg.get("top_setups", 6))]:
        key = _setup_match_key(row)
        peers = [c for c in candidates if _setup_match_key(c) == key]
        by_target = {int(_safe_float(p.get("target_percent"), -1)): p for p in peers}
        curr_target = int(_safe_float(row.get("target_percent"), 0))

        desired = sorted(set([curr_target, *target_values]))
        for p in peers:
            desired.append(int(_safe_float(p.get("target_percent"), curr_target)))
        desired = sorted(set(desired), key=lambda t: abs(t - curr_target))[:max_neighbors]

        rows: List[Dict[str, Any]] = []
        for t in desired:
            hit = by_target.get(int(t))
            if not hit:
                continue
            rows.append(
                {
                    "target_percent": t,
                    "event_count": int(hit.get("n_events") or 0),
                    "win_rate": hit.get("win_rate"),
                    "score": hit.get("composite_score"),
                    "delta_vs_main": round(_safe_float(hit.get("composite_score")) - _safe_float(row.get("composite_score")), 6),
                }
            )
        rows.sort(key=lambda r: abs(int(r["target_percent"]) - curr_target))
        out.append({"setup_definition": row.get("setup_definition"), "main_target_percent": curr_target, "neighbors": rows})
    return out


def _split_events_for_setup(source: Dict[str, Any], setup: Dict[str, Any], n_splits: int) -> Dict[str, Any]:
    events = (source.get("trade_analysis") or {}).get("trade_events_sample") or []
    if not events:
        return {"setup_definition": setup.get("setup_definition"), "available": False, "reason": "trade_events_sample unavailable"}

    matched = []
    for e in events:
        if e.get("trade_mode") != setup.get("trade_mode"):
            continue
        if int(e.get("tf_minutes") or -1) != int(setup.get("tf_minutes") or -2):
            continue
        if str(e.get("candle_bucket")) != str(setup.get("candle_bucket")):
            continue
        if int(_safe_float(e.get("target_percent"), -1)) != int(_safe_float(setup.get("target_percent"), -2)):
            continue
        matched.append(e)

    if len(matched) < max(12, n_splits * 3):
        return {
            "setup_definition": setup.get("setup_definition"),
            "available": False,
            "reason": f"insufficient sampled events ({len(matched)}) for {n_splits} splits",
        }

    matched.sort(key=lambda e: _extract_time_value(e))
    seg_len = max(1, len(matched) // n_splits)
    names = ["early", "mid", "late"] if n_splits == 3 else [f"split_{i+1}" for i in range(n_splits)]
    split_rows = []
    for i in range(n_splits):
        start = i * seg_len
        end = len(matched) if i == n_splits - 1 else min(len(matched), (i + 1) * seg_len)
        part = matched[start:end]
        if not part:
            continue
        n = len(part)
        wins = sum(1 for e in part if bool(e.get("win")))
        wr = (wins / n) * 100.0 if n else 0.0
        fav = sum(_safe_float(e.get("trade_fav_ticks")) for e in part) / n if n else 0.0
        adv = sum(_safe_float(e.get("trade_adv_ticks")) for e in part) / n if n else 0.0
        expectancy = fav - adv
        score = 0.40 * (wr / 100.0) + 0.20 * _clamp01(math.log10(max(1.0, float(n))) / 2.5) + 0.20 * _clamp01((expectancy + 8.0) / 20.0)
        split_rows.append({"segment": names[i] if i < len(names) else f"split_{i+1}", "event_count": n, "win_rate": round(wr, 3), "score": round(score, 6)})

    return {
        "setup_definition": setup.get("setup_definition"),
        "available": True,
        "coverage_ratio": round(len(matched) / max(1, int(setup.get("n_events") or 0)), 3),
        "splits": split_rows,
    }


def _refine_candidates(candidates: List[Dict[str, Any]], step: int, top_n_parents: int) -> List[Dict[str, Any]]:
    parents = candidates[:top_n_parents]
    rows: List[Dict[str, Any]] = []
    lookup = {}
    for c in candidates:
        key = (
            c.get("trade_mode"),
            c.get("direction"),
            c.get("tf_minutes"),
            c.get("lookback"),
            c.get("basis"),
            c.get("threshold_value"),
            c.get("candle_bucket"),
            int(_safe_float(c.get("target_percent"), 0)),
        )
        lookup[key] = c

    for p in parents:
        base_t = int(_safe_float(p.get("target_percent"), 0))
        for delta in (-step, 0, step):
            t = max(5, base_t + delta)
            key = (
                p.get("trade_mode"),
                p.get("direction"),
                p.get("tf_minutes"),
                p.get("lookback"),
                p.get("basis"),
                p.get("threshold_value"),
                p.get("candle_bucket"),
                t,
            )
            hit = lookup.get(key)
            if hit:
                improvement = round(_safe_float(hit.get("composite_score")) - _safe_float(p.get("composite_score")), 6)
                rows.append(
                    {
                        "parent_setup": p.get("setup_definition"),
                        "refined_target_percent": t,
                        "child_setup": hit.get("setup_definition"),
                        "child_score": hit.get("composite_score"),
                        "score_delta_vs_parent": improvement,
                        "child_win_rate": hit.get("win_rate"),
                        "child_n_events": hit.get("n_events"),
                    }
                )
    rows.sort(key=lambda r: (-_safe_float(r.get("child_score"), -999), -_safe_float(r.get("child_n_events"), 0)))
    return rows


def _chain_candidates(candidates: List[Dict[str, Any]], interactions: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    top_n_parents = int(cfg.get("top_n_parents", 10))
    min_imp = float(cfg.get("min_incremental_improvement", 0.03))
    max_depth = int(cfg.get("max_chain_depth", 3))
    min_remaining = int(cfg.get("min_remaining_events", 50))
    min_score = float(cfg.get("min_score", 0.45))

    accepted: List[Dict[str, Any]] = []
    attempted: List[Dict[str, Any]] = []
    parents = candidates[:top_n_parents]
    for p in parents:
        base_score = _safe_float(p.get("composite_score"), 0.0)
        p_mode = p.get("trade_mode")
        p_dir = p.get("direction")
        for ix in interactions:
            cont = _safe_float(ix.get("cont_win_rate"), 0.0)
            rev = _safe_float(ix.get("rev_win_rate"), 0.0)
            n = int(ix.get("n_observations") or 0)
            better = ix.get("better_mode")
            ix_edge = abs(cont - rev) / 100.0
            improvement = ix_edge * 0.30
            chain_depth = min(max_depth, 2)
            complexity_penalty = 0.04 * (chain_depth - 1)
            chained_score = base_score + improvement - complexity_penalty

            rejection = ""
            if n < min_remaining:
                rejection = "low sample size"
            elif (p_mode == "continuation" and better != "continuation") or (p_mode == "reverse" and better != "reverse"):
                rejection = "insufficient improvement"
            elif improvement < min_imp:
                rejection = "insufficient improvement"
            elif chained_score < min_score:
                rejection = "low score"
            elif _safe_float(p.get("stability_score"), 0.5) < 0.45:
                rejection = "instability"
            elif complexity_penalty > 0.12:
                rejection = "complexity penalty"

            rec = {
                "base_setup": p.get("setup_definition"),
                "trade_mode": p_mode,
                "direction": p_dir,
                "chain_conditions": [
                    f"candle_bucket={p.get('candle_bucket')}",
                    f"interaction={ix.get('vol_bucket', ix.get('candle_bucket', 'context'))}:{ix.get('mean_fav_ticks', 'na')}",
                ],
                "chain_depth": chain_depth,
                "n_events": n,
                "base_score": round(base_score, 6),
                "incremental_improvement": round(improvement, 6),
                "complexity_penalty": round(complexity_penalty, 6),
                "robustness_score": round(_clamp01(_safe_float(p.get("stability_score"), 0.5) - complexity_penalty), 6),
                "composite_score": round(chained_score, 6),
                "win_rate": p.get("win_rate"),
                "rejection_reason": rejection,
            }
            attempted.append(rec)
            if not rejection:
                accepted.append(rec)

    accepted.sort(key=lambda r: (-_safe_float(r.get("composite_score"), -999), -_safe_float(r.get("n_events"), 0)))
    attempted.sort(key=lambda r: (-_safe_float(r.get("composite_score"), -999), -_safe_float(r.get("n_events"), 0)))
    return {
        "candidates": accepted,
        "attempted": attempted[: int(cfg.get("attempted_top_n", 15))],
        "n_attempted": len(attempted),
        "n_passed": len(accepted),
    }


def _compute_setup_family_key(rec: Dict[str, Any]) -> Tuple[str, str, int, str]:
    """Family = (trade_mode, direction, tf_minutes, candle_bucket) — ignores lookback/threshold/basis/target."""
    return (
        str(rec.get("trade_mode", "")),
        str(rec.get("direction", "")),
        int(rec.get("tf_minutes") or 0),
        str(rec.get("candle_bucket", "")),
    )


def _classify_behavior_from_candidates(
    rec: Dict[str, Any],
    curves: Dict[str, Any],
    all_ranked: List[Dict[str, Any]],
    plateau_tolerance_pp: float = 3.0,
) -> str:
    """Classify behavior_type for a setup, falling back to peer-curve inference to avoid 'unknown'."""
    mode   = rec.get("trade_mode", "")
    direc  = rec.get("direction", "")
    bucket = rec.get("candle_bucket", "")
    tf     = rec.get("tf_minutes")
    window = rec.get("window_minutes")

    # Direct lookup
    setup_key = f"{mode}|{direc}|{bucket}|tf{tf}m|w{window}m"
    curve = curves.get(setup_key) or {}
    bt = curve.get("behavior_type", "")
    if bt and bt != "unknown":
        return bt

    # Try family peers with different window values
    family_key = _compute_setup_family_key(rec)
    peer_behaviors: List[str] = []
    for key, cv in curves.items():
        parts = key.split("|")
        if len(parts) < 5:
            continue
        peer_mode, peer_dir, peer_bucket = parts[0], parts[1], parts[2]
        peer_tf_str = parts[3]  # e.g. "tf5m"
        peer_tf = int(peer_tf_str.replace("tf", "").replace("m", "")) if peer_tf_str else 0
        if (peer_mode, peer_dir, peer_tf, peer_bucket) == family_key:
            pbt = cv.get("behavior_type", "")
            if pbt and pbt != "unknown":
                peer_behaviors.append(pbt)

    if peer_behaviors:
        counts: Dict[str, int] = {}
        for b in peer_behaviors:
            counts[b] = counts.get(b, 0) + 1
        dominant = max(counts, key=lambda k: counts[k])
        if counts[dominant] >= 2:
            return dominant
        return peer_behaviors[0]

    # Infer from target-curve data if available — look at win_rate vs target for peer ranked rows
    fam_peers = [r for r in all_ranked if _compute_setup_family_key(r) == family_key]
    if len(fam_peers) >= 3:
        fam_peers.sort(key=lambda r: _safe_float(r.get("target_percent"), 0.0))
        targets = [_safe_float(r.get("target_percent"), 0.0) for r in fam_peers]
        wrs = [_safe_float(r.get("win_rate"), 0.0) for r in fam_peers]
        peak_wr = max(wrs) if wrs else 0.0
        peak_idx = wrs.index(peak_wr)
        peak_target = targets[peak_idx] if targets else 0.0
        # Compute edge decay: last WR vs first WR
        wr_first = wrs[0] if wrs else 0.0
        wr_last = wrs[-1] if wrs else 0.0
        decay_frac = (wr_first - wr_last) / max(1.0, wr_first) if wr_first > 0 else 0.0
        # Runner: peak not at lowest target and plateau visible
        if peak_target > 50.0:
            return "runner"
        # Scalp: peak at low target with sharp decay
        if peak_target <= 50.0 and decay_frac >= 0.20:
            return "scalp"
        return "mixed (limited data)"

    return "mixed (limited data)"


def _classify_curve_stability(plateau_width: int, edge_decay_penalty: float) -> str:
    """Return stable / moderate / fragile classification for a setup's target curve."""
    if plateau_width >= 3 and edge_decay_penalty <= 0.40:
        return "stable"
    if plateau_width >= 2 or edge_decay_penalty <= 0.60:
        return "moderate"
    if plateau_width < 1:
        return "fragile (no plateau)"
    return "fragile"


def _session_table_for_setup(
    rec: Dict[str, Any],
    trade_events_sample: List[Dict[str, Any]],
    min_n: int = 10,
) -> List[Dict[str, Any]]:
    """Build per-session breakdown rows for a given setup from sampled trade events."""
    mode   = rec.get("trade_mode")
    tf     = int(rec.get("tf_minutes") or -1)
    bucket = str(rec.get("candle_bucket", ""))
    target = int(_safe_float(rec.get("target_percent"), -1))

    matched: List[Dict[str, Any]] = []
    for e in (trade_events_sample or []):
        if e.get("trade_mode") != mode:
            continue
        if int(e.get("tf_minutes") or -2) != tf:
            continue
        if str(e.get("candle_bucket", "")) != bucket:
            continue
        if int(_safe_float(e.get("target_percent"), -2)) != target:
            continue
        matched.append(e)

    if not matched:
        return []

    total = len(matched)
    by_session: Dict[str, List[Dict[str, Any]]] = {}
    for e in matched:
        sess = str(e.get("session_bucket") or "unknown")
        by_session.setdefault(sess, []).append(e)

    rows: List[Dict[str, Any]] = []
    for sess, evts in by_session.items():
        n = len(evts)
        if n < min_n:
            continue
        wins = sum(1 for e in evts if bool(e.get("win")))
        wr = (wins / n) * 100.0
        fav = sum(_safe_float(e.get("trade_fav_ticks")) for e in evts) / n
        adv = sum(_safe_float(e.get("trade_adv_ticks")) for e in evts) / n
        rows.append({
            "session": sess,
            "n": n,
            "win_rate": round(wr, 1),
            "expectancy_ticks": round(fav - adv, 2),
            "pct_of_total": round(100.0 * n / total, 1),
        })
    rows.sort(key=lambda r: -r["n"])
    return rows


def _compute_tradability_for_setup(
    rec: Dict[str, Any],
    trade_events_sample: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute events_per_day and P90 adverse excursion from sampled trade events."""
    mode   = rec.get("trade_mode")
    tf     = int(rec.get("tf_minutes") or -1)
    bucket = str(rec.get("candle_bucket", ""))
    target = int(_safe_float(rec.get("target_percent"), -1))

    matched: List[Dict[str, Any]] = []
    for e in (trade_events_sample or []):
        if e.get("trade_mode") != mode:
            continue
        if int(e.get("tf_minutes") or -2) != tf:
            continue
        if str(e.get("candle_bucket", "")) != bucket:
            continue
        if int(_safe_float(e.get("target_percent"), -2)) != target:
            continue
        matched.append(e)

    if not matched:
        return {}

    # Events per day
    dates: set = set()
    for e in matched:
        tv = _extract_time_value(e)
        if tv and len(tv) >= 10:
            dates.add(tv[:10])
    events_per_day = round(len(matched) / max(1, len(dates)), 2) if dates else None

    # P90 adverse excursion
    adv_values = sorted(_safe_float(e.get("trade_adv_ticks"), 0.0) for e in matched)
    p90_idx = int(0.90 * len(adv_values))
    p90_adverse = adv_values[min(p90_idx, len(adv_values) - 1)] if adv_values else None

    return {
        "events_per_day": events_per_day,
        "p90_adverse_excursion_ticks": p90_adverse,
        "unique_trading_days": len(dates) if dates else None,
        "matched_events": len(matched),
    }


def _classify_promotion_level(
    rec: Dict[str, Any],
    curve: Dict[str, Any],
    fragility_flags: List[str],
    promo_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Classify promotion level: observation / candidate / strategy-test-ready."""
    score    = _safe_float(rec.get("composite_score"), 0.0)
    n        = int(rec.get("n_events") or 0)
    plateau  = int(curve.get("plateau_width") or 0)
    edp      = _safe_float(curve.get("edge_decay_penalty"), 1.0)

    str_rdy = promo_cfg.get("strategy_test_ready") or {}
    cand    = promo_cfg.get("candidate") or {}

    # Check strategy-test-ready
    if (
        score >= float(str_rdy.get("min_score", 0.57))
        and n >= int(str_rdy.get("min_n", 120))
        and plateau >= int(str_rdy.get("min_plateau", 2))
        and edp <= float(str_rdy.get("max_edge_decay_penalty", 0.50))
        and "low_sample" not in fragility_flags
    ):
        return {"level": "strategy-test-ready", "reason": f"score={score:.3f}, N={n}, plateau={plateau}, edge_decay={edp:.2f}"}

    # Check candidate
    if (
        score >= float(cand.get("min_score", 0.52))
        and n >= int(cand.get("min_n", 80))
        and plateau >= int(cand.get("min_plateau", 2))
        and edp <= float(cand.get("max_edge_decay_penalty", 0.80))
    ):
        reasons = []
        if score < float(str_rdy.get("min_score", 0.57)):
            reasons.append(f"score {score:.3f} < {str_rdy.get('min_score', 0.57)}")
        if n < int(str_rdy.get("min_n", 120)):
            reasons.append(f"N={n} < {str_rdy.get('min_n', 120)}")
        if edp > float(str_rdy.get("max_edge_decay_penalty", 0.50)):
            reasons.append(f"edge_decay={edp:.2f} > {str_rdy.get('max_edge_decay_penalty', 0.50)}")
        return {"level": "candidate", "reason": "needs: " + "; ".join(reasons) if reasons else "meets candidate thresholds"}

    # Observation
    obs_reasons = []
    if score < float(cand.get("min_score", 0.52)):
        obs_reasons.append(f"score {score:.3f}")
    if n < int(cand.get("min_n", 80)):
        obs_reasons.append(f"N={n}")
    if plateau < int(cand.get("min_plateau", 2)):
        obs_reasons.append(f"plateau={plateau}")
    if edp > float(cand.get("max_edge_decay_penalty", 0.80)):
        obs_reasons.append(f"high edge_decay={edp:.2f}")
    return {"level": "observation", "reason": "insufficient: " + "; ".join(obs_reasons) if obs_reasons else "below thresholds"}


def _group_into_families(
    all_ranked: List[Dict[str, Any]],
    curves: Dict[str, Any],
    sample_thr: Dict[str, Any],
    top_n_families: int = 20,
) -> List[Dict[str, Any]]:
    """Group ranked setups into families by (trade_mode, direction, tf, candle_bucket)."""
    family_map: Dict[Tuple, List[Dict[str, Any]]] = {}
    for rec in all_ranked:
        fk = _compute_setup_family_key(rec)
        family_map.setdefault(fk, []).append(rec)

    fragility_low = int(sample_thr.get("fragility_low", 60))
    suspicious_n  = int(sample_thr.get("suspicious_strength_n", 80))
    suspicious_wr = float(sample_thr.get("suspicious_strength_wr", 70.0))

    families: List[Dict[str, Any]] = []
    for fk, members in family_map.items():
        members.sort(key=lambda r: -_safe_float(r.get("composite_score"), -999))
        best = members[0]

        scores = [_safe_float(r.get("composite_score"), 0.0) for r in members]
        avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0
        best_score = round(max(scores), 4) if scores else 0.0

        # Best curve for this family
        mode   = best.get("trade_mode", "")
        direc  = best.get("direction", "")
        bucket = best.get("candle_bucket", "")
        tf     = best.get("tf_minutes")
        window = best.get("window_minutes")
        setup_key = f"{mode}|{direc}|{bucket}|tf{tf}m|w{window}m"
        curve = curves.get(setup_key) or {}
        bt = _classify_behavior_from_candidates(best, curves, all_ranked)
        plateau = int(curve.get("plateau_width") or 0)
        edp = _safe_float(curve.get("edge_decay_penalty"), 1.0)
        curve_stability = _classify_curve_stability(plateau, edp)

        # Fragility across family
        family_flags: List[str] = []
        n_best = int(best.get("n_events") or 0)
        wr_best = _safe_float(best.get("win_rate"), 0.0)
        if n_best < fragility_low:
            family_flags.append("low_sample")
        if wr_best >= suspicious_wr and n_best < suspicious_n:
            family_flags.append("suspicious_strength")
        if plateau < 1:
            family_flags.append("no_plateau")
        if edp > 0.70:
            family_flags.append("rapid_edge_decay")
        if len(members) > 5:
            family_flags.append("many_variants")

        families.append({
            "family_key": fk,
            "trade_mode":    fk[0],
            "direction":     fk[1],
            "tf_minutes":    fk[2],
            "candle_bucket": fk[3],
            "variant_count": len(members),
            "best_variant":  best,
            "best_score":    best_score,
            "avg_score":     avg_score,
            "behavior_type": bt,
            "plateau_width": plateau,
            "edge_decay_penalty": round(edp, 3),
            "curve_stability": curve_stability,
            "fragility_flags": family_flags,
        })

    families.sort(key=lambda f: (-f["best_score"], -f["variant_count"]))
    return families[:top_n_families]


def _build_family_next_tests(
    families: List[Dict[str, Any]],
    strong: Dict[str, List[Dict[str, Any]]],
    top_n: int = 10,
) -> List[str]:
    """Generate family-specific next-test recommendations."""
    tests: List[str] = []
    for fam in families[:5]:
        fk = fam["family_key"]
        mode, direc, tf, bucket = fk
        best = fam.get("best_variant") or {}
        tgt  = int(_safe_float(best.get("target_percent"), 0))
        plateau = fam.get("plateau_width", 0)
        edp = fam.get("edge_decay_penalty", 1.0)
        bt  = fam.get("behavior_type", "mixed")
        stability = fam.get("curve_stability", "fragile")

        # Session restriction test
        tests.append(
            f"[{mode}/{direc}/tf{tf}m/{bucket}] Restrict to top session (NY Open or overlap) and re-score — "
            f"compare session-filtered WR vs all-session baseline."
        )

        # Target ladder refinement
        if plateau < 2:
            tests.append(
                f"[{mode}/{direc}/tf{tf}m/{bucket}] Sweep targets in 5% steps around {tgt}% to locate any plateau — "
                f"current plateau width is {plateau} (fragile)."
            )
        else:
            tests.append(
                f"[{mode}/{direc}/tf{tf}m/{bucket}] Confirm plateau stability at {tgt}% with a held-out date segment."
            )

        # Basis comparison (body vs range)
        tests.append(
            f"[{mode}/{direc}/tf{tf}m/{bucket}] Compare body-basis vs range-basis qualification — "
            f"check if one basis produces materially better WR."
        )

        # Edge decay warning
        if edp > 0.50:
            tests.append(
                f"[{mode}/{direc}/tf{tf}m/{bucket}] High edge decay ({edp:.2f}) — test target cap at ≤{tgt}% to prevent over-reaching."
            )

    # Context effects
    if strong.get("volume"):
        b = strong["volume"][0]
        tests.append(
            f"Split RVOL '{b.get('bucket')}' bucket into narrower bands to isolate which volume regime drives the edge."
        )

    deduped, _ = _dedupe_rank_next_tests(tests, max_n=top_n)
    return deduped


def _build_rich_executive_summary(
    families: List[Dict[str, Any]],
    top: List[Dict[str, Any]],
    strong: Dict[str, List[Dict[str, Any]]],
    interaction_diag: Dict[str, Any],
    all_ranked: List[Dict[str, Any]],
) -> List[str]:
    """Generate a richer executive summary covering patterns, behavior, sessions, robustness."""
    summary: List[str] = []

    # Best family
    if families:
        f = families[0]
        fk = f["family_key"]
        summary.append(
            f"Strongest setup family: {fk[0]}/{fk[1]}/tf{fk[2]}m/{fk[3]} — "
            f"best score {f['best_score']:.4f}, {f['variant_count']} variants, "
            f"behavior: {f['behavior_type']}, curve: {f['curve_stability']}."
        )

    # Continuation vs reverse dominance
    n_cont = sum(1 for r in all_ranked if r.get("trade_mode") == "continuation")
    n_rev  = sum(1 for r in all_ranked if r.get("trade_mode") == "reverse")
    if n_cont > 0 or n_rev > 0:
        dominant = "continuation" if n_cont >= n_rev else "reverse"
        summary.append(
            f"Direction dominance: {n_cont} continuation setups vs {n_rev} reverse setups pass filters — "
            f"{dominant} has more qualifying setups."
        )

    # Behavior distribution
    behavior_counts: Dict[str, int] = {}
    for f in families:
        bt = f.get("behavior_type", "mixed")
        behavior_counts[bt] = behavior_counts.get(bt, 0) + 1
    if behavior_counts:
        bc_str = ", ".join(f"{k}: {v}" for k, v in sorted(behavior_counts.items(), key=lambda x: -x[1]))
        summary.append(f"Behavior distribution across families: {bc_str}.")

    # Promotion ladder counts
    n_str = sum(1 for f in families if (f.get("best_variant") or {}).get("_promotion_level") == "strategy-test-ready")
    n_cand = sum(1 for f in families if (f.get("best_variant") or {}).get("_promotion_level") == "candidate")
    if n_str > 0 or n_cand > 0:
        summary.append(f"Promotion ladder: {n_str} strategy-test-ready, {n_cand} candidate families.")

    # Robustness vs fragility
    n_stable  = sum(1 for f in families if f.get("curve_stability") == "stable")
    n_fragile = sum(1 for f in families if "fragile" in (f.get("curve_stability") or ""))
    if n_stable + n_fragile > 0:
        summary.append(f"Robustness: {n_stable} families with stable curves, {n_fragile} fragile.")

    # Context effects
    if strong.get("volume"):
        b = strong["volume"][0]
        summary.append(
            f"Strongest volume effect: '{b.get('bucket')}' (edge={b.get('edge_abs')} pp, N={b.get('n_observations', 0)})."
        )
    if strong.get("structure"):
        b = strong["structure"][0]
        summary.append(
            f"Strongest structure effect: '{b.get('bucket')}' (edge={b.get('edge_abs')} pp, N={b.get('n_observations', 0)})."
        )

    # Interaction effects
    if interaction_diag.get("n_passed", 0) > 0:
        summary.append(f"{interaction_diag['n_passed']} interaction conditions passed final filters.")
    elif interaction_diag.get("n_attempted", 0) > 0:
        summary.append("Interaction effects tested but none passed final quality filters.")

    return summary


def _best_sessions_for_mode(
    session_by: List[Dict[str, Any]],
    mode: str,
    top_n: int = 2,
) -> List[str]:
    """Return up to top_n session names where the given trade mode has highest win rate."""
    wr_key = "cont_win_rate" if mode == "continuation" else "rev_win_rate"
    valid = [s for s in (session_by or []) if s.get(wr_key) is not None and int(s.get("n_observations") or 0) >= 20]
    valid.sort(key=lambda s: -_safe_float(s.get(wr_key), 0.0))
    return [s["session_bucket"] for s in valid[:top_n] if "session_bucket" in s]


def _build_why_narrative(
    rec: Dict[str, Any],
    behavior_type: str,
    plateau_width: int,
    stable_range: List,
    best_sessions: List[str],
    fragility_flags: List[str],
) -> str:
    """Build a one-paragraph plain-language narrative explaining why this setup is interesting."""
    mode = rec.get("trade_mode", "continuation")
    wr = _safe_float(rec.get("win_rate"), 0.0)
    n = int(rec.get("n_events") or 0)
    bucket = rec.get("candle_bucket", "?")
    tf = rec.get("tf_minutes", "?")
    window = rec.get("window_minutes", "?")
    target_pct = int(_safe_float(rec.get("target_percent"), 0))

    parts: List[str] = []

    # Trade direction
    if mode == "continuation":
        parts.append(f"Enter in the direction of a {bucket}-tick large candle on the {tf}m chart.")
    else:
        parts.append(f"Fade (reverse) a {bucket}-tick large candle on the {tf}m chart.")

    # Behavior
    if behavior_type == "runner":
        r_lo = stable_range[0] if stable_range and stable_range[0] is not None else "?"
        r_hi = stable_range[1] if stable_range and stable_range[1] is not None else "?"
        parts.append(
            f"Target curve shows a runner profile — win rate is stable across a wide range "
            f"({plateau_width} consecutive steps); the plateau spans {r_lo}%–{r_hi}% of candle size, "
            f"giving flexibility in target placement."
        )
    elif behavior_type == "scalp":
        parts.append(
            f"Target curve shows a scalp profile — the edge is concentrated near low target percentages "
            f"and decays sharply at higher targets; take profit quickly at or below {target_pct}%."
        )
    else:
        parts.append(
            f"Target curve shows mixed behavior — neither a pure scalp nor a clean runner; "
            f"use the stable target range for guidance and confirm with time-split robustness."
        )

    # Win rate observation
    if wr >= 65:
        parts.append(f"Win rate of {wr:.1f}% ({n} events) is strong.")
    elif wr >= 55:
        parts.append(f"Win rate of {wr:.1f}% ({n} events) is solid.")
    else:
        parts.append(f"Win rate of {wr:.1f}% ({n} events) is marginal; relies on positive expectancy.")

    # Session context
    if best_sessions:
        parts.append(f"Historically strongest in: {', '.join(best_sessions)}.")

    # Fragility
    if "low_sample" in fragility_flags:
        parts.append("⚠ Small sample — validate before use.")
    if "rapid_edge_decay" in fragility_flags:
        parts.append("⚠ Edge decays quickly — keep targets tight.")
    if "suspicious_strength" in fragility_flags:
        parts.append("⚠ High WR on limited sample — may be noise.")
    if "neighbor_instability" in fragility_flags:
        parts.append("⚠ Parameter sensitivity detected — test neighboring values.")

    return " ".join(parts)


def _build_strategy_cards(
    top_discoveries: List[Dict[str, Any]],
    lce: Dict[str, Any],
    top_n: int,
    min_sample_thresholds: Optional[Dict[str, Any]] = None,
    promo_cfg: Optional[Dict[str, Any]] = None,
    all_ranked: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Build enriched strategy card dicts from top discoveries + target curve + session context."""
    curves = (lce.get("target_curves") or {}).get("curves") or {}
    session_by = (
        (lce.get("context_analysis") or {})
        .get("session_context", {})
        .get("by_session") or []
    )
    trade_events_sample = (lce.get("trade_analysis") or {}).get("trade_events_sample") or []

    st = min_sample_thresholds or {}
    fragility_low = int(st.get("fragility_low", 60))
    suspicious_n  = int(st.get("suspicious_strength_n", 80))
    suspicious_wr = float(st.get("suspicious_strength_wr", 70.0))

    _all_ranked = all_ranked or top_discoveries

    cards: List[Dict[str, Any]] = []
    for rank, rec in enumerate(top_discoveries[:top_n], 1):
        tf     = rec.get("tf_minutes")
        window = rec.get("window_minutes")
        mode   = rec.get("trade_mode", "continuation")
        direc  = rec.get("direction", "?")
        bucket = rec.get("candle_bucket", "?")

        # Find matching target curve
        setup_key = f"{mode}|{direc}|{bucket}|tf{tf}m|w{window}m"
        curve = curves.get(setup_key) or {}

        # Behavior type — never "unknown"
        behavior_type     = _classify_behavior_from_candidates(rec, curves, _all_ranked)
        plateau_width     = int(curve.get("plateau_width") or 0)
        stable_range      = curve.get("stable_target_range") or [None, None]
        peak_target_pct   = curve.get("peak_target_pct")
        edge_decay_penalty = _safe_float(curve.get("edge_decay_penalty"), 0.0)
        curve_stability   = _classify_curve_stability(plateau_width, edge_decay_penalty)

        if stable_range and stable_range[0] is not None and stable_range[1] is not None:
            stable_range_str = f"{stable_range[0]:.0f}%\u2013{stable_range[1]:.0f}%"
        else:
            stable_range_str = None

        # Session context for this trade mode
        best_sessions = _best_sessions_for_mode(session_by, mode, top_n=2)

        # Fragility flags
        n  = int(rec.get("n_events") or 0)
        wr = _safe_float(rec.get("win_rate"), 0.0)
        stability = _safe_float(rec.get("stability_score"), 0.5)
        fragility_flags: List[str] = []
        if n < fragility_low:
            fragility_flags.append("low_sample")
        if wr >= suspicious_wr and n < suspicious_n:
            fragility_flags.append("suspicious_strength")
        if stability < 0.45:
            fragility_flags.append("neighbor_instability")
        if edge_decay_penalty > 0.70:
            fragility_flags.append("rapid_edge_decay")
        if plateau_width < 1:
            fragility_flags.append("no_plateau")

        # Promotion level
        promotion = _classify_promotion_level(rec, curve, fragility_flags, promo_cfg or {})

        # Per-session breakdown table
        session_table = _session_table_for_setup(rec, trade_events_sample)

        # Tradability metrics
        tradability_ext = _compute_tradability_for_setup(rec, trade_events_sample)

        # Why-interesting narrative
        why = _build_why_narrative(rec, behavior_type, plateau_width, stable_range, best_sessions, fragility_flags)

        # Threshold label
        tmode = rec.get("threshold_mode", "multiplier")
        tval  = rec.get("threshold_value")
        threshold_label = f"{tval}\u00d7" if tmode == "multiplier" else str(tval)

        cards.append({
            "rank": rank,
            "setup_definition": rec.get("setup_definition", ""),
            "why_interesting": why,
            "promotion": promotion,
            "conditions": {
                "trade_mode":    mode,
                "direction":     direc,
                "timeframe_min": tf,
                "window_minutes":window,
                "candle_bucket": bucket,
                "lookback":      rec.get("lookback"),
                "basis":         rec.get("basis"),
                "threshold":     threshold_label,
                "target_percent":rec.get("target_percent"),
            },
            "metrics": {
                "behavior_type":    behavior_type,
                "win_rate":         rec.get("win_rate"),
                "n_events":         n,
                "composite_score":  rec.get("composite_score"),
                "plateau_width":    plateau_width,
                "curve_stability":  curve_stability,
                "expectancy_ticks": rec.get("expectancy_ticks"),
                "edge_decay_penalty": edge_decay_penalty,
                "stability_score":  rec.get("stability_score"),
                "sample_score":     rec.get("sample_score"),
                "sparse_penalty":   rec.get("sparse_penalty"),
            },
            "targets": {
                "stable_range":       stable_range_str,
                "peak_target_pct":    peak_target_pct,
                "raw_stable_range":   stable_range,
            },
            "session_context": {
                "best_sessions": best_sessions,
                "session_table": session_table,
            },
            "tradability": tradability_ext,
            "fragility_flags": fragility_flags,
        })

    return cards


def build_large_candle_excursion_findings(source_lce: Optional[Dict[str, Any]], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = _deep_merge(DEFAULT_FINDINGS_CONFIG, config or {})
    source, err = _extract_lce(source_lce)
    if err:
        return {"enabled": True, "has_source": False, "message": err, "config": cfg}

    min_events   = int(cfg.get("min_events", 30))
    min_wr       = float((cfg.get("ranking") or {}).get("require_min_win_rate", 0.5)) * 100.0
    ranked_n     = int((cfg.get("output") or {}).get("top_n_discoveries", 25))
    top_n_cards  = int((cfg.get("output") or {}).get("top_n_strategy_cards", 10))
    scoring_w    = cfg.get("scoring_weights") or {}
    sample_thr   = cfg.get("min_sample_thresholds") or {}

    candidates = _build_trade_candidates(source, min_events=min_events, min_win_rate_pct=min_wr)

    # Build scoring formula description from active weights
    w_wr   = float(scoring_w.get("win_rate",          0.40))
    w_samp = float(scoring_w.get("sample_score",      0.20))
    w_exp  = float(scoring_w.get("expectancy_score",  0.20))
    w_stab = float(scoring_w.get("stability",         0.15))
    w_simp = float(scoring_w.get("simplicity",        0.05))
    formula_str = (
        f"{w_wr}*win_rate + {w_samp}*sample_score + {w_exp}*expectancy_score "
        f"+ {w_stab}*stability + {w_simp}*simplicity - sparse_penalty"
    )

    if not candidates:
        return {
            "enabled": True,
            "has_source": True,
            "message": "source analytics present but no findings met thresholds",
            "config": cfg,
            "top_discoveries": [],
            "strategy_cards": [],
            "executive_summary": [],
            "fragility_warnings": [],
            "next_tests": [],
            "scoring_methodology": {"formula": formula_str},
        }

    ranked = _score_candidates(
        candidates,
        penalize_low_sample=bool((cfg.get("ranking") or {}).get("penalize_low_sample", True)),
        penalize_complexity=bool((cfg.get("ranking") or {}).get("penalize_complexity", True)),
        scoring_weights=scoring_w,
        min_sample_thresholds=sample_thr,
    )

    all_ranked = [r.record for r in ranked]
    top = all_ranked[:ranked_n]
    top_cont = next((r for r in all_ranked if r.get("trade_mode") == "continuation"), None)
    top_rev = next((r for r in all_ranked if r.get("trade_mode") == "reverse"), None)

    context = source.get("context_analysis") or {}
    strong = _strong_context_effects(context)
    raw_interactions = ((context.get("interactions") or {}).get("vol_x_size") or [])
    interaction_diag = _evaluate_interaction_candidates(raw_interactions, cfg.get("interactions") or {})

    # Setup families
    curves = (source.get("target_curves") or {}).get("curves") or {}
    promo_cfg  = cfg.get("promotion") or {}
    family_cfg = cfg.get("family_grouping") or {}
    families = _group_into_families(
        all_ranked, curves, sample_thr,
        top_n_families=int(family_cfg.get("top_n_families", 20)),
    )

    # Annotate all_ranked records with promotion level (used by families section)
    for rec in all_ranked:
        mode   = rec.get("trade_mode", "")
        direc  = rec.get("direction", "")
        bucket = rec.get("candle_bucket", "")
        tf     = rec.get("tf_minutes")
        window = rec.get("window_minutes")
        setup_key = f"{mode}|{direc}|{bucket}|tf{tf}m|w{window}m"
        curve = curves.get(setup_key) or {}
        n  = int(rec.get("n_events") or 0)
        wr = _safe_float(rec.get("win_rate"), 0.0)
        edp = _safe_float(curve.get("edge_decay_penalty"), 1.0)
        plateau = int(curve.get("plateau_width") or 0)
        flags: List[str] = []
        fragility_low_v = int(sample_thr.get("fragility_low", 60))
        if n < fragility_low_v:
            flags.append("low_sample")
        rec["_promotion_level"] = _classify_promotion_level(rec, curve, flags, promo_cfg).get("level", "observation")

    # Reversal size analysis — large-move probability, runner potential, session breakdown
    events_sample = source.get("events_sample") or []
    rsa_cfg = _deep_merge(DEFAULT_REVERSAL_SIZE_CONFIG, cfg.get("reversal_size_analysis") or {})
    reversal_size_analysis_result = compute_reversal_size_analysis(events_sample, rsa_cfg)

    # Context-conditioned setups (uses enriched events_sample)
    context_conditioned_setups = _compute_context_conditioned_setups(
        events_sample=events_sample,
        context_analysis=context,
        min_n=max(20, int(cfg.get("min_events", 30) // 2)),
        min_lift_pp=3.0,
        top_n=25,
    )

    # Rich executive summary
    neighbor = _neighbor_analysis(all_ranked, [r for r in [top_cont, top_rev] if r], cfg.get("neighbor_analysis") or {})
    for nb in neighbor:
        deltas = [abs(_safe_float(r.get("delta_vs_main"))) for r in (nb.get("neighbors") or []) if r.get("delta_vs_main") is not None]
        if deltas and (sum(deltas) / len(deltas)) <= 0.03:
            all_ranked_extra_note = f"Top discovery appears stable across nearby parameters: {nb.get('setup_definition')}."
        else:
            all_ranked_extra_note = None
    summary = _build_rich_executive_summary(families, top, strong, interaction_diag, all_ranked)

    fragility_low_v  = int(sample_thr.get("fragility_low",          60))
    suspicious_n     = int(sample_thr.get("suspicious_strength_n",  80))
    suspicious_wr    = float(sample_thr.get("suspicious_strength_wr", 70.0))

    fragility: List[Dict[str, Any]] = []
    for rec in top:
        n = int(rec.get("n_events") or 0)
        wr = _safe_float(rec.get("win_rate"), 0.0)
        st = _safe_float(rec.get("stability_score"), 0.5)
        mode   = rec.get("trade_mode", "")
        direc  = rec.get("direction", "")
        bucket = rec.get("candle_bucket", "")
        tf     = rec.get("tf_minutes")
        window = rec.get("window_minutes")
        setup_key = f"{mode}|{direc}|{bucket}|tf{tf}m|w{window}m"
        curve = curves.get(setup_key) or {}
        plateau = int(curve.get("plateau_width") or 0)
        edp = _safe_float(curve.get("edge_decay_penalty"), 0.0)
        if n < fragility_low_v:
            fragility.append({"type": "low_sample", "severity": "high", "setup": rec.get("setup_definition"), "details": f"Only {n} events"})
        if wr >= suspicious_wr and n < suspicious_n:
            fragility.append({
                "type": "suspicious_strength", "severity": "medium",
                "setup": rec.get("setup_definition"),
                "details": f"High WR ({wr:.1f}%) on limited sample ({n})",
            })
        if st < 0.45:
            fragility.append({
                "type": "neighbor_instability", "severity": "medium",
                "setup": rec.get("setup_definition"),
                "details": f"Low neighbor stability ({st:.2f})",
            })
        if plateau < 1:
            fragility.append({
                "type": "no_plateau", "severity": "high",
                "setup": rec.get("setup_definition"),
                "details": "No target plateau — edge may not be stable across target levels",
            })
        if edp > 0.70:
            fragility.append({
                "type": "rapid_edge_decay", "severity": "medium",
                "setup": rec.get("setup_definition"),
                "details": f"High edge decay penalty ({edp:.2f})",
            })

    # Family-specific next tests
    max_next = int((cfg.get("output") or {}).get("max_next_tests", 10))
    deduped_tests = _build_family_next_tests(families, strong, top_n=max_next)
    ranked_test_rows: List[Dict[str, Any]] = [{"recommendation": t, "count": 1, "group": "family_specific"} for t in deduped_tests]

    split_cfg = cfg.get("time_split") or {}
    split_rows = []
    if split_cfg.get("enabled", True):
        for setup in [r for r in [top_cont, top_rev] if r]:
            split_rows.append(_split_events_for_setup(source, setup, int(split_cfg.get("n_splits", 3))))

    # Build strategy cards (enriched with promotion, session table, tradability)
    strategy_cards = _build_strategy_cards(
        top, lce=source, top_n=top_n_cards,
        min_sample_thresholds=sample_thr,
        promo_cfg=promo_cfg,
        all_ranked=all_ranked,
    )

    return {
        "enabled": True,
        "has_source": True,
        "source_key": "large_candle_excursion",
        "config": cfg,
        "scoring_methodology": {
            "formula": formula_str,
            "components": ["win_rate", "sample_size", "expectancy_ticks", "neighbor_stability", "simplicity", "sparse_penalty"],
            "weights": {"win_rate": w_wr, "sample_score": w_samp, "expectancy_score": w_exp, "stability": w_stab, "simplicity": w_simp},
        },
        "executive_summary": summary,
        "top_discoveries": top,
        "setup_families": families,
        "strategy_cards": strategy_cards,
        "strong_context_effects": strong,
        "strongest_interactions": interaction_diag.get("passed") or [],
        "interaction_diagnostics": interaction_diag,
        "neighbor_analysis": neighbor,
        "time_split_robustness": split_rows,
        "fragility_warnings": fragility,
        "next_tests": deduped_tests,
        "next_tests_ranked": ranked_test_rows,
        "context_conditioned_setups": context_conditioned_setups,
        "reversal_size_analysis":     reversal_size_analysis_result,
        "diagnostics": {
            "n_candidates_screened": len(candidates),
            "n_ranked": len(ranked),
            "n_top": len(top),
            "n_families": len(families),
            "interaction_attempted": interaction_diag.get("n_attempted", 0),
            "interaction_passed": interaction_diag.get("n_passed", 0),
            "n_context_conditioned": len(context_conditioned_setups),
        },
    }


def _compute_context_conditioned_setups(
    events_sample: List[Dict[str, Any]],
    context_analysis: Dict[str, Any],
    min_n: int = 30,
    min_lift_pp: float = 3.0,
    top_n: int = 20,
) -> List[Dict[str, Any]]:
    """
    Build context-conditioned setup rankings from enriched events sample.

    For each (direction, tf_minutes, candle_bucket) base family, compute
    per-context-bucket win rates and find conditions with significant lift
    above the population baseline.

    Returns a ranked list of dicts with keys:
        family_label, context_feature, context_bucket,
        n, cont_wr, baseline_cont_wr, lift_pp, edge_pp,
        description
    """
    if not events_sample:
        return []

    # Population baseline from context_analysis
    baseline = (context_analysis.get("population_baseline") or {})
    pop_cont_wr = _safe_float(baseline.get("cont_win_rate"), None)
    if pop_cont_wr is None:
        return []

    # Context feature columns to evaluate
    context_features = [
        ("directional_context_label", "Directional Context"),
        ("prev_direction_bucket",     "Prev Direction"),
        ("streak_bucket",             "Direction Streak"),
        ("engulf_bucket",             "Engulf"),
        ("vwap_signed_bucket",        "VWAP Location (signed)"),
        ("ma100_signed_bucket",       "MA100 Location (signed)"),
        ("ma200_signed_bucket",       "MA200 Location (signed)"),
        ("vwap_location_bucket",      "VWAP Location"),
        ("ma100_location_bucket",     "MA100 Location"),
        ("ma200_location_bucket",     "MA200 Location"),
        ("nearest_level_type",        "Nearest Level"),
        ("level_interaction_label",   "Level Interaction"),
        ("trend_alignment_label",     "Trend Alignment"),
        ("ma100_ext_bucket",          "MA100 Extension"),
        ("vwap_ext_bucket",           "VWAP Extension"),
        ("exhaustion_label",          "Exhaustion Type"),
    ]

    # Group events by base family
    family_groups: Dict[str, List[Dict]] = {}
    for ev in events_sample:
        direction = int(ev.get("direction", 0))
        tf_min    = ev.get("tf_minutes")
        cb        = ev.get("candle_bucket", "unknown")
        fam_label = f"{'Bull' if direction == 1 else 'Bear'} / {tf_min}m / {cb}"
        family_groups.setdefault(fam_label, []).append(ev)

    results: List[Dict[str, Any]] = []

    for fam_label, fam_events in family_groups.items():
        if len(fam_events) < min_n:
            continue

        # Family baseline: need fav/adv/size to compute win rate
        fam_wins = 0
        fam_valid = 0
        for ev in fam_events:
            fav  = _safe_float(ev.get("fav_ticks"))
            adv  = _safe_float(ev.get("adv_ticks"))
            size = _safe_float(ev.get("size_ticks") or ev.get("body_ticks"))
            if fav is None or adv is None or size is None or size <= 0:
                continue
            fam_valid += 1
            target = size * 0.5  # 50% of signal candle as simple target
            if fav >= target:
                fam_wins += 1
        if fam_valid < min_n:
            continue
        fam_cont_wr = round(fam_wins / fam_valid * 100, 1)

        # For each context feature
        for feat_col, feat_label in context_features:
            # Group events by this feature's bucket
            bucket_groups: Dict[str, List[Dict]] = {}
            for ev in fam_events:
                val = ev.get(feat_col)
                if val is None or str(val) in ("unknown", "other", "nan"):
                    continue
                bucket_groups.setdefault(str(val), []).append(ev)

            for bucket_val, bucket_events in bucket_groups.items():
                if len(bucket_events) < min_n:
                    continue
                # Compute continuation WR for this bucket
                b_wins  = 0
                b_valid = 0
                for ev in bucket_events:
                    fav  = _safe_float(ev.get("fav_ticks"))
                    adv  = _safe_float(ev.get("adv_ticks"))
                    size = _safe_float(ev.get("size_ticks") or ev.get("body_ticks"))
                    if fav is None or adv is None or size is None or size <= 0:
                        continue
                    b_valid += 1
                    target = size * 0.5
                    if fav >= target:
                        b_wins += 1
                if b_valid < min_n:
                    continue
                b_cont_wr = round(b_wins / b_valid * 100, 1)

                # WR for reverse (adv hits target)
                r_wins = sum(
                    1 for ev in bucket_events
                    if _safe_float(ev.get("adv_ticks")) is not None
                    and _safe_float(ev.get("size_ticks") or ev.get("body_ticks"), 0) > 0
                    and _safe_float(ev.get("adv_ticks")) >= (_safe_float(ev.get("size_ticks") or ev.get("body_ticks"), 1)) * 0.5
                )
                b_rev_wr = round(r_wins / b_valid * 100, 1) if b_valid > 0 else None

                lift_pp = round(b_cont_wr - pop_cont_wr, 1)
                edge_pp = round(abs(b_cont_wr - (b_rev_wr or 50.0)), 1)

                # Only include if lift is meaningful
                if abs(lift_pp) < min_lift_pp:
                    continue

                better = "continuation" if b_cont_wr > (b_rev_wr or 50.0) + 2 else (
                    "reversal" if (b_rev_wr or 50.0) > b_cont_wr + 2 else "neutral")

                # Build human-readable description
                desc_parts = [fam_label]
                if feat_col in ("prev_direction_bucket", "streak_bucket", "engulf_bucket"):
                    desc_parts.append(f"prev={bucket_val}")
                elif feat_col in ("vwap_location_bucket", "ma100_location_bucket", "ma200_location_bucket"):
                    desc_parts.append(bucket_val.replace("_", " "))
                elif feat_col in ("level_interaction_label", "nearest_level_type"):
                    desc_parts.append(bucket_val.replace("_", " "))
                elif feat_col == "trend_alignment_label":
                    desc_parts.append(f"trend={bucket_val}")
                else:
                    desc_parts.append(f"{feat_label.lower()}={bucket_val}")

                results.append({
                    "family_label":       fam_label,
                    "context_feature":    feat_label,
                    "context_col":        feat_col,
                    "context_bucket":     bucket_val,
                    "n":                  b_valid,
                    "cont_wr":            b_cont_wr,
                    "rev_wr":             b_rev_wr,
                    "family_cont_wr":     fam_cont_wr,
                    "baseline_cont_wr":   pop_cont_wr,
                    "lift_pp":            lift_pp,
                    "edge_pp":            edge_pp,
                    "better":             better,
                    "description":        " / ".join(desc_parts),
                })

    # Rank by abs(lift_pp) descending, then n descending
    results.sort(key=lambda r: (-abs(r["lift_pp"]), -r["n"]))
    return results[:top_n]


def build_large_candle_excursion_discovery(source_lce: Optional[Dict[str, Any]], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = _deep_merge(DEFAULT_DISCOVERY_CONFIG, config or {})
    source, err = _extract_lce(source_lce)
    if err:
        return {"enabled": True, "has_source": False, "message": err, "config": cfg}

    obj = cfg.get("objective") or {}
    min_events = int(obj.get("min_events", 30))
    min_wr = float(obj.get("min_win_rate", 0.5)) * 100.0

    broad_cfg = ((cfg.get("stages") or {}).get("broad_scan") or {})
    refinement_cfg = ((cfg.get("stages") or {}).get("refinement") or {})
    chain_cfg = ((cfg.get("stages") or {}).get("interaction_chaining") or {})
    robust_cfg = ((cfg.get("stages") or {}).get("robustness_validation") or {})

    base = _build_trade_candidates(source, min_events=min_events, min_win_rate_pct=min_wr)
    ranked = _score_candidates(base, penalize_low_sample=True, penalize_complexity=True)
    broad_keep = int(broad_cfg.get("top_n_to_keep", 50))
    broad_candidates = [r.record for r in ranked[:broad_keep]]

    if not broad_candidates:
        return {
            "enabled": True,
            "has_source": True,
            "message": "source analytics present but no candidates met discovery thresholds",
            "config": cfg,
            "broad_scan": {"candidates": [], "n_evaluated": 0, "n_retained": 0},
        }

    refinement_rows: List[Dict[str, Any]] = []
    if refinement_cfg.get("enabled", True):
        step = int(((refinement_cfg.get("refinement_rules") or {}).get("target_percent_step", 10)))
        refinement_rows = _refine_candidates(broad_candidates, step=step, top_n_parents=int(refinement_cfg.get("top_n_parents", 20)))

    interactions = ((source.get("context_analysis") or {}).get("interactions") or {}).get("vol_x_size") or []
    chaining = {"candidates": [], "attempted": [], "n_attempted": 0, "n_passed": 0}
    if chain_cfg.get("enabled", True):
        chaining = _chain_candidates(broad_candidates, interactions, chain_cfg)

    robust_rows: List[Dict[str, Any]] = []
    if robust_cfg.get("enabled", True):
        for c in broad_candidates:
            st = _safe_float(c.get("stability_score"), 0.5)
            split_instability = max(0.0, 0.65 - st)
            oos_required = bool(robust_cfg.get("require_out_of_sample_check", False))
            oos_penalty = 0.10 if oos_required else 0.0
            robust_rows.append(
                {
                    "setup_definition": c.get("setup_definition"),
                    "neighbor_stability": round(st, 6),
                    "split_instability_penalty": round(split_instability, 6),
                    "oos_check_required": oos_required,
                    "oos_penalty": round(oos_penalty, 6),
                    "robustness_score": round(_clamp01(st - split_instability - oos_penalty), 6),
                }
            )

    final_pool = list(broad_candidates)
    final_pool.extend(chaining.get("candidates") or [])
    final_pool.sort(key=lambda r: (-_safe_float(r.get("composite_score"), -999), -_safe_float(r.get("n_events"), 0)))
    top_final_n = int((cfg.get("output") or {}).get("top_n_final_discoveries", 25))
    final_rows = final_pool[:top_final_n]

    neighbor = _neighbor_analysis(broad_candidates, final_rows, cfg.get("neighbor_analysis") or {})
    time_split_rows = [_split_events_for_setup(source, setup, int(robust_cfg.get("time_splits", 3))) for setup in final_rows[:5]]

    diagnostics = {
        "n_broad_evaluated": len(base),
        "n_broad_retained": len(broad_candidates),
        "n_refinement_rows": len(refinement_rows),
        "n_chain_rows": len(chaining.get("candidates") or []),
        "n_chain_attempted": chaining.get("n_attempted", 0),
        "n_robust_rows": len(robust_rows),
        "n_final": len(final_rows),
        "dropped_low_sample_or_winrate": max(0, len((source.get("trade_analysis") or {}).get("trade_combo_results") or []) - len(base)),
    }

    cautions: List[str] = []
    if diagnostics["n_chain_rows"] == 0:
        cautions.append("No chained setups improved results beyond simple configurations.")
    if any(_safe_float(r.get("robustness_score"), 0.0) < 0.45 for r in robust_rows[:10]):
        cautions.append("Some top candidates show weak neighbor stability; treat as fragile until validated out-of-sample.")
    if any(not bool(s.get("available")) for s in time_split_rows):
        cautions.append("Time split robustness is limited when sampled trade events are sparse.")

    summary = {
        "strongest_broad_scan": broad_candidates[0] if broad_candidates else None,
        "strongest_refined": refinement_rows[0] if refinement_rows else None,
        "strongest_chain": (chaining.get("candidates") or [None])[0],
        "major_cautions": cautions,
    }

    next_steps = [
        "Validate top 5 final discoveries on a later time segment.",
        "For strongest target-based setup, sweep ±10% around target to confirm local plateau.",
        "Drop candidates with robustness_score < 0.45 unless additional evidence appears.",
    ]

    return {
        "enabled": True,
        "has_source": True,
        "source_key": "large_candle_excursion",
        "config": cfg,
        "scoring_methodology": {
            "candidate_score": "same composite score as findings",
            "chain_score": "base_score + incremental_improvement - complexity_penalty",
            "robustness": "neighbor_stability - split_instability_penalty - optional_oos_penalty",
        },
        "summary": summary,
        "broad_scan": {
            "candidates": broad_candidates,
            "n_evaluated": len(base),
            "n_retained": len(broad_candidates),
        },
        "refinement": {"candidates": refinement_rows},
        "interaction_chaining": chaining,
        "robustness_validation": {"candidates": robust_rows, "time_splits": time_split_rows},
        "neighbor_analysis": neighbor,
        "final_discoveries": final_rows,
        "diagnostics": diagnostics,
        "next_steps": next_steps,
    }
