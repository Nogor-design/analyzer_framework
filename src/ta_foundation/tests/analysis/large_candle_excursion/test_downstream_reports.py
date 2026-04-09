from __future__ import annotations

import yaml

from ta_foundation.analysis.large_candle_excursion.downstream_reports import (
    build_large_candle_excursion_discovery,
    build_large_candle_excursion_findings,
)


def _source_payload() -> dict:
    return {
        "enabled": True,
        "trade_analysis": {
            "enabled": True,
            "trade_combo_results": [
                {
                    "trade_mode": "continuation",
                    "direction": 1,
                    "tf_minutes": 5,
                    "lookback": 20,
                    "basis": "range",
                    "threshold_mode": "multiplier",
                    "threshold_value": 2.0,
                    "window_minutes": 15,
                    "target_percent": 50,
                    "candle_bucket": "75-100",
                    "n_events": 120,
                    "n_wins": 74,
                    "win_rate": 61.7,
                    "avg_trade_fav_ticks": 14.5,
                    "avg_trade_adv_ticks": 9.0,
                },
                {
                    "trade_mode": "reverse",
                    "direction": 1,
                    "tf_minutes": 5,
                    "lookback": 20,
                    "basis": "range",
                    "threshold_mode": "multiplier",
                    "threshold_value": 2.0,
                    "window_minutes": 15,
                    "target_percent": 50,
                    "candle_bucket": "75-100",
                    "n_events": 140,
                    "n_wins": 92,
                    "win_rate": 65.7,
                    "avg_trade_fav_ticks": 16.1,
                    "avg_trade_adv_ticks": 8.7,
                    "avg_target_ticks": 6.5,
                    "median_target_ticks": 6.0,
                    "median_trade_fav_ticks": 12.2,
                    "median_trade_adv_ticks": 7.4,
                },
                {
                    "trade_mode": "reverse",
                    "direction": 1,
                    "tf_minutes": 5,
                    "lookback": 20,
                    "basis": "range",
                    "threshold_mode": "multiplier",
                    "threshold_value": 2.0,
                    "window_minutes": 15,
                    "target_percent": 60,
                    "candle_bucket": "75-100",
                    "n_events": 110,
                    "n_wins": 70,
                    "win_rate": 63.6,
                    "avg_trade_fav_ticks": 16.0,
                    "avg_trade_adv_ticks": 9.4,
                    "avg_target_ticks": 7.1,
                    "median_target_ticks": 7.0,
                    "median_trade_fav_ticks": 12.0,
                    "median_trade_adv_ticks": 8.0,
                },
                {
                    "trade_mode": "reverse",
                    "direction": -1,
                    "tf_minutes": 1,
                    "lookback": 10,
                    "basis": "body",
                    "threshold_mode": "multiplier",
                    "threshold_value": 1.5,
                    "window_minutes": 10,
                    "target_percent": 50,
                    "candle_bucket": "50-75",
                    "n_events": 35,
                    "n_wins": 27,
                    "win_rate": 77.1,
                    "avg_trade_fav_ticks": 12.0,
                    "avg_trade_adv_ticks": 5.0,
                    "avg_target_ticks": 5.5,
                    "median_target_ticks": 5.0,
                    "median_trade_fav_ticks": 10.0,
                    "median_trade_adv_ticks": 4.5,
                },
            ],
            "trade_events_sample": [
                {
                    "dt": f"2026-01-0{idx}T08:0{idx}:00-07:00",
                    "trade_mode": "reverse",
                    "direction": 1,
                    "tf_minutes": 5,
                    "lookback": 20,
                    "basis": "range",
                    "threshold_value": 2.0,
                    "target_percent": 50,
                    "candle_bucket": "75-100",
                    "win": idx % 2 == 0,
                    "trade_fav_ticks": 14 + idx,
                    "trade_adv_ticks": 8 + idx / 2,
                }
                for idx in range(1, 10)
            ],
        },
        "context_analysis": {
            "enabled": True,
            "volume_context": {
                "enabled": True,
                "by_vol_bucket": [
                    {"vol_bucket": "lt_0_8x", "n_observations": 95, "cont_win_rate": 44.0, "rev_win_rate": 63.0, "better_mode": "reverse"}
                ],
            },
            "structure_context": {
                "enabled": True,
                "by_close_pos": [
                    {"close_pos_bucket": "top_10pct", "n_observations": 84, "cont_win_rate": 66.0, "rev_win_rate": 49.0, "better_mode": "continuation"}
                ],
            },
            "volatility_context": {
                "enabled": True,
                "by_atr_bucket": [
                    {"atr_bucket": "ge_2_0_atr", "n_observations": 76, "cont_win_rate": 41.0, "rev_win_rate": 68.0, "better_mode": "reverse"}
                ],
            },
            "interactions": {
                "vol_x_size": [
                    {"vol_bucket": "lt_0_8x", "candle_bucket": "75-100", "n_observations": 80, "cont_win_rate": 41.0, "rev_win_rate": 69.0, "better_mode": "reverse", "mean_fav_ticks": 15.0}
                ]
            },
        },
    }


