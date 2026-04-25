from __future__ import annotations

from typing import Any, Dict, List

from ta_foundation.analysis.large_candle_excursion.strategy_blueprint_exporter import (
    compute_strategy_blueprint_exporter,
    DEFAULT_BLUEPRINT_EXPORTER_CONFIG,
)


def _ledger_row(**overrides: Any) -> Dict[str, Any]:
    base = {
        "candidate_name": "first_large_after_failed_continuation x midpoint_reclaim_yes",
        "onset_condition": "first_large_after_failed_continuation",
        "early_path_condition": "midpoint_reclaim_yes",
        "candidate_action": "hold",
        "n": 1432,
        "unique_days": 28,
        "dominant_session": "london_ny_overlap",
        "session_concentration_pct": 58.0,
        "win_rate": 92.6,
        "fail_rate": 1.5,
        "runner_rate": 62.4,
        "expectancy_ticks": 6.83,
        "mfe_mae_ratio": 2.1,
        "cluster_participation_rate": 82.6,
        "median_cluster_length": 3,
        "median_decay_minutes": 19.0,
        "time_split_stability": 0.65,
        "confidence_label": "paper_test_candidate",
    }
    base.update(overrides)
    return base


def _event(**kw: Any) -> Dict[str, Any]:
    base = {
        "dt": "2026-01-01T07:10:00-07:00",
        "size_ticks": 40,
        "lookback": 20,
        "basis": "range",
        "threshold_mode": "multiplier",
        "threshold_value": 1.5,
        "direction": 1,
        "prev_was_failed_continuation": True,
        "did_price_reclaim_signal_midpoint": True,
        "did_price_break_signal_extreme_again": False,
        "early_fav_2bar_ticks": 22.0,  # 55%
        "early_adv_2bar_ticks": 4.0,   # 10%
        "session_bucket": "london_ny_overlap",
    }
    base.update(kw)
    return base


def _findings_payload(with_validation: bool = True) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "config": {"friction": {"commission_per_trade": 4.18, "tick_value": 5.0, "slippage_ticks_per_side": 1}},
        "regime_discovery": {
            "config": {
                "onset_detection": {
                    "quiet_lookback_minutes": 60,
                    "quiet_max_prior_signals": 1,
                    "vwap_stretch_atr": 0.75,
                    "volume_multiple": 2.5,
                    "direction_streak_min": 3,
                    "range_expansion_multiple": 1.5,
                    "failed_continuation_lookback_signals": 3,
                },
            },
            "onset_path_interaction_analysis": {
                "tradeable_regime_candidate_ledger": [_ledger_row()],
            },
        },
        "edge_validation_engine": {
            "validated_candidates": [
                {
                    "candidate_name": "rule:first_large_after_failed_continuation",
                    "validation_label": "stable_edge",
                    "filters": {"onset_condition": "first_large_after_failed_continuation", "early_path_class": "midpoint_reclaim_yes"},
                    "out_of_sample": {
                        "n": 120,
                        "avg_mae": 24.0,
                        "friction_viability": "friction-viable",
                        "net_expectancy_after_friction_ticks": 4.5,
                    },
                }
            ],
        } if with_validation else {},
        "strategy_construction_engine": {
            "constructed_strategies": [
                {
                    "archetype": "runner_reversal",
                    "source_candidate": "rule:first_large_after_failed_continuation",
                    "eligible_market_context": {"early_path_class": "midpoint_reclaim_yes"},
                    "deployment_bucket": "paper_test_ready",
                    "deployment_score": 0.73,
                    "strategy_spec": {},
                }
            ],
        },
        "reversal_decision_engine": {},
    }
    return payload


def _upstream_config() -> Dict[str, Any]:
    return {
        "timeframes": [1, 2, 3, 5],
        "candle_size": {
            "average_lookbacks": [5, 10, 20],
            "threshold_mode": "multiplier",
            "threshold_multipliers": [1.5, 2.0, 2.5],
        },
        "min_body_ticks": 2,
        "min_range_ticks": 4,
        "atr_period": 14,
    }


