from __future__ import annotations

import json

import pandas as pd

from ta_foundation.analysis.large_candle_excursion import (
    adaptive_context_gate as gate_module,
)
from ta_foundation.analysis.large_candle_excursion.adaptive_context_gate import (
    CONTEXT_GATE_PROFILES,
    DEFAULT_CONTEXT_GATE_CONFIG,
    _apply_execution_capacity,
    _run_stream_replay,
    eligible_history,
    score_context_cell,
    select_context_policy,
    transition_gate_state,
)


DENVER = "America/Denver"


def _gate_config(**overrides):
    config = {
        **DEFAULT_CONTEXT_GATE_CONFIG,
        **CONTEXT_GATE_PROFILES["balanced"],
        "profile": "balanced",
        "max_stale_days": 30.0,
        "min_local_signals": 1,
        "min_unique_sessions": 1,
        "mode_margin_ticks": 1.0,
    }
    config.update(overrides)
    return config


def _event(
    day,
    minute="10:05",
    *,
    continuation=20.0,
    reversion=-20.0,
    trend_state="up",
    signal_side="bull",
    exit_delay=5,
):
    signal_dt = pd.Timestamp(f"{day} {minute}", tz=DENVER)
    entry_dt = signal_dt + pd.Timedelta(minutes=1)
    exit_known_dt = signal_dt + pd.Timedelta(minutes=exit_delay)
    signal_direction = 1 if signal_side == "bull" else -1
    return {
        "signal_dt": signal_dt,
        "entry_dt": entry_dt,
        "session_id": str(day),
        "time_bucket": "10:00-10:30",
        "trend_state": trend_state,
        "signal_side": signal_side,
        "context_dt": signal_dt,
        "close_vs_vwap": 1.0 if trend_state == "up" else -1.0,
        "vwap_slope_15": 1.0 if trend_state == "up" else -1.0,
        "return_60m": 0.01 if trend_state == "up" else -0.01,
        "trigger_type": "fresh_large_candle",
        "continuation": {
            "available": True,
            "entry_dt": entry_dt,
            "exit_known_dt": exit_known_dt,
            "trade_direction": signal_direction,
            "net_pnl_ticks": continuation,
            "capacity_eligible": True,
        },
        "reversion": {
            "available": True,
            "entry_dt": entry_dt,
            "exit_known_dt": exit_known_dt,
            "trade_direction": -signal_direction,
            "net_pnl_ticks": reversion,
            "capacity_eligible": True,
        },
    }


def _stream(events, signal_side="bull"):
    return {
        "lane_id": "tf1m|lb5|range|x1.5",
        "signal_side": signal_side,
        "timeframe": 1,
        "lookback": 5,
        "basis": "range",
        "multiplier": 1.5,
        "events": events,
    }


def _run_config(**gate_overrides):
    return {
        "tick_value": 5.0,
        "time_filter": {
            "enabled": True,
            "start": "10:00",
            "end": "10:29",
            "timezone": DENVER,
        },
        "outcome": {"max_concurrent_per_direction": 3},
        "context": {
            "timezone": DENVER,
            "session_anchor": "07:30",
            "time_bucket_minutes": 30,
        },
        "context_gate": gate_overrides,
    }


def _score_stub(
    *,
    trend_state="up",
    continuation=20.0,
    reversion=-20.0,
    lower_bound=10.0,
):
    asof = pd.Timestamp("2026-07-20 07:30", tz=DENVER)

    def level(mean):
        return {
            "n": 8,
            "effective_n": 7.5,
            "unique_sessions": 4,
            "weighted_mean_ticks": mean,
            "posterior_mean_ticks": mean,
            "lower_bound_ticks": lower_bound if mean == continuation else mean,
            "weighted_win_rate_pct": 75.0,
            "oldest_eligible_outcome_dt": asof - pd.Timedelta(days=5),
            "newest_eligible_outcome_dt": asof - pd.Timedelta(hours=1),
            "fast_posterior_mean_ticks": mean,
            "slow_posterior_mean_ticks": mean,
        }

    return {
        "decision_asof": asof,
        "time_bucket": "10:00-10:30",
        "trend_state": trend_state,
        "paired_history_n": 8,
        "local_n": 8,
        "local_unique_sessions": 4,
        "newest_local_outcome_dt": asof - pd.Timedelta(hours=1),
        "modes": {
            "continuation": {
                "local": level(continuation),
                "parent": level(continuation),
                "stream": level(continuation),
            },
            "reversion": {
                "local": level(reversion),
                "parent": level(reversion),
                "stream": level(reversion),
            },
        },
    }


