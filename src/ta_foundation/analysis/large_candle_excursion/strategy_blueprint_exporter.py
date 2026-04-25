from __future__ import annotations

"""
Strategy Blueprint Exporter
===========================
Transforms findings_payload + events_sample + upstream detector config into
fully-numeric strategy blueprints — one per accepted candidate.

Each blueprint is a JSON-safe dict whose every leaf is a scalar or list of
scalars.  A strategy template (NinjaTrader or otherwise) can load the blueprint
and map every field 1:1 to a strategy parameter, swapping candidates by
swapping blueprint files.

Primary source of candidates
----------------------------
regime_discovery.onset_path_interaction_analysis.tradeable_regime_candidate_ledger
    — provides (onset_condition × early_path_condition) candidates with
      metrics (N, WR, runner, fail, decay, etc.) and recommended action.

Cross-referenced enrichment
---------------------------
strategy_construction_engine.constructed_strategies
    — optional: friction_viability, archetype, deployment_bucket, stop hints.
edge_validation_engine.validated_candidates
    — optional: validation_label, oos_metrics.
reversal_decision_engine
    — numeric early-path thresholds (midpoint_reclaim_bars, fav_2bar_pct, ...).
events_sample
    — per-event (lookback, basis, threshold_value) allowing sweep-dominant
      onset-parameter selection per candidate.
upstream_config (the large_candle_excursion block of YAML)
    — default detector params (candle_size, atr_period, direction, etc.).
regime_discovery.config.onset_detection
    — default onset-context gates (vwap_stretch, volume_multiple, streak, ...).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import math


DEFAULT_BLUEPRINT_EXPORTER_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "max_blueprints": 10,
    # Candidate filter: ledger action must be one of these to export.
    "accepted_actions": ["hold", "scalp", "flip_watch"],
    # Minimum sample size on the candidate (ledger.n) required to export.
    "min_n": 60,
    # Minimum win rate %.  Set to 0 to disable.
    "min_win_rate_pct": 50.0,
    # Target percentages (% of signal candle size).  Single values so templates
    # can consume them directly; tune here if pivots should change.
    "targets_pct_of_signal_candle": {
        "scalp": 30,
        "expansion": 62,
        "runner": 125,
    },
    # Stop formula (mirrors strategy_construction_engine for parity).
    "stop": {
        "style": "signal_candle_extreme_with_cap",
        "avg_mae_multiple": 1.3,
        "min_stop_ticks": 20,
        "max_stop_ticks": 100,
        "atr_fallback_multiple": 1.2,
        "base_offset_ticks": 4,
    },
    # Exit management (trail).
    "exit": {
        "trail_style": "atr",
        "atr_trail_multiple": 1.5,
        "time_stop_minutes": 30,
    },
    # Risk & friction defaults (overridable via upstream friction config).
    "risk_and_friction": {
        "base_unit": 1,
        "max_add_ons": 1,
        "max_total_units": 2,
    },
    # Instrument metadata (NQ-first; override per deployment).
    "instrument": {
        "root": "NQ",
        "tick_size": 0.25,
        "tick_value_usd": 5.00,
    },
    # Post-entry "hold rule" catalog — mirrored into every blueprint so the
    # template sees a single rule shape per candidate.
    "hold_rule_catalog": {
        "midpoint_reclaim_yes": {
            "requires_midpoint_reclaim_within_bars": 2,
        },
        "midpoint_reclaim_no": {
            "requires_midpoint_reclaim_within_bars": 0,  # sentinel: must NOT reclaim
        },
        "rebreak_no": {
            "max_rebreak_within_bars": 0,
            "check_bars": 2,
        },
        "rebreak_yes": {
            "max_rebreak_within_bars": 1,
            "check_bars": 2,
        },
        "explosive_start": {
            "fav_2bar_pct_min": 45.0,
            "adv_2bar_pct_max": 20.0,
        },
        "orderly_start": {
            "fav_2bar_pct_min": 25.0,
            "adv_2bar_pct_max": 35.0,
        },
        "weak_start": {
            "fav_2bar_pct_max": 15.0,
            "adv_2bar_pct_min": 35.0,
        },
        "fav2bar_ge_35pct": {
            "fav_2bar_pct_min": 35.0,
        },
    },
}


# ---------------------------------------------------------------------------
# Direction policy per onset signature
# ---------------------------------------------------------------------------

_DIRECTION_POLICY_BY_ONSET: Dict[str, str] = {
    "first_large_after_failed_continuation": "counter_to_failed_continuation",
    "first_large_after_directional_run":     "counter_to_directional_run",
    "first_large_at_key_level_with_extended_vwap_stretch": "counter_to_level_rejection",
    "first_large_after_session_range_break": "continuation_of_range_break",
    "first_large_after_compression":         "continuation_of_compression_breakout",
    "unclassified":                          "signal_direction",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _sf(v: Any, default: Optional[float] = None) -> Optional[float]:
    if v is None:
        return default
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except Exception:
        return default


def _sf_nn(v: Any, default: float = 0.0) -> float:
    f = _sf(v, default)
    return default if f is None else f


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _round_n(v: Optional[float], digits: int = 2) -> Optional[float]:
    if v is None:
        return None
    try:
        return round(float(v), digits)
    except Exception:
        return None


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _slug(*parts: str) -> str:
    out = "_".join(p for p in parts if p)
    return out.replace(" ", "_").replace("/", "_").lower()


# ---------------------------------------------------------------------------
# Sweep-dominant (lookback, basis, threshold_value) per candidate
# ---------------------------------------------------------------------------

def _events_match_onset(ev: Dict[str, Any], onset_condition: str) -> bool:
    """Mirror the predicates used in regime_discovery._onset_signature_map.
    Uses event columns already present in events_sample.
    """
    if onset_condition == "first_large_after_failed_continuation":
        return bool(ev.get("prev_was_failed_continuation"))
    if onset_condition == "first_large_after_session_range_break":
        lvl = ev.get("nearest_level_type")
        interaction = ev.get("level_interaction_label") or ev.get("key_level_interaction")
        is_break = lvl in ("session_high", "session_low") and interaction in (
            "at_level", "approaching", "departing", "nearby"
        )
        return is_break and not bool(ev.get("prev_was_session_range_break"))
    if onset_condition == "first_large_after_directional_run":
        streak = _sf_nn(ev.get("direction_streak"), 0.0)
        return streak >= 3.0 and bool(ev.get("same_as_prev"))
    if onset_condition == "first_large_after_compression":
        density = _sf_nn(ev.get("prior_large_candle_density"), 0.0)
        rvs = _sf_nn(ev.get("range_vs_avg_range"), 0.0)
        return density <= 1.0 and rvs >= 1.5
    if onset_condition == "first_large_at_key_level_with_extended_vwap_stretch":
        interaction = ev.get("level_interaction_label") or ev.get("key_level_interaction")
        return interaction in ("at_level", "approaching") and _sf_nn(ev.get("dist_vwap_atr"), 0.0) >= 0.75
    return True  # unknown onset: do not gate


def _early_path_matches(ev: Dict[str, Any], early_path_condition: str, size_ticks: float) -> bool:
    epc = str(early_path_condition or "").lower()
    fav2 = _sf_nn(ev.get("early_fav_2bar_ticks"), 0.0)
    adv2 = _sf_nn(ev.get("early_adv_2bar_ticks"), 0.0)
    fav2_pct = 100.0 * fav2 / size_ticks if size_ticks else 0.0
    adv2_pct = 100.0 * adv2 / size_ticks if size_ticks else 0.0

    if epc in ("midpoint_reclaim_yes",):
        return bool(ev.get("did_price_reclaim_signal_midpoint"))
    if epc in ("midpoint_reclaim_no",):
        return not bool(ev.get("did_price_reclaim_signal_midpoint"))
    if epc in ("rebreak_no",):
        return not bool(ev.get("did_price_break_signal_extreme_again"))
    if epc in ("rebreak_yes",):
        return bool(ev.get("did_price_break_signal_extreme_again"))
    if epc == "explosive_start":
        return fav2_pct >= 45.0 and adv2_pct <= 20.0
    if epc == "orderly_start":
        return fav2_pct >= 25.0 and adv2_pct <= 35.0
    if epc == "weak_start":
        return fav2_pct <= 15.0 and adv2_pct >= 35.0
    if epc.startswith("fav2bar_ge_"):
        # e.g. "fav2bar_ge_35pct"
        try:
            n = float(epc.replace("fav2bar_ge_", "").replace("pct", ""))
            return fav2_pct >= n
        except Exception:
            return True
    # Unknown early-path condition: do not filter.
    return True


def _sweep_dominant_for_candidate(
    events_sample: List[Dict[str, Any]],
    onset_condition: str,
    early_path_condition: str,
) -> Optional[Dict[str, Any]]:
    """Pick the (lookback, basis, threshold_value) combo with the highest
    number of matched events for this candidate.  Returns None if no combo
    has at least 5 matches."""
    counts: Dict[Tuple[int, str, float], int] = {}
    for ev in events_sample or []:
        lookback = ev.get("lookback")
        basis = ev.get("basis")
        tv = ev.get("threshold_value")
        if lookback is None or basis is None or tv is None:
            continue
        size = _sf_nn(ev.get("size_ticks"), 0.0)
        if size <= 0:
            continue
        if not _events_match_onset(ev, onset_condition):
            continue
        if not _early_path_matches(ev, early_path_condition, size):
            continue
        try:
            key = (int(lookback), str(basis), float(tv))
        except Exception:
            continue
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    best = max(counts.items(), key=lambda kv: (kv[1], kv[0][0], -kv[0][2]))
    (lookback, basis, tv), n = best
    if n < 5:
        return None
    return {
        "lookback_bars": lookback,
        "basis": basis,
        "threshold_mode": "multiplier",
        "threshold_value": round(tv, 3),
        "n_events_matched": n,
    }


# ---------------------------------------------------------------------------
# Enrichment lookups
# ---------------------------------------------------------------------------

def _find_validated_candidate(
    validation_result: Dict[str, Any],
    onset: str,
    early_path: str,
) -> Optional[Dict[str, Any]]:
    for vc in (validation_result or {}).get("validated_candidates") or []:
        f = vc.get("filters") or {}
        if str(f.get("onset_condition")) == onset or str(f.get("early_path_class")) == early_path:
            return vc
    return None


def _find_constructed_strategy(
    construction_result: Dict[str, Any],
    onset: str,
    early_path: str,
) -> Optional[Dict[str, Any]]:
    for s in (construction_result or {}).get("constructed_strategies") or []:
        ctx = s.get("eligible_market_context") or {}
        sc = str(s.get("source_candidate") or "")
        if (onset in sc) or (early_path in sc) or str(ctx.get("early_path_class", "")) == early_path:
            return s
    return None


def _onset_detection_config(upstream_config: Dict[str, Any], regime_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Merge upstream candle_size + regime onset_detection into blueprint onset_detection shape."""
    candle_cfg = (upstream_config or {}).get("candle_size") or {}
    onset_cfg = (regime_cfg or {}).get("onset_detection") or {}
    lookbacks = candle_cfg.get("average_lookbacks") or [20]
    multipliers = candle_cfg.get("threshold_multipliers") or [1.5]
    default_lookback = int(lookbacks[-1])
    default_multiplier = float(multipliers[0])
    return {
        "default_lookback_bars": default_lookback,
        "all_lookbacks": [int(x) for x in lookbacks],
        "default_threshold_value": default_multiplier,
        "all_threshold_values": [float(x) for x in multipliers],
        "threshold_mode": str(candle_cfg.get("threshold_mode") or "multiplier"),
        "min_body_ticks": int((upstream_config or {}).get("min_body_ticks", 2)),
        "min_range_ticks": int((upstream_config or {}).get("min_range_ticks", 4)),
        "atr_period": int((upstream_config or {}).get("atr_period", 14)),
        "quiet_lookback_minutes": int(onset_cfg.get("quiet_lookback_minutes", 60)),
        "quiet_max_prior_signals": int(onset_cfg.get("quiet_max_prior_signals", 1)),
        "vwap_stretch_atr_min": float(onset_cfg.get("vwap_stretch_atr", 0.75)),
        "volume_multiple_min": float(onset_cfg.get("volume_multiple", 2.5)),
        "direction_streak_min": int(onset_cfg.get("direction_streak_min", 3)),
        "range_expansion_multiple_min": float(onset_cfg.get("range_expansion_multiple", 1.5)),
        # Default lowered from 3 → 1 to match the singular research definition
        # ("the previous signal had weak early behavior").  Higher counts should
        # only be emitted when the research explicitly validates them; see the
        # matching default in LargeCandleReversal.cs (FIX #4a).
        "failed_continuation_lookback_signals": int(onset_cfg.get("failed_continuation_lookback_signals", 1)),
    }


