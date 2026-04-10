from __future__ import annotations

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.sections.large_candle_excursion_findings_decision_engine import (
    render_large_candle_excursion_findings_decision_engine,
)


def test_decision_engine_section_renders() -> None:
    pkg = AnalysisPackage(
        run_id="run1",
        metadata={
            "derived": {
                "large_candle_excursion_findings": {
                    "enabled": True,
                    "has_source": True,
                    "reversal_decision_engine": {
                        "enabled": True,
                        "strong_runner_definition": "adv_pct >= 100%",
                        "baseline": {
                            "n": 100,
                            "failure_rate": 20.0,
                            "scalp_rate": 30.0,
                            "expansion_rate": 40.0,
                            "runner_rate": 10.0,
                            "mfe_mae": 1.8,
                        },
                        "tables": {
                            "outcome_by_early_path_class": [
                                {
                                    "group_key": {"early_path_class": "explosive_start"},
                                    "n": 40,
                                    "failure_rate": 10.0,
                                    "scalp_rate": 20.0,
                                    "expansion_rate": 50.0,
                                    "runner_rate": 20.0,
                                }
                            ]
                        },
                        "decision_rules": [
                            {
                                "conditions": {"early_path_class": "explosive_start", "session": "ny_open"},
                                "recommended_action": "hold_for_runner",
                                "n": 40,
                                "failure_rate": 10.0,
                                "expansion_rate": 50.0,
                                "runner_rate": 20.0,
                                "lift_vs_baseline_runner_pp": 10.0,
                                "mfe_mae": 2.0,
                            }
                        ],
                        "research_questions": {"press_runner_supported": False},
                    },
                }
            }
        },
    )
    html = render_large_candle_excursion_findings_decision_engine({"packages": {"run1": pkg}, "options": {}})
    assert "Reversal Decision Engine Findings" in html
    assert "Extracted Mechanical Decision Rules" in html
    assert "hold_for_runner" in html
