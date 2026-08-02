from __future__ import annotations

import pandas as pd
import pytest

from ta_foundation.analysis.large_candle_excursion.opening_inventory_audit import (
    DEVELOPMENT_SEQUENCES,
    SENSITIVITY_SEQUENCES,
    TRANSFER_SEQUENCES,
    OpeningInventoryAuditError,
    run_opening_inventory_audit,
)


SEQUENCES = DEVELOPMENT_SEQUENCES + TRANSFER_SEQUENCES + SENSITIVITY_SEQUENCES


def _synthetic_inputs():
    rows = {}
    bars = {}
    registry = {}
    dates = pd.bdate_range("2026-01-05", periods=26)
    for sequence_index, sequence in enumerate(SEQUENCES):
        bar_rows = []
        for index, date in enumerate(dates):
            day = date.strftime("%Y-%m-%d")
            cash_close = 1000.0 + sequence_index * 100 + index
            prior_cash = 1000.0 + sequence_index * 100 + max(0, index - 1)
            overnight_sign = 1 if index % 2 else -1
            preopen = prior_cash + overnight_sign * 4.0
            bar_rows.extend([
                {"dt": f"{day}T07:29:00-07:00", "close": preopen},
                {"dt": f"{day}T13:59:00-07:00", "close": cash_close},
            ])
        bars[sequence] = pd.DataFrame(bar_rows)
        outcome_rows = []
        opportunity_index = 0
        for session_index, date in enumerate(dates[1:], start=1):
            day = date.strftime("%Y-%m-%d")
            overnight_direction = 1 if session_index % 2 else -1
            for event_index, minute in enumerate((40, 60, 80)):
                signal_direction = overnight_direction if event_index != 1 else -overnight_direction
                aligned = signal_direction == overnight_direction
                primary_mode = "continuation" if aligned else "reversion"
                selected_reward = -5.0 if opportunity_index % 5 == 0 else 10.0
                other_reward = -10.0 if opportunity_index % 5 == 0 else -5.0
                hour, minute_of_hour = divmod(7 * 60 + minute, 60)
                signal_dt = pd.Timestamp(
                    f"{day}T{hour:02d}:{minute_of_hour:02d}:00-07:00"
                )
                entry_dt = signal_dt + pd.Timedelta(minutes=1)
                physical_id = f"{sequence}|{day}|{event_index}"
                lane_id = f"lane|{physical_id}"
                for mode in ("continuation", "reversion"):
                    reward = selected_reward if mode == primary_mode else other_reward
                    direction = signal_direction if mode == "continuation" else -signal_direction
                    outcome_rows.append({
                        "sequence": sequence,
                        "physical_opportunity_id": physical_id,
                        "lane_event_id": lane_id,
                        "session_id": day,
                        "signal_dt": signal_dt,
                        "entry_dt": entry_dt,
                        "exit_known_dt": entry_dt + pd.Timedelta(minutes=1),
                        "signal_direction": signal_direction,
                        "mode": mode,
                        "trade_direction": direction,
                        "net_pnl_ticks": reward,
                        "timeframe": 1,
                        "lookback": 5,
                        "multiplier": 1.5,
                        "trigger_type": "fresh",
                    })
                opportunity_index += 1
        rows[sequence] = pd.DataFrame(outcome_rows)
        registry[sequence] = {
            "path": f"synthetic/{sequence}", "sha256": sequence,
            "tick_size": 1.0, "tick_value": 1.0, "cost": 1.0,
        }
    return rows, bars, registry


def test_opening_inventory_audit_reports_complete_passing_synthetic_panel():
    rows, bars, registry = _synthetic_inputs()
    result = run_opening_inventory_audit(rows, bars, raw_source_registry=registry)
    assert result["summary"]["result_label"] == "OPENING_INVENTORY_DEVELOPMENT_EVIDENCE"
    assert result["summary"]["transfer_label"] == "TRANSFER_PRESENT"
    assert result["summary"]["panel"] == {
        "sequences": 10,
        "eligible_opportunities": 750,
        "policy_capacity_rows": 3000,
        "complete_policy_blocks": 200,
    }
    assert len(result["sequence_policy_rows"]) == 40
    assert len(result["mechanism_rows"]) == 10
    assert all(row["paired_uplift_ticks"] > 0 for row in result["mechanism_rows"])
    ledger = pd.DataFrame(result["capacity_rows"])
    assert not ledger.duplicated(["sequence", "policy", "physical_opportunity_id"]).any()
    assert (ledger["executed"].astype(bool) ^ ledger["capacity_skip"].astype(bool)).all()


def test_opening_inventory_audit_rejects_missing_paired_mode():
    rows, bars, registry = _synthetic_inputs()
    sequence = SEQUENCES[0]
    rows[sequence] = rows[sequence].iloc[1:].copy()
    with pytest.raises(OpeningInventoryAuditError, match="missing paired mode"):
        run_opening_inventory_audit(rows, bars, raw_source_registry=registry)
