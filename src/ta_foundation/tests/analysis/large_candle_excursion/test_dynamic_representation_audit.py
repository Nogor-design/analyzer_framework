from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from ta_foundation.analysis.large_candle_excursion.dynamic_representation_audit import (
    DEFAULT_REPRESENTATION_CONFIG,
    RepresentationAuditError,
    _canonical_physical_rows,
    run_representation_audit,
)


def _sequence_rows(sequence: str, offset: int = 0) -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-05 10:00", tz="America/Denver")
    for opportunity_index in range(4):
        signal_dt = start + timedelta(
            days=opportunity_index // 2,
            minutes=opportunity_index * 20,
        )
        physical_id = f"{sequence}-p{opportunity_index}"
        aligned = -1.0 if opportunity_index % 2 == 0 else 1.0
        continuation = 20.0 * aligned + offset
        reversion = -20.0 * aligned + offset
        for lane_index in range(2):
            for mode, reward in (
                ("continuation", continuation),
                ("reversion", reversion),
            ):
                rows.append(
                    {
                        "sequence": sequence,
                        "physical_opportunity_id": physical_id,
                        "lane_event_id": f"{physical_id}-l{lane_index}",
                        "lane_id": f"lane-{lane_index}",
                        "session_id": signal_dt.date().isoformat(),
                        "signal_side": "bull",
                        "signal_dt": signal_dt,
                        "entry_dt": signal_dt + timedelta(minutes=1),
                        "context_dt": signal_dt,
                        "mode": mode,
                        "net_pnl_ticks": reward,
                        "trend_state": "up" if aligned > 0 else "down",
                        "return_60m": 0.01 * aligned,
                        "vwap_slope_15": 2.0 * aligned,
                        "close_vs_vwap": 10.0 * aligned,
                        "signal_ratio": 1.2 + 0.1 * lane_index,
                        "latched_outside_window": lane_index == 1,
                        "zone_break_trigger": lane_index == 0,
                    }
                )
    return pd.DataFrame(rows)


def test_physical_collapse_removes_mode_and_lane_duplicates():
    physical = _canonical_physical_rows(_sequence_rows("NQ 03-26"))
    assert len(physical) == 4
    assert physical["physical_opportunity_id"].nunique() == 4
    assert set(physical["continuation_net_ticks"]) == {-20.0, 20.0}
    assert set(physical["signal_lane_fraction"]) == {2.0 / 36.0}
    assert set(physical["zone_break_fraction"]) == {0.5}


def test_physical_boundary_uses_latest_pre_entry_signal():
    rows = _sequence_rows("NQ 03-26")
    mask = (
        (rows["physical_opportunity_id"] == "NQ 03-26-p0")
        & (rows["lane_id"] == "lane-0")
    )
    rows.loc[mask, "signal_dt"] = rows.loc[mask, "signal_dt"] - timedelta(
        minutes=1
    )
    rows.loc[mask, "context_dt"] = rows.loc[mask, "signal_dt"]
    physical = _canonical_physical_rows(rows)
    first = physical.loc[
        physical["physical_opportunity_id"] == "NQ 03-26-p0"
    ].iloc[0]
    assert first["signal_dt"] == rows.loc[~mask & (
        rows["physical_opportunity_id"] == "NQ 03-26-p0"
    ), "signal_dt"].iloc[0]
    assert physical.attrs["multi_signal_opportunities"] == 1


def test_post_signal_context_is_rejected():
    rows = _sequence_rows("NQ 03-26")
    rows.loc[0, "context_dt"] = rows.loc[0, "signal_dt"] + timedelta(minutes=1)
    with pytest.raises(RepresentationAuditError, match="post-signal"):
        _canonical_physical_rows(rows)


def test_incomplete_context_is_excluded_once():
    rows = _sequence_rows("NQ 03-26")
    rows.loc[
        rows["physical_opportunity_id"] == "NQ 03-26-p0",
        "return_60m",
    ] = float("nan")
    physical = _canonical_physical_rows(rows)
    assert len(physical) == 3
    assert physical.attrs["excluded_incomplete_context"] == 1


def test_cross_lane_rewards_are_median_collapsed():
    rows = _sequence_rows("NQ 03-26")
    mask = (
        (rows["physical_opportunity_id"] == "NQ 03-26-p0")
        & (rows["mode"] == "continuation")
        & (rows["lane_id"] == "lane-0")
    )
    rows.loc[mask, "net_pnl_ticks"] = 100.0
    physical = _canonical_physical_rows(rows)
    first = physical.loc[
        physical["physical_opportunity_id"] == "NQ 03-26-p0"
    ].iloc[0]
    assert first["continuation_net_ticks"] == 40.0
    assert physical.attrs["multi_outcome_opportunities"] == 1


def test_non_invariant_reward_within_lane_is_rejected():
    rows = _sequence_rows("NQ 03-26")
    duplicate = rows.iloc[[0]].copy()
    duplicate["net_pnl_ticks"] = 99.0
    rows = pd.concat([rows, duplicate], ignore_index=True)
    with pytest.raises(RepresentationAuditError, match="non-invariant"):
        _canonical_physical_rows(rows)


def test_leave_one_sequence_out_scores_each_opportunity_once():
    names = [
        "NQ 03-26",
        "NQ 06-26",
        "NQ 09-26",
        "ES 03-26",
        "RTY 03-26",
        "YM 03-26",
        "YM 06-26",
    ]
    result = run_representation_audit(
        {
            name: _sequence_rows(name, offset=index)
            for index, name in enumerate(names)
        }
    )
    assert result["manifest"]["counts"]["physical_opportunities"] == 28
    assert result["manifest"]["counts"]["fit_records"] == 42
    assert result["manifest"]["counts"]["cross_fitted_scores"] == 168
    score_keys = {
        (row["representation_id"], row["physical_opportunity_id"])
        for row in result["score_rows"]
    }
    assert len(score_keys) == 168
    for fit in result["fit_rows"]:
        assert fit["held_out_sequence"] not in fit["training_sequences"]


def test_configuration_is_frozen():
    changed = dict(DEFAULT_REPRESENTATION_CONFIG)
    changed["ridge_penalty"] = 2.0
    with pytest.raises(RepresentationAuditError, match="freezes"):
        run_representation_audit(
            {
                name: _sequence_rows(name)
                for name in (
                    "NQ 03-26",
                    "NQ 06-26",
                    "NQ 09-26",
                    "ES 03-26",
                    "RTY 03-26",
                    "YM 03-26",
                    "YM 06-26",
                )
            },
            config=changed,
        )
