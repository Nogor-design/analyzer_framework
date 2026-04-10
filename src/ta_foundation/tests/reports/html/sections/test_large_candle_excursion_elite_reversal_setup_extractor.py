from __future__ import annotations

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.sections.large_candle_excursion_elite_reversal_setup_extractor import (
    render_large_candle_excursion_elite_reversal_setup_extractor,
)


def test_elite_reversal_section_renders() -> None:
    pkg = AnalysisPackage(
        run_id="run1",
        metadata={
            "derived": {
                "large_candle_excursion_findings": {
                    "elite_reversal_setup_extractor": {
                        "enabled": True,
                        "repair_summary": {
                            "decision_engine_broken": False,
                            "probabilities_valid": True,
                            "what_was_broken": "x",
                            "what_changed": "y",
                        },
                        "elite_setups": [
                            {
                                "conditions": {"early_path_class": "explosive_start", "session": "ny_open", "timeframe": 1},
                                "n": 44,
                                "failure_rate": 14.0,
                                "scalp_rate": 20.0,
                                "expansion_rate": 41.0,
                                "runner_rate": 25.0,
                                "avg_mfe": 82.0,
                                "avg_mae": 32.0,
                                "mfe_mae": 2.56,
                                "elite_score": 0.71,
                                "recommended_action": "hold_for_runner",
                                "strategy": {"early_validation": ["midpoint reclaimed within 2 bars"]},
                                "comparison": {
                                    "baseline_reversal": {"failure_rate": 28.0, "runner_rate": 12.0, "mfe_mae": 1.3},
                                    "family_baseline": {"failure_rate": 20.0, "runner_rate": 18.0, "mfe_mae": 1.8},
                                    "elite_subset": {"failure_rate": 14.0, "runner_rate": 25.0, "mfe_mae": 2.56},
                                },
                            }
                        ],
                        "near_miss_setups": [
                            {
                                "conditions": {"early_path_class": "orderly_start"},
                                "n": 12,
                                "failure_rate": 10.0,
                                "runner_rate": 30.0,
                                "rejection_reasons": ["low N (12 < 25)"],
                            }
                        ],
                        "research_answers": {"valid_probability_slices": {"holds": True}},
                    }
                }
            }
        },
    )

    html = render_large_candle_excursion_elite_reversal_setup_extractor({"packages": {"run1": pkg}, "options": {}})

    assert "Elite Reversal Setup Extractor" in html
    assert "Rejected Near-Miss Setups" in html
    assert "hold_for_runner" in html
