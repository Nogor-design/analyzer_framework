from __future__ import annotations

from ta_foundation.analysis.large_candle_excursion.edge_validation_engine import compute_edge_validation_engine
from ta_foundation.analysis.large_candle_excursion.strategy_construction_engine import compute_strategy_construction_engine


def _event(i: int, *, fav2: float, adv2: float, adv: float, fav: float, session: str) -> dict:
    return {
        "dt": f"2026-01-01T07:{i:02d}:00-07:00",
        "window_minutes": 30,
        "trade_mode": "reverse",
        "size_ticks": 20.0,
        "adv_ticks": adv,
        "fav_ticks": fav,
        "early_fav_2bar_ticks": fav2,
        "early_adv_2bar_ticks": adv2,
        "did_price_reclaim_signal_midpoint": True,
        "did_price_break_signal_extreme_again": False,
        "session_bucket": session,
        "trend_alignment_label": "countertrend_exhaustion",
        "vwap_ext_bucket": "extended",
        "directional_context_label": "trend_exhaustion",
        "level_interaction_label": "approaching",
        "candle_bucket": "25-50",
        "tf_minutes": 1,
        "direction": -1,
    }


def test_edge_validation_engine_outputs_classified_candidates() -> None:
    events = []
    for i in range(70):
        events.append(_event(i, fav2=11.0, adv2=2.0, adv=22.0, fav=3.0, session="asia"))
    for i in range(70, 100):
        events.append(_event(i, fav2=8.0, adv2=4.0, adv=18.0, fav=5.0, session="asia"))

    findings_payload = {
        "config": {},
        "reversal_decision_engine": {
            "decision_rules": [
                {
                    "conditions": {
                        "early_path_class": "explosive_start",
                        "session": "asia",
                        "timeframe": 1,
                        "candle_bucket": "25-50",
                    }
                }
            ],
            "tables": {
                "outcome_by_early_path_class": [
                    {"group_key": {"early_path_class": "explosive_start"}, "n": 80, "runner_rate": 75.0, "failure_rate": 5.0}
                ]
            },
        },
        "elite_reversal_setup_extractor": {
            "elite_setups": [
                {
                    "conditions": {
                        "early_path_class": "explosive_start",
                        "session": "asia",
                        "timeframe": 1,
                        "candle_bucket": "25-50",
                    }
                }
            ]
        },
        "recursive_edge_search": {
            "final_promoted_candidates": [
                {"filters": {"early_path_class": "explosive_start", "session": "asia", "tf_minutes": 1, "candle_bucket": "25-50"}}
            ]
        },
        "setup_families": [
            {"family_key": {"trade_mode": "reverse", "direction": -1, "tf_minutes": 1, "candle_bucket": "25-50"}}
        ],
    }
    out = compute_edge_validation_engine(findings_payload, events, cfg={"minimum_samples": {"is_min_n": 10, "oos_min_n": 10}})

    assert out.get("enabled") is True
    assert out.get("validated_candidates")
    assert out.get("validation_leaderboard")
    assert any(r.get("validation_label") in ("stable_edge", "acceptable_degradation", "fragile_edge", "likely_overfit") for r in out.get("validated_candidates") or [])


def test_edge_validation_marks_oos_candidate_friction_invalid() -> None:
    events = []
    for i in range(70):
        events.append(_event(i, fav2=11.0, adv2=2.0, adv=22.0, fav=3.0, session="asia"))
    for i in range(70, 100):
        events.append(_event(i, fav2=8.0, adv2=4.0, adv=18.0, fav=5.0, session="asia"))

    findings_payload = {
        "config": {},
        "reversal_decision_engine": {
            "decision_rules": [
                {"conditions": {"early_path_class": "explosive_start", "session": "asia", "timeframe": 1, "candle_bucket": "25-50"}}
            ],
            "tables": {"outcome_by_early_path_class": []},
        },
    }
    out = compute_edge_validation_engine(
        findings_payload,
        events,
        cfg={
            "minimum_samples": {"is_min_n": 10, "oos_min_n": 10},
            "friction": {
                "commission_per_trade": 4.0,
                "tick_value": 5.0,
                "slippage_ticks_per_side": 1,
                "target_percent": 25,
                "min_target_ticks": 6,
                "min_net_target_ticks": 2,
                "min_post_friction_rr": 0.25,
            },
        },
    )

    top = out["validated_candidates"][0]
    assert top["out_of_sample"]["friction_viability"] == "friction-invalid"
    assert top["validation_label"] == "friction_invalid"
    assert out["friction_invalid_candidates"]


def test_strategy_construction_rejects_friction_invalid_candidate() -> None:
    findings_payload = {
        "edge_validation_engine": {
            "enabled": True,
            "validated_candidates": [
                {
                    "candidate_name": "decision_rule:{early_path_class=explosive_start}",
                    "source_type": "decision_rule",
                    "validation_label": "stable_edge",
                    "filters": {"early_path_class": "explosive_start"},
                    "out_of_sample": {
                        "n": 30,
                        "runner_rate": 70.0,
                        "expansion_rate": 80.0,
                        "fail_rate": 5.0,
                        "avg_mae": 12.0,
                        "friction_viability": "friction-invalid",
                        "net_expectancy_after_friction_ticks": -1.0,
                    },
                }
            ],
        },
        "reversal_decision_engine": {"threshold_discovery": [], "decision_rules": []},
    }

    out = compute_strategy_construction_engine(findings_payload, cfg={})

    assert out["constructed_strategies"] == []
    assert out["rejected_construction_candidates"][0]["reason"] == "friction_invalid"

