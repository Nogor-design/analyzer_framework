from __future__ import annotations

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_diagnostics import render_large_candle_excursion_discovery_diagnostics
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_refinement import render_large_candle_excursion_discovery_refinement
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_robustness import render_large_candle_excursion_discovery_robustness
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_summary import render_large_candle_excursion_discovery_summary
from ta_foundation.reports.html.sections.large_candle_excursion_findings_executive_summary import render_large_candle_excursion_findings_executive_summary
from ta_foundation.reports.html.sections.large_candle_excursion_findings_interactions import render_large_candle_excursion_findings_interactions
from ta_foundation.reports.html.sections.large_candle_excursion_findings_next_tests import render_large_candle_excursion_findings_next_tests
from ta_foundation.reports.html.sections.large_candle_excursion_findings_top_discoveries import render_large_candle_excursion_findings_top_discoveries


def test_findings_section_renders_payload() -> None:
    pkg = AnalysisPackage(
        run_id="run1",
        metadata={
            "derived": {
                "large_candle_excursion_findings": {
                    "enabled": True,
                    "has_source": True,
                    "executive_summary": ["Best reverse setup found."],
                }
            }
        },
    )
    html = render_large_candle_excursion_findings_executive_summary({"packages": {"run1": pkg}, "options": {}})
    assert "Executive Summary" in html
    assert "Best reverse setup found." in html


def test_discovery_section_truthful_missing_source_state() -> None:
    pkg = AnalysisPackage(
        run_id="run1",
        metadata={
            "derived": {
                "large_candle_excursion_discovery": {
                    "enabled": True,
                    "has_source": False,
                    "message": "source analytics missing",
                }
            }
        },
    )
    html = render_large_candle_excursion_discovery_summary({"packages": {"run1": pkg}, "options": {}})
    assert "source analytics missing" in html


def test_discovery_sections_render_plateau_timesplit_tradability_and_chain_rejections() -> None:
    payload = {
        "enabled": True,
        "has_source": True,
        "summary": {
            "plateau_assessment": "stable_plateau",
            "chain_value_assessment": "no_chain_added_value",
            "time_split_assessment": "acceptable_through_time",
            "major_cautions": [],
        },
        "refinement": {
            "candidates": [
                {"parent_setup": "p", "refined_target_percent": 50, "child_setup": "c", "child_score": 0.7, "score_delta_vs_parent": 0.02, "child_win_rate": 61.0, "child_n_events": 100}
            ]
        },
        "plateau_analysis": [
            {
                "setup_definition": "reverse | tf=5m | bucket=75-100 | target=50%",
                "plateau_label": "stable_plateau",
                "neighbors": [
                    {"neighbor_type": "self", "target_percent": 50, "threshold_value": 2.0, "candle_bucket": "75-100", "score": 0.71, "win_rate": 62.1, "n_events": 100, "robustness_score": 0.8, "delta_from_parent": 0.0}
                ],
            }
        ],
        "robustness_validation": {"candidates": [{"setup_definition": "x", "neighbor_stability": 0.8, "split_instability_penalty": 0.1, "oos_check_required": False, "oos_penalty": 0.0, "robustness_score": 0.7}]},
        "time_split_validation": [{"setup_definition": "x", "splits": [{"split_id": "split_1", "n_events": 30, "win_rate": 56.0, "score": 0.7, "expectancy_ticks": 2.1}]}],
        "final_discoveries": [{"setup_definition": "x", "avg_target_ticks": 6.5, "median_target_ticks": 6.0, "avg_favorable_ticks": 14.0, "median_favorable_ticks": 11.0, "avg_adverse_ticks": 8.0, "median_adverse_ticks": 7.0, "stop_hit_rate": 22.0, "expectancy_ticks": 3.5}],
        "diagnostics": {"n_chain_rejected": 5},
        "chain_rejection_diagnostics": {"attempted": [{"base_setup": "x", "reason": "insufficient_incremental_improvement", "details": "improvement too small"}]},
    }
    pkg = AnalysisPackage(run_id="run1", metadata={"derived": {"large_candle_excursion_discovery": payload}})

    ctx = {"packages": {"run1": pkg}, "options": {}}
    summary_html = render_large_candle_excursion_discovery_summary(ctx)
    refinement_html = render_large_candle_excursion_discovery_refinement(ctx)
    robustness_html = render_large_candle_excursion_discovery_robustness(ctx)
    diagnostics_html = render_large_candle_excursion_discovery_diagnostics(ctx)

    assert "stable plateau" in summary_html.lower()
    assert "no chained setup added enough value" in summary_html.lower()
    assert "acceptable performance across time splits" in summary_html.lower()
    assert "Plateau / Neighbor Stability" in refinement_html
    assert "Time-Split Robustness" in robustness_html
    assert "Tradability Metrics" in robustness_html
    assert "Chaining Rejection Diagnostics" in diagnostics_html