def test_exporter_emits_blueprint_for_valid_candidate() -> None:
    events: List[Dict[str, Any]] = [_event() for _ in range(40)]
    out = compute_strategy_blueprint_exporter(
        findings_payload=_findings_payload(),
        events_sample=events,
        upstream_config=_upstream_config(),
    )

    assert out["enabled"] is True
    assert len(out["blueprints"]) == 1
    bp = out["blueprints"][0]
    assert bp["blueprint_id"]
    assert bp["provenance"]["onset_condition"] == "first_large_after_failed_continuation"
    assert bp["provenance"]["early_path_condition"] == "midpoint_reclaim_yes"
    assert bp["direction_policy"] == "counter_to_failed_continuation"
    assert bp["timeframe_minutes"] == 1


def test_blueprint_fields_are_scalar_or_container_only() -> None:
    """Every leaf in the blueprint should be scalar, None, or a list of scalars/dicts.
    Nothing should be a DataFrame, callable, etc."""
    import types

    events = [_event() for _ in range(30)]
    out = compute_strategy_blueprint_exporter(
        findings_payload=_findings_payload(),
        events_sample=events,
        upstream_config=_upstream_config(),
    )
    bp = out["blueprints"][0]

    def _check(value: Any, path: str) -> None:
        if value is None or isinstance(value, (bool, int, float, str)):
            return
        if isinstance(value, list):
            for i, item in enumerate(value):
                _check(item, f"{path}[{i}]")
            return
        if isinstance(value, dict):
            for k, v in value.items():
                _check(v, f"{path}.{k}")
            return
        assert False, f"Non-serialisable value at {path}: {type(value).__name__}"

    _check(bp, "blueprint")


def test_sweep_dominant_picks_highest_count_combo() -> None:
    events: List[Dict[str, Any]] = []
    # 20 events at (lookback=10, thresh=1.5)
    for _ in range(20):
        events.append(_event(lookback=10, threshold_value=1.5))
    # 30 events at (lookback=20, thresh=2.0) — should win
    for _ in range(30):
        events.append(_event(lookback=20, threshold_value=2.0))
    # 8 events at (lookback=5, thresh=2.5)
    for _ in range(8):
        events.append(_event(lookback=5, threshold_value=2.5))

    out = compute_strategy_blueprint_exporter(
        findings_payload=_findings_payload(),
        events_sample=events,
        upstream_config=_upstream_config(),
    )
    bp = out["blueprints"][0]
    dom = bp["onset_detection"]["candle_size"]["sweep_dominant"]
    assert dom is not None
    assert dom["lookback_bars"] == 20
    assert dom["threshold_value"] == 2.0
    assert dom["n_events_matched"] == 30


def test_stop_formula_mirrors_construction_engine() -> None:
    events = [_event() for _ in range(25)]
    payload = _findings_payload()
    # OOS avg_mae = 24 ticks; formula = 24 * 1.3 = 31.2, capped at 100
    payload["edge_validation_engine"]["validated_candidates"][0]["out_of_sample"]["avg_mae"] = 24.0

    out = compute_strategy_blueprint_exporter(
        findings_payload=payload,
        events_sample=events,
        upstream_config=_upstream_config(),
    )
    stop = out["blueprints"][0]["stop_rule"]
    assert stop["recommended_stop_ticks"] == 31
    assert stop["capped"] is False
    assert stop["max_stop_ticks"] == 100


def test_stop_capped_when_avg_mae_exceeds_cap() -> None:
    events = [_event() for _ in range(25)]
    payload = _findings_payload()
    # avg_mae = 200 → 200 * 1.3 = 260 → capped at 100
    payload["edge_validation_engine"]["validated_candidates"][0]["out_of_sample"]["avg_mae"] = 200.0

    out = compute_strategy_blueprint_exporter(
        findings_payload=payload,
        events_sample=events,
        upstream_config=_upstream_config(),
    )
    stop = out["blueprints"][0]["stop_rule"]
    assert stop["recommended_stop_ticks"] == 100
    assert stop["capped"] is True


