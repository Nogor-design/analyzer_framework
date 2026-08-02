from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from ta_foundation.analysis.large_candle_excursion.dynamic_fixed_share_selector import (
    DYNAMIC_CARRIED_FIXED_SHARE_SELECTOR_SCHEMA_VERSION,
    DYNAMIC_FIXED_SHARE_SELECTOR_SCHEMA_VERSION,
    DynamicSelectorError,
    run_dynamic_carried_fixed_share_matrix,
    run_dynamic_carried_fixed_share_selector,
    run_dynamic_fixed_share_matrix,
    run_dynamic_fixed_share_selector,
    write_dynamic_fixed_share_selector,
)


DENVER = "America/Denver"
CELL = "bear|10:00-10:30|down"


def _row(
    session: str,
    physical: str,
    expert: str,
    pnl: float,
    *,
    signal_time: str = "10:00",
    exit_known: pd.Timestamp | None = None,
    capacity_eligible: bool = True,
) -> dict:
    signal = pd.Timestamp(f"{session} {signal_time}", tz=DENVER)
    entry = signal + pd.Timedelta(minutes=1)
    exit_dt = entry + pd.Timedelta(minutes=5)
    known = exit_known or exit_dt + pd.Timedelta(minutes=1)
    mode = "continuation" if expert == "expert-a" else "reversion"
    return {
        "sequence": "NQ 09-26",
        "physical_opportunity_id": physical,
        "lane_event_id": f"lane-{physical}-{expert}",
        "expert_id": expert,
        "outcome_id": f"outcome-{physical}-{expert}",
        "expert_lane_id": f"lane-{expert}",
        "signal_side": "bear",
        "timeframe": 1 if expert == "expert-a" else 2,
        "lookback": 5,
        "basis": "range",
        "multiplier": 1.5,
        "time_bucket": "10:00-10:30",
        "trend_state": "down",
        "context_cell": CELL,
        "session_id": session,
        "mode": mode,
        "trade_direction": -1 if mode == "continuation" else 1,
        "signal_dt": signal,
        "entry_dt": entry,
        "exit_dt": exit_dt,
        "exit_known_dt": known,
        "net_pnl_ticks": pnl,
        "capacity_eligible": capacity_eligible,
    }


def _sessions(count: int = 6) -> list[str]:
    return [
        value.strftime("%Y-%m-%d")
        for value in pd.date_range("2026-07-01", periods=count, freq="B")
    ]


def _daily_rows(count: int = 6) -> list[dict]:
    rows = []
    for index, session in enumerate(_sessions(count)):
        physical = f"physical-{index}"
        rows.extend(
            [
                _row(session, physical, "expert-a", 75.0),
                _row(session, physical, "expert-b", -75.0),
            ]
        )
    return rows


def _oracle(value: float = 500.0) -> dict:
    return {
        "detectability_ceiling": {
            "bounded_oracle_penalized_objective_ticks": value
        }
    }


def test_daily_frozen_selector_is_strictly_causal_and_confirms_watch_before_on():
    result = run_dynamic_fixed_share_selector(
        _daily_rows(),
        profile="Balanced",
        protocol="daily_frozen",
        oracle_summary=_oracle(),
    )
    states = result["state_rows"]

    assert json.dumps(result, allow_nan=False)
    assert result["manifest"]["schema_version"] == (
        DYNAMIC_FIXED_SHARE_SELECTOR_SCHEMA_VERSION
    )
    assert states[0]["state"] == "OFF"
    assert states[0]["known_outcomes"] == 0
    assert [row["state"] for row in states][1:4] == ["WATCH"] * 3
    assert states[4]["state"] == "ON"
    assert all(
        row["evidence_through"] is None
        or pd.Timestamp(row["evidence_through"])
        < pd.Timestamp(row["decision_asof"])
        for row in states
    )
    assert result["summary"]["dynamic_regret"][
        "comparable_to_phase2_oracle"
    ] is True
    assert result["summary"]["execution"]["trades"] == 2


def test_exit_known_exactly_at_boundary_is_not_eligible():
    sessions = _sessions(3)
    rows = _daily_rows(3)
    next_boundary = pd.Timestamp(f"{sessions[1]} 10:00", tz=DENVER)
    for row in rows:
        if row["session_id"] == sessions[0]:
            row["exit_known_dt"] = next_boundary

    result = run_dynamic_fixed_share_selector(rows, profile="Fast")
    second = result["state_rows"][1]

    assert second["known_outcomes"] == 0
    assert second["evidence_through"] is None


