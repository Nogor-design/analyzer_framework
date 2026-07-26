from __future__ import annotations

from typing import Any, Dict, List

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    get_derived_payload,
    hdr,
    cell,
    fmt,
    info_box,
    section_title,
)


def _table(headers: List[str], rows: List[List[Any]], empty: str = "No qualifying rows.") -> str:
    if not rows:
        return info_box(empty, color="#f8f9fa", border="#dee2e6")
    head = "<tr>" + "".join(hdr(h) for h in headers) + "</tr>"
    body = ""
    for row in rows:
        body += "<tr>" + "".join(cell(str(v if v is not None else "-")) for v in row) + "</tr>"
    return (
        '<div style="overflow-x:auto;margin-bottom:14px">'
        '<table style="width:100%;border-collapse:collapse">'
        f"<thead>{head}</thead><tbody>{body}</tbody></table></div>"
    )


def _h4(title: str) -> str:
    return f'<h4 style="margin:14px 0 6px;color:#2c3e50">{title}</h4>'


def _onset_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("onset_condition"),
            r.get("n"),
            fmt(r.get("post_onset_win_rate"), 1, "%"),
            fmt(r.get("post_onset_expectancy_ticks"), 2),
            fmt(r.get("avg_cluster_length"), 2),
            fmt(r.get("avg_cluster_duration_minutes"), 1),
            fmt(r.get("avg_time_to_decay_minutes"), 1),
        ]
        for r in rows[:20]
    ]


def _cluster_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("start_dt"),
            r.get("length"),
            fmt(r.get("duration_minutes"), 1),
            fmt(r.get("expectancy_ticks"), 2),
            fmt(r.get("avg_cleanliness"), 1),
            fmt(r.get("time_to_decay_minutes"), 1),
        ]
        for r in rows[:20]
    ]


def _cluster_summary_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("onset_condition"),
            r.get("cluster_count"),
            fmt(r.get("median_cluster_length"), 2),
            fmt(r.get("median_cluster_duration_minutes"), 1),
            fmt(r.get("median_cleanliness"), 1),
            fmt(r.get("median_expectancy_ticks"), 2),
            fmt(r.get("median_time_to_decay_minutes"), 1),
        ]
        for r in rows[:20]
    ]


def _decision_card_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("regime_signature"),
            r.get("background_state"),
            r.get("trigger_event"),
            r.get("early_validation"),
            fmt(r.get("expected_cluster_length"), 1),
            fmt(r.get("expected_decay_window_minutes"), 1),
            r.get("preferred_action"),
            r.get("action_reason"),
        ]
        for r in rows[:5]
    ]


def _anchor_rows(anchors: List[Dict[str, Any]]) -> List[List[Any]]:
    rows: List[List[Any]] = []
    for a in anchors[:12]:
        best = None
        windows = (a.get("minute_windows") or []) + (a.get("signal_windows") or [])
        if windows:
            best = sorted(windows, key=lambda r: float(r.get("expectancy_ticks") or -999), reverse=True)[0]
        rows.append([
            a.get("anchor"),
            a.get("anchor_count"),
            (best or {}).get("window"),
            (best or {}).get("n_signals"),
            fmt((best or {}).get("win_rate"), 1, "%"),
            fmt((best or {}).get("expectancy_ticks"), 2),
            fmt((best or {}).get("avg_cleanliness"), 1),
        ])
    return rows


def _signature_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("signature"),
            r.get("n"),
            fmt(r.get("failure_rate"), 1, "%"),
            fmt(r.get("flip_rate_after_failure"), 1, "%"),
            fmt(r.get("strict_continuation_flip_rate"), 1, "%"),
            fmt(r.get("rebreak_proxy_rate"), 1, "%"),
            fmt(r.get("reversal_expectancy_ticks"), 2),
            fmt(r.get("flip_expectancy_ticks"), 2),
            "yes" if r.get("flip_exceeds_stay") else "no",
        ]
        for r in rows[:20]
    ]


def _decay_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("cluster_start_dt"),
            r.get("cluster_length"),
            fmt(r.get("time_to_decay_minutes"), 1),
            ", ".join(r.get("decay_signals") or []),
        ]
        for r in rows[:20]
    ]