def _stop_ticks(
    avg_mae_ticks: Optional[float],
    stop_cfg: Dict[str, Any],
) -> Tuple[float, bool]:
    """Return (recommended_stop_ticks, capped_flag)."""
    mae_mult = float(stop_cfg.get("avg_mae_multiple", 1.3))
    min_stop = float(stop_cfg.get("min_stop_ticks", 20))
    max_stop = float(stop_cfg.get("max_stop_ticks", 100))
    raw = (_sf_nn(avg_mae_ticks, 20.0) * mae_mult) if avg_mae_ticks else 20.0
    raw = max(min_stop, raw)
    capped = raw > max_stop
    return _clip(raw, min_stop, max_stop), capped


def _hold_rule_from_early_path(
    early_path_condition: str,
    catalog: Dict[str, Any],
) -> Dict[str, Any]:
    key = str(early_path_condition or "").lower()
    if key in catalog:
        return {"name": key, **catalog[key]}
    # Fallback: treat unknown early-path conditions as "trust the onset".
    return {"name": key or "any", "note": "no catalog entry; treat as pass-through"}


# Legacy research labels → canonical labels expected by the NT8 template
# generator (which then maps them to C# AllowSession* properties).  Keep this
# map in sync with _RESEARCH_SESSION_ALIASES in
# ta_foundation.strategies.LargeCandleReversal.generate_nt8_template.
_CANONICAL_SESSION_ALIASES: Dict[str, str] = {
    "asia":               "asia",
    "early_london":       "early_london",
    "mid_london":         "mid_london",
    "london":             "asia",
    "london_ny_overlap":  "london_ny_overlap",
    "ny_pre":             "ny_pre_open",
    "ny_pre_open":        "ny_pre_open",
    "ny_open":            "ny_open",
    "mid_ny":             "mid_day",
    "mid_day":            "mid_day",
    "power_hour":         "power_hour",
    "after_hours":        "after_hours",
    "overnight":          "overnight",
}