def test_serialized_denver_offsets_remain_valid_across_dst_transition():
    rows = []
    for index, session in enumerate(("2026-03-06", "2026-03-09")):
        physical = f"physical-{index}"
        rows.extend(
            [
                _row(session, physical, "expert-a", 75.0),
                _row(session, physical, "expert-b", -75.0),
            ]
        )
    for row in rows:
        for column in ("signal_dt", "entry_dt", "exit_dt", "exit_known_dt"):
            row[column] = row[column].isoformat()

    result = run_dynamic_fixed_share_selector(rows, profile="Fast")

    assert result["summary"]["sessions"] == 2
    assert result["state_rows"][0]["decision_asof"].endswith("-07:00")
    assert result["state_rows"][1]["decision_asof"].endswith("-06:00")


def test_event_updated_reacts_only_after_outcome_is_known():
    session = _sessions(1)[0]
    rows = []
    for index, signal_time in enumerate(("10:00", "10:10", "10:20", "10:30", "10:40")):
        physical = f"physical-{index}"
        rows.extend(
            [
                _row(
                    session,
                    physical,
                    "expert-a",
                    75.0,
                    signal_time=signal_time,
                ),
                _row(
                    session,
                    physical,
                    "expert-b",
                    -75.0,
                    signal_time=signal_time,
                ),
            ]
        )

    result = run_dynamic_fixed_share_selector(
        rows,
        profile="Balanced",
        protocol="event_updated",
    )
    executions = result["execution_rows"]

    assert executions[0]["reason_code"] == "SELECTOR_NOT_ON"
    assert executions[-1]["executed"] is True
    assert result["summary"]["dynamic_regret"][
        "comparable_to_phase2_oracle"
    ] is False


def test_carried_fixed_share_uses_tempered_posterior_and_stable_reward_batch():
    result = run_dynamic_carried_fixed_share_selector(
        _daily_rows(3),
        profile="Balanced",
        protocol="daily_frozen",
    )
    second_boundary = [
        row
        for row in result["evidence_rows"]
        if row["decision_index"] == 1
    ]
    probabilities = {
        row["expert_id"]: row["selector_probability"]
        for row in second_boundary
    }
    reward_posterior_a = 1.0 / (1.0 + pow(2.718281828459045, -1.0))
    expected_a = 0.95 * reward_posterior_a + 0.025

    assert result["manifest"]["schema_version"] == (
        DYNAMIC_CARRIED_FIXED_SHARE_SELECTOR_SCHEMA_VERSION
    )
    assert result["manifest"]["contracts"]["ranking_source"] == (
        "carried_posterior_weight"
    )
    assert probabilities["expert-a"] == pytest.approx(expected_a)
    assert sum(probabilities.values()) == pytest.approx(1.0)


def test_carried_fixed_share_future_append_does_not_change_prior_posterior():
    prefix = run_dynamic_carried_fixed_share_selector(
        _daily_rows(5),
        profile="Fast",
    )
    full = run_dynamic_carried_fixed_share_selector(
        _daily_rows(6),
        profile="Fast",
    )

    assert full["evidence_rows"][: len(prefix["evidence_rows"])] == (
        prefix["evidence_rows"]
    )
    assert full["state_rows"][: len(prefix["state_rows"])] == prefix["state_rows"]
    assert full["execution_rows"][: len(prefix["execution_rows"])] == (
        prefix["execution_rows"]
    )


def test_oracle_values_cannot_change_causal_decisions():
    low = run_dynamic_fixed_share_selector(
        _daily_rows(),
        profile="Balanced",
        oracle_summary=_oracle(100.0),
    )
    high = run_dynamic_fixed_share_selector(
        _daily_rows(),
        profile="Balanced",
        oracle_summary=_oracle(10_000.0),
    )

    assert low["state_rows"] == high["state_rows"]
    assert low["execution_rows"] == high["execution_rows"]
    assert low["summary"]["dynamic_regret"]["dynamic_regret_ticks"] != (
        high["summary"]["dynamic_regret"]["dynamic_regret_ticks"]
    )


def test_physical_conflicts_are_deduplicated_before_global_direction_capacity():
    rows = _daily_rows(4)
    second_cell = "bear|10:30-11:00|down"

    def add_second_cell(source_rows: list[dict]) -> None:
        additions = []
        for row in source_rows:
            clone = dict(row)
            positive = row["expert_id"] == "expert-a"
            clone["expert_id"] = "expert-c" if positive else "expert-d"
            clone["outcome_id"] = (
                f"{row['outcome_id']}-second-cell"
            )
            clone["lane_event_id"] = (
                f"{row['lane_event_id']}-second-cell"
            )
            clone["expert_lane_id"] = (
                "lane-expert-c" if positive else "lane-expert-d"
            )
            clone["time_bucket"] = "10:30-11:00"
            clone["context_cell"] = second_cell
            clone["mode"] = "continuation" if positive else "reversion"
            clone["trade_direction"] = -1 if positive else 1
            additions.append(clone)
        source_rows.extend(additions)

    add_second_cell(rows)
    final_session = _sessions(5)[-1]
    final_rows = []
    for index, signal_time in enumerate(("10:00", "10:01", "10:02", "10:03")):
        physical = f"final-{index}"
        final_rows.extend(
            [
                _row(
                    final_session,
                    physical,
                    "expert-a",
                    75.0,
                    signal_time=signal_time,
                ),
                _row(
                    final_session,
                    physical,
                    "expert-b",
                    -75.0,
                    signal_time=signal_time,
                ),
            ]
        )
    add_second_cell(final_rows)
    rows.extend(final_rows)

    result = run_dynamic_fixed_share_selector(rows, profile="Balanced")
    final = [
        row
        for row in result["execution_rows"]
        if row["session_id"] == final_session
    ]

    assert all(row["deduplication_conflicts"] == 1 for row in final)
    assert sum(row["executed"] for row in final) == 3
    assert sum(row["capacity_skipped"] for row in final) == 1