def _decay_signal_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [r.get("decay_signal"), r.get("count"), fmt(r.get("pct_of_decay_events"), 1, "%")]
        for r in rows[:20]
    ]


def _decay_signature_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("decay_signature"),
            r.get("n"),
            fmt(r.get("median_time_to_decay_minutes"), 1),
            fmt(r.get("median_cluster_length"), 1),
        ]
        for r in rows[:20]
    ]


def _decay_driver_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [r.get("onset_condition"), r.get("decay_driver"), r.get("count"), fmt(r.get("pct_of_onset_decays"), 1, "%")]
        for r in rows[:30]
    ]


def _interaction_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("onset_condition"),
            r.get("early_path_condition"),
            r.get("n"),
            fmt(r.get("win_rate"), 1, "%"),
            fmt(r.get("expectancy_ticks"), 2),
            fmt(r.get("avg_cleanliness"), 1),
            fmt(r.get("runner_rate"), 1, "%"),
            fmt(r.get("failure_rate"), 1, "%"),
            fmt(r.get("cluster_persistence"), 1),
            fmt(r.get("win_rate_lift_vs_onset_pp"), 1, "pp"),
            fmt(r.get("expectancy_lift_vs_onset_ticks"), 2),
            fmt(r.get("median_time_to_decay_minutes"), 1),
            r.get("top_decay_driver"),
            r.get("operational_action"),
            r.get("action_reason"),
        ]
        for r in rows[:30]
    ]


def _interaction_matrix_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("onset_condition"),
            r.get("early_path_condition"),
            r.get("n"),
            fmt(r.get("win_rate"), 1, "%"),
            fmt(r.get("fail_rate"), 1, "%"),
            fmt(r.get("scalp_rate"), 1, "%"),
            fmt(r.get("expansion_rate"), 1, "%"),
            fmt(r.get("runner_rate"), 1, "%"),
            fmt(r.get("expectancy_ticks"), 2),
            fmt(r.get("avg_mfe_pct"), 1, "%"),
            fmt(r.get("avg_mae_pct"), 1, "%"),
            fmt(r.get("mfe_mae_ratio"), 2),
            fmt(r.get("cluster_participation_rate"), 1, "%"),
            fmt(r.get("median_cluster_length"), 1),
            fmt(r.get("median_decay_minutes"), 1),
            r.get("first_signal_count"),
            r.get("follow_on_count"),
            fmt(r.get("interaction_quality_score"), 3),
        ]
        for r in rows[:40]
    ]


def _incremental_lift_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("onset_condition"),
            r.get("early_path_condition"),
            r.get("n"),
            fmt(r.get("baseline_wr"), 1, "%"),
            fmt(r.get("onset_wr"), 1, "%"),
            fmt(r.get("early_path_wr"), 1, "%"),
            fmt(r.get("interaction_wr"), 1, "%"),
            fmt(r.get("delta_wr_vs_onset_pp"), 1, "pp"),
            fmt(r.get("delta_wr_vs_early_path_pp"), 1, "pp"),
            fmt(r.get("delta_fail_rate_vs_onset_pp"), 1, "pp"),
            fmt(r.get("delta_runner_rate_vs_onset_pp"), 1, "pp"),
            fmt(r.get("delta_expectancy_vs_onset_ticks"), 2),
            fmt(r.get("delta_cluster_participation_vs_baseline_pp"), 1, "pp"),
            fmt(r.get("delta_persistence_vs_baseline"), 1),
            r.get("incremental_signal"),
        ]
        for r in rows[:30]
    ]


def _ignition_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("onset_condition"),
            r.get("early_path_condition"),
            r.get("n"),
            fmt(r.get("prob_event_belongs_to_cluster"), 1, "%"),
            fmt(r.get("prob_next_qualifying_signal_same_regime"), 1, "%"),
            fmt(r.get("median_cluster_length"), 1),
            fmt(r.get("median_decay_minutes"), 1),
            fmt(r.get("post_onset_follow_through"), 1, "%"),
            fmt(r.get("contamination_breakdown_rate"), 1, "%"),
            fmt(r.get("regime_ignition_score"), 3),
            r.get("operational_action"),
        ]
        for r in rows[:25]
    ]


