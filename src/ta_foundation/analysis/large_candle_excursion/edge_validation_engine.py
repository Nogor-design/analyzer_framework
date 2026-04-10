from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import math


DEFAULT_EDGE_VALIDATION_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "candidate_limits": {
        "elite_setups": 10,
        "decision_rules": 12,
        "recursive_branches": 12,
        "family_baselines": 8,
    },
    "split": {
        "method": "time_is_oos",
        "is_ratio": 0.7,
        "multi_period": {
            "enabled": True,
            "periods": 3,
        },
        "rolling": {
            "enabled": False,
            "train_ratio": 0.6,
            "test_ratio": 0.2,
            "step_ratio": 0.2,
        },
    },
    "minimum_samples": {
        "is_min_n": 30,
        "oos_min_n": 20,
    },
    "classification": {
        "stable": {
            "runner_drop_pp_max": 8.0,
            "fail_increase_pp_max": 6.0,
            "min_oos_mfe_mae": 1.05,
            "min_oos_runner_vs_baseline_pp": 6.0,
        },
        "acceptable": {
            "runner_drop_pp_max": 15.0,
            "fail_increase_pp_max": 10.0,
            "min_oos_runner_vs_baseline_pp": 2.0,
        },
        "likely_overfit": {
            "runner_drop_pp_min": 20.0,
            "fail_increase_pp_min": 12.0,
            "runner_to_baseline_distance_pp_max": 3.0,
            "mfe_mae_collapse_delta_min": 0.35,
        },
    },
    "stability_score": {
        "weights": {
            "runner_retention": 0.30,
            "fail_control": 0.25,
            "mfe_mae_retention": 0.20,
            "oos_edge_vs_baseline": 0.15,
            "sample_quality": 0.10,
        }
    },
    "leaderboard": {
        "top_n": 12,
    },
    "handoff": {
        "max_candidates": 8,
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


def _parse_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return datetime.min.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return v
    if isinstance(v, str):
        s = v.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                return datetime.min.replace(tzinfo=datetime.now().astimezone().tzinfo)
            return dt
        except Exception:
            pass
    return datetime.min.replace(tzinfo=datetime.now().astimezone().tzinfo)


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
            "dt": ev.get("dt") or ev.get("signal_dt") or ev.get("entry_dt") or ev.get("timestamp"),
            "trade_mode": ev.get("trade_mode") or "reverse",
            "direction": direction,
            "tf_minutes": int(_sf(ev.get("tf_minutes"), 0)),
            "timeframe": int(_sf(ev.get("tf_minutes"), 0)),
            "window_minutes": int(_sf(ev.get("window_minutes", ev.get("forward_window_minutes")), 0)),
            "candle_bucket": ev.get("candle_bucket") or "unknown",
            "session": ev.get("session_bucket") or "unknown",
            "basis": ev.get("basis") or "unknown",
            "target_percent": _sf(ev.get("target_percent"), 0.0),
            "trend_state": ev.get("trend_alignment_label") or "unknown",
            "vwap_stretch_bucket": ev.get("vwap_ext_bucket") or ev.get("vwap_signed_bucket") or "unknown",
            "key_level_interaction": ev.get("level_interaction_label") or "unknown",
            "signal_structure": ev.get("directional_context_label") or "unknown",
            "favorable_move_2bar_pct": round(fav2, 3),
            "adverse_move_2bar_pct": round(adv2, 3),
            "midpoint_reclaimed_within_2bars": _to_bool(ev.get("did_price_reclaim_signal_midpoint")),
            "signal_extreme_rebreak_within_2bars": _to_bool(ev.get("did_price_break_signal_extreme_again")),
            "mfe_pct": round(mfe_pct, 3),
            "mae_pct": round(mae_pct, 3),
        }
        row["early_path_class"] = _early_path_class(row, ep_cfg)
        row["is_fail"] = mfe_pct < fail_max
        row["is_scalp"] = fail_max <= mfe_pct < scalp_max
        row["is_expansion"] = mfe_pct >= exp_min
        row["is_runner"] = mfe_pct >= run_min
        out.append(row)
    out.sort(key=lambda r: _parse_dt(r.get("dt")))
    return out


