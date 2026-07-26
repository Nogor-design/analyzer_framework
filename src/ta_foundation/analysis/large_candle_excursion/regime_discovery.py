from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
import math


DEFAULT_REGIME_DISCOVERY_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "forward_window_minutes": 30,
    "win_target_pct": 50.0,
    "min_events": 30,
    "max_rows": 5000,
    "cluster_detection": {
        "min_consecutive_wins": 2,
        "max_time_gap_minutes": 45,
        "min_expectancy_ticks": 0.0,
        "rolling_window_signals": 10,
        "rolling_density_threshold": 0.60,
    },
    "onset_detection": {
        "quiet_lookback_minutes": 60,
        "quiet_max_prior_signals": 1,
        "lookahead_minutes": 60,
        "lookahead_signals": 10,
        "min_n": 5,
        "vwap_stretch_atr": 0.75,
        "volume_multiple": 2.5,
        "direction_streak_min": 3,
        "range_expansion_multiple": 1.5,
        "failed_continuation_lookback_signals": 3,
        "follow_through_max_signals_to_cluster": 3,
        "follow_through_min_cluster_length": 2,
        "too_broad_coverage_pct": 65.0,
        "high_overlap_pct": 65.0,
        "exclusive_overlap_max_pct": 35.0,
    },
    "onset_path_interactions": {
        "min_n": 5,
        "strong_min_n": 20,
        "runner_reversal_pct": 100.0,
        "failure_reversal_pct": 25.0,
        "scalp_reversal_pct": 50.0,
        "expansion_reversal_pct": 50.0,
        "top_filter_n": 5,
        "matrix_top_n": 40,
        "stability_top_n": 20,
        "good_lift_pp": 5.0,
        "weak_lift_pp": 0.0,
        "good_expectancy_lift_ticks": 0.0,
        "clean_reclaim_min_cleanliness": 65.0,
        "hold_min_cleanliness": 72.0,
        "hold_min_win_rate": 55.0,
        "poor_follow_through_pct": 35.0,
        "fast_decay_minutes": 20.0,
        "failure_dominance_rate_pct": 55.0,
        "small_sample_n": 30,
        "session_concentration_warning_pct": 70.0,
        "time_split_count": 3,
        "time_instability_warning_pp": 12.0,
        "candidate_min_n": 30,
        "candidate_min_unique_days": 5,
        "hold_min_expectancy_ticks": 0.0,
        "hold_max_fail_rate": 35.0,
        "hold_min_cluster_participation": 25.0,
        "scalp_min_expectancy_ticks": 0.0,
        "flip_watch_min_failure_rate": 40.0,
        "shutdown_max_expectancy_ticks": 0.0,
        "shutdown_min_fail_rate": 45.0,
        "max_session_concentration_pct": 75.0,
        "min_time_split_stability": 0.45,
        "require_incremental_lift": True,
        "min_incremental_expectancy_lift_ticks": 0.0,
        "min_incremental_cluster_lift_pp": 3.0,
    },
    "trigger_windows": {
        "minute_windows": [10, 20, 30, 60],
        "signal_windows": [3, 5, 10],
        "min_anchors": 3,
        "vwap_stretch_atr": 0.75,
        "volume_multiple": 2.5,
    },
    "cleanliness": {
        "enabled": True,
        "target_pct": 50.0,
        "speed_full_credit_minutes": 5.0,
        "speed_zero_credit_minutes": 30.0,
        "weights": {
            "mae": 0.30,
            "speed": 0.20,
            "smoothness": 0.20,
            "rebreak_avoidance": 0.20,
            "flip_independence": 0.10,
        },
    },
    "failure_to_flip": {
        "failure_max_reversal_pct": 25.0,
        "flip_min_continuation_pct": 50.0,
        "count_rebreak_as_flip": False,
        "min_n": 5,
    },
    "decay_detection": {
        "rolling_window_signals": 5,
        "win_density_drop_below": 0.50,
        "mae_rise_multiple": 1.35,
        "early_favorable_drop_ratio": 0.70,
        "cleanliness_drop_points": 15.0,
        "expectancy_drop_ticks": 0.0,
        "slow_favorable_minutes": 20.0,
        "failed_reversals_allowed": 1,
        "min_signature_n": 2,
    },
}


