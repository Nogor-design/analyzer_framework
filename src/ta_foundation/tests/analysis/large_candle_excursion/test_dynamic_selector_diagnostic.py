from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ta_foundation.analysis.large_candle_excursion.dynamic_selector_diagnostic import (
    analyze_sequence,
    build_panel_summary,
    classify_policy_turnover,
    write_dynamic_selector_diagnostic,
)


def _fixture() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict]]:
    outcomes = pd.DataFrame(
        [
            {
                "sequence": "NQ 09-26",
                "outcome_id": "o1",
                "physical_opportunity_id": "p1",
                "context_cell": "cell-a",
                "session_id": "2026-07-01",
                "expert_id": "expert-a",
                "net_pnl_ticks": 10.0,
                "capacity_eligible": True,
            },
            {
                "sequence": "NQ 09-26",
                "outcome_id": "o2",
                "physical_opportunity_id": "p1",
                "context_cell": "cell-b",
                "session_id": "2026-07-01",
                "expert_id": "expert-b",
                "net_pnl_ticks": -5.0,
                "capacity_eligible": True,
            },
            {
                "sequence": "NQ 09-26",
                "outcome_id": "o3",
                "physical_opportunity_id": "p2",
                "context_cell": "cell-a",
                "session_id": "2026-07-01",
                "expert_id": "expert-a",
                "net_pnl_ticks": -20.0,
                "capacity_eligible": True,
            },
        ]
    )
    states = []
    for protocol in ("daily_frozen", "event_updated"):
        for cell, expert in (("cell-a", "expert-a"), ("cell-b", "expert-b")):
            states.append(
                {
                    "sequence": "NQ 09-26",
                    "profile": "Balanced",
                    "protocol": protocol,
                    "decision_index": 0,
                    "decision_asof": "2026-07-01T10:00:00-06:00",
                    "session_id": "2026-07-01",
                    "context_cell": cell,
                    "state": "ON",
                    "selected_expert_id": expert,
                }
            )
    executions = pd.DataFrame(
        [
            {
                "sequence": "NQ 09-26",
                "profile": "Balanced",
                "protocol": "daily_frozen",
                "physical_opportunity_id": "p1",
                "session_id": "2026-07-01",
                "signal_dt": "2026-07-01T10:00:00-06:00",
                "selected_expert_id": "expert-a",
                "selected_outcome_id": "o1",
                "executed": True,
                "net_pnl_ticks": 10.0,
            },
            {
                "sequence": "NQ 09-26",
                "profile": "Balanced",
                "protocol": "daily_frozen",
                "physical_opportunity_id": "p2",
                "session_id": "2026-07-01",
                "signal_dt": "2026-07-01T10:01:00-06:00",
                "selected_expert_id": "expert-a",
                "selected_outcome_id": "o3",
                "executed": False,
                "net_pnl_ticks": None,
            },
            {
                "sequence": "NQ 09-26",
                "profile": "Balanced",
                "protocol": "event_updated",
                "physical_opportunity_id": "p1",
                "session_id": "2026-07-01",
                "signal_dt": "2026-07-01T10:00:00-06:00",
                "selected_expert_id": "expert-b",
                "selected_outcome_id": "o2",
                "executed": True,
                "net_pnl_ticks": -5.0,
            },
            {
                "sequence": "NQ 09-26",
                "profile": "Balanced",
                "protocol": "event_updated",
                "physical_opportunity_id": "p2",
                "session_id": "2026-07-01",
                "signal_dt": "2026-07-01T10:01:00-06:00",
                "selected_expert_id": None,
                "selected_outcome_id": None,
                "executed": False,
                "net_pnl_ticks": None,
            },
        ]
    )
    summaries = [
        {
            "sequence": "NQ 09-26",
            "profile": "Balanced",
            "protocol": "daily_frozen",
            "dynamic_regret": {
                "selector_paper_reward_ticks": -15.0,
                "selector_switch_penalties_ticks": 0.0,
                "selector_penalized_objective_ticks": -15.0,
            },
            "execution": {"net_ticks": 10.0},
        },
        {
            "sequence": "NQ 09-26",
            "profile": "Balanced",
            "protocol": "event_updated",
            "dynamic_regret": {
                "selector_paper_reward_ticks": None,
                "selector_switch_penalties_ticks": None,
                "selector_penalized_objective_ticks": None,
            },
            "execution": {"net_ticks": -5.0},
        },
    ]
    return outcomes, pd.DataFrame(states), executions, summaries


def test_gap_attribution_reconciles_deduplication_and_capacity():
    result = analyze_sequence(*_fixture(), switch_penalty_ticks=15.0)
    gap = result["gap_rows"][0]

    assert gap["cellwise_paper_reward_ticks"] == -15.0
    assert gap["deduplicated_candidate_reward_ticks"] == -10.0
    assert gap["deduplication_impact_ticks"] == 5.0
    assert gap["capacity_impact_ticks"] == 20.0
    assert gap["executed_net_ticks"] == 10.0
    assert gap["accounting_reconciled"] is True

    divergence = {
        row["divergence_class"]: row
        for row in result["divergence_summary_rows"]
    }
    assert divergence["EVENT_EXPERT_REPLACEMENT"][
        "event_minus_daily_ticks"
    ] == -15.0
    assert divergence["EVENT_DEACTIVATION"][
        "event_minus_daily_ticks"
    ] == 0.0


def test_turnover_classifies_off_gaps_replacements_and_intraday_churn():
    rows = pd.DataFrame(
        [
            {
                "sequence": "NQ 09-26",
                "profile": "Balanced",
                "protocol": "event_updated",
                "decision_index": index,
                "decision_asof": f"2026-07-0{session}T10:0{index}:00-06:00",
                "session_id": f"2026-07-0{session}",
                "context_cell": "cell-a",
                "state": state,
                "selected_expert_id": expert,
            }
            for index, (session, state, expert) in enumerate(
                (
                    (1, "OFF", None),
                    (1, "ON", "expert-a"),
                    (1, "OFF", None),
                    (1, "ON", "expert-a"),
                    (2, "OFF", None),
                    (2, "ON", "expert-b"),
                    (2, "ON", "expert-c"),
                )
            )
        ]
    )
    ledger, summary = classify_policy_turnover(rows)

    assert [row["transition_category"] for row in ledger] == [
        "FIRST_ACTIVATION",
        "OFF_GAP_OPEN",
        "SAME_EXPERT_REACTIVATION",
        "OFF_GAP_OPEN",
        "EXPERT_REPLACEMENT_AFTER_OFF_GAP",
        "DIRECT_EXPERT_REPLACEMENT",
    ]
    assert summary["expert_replacements_total"] == 2
    assert summary["same_session_event_updated_churn"] == 5


def test_panel_summary_and_writer_keep_diagnostic_lock(tmp_path: Path):
    result = analyze_sequence(*_fixture(), switch_penalty_ticks=15.0)
    summary = build_panel_summary(result)

    assert summary["selector_configuration_changed"] is False
    assert summary["primary_static_comparison"]["sequences_beating_static"] == 0
    output = write_dynamic_selector_diagnostic(
        tmp_path / "diagnostic",
        result,
        panel_summary=summary,
        source_phase4_manifest={
            "schema_version": "dynamic_multisequence_replay.v1",
            "manifest_sha256": "a" * 64,
        },
        parity_record={"passed": True},
    )
    manifest = json.loads(
        (output / "diagnostic_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["manifest_sha256"]
    assert manifest["contracts"]["selector_replayed"] is False
    assert manifest["forward_authorized"] is False
