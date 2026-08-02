from __future__ import annotations

import pandas as pd

from ta_foundation.analysis.large_candle_excursion.gc_opening_inventory_robustness import (
    run_gc_opening_inventory_robustness,
)


def test_gc_tick_robustness_resolves_paths_and_passes_strong_synthetic_panel(tmp_path):
    dates = pd.bdate_range("2026-01-05", periods=10)
    parent_rows = []
    cube_rows = []
    tick_lines = []
    for index, date in enumerate(dates):
        day = date.strftime("%Y%m%d")
        session = date.strftime("%Y-%m-%d")
        direction = 1 if index % 2 == 0 else -1
        is_loss = index == 0
        entry_price = 100.0
        hit_price = entry_price - direction * 15.0 if is_loss else entry_price + direction * 7.5
        gross = -150.0 if is_loss else 75.0
        reason = "stop" if is_loss else "target"
        physical_id = f"gc|{session}"
        entry = pd.Timestamp(f"{session}T14:30:00Z")
        # Minute outcomes timestamp an immediate intrabar exit at the start of
        # the entry/exit bar. The tick scan must still include that full minute.
        exit_dt = entry
        parent_rows.append({
            "sequence": "GC 04-26", "physical_opportunity_id": physical_id,
            "session_id": session, "session_index": index, "block_index": index // 5,
            "complete_block": True, "signal_direction": direction,
            "overnight_direction": direction, "primary_mode": "continuation",
            "continuation_reward_ticks": gross - 4.398,
            "reversion_reward_ticks": -154.398,
            "paired_uplift_ticks": gross + 150.0,
        })
        cube_rows.append({
            "sequence": "GC 04-26", "physical_opportunity_id": physical_id,
            "mode": "continuation", "entry_dt": entry, "exit_dt": exit_dt,
            "exit_known_dt": exit_dt + pd.Timedelta(minutes=1),
            "entry_price": entry_price, "exit_price": hit_price,
            "gross_pnl_ticks": gross, "net_pnl_ticks": gross - 4.398,
            "exit_reason": reason, "ambiguous_same_bar": False,
            "trade_direction": direction, "round_trip_cost_ticks": 4.398,
        })
        tick_lines.append(
            f"{day} 143010 1234567;{hit_price};{hit_price - 0.1};{hit_price + 0.1};1\n"
        )
    tick_path = tmp_path / "GC 04-26 Tick.Last.txt"
    tick_path.write_text("".join(tick_lines), encoding="utf-8")

    result = run_gc_opening_inventory_robustness(
        pd.DataFrame(parent_rows),
        pd.DataFrame(cube_rows),
        tick_path,
        source_bindings={"synthetic": True},
    )

    assert result["summary"]["result_label"] == "GC_OBSERVATION_SURVIVES_SAME_CONTRACT_ROBUSTNESS"
    assert result["summary"]["comparison"]["coverage_pct"] == 100.0
    assert result["summary"]["tick_capacity"]["executed_trades"] == 10
    assert len(result["bootstrap_rows"]) == 10_000
    assert len(result["permutation_rows"]) == 10_000
    assert all(result["summary"]["gates"]["criteria"].values())