def _compute_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    if n <= 0:
        return {
            "n": 0,
            "fail_rate": 0.0,
            "scalp_rate": 0.0,
            "expansion_rate": 0.0,
            "runner_rate": 0.0,
            "avg_mfe": 0.0,
            "avg_mae": 0.0,
            "mfe_mae": None,
        }
    fail_rate = round(sum(1 for r in rows if r["is_fail"]) * 100.0 / n, 1)
    scalp_rate = round(sum(1 for r in rows if r["is_scalp"]) * 100.0 / n, 1)
    expansion_rate = round(sum(1 for r in rows if r["is_expansion"]) * 100.0 / n, 1)
    runner_rate = round(sum(1 for r in rows if r["is_runner"]) * 100.0 / n, 1)
    avg_mfe = round(sum(_sf(r.get("mfe_pct"), 0.0) for r in rows) / n, 3)
    avg_mae = round(sum(_sf(r.get("mae_pct"), 0.0) for r in rows) / n, 3)
    mfe_mae = round(_safe_div(avg_mfe, avg_mae), 4) if avg_mae > 0 else None
    return {
        "n": n,
        "fail_rate": fail_rate,
        "scalp_rate": scalp_rate,
        "expansion_rate": expansion_rate,
        "runner_rate": runner_rate,
        "avg_mfe": avg_mfe,
        "avg_mae": avg_mae,
        "mfe_mae": mfe_mae,
    }


