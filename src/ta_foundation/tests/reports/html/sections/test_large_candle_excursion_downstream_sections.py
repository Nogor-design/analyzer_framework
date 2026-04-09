from __future__ import annotations

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_chains import render_large_candle_excursion_discovery_chains
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_summary import render_large_candle_excursion_discovery_summary
from ta_foundation.reports.html.sections.large_candle_excursion_findings_executive_summary import render_large_candle_excursion_findings_executive_summary
from ta_foundation.reports.html.sections.large_candle_excursion_findings_interactions import render_large_candle_excursion_findings_interactions


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