def compute_regime_discovery(
    events_sample: List[Dict[str, Any]],
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    merged = _deep_merge(DEFAULT_REGIME_DISCOVERY_CONFIG, cfg or {})
    if not merged.get("enabled", False):
        return {"enabled": False}
    if not events_sample:
        return {"enabled": True, "message": "no events in sample", "n_events": 0, "config": merged}

    rows, diagnostics = _event_rows(events_sample, merged)
    if not rows:
        return {
            "enabled": True,
            "message": "no valid regime-discovery events",
            "n_events": 0,
            "config": merged,
            "diagnostics": diagnostics,
        }

    _score_cleanliness(rows, merged.get("cleanliness") or {})
    _add_cluster_state_features(rows, merged)
    _assign_onset_signatures(rows, merged.get("onset_detection") or {})

    clusters = detect_win_clusters(rows, merged.get("cluster_detection") or {})
    onset = detect_regime_onsets(rows, clusters, merged.get("onset_detection") or {})
    anchors = analyze_trigger_anchored_windows(rows, merged.get("trigger_windows") or {})
    failure_flip = analyze_failure_to_flip(rows, merged.get("failure_to_flip") or {})
    decay = detect_regime_decay(rows, clusters, merged.get("decay_detection") or {})
    interactions = analyze_onset_path_interactions(
        rows,
        clusters,
        onset,
        decay,
        failure_flip,
        merged.get("onset_path_interactions") or {},
    )

    return {
        "enabled": True,
        "n_events": len(rows),
        "config": merged,
        "diagnostics": diagnostics,
        "event_feature_sample": rows[:25],
        "cleanliness_summary": _cleanliness_summary(rows),
        "cleanliness_definition": _cleanliness_definition(merged.get("cleanliness") or {}),
        "win_cluster_analysis": clusters,
        "regime_onset_detector": onset,
        "trigger_anchored_window_analysis": anchors,
        "onset_path_interaction_analysis": interactions,
        "failure_to_flip_transition_report": failure_flip,
        "regime_decay_detector": decay,
        "decision_summary_cards": _build_decision_summary_cards(onset, failure_flip, decay, interactions),
    }


def detect_win_clusters(rows: List[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or {}
    min_wins = max(1, int(cfg.get("min_consecutive_wins", 2)))
    max_gap = float(cfg.get("max_time_gap_minutes", 45))
    min_exp = float(cfg.get("min_expectancy_ticks", 0.0))
    rolling_n = max(1, int(cfg.get("rolling_window_signals", 10)))

    clusters: List[Dict[str, Any]] = []
    cur: List[Dict[str, Any]] = []

    def _flush() -> None:
        nonlocal cur
        if len(cur) >= min_wins:
            exp = _avg([r.get("expectancy_ticks") for r in cur])
            if exp is not None and exp >= min_exp:
                clusters.append(_cluster_payload(cur))
        cur = []

    prev: Optional[Dict[str, Any]] = None
    for row in rows:
        gap_ok = True
        if prev is not None:
            gap = _minutes_between(prev.get("_dt"), row.get("_dt"))
            gap_ok = gap is not None and gap <= max_gap
        if row.get("is_reversal_win") and gap_ok:
            cur.append(row)
        else:
            _flush()
            cur = [row] if row.get("is_reversal_win") else []
        prev = row
    _flush()

    rolling = []
    for i, row in enumerate(rows):
        win = rows[max(0, i - rolling_n + 1): i + 1]
        rolling.append({
            "dt": row.get("dt"),
            "rolling_signals": len(win),
            "rolling_win_density": round(sum(1 for r in win if r.get("is_reversal_win")) / len(win), 4) if win else None,
            "rolling_expectancy_ticks": _round(_avg([r.get("expectancy_ticks") for r in win]), 3),
        })

    gaps = []
    for a, b in zip(clusters, clusters[1:]):
        gaps.append(_minutes_between(_parse_dt(a.get("end_dt")), _parse_dt(b.get("start_dt"))))
    for cluster_i, cluster in enumerate(clusters, start=1):
        start_idx = int(cluster.get("start_index") or 0)
        end_idx = int(cluster.get("end_index") or 0)
        decay_row = next((r for r in rows[end_idx + 1:] if not r.get("is_reversal_win")), None)
        if decay_row is not None:
            cluster["time_to_decay_minutes"] = _round(_minutes_between(rows[start_idx].get("_dt"), decay_row.get("_dt")), 2)
        for i in range(start_idx, end_idx + 1):
            if 0 <= i < len(rows):
                rows[i]["inside_win_cluster"] = True
                rows[i]["cluster_id"] = cluster_i
                rows[i]["cluster_position"] = i - start_idx + 1
                rows[i]["cluster_length"] = int(cluster.get("length") or 0)
                rows[i]["minutes_since_cluster_start"] = _round(_minutes_between(rows[start_idx].get("_dt"), rows[i].get("_dt")), 2)
                rows[i]["cluster_time_to_decay_minutes"] = cluster.get("time_to_decay_minutes")

    lengths = [int(c.get("length") or 0) for c in clusters]
    durations = [float(c.get("duration_minutes") or 0) for c in clusters]
    clustered_signals = sum(1 for r in rows if r.get("inside_win_cluster"))
    total_wins = sum(1 for r in rows if r.get("is_reversal_win"))
    clustered_wins = sum(1 for r in rows if r.get("is_reversal_win") and r.get("inside_win_cluster"))
    return {
        "enabled": True,
        "cluster_count": len(clusters),
        "clusters": clusters,
        "longest_streaks": clusters[:10],
        "median_cluster_length": _round(_median(lengths), 2),
        "avg_cluster_duration_minutes": _round(_avg(durations), 2),
        "median_gap_between_clusters_minutes": _round(_median([g for g in gaps if g is not None]), 2),
        "percent_wins_inside_clusters": _round(100.0 * clustered_wins / total_wins, 1) if total_wins else 0.0,
        "percent_signals_inside_clusters": _round(100.0 * clustered_signals / len(rows), 1) if rows else 0.0,
        "summary_by_onset_condition": _cluster_summary_by_onset(clusters),
        "rolling_profile_sample": rolling[:250],
        "config": cfg,
    }


def detect_regime_onsets(
    rows: List[Dict[str, Any]],
    cluster_result: Dict[str, Any],
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = cfg or {}
    min_n = int(cfg.get("min_n", 5))
    lookahead_minutes = float(cfg.get("lookahead_minutes", 60))
    lookahead_signals = int(cfg.get("lookahead_signals", 10))
    follow_max_signals = int(cfg.get("follow_through_max_signals_to_cluster", 3))
    follow_min_len = int(cfg.get("follow_through_min_cluster_length", 2))
    too_broad_pct = float(cfg.get("too_broad_coverage_pct", 65.0))
    high_overlap_pct = float(cfg.get("high_overlap_pct", 65.0))
    exclusive_overlap_max = float(cfg.get("exclusive_overlap_max_pct", 35.0))
    cluster_starts = {_cluster_row_index(c) for c in cluster_result.get("clusters") or []}
    clusters = cluster_result.get("clusters") or []
    baseline_wr = _win_rate(rows)
    baseline_exp = _avg([r.get("expectancy_ticks") for r in rows]) or 0.0

    candidates = _onset_condition_map(cfg)
    summaries: List[Dict[str, Any]] = []
    for label, pred in candidates:
        hits: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
        for i, row in enumerate(rows):
            if not pred(row):
                continue
            future = _future_rows(rows, i, max_minutes=lookahead_minutes, max_signals=lookahead_signals)
            if future:
                hits.append((row, future))
        if len(hits) < min_n:
            continue

        future_rows = [r for _, fut in hits for r in fut]
        matched_cluster_lengths = []
        matched_cluster_durations = []
        matched_decay_times = []
        matched_cluster_cleanliness = []
        matched_cluster_expectancy = []
        signals_to_cluster = []
        first_persistence_values = []
        for row, _ in hits:
            idx = int(row.get("_row_index") or 0)
            next_cluster = _first_cluster_in_window(
                clusters,
                rows,
                idx,
                max_signals=follow_max_signals,
                max_minutes=lookahead_minutes,
                min_length=follow_min_len,
            )
            if next_cluster is not None:
                matched_cluster_lengths.append(next_cluster.get("length"))
                matched_cluster_durations.append(next_cluster.get("duration_minutes"))
                matched_decay_times.append(next_cluster.get("time_to_decay_minutes"))
                matched_cluster_cleanliness.append(next_cluster.get("avg_cleanliness"))
                matched_cluster_expectancy.append(next_cluster.get("expectancy_ticks"))
                signals_to_cluster.append(max(0, int(next_cluster.get("start_index") or idx) - idx))
            first_persistence_values.append(_persistence_after_onset(rows, idx, max_signals=lookahead_signals))
        coverage = 100.0 * len(hits) / len(rows) if rows else 0.0
        overlaps = []
        for row, _ in hits:
            sigs = [s for s in (row.get("onset_signatures") or []) if s != label]
            if sigs:
                overlaps.append(row)
        overlap_pct = 100.0 * len(overlaps) / len(hits) if hits else 0.0
        exclusive_pct = 100.0 - overlap_pct
        high_coverage = coverage >= too_broad_pct
        high_overlap = overlap_pct >= high_overlap_pct
        low_exclusivity = exclusive_pct <= exclusive_overlap_max
        too_broad = high_coverage or high_overlap or low_exclusivity

        summaries.append({
            "onset_condition": label,
            "n": len(hits),
            "coverage_pct": _round(coverage, 1),
            "overlap_pct": _round(overlap_pct, 1),
            "exclusivity_pct": _round(exclusive_pct, 1),
            "high_coverage": bool(high_coverage),
            "high_overlap": bool(high_overlap),
            "low_exclusivity": bool(low_exclusivity),
            "too_broad_to_be_actionable": bool(too_broad),
            "post_onset_win_rate": _round(_win_rate(future_rows), 1),
            "post_onset_expectancy_ticks": _round(_avg([r.get("expectancy_ticks") for r in future_rows]), 3),
            "win_rate_lift_vs_baseline_pp": _round(_win_rate(future_rows) - baseline_wr, 1),
            "expectancy_lift_vs_baseline_ticks": _round((_avg([r.get("expectancy_ticks") for r in future_rows]) or 0.0) - baseline_exp, 3),
            "avg_cluster_length": _round(_avg(matched_cluster_lengths), 2),
            "avg_cluster_duration_minutes": _round(_avg(matched_cluster_durations), 2),
            "avg_time_to_decay_minutes": _round(_avg(matched_decay_times), 2),
            "median_cluster_length": _round(_median(matched_cluster_lengths), 2),
            "median_cluster_duration_minutes": _round(_median(matched_cluster_durations), 2),
            "median_cluster_cleanliness": _round(_median(matched_cluster_cleanliness), 2),
            "median_cluster_expectancy_ticks": _round(_median(matched_cluster_expectancy), 3),
            "cluster_follow_through_rate": _round(100.0 * len(matched_cluster_lengths) / len(hits), 1) if hits else 0.0,
            "median_signals_to_cluster": _round(_median(signals_to_cluster), 2),
            "cluster_persistence_after_first_onset": _round(first_persistence_values[0], 2) if first_persistence_values else None,
            "median_first_persistence": _round(_median(first_persistence_values), 2),
            "cluster_start_hits": sum(1 for row, _ in hits if int(row.get("_row_index") or -1) in cluster_starts),
            "background_state": _signature_metadata(label).get("background_state"),
            "trigger_event": _signature_metadata(label).get("trigger_event"),
            "early_validation": _signature_metadata(label).get("early_validation"),
            "diagnostics": {
                "follow_through_denominator": len(hits),
                "follow_through_numerator": len(matched_cluster_lengths),
                "follow_through_max_signals_to_cluster": follow_max_signals,
                "follow_through_min_cluster_length": follow_min_len,
                "lookahead_minutes": lookahead_minutes,
                "lookahead_signals": lookahead_signals,
            },
        })

    summaries.sort(key=lambda r: (-_sf(r.get("post_onset_expectancy_ticks")), -_sf(r.get("post_onset_win_rate")), -int(r.get("n") or 0)))
    return {
        "enabled": True,
        "conditions": summaries,
        "selectivity_interpretation": _onset_selectivity_interpretation(summaries),
        "config": cfg,
    }


def analyze_onset_path_interactions(
    rows: List[Dict[str, Any]],
    cluster_result: Dict[str, Any],
    onset_result: Dict[str, Any],
    decay_result: Dict[str, Any],
    failure_flip_result: Dict[str, Any],
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = cfg or {}
    min_n = int(cfg.get("min_n", 5))
    runner_pct = float(cfg.get("runner_reversal_pct", 100.0))
    failure_pct = float(cfg.get("failure_reversal_pct", 25.0))
    top_n = int(cfg.get("top_filter_n", 5))
    clusters = cluster_result.get("clusters") or []
    onset_by_label = {str(r.get("onset_condition")): r for r in onset_result.get("conditions") or []}
    decay_driver_by_onset = _top_decay_driver_by_onset(decay_result.get("decay_drivers_by_onset") or [])
    baseline_wins = sum(1 for r in rows if r.get("is_reversal_win"))

    interactions: List[Dict[str, Any]] = []
    for onset_label, onset_row in onset_by_label.items():
        onset_rows = [r for r in rows if onset_label in (r.get("onset_signatures") or [])]
        if not onset_rows:
            continue
        onset_wr = _win_rate(onset_rows)
        onset_exp = _avg([r.get("expectancy_ticks") for r in onset_rows]) or 0.0
        onset_clean = _avg([r.get("cleanliness_score") for r in onset_rows]) or 0.0

        for path_label, pred in _early_path_interaction_conditions():
            matched = [r for r in onset_rows if pred(r)]
            if len(matched) < min_n:
                continue
            persistence_values = [
                _persistence_after_onset(rows, int(r.get("_row_index") or 0), max_signals=10)
                for r in matched
            ]
            cluster_lengths = []
            decay_times = []
            for r in matched:
                idx = int(r.get("_row_index") or 0)
                cluster = _first_cluster_in_window(clusters, rows, idx, max_signals=3, max_minutes=60.0, min_length=2)
                if cluster is not None:
                    cluster_lengths.append(cluster.get("length"))
                    decay_times.append(cluster.get("time_to_decay_minutes"))
            wr = _win_rate(matched)
            exp = _avg([r.get("expectancy_ticks") for r in matched])
            clean = _avg([r.get("cleanliness_score") for r in matched])
            win_lift = wr - onset_wr
            exp_lift = (exp or 0.0) - onset_exp
            clean_lift = (clean or 0.0) - onset_clean
            action, reasons = _operational_action_for_interaction(
                {
                    "win_rate": wr,
                    "expectancy_ticks": exp,
                    "avg_cleanliness": clean,
                    "win_rate_lift_vs_onset_pp": win_lift,
                    "expectancy_lift_vs_onset_ticks": exp_lift,
                    "failure_rate": 100.0 * sum(1 for r in matched if _sf(r.get("reversal_mfe_pct")) <= failure_pct) / len(matched),
                    "cluster_persistence": _median(persistence_values),
                    "median_time_to_decay_minutes": _median(decay_times),
                    "path_condition": path_label,
                    "onset": onset_row,
                    "failure_flip": failure_flip_result,
                },
                cfg,
            )
            interactions.append({
                "onset_condition": onset_label,
                "early_path_condition": path_label,
                "n": len(matched),
                "win_rate": _round(wr, 1),
                "expectancy_ticks": _round(exp, 3),
                "avg_cleanliness": _round(clean, 2),
                "runner_rate": _round(100.0 * sum(1 for r in matched if _sf(r.get("reversal_mfe_pct")) >= runner_pct) / len(matched), 1),
                "failure_rate": _round(100.0 * sum(1 for r in matched if _sf(r.get("reversal_mfe_pct")) <= failure_pct) / len(matched), 1),
                "cluster_persistence": _round(_median(persistence_values), 2),
                "median_cluster_length": _round(_median(cluster_lengths), 2),
                "median_time_to_decay_minutes": _round(_median(decay_times), 2),
                "top_decay_driver": decay_driver_by_onset.get(onset_label),
                "win_rate_lift_vs_onset_pp": _round(win_lift, 1),
                "expectancy_lift_vs_onset_ticks": _round(exp_lift, 3),
                "cleanliness_lift_vs_onset": _round(clean_lift, 2),
                "wins_captured": sum(1 for r in matched if r.get("is_reversal_win")),
                "signals_captured": len(matched),
                "matched_row_indexes": [int(r.get("_row_index") or 0) for r in matched],
                "operational_action": action,
                "action_reason": ", ".join(reasons),
            })

    interactions.sort(
        key=lambda r: (
            _action_rank(str(r.get("operational_action"))),
            -_sf(r.get("expectancy_ticks")),
            -_sf(r.get("win_rate")),
            -int(r.get("n") or 0),
        )
    )
    top = interactions[:top_n]
    top_indexes = sorted({
        int(idx)
        for r in top
        for idx in (r.get("matched_row_indexes") or [])
    })
    top_wins = sum(1 for idx in top_indexes if 0 <= idx < len(rows) and rows[idx].get("is_reversal_win"))
    top_signals = len(top_indexes)
    interaction_matrix = _build_interaction_matrix(rows, clusters, interactions, cfg)
    incremental_lift = _build_incremental_lift_table(rows, clusters, interaction_matrix, cfg)
    ignition_table = _build_regime_ignition_table(rows, clusters, interaction_matrix, cfg)
    strongest = _strong_interaction_keys(ignition_table, interaction_matrix, cfg)
    decay_table = _build_regime_decay_table(rows, strongest, cfg)
    first_follow = _build_first_signal_follow_on_table(rows, strongest, cfg)
    stability_table = _build_interaction_stability_table(rows, interaction_matrix, onset_by_label, cfg)
    edge_decomposition = _build_edge_decomposition_table(incremental_lift)
    candidate_ledger = _build_tradeable_regime_candidate_ledger(
        interaction_matrix,
        incremental_lift,
        ignition_table,
        stability_table,
        edge_decomposition,
        cfg,
    )
    action_tables = _build_action_decision_tables(candidate_ledger, decay_table, first_follow, cfg)
    readiness = _build_live_decision_readiness_screen(candidate_ledger)
    failure_audit = _build_failure_mode_audit(candidate_ledger, interaction_matrix, stability_table, cfg)
    executive_summary = _build_executive_summary_payload(candidate_ledger, action_tables, failure_audit, cfg)
    return {
        "enabled": True,
        "interactions": interactions,
        "interaction_matrix": interaction_matrix,
        "incremental_lift_table": incremental_lift,
        "regime_ignition_table": ignition_table,
        "regime_decay_table": decay_table,
        "first_signal_follow_on_table": first_follow,
        "interaction_stability_table": stability_table,
        "tradeable_regime_candidate_ledger": candidate_ledger,
        "action_decision_tables": action_tables,
        "live_decision_readiness_screen": readiness,
        "edge_decomposition_table": edge_decomposition,
        "failure_mode_audit": failure_audit,
        "executive_summary_payload": executive_summary,
        "top_filter_capture": {
            "top_n": top_n,
            "percent_wins_captured": _round(100.0 * top_wins / baseline_wins, 1) if baseline_wins else 0.0,
            "percent_signals_captured": _round(100.0 * top_signals / len(rows), 1) if rows else 0.0,
            "top_states": [f"{r.get('onset_condition')} x {r.get('early_path_condition')}" for r in top],
        },
        "definitions": {
            "decision_time_features": ["onset_signatures", "session", "key_level_interaction", "vwap_stretch_bucket"],
            "post_entry_validation_features": ["early_path_class", "midpoint_reclaimed_within_2bars", "signal_extreme_rebreak_within_2bars", "favorable_move_2bar_pct", "adverse_move_2bar_pct"],
            "warning": "Early-path labels use the first post-entry bars and should be treated as validation/management inputs, not pure entry-time filters.",
        },
        "config": cfg,
    }


def analyze_trigger_anchored_windows(rows: List[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or {}
    minute_windows = [int(x) for x in cfg.get("minute_windows", [10, 20, 30, 60])]
    signal_windows = [int(x) for x in cfg.get("signal_windows", [3, 5, 10])]
    min_anchors = int(cfg.get("min_anchors", 3))
    anchor_defs = _anchor_definitions(cfg)

    results: List[Dict[str, Any]] = []
    for anchor_name, pred in anchor_defs:
        anchor_indexes = _first_per_day(rows, pred)
        if len(anchor_indexes) < min_anchors:
            continue
        minute_rows = []
        for minutes in minute_windows:
            slices = [_future_rows(rows, idx, max_minutes=float(minutes), max_signals=None) for idx in anchor_indexes]
            flat = [r for s in slices for r in s]
            minute_rows.append(_window_summary(f"next_{minutes}_minutes", flat, len(anchor_indexes)))
        signal_rows = []
        for n in signal_windows:
            slices = [_future_rows(rows, idx, max_minutes=None, max_signals=n) for idx in anchor_indexes]
            flat = [r for s in slices for r in s]
            signal_rows.append(_window_summary(f"next_{n}_signals", flat, len(anchor_indexes)))
        results.append({
            "anchor": anchor_name,
            "anchor_count": len(anchor_indexes),
            "minute_windows": minute_rows,
            "signal_windows": signal_rows,
        })

    results.sort(key=lambda r: -max([_sf(w.get("expectancy_ticks")) for w in (r.get("minute_windows") or []) + (r.get("signal_windows") or [])] or [0.0]))
    return {"enabled": True, "anchors": results, "config": cfg}


def analyze_failure_to_flip(rows: List[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or {}
    failure_max = float(cfg.get("failure_max_reversal_pct", 25.0))
    flip_min = float(cfg.get("flip_min_continuation_pct", 50.0))
    count_rebreak_as_flip = bool(cfg.get("count_rebreak_as_flip", False))
    min_n = int(cfg.get("min_n", 5))

    eligible_rows = [r for r in rows if r.get("has_reversal_mfe") and r.get("has_continuation_mfe")]
    failures = [r for r in eligible_rows if _sf(r.get("reversal_mfe_pct")) <= failure_max]
    strict_flip_rows = [r for r in failures if _sf(r.get("continuation_mfe_pct")) >= flip_min]
    rebreak_proxy_rows = [r for r in failures if bool(r.get("signal_extreme_rebreak_within_2bars"))]
    flip_rows = [
        r for r in failures
        if _sf(r.get("continuation_mfe_pct")) >= flip_min
        or (count_rebreak_as_flip and bool(r.get("signal_extreme_rebreak_within_2bars")))
    ]

    signature_keys = [
        "early_path_class",
        "signal_extreme_rebreak_within_2bars",
        "midpoint_reclaimed_within_2bars",
        "session",
        "vwap_stretch_bucket",
        "key_level_interaction",
    ]
    signatures = []
    for key in signature_keys:
        groups: Dict[Any, List[Dict[str, Any]]] = {}
        for r in rows:
            groups.setdefault(r.get(key), []).append(r)
        for value, grp in groups.items():
            if len(grp) < min_n:
                continue
            grp_failures = [r for r in grp if r.get("has_reversal_mfe") and r.get("has_continuation_mfe") and _sf(r.get("reversal_mfe_pct")) <= failure_max]
            if len(grp_failures) < min_n:
                continue
            grp_strict_flips = [r for r in grp_failures if _sf(r.get("continuation_mfe_pct")) >= flip_min]
            grp_rebreak_proxy = [r for r in grp_failures if bool(r.get("signal_extreme_rebreak_within_2bars"))]
            grp_flips = [
                r for r in grp_failures
                if _sf(r.get("continuation_mfe_pct")) >= flip_min
                or (count_rebreak_as_flip and bool(r.get("signal_extreme_rebreak_within_2bars")))
            ]
            signatures.append({
                "signature": f"{key}={value}",
                "n": len(grp),
                "failure_rate": _round(100.0 * len(grp_failures) / len(grp), 1),
                "flip_rate_after_failure": _round(100.0 * len(grp_flips) / len(grp_failures), 1),
                "strict_continuation_flip_rate": _round(100.0 * len(grp_strict_flips) / len(grp_failures), 1),
                "rebreak_proxy_rate": _round(100.0 * len(grp_rebreak_proxy) / len(grp_failures), 1),
                "reversal_expectancy_ticks": _round(_avg([r.get("expectancy_ticks") for r in grp_failures]), 3),
                "flip_expectancy_ticks": _round(_avg([r.get("flip_expectancy_ticks") for r in grp_strict_flips]), 3),
                "flip_exceeds_stay": _sf(_avg([r.get("flip_expectancy_ticks") for r in grp_strict_flips])) > _sf(_avg([r.get("expectancy_ticks") for r in grp_failures])),
            })

    signatures.sort(key=lambda r: (-_sf(r.get("flip_expectancy_ticks")), -_sf(r.get("flip_rate_after_failure")), -int(r.get("n") or 0)))
    null_reversal = sum(1 for r in rows if not r.get("has_reversal_mfe"))
    null_continuation = sum(1 for r in rows if not r.get("has_continuation_mfe"))
    audited_rate = _round(100.0 * len(flip_rows) / len(failures), 1) if failures else 0.0
    strict_rate = _round(100.0 * len(strict_flip_rows) / len(failures), 1) if failures else 0.0
    proxy_rate = _round(100.0 * len(rebreak_proxy_rows) / len(failures), 1) if failures else 0.0
    suspicious = []
    if audited_rate == 100.0 and len(failures) < max(20, min_n * 2):
        suspicious.append("100pct_flip_rate_on_low_failure_sample")
    if audited_rate == 100.0 and strict_rate < 100.0:
        suspicious.append("100pct_requires_rebreak_proxy_not_strict_continuation")
    if proxy_rate == 100.0 and strict_rate < 100.0:
        suspicious.append("all_failures_rebreak_but_not_all_meet_continuation_threshold")
    exact_sample = [
        {
            "dt": r.get("dt"),
            "reversal_mfe_pct": r.get("reversal_mfe_pct"),
            "continuation_mfe_pct": r.get("continuation_mfe_pct"),
            "flip_expectancy_ticks": r.get("flip_expectancy_ticks"),
            "time_to_flip_min": r.get("time_to_max_continuation_min"),
            "cleanliness_score": r.get("cleanliness_score"),
            "strict_flip": _sf(r.get("continuation_mfe_pct")) >= flip_min,
            "rebreak_proxy": bool(r.get("signal_extreme_rebreak_within_2bars")),
        }
        for r in failures[:50]
    ]
    return {
        "enabled": True,
        "definition": {
            "conditional_definition": "Among reversal failures, estimate whether continuation/flip behavior becomes preferable.",
            "denominator": f"events with reversal_mfe_pct <= {failure_max} and non-null reversal/continuation excursions",
            "numerator": (
                f"denominator events with continuation_mfe_pct >= {flip_min}"
                + (" OR signal_extreme_rebreak_within_2bars" if count_rebreak_as_flip else "")
            ),
            "strict_flip_threshold": f"continuation_mfe_pct >= {flip_min}",
            "rebreak_proxy_counted_as_flip": count_rebreak_as_flip,
        },
        "diagnostics": {
            "input_rows": len(rows),
            "eligible_rows": len(eligible_rows),
            "excluded_null_reversal_mfe": null_reversal,
            "excluded_null_continuation_mfe": null_continuation,
            "denominator_failures": len(failures),
            "numerator_flips": len(flip_rows),
            "strict_continuation_flips": len(strict_flip_rows),
            "rebreak_proxy_flips": len(rebreak_proxy_rows),
            "exact_qualifying_sample": exact_sample,
            "fallback_paths": ["strict_continuation_threshold"] + (["signal_extreme_rebreak_proxy"] if count_rebreak_as_flip else []),
            "suspicious_flags": suspicious,
        },
        "n_failures": len(failures),
        "n_flip_candidates": len(flip_rows),
        "failure_rate": _round(100.0 * len(failures) / len(eligible_rows), 1) if eligible_rows else 0.0,
        "flip_rate_after_failure": audited_rate,
        "strict_continuation_flip_rate": strict_rate,
        "rebreak_proxy_rate": proxy_rate,
        "median_expectancy_after_flip_ticks": _round(_median([r.get("flip_expectancy_ticks") for r in strict_flip_rows]), 3),
        "median_time_to_flip_minutes": _round(_median([r.get("time_to_max_continuation_min") for r in strict_flip_rows]), 2),
        "median_cleanliness_after_flip": _round(_median([r.get("cleanliness_score") for r in strict_flip_rows]), 2),
        "avg_bars_to_flip_confirmation": _round(_avg([r.get("time_to_first_pullback_bars") for r in strict_flip_rows]), 2),
        "avg_minutes_to_flip_confirmation": _round(_avg([r.get("time_to_max_continuation_min") for r in strict_flip_rows]), 2),
        "signatures": signatures[:25],
        "config": cfg,
    }


def detect_regime_decay(
    rows: List[Dict[str, Any]],
    cluster_result: Dict[str, Any],
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = cfg or {}
    rolling_n = max(1, int(cfg.get("rolling_window_signals", 5)))
    density_floor = float(cfg.get("win_density_drop_below", 0.50))
    mae_rise_multiple = float(cfg.get("mae_rise_multiple", 1.35))
    early_drop_ratio = float(cfg.get("early_favorable_drop_ratio", 0.70))
    cleanliness_drop = float(cfg.get("cleanliness_drop_points", 15.0))
    expectancy_drop = float(cfg.get("expectancy_drop_ticks", 0.0))
    slow_minutes = float(cfg.get("slow_favorable_minutes", 20.0))
    failed_allowed = int(cfg.get("failed_reversals_allowed", 1))
    min_signature_n = int(cfg.get("min_signature_n", 2))
    by_index = {int(r.get("_row_index") or 0): r for r in rows}

    decay_rows: List[Dict[str, Any]] = []
    for cluster in cluster_result.get("clusters") or []:
        end_idx = int(cluster.get("end_index") or 0)
        start_idx = int(cluster.get("start_index") or 0)
        in_cluster = [by_index[i] for i in range(start_idx, end_idx + 1) if i in by_index]
        post = rows[end_idx + 1: end_idx + 1 + rolling_n * 3]
        base_mae = _avg([r.get("reversal_mae_pct") for r in in_cluster]) or 0.0
        base_early = _avg([_safe_pct(r.get("early_fav_2bar_ticks"), r.get("size_ticks")) for r in in_cluster]) or 0.0
        base_clean = _avg([r.get("cleanliness_score") for r in in_cluster]) or 0.0
        base_exp = _avg([r.get("expectancy_ticks") for r in in_cluster]) or 0.0
        signals: List[str] = []
        decay_idx: Optional[int] = None
        failed_seen = 0
        for offset, row in enumerate(post):
            window = post[max(0, offset - rolling_n + 1): offset + 1]
            density = sum(1 for r in window if r.get("is_reversal_win")) / len(window) if window else 1.0
            mae = _avg([r.get("reversal_mae_pct") for r in window]) or 0.0
            early = _avg([_safe_pct(r.get("early_fav_2bar_ticks"), r.get("size_ticks")) for r in window]) or 0.0
            clean = _avg([r.get("cleanliness_score") for r in window]) or 0.0
            exp = _avg([r.get("expectancy_ticks") for r in window]) or 0.0
            rebreak_rate = sum(1 for r in window if r.get("signal_extreme_rebreak_within_2bars")) / len(window) if window else 0.0
            if density < density_floor:
                signals.append("falling_rolling_win_density")
                decay_idx = offset
            if base_mae > 0 and mae >= base_mae * mae_rise_multiple:
                signals.append("rising_adverse_excursion")
                decay_idx = offset
            if base_early > 0 and early <= base_early * early_drop_ratio:
                signals.append("lower_early_favorable_move_rate")
                decay_idx = offset
            if rebreak_rate > 0:
                signals.append("more_signal_extreme_rebreaks")
                decay_idx = offset if decay_idx is None else decay_idx
            if base_clean > 0 and clean <= base_clean - cleanliness_drop:
                signals.append("declining_cleanliness")
                decay_idx = offset
            if exp <= base_exp - expectancy_drop and exp < base_exp:
                signals.append("declining_rolling_expectancy")
                decay_idx = offset
            if _sf(row.get("time_to_max_reversal_min")) >= slow_minutes:
                signals.append("slower_favorable_movement")
            if not row.get("is_reversal_win"):
                failed_seen += 1
            if failed_seen > failed_allowed:
                signals.append("increasing_failed_reversals")
                decay_idx = offset
            if bool(row.get("signal_extreme_rebreak_within_2bars")):
                signals.append("more_signal_extreme_rebreaks")
            if decay_idx is not None:
                break
        if decay_idx is not None and decay_idx < len(post):
            decay_row = post[decay_idx]
            time_to_decay = _minutes_between(_parse_dt(cluster.get("start_dt")), decay_row.get("_dt"))
        else:
            time_to_decay = cluster.get("time_to_decay_minutes")
        decay_rows.append({
            "cluster_start_dt": cluster.get("start_dt"),
            "cluster_end_dt": cluster.get("end_dt"),
            "onset_condition": cluster.get("onset_condition"),
            "cluster_length": cluster.get("length"),
            "time_to_decay_minutes": _round(time_to_decay, 2),
            "decay_signals": sorted(set(signals)) or ["cluster_gap_or_first_loss"],
            "baseline": {
                "mae_pct": _round(base_mae, 2),
                "early_fav_2bar_pct": _round(base_early, 2),
                "cleanliness": _round(base_clean, 2),
                "expectancy_ticks": _round(base_exp, 3),
            },
        })

    signal_counts: Dict[str, int] = {}
    for row in decay_rows:
        for sig in row.get("decay_signals") or []:
            signal_counts[sig] = signal_counts.get(sig, 0) + 1
    signature_rows = _decay_signature_summary(decay_rows, min_signature_n)

    return {
        "enabled": True,
        "decay_events": decay_rows,
        "avg_time_to_decay_minutes": _round(_avg([r.get("time_to_decay_minutes") for r in decay_rows]), 2),
        "signal_counts": signal_counts,
        "summary_by_signal": [
            {"decay_signal": k, "count": v, "pct_of_decay_events": _round(100.0 * v / len(decay_rows), 1) if decay_rows else 0.0}
            for k, v in sorted(signal_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "decay_drivers_by_onset": _decay_drivers_by_onset(decay_rows),
        "top_decay_signatures": signature_rows,
        "config": cfg,
    }


def _event_rows(events: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    desired_win = int(cfg.get("forward_window_minutes", 30))
    target_pct = float(cfg.get("win_target_pct", 50.0))
    max_rows = int(cfg.get("max_rows", 5000))
    selected, win_meta = _select_window_events(events, desired_win)
    rows: List[Dict[str, Any]] = []
    diag = {**win_meta, "input_events": len(events), "valid_rows": 0, "excluded_missing": 0}

    for ev in selected[:max_rows]:
        dt = _parse_dt(ev.get("dt"))
        size = _sf(ev.get("size_ticks"), None)
        if dt is None or size is None or size <= 0:
            diag["excluded_missing"] += 1
            continue
        has_continuation_mfe = _has_number(ev.get("fav_ticks"))
        has_reversal_mfe = _has_number(ev.get("adv_ticks"))
        continuation_mfe = _sf(ev.get("fav_ticks"), 0.0)
        continuation_mae = _sf(ev.get("adv_ticks"), 0.0)
        reversal_mfe = continuation_mae
        reversal_mae = continuation_mfe
        target_ticks = size * target_pct / 100.0
        early_fav_2bar_pct = _safe_pct(ev.get("early_fav_2bar_ticks"), size)
        early_adv_2bar_pct = _safe_pct(ev.get("early_adv_2bar_ticks"), size)
        row = {
            "dt": dt.isoformat(),
            "_dt": dt,
            "date": dt.date().isoformat(),
            "session": ev.get("session_bucket") or "unknown",
            "direction": ev.get("direction"),
            "signal_direction": "bull" if int(ev.get("direction") or 0) == 1 else "bear",
            "tf_minutes": ev.get("tf_minutes"),
            "window_minutes": ev.get("window_minutes"),
            "size_ticks": _round(size, 3),
            "target_ticks": _round(target_ticks, 3),
            "reversal_mfe_ticks": _round(reversal_mfe, 3),
            "reversal_mae_ticks": _round(reversal_mae, 3),
            "reversal_mfe_pct": _round((reversal_mfe / size) * 100.0, 1),
            "reversal_mae_pct": _round((reversal_mae / size) * 100.0, 1),
            "continuation_mfe_ticks": _round(continuation_mfe, 3),
            "continuation_mae_ticks": _round(continuation_mae, 3),
            "continuation_mfe_pct": _round((continuation_mfe / size) * 100.0, 1),
            "has_reversal_mfe": has_reversal_mfe,
            "has_continuation_mfe": has_continuation_mfe,
            "expectancy_ticks": _round(reversal_mfe - reversal_mae, 3),
            "flip_expectancy_ticks": _round(continuation_mfe - continuation_mae, 3),
            "is_reversal_win": reversal_mfe >= target_ticks,
            "time_to_max_reversal_min": ev.get("time_to_max_adv_min"),
            "time_to_max_continuation_min": ev.get("time_to_max_fav_min"),
            "early_fav_1bar_ticks": ev.get("early_fav_1bar_ticks"),
            "early_fav_2bar_ticks": ev.get("early_fav_2bar_ticks"),
            "early_adv_1bar_ticks": ev.get("early_adv_1bar_ticks"),
            "early_adv_2bar_ticks": ev.get("early_adv_2bar_ticks"),
            "favorable_move_2bar_pct": _round(early_fav_2bar_pct, 1),
            "adverse_move_2bar_pct": _round(early_adv_2bar_pct, 1),
            "early_path_efficiency": _round(_sf(early_fav_2bar_pct, 0.0) / max(_sf(early_adv_2bar_pct, 0.0), 1.0), 3),
            "midpoint_reclaimed_within_2bars": _to_bool(ev.get("did_price_reclaim_signal_midpoint")),
            "signal_extreme_rebreak_within_2bars": _to_bool(ev.get("did_price_break_signal_extreme_again")),
            "time_to_first_pullback_bars": ev.get("time_to_first_pullback_bars"),
            "early_path_class": _early_path_class_from_event(ev, size),
            "relative_volume": ev.get("relative_volume"),
            "vol_bucket": ev.get("vol_bucket") or "unknown",
            "body_to_range_ratio": ev.get("body_to_range_ratio"),
            "close_location": ev.get("close_position_in_range"),
            "upper_wick_to_range_ratio": ev.get("upper_wick_to_range_ratio"),
            "lower_wick_to_range_ratio": ev.get("lower_wick_to_range_ratio"),
            "wick_asymmetry": _wick_asymmetry(ev),
            "range_vs_avg_range": ev.get("range_vs_avg_range"),
            "range_as_atr": ev.get("range_as_atr"),
            "range_avg_bucket": ev.get("range_avg_bucket") or "unknown",
            "atr_bucket": ev.get("atr_bucket") or "unknown",
            "dist_vwap_atr": ev.get("dist_vwap_atr"),
            "vwap_stretch_bucket": ev.get("vwap_ext_bucket") or ev.get("vwap_signed_bucket") or "unknown",
            "dist_ma100_atr": ev.get("dist_ma100_atr"),
            "dist_ma200_atr": ev.get("dist_ma200_atr"),
            "ma100_slope_label": ev.get("ma100_slope_label") or "unknown",
            "ma200_slope_label": ev.get("ma200_slope_label") or "unknown",
            "trend_state": ev.get("trend_alignment_label") or "unknown",
            "direction_streak": ev.get("direction_streak"),
            "same_as_prev": ev.get("same_as_prev"),
            "key_level_interaction": ev.get("level_interaction_label") or "unknown",
            "nearest_level_type": ev.get("nearest_level_type") or "unknown",
            "exhaustion_label": ev.get("exhaustion_label") or "unknown",
            "continuation_vs_prior_candle": _continuation_label(ev.get("same_as_prev")),
        }
        rows.append(row)

    rows.sort(key=lambda r: r["_dt"])
    for i, row in enumerate(rows):
        row["_row_index"] = i
    diag["valid_rows"] = len(rows)
    return rows, diag


def _score_cleanliness(rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> None:
    target_pct = float(cfg.get("target_pct", 50.0))
    weights = _deep_merge(DEFAULT_REGIME_DISCOVERY_CONFIG["cleanliness"]["weights"], cfg.get("weights") or {})
    speed_full = float(cfg.get("speed_full_credit_minutes", 5.0))
    speed_zero = float(cfg.get("speed_zero_credit_minutes", 30.0))
    for row in rows:
        target = _sf(row.get("size_ticks")) * target_pct / 100.0
        mae = _sf(row.get("reversal_mae_ticks"))
        mfe = _sf(row.get("reversal_mfe_ticks"))
        early = _sf(row.get("early_fav_2bar_ticks"))
        early_adv = _sf(row.get("early_adv_2bar_ticks"))
        speed_min = _sf(row.get("time_to_max_reversal_min"))
        mae_component = 1.0 - _clamp01(mae / target) if target > 0 else 0.0
        speed_component = 1.0 - _clamp01((speed_min - speed_full) / max(1.0, speed_zero - speed_full))
        smooth_component = _clamp01((early / max(1.0, early_adv)) / 2.0) if early is not None and early_adv is not None else 0.0
        rebreak_component = 0.0 if row.get("signal_extreme_rebreak_within_2bars") else 1.0
        flip_component = 0.0 if row.get("signal_extreme_rebreak_within_2bars") and mfe < target else 1.0
        score = (
            weights.get("mae", 0.30) * mae_component
            + weights.get("speed", 0.20) * speed_component
            + weights.get("smoothness", 0.20) * smooth_component
            + weights.get("rebreak_avoidance", 0.20) * rebreak_component
            + weights.get("flip_independence", 0.10) * flip_component
        )
        row["cleanliness_score"] = _round(_clamp01(score) * 100.0, 1)
        row["cleanliness_components"] = {
            "mae": _round(mae_component, 3),
            "speed": _round(speed_component, 3),
            "smoothness": _round(smooth_component, 3),
            "rebreak_avoidance": _round(rebreak_component, 3),
            "flip_independence": _round(flip_component, 3),
        }


def _add_cluster_state_features(rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> None:
    quiet_minutes = float((cfg.get("onset_detection") or {}).get("quiet_lookback_minutes", 60))
    failed_lb = int((cfg.get("onset_detection") or {}).get("failed_continuation_lookback_signals", 3))
    for i, row in enumerate(rows):
        prev = rows[i - 1] if i > 0 else None
        last3 = rows[max(0, i - 3):i]
        last5 = rows[max(0, i - 5):i]
        last10 = rows[max(0, i - 10):i]
        recent_time = [r for r in rows[:i] if _minutes_between(r.get("_dt"), row.get("_dt")) is not None and _minutes_between(r.get("_dt"), row.get("_dt")) <= quiet_minutes]
        streak = 0
        for prev in reversed(rows[:i]):
            if prev.get("is_reversal_win"):
                streak += 1
            else:
                break
        row["wins_last_3_signals"] = sum(1 for r in last3 if r.get("is_reversal_win"))
        row["wins_last_5_signals"] = sum(1 for r in last5 if r.get("is_reversal_win"))
        row["wins_last_10_signals"] = sum(1 for r in last10 if r.get("is_reversal_win"))
        row["rolling_expectancy_10"] = _round(_avg([r.get("expectancy_ticks") for r in last10]), 3)
        row["rolling_cleanliness_10"] = _round(_avg([r.get("cleanliness_score") for r in last10]), 2)
        row["current_streak_length"] = streak
        row["prior_large_candle_density"] = len(recent_time)
        row["qualifying_signal_density_recent_window"] = len(recent_time)
        row["prev_direction_streak"] = prev.get("direction_streak") if prev else None
        row["prev_signal_direction"] = prev.get("signal_direction") if prev else None
        row["prev_was_failed_continuation"] = bool(
            prev
            and prev.get("early_path_class") == "weak_start"
            and bool(prev.get("signal_extreme_rebreak_within_2bars"))
        )
        row["prev_was_key_vwap_stretch"] = bool(
            prev
            and prev.get("key_level_interaction") in {"at_level", "approaching", "nearby"}
            and (abs(_sf(prev.get("dist_vwap_atr"))) >= float((cfg.get("onset_detection") or {}).get("vwap_stretch_atr", 0.75)) or prev.get("vwap_stretch_bucket") == "extended")
        )
        row["prev_was_session_range_break"] = bool(
            prev
            and prev.get("nearest_level_type") in {"session_high", "session_low"}
            and prev.get("key_level_interaction") in {"at_level", "approaching", "departing", "nearby"}
        )
        recent_failed = rows[max(0, i - failed_lb):i]
        row["recent_failed_continuations"] = sum(
            1 for r in recent_failed
            if r.get("early_path_class") == "weak_start" and bool(r.get("signal_extreme_rebreak_within_2bars"))
        )


def _assign_onset_signatures(rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> None:
    signature_map = _onset_condition_map(cfg)
    for row in rows:
        row["onset_signatures"] = [label for label, pred in signature_map if pred(row)]
        row["primary_onset_signature"] = _primary_onset_signature(row.get("onset_signatures") or [])


def _primary_onset_signature(signatures: List[str]) -> str:
    priority = [
        "first_large_at_key_level_with_extended_vwap_stretch",
        "first_large_after_failed_continuation",
        "first_large_after_session_range_break",
        "first_large_after_directional_run",
        "first_large_after_compression",
    ]
    for p in priority:
        if p in signatures:
            return p
    return signatures[0] if signatures else "unclassified"


def _cluster_payload(cur: List[Dict[str, Any]]) -> Dict[str, Any]:
    start = cur[0]
    end = cur[-1]
    duration = _minutes_between(start.get("_dt"), end.get("_dt")) or 0.0
    onset = start.get("primary_onset_signature") or "unclassified"
    return {
        "start_dt": start.get("dt"),
        "end_dt": end.get("dt"),
        "start_index": int(start.get("_row_index") or 0),
        "end_index": int(end.get("_row_index") or 0),
        "onset_condition": onset,
        "onset_signatures": start.get("onset_signatures") or [],
        "length": len(cur),
        "duration_minutes": _round(duration, 2),
        "win_rate": 100.0,
        "expectancy_ticks": _round(_avg([r.get("expectancy_ticks") for r in cur]), 3),
        "avg_cleanliness": _round(_avg([r.get("cleanliness_score") for r in cur]), 2),
        "avg_mae_pct": _round(_avg([r.get("reversal_mae_pct") for r in cur]), 2),
        "time_to_decay_minutes": None,
    }


def _cluster_summary_by_onset(clusters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for cluster in clusters:
        groups.setdefault(str(cluster.get("onset_condition") or "unclassified"), []).append(cluster)
    out: List[Dict[str, Any]] = []
    for onset, grp in groups.items():
        out.append({
            "onset_condition": onset,
            "cluster_count": len(grp),
            "median_cluster_length": _round(_median([g.get("length") for g in grp]), 2),
            "median_cluster_duration_minutes": _round(_median([g.get("duration_minutes") for g in grp]), 2),
            "median_cleanliness": _round(_median([g.get("avg_cleanliness") for g in grp]), 2),
            "median_expectancy_ticks": _round(_median([g.get("expectancy_ticks") for g in grp]), 3),
            "median_time_to_decay_minutes": _round(_median([g.get("time_to_decay_minutes") for g in grp]), 2),
        })
    out.sort(key=lambda r: (-_sf(r.get("median_expectancy_ticks")), -_sf(r.get("median_cleanliness")), -int(r.get("cluster_count") or 0)))
    return out


def _cleanliness_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    winners = [r for r in rows if r.get("is_reversal_win")]
    losers = [r for r in rows if not r.get("is_reversal_win")]
    return {
        "avg_cleanliness": _round(_avg([r.get("cleanliness_score") for r in rows]), 2),
        "winner_cleanliness": _round(_avg([r.get("cleanliness_score") for r in winners]), 2),
        "loser_cleanliness": _round(_avg([r.get("cleanliness_score") for r in losers]), 2),
        "winner_loser_cleanliness_gap": _round((_avg([r.get("cleanliness_score") for r in winners]) or 0) - (_avg([r.get("cleanliness_score") for r in losers]) or 0), 2),
    }


def _cleanliness_definition(cfg: Dict[str, Any]) -> Dict[str, Any]:
    weights = _deep_merge(DEFAULT_REGIME_DISCOVERY_CONFIG["cleanliness"]["weights"], cfg.get("weights") or {})
    target_pct = float(cfg.get("target_pct", 50.0))
    return {
        "formula": "100 * weighted_sum(mae_component, speed_component, smoothness_component, rebreak_avoidance_component, flip_independence_component)",
        "components": {
            "mae_component": f"1 - clamp(reversal_mae_ticks / ({target_pct}% of signal size), 0, 1)",
            "speed_component": "1 is fastest favorable excursion, decays toward 0 by speed_zero_credit_minutes",
            "smoothness_component": "clamp((early_fav_2bar_ticks / max(early_adv_2bar_ticks, 1)) / 2, 0, 1)",
            "rebreak_avoidance_component": "1 when signal extreme is not re-broken, else 0",
            "flip_independence_component": "1 when reversal does not require flip rescue, else 0",
        },
        "weights": weights,
        "normalization": "Each component is normalized to 0..1 before weighting; final score is scaled to 0..100.",
        "score_range": "0..100",
        "interpretation_bands": {
            "clean": ">= 75",
            "workable": "55 to 74.9",
            "messy": "35 to 54.9",
            "avoid_or_flip_watch": "< 35",
        },
        "higher_is_always_better": True,
    }


def _onset_condition_map(cfg: Dict[str, Any]) -> List[Tuple[str, Callable[[Dict[str, Any]], bool]]]:
    quiet_max = int(cfg.get("quiet_max_prior_signals", 1))
    vwap_atr = float(cfg.get("vwap_stretch_atr", 0.75))
    streak_min = int(cfg.get("direction_streak_min", 3))
    range_mult = float(cfg.get("range_expansion_multiple", 1.5))
    def _key_vwap(r: Dict[str, Any]) -> bool:
        return (
            r.get("key_level_interaction") in {"at_level", "approaching", "nearby"}
            and (abs(_sf(r.get("dist_vwap_atr"))) >= vwap_atr or r.get("vwap_stretch_bucket") == "extended")
            and not bool(r.get("prev_was_key_vwap_stretch"))
        )
    def _directional_run_transition(r: Dict[str, Any]) -> bool:
        if _sf(r.get("direction_streak")) < streak_min:
            return False
        prev_streak = _sf(r.get("prev_direction_streak"), 0.0)
        direction_changed = bool(r.get("prev_signal_direction") and r.get("prev_signal_direction") != r.get("signal_direction"))
        return prev_streak < streak_min or direction_changed
    def _session_range_break_transition(r: Dict[str, Any]) -> bool:
        is_break = (
            r.get("nearest_level_type") in {"session_high", "session_low"}
            and r.get("key_level_interaction") in {"at_level", "approaching", "departing", "nearby"}
        )
        return is_break and not bool(r.get("prev_was_session_range_break"))
    return [
        ("first_large_after_compression", lambda r: _sf(r.get("prior_large_candle_density")) <= quiet_max and (_sf(r.get("range_vs_avg_range")) >= range_mult or r.get("range_avg_bucket") in {"1_5x_to_2_0x", "2_0x_to_3_0x", "ge_3_0x"})),
        ("first_large_after_directional_run", _directional_run_transition),
        ("first_large_at_key_level_with_extended_vwap_stretch", _key_vwap),
        ("first_large_after_failed_continuation", lambda r: bool(r.get("prev_was_failed_continuation"))),
        ("first_large_after_session_range_break", _session_range_break_transition),
    ]


def _signature_metadata(signature: str) -> Dict[str, Any]:
    meta = {
        "first_large_after_compression": {
            "background_state": "quiet recent large-candle density, then range expansion",
            "trigger_event": "first qualifying large candle after compression",
            "early_validation": "requires follow-through confirmation from early path",
            "preferred_action": "scalp_only",
        },
        "first_large_after_directional_run": {
            "background_state": "same-direction lead-in or exhaustion context",
            "trigger_event": "large candle after directional run",
            "early_validation": "midpoint reclaim and no re-break improve hold quality",
            "preferred_action": "scalp_only",
        },
        "first_large_at_key_level_with_extended_vwap_stretch": {
            "background_state": "extended from VWAP while interacting with a key level",
            "trigger_event": "large candle at/near level",
            "early_validation": "clean reclaim supports reversal hold; re-break means flip-watch",
            "preferred_action": "reversal_hold",
        },
        "first_large_after_failed_continuation": {
            "background_state": "recent continuation attempt failed/re-broke signal extreme",
            "trigger_event": "next large candle after failure",
            "early_validation": "watch continuation MFE before trusting reversal",
            "preferred_action": "flip_watch",
        },
        "first_large_after_session_range_break": {
            "background_state": "session high/low interaction or range break",
            "trigger_event": "large candle around session range boundary",
            "early_validation": "avoid if adverse excursion rises immediately",
            "preferred_action": "scalp_only",
        },
        "unclassified": {
            "background_state": "mixed or unavailable",
            "trigger_event": "qualifying large candle",
            "early_validation": "use decision engine early-path class",
            "preferred_action": "avoid",
        },
    }
    return meta.get(signature, meta["unclassified"])


def _build_decision_summary_cards(
    onset_result: Dict[str, Any],
    failure_flip_result: Dict[str, Any],
    decay_result: Dict[str, Any],
    interaction_result: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    flip_rate = _sf(failure_flip_result.get("strict_continuation_flip_rate"))
    decay_avg = decay_result.get("avg_time_to_decay_minutes")
    interactions = (interaction_result or {}).get("interactions") or []
    if interactions:
        for row in interactions[:5]:
            sig = str(row.get("onset_condition") or "unclassified")
            meta = _signature_metadata(sig)
            cards.append({
                "regime_signature": f"{sig} x {row.get('early_path_condition')}",
                "background_state": meta.get("background_state"),
                "trigger_event": meta.get("trigger_event"),
                "early_validation": row.get("early_path_condition"),
                "expected_cluster_length": row.get("median_cluster_length") or row.get("cluster_persistence"),
                "expected_decay_window_minutes": row.get("median_time_to_decay_minutes") or decay_avg,
                "preferred_action": row.get("operational_action"),
                "action_reason": row.get("action_reason"),
                "post_onset_win_rate": row.get("win_rate"),
                "post_onset_expectancy_ticks": row.get("expectancy_ticks"),
                "median_cleanliness": row.get("avg_cleanliness"),
            })
        return cards

    for row in (onset_result.get("conditions") or [])[:5]:
        sig = str(row.get("onset_condition") or "unclassified")
        meta = _signature_metadata(sig)
        wr = _sf(row.get("post_onset_win_rate"))
        exp = _sf(row.get("post_onset_expectancy_ticks"))
        clean = _sf(row.get("median_cluster_cleanliness"))
        action = str(meta.get("preferred_action") or "avoid")
        reason = "good early-path confirmation" if wr >= 55 and exp > 0 else "weak lift"
        if row.get("high_overlap"):
            reason = "high overlap"
        if row.get("low_exclusivity"):
            reason = "low exclusivity"
        if row.get("high_coverage"):
            reason = "weak lift"
        if row.get("high_coverage") or row.get("low_exclusivity"):
            action = "avoid"
        elif wr < 45 or exp <= 0:
            action = "avoid"
        elif flip_rate >= 60 and sig == "first_large_after_failed_continuation":
            action = "flip_watch"
        elif clean >= 82 and wr >= 65 and _sf(row.get("median_cluster_length")) >= 4:
            action = "reversal_hold"
        elif clean >= 70 and wr >= 58:
            action = "reversal_hold"
        elif clean >= 55 and wr >= 50:
            action = "scalp_only"
        elif flip_rate >= 50:
            action = "continuation_preferred"
        elif wr >= 50:
            action = "scalp_only"
        cards.append({
            "regime_signature": sig,
            "background_state": meta.get("background_state"),
            "trigger_event": meta.get("trigger_event"),
            "early_validation": meta.get("early_validation"),
            "expected_cluster_length": row.get("median_cluster_length"),
            "expected_decay_window_minutes": row.get("avg_time_to_decay_minutes") or decay_avg,
            "preferred_action": action,
            "action_reason": reason,
            "post_onset_win_rate": row.get("post_onset_win_rate"),
            "post_onset_expectancy_ticks": row.get("post_onset_expectancy_ticks"),
            "median_cleanliness": row.get("median_cluster_cleanliness"),
        })
    return cards


def _early_path_interaction_conditions() -> List[Tuple[str, Callable[[Dict[str, Any]], bool]]]:
    return [
        ("explosive_start", lambda r: r.get("early_path_class") == "explosive_start"),
        ("orderly_start", lambda r: r.get("early_path_class") == "orderly_start"),
        ("weak_start", lambda r: r.get("early_path_class") == "weak_start"),
        ("mixed_start", lambda r: r.get("early_path_class") == "mixed_start"),
        ("midpoint_reclaim_yes", lambda r: bool(r.get("midpoint_reclaimed_within_2bars"))),
        ("midpoint_reclaim_no", lambda r: not bool(r.get("midpoint_reclaimed_within_2bars"))),
        ("rebreak_yes", lambda r: bool(r.get("signal_extreme_rebreak_within_2bars"))),
        ("rebreak_no", lambda r: not bool(r.get("signal_extreme_rebreak_within_2bars"))),
        ("fav2bar_lt_15pct", lambda r: _sf(r.get("favorable_move_2bar_pct")) < 15.0),
        ("fav2bar_15_35pct", lambda r: 15.0 <= _sf(r.get("favorable_move_2bar_pct")) < 35.0),
        ("fav2bar_ge_35pct", lambda r: _sf(r.get("favorable_move_2bar_pct")) >= 35.0),
        ("adv2bar_lt_20pct", lambda r: _sf(r.get("adverse_move_2bar_pct")) < 20.0),
        ("adv2bar_20_40pct", lambda r: 20.0 <= _sf(r.get("adverse_move_2bar_pct")) < 40.0),
        ("adv2bar_ge_40pct", lambda r: _sf(r.get("adverse_move_2bar_pct")) >= 40.0),
    ]


def _build_interaction_matrix(
    rows: List[Dict[str, Any]],
    clusters: List[Dict[str, Any]],
    legacy_interactions: List[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    min_n = int(cfg.get("min_n", 5))
    top_n = int(cfg.get("matrix_top_n", 40))
    onset_labels = sorted({str(sig) for r in rows for sig in (r.get("onset_signatures") or [])})
    out: List[Dict[str, Any]] = []
    legacy_by_key = {
        (str(r.get("onset_condition")), str(r.get("early_path_condition"))): r
        for r in legacy_interactions
    }
    for onset_label in onset_labels:
        onset_rows = [r for r in rows if onset_label in (r.get("onset_signatures") or [])]
        if len(onset_rows) < min_n:
            continue
        for path_label, pred in _early_path_interaction_conditions():
            matched = [r for r in onset_rows if pred(r)]
            if len(matched) < min_n:
                continue
            metrics = _interaction_group_metrics(matched, rows, clusters, cfg)
            legacy = legacy_by_key.get((onset_label, path_label), {})
            metrics.update({
                "onset_condition": onset_label,
                "early_path_condition": path_label,
                "availability": _early_path_availability(path_label),
                "operational_action": legacy.get("operational_action") or _action_from_metrics(metrics, cfg),
                "action_reason": legacy.get("action_reason") or _action_reason_from_metrics(metrics, cfg),
            })
            metrics["interaction_quality_score"] = _round(_interaction_quality_score(metrics, rows, cfg), 4)
            out.append(metrics)
    out.sort(key=lambda r: (-_sf(r.get("interaction_quality_score")), -int(r.get("n") or 0)))
    return out[:top_n]


def _build_incremental_lift_table(
    rows: List[Dict[str, Any]],
    clusters: List[Dict[str, Any]],
    matrix: List[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    baseline = _interaction_group_metrics(rows, rows, clusters, cfg)
    out: List[Dict[str, Any]] = []
    path_conditions = dict(_early_path_interaction_conditions())
    for row in matrix:
        onset_label = str(row.get("onset_condition"))
        path_label = str(row.get("early_path_condition"))
        pred = path_conditions.get(path_label)
        onset_rows = [r for r in rows if onset_label in (r.get("onset_signatures") or [])]
        path_rows = [r for r in rows if pred and pred(r)]
        onset = _interaction_group_metrics(onset_rows, rows, clusters, cfg)
        path = _interaction_group_metrics(path_rows, rows, clusters, cfg)
        combo = row
        out.append({
            "onset_condition": onset_label,
            "early_path_condition": path_label,
            "n": combo.get("n"),
            "baseline_wr": baseline.get("win_rate"),
            "onset_wr": onset.get("win_rate"),
            "early_path_wr": path.get("win_rate"),
            "interaction_wr": combo.get("win_rate"),
            "delta_wr_vs_baseline_pp": _round(_sf(combo.get("win_rate")) - _sf(baseline.get("win_rate")), 1),
            "delta_wr_vs_onset_pp": _round(_sf(combo.get("win_rate")) - _sf(onset.get("win_rate")), 1),
            "delta_wr_vs_early_path_pp": _round(_sf(combo.get("win_rate")) - _sf(path.get("win_rate")), 1),
            "delta_fail_rate_vs_baseline_pp": _round(_sf(combo.get("fail_rate")) - _sf(baseline.get("fail_rate")), 1),
            "delta_fail_rate_vs_onset_pp": _round(_sf(combo.get("fail_rate")) - _sf(onset.get("fail_rate")), 1),
            "delta_fail_rate_vs_early_path_pp": _round(_sf(combo.get("fail_rate")) - _sf(path.get("fail_rate")), 1),
            "delta_runner_rate_vs_baseline_pp": _round(_sf(combo.get("runner_rate")) - _sf(baseline.get("runner_rate")), 1),
            "delta_runner_rate_vs_onset_pp": _round(_sf(combo.get("runner_rate")) - _sf(onset.get("runner_rate")), 1),
            "delta_runner_rate_vs_early_path_pp": _round(_sf(combo.get("runner_rate")) - _sf(path.get("runner_rate")), 1),
            "delta_expectancy_vs_baseline_ticks": _round(_sf(combo.get("expectancy_ticks")) - _sf(baseline.get("expectancy_ticks")), 3),
            "delta_expectancy_vs_onset_ticks": _round(_sf(combo.get("expectancy_ticks")) - _sf(onset.get("expectancy_ticks")), 3),
            "delta_expectancy_vs_early_path_ticks": _round(_sf(combo.get("expectancy_ticks")) - _sf(path.get("expectancy_ticks")), 3),
            "delta_cluster_participation_vs_baseline_pp": _round(_sf(combo.get("cluster_participation_rate")) - _sf(baseline.get("cluster_participation_rate")), 1),
            "delta_persistence_vs_baseline": _round(_sf(combo.get("median_persistence_signals")) - _sf(baseline.get("median_persistence_signals")), 2),
            "incremental_signal": _incremental_signal_label(combo, onset, path, baseline),
        })
    out.sort(key=lambda r: (-_sf(r.get("delta_expectancy_vs_onset_ticks")), -_sf(r.get("delta_wr_vs_onset_pp")), -int(r.get("n") or 0)))
    return out


def _build_regime_ignition_table(
    rows: List[Dict[str, Any]],
    clusters: List[Dict[str, Any]],
    matrix: List[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    path_conditions = dict(_early_path_interaction_conditions())
    for row in matrix:
        onset_label = str(row.get("onset_condition"))
        path_label = str(row.get("early_path_condition"))
        pred = path_conditions.get(path_label)
        matched = [r for r in rows if onset_label in (r.get("onset_signatures") or []) and pred and pred(r)]
        if not matched:
            continue
        same_regime_next = [_next_signal_same_regime(rows, int(r.get("_row_index") or 0), onset_label, path_label, pred) for r in matched]
        breakdown = [r for r in matched if (not r.get("is_reversal_win")) or bool(r.get("signal_extreme_rebreak_within_2bars"))]
        follow = []
        for r in matched:
            idx = int(r.get("_row_index") or 0)
            c = _first_cluster_in_window(clusters, rows, idx, max_signals=3, max_minutes=60.0, min_length=2)
            follow.append(c is not None)
        ignition = dict(row)
        ignition.update({
            "prob_event_belongs_to_cluster": row.get("cluster_participation_rate"),
            "prob_next_qualifying_signal_same_regime": _round(100.0 * sum(1 for x in same_regime_next if x) / len(same_regime_next), 1) if same_regime_next else None,
            "post_onset_follow_through": _round(100.0 * sum(1 for x in follow if x) / len(follow), 1) if follow else None,
            "contamination_breakdown_rate": _round(100.0 * len(breakdown) / len(matched), 1),
        })
        ignition["regime_ignition_score"] = _round(_regime_ignition_score(ignition, cfg), 4)
        out.append(ignition)
    out.sort(key=lambda r: (-_sf(r.get("regime_ignition_score")), -int(r.get("n") or 0)))
    return out


def _build_regime_decay_table(
    rows: List[Dict[str, Any]],
    strong_keys: List[Tuple[str, str]],
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    path_conditions = dict(_early_path_interaction_conditions())
    for onset_label, path_label in strong_keys:
        pred = path_conditions.get(path_label)
        matched = [r for r in rows if onset_label in (r.get("onset_signatures") or []) and pred and pred(r)]
        slices = [
            ("cluster_trade_1", [r for r in matched if int(r.get("cluster_position") or 0) == 1]),
            ("cluster_trade_2", [r for r in matched if int(r.get("cluster_position") or 0) == 2]),
            ("cluster_trade_later", [r for r in matched if int(r.get("cluster_position") or 0) >= 3]),
            ("minutes_0_10", [r for r in matched if 0 <= _sf(r.get("minutes_since_cluster_start"), -1.0) <= 10]),
            ("minutes_10_30", [r for r in matched if 10 < _sf(r.get("minutes_since_cluster_start"), -1.0) <= 30]),
            ("minutes_30_plus", [r for r in matched if _sf(r.get("minutes_since_cluster_start"), -1.0) > 30]),
            ("after_failed_follow_through", [r for r in matched if _prior_signal_failed(rows, int(r.get("_row_index") or 0))]),
            ("after_rebreak_of_signal_extreme", [r for r in matched if bool(r.get("signal_extreme_rebreak_within_2bars"))]),
            ("after_opposing_large_candle", [r for r in matched if _prior_signal_opposed(rows, int(r.get("_row_index") or 0))]),
        ]
        for decay_bucket, bucket_rows in slices:
            if not bucket_rows:
                continue
            metrics = _interaction_group_metrics(bucket_rows, rows, [], cfg)
            metrics.update({
                "onset_condition": onset_label,
                "early_path_condition": path_label,
                "decay_bucket": decay_bucket,
            })
            metrics["regime_decay_resilience_score"] = _round(_decay_resilience_score(metrics, cfg), 4)
            out.append(metrics)
    out.sort(key=lambda r: (str(r.get("onset_condition")), str(r.get("early_path_condition")), -_sf(r.get("regime_decay_resilience_score"))))
    return out


def _build_first_signal_follow_on_table(
    rows: List[Dict[str, Any]],
    strong_keys: List[Tuple[str, str]],
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    path_conditions = dict(_early_path_interaction_conditions())
    for onset_label, path_label in strong_keys:
        pred = path_conditions.get(path_label)
        matched = [r for r in rows if onset_label in (r.get("onset_signatures") or []) and pred and pred(r)]
        slices = [
            ("first_event_in_cluster", [r for r in matched if int(r.get("cluster_position") or 0) == 1]),
            ("second_signal", [r for r in matched if int(r.get("cluster_position") or 0) == 2]),
            ("later_signals", [r for r in matched if int(r.get("cluster_position") or 0) >= 3]),
        ]
        for signal_role, bucket_rows in slices:
            if not bucket_rows:
                continue
            metrics = _interaction_group_metrics(bucket_rows, rows, [], cfg)
            metrics.update({
                "onset_condition": onset_label,
                "early_path_condition": path_label,
                "signal_role": signal_role,
                "median_time_to_target_minutes": _round(_median([r.get("time_to_max_reversal_min") for r in bucket_rows]), 2),
            })
            out.append(metrics)
    out.sort(key=lambda r: (str(r.get("onset_condition")), str(r.get("early_path_condition")), str(r.get("signal_role"))))
    return out


def _build_interaction_stability_table(
    rows: List[Dict[str, Any]],
    matrix: List[Dict[str, Any]],
    onset_by_label: Dict[str, Dict[str, Any]],
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    top_n = int(cfg.get("stability_top_n", 20))
    split_count = max(2, int(cfg.get("time_split_count", 3)))
    session_warn = float(cfg.get("session_concentration_warning_pct", 70.0))
    small_n = int(cfg.get("small_sample_n", 30))
    instability_warn = float(cfg.get("time_instability_warning_pp", 12.0))
    path_conditions = dict(_early_path_interaction_conditions())
    out: List[Dict[str, Any]] = []
    for row in matrix[:top_n]:
        onset_label = str(row.get("onset_condition"))
        path_label = str(row.get("early_path_condition"))
        pred = path_conditions.get(path_label)
        matched = [r for r in rows if onset_label in (r.get("onset_signatures") or []) and pred and pred(r)]
        sessions: Dict[str, int] = {}
        for r in matched:
            s = str(r.get("session") or "unknown")
            sessions[s] = sessions.get(s, 0) + 1
        dominant_session = max(sessions.items(), key=lambda kv: kv[1])[0] if sessions else "unknown"
        concentration = 100.0 * max(sessions.values()) / len(matched) if matched and sessions else 0.0
        split_rates = _time_split_win_rates(matched, split_count)
        time_range = (max(split_rates) - min(split_rates)) if split_rates else None
        onset_meta = onset_by_label.get(onset_label) or {}
        warnings = []
        if len(matched) < small_n:
            warnings.append("small_sample")
        if concentration >= session_warn:
            warnings.append("session_concentration")
        if time_range is not None and time_range >= instability_warn:
            warnings.append("time_period_instability")
        if onset_meta.get("high_overlap"):
            warnings.append("overlap_warning")
        if onset_meta.get("low_exclusivity"):
            warnings.append("exclusivity_warning")
        if row.get("availability") == "post_entry_2bar_validation":
            warnings.append("post_entry_validation_not_entry_filter")
        if _sf(row.get("win_rate")) >= 70.0 and len(matched) < max(small_n * 2, 60):
            warnings.append("possible_overfit")
        confidence = _confidence_label(len(matched), warnings, row)
        out.append({
            "onset_condition": onset_label,
            "early_path_condition": path_label,
            "total_n": len(matched),
            "unique_days": len({r.get("date") for r in matched if r.get("date")}),
            "dominant_session": dominant_session,
            "session_concentration_pct": _round(concentration, 1),
            "time_split_stability": _round(1.0 - _clamp01((_sf(time_range, 100.0)) / 100.0), 3) if time_range is not None else None,
            "time_split_wr_range_pp": _round(time_range, 1),
            "small_sample_warning": "yes" if "small_sample" in warnings else "no",
            "overlap_warning": "yes" if "overlap_warning" in warnings else "no",
            "exclusivity_warning": "yes" if "exclusivity_warning" in warnings else "no",
            "possible_overfit_warning": "yes" if "possible_overfit" in warnings else "no",
            "warnings": warnings,
            "recommended_confidence_label": confidence,
        })
    return out


def _build_edge_decomposition_table(incremental_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in incremental_rows:
        material = (
            str(r.get("incremental_signal")) == "interaction_adds_regime_signal"
            or (_sf(r.get("delta_expectancy_vs_onset_ticks")) > 0 and _sf(r.get("delta_expectancy_vs_early_path_ticks")) > 0)
        )
        out.append({
            "onset_condition": r.get("onset_condition"),
            "early_path_condition": r.get("early_path_condition"),
            "n": r.get("n"),
            "baseline_wr": r.get("baseline_wr"),
            "onset_wr": r.get("onset_wr"),
            "early_path_wr": r.get("early_path_wr"),
            "interaction_wr": r.get("interaction_wr"),
            "incremental_wr_lift_vs_onset_pp": r.get("delta_wr_vs_onset_pp"),
            "incremental_wr_lift_vs_early_path_pp": r.get("delta_wr_vs_early_path_pp"),
            "incremental_expectancy_lift_vs_onset_ticks": r.get("delta_expectancy_vs_onset_ticks"),
            "incremental_expectancy_lift_vs_early_path_ticks": r.get("delta_expectancy_vs_early_path_ticks"),
            "incremental_cluster_lift_vs_baseline_pp": r.get("delta_cluster_participation_vs_baseline_pp"),
            "incremental_persistence_lift_vs_baseline": r.get("delta_persistence_vs_baseline"),
            "material_interaction_signal": "yes" if material else "no",
            "interpretation": _edge_decomposition_interpretation(r, material),
        })
    return out


def _build_tradeable_regime_candidate_ledger(
    matrix: List[Dict[str, Any]],
    incremental: List[Dict[str, Any]],
    ignition: List[Dict[str, Any]],
    stability: List[Dict[str, Any]],
    decomposition: List[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    inc_by_key = _by_interaction_key(incremental)
    ign_by_key = _by_interaction_key(ignition)
    stab_by_key = _by_interaction_key(stability, onset_key="onset_condition", path_key="early_path_condition")
    decomp_by_key = _by_interaction_key(decomposition)
    rows: List[Dict[str, Any]] = []
    for m in matrix:
        key = (str(m.get("onset_condition")), str(m.get("early_path_condition")))
        inc = inc_by_key.get(key) or {}
        ign = ign_by_key.get(key) or {}
        stab = stab_by_key.get(key) or {}
        decomp = decomp_by_key.get(key) or {}
        warnings = list(stab.get("warnings") or [])
        action, reasons = _candidate_action(m, inc, ign, stab, decomp, warnings, cfg)
        confidence = _candidate_confidence(m, stab, inc, action, warnings, cfg)
        candidate = {
            "candidate_name": _candidate_name(key[0], key[1]),
            "onset_condition": key[0],
            "early_path_condition": key[1],
            "when_condition_is_known": _when_condition_is_known(str(m.get("availability") or "")),
            "candidate_action": action,
            "n": m.get("n"),
            "unique_days": stab.get("unique_days"),
            "dominant_session": stab.get("dominant_session"),
            "win_rate": m.get("win_rate"),
            "fail_rate": m.get("fail_rate"),
            "runner_rate": m.get("runner_rate"),
            "expectancy_ticks": m.get("expectancy_ticks"),
            "mfe_mae_ratio": m.get("mfe_mae_ratio"),
            "cluster_participation_rate": m.get("cluster_participation_rate"),
            "median_cluster_length": m.get("median_cluster_length"),
            "median_decay_minutes": m.get("median_decay_minutes"),
            "time_split_stability": stab.get("time_split_stability"),
            "confidence_label": confidence,
            "rejection_or_caution_reason": "; ".join(reasons or ["passes conservative research screen"]),
            "practical_rule_note": _practical_rule_note(action, m),
            "interaction_quality_score": m.get("interaction_quality_score"),
            "regime_ignition_score": ign.get("regime_ignition_score"),
            "material_interaction_signal": decomp.get("material_interaction_signal"),
            "incremental_expectancy_lift_vs_onset_ticks": inc.get("delta_expectancy_vs_onset_ticks"),
            "incremental_cluster_lift_vs_baseline_pp": inc.get("delta_cluster_participation_vs_baseline_pp"),
            "warnings": warnings,
        }
        rows.append(candidate)
    rows.sort(key=lambda r: (_candidate_action_rank(str(r.get("candidate_action"))), -_sf(r.get("regime_ignition_score")), -_sf(r.get("interaction_quality_score")), -int(r.get("n") or 0)))
    return rows


def _build_action_decision_tables(
    ledger: List[Dict[str, Any]],
    decay_table: List[Dict[str, Any]],
    first_follow: List[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    tables: Dict[str, List[Dict[str, Any]]] = {
        "hold_candidates": [],
        "scalp_candidates": [],
        "scratch_reduce_risk_candidates": [],
        "flip_watch_candidates": [],
        "re_entry_candidates": [],
        "regime_shutdown_candidates": [],
        "avoid_or_rejected": [],
    }
    for row in ledger:
        entry = _action_table_row(row)
        action = str(row.get("candidate_action") or "")
        if action == "hold":
            tables["hold_candidates"].append(entry)
        elif action == "scalp":
            tables["scalp_candidates"].append(entry)
        elif action == "scratch":
            tables["scratch_reduce_risk_candidates"].append(entry)
        elif action == "flip_watch":
            tables["flip_watch_candidates"].append(entry)
        elif action == "regime_shutdown":
            tables["regime_shutdown_candidates"].append(entry)
        else:
            tables["avoid_or_rejected"].append(entry)

    for row in _derive_re_entry_candidates(first_follow, ledger):
        tables["re_entry_candidates"].append(row)
    for row in _derive_shutdown_candidates(decay_table, ledger, cfg):
        if row not in tables["regime_shutdown_candidates"]:
            tables["regime_shutdown_candidates"].append(row)
    return tables


def _build_live_decision_readiness_screen(ledger: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in ledger:
        path = str(row.get("early_path_condition") or "")
        availability = "post_entry_2bar_validation" if _early_path_availability(path) == "post_entry_2bar_validation" else "decision_time"
        uses_post = availability == "post_entry_2bar_validation"
        requires_cluster = str(row.get("candidate_action")) in {"re_entry_watch", "regime_shutdown"}
        requires_future = str(row.get("candidate_action")) in {"regime_shutdown"} or "cluster" in str(row.get("practical_rule_note") or "").lower()
        if requires_future:
            label = "research_only"
        elif uses_post:
            label = "management_rule_only"
        else:
            label = "immediately_actionable"
        out.append({
            "candidate_name": row.get("candidate_name"),
            "onset_condition": row.get("onset_condition"),
            "early_path_condition": path,
            "uses_only_decision_time_features": "no" if uses_post or requires_cluster or requires_future else "yes",
            "uses_post_entry_validation": "yes" if uses_post else "no",
            "bars_until_known": 2 if uses_post else 0,
            "requires_cluster_context": "yes" if requires_cluster else "no",
            "requires_future_outcome_label": "yes" if requires_future else "no",
            "live_actionability_label": label,
            "leakage_warning": _leakage_warning(label, uses_post, requires_future),
        })
    return out


def _build_failure_mode_audit(
    ledger: List[Dict[str, Any]],
    matrix: List[Dict[str, Any]],
    stability: List[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    matrix_by_key = _by_interaction_key(matrix)
    stability_by_key = _by_interaction_key(stability)
    out: List[Dict[str, Any]] = []
    for c in ledger:
        key = (str(c.get("onset_condition")), str(c.get("early_path_condition")))
        m = matrix_by_key.get(key) or {}
        s = stability_by_key.get(key) or {}
        modes = []
        path = str(c.get("early_path_condition") or "")
        if _sf(c.get("fail_rate")) >= float(cfg.get("flip_watch_min_failure_rate", 40.0)):
            modes.append("immediate_adverse_or_failed_reversal")
        if path in {"midpoint_reclaim_no", "weak_start", "fav2bar_lt_15pct"}:
            modes.append("no_midpoint_reclaim_or_weak_start")
        if path in {"rebreak_yes", "weak_start", "adv2bar_ge_40pct"}:
            modes.append("rebreak_of_signal_extreme")
        if _sf(c.get("median_decay_minutes"), 999.0) <= float(cfg.get("fast_decay_minutes", 20.0)):
            modes.append("fast_decay")
        if _sf(s.get("session_concentration_pct")) >= float(cfg.get("max_session_concentration_pct", 75.0)):
            modes.append("session_concentration")
        if _sf(s.get("time_split_stability"), 1.0) < float(cfg.get("min_time_split_stability", 0.45)):
            modes.append("time_period_instability")
        if "overlap_warning" in (s.get("warnings") or []):
            modes.append("overlap_low_independence")
        if "small_sample" in (s.get("warnings") or []):
            modes.append("small_sample_inflation")
        out.append({
            "candidate_name": c.get("candidate_name"),
            "candidate_action": c.get("candidate_action"),
            "n": c.get("n"),
            "dominant_failure_modes": modes or ["no_dominant_failure_mode_flagged"],
            "fail_rate": c.get("fail_rate"),
            "median_decay_minutes": c.get("median_decay_minutes"),
            "session_concentration_pct": s.get("session_concentration_pct"),
            "time_split_stability": s.get("time_split_stability"),
            "recommended_interpretation": _failure_interpretation(c, modes, m),
        })
    return out


def _build_executive_summary_payload(
    ledger: List[Dict[str, Any]],
    action_tables: Dict[str, List[Dict[str, Any]]],
    failure_audit: List[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    promoted = [r for r in ledger if str(r.get("candidate_action")) not in {"avoid", "regime_shutdown"} and str(r.get("confidence_label")) not in {"very_fragile"}]
    shutdowns = action_tables.get("regime_shutdown_candidates") or []
    payload = {
        "title": "Large Candle Regime Discovery Executive Summary",
        "top_tradeable_candidates": promoted[:3],
        "best_hold_candidate": _first(action_tables.get("hold_candidates") or []),
        "best_scalp_candidate": _first(action_tables.get("scalp_candidates") or []),
        "best_flip_watch_candidate": _first(action_tables.get("flip_watch_candidates") or []),
        "strongest_regime_shutdown_warning": _first(shutdowns),
        "top_fragility_warnings": failure_audit[:5],
        "next_recommended_tests": _next_recommended_tests(promoted, shutdowns),
        "no_candidate_reason": None,
        "what_this_does_not_prove": [
            "This is not causal proof that the onset creates the reversal.",
            "Post-entry early-path labels are management inputs, not initial-entry filters.",
            "Cluster participation and decay statistics are historical regime-quality descriptors.",
            "Any candidate still needs out-of-sample and paper-trading validation before live use.",
        ],
    }
    if not promoted:
        payload["no_candidate_reason"] = "No onset x early-path interaction passed the conservative tradeability screen without major fragility warnings."
    return payload


def _interaction_group_metrics(
    group_rows: List[Dict[str, Any]],
    all_rows: List[Dict[str, Any]],
    clusters: List[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    n = len(group_rows)
    failure_pct = float(cfg.get("failure_reversal_pct", 25.0))
    scalp_pct = float(cfg.get("scalp_reversal_pct", 50.0))
    expansion_pct = float(cfg.get("expansion_reversal_pct", 50.0))
    runner_pct = float(cfg.get("runner_reversal_pct", 100.0))
    cluster_by_id = {i + 1: c for i, c in enumerate(clusters or [])}
    cluster_lengths = []
    decay_times = []
    for r in group_rows:
        cid = int(r.get("cluster_id") or 0)
        if cid and cid in cluster_by_id:
            cluster_lengths.append(cluster_by_id[cid].get("length"))
            decay_times.append(cluster_by_id[cid].get("time_to_decay_minutes"))
        elif r.get("cluster_length"):
            cluster_lengths.append(r.get("cluster_length"))
            decay_times.append(r.get("cluster_time_to_decay_minutes"))
    mfe_values = [r.get("reversal_mfe_pct") for r in group_rows]
    mae_values = [r.get("reversal_mae_pct") for r in group_rows]
    avg_mae = _avg(mae_values)
    persistence = [_persistence_after_onset(all_rows, int(r.get("_row_index") or 0), max_signals=10) for r in group_rows] if all_rows else []
    return {
        "n": n,
        "win_rate": _round(_win_rate(group_rows), 1),
        "fail_rate": _round(100.0 * sum(1 for r in group_rows if _sf(r.get("reversal_mfe_pct")) <= failure_pct) / n, 1) if n else 0.0,
        "scalp_rate": _round(100.0 * sum(1 for r in group_rows if failure_pct < _sf(r.get("reversal_mfe_pct")) < scalp_pct) / n, 1) if n else 0.0,
        "expansion_rate": _round(100.0 * sum(1 for r in group_rows if _sf(r.get("reversal_mfe_pct")) >= expansion_pct) / n, 1) if n else 0.0,
        "runner_rate": _round(100.0 * sum(1 for r in group_rows if _sf(r.get("reversal_mfe_pct")) >= runner_pct) / n, 1) if n else 0.0,
        "expectancy_ticks": _round(_avg([r.get("expectancy_ticks") for r in group_rows]), 3),
        "avg_mfe_pct": _round(_avg(mfe_values), 2),
        "avg_mae_pct": _round(avg_mae, 2),
        "mfe_mae_ratio": _round((_avg(mfe_values) or 0.0) / max(avg_mae or 0.0, 1.0), 3),
        "cluster_participation_rate": _round(100.0 * sum(1 for r in group_rows if r.get("inside_win_cluster")) / n, 1) if n else 0.0,
        "median_cluster_length": _round(_median(cluster_lengths), 2),
        "median_persistence_signals": _round(_median(persistence), 2),
        "median_decay_minutes": _round(_median(decay_times), 2),
        "first_signal_count": sum(1 for r in group_rows if int(r.get("cluster_position") or 0) == 1),
        "follow_on_count": sum(1 for r in group_rows if int(r.get("cluster_position") or 0) >= 2),
        "median_time_to_target_minutes": _round(_median([r.get("time_to_max_reversal_min") for r in group_rows]), 2),
    }


def _strong_interaction_keys(
    ignition_table: List[Dict[str, Any]],
    matrix: List[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> List[Tuple[str, str]]:
    strong_n = int(cfg.get("strong_min_n", 20))
    top_n = int(cfg.get("top_filter_n", 5))
    source = ignition_table or matrix
    keys: List[Tuple[str, str]] = []
    for row in source:
        if int(row.get("n") or 0) < strong_n and len(keys) >= top_n:
            continue
        key = (str(row.get("onset_condition")), str(row.get("early_path_condition")))
        if key not in keys:
            keys.append(key)
        if len(keys) >= top_n:
            break
    return keys


def _interaction_quality_score(row: Dict[str, Any], all_rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> float:
    baseline_wr = _win_rate(all_rows)
    baseline_exp = _avg([r.get("expectancy_ticks") for r in all_rows]) or 0.0
    sample = _sample_score(int(row.get("n") or 0), int(cfg.get("small_sample_n", 30)))
    wr_lift = _clamp01((_sf(row.get("win_rate")) - baseline_wr + 15.0) / 30.0)
    exp_lift = _clamp01((_sf(row.get("expectancy_ticks")) - baseline_exp + 20.0) / 40.0)
    runner = _clamp01(_sf(row.get("runner_rate")) / 40.0)
    cluster = _clamp01(_sf(row.get("cluster_participation_rate")) / 60.0)
    ratio = _clamp01((_sf(row.get("mfe_mae_ratio")) - 0.8) / 1.2)
    penalty = _fragility_penalty(row, cfg)
    return _clamp01(0.20 * sample + 0.22 * wr_lift + 0.22 * exp_lift + 0.14 * runner + 0.14 * cluster + 0.08 * ratio - penalty)


def _regime_ignition_score(row: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    cluster = _clamp01(_sf(row.get("prob_event_belongs_to_cluster")) / 70.0)
    same = _clamp01(_sf(row.get("prob_next_qualifying_signal_same_regime")) / 70.0)
    follow = _clamp01(_sf(row.get("post_onset_follow_through")) / 70.0)
    length = _clamp01(_sf(row.get("median_cluster_length")) / 5.0)
    decay = _clamp01(_sf(row.get("median_decay_minutes")) / 60.0)
    breakdown_penalty = _clamp01(_sf(row.get("contamination_breakdown_rate")) / 70.0) * 0.20
    fragility = _fragility_penalty(row, cfg)
    return _clamp01(0.24 * cluster + 0.18 * same + 0.20 * follow + 0.16 * length + 0.10 * decay + 0.12 * _sample_score(int(row.get("n") or 0), int(cfg.get("small_sample_n", 30))) - breakdown_penalty - fragility)


def _decay_resilience_score(row: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    wr = _clamp01(_sf(row.get("win_rate")) / 75.0)
    exp = _clamp01((_sf(row.get("expectancy_ticks")) + 20.0) / 50.0)
    mae = 1.0 - _clamp01(_sf(row.get("avg_mae_pct")) / 100.0)
    runner = _clamp01(_sf(row.get("runner_rate")) / 40.0)
    fail_penalty = _clamp01(_sf(row.get("fail_rate")) / 60.0) * 0.20
    return _clamp01(0.26 * wr + 0.26 * exp + 0.20 * mae + 0.16 * runner + 0.12 * _sample_score(int(row.get("n") or 0), int(cfg.get("small_sample_n", 30))) - fail_penalty)


def _sample_score(n: int, target_n: int) -> float:
    return _clamp01(n / max(1.0, float(target_n)))


def _fragility_penalty(row: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    n = int(row.get("n") or row.get("total_n") or 0)
    small_n = int(cfg.get("small_sample_n", 30))
    penalty = 0.0
    if n < small_n:
        penalty += 0.12 * (1.0 - _sample_score(n, small_n))
    if _sf(row.get("fail_rate")) >= 45.0:
        penalty += 0.06
    if row.get("availability") == "post_entry_2bar_validation":
        penalty += 0.02
    return penalty


def _incremental_signal_label(combo: Dict[str, Any], onset: Dict[str, Any], path: Dict[str, Any], baseline: Dict[str, Any]) -> str:
    wr_gain = min(_sf(combo.get("win_rate")) - _sf(onset.get("win_rate")), _sf(combo.get("win_rate")) - _sf(path.get("win_rate")))
    exp_gain = min(_sf(combo.get("expectancy_ticks")) - _sf(onset.get("expectancy_ticks")), _sf(combo.get("expectancy_ticks")) - _sf(path.get("expectancy_ticks")))
    cluster_gain = _sf(combo.get("cluster_participation_rate")) - _sf(baseline.get("cluster_participation_rate"))
    if wr_gain >= 5.0 and exp_gain > 0 and cluster_gain >= 5.0:
        return "interaction_adds_regime_signal"
    if wr_gain >= 3.0 or exp_gain > 0:
        return "modest_incremental_signal"
    return "mostly_explained_by_parts"


def _early_path_availability(path_label: str) -> str:
    if path_label.startswith(("fav2bar", "adv2bar")) or path_label in {
        "explosive_start", "orderly_start", "weak_start", "mixed_start",
        "midpoint_reclaim_yes", "midpoint_reclaim_no", "rebreak_yes", "rebreak_no",
    }:
        return "post_entry_2bar_validation"
    return "decision_time"


def _action_from_metrics(row: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    if _sf(row.get("fail_rate")) >= 45.0:
        return "flip_watch"
    if _sf(row.get("expectancy_ticks")) <= 0 and _sf(row.get("win_rate")) < 50:
        return "avoid"
    if _sf(row.get("runner_rate")) >= 25.0 and _sf(row.get("cluster_participation_rate")) >= 35.0:
        return "reversal_hold"
    if _sf(row.get("expansion_rate")) >= 50.0:
        return "reversal_only_if_clean_reclaim"
    return "scalp_only"


def _action_reason_from_metrics(row: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    reasons = []
    if _sf(row.get("runner_rate")) >= 25.0:
        reasons.append("runner participation")
    if _sf(row.get("cluster_participation_rate")) >= 35.0:
        reasons.append("cluster participation")
    if _sf(row.get("fail_rate")) >= 45.0:
        reasons.append("failure risk")
    if row.get("availability") == "post_entry_2bar_validation":
        reasons.append("post-entry validation")
    return ", ".join(reasons or ["descriptive only"])


def _next_signal_same_regime(
    rows: List[Dict[str, Any]],
    idx: int,
    onset_label: str,
    path_label: str,
    pred: Optional[Callable[[Dict[str, Any]], bool]],
) -> bool:
    if pred is None:
        return False
    for r in rows[idx + 1:]:
        if onset_label not in (r.get("onset_signatures") or []):
            continue
        return pred(r)
    return False


def _prior_signal_failed(rows: List[Dict[str, Any]], idx: int) -> bool:
    if idx <= 0 or idx >= len(rows):
        return False
    prev = rows[idx - 1]
    return not bool(prev.get("is_reversal_win"))


def _prior_signal_opposed(rows: List[Dict[str, Any]], idx: int) -> bool:
    if idx <= 0 or idx >= len(rows):
        return False
    prev = rows[idx - 1]
    return bool(prev.get("signal_direction") and prev.get("signal_direction") != rows[idx].get("signal_direction"))


def _time_split_win_rates(rows: List[Dict[str, Any]], split_count: int) -> List[float]:
    if not rows:
        return []
    ordered = sorted(rows, key=lambda r: r.get("_dt") or _parse_dt(r.get("dt")) or datetime.min)
    splits: List[List[Dict[str, Any]]] = []
    for i in range(split_count):
        start = int(len(ordered) * i / split_count)
        end = int(len(ordered) * (i + 1) / split_count)
        part = ordered[start:end]
        if part:
            splits.append(part)
    return [_win_rate(part) for part in splits if part]


def _confidence_label(n: int, warnings: List[str], row: Dict[str, Any]) -> str:
    if n < 10 or "possible_overfit" in warnings:
        return "very_fragile"
    if "small_sample" in warnings or "time_period_instability" in warnings:
        return "tentative"
    if "session_concentration" in warnings or "overlap_warning" in warnings or "exclusivity_warning" in warnings:
        return "conditional"
    if _sf(row.get("interaction_quality_score")) >= 0.65:
        return "research_candidate"
    return "monitor"


def _by_interaction_key(rows: List[Dict[str, Any]], onset_key: str = "onset_condition", path_key: str = "early_path_condition") -> Dict[Tuple[str, str], Dict[str, Any]]:
    return {(str(r.get(onset_key)), str(r.get(path_key))): r for r in rows}


def _candidate_name(onset: str, path: str) -> str:
    return f"{onset} x {path}"


def _when_condition_is_known(availability: str) -> str:
    if availability == "post_entry_2bar_validation":
        return "after_2_post_entry_bars"
    return "at_signal_close"


def _candidate_action(
    metrics: Dict[str, Any],
    incremental: Dict[str, Any],
    ignition: Dict[str, Any],
    stability: Dict[str, Any],
    decomposition: Dict[str, Any],
    warnings: List[str],
    cfg: Dict[str, Any],
) -> Tuple[str, List[str]]:
    reasons = _candidate_rejection_reasons(metrics, incremental, stability, decomposition, warnings, cfg)
    fail = _sf(metrics.get("fail_rate"))
    exp = _sf(metrics.get("expectancy_ticks"))
    cluster = _sf(metrics.get("cluster_participation_rate"))
    runner = _sf(metrics.get("runner_rate"))
    decay = _sf(metrics.get("median_decay_minutes"), 999.0)
    path = str(metrics.get("early_path_condition") or "")

    if fail >= float(cfg.get("shutdown_min_fail_rate", 45.0)) and exp <= float(cfg.get("shutdown_max_expectancy_ticks", 0.0)):
        return "regime_shutdown", reasons or ["negative expectancy with high failure rate"]
    if path in {"weak_start", "rebreak_yes", "midpoint_reclaim_no", "adv2bar_ge_40pct"} and fail >= float(cfg.get("flip_watch_min_failure_rate", 40.0)):
        return "flip_watch", reasons or ["weak/rebreak path with high failure rate"]
    if reasons:
        if exp > float(cfg.get("scalp_min_expectancy_ticks", 0.0)) and fail < float(cfg.get("shutdown_min_fail_rate", 45.0)):
            return "scalp", reasons
        return "avoid", reasons
    if (
        exp > float(cfg.get("hold_min_expectancy_ticks", 0.0))
        and fail <= float(cfg.get("hold_max_fail_rate", 35.0))
        and cluster >= float(cfg.get("hold_min_cluster_participation", 25.0))
        and (runner >= 15.0 or decay >= float(cfg.get("fast_decay_minutes", 20.0)))
    ):
        return "hold", ["positive expectancy, cluster participation, and tolerable failure profile"]
    if exp > float(cfg.get("scalp_min_expectancy_ticks", 0.0)) or _sf(metrics.get("win_rate")) >= 55.0:
        return "scalp", ["positive short-horizon quality but not enough regime persistence for hold"]
    if fail >= 35.0 or decay <= float(cfg.get("fast_decay_minutes", 20.0)):
        return "scratch", ["quality degrades enough that holding is unattractive"]
    return "avoid", ["insufficient edge after conservative screen"]


def _candidate_rejection_reasons(
    metrics: Dict[str, Any],
    incremental: Dict[str, Any],
    stability: Dict[str, Any],
    decomposition: Dict[str, Any],
    warnings: List[str],
    cfg: Dict[str, Any],
) -> List[str]:
    reasons: List[str] = []
    n = int(metrics.get("n") or 0)
    if n < int(cfg.get("candidate_min_n", 30)):
        reasons.append("candidate N below threshold")
    days = int(stability.get("unique_days") or 0)
    if days and days < int(cfg.get("candidate_min_unique_days", 5)):
        reasons.append("too few unique days")
    if _sf(stability.get("session_concentration_pct")) >= float(cfg.get("max_session_concentration_pct", 75.0)):
        reasons.append("session concentration")
    if _sf(stability.get("time_split_stability"), 1.0) < float(cfg.get("min_time_split_stability", 0.45)):
        reasons.append("time instability")
    if "possible_overfit" in warnings:
        reasons.append("possible overfit")
    if "overlap_warning" in warnings and "exclusivity_warning" in warnings:
        reasons.append("onset overlap / low exclusivity")
    if bool(cfg.get("require_incremental_lift", True)):
        exp_lift = _sf(incremental.get("delta_expectancy_vs_onset_ticks"))
        cluster_lift = _sf(incremental.get("delta_cluster_participation_vs_baseline_pp"))
        material = str(decomposition.get("material_interaction_signal")) == "yes"
        if exp_lift <= float(cfg.get("min_incremental_expectancy_lift_ticks", 0.0)) and cluster_lift < float(cfg.get("min_incremental_cluster_lift_pp", 3.0)) and not material:
            reasons.append("interaction does not add material lift")
    return reasons


def _candidate_confidence(
    metrics: Dict[str, Any],
    stability: Dict[str, Any],
    incremental: Dict[str, Any],
    action: str,
    warnings: List[str],
    cfg: Dict[str, Any],
) -> str:
    if action == "avoid" or "possible_overfit" in warnings:
        return "rejected"
    if int(metrics.get("n") or 0) < int(cfg.get("candidate_min_n", 30)):
        return "very_fragile"
    if "small_sample" in warnings or "time_period_instability" in warnings:
        return "tentative"
    if "session_concentration" in warnings or "overlap_warning" in warnings or "exclusivity_warning" in warnings:
        return "conditional"
    if _sf(incremental.get("delta_expectancy_vs_onset_ticks")) > 0 and _sf(metrics.get("interaction_quality_score")) >= 0.60:
        return "paper_test_candidate"
    return "research_candidate"


def _candidate_action_rank(action: str) -> int:
    ranks = {
        "hold": 0,
        "scalp": 1,
        "scratch": 2,
        "flip_watch": 3,
        "re_entry_watch": 4,
        "regime_shutdown": 5,
        "avoid": 6,
    }
    return ranks.get(action, 9)


def _practical_rule_note(action: str, row: Dict[str, Any]) -> str:
    path = str(row.get("early_path_condition") or "")
    availability = _when_condition_is_known(str(row.get("availability") or ""))
    if action == "hold":
        return f"Consider holding only after {path} is confirmed ({availability}); monitor decay and rebreak."
    if action == "scalp":
        return f"Treat {path} as a management cue for quicker profit-taking; do not assume persistence."
    if action == "scratch":
        return f"If {path} appears, reduce risk or scratch unless follow-through improves immediately."
    if action == "flip_watch":
        return f"If reversal fails under {path}, watch for continuation/flip confirmation before re-entering."
    if action == "regime_shutdown":
        return "Historical decay profile says stop taking follow-on reversals after this state without fresh validation."
    return "Research-only or rejected until more robust evidence appears."


def _action_table_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "candidate_name": row.get("candidate_name"),
        "n": row.get("n"),
        "win_rate": row.get("win_rate"),
        "fail_rate": row.get("fail_rate"),
        "runner_rate": row.get("runner_rate"),
        "expectancy_ticks": row.get("expectancy_ticks"),
        "cluster_participation_rate": row.get("cluster_participation_rate"),
        "median_decay_minutes": row.get("median_decay_minutes"),
        "action_rationale": row.get("rejection_or_caution_reason"),
        "confidence_label": row.get("confidence_label"),
    }


def _derive_re_entry_candidates(first_follow: List[Dict[str, Any]], ledger: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], Dict[str, Dict[str, Any]]] = {}
    for r in first_follow:
        grouped.setdefault((str(r.get("onset_condition")), str(r.get("early_path_condition"))), {})[str(r.get("signal_role"))] = r
    ledger_by_key = _by_interaction_key(ledger)
    out: List[Dict[str, Any]] = []
    for key, parts in grouped.items():
        first = parts.get("first_event_in_cluster")
        later = parts.get("second_signal") or parts.get("later_signals")
        if not first or not later:
            continue
        if _sf(later.get("expectancy_ticks")) > _sf(first.get("expectancy_ticks")) and _sf(later.get("win_rate")) >= _sf(first.get("win_rate")):
            base = ledger_by_key.get(key) or {}
            out.append({
                "candidate_name": _candidate_name(*key),
                "n": later.get("n"),
                "win_rate": later.get("win_rate"),
                "fail_rate": later.get("fail_rate"),
                "runner_rate": later.get("runner_rate"),
                "expectancy_ticks": later.get("expectancy_ticks"),
                "cluster_participation_rate": base.get("cluster_participation_rate"),
                "median_decay_minutes": base.get("median_decay_minutes"),
                "action_rationale": "follow-on signal quality improves versus first cluster signal",
                "confidence_label": base.get("confidence_label") or "research_candidate",
            })
    return out


def _derive_shutdown_candidates(decay_table: List[Dict[str, Any]], ledger: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    ledger_by_key = _by_interaction_key(ledger)
    for r in decay_table:
        if _sf(r.get("expectancy_ticks")) <= float(cfg.get("shutdown_max_expectancy_ticks", 0.0)) and _sf(r.get("fail_rate")) >= float(cfg.get("shutdown_min_fail_rate", 45.0)):
            key = (str(r.get("onset_condition")), str(r.get("early_path_condition")))
            base = ledger_by_key.get(key) or {}
            out.append({
                "candidate_name": f"{_candidate_name(*key)} after {r.get('decay_bucket')}",
                "n": r.get("n"),
                "win_rate": r.get("win_rate"),
                "fail_rate": r.get("fail_rate"),
                "runner_rate": r.get("runner_rate"),
                "expectancy_ticks": r.get("expectancy_ticks"),
                "cluster_participation_rate": base.get("cluster_participation_rate"),
                "median_decay_minutes": base.get("median_decay_minutes"),
                "action_rationale": "decay bucket has poor expectancy and high failure rate",
                "confidence_label": base.get("confidence_label") or "research_candidate",
            })
    return out[:10]


def _leakage_warning(label: str, uses_post: bool, requires_future: bool) -> str:
    if requires_future:
        return "Uses cluster/decay outcome context; use for research or shutdown diagnostics, not entry-time signals."
    if uses_post:
        return "Uses early post-entry bars; valid as management logic, not as initial entry filter."
    if label == "immediately_actionable":
        return "No leakage flag from available fields, but validate out of sample."
    return "Review feature timing before live use."


def _failure_interpretation(candidate: Dict[str, Any], modes: List[str], matrix_row: Dict[str, Any]) -> str:
    action = str(candidate.get("candidate_action") or "")
    if "small_sample_inflation" in modes:
        return "needs more data"
    if "time_period_instability" in modes or "session_concentration" in modes:
        return "management-only clue"
    if action in {"hold", "scalp"} and not modes:
        return "tradeable edge candidate"
    if action == "regime_shutdown":
        return "avoid / shut down"
    if action == "flip_watch":
        return "management-only clue"
    if _sf(matrix_row.get("interaction_quality_score")) < 0.45:
        return "likely overfit"
    return "needs more data"


def _edge_decomposition_interpretation(row: Dict[str, Any], material: bool) -> str:
    if material:
        return "interaction adds signal beyond onset-only and early-path-only baselines"
    if _sf(row.get("delta_wr_vs_early_path_pp")) <= 0 and _sf(row.get("delta_expectancy_vs_early_path_ticks")) <= 0:
        return "mostly explained by early-path baseline"
    if _sf(row.get("delta_wr_vs_onset_pp")) <= 0 and _sf(row.get("delta_expectancy_vs_onset_ticks")) <= 0:
        return "mostly explained by onset baseline"
    return "mixed or modest incremental lift"


def _first(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return rows[0] if rows else None


def _next_recommended_tests(promoted: List[Dict[str, Any]], shutdowns: List[Dict[str, Any]]) -> List[str]:
    tests = []
    if promoted:
        tests.append("Paper-test the top candidate with entry-time onset plus post-entry early-path management, not as a pure entry filter.")
        tests.append("Run an out-of-sample split focused on the top candidate's dominant session and non-dominant sessions separately.")
    if shutdowns:
        tests.append("Backtest a regime-shutdown rule that blocks follow-on reversals after the strongest decay warning.")
    tests.append("Compare candidate behavior after commissions/slippage and minimum target constraints.")
    return tests


def _operational_action_for_interaction(row: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[str, List[str]]:
    onset = row.get("onset") or {}
    reasons: List[str] = []
    wr = _sf(row.get("win_rate"))
    exp = _sf(row.get("expectancy_ticks"))
    clean = _sf(row.get("avg_cleanliness"))
    lift = _sf(row.get("win_rate_lift_vs_onset_pp"))
    exp_lift = _sf(row.get("expectancy_lift_vs_onset_ticks"))
    fail_rate = _sf(row.get("failure_rate"))
    decay = _sf(row.get("median_time_to_decay_minutes"), 999.0)
    follow = _sf(onset.get("cluster_follow_through_rate"))
    flip_rate = _sf((row.get("failure_flip") or {}).get("strict_continuation_flip_rate"))
    path = str(row.get("path_condition") or "")

    if lift <= float(cfg.get("weak_lift_pp", 0.0)) and exp_lift <= float(cfg.get("good_expectancy_lift_ticks", 0.0)):
        reasons.append("weak lift")
    if onset.get("high_overlap"):
        reasons.append("high overlap")
    if onset.get("low_exclusivity"):
        reasons.append("low exclusivity")
    if decay <= float(cfg.get("fast_decay_minutes", 20.0)):
        reasons.append("fast decay")
    if follow <= float(cfg.get("poor_follow_through_pct", 35.0)):
        reasons.append("poor follow-through")
    if flip_rate >= float(cfg.get("failure_dominance_rate_pct", 55.0)) and fail_rate >= 35.0:
        reasons.append("failure-to-flip dominance")
    if path in {"explosive_start", "orderly_start", "midpoint_reclaim_yes", "rebreak_no"} and clean >= float(cfg.get("clean_reclaim_min_cleanliness", 65.0)):
        reasons.append("good early-path confirmation")

    if "failure-to-flip dominance" in reasons and path in {"weak_start", "rebreak_yes", "midpoint_reclaim_no"}:
        return "continuation_preferred", reasons
    if path in {"weak_start", "rebreak_yes"} and fail_rate >= 40.0:
        return "flip_watch", reasons or ["failure-to-flip dominance"]
    if "poor follow-through" in reasons and "good early-path confirmation" not in reasons:
        return "scalp_only", reasons
    if "low exclusivity" in reasons and "good early-path confirmation" not in reasons:
        return "scalp_only", reasons
    if exp <= 0 and lift <= 0 and clean < 50:
        return "avoid", reasons or ["weak lift"]
    if path == "midpoint_reclaim_no" and clean >= 50:
        return "reversal_only_if_clean_reclaim", reasons or ["good early-path confirmation"]
    if clean >= float(cfg.get("hold_min_cleanliness", 72.0)) and wr >= float(cfg.get("hold_min_win_rate", 55.0)) and exp > 0:
        return "reversal_hold", reasons or ["good early-path confirmation"]
    if clean >= float(cfg.get("clean_reclaim_min_cleanliness", 65.0)) and exp > 0:
        return "reversal_only_if_clean_reclaim", reasons or ["good early-path confirmation"]
    if exp > 0 or wr >= 50:
        return "scalp_only", reasons or ["weak lift"]
    return "avoid", reasons or ["weak lift"]


def _action_rank(action: str) -> int:
    ranks = {
        "reversal_hold": 0,
        "reversal_only_if_clean_reclaim": 1,
        "scalp_only": 2,
        "flip_watch": 3,
        "continuation_preferred": 4,
        "avoid": 5,
    }
    return ranks.get(action, 9)


def _top_decay_driver_by_onset(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for row in rows:
        onset = str(row.get("onset_condition") or "unclassified")
        if onset not in out:
            out[onset] = str(row.get("decay_driver") or "")
    return out


def _onset_selectivity_interpretation(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {
            "summary": "No onset signatures met the minimum sample threshold.",
            "overlap_interpretation": "No overlap can be assessed.",
            "exclusivity_interpretation": "No exclusivity can be assessed.",
        }
    avg_overlap = _avg([r.get("overlap_pct") for r in rows]) or 0.0
    avg_exclusive = _avg([r.get("exclusivity_pct") for r in rows]) or 0.0
    high_overlap_count = sum(1 for r in rows if r.get("high_overlap"))
    low_exclusive_count = sum(1 for r in rows if r.get("low_exclusivity"))
    if avg_overlap >= 60.0:
        overlap_text = "Onset signatures are mostly overlapping views of the same event pool; validate with early-path confirmation before treating them as separate regimes."
    elif avg_overlap >= 30.0:
        overlap_text = "Onset signatures have moderate overlap; some are related state descriptions while others may be distinct regime identifiers."
    else:
        overlap_text = "Onset signatures are mostly distinct event pools; differences in follow-through and decay are more likely to be operationally meaningful."
    if avg_exclusive <= 40.0:
        exclusive_text = "Low exclusivity means many events qualify for multiple onset labels, so action rules should prefer the onset-plus-validation interaction rows."
    else:
        exclusive_text = "Healthy exclusivity means several onset labels can be reviewed as standalone regime identifiers, then refined by early-path state."
    return {
        "summary": f"Average overlap {avg_overlap:.1f}%, average exclusivity {avg_exclusive:.1f}%. High-overlap labels: {high_overlap_count}; low-exclusivity labels: {low_exclusive_count}.",
        "overlap_interpretation": overlap_text,
        "exclusivity_interpretation": exclusive_text,
    }


def _decay_signature_summary(decay_rows: List[Dict[str, Any]], min_n: int) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in decay_rows:
        onset = str(row.get("onset_condition") or "unclassified")
        for signal in row.get("decay_signals") or []:
            groups.setdefault((onset, signal), []).append(row)
    out: List[Dict[str, Any]] = []
    for (onset, signal), grp in groups.items():
        if len(grp) < min_n:
            continue
        out.append({
            "decay_signature": f"{onset} -> {signal}",
            "onset_condition": onset,
            "decay_signal": signal,
            "n": len(grp),
            "median_time_to_decay_minutes": _round(_median([r.get("time_to_decay_minutes") for r in grp]), 2),
            "median_cluster_length": _round(_median([r.get("cluster_length") for r in grp]), 2),
        })
    out.sort(key=lambda r: (-int(r.get("n") or 0), _sf(r.get("median_time_to_decay_minutes"), 999.0)))
    return out[:20]


def _decay_drivers_by_onset(decay_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, int]] = {}
    totals: Dict[str, int] = {}
    for row in decay_rows:
        onset = str(row.get("onset_condition") or "unclassified")
        totals[onset] = totals.get(onset, 0) + 1
        groups.setdefault(onset, {})
        for signal in row.get("decay_signals") or []:
            groups[onset][signal] = groups[onset].get(signal, 0) + 1
    out: List[Dict[str, Any]] = []
    for onset, counts in groups.items():
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        for signal, count in ordered[:3]:
            out.append({
                "onset_condition": onset,
                "decay_driver": signal,
                "count": count,
                "pct_of_onset_decays": _round(100.0 * count / totals.get(onset, 1), 1),
            })
    out.sort(key=lambda r: (str(r.get("onset_condition")), -int(r.get("count") or 0)))
    return out


def _anchor_definitions(cfg: Dict[str, Any]) -> List[Tuple[str, Callable[[Dict[str, Any]], bool]]]:
    vol_mult = float(cfg.get("volume_multiple", 2.5))
    vwap_atr = float(cfg.get("vwap_stretch_atr", 0.75))
    return [
        ("first_qualifying_large_candle_after_session_open", lambda r: True),
        ("first_2_5x_volume_candle", lambda r: _sf(r.get("relative_volume")) >= vol_mult or r.get("vol_bucket") == "ge_2_5x"),
        ("first_large_candle_at_key_level", lambda r: r.get("key_level_interaction") in {"at_level", "approaching", "nearby"}),
        ("first_extreme_vwap_stretch", lambda r: abs(_sf(r.get("dist_vwap_atr"))) >= vwap_atr or r.get("vwap_stretch_bucket") == "extended"),
        ("first_large_candle_after_compression", lambda r: _sf(r.get("prior_large_candle_density")) <= 1),
        ("first_failed_continuation_candle", lambda r: r.get("early_path_class") == "weak_start" and bool(r.get("signal_extreme_rebreak_within_2bars"))),
        ("first_successful_reversal_after_extended_move", lambda r: r.get("early_path_class") in {"explosive_start", "orderly_start"} and (r.get("vwap_stretch_bucket") == "extended" or r.get("exhaustion_label") not in {"unknown", "none"})),
    ]


def _future_rows(rows: List[Dict[str, Any]], idx: int, max_minutes: Optional[float], max_signals: Optional[int]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    start_dt = rows[idx].get("_dt")
    for r in rows[idx + 1:]:
        if max_minutes is not None:
            delta = _minutes_between(start_dt, r.get("_dt"))
            if delta is None or delta > max_minutes:
                break
        out.append(r)
        if max_signals is not None and len(out) >= max_signals:
            break
    return out


def _window_summary(label: str, rows: List[Dict[str, Any]], anchor_count: int) -> Dict[str, Any]:
    return {
        "window": label,
        "anchor_count": anchor_count,
        "n_signals": len(rows),
        "win_rate": _round(_win_rate(rows), 1),
        "expectancy_ticks": _round(_avg([r.get("expectancy_ticks") for r in rows]), 3),
        "avg_cleanliness": _round(_avg([r.get("cleanliness_score") for r in rows]), 2),
    }


def _first_per_day(rows: List[Dict[str, Any]], pred: Callable[[Dict[str, Any]], bool]) -> List[int]:
    seen: set[Tuple[str, str]] = set()
    out: List[int] = []
    for i, row in enumerate(rows):
        key = (str(row.get("date")), str(row.get("session")))
        if key in seen or not pred(row):
            continue
        seen.add(key)
        out.append(i)
    return out


def _select_window_events(events: List[Dict[str, Any]], desired_win: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    selected = []
    for ev in events:
        try:
            if int(float(ev.get("window_minutes", ev.get("forward_window_minutes")))) == desired_win:
                selected.append(ev)
        except Exception:
            continue
    if selected:
        return selected, {"requested_window_minutes": desired_win, "selected_window_minutes": desired_win, "window_fallback_used": False}
    windows = []
    for ev in events:
        try:
            windows.append(int(float(ev.get("window_minutes", ev.get("forward_window_minutes")))))
        except Exception:
            pass
    if windows:
        fallback = max(set(windows))
        fallback_events = []
        for ev in events:
            try:
                if int(float(ev.get("window_minutes", ev.get("forward_window_minutes")))) == fallback:
                    fallback_events.append(ev)
            except Exception:
                continue
        return fallback_events, {
            "requested_window_minutes": desired_win,
            "selected_window_minutes": fallback,
            "window_fallback_used": True,
        }
    return events, {"requested_window_minutes": desired_win, "selected_window_minutes": None, "window_fallback_used": True}


def _early_path_class_from_event(ev: Dict[str, Any], size: float) -> str:
    fav2 = _sf(ev.get("early_fav_2bar_ticks")) / size * 100.0 if size else 0.0
    adv2 = _sf(ev.get("early_adv_2bar_ticks")) / size * 100.0 if size else 0.0
    reclaimed = _to_bool(ev.get("did_price_reclaim_signal_midpoint"))
    rebreak = _to_bool(ev.get("did_price_break_signal_extreme_again"))
    if fav2 >= 45 and adv2 <= 20 and reclaimed and not rebreak:
        return "explosive_start"
    if fav2 >= 25 and adv2 <= 35 and reclaimed:
        return "orderly_start"
    if fav2 <= 15 or adv2 >= 35 or rebreak:
        return "weak_start"
    return "mixed_start"


def _next_cluster_after(clusters: List[Dict[str, Any]], idx: int) -> Optional[Dict[str, Any]]:
    for c in clusters:
        if int(c.get("start_index") or 0) >= idx:
            return c
    return None


def _first_cluster_in_window(
    clusters: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
    idx: int,
    max_signals: int,
    max_minutes: float,
    min_length: int,
) -> Optional[Dict[str, Any]]:
    onset_dt = rows[idx].get("_dt") if 0 <= idx < len(rows) else None
    for c in clusters:
        start_idx = int(c.get("start_index") or -1)
        if start_idx < idx:
            continue
        if start_idx - idx > max_signals:
            continue
        if int(c.get("length") or 0) < min_length:
            continue
        cluster_dt = rows[start_idx].get("_dt") if 0 <= start_idx < len(rows) else _parse_dt(c.get("start_dt"))
        minutes = _minutes_between(onset_dt, cluster_dt)
        if minutes is None or minutes > max_minutes:
            continue
        return c
    return None


def _persistence_after_onset(rows: List[Dict[str, Any]], idx: int, max_signals: int) -> int:
    persistence = 0
    started = False
    for row in rows[idx + 1: idx + 1 + max_signals]:
        if row.get("is_reversal_win"):
            started = True
            persistence += 1
        elif started:
            break
    return persistence


def _cluster_row_index(cluster: Dict[str, Any]) -> int:
    return int(cluster.get("start_index") or -1)


def _wick_asymmetry(ev: Dict[str, Any]) -> Optional[float]:
    up = _sf(ev.get("upper_wick_to_range_ratio"), None)
    low = _sf(ev.get("lower_wick_to_range_ratio"), None)
    if up is None or low is None:
        return None
    return round(up - low, 3)


def _continuation_label(v: Any) -> str:
    if v is None:
        return "unknown"
    return "continuation" if _to_bool(v) else "counter_move"


def _parse_dt(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v
    if v is None:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def _minutes_between(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if a is None or b is None:
        return None
    try:
        return (b - a).total_seconds() / 60.0
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


def _sf(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        f = float(v)
        return default if math.isnan(f) else f
    except Exception:
        return default


def _has_number(v: Any) -> bool:
    try:
        if v is None:
            return False
        f = float(v)
        return not math.isnan(f)
    except Exception:
        return False


def _safe_pct(numer: Any, denom: Any) -> Optional[float]:
    d = _sf(denom, 0.0)
    if d <= 0:
        return None
    return _sf(numer, 0.0) / d * 100.0


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in {"true", "t", "1", "yes", "y"}
    return bool(v)


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _avg(values: Iterable[Any]) -> Optional[float]:
    vals = [_sf(v, None) for v in values]
    nums = [v for v in vals if v is not None]
    return sum(nums) / len(nums) if nums else None


def _median(values: Iterable[Any]) -> Optional[float]:
    nums = sorted([_sf(v, None) for v in values if _sf(v, None) is not None])
    if not nums:
        return None
    mid = len(nums) // 2
    if len(nums) % 2:
        return nums[mid]
    return (nums[mid - 1] + nums[mid]) / 2.0


def _round(v: Any, digits: int = 2) -> Any:
    if v is None:
        return None
    try:
        return round(float(v), digits)
    except Exception:
        return v


def _win_rate(rows: List[Dict[str, Any]]) -> float:
    return 100.0 * sum(1 for r in rows if r.get("is_reversal_win")) / len(rows) if rows else 0.0