def _slice_by_filters(rows: List[Dict[str, Any]], filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    alias = {
        "timeframe": "tf_minutes",
        "structure": "signal_structure",
    }
    out = rows
    for key, val in (filters or {}).items():
        if key in ("recommended_action", "source", "seed_type"):
            continue
        rk = alias.get(key, key)
        out = [r for r in out if r.get(rk) == val]
    return out


def _build_candidates(findings_payload: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    limits = cfg.get("candidate_limits") or {}

    for rec in ((findings_payload.get("elite_reversal_setup_extractor") or {}).get("elite_setups") or [])[: int(limits.get("elite_setups", 10))]:
        cond = dict(rec.get("conditions") or {})
        out.append({
            "candidate_name": f"elite:{cond}",
            "source_type": "elite_setup",
            "parent_branch": rec.get("source") or "elite_reversal_setup_extractor",
            "filters": cond,
        })

    for rec in ((findings_payload.get("reversal_decision_engine") or {}).get("decision_rules") or [])[: int(limits.get("decision_rules", 12))]:
        cond = dict(rec.get("conditions") or {})
        out.append({
            "candidate_name": f"decision_rule:{cond}",
            "source_type": "decision_rule",
            "parent_branch": "reversal_decision_engine",
            "filters": cond,
        })

    branches = (
        ((findings_payload.get("recursive_edge_search") or {}).get("final_promoted_candidates") or [])
        or ((findings_payload.get("recursive_edge_search") or {}).get("best_promoted_branches") or [])
    )
    for rec in branches[: int(limits.get("recursive_branches", 12))]:
        filters = dict(rec.get("filters") or {})
        out.append({
            "candidate_name": f"recursive:{filters}",
            "source_type": "recursive_branch",
            "parent_branch": str(rec.get("parent_filters") or "seed"),
            "filters": filters,
        })

    for rec in (findings_payload.get("setup_families") or [])[: int(limits.get("family_baselines", 8))]:
        key = rec.get("family_key")
        if isinstance(key, dict):
            filters = {
                "trade_mode": key.get("trade_mode"),
                "direction": key.get("direction"),
                "tf_minutes": key.get("tf_minutes"),
                "candle_bucket": key.get("candle_bucket"),
            }
        else:
            filters = {
                "trade_mode": rec.get("trade_mode"),
                "direction": rec.get("direction"),
                "tf_minutes": rec.get("tf_minutes"),
                "candle_bucket": rec.get("candle_bucket"),
            }
        filters = {k: v for k, v in filters.items() if v is not None}
        if not filters:
            continue
        out.append({
            "candidate_name": f"family_anchor:{filters}",
            "source_type": "family_baseline",
            "parent_branch": "setup_family",
            "filters": filters,
        })

    seen = set()
    deduped = []
    for c in out:
        sig = (c.get("source_type"), tuple(sorted((c.get("filters") or {}).items())))
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(c)
    return deduped


def _split_rows(rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    if not rows:
        return [], [], {"method": "time_is_oos", "is_ratio": 0.7, "n_total": 0}
    split_cfg = cfg.get("split") or {}
    method = str(split_cfg.get("method") or "time_is_oos")
    is_ratio = _sf(split_cfg.get("is_ratio"), 0.7)
    is_ratio = max(0.5, min(0.9, is_ratio))
    cut = max(1, min(len(rows) - 1, int(len(rows) * is_ratio)))
    is_rows = rows[:cut]
    oos_rows = rows[cut:]
    return is_rows, oos_rows, {
        "method": method,
        "is_ratio": is_ratio,
        "n_total": len(rows),
        "is_range": [str(is_rows[0].get("dt")), str(is_rows[-1].get("dt"))] if is_rows else [None, None],
        "oos_range": [str(oos_rows[0].get("dt")), str(oos_rows[-1].get("dt"))] if oos_rows else [None, None],
    }


def _multi_period(rows: List[Dict[str, Any]], periods: int) -> List[Dict[str, Any]]:
    if periods <= 1 or not rows:
        return []
    n = len(rows)
    chunk = max(1, n // periods)
    out: List[Dict[str, Any]] = []
    for i in range(periods):
        start = i * chunk
        end = n if i == periods - 1 else min(n, (i + 1) * chunk)
        sub = rows[start:end]
        if not sub:
            continue
        m = _compute_metrics(sub)
        out.append({
            "period": i + 1,
            "label": ["early", "middle", "late"][i] if periods == 3 else f"period_{i + 1}",
            "n": m.get("n", 0),
            "runner_rate": m.get("runner_rate", 0.0),
            "fail_rate": m.get("fail_rate", 0.0),
            "mfe_mae": m.get("mfe_mae"),
        })
    return out


def _rolling_stub(rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    rcfg = ((cfg.get("split") or {}).get("rolling") or {})
    if not rcfg.get("enabled"):
        return {"enabled": False, "message": "rolling validation disabled"}
    return {
        "enabled": True,
        "message": "rolling validation scaffold ready; full rolling window scoring pending",
        "train_ratio": _sf(rcfg.get("train_ratio"), 0.6),
        "test_ratio": _sf(rcfg.get("test_ratio"), 0.2),
        "step_ratio": _sf(rcfg.get("step_ratio"), 0.2),
        "n_events": len(rows),
    }


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _stability_score(rec: Dict[str, Any], baseline_runner: float, cfg: Dict[str, Any]) -> float:
    w = ((cfg.get("stability_score") or {}).get("weights") or {})
    is_runner = _sf(((rec.get("in_sample") or {}).get("runner_rate")), 0.0)
    oos_runner = _sf(((rec.get("out_of_sample") or {}).get("runner_rate")), 0.0)
    is_fail = _sf(((rec.get("in_sample") or {}).get("fail_rate")), 100.0)
    oos_fail = _sf(((rec.get("out_of_sample") or {}).get("fail_rate")), 100.0)
    is_mr = _sf(((rec.get("in_sample") or {}).get("mfe_mae")), 0.0)
    oos_mr = _sf(((rec.get("out_of_sample") or {}).get("mfe_mae")), 0.0)
    n_is = int(((rec.get("in_sample") or {}).get("n")) or 0)
    n_oos = int(((rec.get("out_of_sample") or {}).get("n")) or 0)

    runner_ret = _clamp01(_safe_div(oos_runner, max(is_runner, 1.0)))
    fail_ctrl = _clamp01(1.0 - max(0.0, oos_fail - is_fail) / 25.0)
    mfe_ret = _clamp01(_safe_div(oos_mr, max(is_mr, 0.1)))
    oos_edge = _clamp01((oos_runner - baseline_runner + 20.0) / 40.0)
    sample = _clamp01(min(1.0, n_oos / max(25.0, n_is * 0.4)))

    score = (
        _sf(w.get("runner_retention"), 0.30) * runner_ret
        + _sf(w.get("fail_control"), 0.25) * fail_ctrl
        + _sf(w.get("mfe_mae_retention"), 0.20) * mfe_ret
        + _sf(w.get("oos_edge_vs_baseline"), 0.15) * oos_edge
        + _sf(w.get("sample_quality"), 0.10) * sample
    )
    return round(score, 4)


def _classify(rec: Dict[str, Any], baseline: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    cls = cfg.get("classification") or {}
    n_oos = int(((rec.get("out_of_sample") or {}).get("n")) or 0)
    oos_min = int((cfg.get("minimum_samples") or {}).get("oos_min_n", 20))
    if n_oos < oos_min:
        return "insufficient_oos_data"

    delta = rec.get("deltas") or {}
    is_s = rec.get("in_sample") or {}
    oos_s = rec.get("out_of_sample") or {}
    runner_drop = max(0.0, _sf(delta.get("runner_rate_delta_pp"), 0.0) * -1.0)
    fail_inc = max(0.0, _sf(delta.get("fail_rate_delta_pp"), 0.0))
    oos_vs_base_runner = _sf(oos_s.get("runner_rate"), 0.0) - _sf(baseline.get("runner_rate"), 0.0)
    mfe_drop = max(0.0, _sf(is_s.get("mfe_mae"), 0.0) - _sf(oos_s.get("mfe_mae"), 0.0))

    likely = cls.get("likely_overfit") or {}
    if (
        runner_drop >= _sf(likely.get("runner_drop_pp_min"), 20.0)
        or fail_inc >= _sf(likely.get("fail_increase_pp_min"), 12.0)
    ) and (
        abs(oos_vs_base_runner) <= _sf(likely.get("runner_to_baseline_distance_pp_max"), 3.0)
        or mfe_drop >= _sf(likely.get("mfe_mae_collapse_delta_min"), 0.35)
    ):
        return "likely_overfit"

    stable = cls.get("stable") or {}
    if (
        runner_drop <= _sf(stable.get("runner_drop_pp_max"), 8.0)
        and fail_inc <= _sf(stable.get("fail_increase_pp_max"), 6.0)
        and _sf(oos_s.get("mfe_mae"), 0.0) >= _sf(stable.get("min_oos_mfe_mae"), 1.05)
        and oos_vs_base_runner >= _sf(stable.get("min_oos_runner_vs_baseline_pp"), 6.0)
    ):
        return "stable_edge"

    acceptable = cls.get("acceptable") or {}
    if (
        runner_drop <= _sf(acceptable.get("runner_drop_pp_max"), 15.0)
        and fail_inc <= _sf(acceptable.get("fail_increase_pp_max"), 10.0)
        and oos_vs_base_runner >= _sf(acceptable.get("min_oos_runner_vs_baseline_pp"), 2.0)
    ):
        return "acceptable_degradation"

    if runner_drop > 0.0 or fail_inc > 0.0:
        return "fragile_edge"
    return "acceptable_degradation"


def compute_edge_validation_engine(
    findings_payload: Dict[str, Any],
    events_sample: List[Dict[str, Any]],
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    merged = _deep_merge(DEFAULT_EDGE_VALIDATION_CONFIG, cfg or {})
    if not merged.get("enabled", True):
        return {"enabled": False}

    rows = _normalize_events(events_sample or [], (findings_payload.get("config") or {}).get("reversal_decision_engine"))
    if not rows:
        return {"enabled": True, "message": "no reversal events available for validation", "validated_candidates": []}

    is_rows, oos_rows, split_meta = _split_rows(rows, merged)
    baseline_is = _compute_metrics(is_rows)
    baseline_oos = _compute_metrics(oos_rows)
    baseline_all = _compute_metrics(rows)

    parent_lookup = {
        (r.get("group_key") or {}).get("early_path_class"): r
        for r in (((findings_payload.get("reversal_decision_engine") or {}).get("tables") or {}).get("outcome_by_early_path_class") or [])
    }

    candidates = _build_candidates(findings_payload, merged)
    validated: List[Dict[str, Any]] = []
    for c in candidates:
        is_sub = _slice_by_filters(is_rows, c.get("filters") or {})
        oos_sub = _slice_by_filters(oos_rows, c.get("filters") or {})
        is_m = _compute_metrics(is_sub)
        oos_m = _compute_metrics(oos_sub)

        family_filters = {
            "trade_mode": (c.get("filters") or {}).get("trade_mode", "reverse"),
            "direction": (c.get("filters") or {}).get("direction", -1),
            "tf_minutes": (c.get("filters") or {}).get("tf_minutes", (c.get("filters") or {}).get("timeframe")),
            "candle_bucket": (c.get("filters") or {}).get("candle_bucket"),
        }
        family_filters = {k: v for k, v in family_filters.items() if v is not None}
        fam_oos = _compute_metrics(_slice_by_filters(oos_rows, family_filters))

        early_path = (c.get("filters") or {}).get("early_path_class")
        parent_baseline = parent_lookup.get(early_path) if early_path else None

        deltas = {
            "fail_rate_delta_pp": round(_sf(oos_m.get("fail_rate")) - _sf(is_m.get("fail_rate")), 2),
            "scalp_rate_delta_pp": round(_sf(oos_m.get("scalp_rate")) - _sf(is_m.get("scalp_rate")), 2),
            "expansion_rate_delta_pp": round(_sf(oos_m.get("expansion_rate")) - _sf(is_m.get("expansion_rate")), 2),
            "runner_rate_delta_pp": round(_sf(oos_m.get("runner_rate")) - _sf(is_m.get("runner_rate")), 2),
            "mfe_delta": round(_sf(oos_m.get("avg_mfe")) - _sf(is_m.get("avg_mfe")), 3),
            "mae_delta": round(_sf(oos_m.get("avg_mae")) - _sf(is_m.get("avg_mae")), 3),
            "mfe_mae_delta": round(_sf(oos_m.get("mfe_mae")) - _sf(is_m.get("mfe_mae")), 4),
            "sample_ratio_oos_to_is": round(_safe_div(_sf(oos_m.get("n")), max(1.0, _sf(is_m.get("n")))), 3),
            "runner_degradation_pct": round(_safe_div(_sf(oos_m.get("runner_rate")) - _sf(is_m.get("runner_rate"), 1.0), max(1.0, _sf(is_m.get("runner_rate"), 1.0))) * 100.0, 2),
            "fail_degradation_pct": round(_safe_div(_sf(oos_m.get("fail_rate")) - _sf(is_m.get("fail_rate"), 1.0), max(1.0, _sf(is_m.get("fail_rate"), 1.0))) * 100.0, 2),
        }

        rec = {
            "candidate_name": c.get("candidate_name"),
            "source_type": c.get("source_type"),
            "parent_branch": c.get("parent_branch"),
            "filters": c.get("filters"),
            "in_sample": is_m,
            "out_of_sample": oos_m,
            "deltas": deltas,
            "baselines": {
                "overall_reversal_is": baseline_is,
                "overall_reversal_oos": baseline_oos,
                "family_oos": fam_oos,
                "decision_parent": parent_baseline,
            },
            "period_stability": _multi_period(
                _slice_by_filters(rows, c.get("filters") or {}),
                int((((merged.get("split") or {}).get("multi_period") or {}).get("periods", 3))),
            ),
        }
        rec["stability_score"] = _stability_score(rec, _sf(baseline_oos.get("runner_rate"), 0.0), merged)
        rec["validation_label"] = _classify(rec, baseline_oos, merged)
        validated.append(rec)

    validated.sort(key=lambda r: (-_sf(r.get("stability_score"), 0.0), -int(_sf((r.get("out_of_sample") or {}).get("n"), 0.0))))

    stable = [r for r in validated if r.get("validation_label") == "stable_edge"]
    acceptable = [r for r in validated if r.get("validation_label") == "acceptable_degradation"]
    fragile = [r for r in validated if r.get("validation_label") == "fragile_edge"]
    overfit = [r for r in validated if r.get("validation_label") == "likely_overfit"]

    handoff: List[Dict[str, Any]] = []
    for rec in (stable + acceptable)[: int((merged.get("handoff") or {}).get("max_candidates", 8))]:
        oos = rec.get("out_of_sample") or {}
        is_m = rec.get("in_sample") or {}
        label = rec.get("validation_label")
        recommendation = "paper_test_ready" if label == "stable_edge" else "monitor_further"
        handoff.append({
            "candidate": rec.get("candidate_name"),
            "branch_definition": rec.get("filters"),
            "why_it_survived": (
                f"OOS runner {oos.get('runner_rate', 0):.1f}% vs IS {is_m.get('runner_rate', 0):.1f}%, "
                f"OOS fail {oos.get('fail_rate', 0):.1f}% vs IS {is_m.get('fail_rate', 0):.1f}%, "
                f"stability_score={rec.get('stability_score', 0):.3f}"
            ),
            "is_vs_oos": {
                "runner_delta_pp": rec.get("deltas", {}).get("runner_rate_delta_pp"),
                "fail_delta_pp": rec.get("deltas", {}).get("fail_rate_delta_pp"),
                "mfe_mae_delta": rec.get("deltas", {}).get("mfe_mae_delta"),
            },
            "practical_recommendation": recommendation,
        })

    questions = {
        "which_elite_setups_remain_better_in_oos": [r.get("candidate_name") for r in stable if r.get("source_type") == "elite_setup"][:5],
        "which_recursive_branches_degrade_most": [r.get("candidate_name") for r in sorted([x for x in validated if x.get("source_type") == "recursive_branch"], key=lambda x: _sf((x.get("deltas") or {}).get("runner_rate_delta_pp")) )[:5]],
        "runner_first_candidates_still_runner_first": [r.get("candidate_name") for r in validated if _sf((r.get("out_of_sample") or {}).get("runner_rate"), 0.0) >= 50.0][:5],
        "low_failure_in_oos": [r.get("candidate_name") for r in validated if _sf((r.get("out_of_sample") or {}).get("fail_rate"), 100.0) <= 8.0][:5],
        "strong_mfe_mae_in_oos": [r.get("candidate_name") for r in validated if _sf((r.get("out_of_sample") or {}).get("mfe_mae"), 0.0) >= 1.25][:5],
        "elite_but_collapsed_oos": [r.get("candidate_name") for r in overfit if r.get("source_type") in ("elite_setup", "decision_rule")][:5],
        "explosive_start_survival_oos": any((r.get("filters") or {}).get("early_path_class") == "explosive_start" and r.get("validation_label") in ("stable_edge", "acceptable_degradation") for r in validated),
        "orderly_start_survival_oos": any((r.get("filters") or {}).get("early_path_class") == "orderly_start" and r.get("validation_label") in ("stable_edge", "acceptable_degradation") for r in validated),
        "session_specific_survival_oos": [r.get("candidate_name") for r in stable if "session" in (r.get("filters") or {})][:5],
        "paper_test_candidates": [h.get("candidate") for h in handoff if h.get("practical_recommendation") == "paper_test_ready"][:5],
    }

    return {
        "enabled": True,
        "validation_configuration": {
            "split": split_meta,
            "minimum_samples": merged.get("minimum_samples"),
            "classification_thresholds": merged.get("classification"),
            "rolling_validation": _rolling_stub(rows, merged),
        },
        "candidate_summary": [
            {
                "candidate_name": r.get("candidate_name"),
                "source_type": r.get("source_type"),
                "parent_branch": r.get("parent_branch"),
                "is_n": (r.get("in_sample") or {}).get("n", 0),
                "oos_n": (r.get("out_of_sample") or {}).get("n", 0),
            }
            for r in validated
        ],
        "overall_baseline": {
            "in_sample": baseline_is,
            "out_of_sample": baseline_oos,
            "all_period": baseline_all,
        },
        "validated_candidates": validated,
        "stable_candidates": stable,
        "degrading_candidates": acceptable + fragile,
        "likely_overfit_candidates": overfit,
        "insufficient_oos_candidates": [r for r in validated if r.get("validation_label") == "insufficient_oos_data"],
        "validation_leaderboard": validated[: int((merged.get("leaderboard") or {}).get("top_n", 12))],
        "research_conclusions": {
            "survived": [r.get("candidate_name") for r in stable],
            "degraded": [r.get("candidate_name") for r in acceptable + fragile],
            "likely_overfit": [r.get("candidate_name") for r in overfit],
            "paper_test_next": [h.get("candidate") for h in handoff if h.get("practical_recommendation") == "paper_test_ready"],
            "discard": [r.get("candidate_name") for r in overfit],
        },
        "strategy_handoff": handoff,
        "validation_questions": questions,
    }