def test_session_filter_reflects_dominant_session() -> None:
    events = [_event() for _ in range(25)]
    out = compute_strategy_blueprint_exporter(
        findings_payload=_findings_payload(),
        events_sample=events,
        upstream_config=_upstream_config(),
    )
    sf = out["blueprints"][0]["session_filter"]
    assert sf["mode"] == "allowlist"
    assert sf["allowed_sessions"] == ["london_ny_overlap"]


def test_action_filter_rejects_unaccepted_actions() -> None:
    events = [_event() for _ in range(25)]
    payload = _findings_payload()
    payload["regime_discovery"]["onset_path_interaction_analysis"]["tradeable_regime_candidate_ledger"] = [
        _ledger_row(candidate_action="regime_shutdown"),
    ]
    out = compute_strategy_blueprint_exporter(
        findings_payload=payload,
        events_sample=events,
        upstream_config=_upstream_config(),
    )
    assert len(out["blueprints"]) == 0
    assert out["diagnostics"]["n_skipped"] == 1
    assert "action" in out["diagnostics"]["skipped"][0]["reason"]


def test_action_filter_rejects_low_sample_size() -> None:
    events = [_event() for _ in range(25)]
    payload = _findings_payload()
    payload["regime_discovery"]["onset_path_interaction_analysis"]["tradeable_regime_candidate_ledger"] = [
        _ledger_row(n=30),
    ]
    out = compute_strategy_blueprint_exporter(
        findings_payload=payload,
        events_sample=events,
        upstream_config=_upstream_config(),
        cfg={"min_n": 60},
    )
    assert len(out["blueprints"]) == 0
    assert "below min_n" in out["diagnostics"]["skipped"][0]["reason"]


def test_disabled_exporter_returns_empty() -> None:
    out = compute_strategy_blueprint_exporter(
        findings_payload=_findings_payload(),
        events_sample=[],
        upstream_config=_upstream_config(),
        cfg={"enabled": False},
    )
    assert out["enabled"] is False
    assert out["blueprints"] == []


def test_exporter_handles_missing_events_sample_gracefully() -> None:
    out = compute_strategy_blueprint_exporter(
        findings_payload=_findings_payload(),
        events_sample=[],
        upstream_config=_upstream_config(),
    )
    assert out["enabled"] is True
    bp = out["blueprints"][0]
    # sweep_dominant should be None when no events
    assert bp["onset_detection"]["candle_size"]["sweep_dominant"] is None
    # The default lookback/threshold should still come from upstream config
    assert bp["onset_detection"]["candle_size"]["lookback_bars"] == 20
    assert bp["onset_detection"]["candle_size"]["threshold_value"] == 1.5


def test_exporter_without_validation_result_still_emits_blueprint() -> None:
    events = [_event() for _ in range(25)]
    out = compute_strategy_blueprint_exporter(
        findings_payload=_findings_payload(with_validation=False),
        events_sample=events,
        upstream_config=_upstream_config(),
    )
    assert len(out["blueprints"]) == 1
    bp = out["blueprints"][0]
    # No validated candidate → fall back to ledger confidence_label
    assert bp["validation_gates"]["validation_label"] == "paper_test_candidate"


def test_direction_policy_enum_for_each_onset() -> None:
    events = [_event() for _ in range(25)]
    for onset, expected in [
        ("first_large_after_failed_continuation", "counter_to_failed_continuation"),
        ("first_large_after_directional_run", "counter_to_directional_run"),
        ("first_large_at_key_level_with_extended_vwap_stretch", "counter_to_level_rejection"),
        ("first_large_after_session_range_break", "continuation_of_range_break"),
        ("first_large_after_compression", "continuation_of_compression_breakout"),
    ]:
        payload = _findings_payload()
        payload["regime_discovery"]["onset_path_interaction_analysis"]["tradeable_regime_candidate_ledger"] = [
            _ledger_row(onset_condition=onset),
        ]
        out = compute_strategy_blueprint_exporter(
            findings_payload=payload,
            events_sample=events,
            upstream_config=_upstream_config(),
        )
        assert len(out["blueprints"]) == 1, f"no blueprint for {onset}"
        assert out["blueprints"][0]["direction_policy"] == expected, onset