def test_outcome_at_decision_asof_is_excluded_but_strictly_prior_is_eligible():
    asof = pd.Timestamp("2026-07-20 10:10", tz=DENVER)
    equal = _event("2026-07-20", exit_delay=5)
    equal["signal_dt"] = asof - pd.Timedelta(minutes=5)
    equal["continuation"]["exit_known_dt"] = asof
    equal["reversion"]["exit_known_dt"] = asof
    prior = _event("2026-07-20", minute="09:55", exit_delay=5)

    rows = eligible_history([equal, prior], asof, _gate_config())

    assert rows == [prior]


def test_hierarchical_score_records_all_levels_and_shrinks_local_to_parent():
    events = [
        _event("2026-07-17", continuation=30.0, reversion=-30.0),
        _event(
            "2026-07-18",
            continuation=-10.0,
            reversion=10.0,
            trend_state="down",
        ),
    ]
    other_bucket = _event(
        "2026-07-19",
        continuation=-50.0,
        reversion=50.0,
    )
    other_bucket["time_bucket"] = "10:30-11:00"
    events.append(other_bucket)

    score = score_context_cell(
        events,
        pd.Timestamp("2026-07-20 07:30", tz=DENVER),
        "10:00-10:30",
        "up",
        _gate_config(prior_strength=5.0),
    )

    continuation = score["modes"]["continuation"]
    assert set(continuation) == {"local", "parent", "stream"}
    assert continuation["local"]["n"] == 1
    assert continuation["parent"]["n"] == 2
    assert continuation["stream"]["n"] == 3
    assert continuation["local"]["unique_sessions"] == 1
    assert (
        continuation["parent"]["posterior_mean_ticks"]
        != continuation["parent"]["weighted_mean_ticks"]
    )
    assert (
        continuation["local"]["posterior_mean_ticks"]
        < continuation["local"]["weighted_mean_ticks"]
    )
    assert continuation["local"]["oldest_eligible_outcome_dt"] is not None
    assert continuation["local"]["newest_eligible_outcome_dt"] is not None


def test_aligned_only_rejects_attractive_structurally_opposing_mode():
    score = _score_stub(continuation=-10.0, reversion=25.0)

    assessment = select_context_policy(
        score,
        "bull",
        _gate_config(policy="aligned_only"),
    )

    assert assessment["target_state"] == "OFF"
    assert assessment["reason_code"] == "STRUCTURAL_MODE_MISMATCH"
    assert assessment["evidence_winner"] == "reversion"
    assert assessment["aligned_mode"] == "continuation"


def test_evidence_only_can_select_the_structurally_opposing_mode():
    score = _score_stub(continuation=-10.0, reversion=25.0)

    assessment = select_context_policy(
        score,
        "bull",
        _gate_config(policy="evidence_only"),
    )

    assert assessment["target_state"] == "ON"
    assert assessment["selected_mode"] == "reversion"
    assert assessment["mode_advantage_ticks"] == 35.0


def test_mixed_trend_and_stale_map_force_off():
    mixed = select_context_policy(
        _score_stub(trend_state="mixed"),
        "bull",
        _gate_config(),
    )
    stale_score = _score_stub()
    stale_score["newest_local_outcome_dt"] = (
        stale_score["decision_asof"] - pd.Timedelta(days=2)
    )
    stale = select_context_policy(
        stale_score,
        "bull",
        _gate_config(max_stale_days=1.0),
    )

    assert mixed["reason_code"] == "MIXED_TREND"
    assert stale["reason_code"] == "STALE_MAP"
    assert transition_gate_state("ON", stale)["state"] == "OFF"