def _interaction_decay_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("onset_condition"),
            r.get("early_path_condition"),
            r.get("decay_bucket"),
            r.get("n"),
            fmt(r.get("win_rate"), 1, "%"),
            fmt(r.get("expectancy_ticks"), 2),
            fmt(r.get("avg_mae_pct"), 1, "%"),
            fmt(r.get("avg_mfe_pct"), 1, "%"),
            fmt(r.get("runner_rate"), 1, "%"),
            fmt(r.get("fail_rate"), 1, "%"),
            fmt(r.get("regime_decay_resilience_score"), 3),
        ]
        for r in rows[:40]
    ]


def _first_follow_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("onset_condition"),
            r.get("early_path_condition"),
            r.get("signal_role"),
            r.get("n"),
            fmt(r.get("win_rate"), 1, "%"),
            fmt(r.get("expectancy_ticks"), 2),
            fmt(r.get("avg_mae_pct"), 1, "%"),
            fmt(r.get("avg_mfe_pct"), 1, "%"),
            fmt(r.get("runner_rate"), 1, "%"),
            fmt(r.get("median_time_to_target_minutes"), 1),
        ]
        for r in rows[:40]
    ]


def _stability_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("onset_condition"),
            r.get("early_path_condition"),
            r.get("total_n"),
            r.get("unique_days"),
            r.get("dominant_session"),
            fmt(r.get("session_concentration_pct"), 1, "%"),
            fmt(r.get("time_split_stability"), 2),
            fmt(r.get("time_split_wr_range_pp"), 1, "pp"),
            r.get("small_sample_warning"),
            r.get("overlap_warning"),
            r.get("exclusivity_warning"),
            r.get("possible_overfit_warning"),
            r.get("recommended_confidence_label"),
        ]
        for r in rows[:30]
    ]


def _candidate_ledger_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("candidate_name"),
            r.get("candidate_action"),
            r.get("when_condition_is_known"),
            r.get("n"),
            r.get("unique_days"),
            r.get("dominant_session"),
            fmt(r.get("win_rate"), 1, "%"),
            fmt(r.get("fail_rate"), 1, "%"),
            fmt(r.get("runner_rate"), 1, "%"),
            fmt(r.get("expectancy_ticks"), 2),
            fmt(r.get("mfe_mae_ratio"), 2),
            fmt(r.get("cluster_participation_rate"), 1, "%"),
            fmt(r.get("median_cluster_length"), 1),
            fmt(r.get("median_decay_minutes"), 1),
            fmt(r.get("time_split_stability"), 2),
            r.get("confidence_label"),
            r.get("rejection_or_caution_reason"),
        ]
        for r in rows[:30]
    ]


def _action_table_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("candidate_name"),
            r.get("n"),
            fmt(r.get("win_rate"), 1, "%"),
            fmt(r.get("fail_rate"), 1, "%"),
            fmt(r.get("runner_rate"), 1, "%"),
            fmt(r.get("expectancy_ticks"), 2),
            fmt(r.get("cluster_participation_rate"), 1, "%"),
            fmt(r.get("median_decay_minutes"), 1),
            r.get("confidence_label"),
            r.get("action_rationale"),
        ]
        for r in rows[:20]
    ]


def _readiness_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("candidate_name"),
            r.get("uses_only_decision_time_features"),
            r.get("uses_post_entry_validation"),
            r.get("bars_until_known"),
            r.get("requires_cluster_context"),
            r.get("requires_future_outcome_label"),
            r.get("live_actionability_label"),
            r.get("leakage_warning"),
        ]
        for r in rows[:30]
    ]


def _edge_decomposition_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("onset_condition"),
            r.get("early_path_condition"),
            r.get("n"),
            fmt(r.get("baseline_wr"), 1, "%"),
            fmt(r.get("onset_wr"), 1, "%"),
            fmt(r.get("early_path_wr"), 1, "%"),
            fmt(r.get("interaction_wr"), 1, "%"),
            fmt(r.get("incremental_wr_lift_vs_onset_pp"), 1, "pp"),
            fmt(r.get("incremental_wr_lift_vs_early_path_pp"), 1, "pp"),
            fmt(r.get("incremental_expectancy_lift_vs_onset_ticks"), 2),
            fmt(r.get("incremental_expectancy_lift_vs_early_path_ticks"), 2),
            fmt(r.get("incremental_cluster_lift_vs_baseline_pp"), 1, "pp"),
            r.get("material_interaction_signal"),
            r.get("interpretation"),
        ]
        for r in rows[:30]
    ]


