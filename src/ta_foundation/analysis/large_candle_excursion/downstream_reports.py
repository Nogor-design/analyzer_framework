from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import math


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
    "output": {
        "top_n_discoveries": 25,
        "include_executive_summary": True,
        "include_fragility_warnings": True,
        "include_suggested_next_tests": True,
    },
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
        },
        "robustness_validation": {
            "enabled": True,
            "time_splits": 3,
            "require_neighbor_stability": True,
            "require_out_of_sample_check": False,
        },
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

        expectancy_ticks = _safe_float(c.get("avg_trade_fav_ticks")) - _safe_float(c.get("avg_trade_adv_ticks"))
        out.append(
            {
                **c,
                "expectancy_ticks": round(expectancy_ticks, 3),
                "avg_target_ticks": c.get("avg_target_ticks"),
                "median_target_ticks": c.get("median_target_ticks"),
                "avg_favorable_ticks": c.get("avg_trade_fav_ticks"),
                "median_favorable_ticks": c.get("median_trade_fav_ticks"),
                "avg_adverse_ticks": c.get("avg_trade_adv_ticks"),
                "median_adverse_ticks": c.get("median_trade_adv_ticks"),
                "stop_hit_rate": (
                    round((_safe_float(c.get("n_stop_hit"), 0.0) / max(_safe_float(c.get("n_events"), 1.0), 1.0)) * 100.0, 2)
                    if c.get("n_stop_hit") is not None
                    else None
                ),
                "win_rate_frac": round(wr / 100.0, 6),
                "setup_definition": (
                    f"{c.get('trade_mode', '?')} | tf={c.get('tf_minutes', '?')}m | "
                    f"bucket={c.get('candle_bucket', '?')} | target={c.get('target_percent', '?')}%"
                ),
            }
        )
    return out


def _neighbor_stability(candidates: List[Dict[str, Any]], row: Dict[str, Any]) -> float:
    peers = [
        c for c in candidates
        if c.get("trade_mode") == row.get("trade_mode")
        and c.get("direction") == row.get("direction")
        and c.get("candle_bucket") == row.get("candle_bucket")
        and c.get("tf_minutes") == row.get("tf_minutes")
        and c.get("lookback") == row.get("lookback")
    ]
    if len(peers) <= 1:
        return 0.5

    target = _safe_float(row.get("target_percent"), 0.0)
    wr = _safe_float(row.get("win_rate"), 0.0)
    close_peers = sorted(peers, key=lambda p: abs(_safe_float(p.get("target_percent"), 0.0) - target))[:4]
    diffs = [abs(wr - _safe_float(p.get("win_rate"), wr)) for p in close_peers if p is not row]
    if not diffs:
        return 0.5
    avg_diff = sum(diffs) / len(diffs)
    return _clamp01(1.0 - avg_diff / 25.0)