def test_state_machine_off_watch_on_and_on_decaying_off():
    qualified = {
        "target_state": "ON",
        "reason_code": "QUALIFIED",
        "fast_score_decay": False,
    }
    weak = {
        "target_state": "WATCH",
        "reason_code": "LOWER_BOUND_NOT_POSITIVE",
    }
    failed = {
        "target_state": "OFF",
        "reason_code": "POSTERIOR_NOT_POSITIVE",
    }

    first = transition_gate_state("OFF", qualified)
    second = transition_gate_state(first["state"], qualified)
    decaying = transition_gate_state(second["state"], weak)
    off = transition_gate_state(decaying["state"], failed)
    recovered = transition_gate_state("DECAYING", qualified)

    assert (first["state"], second["state"]) == ("WATCH", "ON")
    assert first["reason_code"] == "WATCH_CONFIRMATION_REQUIRED"
    assert (decaying["state"], off["state"]) == ("DECAYING", "OFF")
    assert recovered["state"] == "ON"


def test_fast_score_decay_moves_on_to_decaying():
    assessment = {
        "target_state": "ON",
        "reason_code": "QUALIFIED",
        "fast_score_decay": True,
    }

    transition = transition_gate_state("ON", assessment)

    assert transition["state"] == "DECAYING"
    assert transition["reason_code"] == "FAST_SCORE_DECAY"


def test_daily_frozen_map_does_not_change_after_intraday_exit():
    events = [
        _event("2026-07-20"),
        _event("2026-07-21"),
        _event("2026-07-22", minute="10:05"),
        _event("2026-07-22", minute="10:20", continuation=-100.0),
    ]
    config = _run_config()
    gate_config = _gate_config(
        replay_protocol="daily_frozen",
        policy="aligned_only",
    )

    result = _run_stream_replay(
        events,
        _stream(events),
        config,
        gate_config,
    )
    repeated = _run_stream_replay(
        events,
        _stream(events),
        config,
        gate_config,
    )
    day_three = [
        row for row in result["decisions"]
        if row["session_id"] == "2026-07-22"
    ]

    assert repeated == result
    assert len(day_three) == 2
    assert {row["state"] for row in day_three} == {"ON"}
    assert len({row["score_map_asof"] for row in day_three}) == 1
    assert day_three[0]["score_map_asof"] == pd.Timestamp(
        "2026-07-22 07:30",
        tz=DENVER,
    )


def test_daily_state_advances_on_sessions_without_a_signal():
    events = [
        _event("2026-07-20"),
        _event("2026-07-22"),
    ]

    result = _run_stream_replay(
        events,
        _stream(events),
        _run_config(),
        _gate_config(
            replay_protocol="daily_frozen",
            policy="aligned_only",
        ),
        replay_sessions=["2026-07-20", "2026-07-21", "2026-07-22"],
    )
    day_three = [
        row for row in result["decisions"]
        if row["session_id"] == "2026-07-22"
    ]

    assert day_three[0]["state"] == "ON"


def test_weekly_frozen_map_does_not_change_during_its_week():
    events = [
        _event("2026-07-06"),
        _event("2026-07-13"),
        _event("2026-07-14"),
        _event("2026-07-15"),
    ]
    config = _run_config()
    gate_config = _gate_config(
        replay_protocol="weekly_frozen",
        policy="aligned_only",
    )

    result = _run_stream_replay(
        events,
        _stream(events),
        config,
        gate_config,
    )
    current_week = [
        row for row in result["decisions"]
        if row["session_id"] >= "2026-07-13"
    ]

    assert {row["score_map_asof"] for row in current_week} == {
        pd.Timestamp("2026-07-13 07:30", tz=DENVER)
    }