def _failure_audit_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("candidate_name"),
            r.get("candidate_action"),
            r.get("n"),
            fmt(r.get("fail_rate"), 1, "%"),
            fmt(r.get("median_decay_minutes"), 1),
            fmt(r.get("session_concentration_pct"), 1, "%"),
            fmt(r.get("time_split_stability"), 2),
            ", ".join(r.get("dominant_failure_modes") or []),
            r.get("recommended_interpretation"),
        ]
        for r in rows[:30]
    ]


def _flip_sample_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            r.get("dt"),
            fmt(r.get("reversal_mfe_pct"), 1, "%"),
            fmt(r.get("continuation_mfe_pct"), 1, "%"),
            fmt(r.get("flip_expectancy_ticks"), 2),
            fmt(r.get("time_to_flip_min"), 1),
            fmt(r.get("cleanliness_score"), 1),
            "yes" if r.get("strict_flip") else "no",
            "yes" if r.get("rebreak_proxy") else "no",
        ]
        for r in rows[:12]
    ]


def render_large_candle_excursion_regime_discovery(ctx: dict) -> str:
    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data:
        return info_box("Regime discovery unavailable: findings data missing.")
    if not data.get("has_source"):
        return info_box(f"Regime discovery unavailable: {data.get('message', 'source analytics missing')}.")

    rd = data.get("regime_discovery") or {}
    if not rd.get("enabled"):
        return info_box(
            "Regime discovery disabled. Enable <code>large_candle_excursion_findings.regime_discovery.enabled: true</code>.",
            color="#f8f9fa",
            border="#dee2e6",
        )
    if rd.get("message"):
        return info_box(f"Regime discovery unavailable: {rd.get('message')}", color="#f8f9fa", border="#dee2e6")

    clean = rd.get("cleanliness_summary") or {}
    clusters = rd.get("win_cluster_analysis") or {}
    onset = rd.get("regime_onset_detector") or {}
    anchors = rd.get("trigger_anchored_window_analysis") or {}
    flips = rd.get("failure_to_flip_transition_report") or {}
    decay = rd.get("regime_decay_detector") or {}
    interactions = rd.get("onset_path_interaction_analysis") or {}
    clean_def = rd.get("cleanliness_definition") or {}
    decision_cards = rd.get("decision_summary_cards") or []

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Regime Discovery Chain")
    html += (
        '<p style="font-size:12px;color:#444;line-height:1.5">'
        "This section reframes large-candle reversal research as a sequence: "
        "background state, trigger event, entry decision, early validation path, "
        "cluster persistence, and regime decay. Metrics are computed from the existing "
        "large-candle event sample with null-safe fallbacks."
        "</p>"
    )

    html += (
        '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px">'
        f'<div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;padding:10px"><b>Events</b><br>{rd.get("n_events", 0)}</div>'
        f'<div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;padding:10px"><b>Clusters</b><br>{clusters.get("cluster_count", 0)}</div>'
        f'<div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;padding:10px"><b>Wins in Clusters</b><br>{fmt(clusters.get("percent_wins_inside_clusters"), 1, "%")}</div>'
        f'<div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;padding:10px"><b>Signals in Clusters</b><br>{fmt(clusters.get("percent_signals_inside_clusters"), 1, "%")}</div>'
        f'<div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;padding:10px"><b>Avg Cleanliness</b><br>{fmt(clean.get("avg_cleanliness"), 1)}</div>'
        f'<div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;padding:10px"><b>Failure to Flip</b><br>{fmt(flips.get("flip_rate_after_failure"), 1, "%")}</div>'
        f'<div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;padding:10px"><b>Avg Decay</b><br>{fmt(decay.get("avg_time_to_decay_minutes"), 1)} min</div>'
        "</div>"
    )

    html += _h4("Decision-Oriented Regime Cards")
    html += _table(
        ["Regime", "Background State", "Trigger", "Early Validation", "Cluster Len", "Decay Min", "Operational Action", "Action Reason"],
        _decision_card_rows(decision_cards),
    )

    html += _h4("1. Regime Onset Detector")
    selectivity = onset.get("selectivity_interpretation") or {}
    html += (
        '<div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;padding:10px;margin-bottom:10px;font-size:12px;color:#444;line-height:1.45">'
        f"<b>Selectivity:</b> {selectivity.get('summary', 'No selectivity interpretation available.')}<br>"
        f"<b>Overlap:</b> {selectivity.get('overlap_interpretation', '')}<br>"
        f"<b>Exclusivity:</b> {selectivity.get('exclusivity_interpretation', '')}"
        "</div>"
    )
    html += _table(
        ["Onset Condition", "N", "Coverage", "High Coverage", "Overlap", "High Overlap", "Exclusive", "Low Exclusive", "Lift WR", "Post WR", "Post Exp", "Median Cluster Len", "Follow-Through", "Median Persistence"],
        [
            [
                r.get("onset_condition"),
                r.get("n"),
                fmt(r.get("coverage_pct"), 1, "%"),
                "yes" if r.get("high_coverage") else "no",
                fmt(r.get("overlap_pct"), 1, "%"),
                "yes" if r.get("high_overlap") else "no",
                fmt(r.get("exclusivity_pct"), 1, "%"),
                "yes" if r.get("low_exclusivity") else "no",
                fmt(r.get("win_rate_lift_vs_baseline_pp"), 1, "pp"),
                fmt(r.get("post_onset_win_rate"), 1, "%"),
                fmt(r.get("post_onset_expectancy_ticks"), 2),
                fmt(r.get("median_cluster_length"), 2),
                fmt(r.get("cluster_follow_through_rate"), 1, "%"),
                fmt(r.get("median_first_persistence"), 1),
            ]
            for r in (onset.get("conditions") or [])[:20]
        ],
    )

    html += _h4("1b. Onset x Early-Path Interaction Analysis")
    capture = interactions.get("top_filter_capture") or {}
    html += (
        '<p style="font-size:12px;color:#444">'
        f"Top interaction filters capture {fmt(capture.get('percent_wins_captured'), 1, '%')} of wins and "
        f"{fmt(capture.get('percent_signals_captured'), 1, '%')} of signals. "
        "Lift columns compare each early-path validation state against that onset condition alone."
        "</p>"
    )
    html += _table(
        ["Onset", "Early Path", "N", "WR", "Exp", "Clean", "Runner", "Failure", "Persistence", "WR Lift", "Exp Lift", "Decay Min", "Decay Driver", "Action", "Reason"],
        _interaction_rows(interactions.get("interactions") or []),
    )

    definitions = interactions.get("definitions") or {}
    html += (
        '<div style="background:#fff3cd;border-left:4px solid #ffc107;padding:10px 14px;border-radius:0 4px 4px 0;margin:12px 0">'
        '<p style="color:#856404;font-size:12px;margin:0;line-height:1.6">'
        "<b>Decision-use note:</b> onset signatures describe the background/trigger. "
        "Early-path labels are validation states, usually available after the first two post-entry bars. "
        f"{definitions.get('warning', '')}"
        "</p></div>"
    )

    html += _h4("1c. Onset x Early-Path Interaction Matrix")
    html += (
        '<p style="font-size:12px;color:#444;line-height:1.5">'
        "This matrix tests whether onset labels become meaningful only after early validation. "
        "Cluster participation and decay columns separate one-trade quality from regime quality."
        "</p>"
    )
    html += _table(
        ["Onset", "Early Path", "N", "WR", "Fail", "Scalp", "Expansion", "Runner", "Exp", "Avg MFE", "Avg MAE", "MFE/MAE", "Cluster Part", "Med Cluster", "Med Decay", "First", "Follow-On", "Quality"],
        _interaction_matrix_rows(interactions.get("interaction_matrix") or []),
    )

    html += _h4("1d. Incremental Lift / Information Gain")
    html += (
        '<p style="font-size:12px;color:#444;line-height:1.5">'
        "This table compares the full interaction against all-events, onset-only, and early-path-only baselines. "
        "Use rows marked as interaction signal only when the combined state improves beyond both ingredients."
        "</p>"
    )
    html += _table(
        ["Onset", "Early Path", "N", "Base WR", "Onset WR", "Path WR", "Interaction WR", "WR vs Onset", "WR vs Path", "Fail vs Onset", "Runner vs Onset", "Exp vs Onset", "Cluster vs Base", "Persist vs Base", "Signal"],
        _incremental_lift_rows(interactions.get("incremental_lift_table") or []),
    )

    html += _h4("1e. Regime Ignition Table")
    html += (
        '<p style="font-size:12px;color:#444;line-height:1.5">'
        "Ranked view of combinations that appear to identify the start of a clean reversal regime. "
        "The score is an interpretable blend of cluster participation, same-regime follow-on, follow-through, persistence, decay time, sample size, and breakdown penalties."
        "</p>"
    )
    html += _table(
        ["Onset", "Early Path", "N", "Cluster Prob", "Next Same-Regime", "Med Cluster", "Med Decay", "Follow-Through", "Breakdown", "Ignition", "Action"],
        _ignition_rows(interactions.get("regime_ignition_table") or []),
    )

    html += _h4("1f. Regime Decay Table")
    html += (
        '<p style="font-size:12px;color:#444;line-height:1.5">'
        "For the strongest interactions, this shows how quality changes after first/second/later cluster trades, elapsed cluster time, failed follow-through, re-breaks, and opposing large candles. "
        "It is descriptive evidence for hold, scalp, scratch, flip-watch, or regime-shutdown rules; it is not a causal proof."
        "</p>"
    )
    html += _table(
        ["Onset", "Early Path", "Decay Bucket", "N", "WR", "Exp", "Avg MAE", "Avg MFE", "Runner", "Fail", "Resilience"],
        _interaction_decay_rows(interactions.get("regime_decay_table") or []),
    )

    html += _h4("1g. First Signal vs Follow-On")
    html += (
        '<p style="font-size:12px;color:#444;line-height:1.5">'
        "This split checks whether the edge is front-loaded at cluster ignition or persists into later qualifying signals."
        "</p>"
    )
    html += _table(
        ["Onset", "Early Path", "Signal Role", "N", "WR", "Exp", "MAE", "MFE", "Runner", "Time Target"],
        _first_follow_rows(interactions.get("first_signal_follow_on_table") or []),
    )

    html += _h4("1h. Interaction Stability / Fragility")
    html += (
        '<p style="font-size:12px;color:#444;line-height:1.5">'
        "This guardrail table prevents tiny or overlapping buckets from being treated as independent discoveries. "
        "Confidence is reduced for small samples, session concentration, time instability, overlapping onset labels, low exclusivity, and post-entry-only validation features."
        "</p>"
    )
    html += _table(
        ["Onset", "Early Path", "N", "Days", "Dom Session", "Session Conc", "Time Stability", "WR Range", "Small N", "Overlap", "Exclusive", "Overfit", "Confidence"],
        _stability_rows(interactions.get("interaction_stability_table") or []),
    )

    html += _h4("1i. Tradeable Regime Candidate Ledger")
    html += (
        '<p style="font-size:12px;color:#444;line-height:1.5">'
        "This ledger converts the research buckets into conservative candidate actions. "
        "Rows are not trade instructions; they are paper-test candidates or rejection records with the evidence needed to decide what to test next."
        "</p>"
    )
    html += _table(
        ["Candidate", "Action", "Known", "N", "Days", "Session", "WR", "Fail", "Runner", "Exp", "MFE/MAE", "Cluster", "Med Len", "Decay", "Stability", "Confidence", "Caution"],
        _candidate_ledger_rows(interactions.get("tradeable_regime_candidate_ledger") or []),
    )

    html += _h4("1j. Action-Specific Decision Tables")
    html += (
        '<p style="font-size:12px;color:#444;line-height:1.5">'
        "These splits show which candidates are better framed as hold, scalp, scratch, flip-watch, re-entry, shutdown, or avoid decisions. "
        "Use this to decide the next paper-test, not to assume live readiness."
        "</p>"
    )
    action_tables = interactions.get("action_decision_tables") or {}
    for label, title in [
        ("hold_candidates", "Hold Candidates"),
        ("scalp_candidates", "Scalp Candidates"),
        ("scratch_reduce_risk_candidates", "Scratch / Reduce-Risk Candidates"),
        ("flip_watch_candidates", "Flip-Watch Candidates"),
        ("re_entry_candidates", "Re-Entry Candidates"),
        ("regime_shutdown_candidates", "Regime-Shutdown Candidates"),
        ("avoid_or_rejected", "Avoid / Rejected"),
    ]:
        html += f'<h5 style="margin:10px 0 4px;color:#374151">{title}</h5>'
        html += _table(
            ["Candidate", "N", "WR", "Fail", "Runner", "Exp", "Cluster", "Decay", "Confidence", "Rationale"],
            _action_table_rows(action_tables.get(label) or []),
            empty=f"No {title.lower()} met the current screen.",
        )

    html += _h4("1k. Live-Decision Readiness Screen")
    html += (
        '<p style="font-size:12px;color:#444;line-height:1.5">'
        "This screen separates entry-time rules from post-entry management clues and research-only diagnostics. "
        "Any row requiring future outcome, cluster, or decay context should not be translated directly into an entry rule."
        "</p>"
    )
    html += _table(
        ["Candidate", "Decision-Time Only", "Uses Post-Entry", "Bars Known", "Needs Cluster", "Needs Future Outcome", "Actionability", "Leakage Warning"],
        _readiness_rows(interactions.get("live_decision_readiness_screen") or []),
    )

    html += _h4("1l. Edge Decomposition")
    html += (
        '<p style="font-size:12px;color:#444;line-height:1.5">'
        "This table explains whether the interaction is truly adding signal beyond onset-only and early-path-only views. "
        "A candidate that is mostly explained by early-path alone is still useful, but less regime-specific."
        "</p>"
    )
    html += _table(
        ["Onset", "Early Path", "N", "Base WR", "Onset WR", "Path WR", "Inter WR", "WR vs Onset", "WR vs Path", "Exp vs Onset", "Exp vs Path", "Cluster Lift", "Material", "Interpretation"],
        _edge_decomposition_rows(interactions.get("edge_decomposition_table") or []),
    )

    html += _h4("1m. Failure Mode Audit")
    html += (
        '<p style="font-size:12px;color:#444;line-height:1.5">'
        "This audit names why promoted and rejected candidates fail: weak early movement, re-breaks, fast decay, concentration, instability, overlap, or small-sample inflation. "
        "It is a guardrail against forcing a strong-edge story where the evidence is fragile."
        "</p>"
    )
    html += _table(
        ["Candidate", "Action", "N", "Fail", "Decay", "Session Conc", "Stability", "Failure Modes", "Interpretation"],
        _failure_audit_rows(interactions.get("failure_mode_audit") or []),
    )

    html += _h4("2. Win Cluster Analysis")
    html += (
        '<p style="font-size:12px;color:#444">'
        f"Median cluster length: {fmt(clusters.get('median_cluster_length'), 2)} | "
        f"Wins inside clusters: {fmt(clusters.get('percent_wins_inside_clusters'), 1, '%')} | "
        f"Signals inside clusters: {fmt(clusters.get('percent_signals_inside_clusters'), 1, '%')}."
        "</p>"
    )
    html += _table(
        ["Onset", "Clusters", "Median Len", "Median Duration", "Median Clean", "Median Exp", "Median Decay"],
        _cluster_summary_rows(clusters.get("summary_by_onset_condition") or []),
    )

    html += _h4("3. Trigger-Anchored Window Analysis")
    html += _table(
        ["Anchor", "Anchors", "Best Window", "Signals", "WR", "Expectancy", "Cleanliness"],
        _anchor_rows(anchors.get("anchors") or []),
    )

    html += _h4("4. Reversal Cleanliness Score")
    html += (
        '<div style="background:#eef6ff;border:1px solid #bfdbfe;border-radius:4px;padding:10px;margin-bottom:10px;font-size:12px;color:#1f2937;line-height:1.45">'
        f"<b>Formula:</b> {clean_def.get('formula', 'weighted component score')}<br>"
        f"<b>Normalization:</b> {clean_def.get('normalization', 'components normalized to 0..1')}<br>"
        f"<b>Range:</b> {clean_def.get('score_range', '0..100')} | <b>Higher is better:</b> {clean_def.get('higher_is_always_better', True)}<br>"
        "<b>Bands:</b> clean >=75, workable 55-74.9, messy 35-54.9, avoid/flip-watch <35."
        "</div>"
    )
    html += _table(
        ["Metric", "Value"],
        [
            ["Average cleanliness", fmt(clean.get("avg_cleanliness"), 1)],
            ["Winner cleanliness", fmt(clean.get("winner_cleanliness"), 1)],
            ["Loser cleanliness", fmt(clean.get("loser_cleanliness"), 1)],
            ["Winner minus loser gap", fmt(clean.get("winner_loser_cleanliness_gap"), 1)],
        ],
    )

    html += _h4("5. Failure-to-Flip Transition Report")
    fdiag = flips.get("diagnostics") or {}
    fdef = flips.get("definition") or {}
    html += (
        '<p style="font-size:12px;color:#444">'
        f"Failures: {flips.get('n_failures', 0)} | "
        f"Flip candidates after failure: {flips.get('n_flip_candidates', 0)} | "
        f"Strict continuation flip rate: {fmt(flips.get('strict_continuation_flip_rate'), 1, '%')} | "
        f"Re-break proxy rate: {fmt(flips.get('rebreak_proxy_rate'), 1, '%')} | "
        f"Median flip expectancy: {fmt(flips.get('median_expectancy_after_flip_ticks'), 2)} | "
        f"Median time to flip: {fmt(flips.get('median_time_to_flip_minutes'), 1)} min | "
        f"Median cleanliness after flip: {fmt(flips.get('median_cleanliness_after_flip'), 1)} | "
        f"Avg bars to confirmation: {fmt(flips.get('avg_bars_to_flip_confirmation'), 2)}"
        "</p>"
    )
    html += _table(
        ["Audit Field", "Value"],
        [
            ["Conditional definition", fdef.get("conditional_definition")],
            ["Denominator", fdef.get("denominator")],
            ["Numerator", fdef.get("numerator")],
            ["Eligible rows", fdiag.get("eligible_rows")],
            ["Denominator failures", fdiag.get("denominator_failures")],
            ["Numerator flips", fdiag.get("numerator_flips")],
            ["Strict flips", fdiag.get("strict_continuation_flips")],
            ["Re-break proxy flips", fdiag.get("rebreak_proxy_flips")],
            ["Median expectancy after flip", fmt(flips.get("median_expectancy_after_flip_ticks"), 2)],
            ["Median time to flip", fmt(flips.get("median_time_to_flip_minutes"), 1)],
            ["Median cleanliness after flip", fmt(flips.get("median_cleanliness_after_flip"), 1)],
            ["Null reversal exclusions", fdiag.get("excluded_null_reversal_mfe")],
            ["Null continuation exclusions", fdiag.get("excluded_null_continuation_mfe")],
            ["Suspicious flags", ", ".join(fdiag.get("suspicious_flags") or []) or "none"],
        ],
    )
    html += _table(
        ["Qualifying Failure Sample", "Rev MFE", "Cont MFE", "Flip Exp", "Time Flip", "Clean", "Strict", "Rebreak"],
        _flip_sample_rows(fdiag.get("exact_qualifying_sample") or []),
    )
    html += _table(
        ["Signature", "N", "Fail Rate", "Flip Rate", "Strict Flip", "Rebreak Proxy", "Stay Exp", "Flip Exp", "Flip Better"],
        _signature_rows(flips.get("signatures") or []),
    )

    html += _h4("6. Regime Decay Detector")
    html += _table(
        ["Decay Signal", "Count", "Pct"],
        _decay_signal_rows(decay.get("summary_by_signal") or []),
    )
    html += _table(
        ["Onset Type", "Top Decay Driver", "Count", "Pct of Onset Decays"],
        _decay_driver_rows(decay.get("decay_drivers_by_onset") or []),
    )
    html += _table(
        ["Decay Signature", "N", "Median Decay Min", "Median Cluster Len"],
        _decay_signature_rows(decay.get("top_decay_signatures") or []),
    )
    html += _table(
        ["Cluster Start", "Length", "Time to Decay", "Decay Signals"],
        _decay_rows(decay.get("decay_events") or []),
    )

    html += "</div>"
    return html
