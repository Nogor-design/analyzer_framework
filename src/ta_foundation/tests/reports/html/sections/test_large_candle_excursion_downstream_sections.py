from __future__ import annotations

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_chains import render_large_candle_excursion_discovery_chains
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_summary import render_large_candle_excursion_discovery_summary
from ta_foundation.reports.html.sections.large_candle_excursion_findings_executive_summary import render_large_candle_excursion_findings_executive_summary
from ta_foundation.reports.html.sections.large_candle_excursion_findings_interactions import render_large_candle_excursion_findings_interactions
from ta_foundation.reports.html.sections.large_candle_excursion_recursive_edge_search import render_large_candle_excursion_recursive_edge_search


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


def test_findings_interaction_section_renders_attempted_candidates() -> None:
    pkg = AnalysisPackage(
        run_id="run1",
        metadata={
            "derived": {
                "large_candle_excursion_findings": {
                    "enabled": True,
                    "has_source": True,
                    "strong_context_effects": {"volume": [], "structure": [], "volatility": []},
                    "strongest_interactions": [],
                    "interaction_diagnostics": {
                        "attempted": [
                            {
                                "condition_combination": "vol=lt_0_8x & size=75-100",
                                "event_count": 22,
                                "win_rate": 55.0,
                                "score": 0.42,
                                "rejection_reason": "low sample size",
                            }
                        ]
                    },
                }
            }
        },
    )
    html = render_large_candle_excursion_findings_interactions({"packages": {"run1": pkg}, "options": {}})
    assert "Attempted Interaction Candidates" in html
    assert "low sample size" in html


def test_discovery_chain_section_renders_attempted_when_none_passed() -> None:
    pkg = AnalysisPackage(
        run_id="run1",
        metadata={
            "derived": {
                "large_candle_excursion_discovery": {
                    "enabled": True,
                    "has_source": True,
                    "interaction_chaining": {
                        "candidates": [],
                        "attempted": [
                            {
                                "base_setup": "reverse | tf=5m",
                                "chain_conditions": ["x", "y"],
                                "incremental_improvement": 0.01,
                                "composite_score": 0.51,
                                "rejection_reason": "insufficient improvement",
                            }
                        ],
                    },
                }
            }
        },
    )
    html = render_large_candle_excursion_discovery_chains({"packages": {"run1": pkg}, "options": {}})
    assert "Attempted Chains" in html
    assert "insufficient improvement" in html


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


def test_recursive_edge_search_section_renders_payload() -> None:
    pkg = AnalysisPackage(
        run_id="run1",
        metadata={
            "derived": {
                "large_candle_excursion_findings": {
                    "enabled": True,
                    "has_source": True,
                    "recursive_edge_search": {
                        "enabled": True,
                        "search_configuration": {"seed_types_used": ["decision_rule"], "max_depth": 2, "max_children_per_node": 8, "max_total_nodes": 50, "promotion_rules": {}, "pruning_rules": {}, "scoring_formula": {}},
                        "seed_summary": [{"seed_type": "decision_rule", "filters": {"early_path_class": "explosive_start"}, "n": 88}],
                        "roots": [{"seed_type": "decision_rule", "filters": {"early_path_class": "explosive_start"}, "metrics": {"n": 88, "branch_score": 0.62}, "children_tested": [{"depth": 1, "filters": {"early_path_class": "explosive_start", "session": "asia"}, "status": "promoted", "reason": "runner +8pp", "metrics": {"n": 34, "fail_rate": 2.0, "expansion_rate": 91.0, "runner_rate": 71.0, "mfe_mae": 1.9, "branch_score": 0.71}}]}],
                        "best_promoted_branches": [],
                        "dead_end_branches": [],
                        "final_promoted_candidates": [],
                        "research_questions": {},
                        "strategy_handoff": [],
                    },
                }
            }
        },
    )
    html = render_large_candle_excursion_recursive_edge_search({"packages": {"run1": pkg}, "options": {}})
    assert "Recursive Edge Search" in html
    assert "runner +8pp" in html