def test_event_updated_map_changes_only_after_strictly_known_outcomes():
    events = [
        _event("2026-07-20", minute="10:00"),
        _event("2026-07-20", minute="10:10"),
        _event("2026-07-20", minute="10:20"),
    ]

    result = _run_stream_replay(
        events,
        _stream(events),
        _run_config(),
        _gate_config(
            replay_protocol="event_updated",
            policy="aligned_only",
        ),
    )

    assert [row["state"] for row in result["decisions"]] == [
        "OFF",
        "WATCH",
        "ON",
    ]
    assert [row["score_map_asof"] for row in result["decisions"]] == [
        event["signal_dt"] for event in events
    ]


def test_watch_and_decaying_do_not_consume_independent_directional_capacity():
    long_one = _event("2026-07-20", minute="10:00")
    paper_long = _event("2026-07-20", minute="10:01")
    short_one = _event("2026-07-20", minute="10:02")
    long_two = _event("2026-07-20", minute="10:03")
    for event in (long_one, paper_long, short_one, long_two):
        for mode in ("continuation", "reversion"):
            event[mode]["exit_known_dt"] = pd.Timestamp(
                "2026-07-20 11:00",
                tz=DENVER,
            )
    short_one["continuation"]["trade_direction"] = -1

    def decision(event, state):
        return {
            "entry_dt": event["entry_dt"],
            "state": state,
            "selected_mode": "continuation",
            "capacity_eligible": None,
            "capacity_skipped": False,
            "outcome_available": False,
            "actual_trade_direction": None,
            "actual_exit_known_dt": None,
            "actual_net_ticks": None,
            "_event": event,
        }

    decisions = [
        decision(long_one, "ON"),
        decision(paper_long, "WATCH"),
        decision(short_one, "ON"),
        decision(long_two, "ON"),
    ]

    _apply_execution_capacity(
        decisions,
        {"outcome": {"max_concurrent_per_direction": 1}},
    )

    assert decisions[0]["capacity_eligible"] is True
    assert decisions[1]["capacity_eligible"] is None
    assert decisions[2]["capacity_eligible"] is True
    assert decisions[3]["capacity_skipped"] is True


def test_public_gate_output_is_json_safe_and_contains_complete_ledgers(
    monkeypatch,
):
    events = [
        _event("2026-07-20"),
        _event("2026-07-21"),
        _event("2026-07-22"),
    ]
    bars = pd.DataFrame(
        {
            "dt": pd.date_range(
                "2026-07-20 10:00",
                periods=2,
                freq="1min",
                tz=DENVER,
            ),
            "open": [100.0, 100.0],
            "high": [101.0, 101.0],
            "low": [99.0, 99.0],
            "close": [100.0, 100.0],
            "volume": [1.0, 1.0],
        }
    )
    context = pd.DataFrame({"dt": bars["dt"]})
    monkeypatch.setattr(
        gate_module,
        "build_intraday_context",
        lambda *_args, **_kwargs: context,
    )
    monkeypatch.setattr(
        gate_module,
        "build_adaptive_event_streams",
        lambda *_args, **_kwargs: [_stream(events)],
    )
    monkeypatch.setattr(
        gate_module,
        "attach_context_to_events",
        lambda rows, *_args, **_kwargs: list(rows),
    )

    result = gate_module.run_adaptive_context_gate(
        bars,
        _run_config(
            replay_protocol="daily_frozen",
            min_local_signals=1,
            min_unique_sessions=1,
            max_stale_days=30.0,
            mode_margin_ticks=1.0,
        ),
    )

    json.dumps(result)
    assert result["n_streams"] == 1
    assert len(result["streams"][0]["decisions"]) == 3
    assert result["streams"][0]["state_ledger"]
    decision = result["streams"][0]["decisions"][-1]
    assert decision["state_reason_code"]
    assert decision["timeframe"] == 1
    assert decision["continuation"]["exit_known_dt"]
    assert decision["reversion"]["exit_known_dt"]