def test_findings_populates_summary_rank_fragility_and_next_tests() -> None:
    findings = build_large_candle_excursion_findings(_source_payload(), {"enabled": True, "min_events": 30})
    assert findings["enabled"] is True
    assert findings["has_source"] is True
    assert findings["executive_summary"]
    assert findings["top_discoveries"]
    assert findings["top_discoveries"][0]["composite_score"] >= findings["top_discoveries"][-1]["composite_score"]
    assert any(w["type"] == "low_sample" for w in findings["fragility_warnings"])
    assert findings["next_tests"]
    assert findings["next_tests_ranked"]
    assert len(findings["next_tests"]) == len(set(findings["next_tests"]))
    assert findings["plateau_analysis"]["best_reverse_neighbors"]
    assert findings["time_split_stability"]["best_reverse_splits"]
    assert "avg_target_ticks" in findings["top_discoveries"][0]
    assert "median_adverse_ticks" in findings["top_discoveries"][0]
    assert findings["interaction_diagnostics"]["attempted"]


def test_discovery_stages_and_diagnostics() -> None:
    discovery = build_large_candle_excursion_discovery(_source_payload(), {"enabled": True})
    assert discovery["enabled"] is True
    assert discovery["has_source"] is True
    assert discovery["broad_scan"]["n_evaluated"] >= discovery["broad_scan"]["n_retained"]
    assert discovery["refinement"]["candidates"]
    assert discovery["interaction_chaining"]["candidates"]
    assert discovery["robustness_validation"]["candidates"]
    assert discovery["plateau_analysis"]
    assert discovery["time_split_validation"]
    assert discovery["chain_rejection_diagnostics"]["attempted"]
    assert discovery["final_discoveries"]
    assert discovery["diagnostics"]["n_final"] == len(discovery["final_discoveries"])
    assert "plateau_assessment" in discovery["summary"]
    assert "chain_value_assessment" in discovery["summary"]
    assert "time_split_assessment" in discovery["summary"]
    first_final = discovery["final_discoveries"][0]
    assert "avg_target_ticks" in first_final
    assert "median_favorable_ticks" in first_final
    assert "expectancy_ticks" in first_final


def test_missing_source_truthful_empty_state() -> None:
    findings = build_large_candle_excursion_findings(None, {"enabled": True})
    discovery = build_large_candle_excursion_discovery(None, {"enabled": True})
    assert findings["has_source"] is False
    assert "missing" in findings["message"]
    assert discovery["has_source"] is False
    assert "missing" in discovery["message"]


def test_yaml_config_blocks_parse_for_downstream_reports() -> None:
    raw = yaml.safe_load(
        """
large_candle_excursion_findings:
  enabled: true
  min_events: 30
  ranking:
    require_min_win_rate: 0.5
large_candle_excursion_discovery:
  enabled: true
  objective:
    min_events: 30
        """
    )
    assert raw["large_candle_excursion_findings"]["enabled"] is True
    assert raw["large_candle_excursion_findings"]["ranking"]["require_min_win_rate"] == 0.5
    assert raw["large_candle_excursion_discovery"]["enabled"] is True


def test_findings_interaction_rejection_diagnostics_when_kept_empty() -> None:
    src = _source_payload()
    src["context_analysis"]["interactions"]["vol_x_size"] = [
        {"vol_bucket": "x", "n_observations": 5, "cont_win_rate": 50.0, "rev_win_rate": 51.0, "better_mode": "reverse"}
    ]
    findings = build_large_candle_excursion_findings(src, {"enabled": True, "min_events": 30})
    assert findings["interaction_diagnostics"]["attempted"]
    assert not findings["interaction_diagnostics"]["kept"]
    assert findings["interaction_diagnostics"]["attempted"][0]["rejection_reason"] in {"low_sample", "weak_edge", "low_composite_score", "low_stability"}
    assert any("No interaction findings passed thresholds" in s for s in findings["executive_summary"])
