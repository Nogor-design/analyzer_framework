from __future__ import annotations

from typing import Any, Dict, List, Tuple
import math


DEFAULT_ELITE_REVERSAL_SETUP_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "top_n": 5,
    "min_n": 25,
    "min_runner_lift_pp": 4.0,
    "max_fail_rate": 35.0,
    "min_mfe_mae": 1.25,
    "max_conditions": 4,
    "dedupe": {
        "enabled": True,
        "priority_features": ["session", "early_path_class", "recommended_action"],
    },
    "score_weights": {
        "runner_weight": 0.38,
        "expansion_weight": 0.22,
        "failure_penalty": 0.30,
        "low_sample_penalty": 0.18,
        "mfe_mae_weight": 0.18,
        "stability_weight": 0.14,
        "simplicity_bonus": 0.08,
    },
}


def _sf(v: Any, d: float = 0.0) -> float:
    try:
        f = float(v)
        if math.isnan(f):
            return d
        return f
    except Exception:
        return d


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _family_baseline(rows: List[Dict[str, Any]], rec: Dict[str, Any]) -> Dict[str, Any]:
    cond = rec.get("conditions") or {}
    c_tf = cond.get("timeframe")
    c_bucket = cond.get("candle_bucket")
    matched = []
    for r in rows:
        k = r.get("group_key") or {}
        if k.get("timeframe") == c_tf and k.get("candle_bucket") == c_bucket:
            matched.append(r)
    if not matched:
        return {}
    matched.sort(key=lambda x: -int(x.get("n") or 0))
    return matched[0]


def _setup_signature(rec: Dict[str, Any], pri: List[str]) -> Tuple[str, ...]:
    cond = rec.get("conditions") or {}
    return tuple(str(cond.get(k, "any")) for k in pri)


def _strategy_definition(rec: Dict[str, Any]) -> Dict[str, Any]:
    cond = rec.get("conditions") or {}
    early_checks = []
    if cond.get("early_path_class"):
        early_checks.append(f"early_path_class == {cond.get('early_path_class')}")
    if cond.get("midpoint_reclaim_within_bars_max") is not None:
        early_checks.append(f"midpoint reclaimed within {int(cond.get('midpoint_reclaim_within_bars_max'))} bars")
    if cond.get("signal_rebreak_within_bars_max") is not None:
        early_checks.append(f"no signal-extreme rebreak within {int(cond.get('signal_rebreak_within_bars_max'))} bars")
    if cond.get("adverse_move_2bar_pct_max") is not None:
        early_checks.append(f"adverse_move_2bar_pct <= {cond.get('adverse_move_2bar_pct_max'):.1f}")
    if cond.get("favorable_move_2bar_pct_min") is not None:
        early_checks.append(f"favorable_move_2bar_pct >= {cond.get('favorable_move_2bar_pct_min'):.1f}")

    return {
        "entry": (
            f"Reversal context: session={cond.get('session', 'any')}, timeframe={cond.get('timeframe', 'any')}m, "
            f"candle_bucket={cond.get('candle_bucket', 'any')}, direction={cond.get('signal_direction', 'any')}, "
            f"vwap_stretch={cond.get('vwap_stretch_bucket', 'any')}, trend={cond.get('trend_state', 'any')}"
        ),
        "early_validation": early_checks,
        "decision": str(rec.get("recommended_action") or "scalp_only"),
        "management": [
            "downgrade to scalp if favorable progress stalls by bar 3",
            "exit if signal extreme breaks again",
            "allow runner only while early-path efficiency remains above threshold",
        ],
    }


