from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import math


DEFAULT_RECURSIVE_EDGE_SEARCH_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "max_depth": 2,
    "max_children_per_node": 12,
    "max_total_nodes": 120,
    "min_gain_to_expand": 0.04,
    "allow_multi_refinement_per_child": False,
    "seed_sources": {
        "suggested_next_tests": True,
        "decision_rules": True,
        "elite_setups": True,
        "top_families": True,
    },
    "operators": {
        "session": {"enabled": True},
        "basis": {"enabled": True},
        "threshold": {"enabled": True},
        "candle_bucket": {"enabled": True},
        "target": {"enabled": True},
        "context": {"enabled": True},
        "early_path": {"enabled": True},
    },
    "promotion": {
        "min_n": 25,
        "min_runner_lift_pp": 4.0,
        "min_fail_reduction_pp": 4.0,
        "min_mfe_mae_improvement": 0.25,
        "min_stability": 0.42,
        "max_complexity_penalty": 0.22,
    },
    "pruning": {
        "max_fail_worsen_pp": 3.0,
        "max_runner_drop_pp": 4.0,
        "min_mfe_mae": 1.0,
        "max_edge_decay_penalty": 0.75,
        "min_plateau_width": 1,
        "max_duplicate_jaccard": 0.92,
        "min_material_gain_score": 0.015,
    },
    "scoring": {
        "runner_weight": 0.34,
        "expansion_weight": 0.20,
        "failure_penalty": 0.28,
        "mfe_mae_weight": 0.16,
        "stability_weight": 0.14,
        "complexity_penalty": 0.12,
        "low_sample_penalty": 0.12,
        "plateau_bonus": 0.06,
        "edge_decay_penalty": 0.10,
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


def _sf(v: Any, d: float = 0.0) -> float:
    try:
        f = float(v)
        if math.isnan(f):
            return d
        return f
    except Exception:
        return d


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("true", "t", "1", "yes", "y")
    return bool(v)


def _early_path_class(row: Dict[str, Any], ep_cfg: Dict[str, Any]) -> str:
    fav2 = _sf(row.get("favorable_move_2bar_pct"), 0.0)
    adv2 = _sf(row.get("adverse_move_2bar_pct"), 0.0)
    reclaimed = _to_bool(row.get("midpoint_reclaimed_within_2bars"))
    rebreak = _to_bool(row.get("signal_extreme_rebreak_within_2bars"))
    if fav2 >= float(ep_cfg.get("explosive_min_fav_2bar_pct", 45.0)) and adv2 <= float(ep_cfg.get("explosive_max_adv_2bar_pct", 20.0)) and reclaimed and not rebreak:
        return "explosive_start"
    if fav2 >= float(ep_cfg.get("orderly_min_fav_2bar_pct", 25.0)) and adv2 <= float(ep_cfg.get("orderly_max_adv_2bar_pct", 35.0)) and reclaimed:
        return "orderly_start"
    if fav2 <= float(ep_cfg.get("weak_max_fav_2bar_pct", 15.0)) or adv2 >= float(ep_cfg.get("weak_min_adv_2bar_pct", 35.0)) or rebreak:
        return "weak_start"
    return "mixed_start"


def _normalize_events(events_sample: List[Dict[str, Any]], decision_cfg: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    ep_cfg = (decision_cfg or {}).get("early_path") or {}
    out_th = (decision_cfg or {}).get("outcome_thresholds") or {}
    fail_max = float(out_th.get("failed_reversal_max_adv_pct", 10.0))
    scalp_max = float(out_th.get("scalp_reversal_max_adv_pct", 50.0))
    exp_min = float(out_th.get("expansion_reversal_min_adv_pct", 50.0))
    run_min = float(out_th.get("strong_runner_min_adv_pct", 100.0))

    for ev in events_sample:
        direction = int(_sf(ev.get("direction"), 0))
        if direction != -1:
            continue
        size = _sf(ev.get("size_ticks"), 0.0)
        if size <= 0:
            continue
        mfe_ticks = _sf(ev.get("adv_ticks", ev.get("trade_fav_ticks")), 0.0)
        mae_ticks = _sf(ev.get("fav_ticks", ev.get("trade_adv_ticks")), 0.0)
        mfe_pct = _safe_div(mfe_ticks, size) * 100.0
        mae_pct = _safe_div(mae_ticks, size) * 100.0
        fav2 = _safe_div(_sf(ev.get("early_fav_2bar_ticks"), 0.0), size) * 100.0
        adv2 = _safe_div(_sf(ev.get("early_adv_2bar_ticks"), 0.0), size) * 100.0
        row = {
            "trade_mode": ev.get("trade_mode") or "reverse",
            "direction": direction,
            "tf_minutes": int(_sf(ev.get("tf_minutes"), 0)),
            "window_minutes": int(_sf(ev.get("window_minutes", ev.get("forward_window_minutes")), 0)),
            "candle_bucket": ev.get("candle_bucket") or "unknown",
            "session": ev.get("session_bucket") or "unknown",
            "basis": ev.get("basis") or "unknown",
            "threshold_value": _sf(ev.get("threshold_value"), 0.0),
            "target_percent": _sf(ev.get("target_percent"), 0.0),
            "trend_state": ev.get("trend_alignment_label") or "unknown",
            "vwap_stretch_bucket": ev.get("vwap_ext_bucket") or ev.get("vwap_signed_bucket") or "unknown",
            "ma_context": ev.get("ma100_location_bucket") or ev.get("ma100_signed_bucket") or "unknown",
            "key_level_interaction": ev.get("level_interaction_label") or "unknown",
            "signal_structure": ev.get("directional_context_label") or "unknown",
            "favorable_move_2bar_pct": round(fav2, 3),
            "adverse_move_2bar_pct": round(adv2, 3),
            "midpoint_reclaimed_within_2bars": _to_bool(ev.get("did_price_reclaim_signal_midpoint")),
            "signal_extreme_rebreak_within_2bars": _to_bool(ev.get("did_price_break_signal_extreme_again")),
            "early_path_efficiency": round(_safe_div(fav2, max(adv2, 1.0)), 4),
            "mfe_pct": round(mfe_pct, 3),
            "mae_pct": round(mae_pct, 3),
        }
        row["early_path_class"] = _early_path_class(row, ep_cfg)
        row["is_fail"] = mfe_pct < fail_max
        row["is_scalp"] = fail_max <= mfe_pct < scalp_max
        row["is_expansion"] = mfe_pct >= exp_min
        row["is_runner"] = mfe_pct >= run_min
        out.append(row)
    return out


def _apply_filters(rows: List[Dict[str, Any]], filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = rows
    for k, v in (filters or {}).items():
        out = [r for r in out if r.get(k) == v]
    return out


def _stability_score(n: int, fail_rate: float, runner_rate: float, mfe_mae: Optional[float], complexity: int) -> float:
    sample = min(1.0, n / 140.0)
    fail_component = max(0.0, 1.0 - (fail_rate / 50.0))
    runner_component = min(1.0, runner_rate / 65.0)
    mfe_component = min(1.0, (mfe_mae or 0.0) / 2.5)
    complexity_drag = min(0.35, 0.04 * complexity)
    return round(max(0.0, min(1.0, (0.32 * sample) + (0.24 * fail_component) + (0.22 * runner_component) + (0.22 * mfe_component) - complexity_drag)), 4)


def _compute_metrics(rows: List[Dict[str, Any]], baseline: Dict[str, Any], parent: Optional[Dict[str, Any]], origin: Dict[str, Any], complexity: int) -> Dict[str, Any]:
    n = len(rows)
    if n <= 0:
        return {"n": 0}
    fail_rate = round(sum(1 for r in rows if r["is_fail"]) * 100.0 / n, 1)
    scalp_rate = round(sum(1 for r in rows if r["is_scalp"]) * 100.0 / n, 1)
    expansion_rate = round(sum(1 for r in rows if r["is_expansion"]) * 100.0 / n, 1)
    runner_rate = round(sum(1 for r in rows if r["is_runner"]) * 100.0 / n, 1)
    avg_mfe = round(sum(_sf(r.get("mfe_pct"), 0.0) for r in rows) / n, 3)
    avg_mae = round(sum(_sf(r.get("mae_pct"), 0.0) for r in rows) / n, 3)
    mfe_mae = round(_safe_div(avg_mfe, avg_mae), 4) if avg_mae > 0 else None
    stability = _stability_score(n, fail_rate, runner_rate, mfe_mae, complexity)
    return {
        "n": n,
        "fail_rate": fail_rate,
        "scalp_rate": scalp_rate,
        "expansion_rate": expansion_rate,
        "runner_rate": runner_rate,
        "avg_mfe": avg_mfe,
        "avg_mae": avg_mae,
        "mfe_mae": mfe_mae,
        "win_rate": round(100.0 - fail_rate, 1),
        "stability_score": stability,
        "lift_vs_baseline_runner_pp": round(runner_rate - _sf(baseline.get("runner_rate"), 0.0), 2),
        "lift_vs_baseline_fail_pp": round(fail_rate - _sf(baseline.get("fail_rate"), 0.0), 2),
        "lift_vs_parent_runner_pp": round(runner_rate - _sf((parent or {}).get("runner_rate"), runner_rate), 2),
        "lift_vs_parent_fail_pp": round(fail_rate - _sf((parent or {}).get("fail_rate"), fail_rate), 2),
        "lift_vs_origin_runner_pp": round(runner_rate - _sf(origin.get("runner_rate"), runner_rate), 2),
        "complexity_count": complexity,
        "plateau_width": None,
        "edge_decay_penalty": None,
    }


def _score_branch(m: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    s = cfg.get("scoring") or {}
    sample_pen = 1.0 if int(m.get("n", 0)) >= int((cfg.get("promotion") or {}).get("min_n", 25)) else 0.0
    mfe_mae_norm = min(1.0, _safe_div(_sf(m.get("mfe_mae"), 0.0), 2.5))
    complexity = _sf(m.get("complexity_count"), 0.0)
    score = (
        _sf(s.get("runner_weight"), 0.34) * _safe_div(_sf(m.get("runner_rate"), 0.0), 100.0)
        + _sf(s.get("expansion_weight"), 0.20) * _safe_div(_sf(m.get("expansion_rate"), 0.0), 100.0)
        - _sf(s.get("failure_penalty"), 0.28) * _safe_div(_sf(m.get("fail_rate"), 0.0), 100.0)
        + _sf(s.get("mfe_mae_weight"), 0.16) * mfe_mae_norm
        + _sf(s.get("stability_weight"), 0.14) * _sf(m.get("stability_score"), 0.0)
        - _sf(s.get("complexity_penalty"), 0.12) * min(1.0, complexity / 6.0)
        - _sf(s.get("low_sample_penalty"), 0.12) * (1.0 - sample_pen)
    )
    return round(score, 5)


def _promote(child: Dict[str, Any], parent: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[bool, str]:
    p = cfg.get("promotion") or {}
    n_ok = int(child.get("n", 0)) >= int(p.get("min_n", 25))
    runner_up = _sf(child.get("lift_vs_parent_runner_pp"), 0.0) >= float(p.get("min_runner_lift_pp", 4.0))
    fail_down = (_sf(parent.get("fail_rate"), 0.0) - _sf(child.get("fail_rate"), 0.0)) >= float(p.get("min_fail_reduction_pp", 4.0))
    mfe_up = (_sf(child.get("mfe_mae"), 0.0) - _sf(parent.get("mfe_mae"), 0.0)) >= float(p.get("min_mfe_mae_improvement", 0.25))
    stab_ok = _sf(child.get("stability_score"), 0.0) >= float(p.get("min_stability", 0.42))
    comp_ok = _sf(child.get("complexity_count"), 0.0) * 0.04 <= float(p.get("max_complexity_penalty", 0.22))
    if n_ok and (runner_up or fail_down or mfe_up) and stab_ok and comp_ok:
        reason_parts = []
        if runner_up:
            reason_parts.append(f"runner +{child.get('lift_vs_parent_runner_pp')}pp")
        if fail_down:
            reason_parts.append(f"fail -{round(_sf(parent.get('fail_rate')) - _sf(child.get('fail_rate')), 2)}pp")
        if mfe_up:
            reason_parts.append(f"MFE/MAE +{round(_sf(child.get('mfe_mae')) - _sf(parent.get('mfe_mae')), 3)}")
        return True, "; ".join(reason_parts) or "material improvement"
    return False, "no material parent-relative improvement"


def _prune_reason(child: Dict[str, Any], parent: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
    p = cfg.get("pruning") or {}
    if int(child.get("n", 0)) < int((cfg.get("promotion") or {}).get("min_n", 25)):
        return "N below minimum threshold"
    if _sf(child.get("fail_rate"), 0.0) - _sf(parent.get("fail_rate"), 0.0) > float(p.get("max_fail_worsen_pp", 3.0)):
        return "failure rate worsened materially"
    if _sf(parent.get("runner_rate"), 0.0) - _sf(child.get("runner_rate"), 0.0) > float(p.get("max_runner_drop_pp", 4.0)):
        return "runner rate collapsed vs parent"
    if _sf(child.get("mfe_mae"), 0.0) < float(p.get("min_mfe_mae", 1.0)):
        return "weak MFE/MAE"
    if child.get("edge_decay_penalty") is not None and _sf(child.get("edge_decay_penalty"), 0.0) > float(p.get("max_edge_decay_penalty", 0.75)):
        return "edge decay too high"
    return None


def _jaccard(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    sa = {f"{k}={v}" for k, v in (a or {}).items()}
    sb = {f"{k}={v}" for k, v in (b or {}).items()}
    if not sa and not sb:
        return 1.0
    return _safe_div(len(sa & sb), len(sa | sb))


def _collect_seed_filters(findings: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    seeds: List[Dict[str, Any]] = []
    ss = cfg.get("seed_sources") or {}
    if ss.get("decision_rules", True):
        for r in ((findings.get("reversal_decision_engine") or {}).get("decision_rules") or [])[:8]:
            cond = (r.get("conditions") or {}).copy()
            if cond:
                seeds.append({"seed_type": "decision_rule", "filters": cond, "label": f"decision_rule:{cond}"})
    if ss.get("elite_setups", True):
        for r in ((findings.get("elite_reversal_setup_extractor") or {}).get("elite_setups") or [])[:8]:
            cond = (r.get("conditions") or {}).copy()
            if cond:
                seeds.append({"seed_type": "elite_setup", "filters": cond, "label": f"elite_setup:{cond}"})
    if ss.get("top_families", True):
        for f in (findings.get("setup_families") or [])[:8]:
            key = f.get("family_key")
            if isinstance(key, dict):
                cond = {
                    "trade_mode": key.get("trade_mode"),
                    "direction": key.get("direction"),
                    "tf_minutes": key.get("tf_minutes"),
                    "candle_bucket": key.get("candle_bucket"),
                }
            elif isinstance(key, (list, tuple)) and len(key) >= 4:
                cond = {
                    "trade_mode": key[0],
                    "direction": key[1],
                    "tf_minutes": key[2],
                    "candle_bucket": key[3],
                }
            else:
                cond = {
                    "trade_mode": f.get("trade_mode"),
                    "direction": f.get("direction"),
                    "tf_minutes": f.get("tf_minutes"),
                    "candle_bucket": f.get("candle_bucket"),
                }
            cond = {k: v for k, v in cond.items() if v is not None}
            if cond:
                seeds.append({"seed_type": "top_family", "filters": cond, "label": f"top_family:{cond}"})
    if ss.get("suggested_next_tests", True):
        for text in (findings.get("next_tests") or [])[:8]:
            t = str(text).lower()
            cond: Dict[str, Any] = {}
            if "body" in t:
                cond["basis"] = "body"
            elif "range" in t:
                cond["basis"] = "range"
            for sess in ("ny_open", "ny_pre", "london", "mid_ny", "power_hour", "asia", "london_ny_overlap"):
                if sess in t:
                    cond["session"] = sess
                    break
            if "explosive" in t:
                cond["early_path_class"] = "explosive_start"
            if cond:
                seeds.append({"seed_type": "next_test", "filters": cond, "label": f"next_test:{text}"})
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for s in seeds:
        sig = tuple(sorted((s.get("filters") or {}).items()))
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(s)
    return deduped[:12]


def _operator_candidates(rows: List[Dict[str, Any]], filters: Dict[str, Any], cfg: Dict[str, Any]) -> List[Tuple[str, str, Any]]:
    candidates: List[Tuple[str, str, Any]] = []
    operators = cfg.get("operators") or {}
    by_op = {
        "session": ["session"],
        "basis": ["basis"],
        "threshold": ["threshold_value"],
        "candle_bucket": ["candle_bucket"],
        "target": ["target_percent"],
        "context": ["trend_state", "vwap_stretch_bucket", "ma_context", "key_level_interaction", "signal_structure"],
        "early_path": ["early_path_class"],
    }
    for op, keys in by_op.items():
        if not (operators.get(op) or {}).get("enabled", True):
            continue
        for k in keys:
            if k in filters:
                continue
            vals = sorted({r.get(k) for r in rows if r.get(k) not in (None, "", "unknown", 0.0)})
            for v in vals[:6]:
                candidates.append((op, k, v))
    return candidates


def compute_recursive_edge_search(
    findings_payload: Dict[str, Any],
    events_sample: List[Dict[str, Any]],
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    merged = _deep_merge(DEFAULT_RECURSIVE_EDGE_SEARCH_CONFIG, cfg or {})
    if not merged.get("enabled", True):
        return {"enabled": False}
    rows = _normalize_events(events_sample or [], (findings_payload.get("config") or {}).get("reversal_decision_engine"))
    if not rows:
        return {"enabled": True, "message": "no valid reversal events for recursive search", "roots": [], "best_promoted_branches": []}

    baseline = _compute_metrics(rows, {}, None, {}, complexity=0)
    baseline["branch_score"] = _score_branch(baseline, merged)
    seeds = _collect_seed_filters(findings_payload, merged)
    if not seeds:
        seeds = [{"seed_type": "baseline", "filters": {"trade_mode": "reverse"}, "label": "baseline_reverse"}]

    max_depth = int(merged.get("max_depth", 2))
    max_children = int(merged.get("max_children_per_node", 12))
    max_nodes = int(merged.get("max_total_nodes", 120))
    min_gain = float(merged.get("min_gain_to_expand", 0.04))
    dup_thr = float((merged.get("pruning") or {}).get("max_duplicate_jaccard", 0.92))

    roots: List[Dict[str, Any]] = []
    promoted_all: List[Dict[str, Any]] = []
    pruned_all: List[Dict[str, Any]] = []
    operator_stats: Dict[str, Dict[str, int]] = {}
    node_count = 0

    for seed in seeds:
        seed_filters = seed.get("filters") or {}
        seed_rows = _apply_filters(rows, seed_filters)
        seed_metrics = _compute_metrics(seed_rows, baseline, baseline, baseline, len(seed_filters))
        if int(seed_metrics.get("n", 0)) < int((merged.get("promotion") or {}).get("min_n", 25)):
            continue
        seed_metrics["branch_score"] = _score_branch(seed_metrics, merged)
        root = {"seed_type": seed.get("seed_type"), "seed_label": seed.get("label"), "filters": seed_filters, "metrics": seed_metrics, "children_tested": [], "promoted_children": [], "pruned_children": []}
        queue: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = [(0, seed_filters, seed_metrics)]
        seen_filters: List[Dict[str, Any]] = [seed_filters]

        while queue and node_count < max_nodes:
            depth, parent_filters, parent_metrics = queue.pop(0)
            if depth >= max_depth:
                continue
            if (_sf(parent_metrics.get("branch_score"), 0.0) - _sf(seed_metrics.get("branch_score"), 0.0)) < min_gain and depth > 0:
                continue
            parent_rows = _apply_filters(rows, parent_filters)
            ops = _operator_candidates(parent_rows, parent_filters, merged)[:max_children]
            for op_name, key, value in ops:
                if node_count >= max_nodes:
                    break
                child_filters = dict(parent_filters)
                child_filters[key] = value
                if any(_jaccard(child_filters, old) >= dup_thr for old in seen_filters):
                    child = {"filters": child_filters, "depth": depth + 1, "operator": op_name, "status": "pruned", "reason": "near-duplicate branch", "metrics": {"n": len(_apply_filters(rows, child_filters))}}
                    root["children_tested"].append(child)
                    root["pruned_children"].append(child)
                    pruned_all.append(child)
                    operator_stats.setdefault(op_name, {"promoted": 0, "pruned": 0})["pruned"] += 1
                    continue
                child_rows = _apply_filters(rows, child_filters)
                child_metrics = _compute_metrics(child_rows, baseline, parent_metrics, seed_metrics, len(child_filters))
                child_metrics["branch_score"] = _score_branch(child_metrics, merged)
                reason = _prune_reason(child_metrics, parent_metrics, merged)
                promoted, promo_reason = _promote(child_metrics, parent_metrics, merged)
                if reason:
                    child = {"filters": child_filters, "depth": depth + 1, "operator": op_name, "status": "pruned", "reason": reason, "metrics": child_metrics}
                    root["children_tested"].append(child)
                    root["pruned_children"].append(child)
                    pruned_all.append(child)
                    operator_stats.setdefault(op_name, {"promoted": 0, "pruned": 0})["pruned"] += 1
                    continue
                if promoted:
                    child = {"filters": child_filters, "depth": depth + 1, "operator": op_name, "status": "promoted", "reason": promo_reason, "metrics": child_metrics, "parent_filters": parent_filters}
                    root["children_tested"].append(child)
                    root["promoted_children"].append(child)
                    promoted_all.append(child)
                    seen_filters.append(child_filters)
                    queue.append((depth + 1, child_filters, child_metrics))
                    operator_stats.setdefault(op_name, {"promoted": 0, "pruned": 0})["promoted"] += 1
                else:
                    child = {"filters": child_filters, "depth": depth + 1, "operator": op_name, "status": "pruned", "reason": promo_reason, "metrics": child_metrics}
                    root["children_tested"].append(child)
                    root["pruned_children"].append(child)
                    pruned_all.append(child)
                    operator_stats.setdefault(op_name, {"promoted": 0, "pruned": 0})["pruned"] += 1
                node_count += 1
        roots.append(root)

    promoted_all.sort(key=lambda x: _sf((x.get("metrics") or {}).get("branch_score"), -1.0), reverse=True)
    best = promoted_all[:10]
    dead = sorted(pruned_all, key=lambda x: int((x.get("metrics") or {}).get("n", 0)), reverse=True)[:12]
    final_candidates = [b for b in best if _sf((b.get("metrics") or {}).get("stability_score"), 0.0) >= 0.5][:6]

    research_answers = {
        "which_seed_type_strongest": (best[0].get("operator") if best else "none"),
        "operators_improve_most": sorted(operator_stats.items(), key=lambda kv: kv[1]["promoted"], reverse=True)[:3],
        "operators_dead_end_most": sorted(operator_stats.items(), key=lambda kv: kv[1]["pruned"], reverse=True)[:3],
        "depth_where_improvement_tapers": max((int(x.get("depth", 0)) for x in best), default=0),
        "best_branch_driver_mix": {
            "early_path": sum(1 for b in best if b.get("operator") == "early_path"),
            "session": sum(1 for b in best if b.get("operator") == "session"),
            "context": sum(1 for b in best if b.get("operator") == "context"),
            "target": sum(1 for b in best if b.get("operator") == "target"),
        },
        "fragile_scalp_family_recursion_value": "mixed_to_low" if sum(1 for b in best if (b.get("filters") or {}).get("early_path_class") == "weak_start") > 2 else "improves_when_early_path_filtered",
        "oos_priority_candidates": [c.get("filters") for c in final_candidates[:4]],
        "likely_overfit_branches": [b.get("filters") for b in best if int((b.get("metrics") or {}).get("n", 0)) < 35][:4],
    }

    strategy_handoff = []
    for c in final_candidates:
        m = c.get("metrics") or {}
        strategy_handoff.append(
            {
                "entry": c.get("filters"),
                "early_validation": [
                    "early_path_class must stay non-weak",
                    "adverse_move_2bar_pct remains under parent median",
                    "no immediate signal-extreme rebreak",
                ],
                "decision": "hold_for_runner" if _sf(m.get("runner_rate"), 0.0) >= 45.0 else "hold_for_expansion",
                "why_survived_recursion": c.get("reason"),
            }
        )

    return {
        "enabled": True,
        "config_used": merged,
        "search_configuration": {
            "seed_types_used": [s.get("seed_type") for s in seeds],
            "max_depth": max_depth,
            "max_children_per_node": max_children,
            "max_total_nodes": max_nodes,
            "min_gain_to_expand": min_gain,
            "promotion_rules": merged.get("promotion"),
            "pruning_rules": merged.get("pruning"),
            "scoring_formula": merged.get("scoring"),
        },
        "seed_summary": [{"seed_type": r.get("seed_type"), "seed_label": r.get("seed_label"), "filters": r.get("filters"), "n": (r.get("metrics") or {}).get("n")} for r in roots],
        "roots": roots,
        "best_promoted_branches": best,
        "dead_end_branches": dead,
        "final_promoted_candidates": final_candidates,
        "operator_stats": operator_stats,
        "research_questions": research_answers,
        "strategy_handoff": strategy_handoff,
        "diagnostics": {
            "n_rows_considered": len(rows),
            "n_roots": len(roots),
            "n_promoted": len(promoted_all),
            "n_pruned": len(pruned_all),
            "n_nodes_visited": node_count,
        },
    }
