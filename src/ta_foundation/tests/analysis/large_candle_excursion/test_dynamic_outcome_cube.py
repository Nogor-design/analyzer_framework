from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from ta_foundation.analysis.large_candle_excursion.dynamic_outcome_cube import (
    DYNAMIC_OUTCOME_CUBE_SCHEMA_VERSION,
    OUTCOME_CUBE_COLUMNS,
    build_dynamic_family_a_outcome_cube,
    write_dynamic_outcome_cube,
)


DENVER = "America/Denver"


def _bars(periods: int = 240) -> pd.DataFrame:
    dts = pd.date_range(
        "2026-07-20 07:30",
        periods=periods,
        freq="1min",
        tz=DENVER,
    )
    rows = []
    for index, dt in enumerate(dts):
        if index % 10 == 9:
            bullish = ((index // 10) % 2) == 0
            rows.append(
                (
                    dt,
                    100.0,
                    102.0,
                    98.0,
                    100.25 if bullish else 99.75,
                    10.0,
                )
            )
        else:
            rows.append((dt, 100.0, 100.5, 99.5, 100.0, 1.0))
    return pd.DataFrame(
        rows,
        columns=("dt", "open", "high", "low", "close", "volume"),
    )


def _small_config(*, max_hold_minutes: int = 10) -> dict:
    return {
        "timeframes": [1, 5],
        "lookbacks": [2],
        "bases": ["range"],
        "multipliers": [1.2],
        "bars_required": 1,
        "signal_direction": "both",
        "signals": {
            "fresh_large_candle": True,
            "center_zone_break": False,
            "latch_outside_window_triggers": False,
        },
        "time_filter": {"enabled": False},
        "outcome": {
            "target_ticks": 1,
            "stop_ticks": 2,
            "max_hold_minutes": max_hold_minutes,
            "round_trip_cost_ticks": 0,
            "same_bar_policy": "stop_first",
            "max_concurrent_per_direction": 3,
        },
    }


def _cube(
    bars: pd.DataFrame,
    *,
    config: dict | None = None,
) -> dict:
    return build_dynamic_family_a_outcome_cube(
        bars,
        config or _small_config(),
        sequence="NQ 09-26",
        source_metadata={"files": [{"sha256": "fixture"}]},
        strict_catalog=False,
    )


def test_cube_retains_paired_outcomes_context_and_stable_identity():
    cube = _cube(_bars())
    rows = cube["rows"]
    manifest = cube["manifest"]

    assert rows
    assert json.dumps(cube, allow_nan=False)
    assert manifest["schema_version"] == DYNAMIC_OUTCOME_CUBE_SCHEMA_VERSION
    assert manifest["catalog_contract"]["signal_lane_count"] == 2
    assert manifest["catalog_contract"]["signal_side_lane_count"] == 4
    assert len(cube["lane_catalog"]) == 4
    assert {row["signal_side"] for row in rows} == {"bull", "bear"}
    assert set(OUTCOME_CUBE_COLUMNS) == set(rows[0])
    assert all(len(manifest[field]) == 64 for field in (
        "lane_catalog_sha256",
        "expert_catalog_sha256",
        "physical_opportunity_index_sha256",
        "outcome_cube_sha256",
        "manifest_sha256",
    ))

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["lane_event_id"], []).append(row)
        assert row["physical_opportunity_id"].startswith("physical:v1:")
        assert row["expert_id"].startswith("expert:v1:")
        assert row["outcome_id"].startswith("outcome:v1:")
        assert pd.Timestamp(row["context_dt"]) <= pd.Timestamp(row["signal_dt"])
        assert pd.Timestamp(row["exit_known_dt"]) > pd.Timestamp(row["exit_dt"])
        assert row["session_id"] == str(pd.Timestamp(row["entry_dt"]).date())
    assert all(
        {row["mode"] for row in pair} == {"continuation", "reversion"}
        for pair in grouped.values()
    )


def test_physical_opportunities_deduplicate_lanes_without_losing_paper_rows():
    cube = _cube(_bars())
    opportunities = cube["physical_opportunities"]

    duplicates = [
        row for row in opportunities if row["candidate_lane_event_count"] > 1
    ]
    assert duplicates
    assert all(row["candidate_lane_event_count"] == 2 for row in duplicates)
    assert all(row["paper_outcome_count"] == 4 for row in duplicates)
    assert len({row["physical_opportunity_id"] for row in opportunities}) == len(
        opportunities
    )
    assert sum(row["paper_outcome_count"] for row in opportunities) == len(
        cube["rows"]
    )
    by_physical: dict[str, set[str]] = {}
    for row in cube["rows"]:
        by_physical.setdefault(row["physical_opportunity_id"], set()).add(
            row["session_id"]
        )
    assert all(len(session_ids) == 1 for session_ids in by_physical.values())
    assert cube["manifest"]["counts"]["duplicate_lane_events"] > 0
    assert (
        cube["manifest"]["causality_contract"][
            "maximum_executable_experts_per_physical_opportunity"
        ]
        == 1
    )


def test_appending_future_bars_does_not_change_prior_cube_rows():
    prefix = _cube(_bars(180))
    full = _cube(_bars(240))
    full_by_id = {row["outcome_id"]: row for row in full["rows"]}

    assert prefix["rows"]
    assert set(row["outcome_id"] for row in prefix["rows"]) <= set(full_by_id)
    for row in prefix["rows"]:
        assert full_by_id[row["outcome_id"]] == row


def test_partial_higher_timeframe_source_bar_is_not_emitted():
    partial_bars = _bars(177)
    completed_bars = _bars(181)
    for bars in (partial_bars, completed_bars):
        bars.loc[176, ["high", "low", "close"]] = [110.0, 90.0, 100.25]
    partial = _cube(partial_bars)
    completed = _cube(completed_bars)
    completed_by_id = {
        row["outcome_id"]: row for row in completed["rows"]
    }
    observation_asof = pd.Timestamp(
        partial["manifest"]["source_data"]["observation_asof"]
    )

    assert all(
        pd.Timestamp(row["entry_dt"]) <= observation_asof
        for row in partial["rows"]
    )
    assert any(
        row["timeframe"] == 5
        and pd.Timestamp(row["entry_dt"]) > observation_asof
        for row in completed["rows"]
    )
    for row in partial["rows"]:
        assert completed_by_id[row["outcome_id"]] == row


def test_right_censored_pairs_are_withheld_until_both_outcomes_are_final():
    config = _small_config(max_hold_minutes=20)
    config["outcome"].update({"target_ticks": 1_000, "stop_ticks": 1_000})

    prefix = _cube(_bars(180), config=config)
    full = _cube(_bars(240), config=config)
    full_by_id = {row["outcome_id"]: row for row in full["rows"]}

    assert prefix["manifest"]["counts"]["withheld_incomplete_outcome_pairs"] > 0
    assert (
        full["manifest"]["counts"]["complete_lane_events"]
        > prefix["manifest"]["counts"]["complete_lane_events"]
    )
    for row in prefix["rows"]:
        assert full_by_id[row["outcome_id"]] == row


def test_strict_family_a_catalog_rejects_narrowed_grid():
    with pytest.raises(ValueError, match="strict Family A timeframes"):
        build_dynamic_family_a_outcome_cube(
            _bars(30),
            {"timeframes": [1]},
            sequence="NQ 09-26",
        )


def test_writer_persists_hash_bound_stable_schema(
    tmp_path: Path,
) -> None:
    cube = _cube(_bars(120))
    paths = write_dynamic_outcome_cube(tmp_path / "cube", cube)

    manifest = json.loads(
        Path(paths["manifest"]).read_text(encoding="utf-8")
    )
    header = pd.read_csv(paths["outcome_cube"], nrows=0).columns.tolist()
    assert header == list(OUTCOME_CUBE_COLUMNS)
    assert set(manifest["artifacts"]) == {
        "outcome_cube",
        "lane_catalog",
        "expert_catalog",
        "physical_opportunities",
    }
    assert all(
        len(record["sha256"]) == 64
        for record in manifest["artifacts"].values()
    )
    assert len(manifest["manifest_sha256"]) == 64

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_dynamic_outcome_cube(tmp_path / "cube", cube)
