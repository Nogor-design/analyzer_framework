from __future__ import annotations

import yaml

from ta_foundation.analysis.large_candle_excursion.downstream_reports import (
    build_large_candle_excursion_discovery,
    build_large_candle_excursion_findings,
)
from ta_foundation.analysis.large_candle_excursion.recursive_edge_search import compute_recursive_edge_search


def _source_payload(include_interactions: bool = True) -> dict:
    interactions = (
        [
            {
                "vol_bucket": "lt_0_8x",
                "candle_bucket": "75-100",
                "n_observations": 80,
                "cont_win_rate": 41.0,
                "rev_win_rate": 69.0,
                "better_mode": "reverse",
                "mean_fav_ticks": 15.0,
            }
        ]
        if include_interactions
        else [
            {
                "vol_bucket": "lt_0_8x",
                "candle_bucket": "75-100",
                "n_observations": 12,
                "cont_win_rate": 50.0,
                "rev_win_rate": 53.0,
                "better_mode": "reverse",
                "mean_fav_ticks": 11.0,
            }
        ]
    )

    trade_events_sample = []
    for i in range(24):
        trade_events_sample.append(
            {
                "event_dt": f"2025-01-{(i % 28) + 1:02d}T10:{i:02d}:00-07:00",
                "trade_mode": "reverse",
                "tf_minutes": 5,
                "candle_bucket": "75-100",
                "target_percent": 50,
                "win": i % 3 != 0,
                "trade_fav_ticks": 10.0 + (i % 5),
                "trade_adv_ticks": 6.0 + (i % 3),
            }
        )

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
                    "avg_target_ticks": 9.6,
                    "median_target_ticks": 9.0,
                    "avg_trade_fav_ticks": 14.5,
                    "median_trade_fav_ticks": 14.0,
                    "avg_trade_adv_ticks": 9.0,
                    "median_trade_adv_ticks": 9.0,
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
                    "avg_target_ticks": 10.1,
                    "median_target_ticks": 10.0,
                    "avg_trade_fav_ticks": 16.1,
                    "median_trade_fav_ticks": 16.0,
                    "avg_trade_adv_ticks": 8.7,
                    "median_trade_adv_ticks": 8.0,
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
                    "avg_target_ticks": 12.5,
                    "median_target_ticks": 12.0,
                    "avg_trade_fav_ticks": 16.0,
                    "median_trade_fav_ticks": 15.9,
                    "avg_trade_adv_ticks": 9.4,
                    "median_trade_adv_ticks": 9.5,
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
                    "avg_target_ticks": 7.8,
                    "median_target_ticks": 8.0,
                    "avg_trade_fav_ticks": 12.0,
                    "median_trade_fav_ticks": 11.0,
                    "avg_trade_adv_ticks": 5.0,
                    "median_trade_adv_ticks": 5.0,
                },
            ],
            "trade_events_sample": trade_events_sample,
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
            "interactions": {"vol_x_size": interactions},
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
    assert "recursive_edge_search" in findings


def test_findings_deduplicates_and_ranks_next_tests() -> None:
    findings = build_large_candle_excursion_findings(
        _source_payload(),
        {"enabled": True, "output": {"max_next_tests": 3}},
    )
    assert len(findings["next_tests"]) <= 3
    assert findings["next_tests_ranked"]
    counts = [int(x["count"]) for x in findings["next_tests_ranked"]]
    assert counts == sorted(counts, reverse=True)


def test_findings_interaction_rejections_are_reported_when_none_pass() -> None:
    findings = build_large_candle_excursion_findings(
        _source_payload(include_interactions=False),
        {
            "enabled": True,
            "interactions": {
                "min_events": 50,
                "min_edge_pp": 5.0,
                "min_score": 0.7,
                "attempted_top_n": 5,
            },
        },
    )
    assert findings["strongest_interactions"] == []
    attempted = (findings.get("interaction_diagnostics") or {}).get("attempted") or []
    assert attempted
    assert attempted[0].get("rejection_reason")


def test_findings_neighbor_time_split_and_tradability_present() -> None:
    findings = build_large_candle_excursion_findings(_source_payload(), {"enabled": True})
    assert findings["neighbor_analysis"]
    assert findings["time_split_robustness"]
    top = findings["top_discoveries"][0]
    assert "tradability" in top
    assert top["tradability"]["avg_favorable_excursion"] is not None
    rec = findings.get("recursive_edge_search") or {}
    assert rec.get("enabled") is True
    assert rec.get("search_configuration") or rec.get("message")


def test_discovery_stages_and_diagnostics() -> None:
    discovery = build_large_candle_excursion_discovery(_source_payload(), {"enabled": True})
    assert discovery["enabled"] is True
    assert discovery["has_source"] is True
    assert discovery["broad_scan"]["n_evaluated"] >= discovery["broad_scan"]["n_retained"]
    assert discovery["refinement"]["candidates"]
    assert discovery["interaction_chaining"]["candidates"]
    assert discovery["robustness_validation"]["candidates"]
    assert discovery["final_discoveries"]
    assert discovery["diagnostics"]["n_final"] == len(discovery["final_discoveries"])


def test_discovery_chain_diagnostics_present_when_no_candidates_survive() -> None:
    discovery = build_large_candle_excursion_discovery(
        _source_payload(include_interactions=False),
        {"enabled": True, "stages": {"interaction_chaining": {"min_incremental_improvement": 0.50, "min_score": 0.95, "attempted_top_n": 5}}},
    )
    assert discovery["interaction_chaining"]["candidates"] == []
    assert discovery["interaction_chaining"]["attempted"]
    assert discovery["interaction_chaining"]["attempted"][0].get("rejection_reason")


def test_discovery_neighbor_and_time_split_payloads_exist() -> None:
    discovery = build_large_candle_excursion_discovery(_source_payload(), {"enabled": True})
    assert discovery["neighbor_analysis"]
    assert (discovery["robustness_validation"] or {}).get("time_splits")


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


def test_recursive_edge_search_accepts_tuple_family_keys() -> None:
    payload = {
        "config": {"reversal_decision_engine": {}},
        "setup_families": [{"family_key": ("reverse", -1, 1, "25-50")}],
        "next_tests": [],
        "reversal_decision_engine": {"decision_rules": []},
        "elite_reversal_setup_extractor": {"elite_setups": []},
    }
    events = [
        {
            "direction": -1,
            "size_ticks": 10,
            "adv_ticks": 8,
            "fav_ticks": 4,
            "early_fav_2bar_ticks": 4,
            "early_adv_2bar_ticks": 1,
            "did_price_reclaim_signal_midpoint": True,
            "did_price_break_signal_extreme_again": False,
            "trade_mode": "reverse",
            "tf_minutes": 1,
            "window_minutes": 30,
            "candle_bucket": "25-50",
            "session_bucket": "asia",
        }
    ] * 40
    out = compute_recursive_edge_search(payload, events, {"enabled": True})
    assert out["enabled"] is True
    assert "message" not in out
