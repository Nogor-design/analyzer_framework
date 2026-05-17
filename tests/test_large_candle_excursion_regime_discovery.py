from __future__ import annotations

from types import SimpleNamespace

from ta_foundation.analysis.large_candle_excursion.regime_discovery import (
    analyze_failure_to_flip,
    analyze_trigger_anchored_windows,
    compute_regime_discovery,
    detect_regime_decay,
    detect_regime_onsets,
    detect_win_clusters,
)
from ta_foundation.reports.html.builder import HtmlReportBuilder, HtmlSection
from ta_foundation.reports.html.registry import SECTION_REGISTRY
from ta_foundation.reports.text.export_large_candle_regime_summary import (
    export_large_candle_regime_summary,
    render_large_candle_regime_summary_markdown,
)


def _event(i: int, reversal_mfe: float, reversal_mae: float, **extra):
    row = {
        "dt": f"2026-01-01T09:{30 + i:02d}:00-07:00",
        "window_minutes": 30,
        "direction": 1,
        "tf_minutes": 1,
        "size_ticks": 100,
        # In the source event convention fav_ticks follows the signal candle;
        # adv_ticks is the fade/reversal excursion.
        "fav_ticks": reversal_mae,
        "adv_ticks": reversal_mfe,
        "time_to_max_adv_min": 4 if reversal_mfe >= 50 else 24,
        "time_to_max_fav_min": 3,
        "early_fav_2bar_ticks": 35 if reversal_mfe >= 50 else 8,
        "early_adv_2bar_ticks": 10 if reversal_mfe >= 50 else 45,
        "did_price_reclaim_signal_midpoint": reversal_mfe >= 50,
        "did_price_break_signal_extreme_again": reversal_mfe < 50,
        "session_bucket": "ny_open",
        "relative_volume": 3.0 if i == 0 else 1.0,
        "dist_vwap_atr": 0.9,
        "vwap_ext_bucket": "extended",
        "level_interaction_label": "at_level" if i % 2 == 0 else "none",
        "range_vs_avg_range": 1.8,
        "direction_streak": 3,
    }
    row.update(extra)
    return row


def _rows():
    events = [
        _event(0, 70, 10),
        _event(1, 65, 12),
        _event(2, 20, 70),
        _event(3, 75, 9),
        _event(4, 80, 8),
        _event(5, 90, 7),
        _event(6, 15, 85),
        _event(7, 12, 95),
    ]
    payload = compute_regime_discovery(events, {"enabled": True, "min_events": 1})
    return payload["event_feature_sample"]


def test_cluster_detection_finds_consecutive_win_bursts():
    rows = _rows()
    result = detect_win_clusters(rows, {"min_consecutive_wins": 2, "max_time_gap_minutes": 10})

    assert result["cluster_count"] == 2
    assert result["median_cluster_length"] == 2.5
    assert result["clusters"][1]["length"] == 3


def test_onset_detection_scores_pre_cluster_conditions():
    rows = _rows()
    clusters = detect_win_clusters(rows, {"min_consecutive_wins": 2, "max_time_gap_minutes": 10})
    result = detect_regime_onsets(rows, clusters, {"min_n": 1, "lookahead_signals": 3})

    labels = {r["onset_condition"] for r in result["conditions"]}
    assert "first_large_at_key_level_with_extended_vwap_stretch" in labels
    assert "first_large_after_directional_run" in labels
    assert "volume_expansion" not in labels
    assert "key_level_interaction" not in labels
    by_label = {r["onset_condition"]: r for r in result["conditions"]}
    assert by_label["first_large_after_directional_run"]["coverage_pct"] < 100
    assert "too_broad_to_be_actionable" in by_label["first_large_after_directional_run"]
    assert {"high_coverage", "high_overlap", "low_exclusivity"}.issubset(by_label["first_large_after_directional_run"])
    assert result["selectivity_interpretation"]["overlap_interpretation"]