def _canonicalise_session(label: str) -> Optional[str]:
    key = str(label or "").strip().lower()
    if not key or key in ("none", "unknown", "all"):
        return None
    return _CANONICAL_SESSION_ALIASES.get(key)


def _session_filter(dominant_session: Optional[str], concentration_pct: Optional[float], max_concentration: float = 75.0) -> Dict[str, Any]:
    canon = _canonicalise_session(dominant_session)
    if canon is None:
        return {
            "mode": "all_session",
            "allowed_sessions": [],
            "session_concentration_max_pct": max_concentration,
        }
    return {
        "mode": "allowlist",
        "allowed_sessions": [canon],
        "session_concentration_max_pct": max_concentration,
        "observed_session_concentration_pct": _round_n(concentration_pct, 1),
    }


def _on_fail_action(candidate_action: str) -> str:
    ca = str(candidate_action or "").lower()
    return {
        "hold": "exit_at_market",
        "scalp": "exit_at_scalp_target",
        "flip_watch": "exit_and_watch_for_flip",
    }.get(ca, "exit_at_market")


def _on_pass_action(candidate_action: str) -> str:
    ca = str(candidate_action or "").lower()
    return {
        "hold": "trail_for_runner",
        "scalp": "exit_at_scalp_target",
        "flip_watch": "exit_and_watch_for_flip",
    }.get(ca, "trail_for_runner")


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def _build_blueprint(
    ledger_row: Dict[str, Any],
    *,
    events_sample: List[Dict[str, Any]],
    upstream_config: Dict[str, Any],
    regime_cfg: Dict[str, Any],
    friction_cfg: Dict[str, Any],
    validation_result: Dict[str, Any],
    construction_result: Dict[str, Any],
    exporter_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    onset = str(ledger_row.get("onset_condition") or "unclassified")
    early_path = str(ledger_row.get("early_path_condition") or "any")
    action = str(ledger_row.get("candidate_action") or "").lower()
    n = int(_sf_nn(ledger_row.get("n"), 0.0))

    blueprint_id = _slug(onset, "x", early_path, str(upstream_config.get("timeframes", [1])[0] if upstream_config else 1) + "m")

    # Enrichment lookups.
    vc = _find_validated_candidate(validation_result, onset, early_path)
    cs = _find_constructed_strategy(construction_result, onset, early_path)
    oos = (vc or {}).get("out_of_sample") or {}

    # Sweep-dominant onset params (from events_sample).
    sweep_dominant = _sweep_dominant_for_candidate(events_sample, onset, early_path)

    # Upstream config knobs.
    od = _onset_detection_config(upstream_config, regime_cfg)

    # Stop calculation (mirror strategy_construction_engine).
    stop_cfg = exporter_cfg.get("stop") or {}
    avg_mae = oos.get("avg_mae")
    stop_ticks, stop_capped = _stop_ticks(avg_mae, stop_cfg)

    # Targets.
    targets = exporter_cfg.get("targets_pct_of_signal_candle") or {}
    exit_cfg = exporter_cfg.get("exit") or {}

    # Post-entry management — primary hold rule determined by early_path.
    primary_hold = _hold_rule_from_early_path(early_path, exporter_cfg.get("hold_rule_catalog") or {})
    # Full "any_of" list: include the three winning hold rules from the findings
    # so the template can test union-policy without needing a code change.
    union_rules = []
    catalog = exporter_cfg.get("hold_rule_catalog") or {}
    for k in ("midpoint_reclaim_yes", "rebreak_no", "explosive_start"):
        if k in catalog:
            union_rules.append({"name": k, **catalog[k]})

    # Risk & friction.
    rf_cfg = exporter_cfg.get("risk_and_friction") or {}
    commission = float((friction_cfg or {}).get("commission_per_trade", 4.18))
    slip = int((friction_cfg or {}).get("slippage_ticks_per_side", 1))
    buffer = int((friction_cfg or {}).get("additional_buffer_ticks", 0))

    # Warnings.
    warnings: List[str] = []
    if n < int(exporter_cfg.get("min_n", 60)):
        warnings.append(f"sample size below exporter min_n: n={n}")
    if stop_capped:
        warnings.append(f"stop demand exceeded cap ({stop_ticks:.0f} > {stop_cfg.get('max_stop_ticks')})")
    if _sf_nn(ledger_row.get("session_concentration_pct")) >= 70.0:
        warnings.append(f"high session concentration ({ledger_row.get('session_concentration_pct'):.1f}%)")
    if _sf_nn(ledger_row.get("time_split_stability"), 1.0) < 0.45:
        warnings.append(f"low time-split stability ({ledger_row.get('time_split_stability')})")
    if (vc or {}).get("validation_label") in ("fragile_edge", "likely_overfit", "insufficient_oos_data"):
        warnings.append(f"validation flagged {vc.get('validation_label')}")
    if oos.get("friction_viability") == "friction-risky":
        warnings.append("friction-risky after commissions/slippage")

    # Validation gates (drive whether the template should even run).
    validation_label = (vc or {}).get("validation_label") or ledger_row.get("confidence_label") or "unvalidated"
    min_oos_n = int((exporter_cfg.get("min_oos_n") or 20))

    blueprint: Dict[str, Any] = {
        "blueprint_id": blueprint_id,
        "blueprint_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),

        "provenance": {
            "candidate_name": ledger_row.get("candidate_name"),
            "onset_condition": onset,
            "early_path_condition": early_path,
            "source_type": "tradeable_regime_candidate",
            "n": n,
            "unique_days": int(_sf_nn(ledger_row.get("unique_days"), 0.0)),
            "dominant_session": ledger_row.get("dominant_session"),
            "session_concentration_pct": _round_n(ledger_row.get("session_concentration_pct"), 1),
            "win_rate_pct": _round_n(ledger_row.get("win_rate"), 1),
            "fail_rate_pct": _round_n(ledger_row.get("fail_rate"), 1),
            "runner_rate_pct": _round_n(ledger_row.get("runner_rate"), 1),
            "expectancy_ticks": _round_n(ledger_row.get("expectancy_ticks"), 2),
            "mfe_mae_ratio": _round_n(ledger_row.get("mfe_mae_ratio"), 2),
            "cluster_participation_pct": _round_n(ledger_row.get("cluster_participation_rate"), 1),
            "median_cluster_length": ledger_row.get("median_cluster_length"),
            "median_decay_minutes": _round_n(ledger_row.get("median_decay_minutes"), 1),
            "time_split_stability": _round_n(ledger_row.get("time_split_stability"), 2),
            "confidence_label": ledger_row.get("confidence_label"),
            "recommended_action": action,
            "validation_label": validation_label,
            "archetype": (cs or {}).get("archetype"),
            "deployment_bucket": (cs or {}).get("deployment_bucket"),
            "deployment_score": (cs or {}).get("deployment_score"),
            "friction_viability": oos.get("friction_viability"),
            "net_expectancy_after_friction_ticks": _round_n(oos.get("net_expectancy_after_friction_ticks"), 2),
            "avg_mae_ticks": _round_n(avg_mae, 1),
        },

        "instrument": dict(exporter_cfg.get("instrument") or {}),
        "timeframe_minutes": int(((upstream_config or {}).get("timeframes") or [1])[0]),
        "direction_policy": _DIRECTION_POLICY_BY_ONSET.get(onset, "signal_direction"),

        "onset_detection": {
            "candle_size": {
                "lookback_bars": od["default_lookback_bars"],
                "basis": (sweep_dominant or {}).get("basis", "range"),
                "threshold_mode": od["threshold_mode"],
                "threshold_value": od["default_threshold_value"],
                "min_body_ticks": od["min_body_ticks"],
                "min_range_ticks": od["min_range_ticks"],
                # candle_bucket (e.g. "50-75" ticks) comes from the regime
                # ledger row when available.  The NT8 template generator reads
                # it to pin MaxRangeTicks/MaxBodyTicks so templates trade only
                # the candle size range the research actually validated.
                "candle_bucket": ledger_row.get("candle_bucket"),
                "sweep_dominant": sweep_dominant,
                "all_lookbacks": od["all_lookbacks"],
                "all_threshold_values": od["all_threshold_values"],
            },
            "failed_continuation": {
                "lookback_signals": od["failed_continuation_lookback_signals"],
                "require_prior_rebreak": True,
            },
            "context_gates": {
                "vwap_stretch_atr_min": od["vwap_stretch_atr_min"],
                "volume_multiple_min": od["volume_multiple_min"],
                "direction_streak_min": od["direction_streak_min"],
                "range_expansion_multiple_min": od["range_expansion_multiple_min"],
            },
            "quiet_filter": {
                "lookback_minutes": od["quiet_lookback_minutes"],
                "max_prior_signals": od["quiet_max_prior_signals"],
            },
            "atr_period": od["atr_period"],
        },

        "entry_rule": {
            "trigger_event": "on_bar_close",
            "direction_policy": _DIRECTION_POLICY_BY_ONSET.get(onset, "signal_direction"),
            "fill_type": "market_on_next_open",
            "max_entries_per_session": 1,
        },

        "stop_rule": {
            "style": str(stop_cfg.get("style", "signal_candle_extreme_with_cap")),
            "base_offset_ticks": int(stop_cfg.get("base_offset_ticks", 4)),
            "max_stop_ticks": int(stop_cfg.get("max_stop_ticks", 100)),
            "atr_fallback_multiple": float(stop_cfg.get("atr_fallback_multiple", 1.2)),
            "recommended_stop_ticks": int(round(stop_ticks)),
            "capped": bool(stop_capped),
        },

        "post_entry_management": {
            "evaluation_bar": 2,
            "primary_hold_rule": primary_hold,
            "union_hold_rules": {
                "type": "any_of",
                "rules": union_rules,
            },
            "on_fail_action": _on_fail_action(action),
            "on_pass_action": _on_pass_action(action),
        },

        "exit_rule": {
            "scalp_target_pct_of_signal_candle": int(targets.get("scalp", 30)),
            "expansion_target_pct_of_signal_candle": int(targets.get("expansion", 62)),
            "runner_target_pct_of_signal_candle": int(targets.get("runner", 125)),
            "time_stop_minutes": int(exit_cfg.get("time_stop_minutes", 30)),
            "decay_warning_minutes": _round_n(ledger_row.get("median_decay_minutes"), 1),
            "trail_style": str(exit_cfg.get("trail_style", "atr")),
            "atr_trail_multiple": float(exit_cfg.get("atr_trail_multiple", 1.5)),
        },

        "session_filter": _session_filter(
            ledger_row.get("dominant_session"),
            _sf(ledger_row.get("session_concentration_pct")),
        ),

        "risk_and_friction": {
            "base_unit": int(rf_cfg.get("base_unit", 1)),
            "max_add_ons": int(rf_cfg.get("max_add_ons", 1)),
            "max_total_units": int(rf_cfg.get("max_total_units", 2)),
            "commission_per_trade_usd": commission,
            "slippage_ticks_per_side": slip,
            "additional_buffer_ticks": buffer,
        },

        "validation_gates": {
            "validation_label": validation_label,
            "min_oos_n": min_oos_n,
            "is_oos_ready": validation_label in ("stable_edge", "acceptable_degradation", "possibly_monitor_further", "paper_test_candidate"),
        },

        "warnings": warnings,
    }
    return blueprint


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_strategy_blueprint_exporter(
    findings_payload: Dict[str, Any],
    events_sample: List[Dict[str, Any]],
    upstream_config: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Produce machine-readable strategy blueprints from findings_payload.

    Parameters
    ----------
    findings_payload : dict emitted during the findings run so far.  Expected
                       keys (all optional — missing keys degrade gracefully):
                         - regime_discovery
                         - edge_validation_engine
                         - strategy_construction_engine
                         - reversal_decision_engine
                         - config (findings YAML block — used for friction)
    events_sample    : per-event records from the upstream large_candle_excursion
                       sweep (needed for sweep-dominant selection).
    upstream_config  : the upstream large_candle_excursion block of YAML.
                       Source of candle_size / atr_period / timeframes / etc.
    cfg              : exporter config (merged with defaults).

    Returns
    -------
    dict with keys:
      enabled, blueprints, diagnostics, exporter_config
    """
    merged = _deep_merge(DEFAULT_BLUEPRINT_EXPORTER_CONFIG, cfg or {})
    if not merged.get("enabled", True):
        return {"enabled": False, "blueprints": [], "diagnostics": {}, "exporter_config": merged}

    upstream_config = upstream_config or {}
    regime_result = (findings_payload or {}).get("regime_discovery") or {}
    regime_cfg = regime_result.get("config") or {}
    interactions = regime_result.get("onset_path_interaction_analysis") or {}
    ledger = interactions.get("tradeable_regime_candidate_ledger") or []

    validation_result = (findings_payload or {}).get("edge_validation_engine") or {}
    construction_result = (findings_payload or {}).get("strategy_construction_engine") or {}
    friction_cfg = ((findings_payload or {}).get("config") or {}).get("friction") or {}

    accepted_actions = set(str(a).lower() for a in merged.get("accepted_actions") or [])
    min_n = int(merged.get("min_n", 60))
    min_wr = float(merged.get("min_win_rate_pct", 0.0))

    blueprints: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for row in ledger:
        action = str(row.get("candidate_action") or "").lower()
        n = int(_sf_nn(row.get("n"), 0.0))
        wr = _sf_nn(row.get("win_rate"), 0.0)
        reason = None
        if accepted_actions and action not in accepted_actions:
            reason = f"action={action} not in accepted_actions"
        elif n < min_n:
            reason = f"n={n} below min_n={min_n}"
        elif wr < min_wr:
            reason = f"win_rate={wr:.1f}% below min_win_rate_pct={min_wr:.1f}%"
        if reason:
            skipped.append({
                "candidate_name": row.get("candidate_name"),
                "onset_condition": row.get("onset_condition"),
                "early_path_condition": row.get("early_path_condition"),
                "reason": reason,
            })
            continue
        blueprints.append(_build_blueprint(
            row,
            events_sample=events_sample or [],
            upstream_config=upstream_config,
            regime_cfg=regime_cfg,
            friction_cfg=friction_cfg,
            validation_result=validation_result,
            construction_result=construction_result,
            exporter_cfg=merged,
        ))
        if len(blueprints) >= int(merged.get("max_blueprints", 10)):
            break

    return {
        "enabled": True,
        "blueprints": blueprints,
        "diagnostics": {
            "n_ledger_rows": len(ledger),
            "n_blueprints": len(blueprints),
            "n_skipped": len(skipped),
            "skipped": skipped[:25],
            "events_sample_size": len(events_sample or []),
        },
        "exporter_config": merged,
    }


__all__ = [
    "DEFAULT_BLUEPRINT_EXPORTER_CONFIG",
    "compute_strategy_blueprint_exporter",
]