def test_findings_sections_render_dedup_next_tests_interaction_diagnostics_and_plateau_tables() -> None:
    payload = {
        "enabled": True,
        "has_source": True,
        "executive_summary": ["reverse advantage strengthens in weak-close buckets"],
        "top_discoveries": [
            {
                "setup_definition": "reverse | tf=5m | bucket=75-100 | target=25%",
                "trade_mode": "reverse",
                "tf_minutes": 5,
                "candle_bucket": "75-100",
                "target_percent": 25,
                "n_events": 120,
                "win_rate": 63.0,
                "expectancy_ticks": 2.4,
                "composite_score": 0.7,
                "stability_score": 0.62,
                "avg_target_ticks": 5.1,
                "median_target_ticks": 5.0,
                "avg_favorable_ticks": 13.2,
                "avg_adverse_ticks": 8.1,
                "median_favorable_ticks": 11.0,
                "median_adverse_ticks": 7.0,
            }
        ],
        "plateau_analysis": {
            "best_continuation_neighbors": [],
            "best_reverse_neighbors": [
                {"target_percent": 15, "win_rate": 61.0, "n_events": 100, "score": 0.68, "stability_score": 0.6, "delta_from_anchor": -0.02},
                {"target_percent": 25, "win_rate": 63.0, "n_events": 120, "score": 0.70, "stability_score": 0.62, "delta_from_anchor": 0.0},
            ],
        },
        "time_split_stability": {
            "best_continuation_splits": [],
            "best_reverse_splits": [
                {"split_id": "split_1", "n_events": 40, "win_rate": 62.0, "score": 0.7},
                {"split_id": "split_2", "n_events": 40, "win_rate": 60.0, "score": 0.7},
            ],
        },
        "interaction_diagnostics": {
            "kept": [],
            "attempted": [
                {"interaction_name": "vol_x_size", "n_observations": 22, "score": 0.22, "edge_abs": 2.0, "rejection_reason": "weak_edge"}
            ],
        },
        "strong_context_effects": {"volume": [], "structure": [], "volatility": []},
        "strongest_interactions": [],
        "next_tests_ranked": [
            {"recommendation": "Refine target around 25%.", "frequency": 3},
            {"recommendation": "Retest OOS.", "frequency": 1},
        ],
        "next_tests": ["Refine target around 25%.", "Retest OOS."],
    }
    pkg = AnalysisPackage(run_id="run1", metadata={"derived": {"large_candle_excursion_findings": payload}})
    ctx = {"packages": {"run1": pkg}, "options": {}}

    top_html = render_large_candle_excursion_findings_top_discoveries(ctx)
    interactions_html = render_large_candle_excursion_findings_interactions(ctx)
    next_tests_html = render_large_candle_excursion_findings_next_tests(ctx)

    assert "Avg Target t" in top_html
    assert "Plateau / Neighbor Analysis" in top_html
    assert "Time-Split Stability" in top_html
    assert "No Rows Passed Final Filters" in interactions_html
    assert "weak_edge" in interactions_html
    assert "freq=3" in next_tests_html