def test_follow_through_and_persistence_are_window_bounded():
    events = [
        _event(0, 70, 10, direction=1, direction_streak=3),
        _event(1, 65, 12, direction=1, direction_streak=4),
        _event(2, 15, 80, direction=1, direction_streak=5),
        _event(3, 10, 90, direction=-1, direction_streak=3),
        _event(4, 15, 90, direction=-1, direction_streak=4),
        _event(5, 70, 10, direction=1, direction_streak=3),
        _event(6, 15, 90, direction=1, direction_streak=4),
    ]
    payload = compute_regime_discovery(
        events,
        {
            "enabled": True,
            "onset_detection": {
                "min_n": 1,
                "lookahead_signals": 3,
                "follow_through_max_signals_to_cluster": 1,
                "follow_through_min_cluster_length": 2,
            },
        },
    )
    row = next(r for r in payload["regime_onset_detector"]["conditions"] if r["onset_condition"] == "first_large_after_directional_run")

    assert row["diagnostics"]["follow_through_denominator"] >= 2
    assert row["cluster_follow_through_rate"] < 100
    assert row["median_first_persistence"] is not None
    assert 0 <= row["median_first_persistence"] <= row["diagnostics"]["lookahead_signals"]


def test_cluster_summary_by_onset_condition_exposes_decision_metrics():
    rows = _rows()
    result = detect_win_clusters(rows, {"min_consecutive_wins": 2, "max_time_gap_minutes": 10})

    summary = result["summary_by_onset_condition"]
    assert result["percent_wins_inside_clusters"] > 0
    assert result["percent_signals_inside_clusters"] > 0
    assert summary[0]["median_cluster_length"] is not None
    assert summary[0]["median_cleanliness"] is not None


def test_trigger_anchored_windows_returns_anchor_performance():
    rows = _rows()
    result = analyze_trigger_anchored_windows(
        rows,
        {"minute_windows": [10], "signal_windows": [3], "min_anchors": 1},
    )

    assert result["anchors"]
    assert result["anchors"][0]["minute_windows"][0]["n_signals"] > 0


def test_cleanliness_scoring_separates_winners_from_losers():
    payload = compute_regime_discovery([_event(0, 90, 5), _event(1, 10, 95)], {"enabled": True})

    clean = payload["cleanliness_summary"]
    assert clean["winner_cleanliness"] > clean["loser_cleanliness"]


def test_failure_to_flip_detects_when_flip_expectancy_beats_staying():
    rows = _rows()
    result = analyze_failure_to_flip(
        rows,
        {"failure_max_reversal_pct": 25, "flip_min_continuation_pct": 50, "min_n": 1},
    )

    assert result["n_failures"] >= 2
    assert any(r["flip_exceeds_stay"] for r in result["signatures"])
    assert result["diagnostics"]["denominator_failures"] == result["n_failures"]
    assert result["diagnostics"]["strict_continuation_flips"] == result["n_flip_candidates"]
    assert result["definition"]["rebreak_proxy_counted_as_flip"] is False
    assert result["diagnostics"]["exact_qualifying_sample"]
    assert result["median_expectancy_after_flip_ticks"] is not None
    assert result["median_time_to_flip_minutes"] is not None
    assert result["median_cleanliness_after_flip"] is not None


def test_decay_detection_reports_cluster_decay_signals():
    rows = _rows()
    clusters = detect_win_clusters(rows, {"min_consecutive_wins": 2, "max_time_gap_minutes": 10})
    result = detect_regime_decay(rows, clusters, {"rolling_window_signals": 2, "min_signature_n": 1})

    assert result["decay_events"]
    assert result["avg_time_to_decay_minutes"] is not None
    assert result["summary_by_signal"]
    assert result["top_decay_signatures"]
    assert result["decay_drivers_by_onset"]
    assert any(r["decay_signal"] in {"declining_cleanliness", "increasing_failed_reversals", "falling_rolling_win_density", "rising_adverse_excursion"} for r in result["summary_by_signal"])


def test_decision_summary_cards_are_generated_from_top_onsets():
    payload = compute_regime_discovery(
        [_event(0, 70, 10), _event(1, 65, 12), _event(2, 20, 70), _event(3, 80, 8)],
        {"enabled": True, "onset_detection": {"min_n": 1}},
    )

    cards = payload["decision_summary_cards"]
    assert cards
    assert cards[0]["preferred_action"] in {
        "scalp_only",
        "reversal_only_if_clean_reclaim",
        "reversal_hold",
        "flip_watch",
        "continuation_preferred",
        "avoid",
    }
    assert cards[0]["action_reason"]
    assert cards[0]["background_state"]