def _score_candidates(candidates: List[Dict[str, Any]], penalize_low_sample: bool, penalize_complexity: bool) -> List[ScoredSetup]:
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
        if penalize_low_sample and n < 80:
            sparse_penalty = (80.0 - n) / 200.0

        score = (
            0.40 * wr
            + 0.20 * sample_score
            + 0.20 * exp_score
            + 0.15 * stability
            + 0.05 * simplicity
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
    interactions = context_analysis.get("interactions") or {}

    return {
        "volume": _top(vol, "vol_bucket"),
        "structure": _top(struct, "close_pos_bucket"),
        "volatility": _top(volat, "atr_bucket"),
        "interactions": _top(interactions.get("vol_x_size") or [], "vol_bucket"),
    }


def _dedupe_ranked_recommendations(lines: List[str]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for raw in lines:
        key = " ".join(str(raw or "").strip().split())
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    rows = [{"recommendation": k, "frequency": v} for k, v in counts.items()]
    rows.sort(key=lambda r: (-int(r["frequency"]), r["recommendation"]))
    return rows


def _interaction_diagnostics(context_analysis: Dict[str, Any], min_events: int) -> Dict[str, Any]:
    interactions = (context_analysis or {}).get("interactions") or {}
    attempted: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []
    for name, rows in interactions.items():
        for r in rows or []:
            n = int(r.get("n_observations") or 0)
            cont = _safe_float(r.get("cont_win_rate"), 0.0)
            rev = _safe_float(r.get("rev_win_rate"), 0.0)
            edge = abs(cont - rev)
            stability = _clamp01(1.0 - abs(50.0 - max(cont, rev)) / 50.0)
            score = round(0.50 * (edge / 100.0) + 0.30 * _clamp01(math.log10(max(n, 1)) / 2.5) + 0.20 * stability, 6)
            rec = {
                "interaction_name": name,
                "n_observations": n,
                "edge_abs": round(edge, 3),
                "score": score,
                "better_mode": r.get("better_mode"),
            }
            attempted.append(rec)
            reason = None
            if n < min_events:
                reason = "low_sample"
            elif edge < 5:
                reason = "weak_edge"
            elif score < 0.35:
                reason = "low_composite_score"
            elif stability < 0.45:
                reason = "low_stability"

            if reason is None:
                kept.append(rec)
            else:
                rec["rejection_reason"] = reason

    attempted.sort(key=lambda x: (-_safe_float(x.get("score"), -1.0), -int(x.get("n_observations", 0))))
    kept.sort(key=lambda x: (-_safe_float(x.get("score"), -1.0), -int(x.get("n_observations", 0))))
    rejected = [r for r in attempted if r.get("rejection_reason")]
    return {"kept": kept, "attempted": attempted[:30], "rejected": rejected[:30]}


def _plateau_neighbors(
    ranked_rows: List[Dict[str, Any]],
    anchor: Dict[str, Any],
    targets: List[int],
) -> List[Dict[str, Any]]:
    if not anchor:
        return []
    rows = []
    for t in targets:
        hit = next(
            (
                r for r in ranked_rows
                if r.get("trade_mode") == anchor.get("trade_mode")
                and r.get("direction") == anchor.get("direction")
                and r.get("tf_minutes") == anchor.get("tf_minutes")
                and r.get("lookback") == anchor.get("lookback")
                and r.get("basis") == anchor.get("basis")
                and r.get("candle_bucket") == anchor.get("candle_bucket")
                and int(_safe_float(r.get("target_percent"), -999)) == int(t)
            ),
            None,
        )
        if hit:
            rows.append(
                {
                    "target_percent": hit.get("target_percent"),
                    "win_rate": hit.get("win_rate"),
                    "n_events": hit.get("n_events"),
                    "score": hit.get("composite_score"),
                    "stability_score": hit.get("stability_score"),
                    "delta_from_anchor": round(_safe_float(hit.get("composite_score")) - _safe_float(anchor.get("composite_score")), 6),
                }
            )
    rows.sort(key=lambda r: _safe_float(r.get("target_percent"), 0.0))
    return rows


def build_large_candle_excursion_findings(source_lce: Optional[Dict[str, Any]], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = _deep_merge(DEFAULT_FINDINGS_CONFIG, config or {})
    source, err = _extract_lce(source_lce)
    if err:
        return {"enabled": True, "has_source": False, "message": err, "config": cfg}

    min_events = int(cfg.get("min_events", 30))
    min_wr = float((cfg.get("ranking") or {}).get("require_min_win_rate", 0.5)) * 100.0
    ranked_n = int((cfg.get("output") or {}).get("top_n_discoveries", 25))

    candidates = _build_trade_candidates(source, min_events=min_events, min_win_rate_pct=min_wr)
    if not candidates:
        return {
            "enabled": True,
            "has_source": True,
            "message": "source analytics present but no findings met thresholds",
            "config": cfg,
            "top_discoveries": [],
            "executive_summary": [],
            "fragility_warnings": [],
            "next_tests": [],
            "scoring_methodology": {
                "formula": "0.40*win_rate + 0.20*sample_score + 0.20*expectancy_score + 0.15*stability + 0.05*simplicity - sparse_penalty"
            },
        }

    ranked = _score_candidates(
        candidates,
        penalize_low_sample=bool((cfg.get("ranking") or {}).get("penalize_low_sample", True)),
        penalize_complexity=bool((cfg.get("ranking") or {}).get("penalize_complexity", True)),
    )

    top = [r.record for r in ranked[:ranked_n]]
    top_cont = next((r.record for r in ranked if r.record.get("trade_mode") == "continuation"), None)
    top_rev = next((r.record for r in ranked if r.record.get("trade_mode") == "reverse"), None)
    ranked_rows = [r.record for r in ranked]

    context = source.get("context_analysis") or {}
    strong = _strong_context_effects(context)
    interaction_diag = _interaction_diagnostics(context, min_events=min_events)
    best_interaction = (interaction_diag.get("kept") or interaction_diag.get("attempted") or [None])[0]

    summary: List[str] = []
    if top_cont:
        summary.append(
            f"Best continuation: {top_cont.get('setup_definition')} | WR {top_cont.get('win_rate')}% | N={top_cont.get('n_events')} | score={top_cont.get('composite_score')} | avg_target={top_cont.get('avg_target_ticks')}t"
        )
    if top_rev:
        summary.append(
            f"Best reverse: {top_rev.get('setup_definition')} | WR {top_rev.get('win_rate')}% | N={top_rev.get('n_events')} | score={top_rev.get('composite_score')} | avg_target={top_rev.get('avg_target_ticks')}t"
        )
    if strong["volume"]:
        b = strong["volume"][0]
        summary.append(
            f"Strongest volume effect: {b.get('bucket')} (edge={b.get('edge_abs')} pp, N={b.get('n_observations', 0)})"
        )
    if strong["structure"]:
        b = strong["structure"][0]
        summary.append(
            f"Strongest structure effect: {b.get('bucket')} (edge={b.get('edge_abs')} pp, N={b.get('n_observations', 0)})"
        )
    if strong["volatility"]:
        b = strong["volatility"][0]
        summary.append(
            f"Strongest volatility effect: {b.get('bucket')} (edge={b.get('edge_abs')} pp, N={b.get('n_observations', 0)})"
        )
    if strong.get("structure"):
        b = strong["structure"][0]
        if str(b.get("bucket", "")).startswith("bottom"):
            summary.append("Reverse advantage strengthens in weak-close buckets.")
    if top and int(_safe_float(top[0].get("target_percent"), 0)) <= 25:
        summary.append("Strongest findings are currently concentrated at small 25% targets.")
    if not interaction_diag.get("kept"):
        summary.append("No interaction findings passed thresholds, suggesting either weak interaction effects or overly strict filtering.")
    elif best_interaction:
        summary.append(
            f"Top retained interaction: {best_interaction.get('interaction_name')} (edge={best_interaction.get('edge_abs')} pp, N={best_interaction.get('n_observations')})."
        )

    fragility: List[Dict[str, Any]] = []
    for rec in top:
        n = int(rec.get("n_events") or 0)
        wr = _safe_float(rec.get("win_rate"), 0.0)
        st = _safe_float(rec.get("stability_score"), 0.5)
        if n < 60:
            fragility.append({"type": "low_sample", "severity": "high", "setup": rec.get("setup_definition"), "details": f"Only {n} events"})
        if wr >= 70 and n < 80:
            fragility.append({"type": "suspicious_strength", "severity": "medium", "setup": rec.get("setup_definition"), "details": f"High WR ({wr:.1f}%) on limited sample ({n})"})
        if st < 0.45:
            fragility.append({"type": "neighbor_instability", "severity": "medium", "setup": rec.get("setup_definition"), "details": f"Low neighbor stability ({st:.2f})"})

    next_tests_raw: List[str] = []
    for rec in top[:8]:
        tgt = int(_safe_float(rec.get("target_percent"), 0.0))
        tf = rec.get("tf_minutes")
        bucket = rec.get("candle_bucket")
        next_tests_raw.append(f"Refine target around {tgt}% for tf={tf}m, bucket={bucket} (test {max(tgt-10, 5)} / {tgt} / {tgt+10}).")
    if strong["volume"]:
        next_tests_raw.append("Split strongest RVOL bucket into narrower bands to test local stability.")
    if top_cont and top_rev:
        next_tests_raw.append("Re-test best continuation and reverse setups on a separate date segment for out-of-sample confidence.")
    next_tests_ranked = _dedupe_ranked_recommendations(next_tests_raw)

    findings_plateau = {
        "best_continuation_neighbors": _plateau_neighbors(ranked_rows, top_cont, [15, 25, 35, 50]),
        "best_reverse_neighbors": _plateau_neighbors(ranked_rows, top_rev, [15, 25, 35, 50]),
    }
    findings_time_split: Dict[str, Any] = {"best_continuation_splits": [], "best_reverse_splits": []}
    trade_events = ((source.get("trade_analysis") or {}).get("trade_events_sample") or [])
    if top_cont:
        findings_time_split["best_continuation_splits"] = _time_split_rows(trade_events, top_cont, n_splits=3)
    if top_rev:
        findings_time_split["best_reverse_splits"] = _time_split_rows(trade_events, top_rev, n_splits=3)

    return {
        "enabled": True,
        "has_source": True,
        "source_key": "large_candle_excursion",
        "config": cfg,
        "scoring_methodology": {
            "formula": "0.40*win_rate + 0.20*sample_score + 0.20*expectancy_score + 0.15*stability + 0.05*simplicity - sparse_penalty",
            "components": ["win_rate", "sample_size", "expectancy_ticks", "neighbor_stability", "simplicity", "sparse_penalty"],
        },
        "executive_summary": summary,
        "top_discoveries": top,
        "top_continuation": top_cont,
        "top_reverse": top_rev,
        "strong_context_effects": strong,
        "strongest_interactions": strong.get("interactions") or [],
        "interaction_diagnostics": interaction_diag,
        "plateau_analysis": findings_plateau,
        "time_split_stability": findings_time_split,
        "fragility_warnings": fragility,
        "next_tests": [r["recommendation"] for r in next_tests_ranked[:20]],
        "next_tests_ranked": next_tests_ranked[:20],
        "diagnostics": {
            "n_candidates_screened": len(candidates),
            "n_ranked": len(ranked),
            "n_top": len(top),
        },
    }


def _refine_candidates(candidates: List[Dict[str, Any]], step: int, top_n_parents: int) -> List[Dict[str, Any]]:
    parents = candidates[:top_n_parents]
    rows: List[Dict[str, Any]] = []
    lookup = {}
    for c in candidates:
        key = (
            c.get("trade_mode"), c.get("direction"), c.get("tf_minutes"), c.get("lookback"),
            c.get("basis"), c.get("threshold_value"), c.get("candle_bucket"), int(_safe_float(c.get("target_percent"), 0)),
        )
        lookup[key] = c

    for p in parents:
        base_t = int(_safe_float(p.get("target_percent"), 0))
        for delta in (-step, 0, step):
            t = max(5, base_t + delta)
            key = (
                p.get("trade_mode"), p.get("direction"), p.get("tf_minutes"), p.get("lookback"),
                p.get("basis"), p.get("threshold_value"), p.get("candle_bucket"), t,
            )
            hit = lookup.get(key)
            if hit:
                improvement = round(_safe_float(hit.get("composite_score")) - _safe_float(p.get("composite_score")), 6)
                rows.append({
                    "parent_setup": p.get("setup_definition"),
                    "refined_target_percent": t,
                    "child_setup": hit.get("setup_definition"),
                    "child_score": hit.get("composite_score"),
                    "score_delta_vs_parent": improvement,
                    "child_win_rate": hit.get("win_rate"),
                    "child_n_events": hit.get("n_events"),
                })
    rows.sort(key=lambda r: (-_safe_float(r.get("child_score"), -999), -_safe_float(r.get("child_n_events"), 0)))
    return rows


def _chain_candidates(candidates: List[Dict[str, Any]], interactions: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    top_n_parents = int(cfg.get("top_n_parents", 10))
    min_imp = float(cfg.get("min_incremental_improvement", 0.03))
    max_depth = int(cfg.get("max_chain_depth", 3))
    min_remaining = int(cfg.get("min_remaining_events", 50))

    out: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    parents = candidates[:top_n_parents]
    for p in parents:
        base_score = _safe_float(p.get("composite_score"), 0.0)
        p_mode = p.get("trade_mode")
        p_dir = p.get("direction")
        for ix in interactions:
            cont = _safe_float(ix.get("cont_win_rate"), 0.0)
            rev = _safe_float(ix.get("rev_win_rate"), 0.0)
            n = int(ix.get("n_observations") or 0)
            if n < min_remaining:
                rejected.append(
                    {
                        "base_setup": p.get("setup_definition"),
                        "reason": "too_few_remaining_events",
                        "details": f"n={n} < min_remaining_events={min_remaining}",
                    }
                )
                continue
            better = ix.get("better_mode")
            if (p_mode == "continuation" and better != "continuation") or (p_mode == "reverse" and better != "reverse"):
                rejected.append(
                    {
                        "base_setup": p.get("setup_definition"),
                        "reason": "mode_mismatch",
                        "details": f"better_mode={better} does not match trade_mode={p_mode}",
                    }
                )
                continue
            ix_edge = abs(cont - rev) / 100.0
            improvement = ix_edge * 0.30
            if improvement < min_imp:
                rejected.append(
                    {
                        "base_setup": p.get("setup_definition"),
                        "reason": "insufficient_incremental_improvement",
                        "details": f"improvement={improvement:.4f} < min_incremental_improvement={min_imp}",
                    }
                )
                continue
            chain_depth = min(max_depth, 2)
            complexity_penalty = 0.04 * (chain_depth - 1)
            if complexity_penalty > 0.10:
                rejected.append(
                    {
                        "base_setup": p.get("setup_definition"),
                        "reason": "complexity_penalty_too_high",
                        "details": f"complexity_penalty={complexity_penalty:.4f}",
                    }
                )
                continue
            chained_score = base_score + improvement - complexity_penalty
            robustness_score = round(_clamp01(_safe_float(p.get("stability_score"), 0.5) - complexity_penalty), 6)
            if robustness_score < 0.40:
                rejected.append(
                    {
                        "base_setup": p.get("setup_definition"),
                        "reason": "robustness_too_weak",
                        "details": f"robustness_score={robustness_score:.4f}",
                    }
                )
                continue
            out.append(
                {
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
                    "robustness_score": robustness_score,
                    "composite_score": round(chained_score, 6),
                    "win_rate": p.get("win_rate"),
                    "avg_target_ticks": p.get("avg_target_ticks"),
                    "median_target_ticks": p.get("median_target_ticks"),
                    "avg_favorable_ticks": p.get("avg_favorable_ticks"),
                    "median_favorable_ticks": p.get("median_favorable_ticks"),
                    "avg_adverse_ticks": p.get("avg_adverse_ticks"),
                    "median_adverse_ticks": p.get("median_adverse_ticks"),
                    "stop_hit_rate": p.get("stop_hit_rate"),
                    "expectancy_ticks": p.get("expectancy_ticks"),
                }
            )
    out.sort(key=lambda r: (-_safe_float(r.get("composite_score"), -999), -_safe_float(r.get("n_events"), 0)))
    return out, rejected


def _bucket_sort_key(label: Any) -> float:
    s = str(label or "")
    try:
        return float(s.replace("+", "").split("-")[0])
    except Exception:
        return 9999.0


def _neighbor_rows_for_candidate(base_candidates: List[Dict[str, Any]], candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
    target = _safe_float(candidate.get("target_percent"), 0.0)
    threshold = _safe_float(candidate.get("threshold_value"), 0.0)
    bucket = candidate.get("candle_bucket")
    peers: List[Dict[str, Any]] = []
    for c in base_candidates:
        if (
            c.get("trade_mode") == candidate.get("trade_mode")
            and c.get("direction") == candidate.get("direction")
            and c.get("tf_minutes") == candidate.get("tf_minutes")
            and c.get("lookback") == candidate.get("lookback")
            and c.get("basis") == candidate.get("basis")
        ):
            rel = None
            if c.get("target_percent") != candidate.get("target_percent"):
                rel = "target_neighbor"
            elif c.get("threshold_value") != candidate.get("threshold_value"):
                rel = "threshold_neighbor"
            elif c.get("candle_bucket") != bucket:
                rel = "bucket_neighbor"
            else:
                rel = "self"

            peers.append(
                {
                    "neighbor_type": rel,
                    "target_percent": c.get("target_percent"),
                    "threshold_value": c.get("threshold_value"),
                    "candle_bucket": c.get("candle_bucket"),
                    "score": c.get("composite_score"),
                    "win_rate": c.get("win_rate"),
                    "n_events": c.get("n_events"),
                    "robustness_score": c.get("stability_score"),
                    "delta_from_parent": round(_safe_float(c.get("composite_score")) - _safe_float(candidate.get("composite_score")), 6),
                    "target_dist": abs(_safe_float(c.get("target_percent"), target) - target),
                    "threshold_dist": abs(_safe_float(c.get("threshold_value"), threshold) - threshold),
                }
            )
    peers.sort(
        key=lambda x: (
            x["neighbor_type"] != "self",
            x.get("target_dist", 999.0),
            x.get("threshold_dist", 999.0),
            _bucket_sort_key(x.get("candle_bucket")),
        )
    )
    return peers[:12]


def _plateau_label(neighbors: List[Dict[str, Any]]) -> str:
    deltas = [abs(_safe_float(n.get("delta_from_parent"), 0.0)) for n in neighbors if n.get("neighbor_type") != "self"]
    if not deltas:
        return "unknown"
    avg_delta = sum(deltas) / len(deltas)
    if avg_delta <= 0.04:
        return "stable_plateau"
    if avg_delta <= 0.09:
        return "moderate_plateau"
    return "fragile_peak"


def _time_split_rows(
    events: List[Dict[str, Any]],
    candidate: Dict[str, Any],
    n_splits: int,
) -> List[Dict[str, Any]]:
    if not events:
        return []
    matched = []
    for ev in events:
        if (
            ev.get("trade_mode") == candidate.get("trade_mode")
            and ev.get("direction") == candidate.get("direction")
            and ev.get("tf_minutes") == candidate.get("tf_minutes")
            and ev.get("lookback") == candidate.get("lookback")
            and ev.get("basis") == candidate.get("basis")
            and _safe_float(ev.get("threshold_value"), -999.0) == _safe_float(candidate.get("threshold_value"), -999.0)
            and _safe_float(ev.get("target_percent"), -999.0) == _safe_float(candidate.get("target_percent"), -999.0)
            and str(ev.get("candle_bucket")) == str(candidate.get("candle_bucket"))
        ):
            matched.append(ev)

    if not matched:
        return []
    matched.sort(key=lambda e: str(e.get("dt") or ""))
    splits = max(1, int(n_splits))
    chunk = max(1, len(matched) // splits)
    rows: List[Dict[str, Any]] = []
    for i in range(splits):
        lo = i * chunk
        hi = len(matched) if i == splits - 1 else min(len(matched), (i + 1) * chunk)
        subset = matched[lo:hi]
        if not subset:
            continue
        n = len(subset)
        wins = sum(1 for s in subset if s.get("win") is True)
        wr = round(wins / n * 100.0, 2) if n > 0 else None
        exp = round(
            sum((_safe_float(s.get("trade_fav_ticks")) - _safe_float(s.get("trade_adv_ticks"))) for s in subset) / n, 4
        ) if n > 0 else None
        rows.append(
            {
                "split_id": f"split_{i+1}",
                "n_events": n,
                "win_rate": wr,
                "score": candidate.get("composite_score"),
                "expectancy_ticks": exp,
            }
        )
    return rows


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
    chained_rows: List[Dict[str, Any]] = []
    rejected_chains: List[Dict[str, Any]] = []
    if chain_cfg.get("enabled", True):
        chained_rows, rejected_chains = _chain_candidates(broad_candidates, interactions, chain_cfg)

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
    final_pool.extend(chained_rows)
    final_pool.sort(key=lambda r: (-_safe_float(r.get("composite_score"), -999), -_safe_float(r.get("n_events"), 0)))
    top_final_n = int((cfg.get("output") or {}).get("top_n_final_discoveries", 25))
    final_rows = final_pool[:top_final_n]

    plateau_rows: List[Dict[str, Any]] = []
    split_rows: List[Dict[str, Any]] = []
    time_split_n = int((robust_cfg or {}).get("time_splits", 3))
    trade_events = ((source.get("trade_analysis") or {}).get("trade_events_sample") or [])
    for cand in final_rows:
        neighbors = _neighbor_rows_for_candidate(base, cand)
        plateau_rows.append(
            {
                "setup_definition": cand.get("setup_definition"),
                "plateau_label": _plateau_label(neighbors),
                "neighbors": neighbors,
            }
        )
        split_rows.append(
            {
                "setup_definition": cand.get("setup_definition"),
                "splits": _time_split_rows(trade_events, cand, time_split_n),
            }
        )

    diagnostics = {
        "n_broad_evaluated": len(base),
        "n_broad_retained": len(broad_candidates),
        "n_refinement_rows": len(refinement_rows),
        "n_chain_rows": len(chained_rows),
        "n_chain_rejected": len(rejected_chains),
        "n_robust_rows": len(robust_rows),
        "n_final": len(final_rows),
        "dropped_low_sample_or_winrate": max(0, len((source.get("trade_analysis") or {}).get("trade_combo_results") or []) - len(base)),
    }

    cautions: List[str] = []
    if diagnostics["n_chain_rows"] == 0:
        cautions.append("No chained candidates passed incremental-improvement and minimum-sample guardrails.")
    if any(_safe_float(r.get("robustness_score"), 0.0) < 0.45 for r in robust_rows[:10]):
        cautions.append("Some top candidates show weak neighbor stability; treat as fragile until validated out-of-sample.")

    summary = {
        "strongest_broad_scan": broad_candidates[0] if broad_candidates else None,
        "strongest_refined": refinement_rows[0] if refinement_rows else None,
        "strongest_chain": chained_rows[0] if chained_rows else None,
        "plateau_assessment": (plateau_rows[0]["plateau_label"] if plateau_rows else "unknown"),
        "time_split_assessment": "acceptable_through_time" if any(
            s.get("win_rate", 0) >= 45.0 for s in (split_rows[0].get("splits") if split_rows else [])
        ) else "weak_through_time",
        "chain_value_assessment": "no_chain_added_value" if not chained_rows else "chain_added_value",
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
        "interaction_chaining": {"candidates": chained_rows},
        "chain_rejection_diagnostics": {"attempted": rejected_chains[:120]},
        "robustness_validation": {"candidates": robust_rows},
        "plateau_analysis": plateau_rows,
        "time_split_validation": split_rows,
        "final_discoveries": final_rows,
        "diagnostics": diagnostics,
        "next_steps": next_steps,
    }
