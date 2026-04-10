from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import math


DEFAULT_REVERSAL_DECISION_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "forward_window_minutes": 30,
    "min_n": {
        "overall": 80,
        "class": 20,
        "interaction": 20,
        "rule": 25,
    },
    "outcome_thresholds": {
        "failed_reversal_max_adv_pct": 10.0,
        "micro_bounce_max_adv_pct": 25.0,
        "scalp_reversal_max_adv_pct": 50.0,
        "expansion_reversal_min_adv_pct": 50.0,
        "strong_runner_min_adv_pct": 100.0,
    },
    "early_path": {
        "explosive_min_fav_2bar_pct": 45.0,
        "explosive_max_adv_2bar_pct": 20.0,
        "orderly_min_fav_2bar_pct": 25.0,
        "orderly_max_adv_2bar_pct": 35.0,
        "weak_max_fav_2bar_pct": 15.0,
        "weak_min_adv_2bar_pct": 35.0,
        "midpoint_reclaim_bars": 2,
        "rebreak_bars": 2,
    },
    "threshold_candidates": {
        "adverse_move_2bar_pct_max": [15, 20, 25, 30, 35, 40],
        "favorable_move_2bar_pct_min": [15, 20, 25, 30, 35, 45, 55],
        "midpoint_reclaim_within_bars": [1, 2, 3],
        "no_rebreak_within_bars": [1, 2, 3],
    },
    "decision_actions": {
        "exit_immediately_fail_prob": 0.55,
        "scratch_if_no_improvement_fail_prob": 0.40,
        "hold_for_expansion_min_prob": 0.35,
        "hold_for_runner_min_prob": 0.25,
        "press_runner_min_prob": 0.38,
        "press_runner_max_fail_prob": 0.18,
        "press_runner_min_n": 40,
    },
    "rule_discovery": {
        "min_lift_vs_baseline_runner_pp": 6.0,
        "max_fail_rate_for_hold": 35.0,
        "max_rules": 14,
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _sf(v: Any, d: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return d
        f = float(v)
        return d if math.isnan(f) else f
    except Exception:
        return d


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def _pct(x: int, n: int) -> float:
    return round(_safe_div(x, n) * 100.0, 1) if n else 0.0


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "t", "1", "yes", "y"):
            return True
        if s in ("false", "f", "0", "no", "n", "", "none", "null"):
            return False
    return bool(v)


def _label_outcome(adv_pct: float, th: Dict[str, float]) -> str:
    if adv_pct < th["failed_reversal_max_adv_pct"]:
        return "failed_reversal"
    if adv_pct < th["micro_bounce_max_adv_pct"]:
        return "micro_bounce"
    if adv_pct < th["scalp_reversal_max_adv_pct"]:
        return "scalp_reversal"
    return "expansion_reversal"


def _early_path_class(row: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    fav2 = _sf(row.get("favorable_move_2bar_pct"), 0.0) or 0.0
    adv2 = _sf(row.get("adverse_move_2bar_pct"), 0.0) or 0.0
    reclaimed = bool(row.get("midpoint_reclaimed_within_2bars"))
    rebreak = bool(row.get("signal_extreme_rebreak_within_2bars"))

    if fav2 >= float(cfg["explosive_min_fav_2bar_pct"]) and adv2 <= float(cfg["explosive_max_adv_2bar_pct"]) and reclaimed and not rebreak:
        return "explosive_start"
    if fav2 >= float(cfg["orderly_min_fav_2bar_pct"]) and adv2 <= float(cfg["orderly_max_adv_2bar_pct"]) and reclaimed:
        return "orderly_start"
    if fav2 <= float(cfg["weak_max_fav_2bar_pct"]) or adv2 >= float(cfg["weak_min_adv_2bar_pct"]) or rebreak:
        return "weak_start"
    return "mixed_start"


def _select_window_events(events: List[Dict[str, Any]], desired_win: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    parse_fail = 0
    for ev in events:
        w = ev.get("window_minutes", ev.get("forward_window_minutes"))
        try:
            if int(float(w)) == desired_win:
                filtered.append(ev)
        except Exception:
            parse_fail += 1

    if filtered:
        return filtered, {
            "requested_window_minutes": desired_win,
            "selected_window_minutes": desired_win,
            "window_fallback_used": False,
            "window_parse_failures": parse_fail,
        }

    all_windows: List[int] = []
    for ev in events:
        w = ev.get("window_minutes", ev.get("forward_window_minutes"))
        try:
            all_windows.append(int(float(w)))
        except Exception:
            continue
    for w in sorted(set(all_windows), reverse=True):
        cands = []
        for ev in events:
            try:
                if int(float(ev.get("window_minutes", ev.get("forward_window_minutes")))) == w:
                    cands.append(ev)
            except Exception:
                continue
        if cands:
            return cands, {
                "requested_window_minutes": desired_win,
                "selected_window_minutes": w,
                "window_fallback_used": True,
                "window_parse_failures": parse_fail,
            }

    return events, {
        "requested_window_minutes": desired_win,
        "selected_window_minutes": None,
        "window_fallback_used": True,
        "window_parse_failures": parse_fail,
    }


def _event_rows(events: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    win = int(cfg.get("forward_window_minutes", 30))
    filtered_events, window_meta = _select_window_events(events, win)
    ep_cfg = cfg.get("early_path") or {}
    th = cfg.get("outcome_thresholds") or {}
    diagnostics = {
        **window_meta,
        "input_events": len(events),
        "window_filtered_events": len(filtered_events),
        "excluded_missing_size_or_excursion": 0,
        "valid_rows": 0,
    }

    for ev in filtered_events:
        size = _sf(ev.get("size_ticks"))
        fav = _sf(ev.get("early_fav_1bar_ticks"), 0.0)
        fav2 = _sf(ev.get("early_fav_2bar_ticks"), 0.0)
        fav3 = _sf(ev.get("early_fav_3bar_ticks"), 0.0)
        adv1 = _sf(ev.get("early_adv_1bar_ticks"), 0.0)
        adv2 = _sf(ev.get("early_adv_2bar_ticks"), 0.0)
        adv3 = _sf(ev.get("early_adv_3bar_ticks"), 0.0)
        rev_mfe = _sf(ev.get("adv_ticks", ev.get("trade_fav_ticks")))
        rev_mae = _sf(ev.get("fav_ticks", ev.get("trade_adv_ticks")))
        if not size or size <= 0 or rev_mfe is None or rev_mae is None:
            diagnostics["excluded_missing_size_or_excursion"] = int(diagnostics["excluded_missing_size_or_excursion"]) + 1
            continue

        fav1_pct = _safe_div(fav or 0.0, size) * 100.0
        fav2_pct = _safe_div(fav2 or 0.0, size) * 100.0
        fav3_pct = _safe_div(fav3 or 0.0, size) * 100.0
        adv1_pct = _safe_div(adv1 or 0.0, size) * 100.0
        adv2_pct = _safe_div(adv2 or 0.0, size) * 100.0
        adv3_pct = _safe_div(adv3 or 0.0, size) * 100.0

        adv_pct = _safe_div(rev_mfe, size) * 100.0
        outcome = _label_outcome(adv_pct, th)
        strong_runner = adv_pct >= float(th.get("strong_runner_min_adv_pct", 100.0))

        row = {
            "dt": ev.get("dt"),
            "session": ev.get("session_bucket") or "unknown",
            "trend_state": ev.get("trend_alignment_label") or "unknown",
            "signal_direction": "bull" if int(ev.get("direction") or 0) == 1 else "bear",
            "vwap_stretch_bucket": ev.get("vwap_ext_bucket") or ev.get("vwap_signed_bucket") or "unknown",
            "structure": ev.get("directional_context_label") or "unknown",
            "key_level_interaction": ev.get("level_interaction_label") or "unknown",
            "candle_bucket": ev.get("candle_bucket") or "unknown",
            "timeframe": int(ev.get("tf_minutes") or 0),
            "early_path_class": "",
            "favorable_move_1bar_pct": round(fav1_pct, 1),
            "favorable_move_2bar_pct": round(fav2_pct, 1),
            "favorable_move_3bar_pct": round(fav3_pct, 1),
            "adverse_move_1bar_pct": round(adv1_pct, 1),
            "adverse_move_2bar_pct": round(adv2_pct, 1),
            "adverse_move_3bar_pct": round(adv3_pct, 1),
            "midpoint_reclaimed_within_2bars": _to_bool(ev.get("did_price_reclaim_signal_midpoint")),
            "signal_extreme_rebreak_within_2bars": _to_bool(ev.get("did_price_break_signal_extreme_again")),
            "first_pullback_timing_bars": ev.get("time_to_first_pullback_bars"),
            "first_pullback_size_ticks": ev.get("first_pullback_size_ticks"),
            "early_path_efficiency": round(_safe_div(fav2_pct, max(adv2_pct, 1.0)), 3),
            "outcome_class": outcome,
            "strong_runner": strong_runner,
            "mfe_pct": round(adv_pct, 1),
            "mae_pct": round(_safe_div(rev_mae, size) * 100.0, 1),
        }
        row["early_path_class"] = _early_path_class(row, ep_cfg)
        out.append(row)
    diagnostics["valid_rows"] = len(out)
    return out, diagnostics


def _distribution(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    if not n:
        return {}
    counts = {
        "failed_reversal": sum(1 for r in rows if r["outcome_class"] == "failed_reversal"),
        "micro_bounce": sum(1 for r in rows if r["outcome_class"] == "micro_bounce"),
        "scalp_reversal": sum(1 for r in rows if r["outcome_class"] == "scalp_reversal"),
        "expansion_reversal": sum(1 for r in rows if r["outcome_class"] == "expansion_reversal"),
        "strong_runner": sum(1 for r in rows if r.get("strong_runner")),
    }
    avg_mfe = round(sum(r.get("mfe_pct", 0.0) for r in rows) / n, 1)
    avg_mae = round(sum(r.get("mae_pct", 0.0) for r in rows) / n, 1)
    return {
        "n": n,
        **{f"prob_{k}": round(counts[k] / n, 4) for k in counts},
        "failure_rate": _pct(counts["failed_reversal"], n),
        "scalp_rate": _pct(counts["scalp_reversal"], n),
        "expansion_rate": _pct(counts["expansion_reversal"], n),
        "runner_rate": _pct(counts["strong_runner"], n),
        "avg_mfe": avg_mfe,
        "avg_mae": avg_mae,
        "mfe_mae": round(_safe_div(avg_mfe, avg_mae), 3) if avg_mae > 0 else None,
    }


def _group_distribution(rows: List[Dict[str, Any]], keys: Tuple[str, ...], min_n: int) -> List[Dict[str, Any]]:
    grp: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for r in rows:
        k = tuple(r.get(c) for c in keys)
        grp.setdefault(k, []).append(r)
    out: List[Dict[str, Any]] = []
    for k, g in grp.items():
        if len(g) < min_n:
            continue
        d = _distribution(g)
        out.append({"group_key": {keys[i]: k[i] for i in range(len(keys))}, **d})
    out.sort(key=lambda x: (x["failure_rate"], -x["runner_rate"], -x["n"]))
    return out


def _discover_thresholds(rows: List[Dict[str, Any]], cfg: Dict[str, Any], baseline: Dict[str, Any], min_n: int) -> List[Dict[str, Any]]:
    cands = cfg.get("threshold_candidates") or {}
    out: List[Dict[str, Any]] = []
    base_runner = baseline.get("runner_rate", 0.0)
    base_fail = baseline.get("failure_rate", 0.0)

    for x in cands.get("adverse_move_2bar_pct_max", []):
        g = [r for r in rows if r.get("adverse_move_2bar_pct", 999) <= float(x)]
        if len(g) < min_n:
            continue
        d = _distribution(g)
        out.append({"threshold": f"adverse_move_2bar_pct <= {x}", "n": len(g), "runner_lift_pp": round(d["runner_rate"] - base_runner, 1), "failure_delta_pp": round(d["failure_rate"] - base_fail, 1), "distribution": d})

    for y in cands.get("favorable_move_2bar_pct_min", []):
        g = [r for r in rows if r.get("favorable_move_2bar_pct", -999) >= float(y)]
        if len(g) < min_n:
            continue
        d = _distribution(g)
        out.append({"threshold": f"favorable_move_2bar_pct >= {y}", "n": len(g), "runner_lift_pp": round(d["runner_rate"] - base_runner, 1), "failure_delta_pp": round(d["failure_rate"] - base_fail, 1), "distribution": d})

    out.sort(key=lambda r: (-r["runner_lift_pp"], r["failure_delta_pp"], -r["n"]))
    return out[:12]


def _recommend_action(probs: Dict[str, float], n: int, cfg: Dict[str, Any]) -> str:
    a = cfg.get("decision_actions") or {}
    fail = probs.get("prob_failed_reversal", 0.0)
    exp = probs.get("prob_expansion_reversal", 0.0)
    run = probs.get("prob_strong_runner", 0.0)

    if fail >= float(a.get("exit_immediately_fail_prob", 0.55)):
        return "exit_immediately"
    if fail >= float(a.get("scratch_if_no_improvement_fail_prob", 0.40)):
        return "scratch_if_no_improvement"
    if run >= float(a.get("press_runner_min_prob", 0.38)) and fail <= float(a.get("press_runner_max_fail_prob", 0.18)) and n >= int(a.get("press_runner_min_n", 40)):
        return "press_runner"
    if run >= float(a.get("hold_for_runner_min_prob", 0.25)):
        return "hold_for_runner"
    if exp + run >= float(a.get("hold_for_expansion_min_prob", 0.35)):
        return "hold_for_expansion"
    return "scalp_only"


def _rule_candidates(rows: List[Dict[str, Any]], baseline: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    min_n = int((cfg.get("min_n") or {}).get("rule", 25))
    rules_cfg = cfg.get("rule_discovery") or {}
    min_runner_lift = float(rules_cfg.get("min_lift_vs_baseline_runner_pp", 6.0))
    max_fail_hold = float(rules_cfg.get("max_fail_rate_for_hold", 35.0))

    combos = [
        ("early_path_class",),
        ("early_path_class", "session"),
        ("early_path_class", "vwap_stretch_bucket"),
        ("early_path_class", "trend_state"),
        ("early_path_class", "key_level_interaction"),
    ]

    out: List[Dict[str, Any]] = []
    for keys in combos:
        for g in _group_distribution(rows, keys, min_n=min_n):
            runner_lift = round(g["runner_rate"] - baseline.get("runner_rate", 0.0), 1)
            fail_delta = round(g["failure_rate"] - baseline.get("failure_rate", 0.0), 1)
            if runner_lift < min_runner_lift and g["failure_rate"] > max_fail_hold:
                continue
            probs = {
                "prob_failed_reversal": g["prob_failed_reversal"],
                "prob_micro_bounce": g["prob_micro_bounce"],
                "prob_scalp_reversal": g["prob_scalp_reversal"],
                "prob_expansion_reversal": g["prob_expansion_reversal"],
                "prob_strong_runner": g["prob_strong_runner"],
            }
            out.append({
                "conditions": g["group_key"],
                "n": g["n"],
                "failure_rate": g["failure_rate"],
                "scalp_rate": g["scalp_rate"],
                "expansion_rate": g["expansion_rate"],
                "runner_rate": g["runner_rate"],
                "avg_mfe": g["avg_mfe"],
                "avg_mae": g["avg_mae"],
                "mfe_mae": g["mfe_mae"],
                "lift_vs_baseline_runner_pp": runner_lift,
                "lift_vs_baseline_failure_pp": fail_delta,
                "sample_quality": round(min(1.0, g["n"] / 120.0), 3),
                "probabilities": probs,
                "recommended_action": _recommend_action(probs, g["n"], cfg),
            })

    out.sort(key=lambda r: (-r["lift_vs_baseline_runner_pp"], r["failure_rate"], -r["n"]))
    return out[: int(rules_cfg.get("max_rules", 14))]


def _event_probabilities(row: Dict[str, Any], by_class: List[Dict[str, Any]], by_class_session: List[Dict[str, Any]], baseline: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    n = baseline.get("n", 1)
    probs = {
        "prob_failed_reversal": baseline.get("prob_failed_reversal", 0.0),
        "prob_micro_bounce": baseline.get("prob_micro_bounce", 0.0),
        "prob_scalp_reversal": baseline.get("prob_scalp_reversal", 0.0),
        "prob_expansion_reversal": baseline.get("prob_expansion_reversal", 0.0),
        "prob_strong_runner": baseline.get("prob_strong_runner", 0.0),
    }

    for g in by_class_session:
        k = g.get("group_key") or {}
        if k.get("early_path_class") == row.get("early_path_class") and k.get("session") == row.get("session"):
            probs = {k2: g[k2] for k2 in probs.keys()}
            n = g.get("n", n)
            break
    else:
        for g in by_class:
            k = g.get("group_key") or {}
            if k.get("early_path_class") == row.get("early_path_class"):
                probs = {k2: g[k2] for k2 in probs.keys()}
                n = g.get("n", n)
                break

    action = _recommend_action(probs, int(n), cfg)
    return {**probs, "recommended_action": action, "evidence_n": int(n)}


def compute_reversal_decision_engine(events_sample: List[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = _deep_merge(DEFAULT_REVERSAL_DECISION_CONFIG, cfg or {})
    if not merged.get("enabled", True):
        return {"enabled": False}
    if not events_sample:
        return {"enabled": True, "message": "no events in sample", "n_events": 0}

    rows, row_diag = _event_rows(events_sample, merged)
    if not rows:
        return {
            "enabled": True,
            "message": "no valid reversal events",
            "n_events": 0,
            "diagnostics": row_diag,
        }

    min_overall = int((merged.get("min_n") or {}).get("overall", 80))
    if len(rows) < min_overall:
        return {
            "enabled": True,
            "message": f"insufficient events for stable decision modeling ({len(rows)} < {min_overall})",
            "n_events": len(rows),
            "diagnostics": row_diag,
        }

    baseline = _distribution(rows)
    min_class = int((merged.get("min_n") or {}).get("class", 20))
    min_interaction = int((merged.get("min_n") or {}).get("interaction", 20))

    table_a = _group_distribution(rows, ("early_path_class",), min_n=min_class)
    table_b = _group_distribution(rows, ("early_path_class", "session"), min_n=min_interaction)
    table_c = _group_distribution(rows, ("early_path_class", "vwap_stretch_bucket"), min_n=min_interaction)
    table_d = _group_distribution(rows, ("early_path_class", "trend_state"), min_n=min_interaction)

    thresholds = _discover_thresholds(rows, merged, baseline, min_n=min_class)
    rules = _rule_candidates(rows, baseline, merged)

    questions = {
        "explosive_start_ny_open_runner": _yes_no_probe(table_b, {"early_path_class": "explosive_start", "session": "ny_open"}, baseline, "runner_rate", 5.0),
        "orderly_start_mid_ny_expansion": _yes_no_probe(table_b, {"early_path_class": "orderly_start", "session": "mid_ny"}, baseline, "expansion_rate", 5.0),
        "weak_start_no_midpoint_reclaim_failure": _probe_filtered(rows, lambda r: r.get("early_path_class") == "weak_start" and not r.get("midpoint_reclaimed_within_2bars"), baseline, "failure_rate", 8.0),
        "rebreak_within_2bars_hurts_runner": _probe_filtered(rows, lambda r: bool(r.get("signal_extreme_rebreak_within_2bars")), baseline, "runner_rate", -6.0),
        "low_adverse_2bar_improves_hold": _best_threshold(thresholds, "adverse_move_2bar_pct <="),
        "vwap_stretch_needs_strong_path": _probe_vwap_combo(rows, baseline),
        "press_runner_supported": any(r.get("recommended_action") == "press_runner" for r in rules),
    }

    event_decisions = []
    for r in rows[:250]:
        p = _event_probabilities(r, table_a, table_b, baseline, merged)
        event_decisions.append({
            "dt": r.get("dt"),
            "early_path_class": r.get("early_path_class"),
            "session": r.get("session"),
            "trend_state": r.get("trend_state"),
            "vwap_stretch_bucket": r.get("vwap_stretch_bucket"),
            **p,
        })

    return {
        "enabled": True,
        "n_events": len(rows),
        "strong_runner_definition": f"adv_pct >= {float((merged.get('outcome_thresholds') or {}).get('strong_runner_min_adv_pct', 100.0)):.1f}% of signal candle size",
        "baseline": baseline,
        "tables": {
            "outcome_by_early_path_class": table_a,
            "outcome_by_early_path_and_session": table_b,
            "outcome_by_early_path_and_vwap_stretch": table_c,
            "outcome_by_early_path_and_trend_state": table_d,
        },
        "threshold_discovery": thresholds,
        "decision_rules": rules,
        "event_decisions_sample": event_decisions,
        "research_questions": questions,
        "diagnostics": row_diag,
    }


def _yes_no_probe(table: List[Dict[str, Any]], cond: Dict[str, Any], baseline: Dict[str, Any], metric: str, min_delta_pp: float) -> Dict[str, Any]:
    for row in table:
        if all((row.get("group_key") or {}).get(k) == v for k, v in cond.items()):
            base = baseline.get(metric, 0.0)
            delta = round(row.get(metric, 0.0) - base, 1)
            return {"tested": True, "n": row.get("n", 0), "value": row.get(metric), "baseline": base, "delta_pp": delta, "holds": delta >= min_delta_pp}
    return {"tested": False, "reason": "combination not present with min_n"}


def _probe_filtered(rows: List[Dict[str, Any]], predicate: Any, baseline: Dict[str, Any], metric: str, min_delta_pp: float) -> Dict[str, Any]:
    sub = [r for r in rows if predicate(r)]
    if len(sub) < 20:
        return {"tested": False, "reason": "insufficient N"}
    d = _distribution(sub)
    base = baseline.get(metric, 0.0)
    delta = round(d.get(metric, 0.0) - base, 1)
    holds = delta >= min_delta_pp if min_delta_pp >= 0 else delta <= min_delta_pp
    return {"tested": True, "n": len(sub), "value": d.get(metric), "baseline": base, "delta_pp": delta, "holds": holds}


def _best_threshold(rows: List[Dict[str, Any]], prefix: str) -> Dict[str, Any]:
    c = [r for r in rows if str(r.get("threshold", "")).startswith(prefix)]
    if not c:
        return {"tested": False, "reason": "no threshold candidates"}
    c.sort(key=lambda r: (-(r.get("runner_lift_pp") or 0), r.get("failure_delta_pp") or 0, -(r.get("n") or 0)))
    return {"tested": True, "best": c[0], "holds": (c[0].get("runner_lift_pp") or 0) >= 4.0 and (c[0].get("failure_delta_pp") or 0) <= 0}


def _probe_vwap_combo(rows: List[Dict[str, Any]], baseline: Dict[str, Any]) -> Dict[str, Any]:
    strong = [r for r in rows if r.get("early_path_class") in ("explosive_start", "orderly_start")]
    strong_ext = [r for r in strong if str(r.get("vwap_stretch_bucket")) in ("extended", "deeply_below", "deeply_above")]
    if len(strong) < 20 or len(strong_ext) < 20:
        return {"tested": False, "reason": "insufficient N"}
    d1 = _distribution(strong)
    d2 = _distribution(strong_ext)
    return {
        "tested": True,
        "n_strong_path": len(strong),
        "n_strong_path_and_stretch": len(strong_ext),
        "runner_rate_strong_path": d1.get("runner_rate"),
        "runner_rate_with_stretch": d2.get("runner_rate"),
        "delta_pp": round((d2.get("runner_rate") or 0) - (d1.get("runner_rate") or 0), 1),
        "holds": (d2.get("runner_rate") or 0) >= (d1.get("runner_rate") or 0) + 3.0,
        "baseline_runner_rate": baseline.get("runner_rate"),
    }