def test_onset_path_interactions_add_validation_lift_and_actions():
    payload = compute_regime_discovery(
        [
            _event(0, 70, 10, direction=1, direction_streak=3),
            _event(1, 65, 12, direction=1, direction_streak=4),
            _event(2, 20, 70, direction=1, direction_streak=5),
            _event(3, 15, 80, direction=-1, direction_streak=3),
            _event(4, 80, 8, direction=-1, direction_streak=4),
            _event(5, 90, 7, direction=-1, direction_streak=5),
        ],
        {
            "enabled": True,
            "onset_detection": {"min_n": 1},
            "onset_path_interactions": {"min_n": 1, "top_filter_n": 3},
        },
    )

    result = payload["onset_path_interaction_analysis"]
    labels = {(r["onset_condition"], r["early_path_condition"]) for r in result["interactions"]}

    assert result["interactions"]
    assert result["interaction_matrix"]
    assert result["incremental_lift_table"]
    assert result["regime_ignition_table"]
    assert result["interaction_stability_table"]
    assert result["tradeable_regime_candidate_ledger"]
    assert result["action_decision_tables"]
    assert result["live_decision_readiness_screen"]
    assert result["edge_decomposition_table"]
    assert result["failure_mode_audit"]
    assert result["executive_summary_payload"]
    assert any(path in {"orderly_start", "weak_start", "midpoint_reclaim_yes", "rebreak_yes"} for _, path in labels)
    assert result["top_filter_capture"]["percent_wins_captured"] > 0
    assert all("win_rate_lift_vs_onset_pp" in r for r in result["interactions"])
    assert all("action_reason" in r for r in result["interactions"])
    assert all("interaction_quality_score" in r for r in result["interaction_matrix"])
    assert all("incremental_signal" in r for r in result["incremental_lift_table"])
    assert all("regime_ignition_score" in r for r in result["regime_ignition_table"])
    assert all("candidate_action" in r for r in result["tradeable_regime_candidate_ledger"])
    assert all("live_actionability_label" in r for r in result["live_decision_readiness_screen"])
    assert {r["operational_action"] for r in result["interactions"]}.issubset({
        "scalp_only",
        "reversal_only_if_clean_reclaim",
        "reversal_hold",
        "flip_watch",
        "continuation_preferred",
        "avoid",
    })


def test_onset_path_interactions_include_decay_and_persistence_by_state():
    payload = compute_regime_discovery(
        [_event(0, 70, 10), _event(1, 65, 12), _event(2, 20, 70), _event(3, 75, 9), _event(4, 80, 8), _event(5, 10, 90)],
        {
            "enabled": True,
            "onset_detection": {"min_n": 1},
            "onset_path_interactions": {"min_n": 1},
            "decay_detection": {"min_signature_n": 1},
        },
    )

    rows = payload["onset_path_interaction_analysis"]["interactions"]

    assert any(r["cluster_persistence"] is not None for r in rows)
    assert any("median_time_to_decay_minutes" in r for r in rows)
    assert any("top_decay_driver" in r for r in rows)


def test_onset_path_interactions_report_decay_and_first_follow_on_tables():
    payload = compute_regime_discovery(
        [
            _event(0, 70, 10, direction=1, direction_streak=3),
            _event(1, 65, 12, direction=1, direction_streak=4),
            _event(2, 75, 9, direction=1, direction_streak=5),
            _event(3, 20, 70, direction=-1, direction_streak=3),
            _event(4, 80, 8, direction=-1, direction_streak=4),
            _event(5, 90, 7, direction=-1, direction_streak=5),
            _event(6, 12, 95, direction=1, direction_streak=3),
        ],
        {
            "enabled": True,
            "onset_detection": {"min_n": 1},
            "onset_path_interactions": {"min_n": 1, "strong_min_n": 1, "top_filter_n": 3},
            "decay_detection": {"min_signature_n": 1},
        },
    )

    result = payload["onset_path_interaction_analysis"]

    assert result["regime_decay_table"]
    assert result["first_signal_follow_on_table"]
    assert all("regime_decay_resilience_score" in r for r in result["regime_decay_table"])
    assert any(r["signal_role"] in {"first_event_in_cluster", "second_signal", "later_signals"} for r in result["first_signal_follow_on_table"])
    assert result["definitions"]["post_entry_validation_features"]


