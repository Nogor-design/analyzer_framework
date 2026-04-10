from __future__ import annotations

from typing import Any, Dict, List, Optional

import math


DEFAULT_STRATEGY_CONSTRUCTION_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "accepted_validation_labels": ["stable_edge", "acceptable_degradation", "possibly_monitor_further"],
    "deprioritized_validation_labels": ["fragile_edge", "likely_overfit", "insufficient_oos_data"],
    "risk_constraints": {
        "instrument": "NQ",
        "max_stop_ticks": 100,
        "base_unit": 1,
        "max_add_ons": 1,
        "max_total_units": 2,
    },
    "scoring_weights": {
        "validation_quality": 0.24,
        "retained_oos_edge": 0.20,
        "execution_clarity": 0.16,
        "session_dependence": 0.10,
        "stop_realism": 0.12,
        "management_complexity": 0.08,
        "automation_feasibility": 0.10,
    },
    "ranking_thresholds": {
        "paper_test_ready_min": 0.72,
        "code_next_min": 0.80,
        "watchlist_min": 0.58,
    },
    "max_strategies": 8,
}


def _sf(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        if math.isnan(f):
            return default
        return f
    except Exception:
        return default


def _clip01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _filters_to_text(filters: Dict[str, Any]) -> str:
    return " AND ".join(f"{k}={v}" for k, v in (filters or {}).items() if v not in (None, "", "any")) or "baseline"


def _find_threshold(decision_engine: Dict[str, Any], key: str, default: float) -> float:
    rows = decision_engine.get("threshold_discovery") or []
    for r in rows:
        t = str(r.get("threshold") or "")
        if key in t:
            tail = t.split("<=")[-1] if "<=" in t else t.split(">=")[-1]
            try:
                return float(tail.strip())
            except Exception:
                continue
    return default


def _candidate_archetype(oos: Dict[str, Any], source_type: str) -> str:
    runner = _sf(oos.get("runner_rate"), 0.0)
    expansion = _sf(oos.get("expansion_rate"), 0.0)
    fail = _sf(oos.get("fail_rate"), 100.0)
    if source_type == "decision_rule" and 20.0 <= runner < 70.0 and expansion >= 45.0:
        return "conditional_hybrid"
    if runner >= 65.0 and fail <= 12.0:
        return "runner_reversal"
    if expansion >= 70.0 and runner >= 35.0:
        return "expansion_reversal"
    return "scalp_reversal"


def _entry_style(arch: str) -> str:
    if arch == "runner_reversal":
        return "entry_after_early_path_confirmation"
    if arch == "conditional_hybrid":
        return "delayed_entry_after_midpoint_reclaim"
    return "immediate_entry_on_qualifying_close"


def _management_mode(arch: str) -> str:
    return {
        "scalp_reversal": "fixed_target",
        "expansion_reversal": "scale_out",
        "runner_reversal": "runner_hold",
        "conditional_hybrid": "hybrid_branching",
    }.get(arch, "scale_out")


def _automation_readiness(arch: str, source_type: str, stop_flag: bool) -> str:
    if stop_flag:
        return "low"
    if arch in ("scalp_reversal", "expansion_reversal") and source_type in ("decision_rule", "elite_setup"):
        return "high"
    if arch == "runner_reversal":
        return "medium"
    return "medium"


def _deployment_bucket(score: float, thresholds: Dict[str, Any]) -> str:
    if score >= _sf(thresholds.get("code_next_min"), 0.80):
        return "code_next"
    if score >= _sf(thresholds.get("paper_test_ready_min"), 0.72):
        return "paper_test_ready"
    if score >= _sf(thresholds.get("watchlist_min"), 0.58):
        return "monitor_only"
    return "discard"


def _build_strategy_card(
    candidate: Dict[str, Any],
    decision_engine: Dict[str, Any],
    max_stop_ticks: int,
    thresholds: Dict[str, float],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    oos = candidate.get("out_of_sample") or {}
    src = str(candidate.get("source_type") or "candidate")
    filters = dict(candidate.get("filters") or {})
    label = str(candidate.get("validation_label") or "unknown")
    archetype = _candidate_archetype(oos, src)
    entry_style = _entry_style(archetype)
    management_mode = _management_mode(archetype)

    stop_from_candle = round(max(20.0, min(max_stop_ticks * 1.25, _sf(oos.get("avg_mae"), 20.0) * 1.3)), 1)
    stop_primary = min(stop_from_candle, float(max_stop_ticks))
    stop_flag = stop_from_candle > float(max_stop_ticks)
    oos_runner = _sf(oos.get("runner_rate"), 0.0)
    oos_fail = _sf(oos.get("fail_rate"), 100.0)
    oos_expansion = _sf(oos.get("expansion_rate"), 0.0)

    name_prefix = {
        "runner_reversal": "Explosive Start Reversal Runner",
        "expansion_reversal": "Orderly Start Session Expansion Reversal",
        "scalp_reversal": "Weak-Start Reclaim Scalp Fade",
        "conditional_hybrid": "Path-Conditioned Reversal Hybrid",
    }.get(archetype, "Validated Reversal Strategy")

    strategy_name = f"{name_prefix} [{src}]"

    thesis = (
        "Constructed from validated reversal edge candidates where early-path behavior and two-bar thresholds "
        "control failure risk and determine whether the trade should be scratched, downgraded to scalp, held for expansion, "
        "or held for runner continuation."
    )

    context = {
        "timeframe": filters.get("tf_minutes", filters.get("timeframe", "any")),
        "candle_bucket": filters.get("candle_bucket", "any"),
        "session": filters.get("session", "all_session"),
        "signal_direction": "reverse_only",
        "vwap_stretch_bucket": filters.get("vwap_stretch_bucket", "any"),
        "trend_state": filters.get("trend_state", "any"),
        "key_level_interaction": filters.get("key_level_interaction", "any"),
        "early_path_class": filters.get("early_path_class", "any"),
    }

    entry_trigger = {
        "style": entry_style,
        "rules": [
            "large candle reversal event detected",
            f"adverse_move_2bar_pct <= {thresholds['adverse_max']}",
            f"favorable_move_2bar_pct >= {thresholds['favorable_min']}",
            "midpoint reclaimed within 2 bars",
            "no signal-extreme rebreak within 2 bars",
        ],
    }

    initial_invalidation = [
        "no midpoint reclaim by bar 2",
        "signal extreme rebreak within 2 bars",
        f"adverse_move_2bar_pct > {thresholds['adverse_max']}",
        "early path degrades to weak_start",
    ]

    stop_logic = {
        "primary": {
            "style": "signal_candle_extreme_with_cap",
            "distance_ticks": stop_primary,
            "max_stop_ticks": max_stop_ticks,
            "note": "Primary stop capped by NQ max-stop realism constraint",
        },
        "alternatives": [
            {"style": "atr_constrained", "description": "1.2x ATR(14) with hard cap at max_stop_ticks"},
            {"style": "fractional_signal_candle", "description": "0.75 * signal candle size with floor 20 ticks"},
        ],
        "realism_warning": "Stop demand exceeded cap; forced cap may lower retention" if stop_flag else None,
    }

    early_mgmt = {
        "scratch_fast": [
            "if midpoint not reclaimed by bar 2: exit",
            "if adverse trajectory accelerates into bar 3: exit",
        ],
        "downgrade_to_scalp": [
            "if favorable progress < 25% of signal size by bar 3",
            "if rebreak risk appears while still above invalidation threshold",
        ],
        "hold_expansion": [
            "if favorable_move_2bar_pct remains above threshold and rebreak absent",
            "if early path is orderly_start or better",
        ],
        "allow_runner": [
            "only when explosive_start or top-decile branch filters hold",
            "only if no structure failure after first scale-out",
        ],
    }

    profit_taking = {
        "scalp_target": "25%-35% of signal candle size",
        "expansion_target": "50%-75% of signal candle size",
        "runner_target": "100%-150% via trailing logic",
        "execution_plan": [
            "take partial at 25%",
            "move stop to reduce-risk after 50% progress",
            "trail remainder using structure after expansion threshold",
        ],
    }

    session_rules = {
        "mode": "session_conditioned" if context["session"] != "all_session" else "all_session",
        "allowed_sessions": [context["session"]] if context["session"] != "all_session" else ["asia", "london_ny_overlap", "mid_ny"],
        "restriction_note": "Use only validation-backed session buckets",
    }

    risk_position_logic = {
        "base_entry_size": int(((cfg.get("risk_constraints") or {}).get("base_unit", 1))),
        "add_on_rule": "single add-on allowed only after 50% expansion and stop at/beyond breakeven",
        "max_add_ons": int(((cfg.get("risk_constraints") or {}).get("max_add_ons", 1))),
        "max_total_exposure": int(((cfg.get("risk_constraints") or {}).get("max_total_units", 2))),
        "prop_firm_notes": [
            "Respect hard stop discipline to control trailing drawdown",
            "Avoid session over-concentration that can inflate variance",
            "Disable add-on during major scheduled news windows",
        ],
    }

    warnings: List[str] = []
    if _sf(oos.get("n"), 0.0) < 25:
        warnings.append("OOS sample is narrow; behavior may be unstable in live forward conditions.")
    if label == "acceptable_degradation":
        warnings.append("Validation label indicates measurable OOS degradation; start with paper testing.")
    if context["session"] != "all_session":
        warnings.append("Session dependence is high; outside-session expectancy may collapse.")
    if stop_flag:
        warnings.append("Uncapped stop requirement exceeded max-stop profile; cap can reduce edge retention.")
    if archetype == "runner_reversal":
        warnings.append("Runner logic may be sensitive to slippage and high-impact news conditions.")

    nt8_notes = {
        "event_definition": "OnBarClose detect qualified large-candle reversal + context filters",
        "state_tracking": "Track early_path_class, bar_count_since_signal, and two-bar progress metrics",
        "bar_counting": "Enforce midpoint/rebreak checks within first 2 bars and branch by bar 3",
        "order_management": "Bracket entry with capped initial stop, partial targets, and conditional trailing",
        "automation_mode": "fully_automated" if archetype in ("scalp_reversal", "expansion_reversal") else "semi_automated_first",
    }

    # deployment scoring
    weights = cfg.get("scoring_weights") or {}
    validation_quality = 1.0 if label == "stable_edge" else (0.72 if label == "acceptable_degradation" else 0.55)
    retained_oos_edge = _clip01((oos_runner / 100.0) * 0.55 + (oos_expansion / 100.0) * 0.30 + ((100.0 - oos_fail) / 100.0) * 0.15)
    execution_clarity = 0.88 if archetype != "conditional_hybrid" else 0.68
    session_dependence = 0.60 if context["session"] != "all_session" else 0.86
    stop_realism = 0.58 if stop_flag else 0.90
    management_complexity = 0.62 if archetype == "conditional_hybrid" else (0.72 if archetype == "runner_reversal" else 0.88)
    automation_feasibility = 0.62 if archetype in ("runner_reversal", "conditional_hybrid") else 0.9

    deployment_score = (
        _sf(weights.get("validation_quality"), 0.24) * validation_quality
        + _sf(weights.get("retained_oos_edge"), 0.20) * retained_oos_edge
        + _sf(weights.get("execution_clarity"), 0.16) * execution_clarity
        + _sf(weights.get("session_dependence"), 0.10) * session_dependence
        + _sf(weights.get("stop_realism"), 0.12) * stop_realism
        + _sf(weights.get("management_complexity"), 0.08) * management_complexity
        + _sf(weights.get("automation_feasibility"), 0.10) * automation_feasibility
    )
    deployment_score = round(_clip01(deployment_score), 4)

    readiness = _deployment_bucket(deployment_score, cfg.get("ranking_thresholds") or {})

    return {
        "strategy_name": strategy_name,
        "source_candidate": str(candidate.get("candidate_name") or "unknown"),
        "source_type": src,
        "archetype": archetype,
        "thesis": thesis,
        "eligible_market_context": context,
        "entry_trigger": entry_trigger,
        "initial_invalidation": initial_invalidation,
        "initial_stop_logic": stop_logic,
        "early_management_logic": early_mgmt,
        "profit_taking_logic": profit_taking,
        "session_rules": session_rules,
        "risk_position_logic": risk_position_logic,
        "failure_mode_warnings": warnings,
        "ninjatrader_translation_notes": nt8_notes,
        "management_mode": management_mode,
        "automation_readiness": _automation_readiness(archetype, src, stop_flag),
        "deployment_score": deployment_score,
        "complexity_level": "high" if archetype == "conditional_hybrid" else ("medium" if archetype == "runner_reversal" else "low"),
        "paper_test_priority": "high" if readiness in ("paper_test_ready", "code_next") else "medium",
        "deployment_bucket": readiness,
        "strategy_spec": {
            "name": strategy_name,
            "entry_style": entry_style,
            "management_mode": management_mode,
            "stop_style": "capped_structure",
            "exit_style": "partial_plus_trail",
            "filters": filters,
        },
        "trace": {
            "validation_label": label,
            "oos_metrics": oos,
            "decision_reference": {
                "rule_count": len(decision_engine.get("decision_rules") or []),
                "threshold_count": len(decision_engine.get("threshold_discovery") or []),
            },
        },
    }


def _answer_research_questions(strategies: List[Dict[str, Any]], rejected: List[Dict[str, Any]]) -> Dict[str, Any]:
    def _names(arch: str) -> List[str]:
        return [s.get("strategy_name") for s in strategies if s.get("archetype") == arch]

    fully_auto = [s.get("strategy_name") for s in strategies if (s.get("ninjatrader_translation_notes") or {}).get("automation_mode") == "fully_automated"]
    semi_auto = [s.get("strategy_name") for s in strategies if (s.get("ninjatrader_translation_notes") or {}).get("automation_mode") != "fully_automated"]

    return {
        "1_which_edges_become_scalp": _names("scalp_reversal"),
        "2_which_edges_become_runner": _names("runner_reversal"),
        "3_which_require_conditional_branching": _names("conditional_hybrid"),
        "4_best_stop_structures": {
            "primary": "signal_candle_extreme_with_cap",
            "alternatives": ["atr_constrained", "fractional_signal_candle"],
        },
        "5_realistic_for_full_automation_nt8": fully_auto,
        "6_better_semi_automated_first": semi_auto,
        "7_retain_edge_after_execution_constraints": [
            s.get("strategy_name") for s in strategies if s.get("deployment_bucket") in ("code_next", "paper_test_ready")
        ],
        "8_validated_but_too_fragile": [r.get("candidate_name") for r in rejected],
    }


def compute_strategy_construction_engine(findings_payload: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = dict(DEFAULT_STRATEGY_CONSTRUCTION_CONFIG)
    for k, v in (cfg or {}).items():
        if isinstance(merged.get(k), dict) and isinstance(v, dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v

    if not merged.get("enabled", True):
        return {"enabled": False, "message": "strategy construction engine disabled"}

    validation = findings_payload.get("edge_validation_engine") or {}
    if not validation.get("enabled"):
        return {"enabled": True, "message": "edge validation engine unavailable", "constructed_strategies": []}

    decision_engine = findings_payload.get("reversal_decision_engine") or {}
    accepted = set(merged.get("accepted_validation_labels") or [])
    deprioritized = set(merged.get("deprioritized_validation_labels") or [])

    adverse_max = _find_threshold(decision_engine, "adverse_move_2bar_pct", 15.0)
    favorable_min = _find_threshold(decision_engine, "favorable_move_2bar_pct", 55.0)

    input_candidates = validation.get("validated_candidates") or []
    considered: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for c in input_candidates:
        label = str(c.get("validation_label") or "")
        if label in accepted:
            considered.append(c)
            continue
        reason = "deprioritized_validation_label" if label in deprioritized else "validation_label_not_accepted"
        rejected.append({
            "candidate_name": c.get("candidate_name"),
            "validation_label": label,
            "reason": reason,
            "details": "Candidate retained for watchlist only; not converted into executable strategy.",
        })

    max_stop = int(((merged.get("risk_constraints") or {}).get("max_stop_ticks", 100)))
    strategies = [
        _build_strategy_card(
            candidate=c,
            decision_engine=decision_engine,
            max_stop_ticks=max_stop,
            thresholds={"adverse_max": adverse_max, "favorable_min": favorable_min},
            cfg=merged,
        )
        for c in considered
    ]

    strategies.sort(key=lambda s: s.get("deployment_score", 0.0), reverse=True)
    strategies = strategies[: int(merged.get("max_strategies", 8))]

    rankings = {
        "paper_test_ready": [s.get("strategy_name") for s in strategies if s.get("deployment_bucket") == "paper_test_ready"],
        "code_next": [s.get("strategy_name") for s in strategies if s.get("deployment_bucket") == "code_next"],
        "monitor_only": [s.get("strategy_name") for s in strategies if s.get("deployment_bucket") == "monitor_only"],
        "discard": [s.get("strategy_name") for s in strategies if s.get("deployment_bucket") == "discard"],
    }

    best_by_type: Dict[str, Optional[str]] = {}
    for archetype in ("scalp_reversal", "expansion_reversal", "runner_reversal", "conditional_hybrid"):
        pick = next((s for s in strategies if s.get("archetype") == archetype), None)
        best_by_type[archetype] = pick.get("strategy_name") if pick else None

    return {
        "enabled": True,
        "input_candidate_summary": {
            "considered": [
                {
                    "candidate_name": c.get("candidate_name"),
                    "source_type": c.get("source_type"),
                    "validation_label": c.get("validation_label"),
                    "oos_n": (c.get("out_of_sample") or {}).get("n"),
                }
                for c in considered
            ],
            "rejected_before_construction": rejected,
        },
        "constructed_strategies": strategies,
        "rejected_construction_candidates": rejected,
        "deployment_rankings": rankings,
        "machine_readable_strategy_specs": [s.get("strategy_spec") for s in strategies],
        "research_questions": _answer_research_questions(strategies, rejected),
        "summary": {
            "best_strategy_candidates": [s.get("strategy_name") for s in strategies[:3]],
            "best_scalp_candidate": best_by_type.get("scalp_reversal"),
            "best_expansion_candidate": best_by_type.get("expansion_reversal"),
            "best_runner_candidate": best_by_type.get("runner_reversal"),
            "best_hybrid_candidate": best_by_type.get("conditional_hybrid"),
            "paper_test_first": rankings.get("code_next") or rankings.get("paper_test_ready") or [],
        },
        "notes": {
            "construction_principle": "Translate validated edge behavior into mechanical entry, risk, management, and exit logic.",
            "stop_realism_constraint": f"Stops are constrained to max_stop_ticks={max_stop} for NQ realism.",
        },
    }