def test_daily_boundary_projects_physical_id_once_across_signal_timestamps():
    rows = _daily_rows(5)
    final_session = _sessions(5)[-1]
    duplicate_representation = dict(rows[-2])
    duplicate_representation["lane_event_id"] = "later-lane-event"
    duplicate_representation["outcome_id"] = "later-outcome"
    duplicate_representation["expert_id"] = "expert-c"
    duplicate_representation["expert_lane_id"] = "lane-expert-c"
    duplicate_representation["signal_dt"] = (
        pd.Timestamp(duplicate_representation["signal_dt"])
        - pd.Timedelta(minutes=1)
    )
    rows.append(duplicate_representation)

    result = run_dynamic_fixed_share_selector(rows, profile="Balanced")
    final_rows = [
        row
        for row in result["execution_rows"]
        if row["session_id"] == final_session
    ]

    assert len(final_rows) == 1
    assert final_rows[0]["physical_opportunity_id"] == "physical-4"


def test_appending_future_rows_does_not_change_prior_decisions():
    prefix = run_dynamic_fixed_share_selector(
        _daily_rows(5),
        profile="Balanced",
    )
    full = run_dynamic_fixed_share_selector(
        _daily_rows(6),
        profile="Balanced",
    )

    assert full["state_rows"][: len(prefix["state_rows"])] == prefix["state_rows"]
    assert (
        full["execution_rows"][: len(prefix["execution_rows"])]
        == prefix["execution_rows"]
    )


def test_matrix_and_writer_preserve_stable_hash_bound_ledgers(tmp_path: Path):
    result = run_dynamic_fixed_share_matrix(
        _daily_rows(),
        profiles=("Fast", "Balanced", "Slow"),
        protocols=("daily_frozen",),
        oracle_summary=_oracle(),
        source_manifest={
            "schema_version": "dynamic_outcome_cube.v1",
            "manifest_sha256": "a" * 64,
            "outcome_cube_sha256": "b" * 64,
        },
        oracle_manifest={
            "schema_version": "dynamic_opportunity_oracle.v1",
            "manifest_sha256": "c" * 64,
        },
    )
    paths = write_dynamic_fixed_share_selector(tmp_path / "selector", result)
    manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))

    assert len(result["summary"]) == 3
    assert set(manifest["artifacts"]) == {
        "summary",
        "evidence_ledger",
        "state_ledger",
        "switch_ledger",
        "window_ledger",
        "execution_ledger",
    }
    assert all(
        len(record["sha256"]) == 64
        for record in manifest["artifacts"].values()
    )
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_dynamic_fixed_share_selector(tmp_path / "selector", result)


def test_carried_matrix_and_writer_preserve_phase4b_contract(tmp_path: Path):
    result = run_dynamic_carried_fixed_share_matrix(
        _daily_rows(),
        profiles=("Fast", "Balanced", "Slow"),
        protocols=("daily_frozen",),
    )
    paths = write_dynamic_fixed_share_selector(tmp_path / "phase4b", result)
    manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))

    assert manifest["schema_version"] == (
        DYNAMIC_CARRIED_FIXED_SHARE_SELECTOR_SCHEMA_VERSION
    )
    assert manifest["research_phase"] == "dynamic_phase_4b"
    assert manifest["contracts"]["posterior_recurrence"].startswith(
        "session_temper"
    )


def test_frozen_contract_rejects_post_result_parameter_changes():
    with pytest.raises(DynamicSelectorError, match="absolute_evidence_floor_r"):
        run_dynamic_fixed_share_selector(
            _daily_rows(),
            profile="Balanced",
            config={"absolute_evidence_floor_r": 0.1},
        )
    with pytest.raises(
        DynamicSelectorError,
        match="new_expert_confirmation_boundaries",
    ):
        run_dynamic_fixed_share_selector(
            _daily_rows(),
            profile="Balanced",
            config={"new_expert_confirmation_boundaries": 2},
        )