def test_executive_summary_payload_renders_markdown():
    payload = compute_regime_discovery(
        [
            _event(0, 70, 10, direction=1, direction_streak=3),
            _event(1, 65, 12, direction=1, direction_streak=4),
            _event(2, 75, 9, direction=1, direction_streak=5),
            _event(3, 20, 70, direction=-1, direction_streak=3),
            _event(4, 80, 8, direction=-1, direction_streak=4),
            _event(5, 90, 7, direction=-1, direction_streak=5),
        ],
        {
            "enabled": True,
            "onset_detection": {"min_n": 1},
            "onset_path_interactions": {"min_n": 1, "candidate_min_n": 1, "candidate_min_unique_days": 1},
        },
    )
    summary_payload = payload["onset_path_interaction_analysis"]["executive_summary_payload"]

    md = render_large_candle_regime_summary_markdown(summary_payload, title="Smoke")

    assert "Executive Summary" in md
    assert "Top Tradeable Regime Candidates" in md
    assert "What This Does NOT Prove" in md


def test_executive_summary_export_writes_markdown_file(tmp_path):
    summary_payload = {
        "top_tradeable_candidates": [
            {
                "candidate_name": "onset x path",
                "candidate_action": "scalp",
                "n": 42,
                "win_rate": 60.0,
                "expectancy_ticks": 4.5,
                "fail_rate": 20.0,
                "runner_rate": 10.0,
                "cluster_participation_rate": 30.0,
                "median_decay_minutes": 25.0,
                "confidence_label": "research_candidate",
            }
        ],
        "what_this_does_not_prove": ["No causal proof."],
        "next_recommended_tests": ["Paper test."],
    }
    pkg = SimpleNamespace(
        metadata={
            "derived": {
                "large_candle_excursion_findings": {
                    "regime_discovery": {
                        "onset_path_interaction_analysis": {
                            "executive_summary_payload": summary_payload,
                        }
                    }
                }
            }
        }
    )
    out = tmp_path / "summary.md"

    wrote = export_large_candle_regime_summary({"run": pkg}, out, title="Smoke")

    assert wrote is True
    assert "onset x path" in out.read_text(encoding="utf-8")


def test_missing_optional_columns_fallback_without_crashing():
    event = {
        "dt": "2026-01-01T09:30:00-07:00",
        "window_minutes": 30,
        "direction": 1,
        "size_ticks": 100,
        "fav_ticks": 20,
        "adv_ticks": 70,
    }

    payload = compute_regime_discovery([event], {"enabled": True})

    assert payload["n_events"] == 1
    assert payload["event_feature_sample"][0]["vwap_stretch_bucket"] == "unknown"


def test_regime_discovery_section_smoke_builds_html_report():
    payload = compute_regime_discovery([_event(0, 80, 10), _event(1, 70, 12)], {"enabled": True, "min_events": 1})
    pkg = SimpleNamespace(metadata={"derived": {"large_candle_excursion_findings": {"enabled": True, "has_source": True, "regime_discovery": payload}}})
    section_def = SECTION_REGISTRY["large_candle_excursion_regime_discovery"]
    builder = HtmlReportBuilder(
        "Smoke",
        [HtmlSection(section_def.id, section_def.default_title, section_def.render_fn)],
    )

    html = builder.build({"packages": {"p": pkg}})

    assert "Regime Discovery Chain" in html
    assert "Failure-to-Flip Transition Report" in html
    assert "Onset x Early-Path Interaction Analysis" in html
    assert "Regime Ignition Table" in html
    assert "Interaction Stability / Fragility" in html
    assert "Tradeable Regime Candidate Ledger" in html
    assert "Live-Decision Readiness Screen" in html
