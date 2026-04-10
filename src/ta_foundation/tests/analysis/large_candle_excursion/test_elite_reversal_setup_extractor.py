from __future__ import annotations

from ta_foundation.analysis.large_candle_excursion.elite_reversal_setup_extractor import (
    compute_elite_reversal_setup_extractor,
)


def test_elite_extractor_returns_ranked_setups() -> None:
    decision_engine = {
        "enabled": True,
        "baseline": {
            "n": 180,
            "failure_rate": 28.0,
            "runner_rate": 12.0,
            "scalp_rate": 36.0,
            "expansion_rate": 24.0,
            "mfe_mae": 1.3,
        },
        "tables": {
            "outcome_by_early_path_class": [
                {
                    "group_key": {"early_path_class": "explosive_start"},
                    "n": 80,
                    "failure_rate": 18.0,
                    "runner_rate": 21.0,
                    "scalp_rate": 25.0,
                    "expansion_rate": 36.0,
                    "avg_mfe": 76.0,
                    "avg_mae": 34.0,
                    "mfe_mae": 2.2,
                }
            ],
            "outcome_by_early_path_and_session": [
                {
                    "group_key": {"early_path_class": "explosive_start", "session": "ny_open", "timeframe": 1, "candle_bucket": "25-50"},
                    "n": 44,
                    "failure_rate": 14.0,
                    "runner_rate": 25.0,
                    "scalp_rate": 20.0,
                    "expansion_rate": 41.0,
                    "avg_mfe": 82.0,
                    "avg_mae": 32.0,
                    "mfe_mae": 2.56,
                }
            ],
        },
        "decision_rules": [
            {
                "conditions": {
                    "early_path_class": "explosive_start",
                    "session": "ny_open",
                    "timeframe": 1,
                    "candle_bucket": "25-50",
                    "vwap_stretch_bucket": "extended",
                },
                "n": 44,
                "failure_rate": 14.0,
                "scalp_rate": 20.0,
                "expansion_rate": 41.0,
                "runner_rate": 25.0,
                "avg_mfe": 82.0,
                "avg_mae": 32.0,
                "mfe_mae": 2.56,
                "lift_vs_baseline_runner_pp": 13.0,
                "lift_vs_baseline_failure_pp": -14.0,
                "sample_quality": 0.6,
                "recommended_action": "hold_for_runner",
            }
        ],
    }

    out = compute_elite_reversal_setup_extractor(decision_engine, cfg={"top_n": 3, "min_n": 20})

    assert out.get("enabled") is True
    assert out.get("repair_summary", {}).get("probabilities_valid") is True
    assert out.get("elite_setups")
    assert out["elite_setups"][0].get("runner_rate", 0.0) >= 20.0
