from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from ta_foundation.analysis.large_candle_excursion.dynamic_opportunity_oracle import (
    DYNAMIC_OPPORTUNITY_ORACLE_SCHEMA_VERSION,
    DynamicOracleError,
    run_bounded_switching_oracle,
    solve_bounded_cell_path,
    write_dynamic_opportunity_oracle,
)


DENVER = "America/Denver"


def _sessions(count: int = 10) -> list[str]:
    return [
        timestamp.strftime("%Y-%m-%d")
        for timestamp in pd.date_range("2026-07-01", periods=count, freq="B")
    ]


def _row(
    session: str,
    expert: str,
    pnl: float,
    *,
    physical: str,
    cell: str = "bear|10:00-10:30|bearish",
    capacity_eligible: bool = True,
) -> dict:
    signal_side, time_bucket, trend_state = cell.split("|")
    entry = pd.Timestamp(f"{session} 10:00", tz=DENVER)
    suffix = f"{physical}-{expert}"
    return {
        "sequence": "NQ 09-26",
        "physical_opportunity_id": physical,
        "lane_event_id": f"lane-event-{suffix}",
        "expert_id": expert,
        "outcome_id": f"outcome-{suffix}",
        "signal_side": signal_side,
        "time_bucket": time_bucket,
        "trend_state": trend_state,
        "context_cell": cell,
        "session_id": session,
        "mode": "continuation",
        "trade_direction": -1,
        "entry_dt": entry,
        "exit_dt": entry + pd.Timedelta(minutes=30),
        "exit_known_dt": entry + pd.Timedelta(minutes=31),
        "net_pnl_ticks": pnl,
        "capacity_eligible": capacity_eligible,
    }


def _changing_rows(count: int = 10) -> list[dict]:
    rows = []
    for index, session in enumerate(_sessions(count)):
        physical = f"physical-{index}"
        rows.append(
            _row(
                session,
                "expert-a",
                10.0 if index < 5 else -10.0,
                physical=physical,
            )
        )
        rows.append(
            _row(
                session,
                "expert-b",
                -10.0 if index < 5 else 10.0,
                physical=physical,
            )
        )
    return rows


def test_bounded_path_switches_only_after_five_sessions_and_pays_penalty():
    sessions = _sessions()
    rewards = {}
    for index, session in enumerate(sessions):
        rewards[("cell", session, "a")] = 10.0 if index < 5 else -10.0
        rewards[("cell", session, "b")] = -10.0 if index < 5 else 10.0

    path = solve_bounded_cell_path(
        sessions,
        ["a", "b"],
        rewards,
        context_cell="cell",
        minimum_dwell_sessions=5,
        switch_penalty_ticks=15.0,
    )

    assert path == ["a"] * 5 + ["b"] * 5


def test_phase2_oracle_reports_static_unconstrained_and_execution_ceiling():
    result = run_bounded_switching_oracle(
        _changing_rows(),
        source_manifest={
            "schema_version": "dynamic_outcome_cube.v1",
            "manifest_sha256": "a" * 64,
            "outcome_cube_sha256": "b" * 64,
        },
    )
    summary = result["summary"]

    assert json.dumps(result, allow_nan=False)
    assert result["manifest"]["schema_version"] == (
        DYNAMIC_OPPORTUNITY_ORACLE_SCHEMA_VERSION
    )
    assert len(result["session_rows"]) == 10
    assert [row["dwell_sessions"] for row in result["regime_rows"]] == [5, 5]
    assert summary["detectability_ceiling"][
        "best_static_per_cell_raw_net_ticks"
    ] == 0.0
    assert summary["detectability_ceiling"][
        "bounded_oracle_raw_net_ticks"
    ] == 100.0
    assert summary["detectability_ceiling"][
        "bounded_oracle_penalized_objective_ticks"
    ] == 85.0
    assert summary["detectability_ceiling"][
        "unconstrained_per_session_raw_net_ticks"
    ] == 100.0
    assert summary["execution_projection"]["trades"] == 10
    assert summary["execution_projection"]["net_ticks"] == 100.0
    assert summary["diagnostic_conclusion"] == (
        "TRACKABLE_IN_HINDSIGHT_DIAGNOSTIC_ONLY"
    )
    assert summary["causal_selector_authorized"] is False


def test_terminal_short_regime_is_allowed_but_marked_right_censored():
    rows = _changing_rows(7)
    for row in rows:
        if row["expert_id"] == "expert-b" and row["session_id"] in _sessions(7)[5:]:
            row["net_pnl_ticks"] = 100.0

    result = run_bounded_switching_oracle(rows)

    assert [row["dwell_sessions"] for row in result["regime_rows"]] == [5, 2]
    assert result["regime_rows"][-1]["right_censored"] is True
    assert result["regime_rows"][-1]["minimum_dwell_satisfied"] is False


def test_serialized_denver_offsets_remain_valid_across_dst_transition():
    rows = [
        _row("2026-03-06", "expert-a", 10.0, physical="physical-before"),
        _row("2026-03-09", "expert-a", 10.0, physical="physical-after"),
    ]
    for row in rows:
        for column in ("entry_dt", "exit_dt", "exit_known_dt"):
            row[column] = row[column].isoformat()

    result = run_bounded_switching_oracle(rows)

    assert result["summary"]["sessions"] == 2
    assert result["manifest"]["counts"]["outcome_rows"] == 2


def test_physical_conflicts_are_deduplicated_by_frozen_tiebreak():
    rows = []
    second_cell = "bear|10:30-11:00|bearish"
    for index, session in enumerate(_sessions(5)):
        physical = f"physical-{index}"
        rows.append(
            _row(session, "expert-a", 10.0, physical=physical)
        )
        rows.append(
            _row(
                session,
                "expert-c",
                20.0,
                physical=physical,
                cell=second_cell,
            )
        )

    result = run_bounded_switching_oracle(rows)
    opportunities = result["opportunity_rows"]

    assert all(row["selected_expert_id"] == "expert-c" for row in opportunities)
    assert all(row["deduplication_conflicts"] == 1 for row in opportunities)
    assert result["summary"]["execution_projection"][
        "deduplication_conflicts"
    ] == 5
    assert result["summary"]["execution_projection"]["net_ticks"] == 100.0


def test_oracle_rejects_noncausal_and_duplicate_rows():
    rows = _changing_rows()
    rows[0]["exit_known_dt"] = rows[0]["exit_dt"]
    with pytest.raises(DynamicOracleError, match="exit_known_dt"):
        run_bounded_switching_oracle(rows)

    rows = _changing_rows()
    rows[1]["expert_id"] = rows[0]["expert_id"]
    with pytest.raises(DynamicOracleError, match="at most one row"):
        run_bounded_switching_oracle(rows)


def test_writer_persists_stable_hash_bound_artifacts(tmp_path: Path):
    result = run_bounded_switching_oracle(_changing_rows())
    paths = write_dynamic_opportunity_oracle(tmp_path / "oracle", result)

    manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
    assert set(manifest["artifacts"]) == {
        "summary",
        "session_ledger",
        "regime_ledger",
        "opportunity_ledger",
    }
    assert all(
        len(record["sha256"]) == 64
        for record in manifest["artifacts"].values()
    )
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_dynamic_opportunity_oracle(tmp_path / "oracle", result)