def compute_elite_reversal_setup_extractor(
    decision_engine: Dict[str, Any],
    cfg: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    merged = _deep_merge(DEFAULT_ELITE_REVERSAL_SETUP_CONFIG, cfg or {})
    if not merged.get("enabled", True):
        return {"enabled": False}

    if not decision_engine or decision_engine.get("message"):
        return {
            "enabled": True,
            "message": "decision engine unavailable for elite extraction",
            "repair_summary": {
                "decision_engine_broken": True,
                "what_was_broken": decision_engine.get("message", "missing decision engine output") if isinstance(decision_engine, dict) else "missing decision engine output",
                "what_changed": "No elite extraction possible until decision engine outputs valid reversal distributions.",
                "probabilities_valid": False,
            },
            "elite_setups": [],
            "near_miss_setups": [],
        }

    baseline = decision_engine.get("baseline") or {}
    rules = decision_engine.get("decision_rules") or []
    by_ctx = (decision_engine.get("tables") or {}).get("outcome_by_early_path_and_session") or []
    by_path = (decision_engine.get("tables") or {}).get("outcome_by_early_path_class") or []

    # Expand candidate pool with context rows as scalp-only defaults.
    candidates = [dict(r) for r in rules]
    for row in by_ctx + by_path:
        cond = dict(row.get("group_key") or {})
        candidates.append({
            "conditions": cond,
            "n": row.get("n"),
            "failure_rate": row.get("failure_rate"),
            "scalp_rate": row.get("scalp_rate"),
            "expansion_rate": row.get("expansion_rate"),
            "runner_rate": row.get("runner_rate"),
            "avg_mfe": row.get("avg_mfe"),
            "avg_mae": row.get("avg_mae"),
            "mfe_mae": row.get("mfe_mae"),
            "lift_vs_baseline_runner_pp": round(_sf(row.get("runner_rate")) - _sf(baseline.get("runner_rate")), 1),
            "lift_vs_baseline_failure_pp": round(_sf(row.get("failure_rate")) - _sf(baseline.get("failure_rate")), 1),
            "sample_quality": round(min(1.0, _sf(row.get("n")) / 120.0), 3),
            "recommended_action": "scalp_only",
            "source": "distribution_table",
        })

    if not candidates:
        return {
            "enabled": True,
            "message": "no candidate setups found",
            "repair_summary": {
                "decision_engine_broken": False,
                "what_was_broken": "No extractable decision slices passed minimum-N thresholds.",
                "what_changed": "Decision engine returned valid baseline but no candidate rules/tables to rank.",
                "probabilities_valid": bool(baseline),
            },
            "elite_setups": [],
            "near_miss_setups": [],
        }

    w = merged.get("score_weights") or {}
    min_n = int(merged.get("min_n", 25))
    max_fail = float(merged.get("max_fail_rate", 35.0))
    min_runner_lift = float(merged.get("min_runner_lift_pp", 4.0))
    min_mfe_mae = float(merged.get("min_mfe_mae", 1.25))

    ranked: List[Dict[str, Any]] = []
    near_miss: List[Dict[str, Any]] = []
    for rec in candidates:
        n = int(_sf(rec.get("n"), 0.0))
        fail = _sf(rec.get("failure_rate"), 0.0)
        run = _sf(rec.get("runner_rate"), 0.0)
        exp = _sf(rec.get("expansion_rate"), 0.0)
        lift = _sf(rec.get("lift_vs_baseline_runner_pp"), run - _sf(baseline.get("runner_rate")))
        mfe_mae = _sf(rec.get("mfe_mae"), 0.0)
        sample_q = _clamp01(_sf(rec.get("sample_quality"), n / 120.0))

        n_cond = len([k for k, v in (rec.get("conditions") or {}).items() if v not in (None, "", "any")])
        stability = _clamp01((sample_q + _clamp01(lift / 20.0)) / 2.0)
        simplicity = _clamp01(1.0 - (max(0, n_cond - 2) / 6.0))

        score = (
            _clamp01(run / 100.0) * float(w.get("runner_weight", 0.38))
            + _clamp01(exp / 100.0) * float(w.get("expansion_weight", 0.22))
            - _clamp01(fail / 100.0) * float(w.get("failure_penalty", 0.30))
            - _clamp01(1.0 - sample_q) * float(w.get("low_sample_penalty", 0.18))
            + _clamp01((mfe_mae - 1.0) / 1.5) * float(w.get("mfe_mae_weight", 0.18))
            + stability * float(w.get("stability_weight", 0.14))
            + simplicity * float(w.get("simplicity_bonus", 0.08))
        )

        enriched = {
            **rec,
            "elite_score": round(score, 4),
            "condition_count": n_cond,
            "stability_score": round(stability, 3),
            "simplicity_score": round(simplicity, 3),
            "strategy": _strategy_definition(rec),
        }

        reasons = []
        if n < min_n:
            reasons.append(f"low N ({n} < {min_n})")
        if fail > max_fail:
            reasons.append(f"high failure ({fail:.1f}% > {max_fail:.1f}%)")
        if lift < min_runner_lift:
            reasons.append(f"low runner lift ({lift:.1f}pp < {min_runner_lift:.1f}pp)")
        if mfe_mae < min_mfe_mae:
            reasons.append(f"weak MFE/MAE ({mfe_mae:.2f} < {min_mfe_mae:.2f})")
        if n_cond > int(merged.get("max_conditions", 4)):
            reasons.append(f"excessive complexity ({n_cond} conditions)")

        if reasons:
            near_miss.append({**enriched, "rejection_reasons": reasons})
            continue

        fam = _family_baseline(by_ctx + by_path, rec)
        enriched["comparison"] = {
            "baseline_reversal": baseline,
            "family_baseline": fam,
            "elite_subset": {
                "n": n,
                "failure_rate": fail,
                "scalp_rate": _sf(rec.get("scalp_rate")),
                "expansion_rate": exp,
                "runner_rate": run,
                "avg_mfe": _sf(rec.get("avg_mfe")),
                "avg_mae": _sf(rec.get("avg_mae")),
                "mfe_mae": _sf(rec.get("mfe_mae")),
            },
        }
        ranked.append(enriched)

    ranked.sort(key=lambda x: (-_sf(x.get("elite_score")), -int(_sf(x.get("n"))), _sf(x.get("failure_rate"))))

    # Redundancy reduction
    if (merged.get("dedupe") or {}).get("enabled", True):
        pri = list((merged.get("dedupe") or {}).get("priority_features") or ["session", "early_path_class", "recommended_action"])
        deduped = []
        seen = set()
        for rec in ranked:
            sig = _setup_signature(rec, pri)
            if sig in seen:
                continue
            seen.add(sig)
            deduped.append(rec)
        ranked = deduped

    top_n = int(merged.get("top_n", 5))
    elite = ranked[:top_n]

    # Research-question answers.
    rq = {
        "valid_probability_slices": {
            "tested": True,
            "n_slices_with_probabilities": len(by_ctx) + len(by_path),
            "holds": (len(by_ctx) + len(by_path)) > 0,
        },
        "biggest_runner_increase": elite[0].get("lift_vs_baseline_runner_pp") if elite else None,
        "biggest_failure_reduction": min((x.get("lift_vs_baseline_failure_pp") for x in elite), default=None),
        "best_mfe_mae": max((_sf(x.get("mfe_mae")) for x in elite), default=None),
        "runner_first_candidates": [x.get("conditions") for x in elite if x.get("recommended_action") in ("hold_for_runner", "press_runner")],
        "session_concentration": sorted({str((x.get("conditions") or {}).get("session", "any")) for x in elite}),
        "early_path_mix": sorted({str((x.get("conditions") or {}).get("early_path_class", "mixed")) for x in elite}),
        "near_miss_count": len(near_miss),
    }

    return {
        "enabled": True,
        "repair_summary": {
            "decision_engine_broken": False,
            "what_was_broken": "Decision engine lacked robust window fallback and excursion field fallback in some datasets.",
            "what_changed": "Added robust window selection, fallback excursion fields, and diagnostics; elite extractor now consumes repaired decision distributions.",
            "probabilities_valid": bool(baseline) and baseline.get("n", 0) > 0,
        },
        "baseline": baseline,
        "elite_setups": elite,
        "near_miss_setups": near_miss[:8],
        "research_answers": rq,
        "diagnostics": {
            "n_candidates": len(candidates),
            "n_accepted": len(elite),
            "n_rejected": len(near_miss),
        },
    }
