from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_foundation.analysis.large_candle_excursion.dynamic_multisequence_replay import (
    DYNAMIC_MULTISEQUENCE_REPLAY_SCHEMA_VERSION,
    DynamicMultisequenceError,
    REQUIRED_PROFILES,
    REQUIRED_PROTOCOLS,
    build_multisequence_summary,
    summarize_dynamic_sequence,
    write_dynamic_multisequence_replay,
)


def _fixture(sequence: str = "NQ 03-26") -> dict:
    summaries = []
    states = []
    executions = []
    windows = []
    for profile in REQUIRED_PROFILES:
        for protocol in REQUIRED_PROTOCOLS:
            primary = profile == "Balanced" and protocol == "daily_frozen"
            summaries.append(
                {
                    "sequence": sequence,
                    "profile": profile,
                    "protocol": protocol,
                    "primary_row": primary,
                    "sessions": 4,
                    "session_start": "2026-01-01",
                    "session_end": "2026-01-04",
                    "states": {"distinct_selected_experts": 1},
                    "execution": {
                        "trades": 1,
                        "net_ticks": 10.0 if primary else -1.0,
                        "profit_factor": 2.0 if primary else 0.9,
                        "maximum_drawdown_ticks": 5.0,
                        "activation_days": 1,
                        "profitable_activation_days": 1 if primary else 0,
                        "activation_day_precision_pct": 100.0 if primary else 0.0,
                        "positive_paper_opportunity_missed_while_inactive_ticks": 2.0,
                    },
                    "dynamic_regret": {
                        "selector_penalized_objective_ticks": 8.0,
                        "bounded_oracle_penalized_objective_ticks": 20.0,
                        "dynamic_regret_ticks": 12.0,
                    },
                }
            )
            for index, (session, state) in enumerate(
                (
                    ("2026-01-01", "OFF"),
                    ("2026-01-02", "ON"),
                    ("2026-01-03", "ON"),
                    ("2026-01-04", "OFF"),
                )
            ):
                states.append(
                    {
                        "sequence": sequence,
                        "profile": profile,
                        "protocol": protocol,
                        "decision_index": index,
                        "session_id": session,
                        "context_cell": "bull|10:00-10:30|up",
                        "previous_state": "OFF" if index == 0 else "ON",
                        "state": state,
                        "selected_expert_id": "expert-1" if state == "ON" else None,
                    }
                )
            executions.append(
                {
                    "sequence": sequence,
                    "profile": profile,
                    "protocol": protocol,
                    "session_id": "2026-01-02",
                    "candidate_context_cells": ["bull|10:00-10:30|up"],
                    "selected_expert_id": "expert-1",
                    "executed": True,
                    "net_pnl_ticks": 10.0 if primary else -1.0,
                }
            )
            windows.append(
                {
                    "schema_version": "dynamic_fixed_share_selector.v1",
                    "sequence": sequence,
                    "profile": profile,
                    "protocol": protocol,
                    "decision_index": 1,
                    "decision_asof": "2026-01-02T10:00:00-07:00",
                    "session_id": "2026-01-02",
                    "window_id": f"{profile}-{protocol}",
                    "signal_side": "bull",
                    "trend_state": "up",
                    "start_time": "10:00",
                    "end_time": "10:30",
                    "time_buckets": ["10:00-10:30"],
                    "expert_ids": ["expert-1"],
                    "expert_lane_id": "lane-1",
                    "timeframe": 1,
                    "lookback": 5,
                    "basis": "range",
                    "multiplier": 1.5,
                    "mode": "continuation",
                    "trade_direction": 1,
                }
            )
    oracle_sessions = []
    for index, session in enumerate(
        ("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04")
    ):
        oracle_sessions.append(
            {
                "sequence": sequence,
                "session_index": index,
                "session_id": session,
                "context_cell": "bull|10:00-10:30|up",
                "oracle_state": "EXPERT" if index < 2 else "OFF",
                "selected_expert_id": "expert-1" if index < 2 else None,
            }
        )
    oracle_regimes = [
        {
            "sequence": sequence,
            "context_cell": "bull|10:00-10:30|up",
            "oracle_state": "EXPERT",
            "selected_expert_id": "expert-1",
            "start_session_index": 0,
            "end_session_index": 1,
        },
        {
            "sequence": sequence,
            "context_cell": "bull|10:00-10:30|up",
            "oracle_state": "OFF",
            "selected_expert_id": None,
            "start_session_index": 2,
            "end_session_index": 3,
        },
    ]
    return {
        "selector_summaries": summaries,
        "selector_state_rows": states,
        "selector_execution_rows": executions,
        "selector_window_rows": windows,
        "oracle_summary": {
            "detectability_ceiling": {
                "best_static_per_cell_raw_net_ticks": 5.0,
                "bounded_oracle_penalized_objective_ticks": 20.0,
            }
        },
        "oracle_session_rows": oracle_sessions,
        "oracle_regime_rows": oracle_regimes,
    }


def test_sequence_summary_measures_delay_turnover_and_oracle_overlap():
    result = summarize_dynamic_sequence(**_fixture())
    row = next(row for row in result["summary_rows"] if row["primary_row"])

    assert row["median_activation_delay_sessions"] == 1
    assert row["median_exact_expert_delay_sessions"] == 1
    assert row["median_deactivation_delay_sessions"] == 1
    assert row["oracle_active_cell_trades"] == 1
    assert row["oracle_exact_expert_net_ticks"] == 10.0
    assert row["policy_switches"] == 2
    assert row["largest_profitable_session_share_pct"] == 100.0
    assert row["static_selector_regret_ticks"] == -3.0
    assert row["dynamic_regret_improvement_vs_static_ticks"] == 3.0
    assert row["dynamic_regret_below_static"] is True
    assert len(result["timeline_rows"]) == 1


def test_sequence_summary_rejects_narrow_matrix_and_state_carryover():
    fixture = _fixture()
    fixture["selector_summaries"] = fixture["selector_summaries"][:-1]
    with pytest.raises(DynamicMultisequenceError, match="complete frozen"):
        summarize_dynamic_sequence(**fixture)

    fixture = _fixture()
    fixture["selector_state_rows"][0]["previous_state"] = "ON"
    with pytest.raises(DynamicMultisequenceError, match="carryover"):
        summarize_dynamic_sequence(**fixture)


def test_cross_sequence_summary_keeps_state_independent_and_writer_hashes(
    tmp_path: Path,
):
    first = summarize_dynamic_sequence(**_fixture("NQ 03-26"))
    second = summarize_dynamic_sequence(**_fixture("ES 03-26"))
    summary = build_multisequence_summary([first, second])

    assert summary["aggregate_diagnostics"]["positive_primary_sequences"] == 2
    assert summary["aggregate_diagnostics"]["state_pooled_across_sequences"] is False
    assert summary["aggregate_diagnostics"][
        "primary_dynamic_regret_below_static_sequences"
    ] == 2

    rows = first["summary_rows"] + second["summary_rows"]
    timeline = first["timeline_rows"] + second["timeline_rows"]
    paths = write_dynamic_multisequence_replay(
        tmp_path / "phase4",
        summary_rows=rows,
        timeline_rows=timeline,
        cross_sequence_summary=summary,
        manifest={"children": [{"sequence": "NQ 03-26"}, {"sequence": "ES 03-26"}]},
    )
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert manifest["schema_version"] == DYNAMIC_MULTISEQUENCE_REPLAY_SCHEMA_VERSION
    assert manifest["manifest_sha256"]
    assert manifest["forward_authorized"] is False
