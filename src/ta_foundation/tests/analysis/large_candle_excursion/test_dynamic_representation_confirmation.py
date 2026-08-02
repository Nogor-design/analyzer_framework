from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from ta_foundation.analysis.large_candle_excursion.dynamic_representation_confirmation import (
    _canonical_confirmation_rows,
    run_representation_confirmation,
)


def _confirmation_rows(sequence: str) -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-05 10:00", tz="America/Denver")
    for index in range(4):
        signal = start + timedelta(days=index // 2, minutes=index * 20)
        physical_id = f"{sequence}-p{index}"
        aligned = -1.0 if index % 2 == 0 else 1.0
        for lane_index, (minutes_early, timeframe) in enumerate(
            ((2, 2), (1, 1))
        ):
            lane_signal = signal - timedelta(minutes=minutes_early - 1)
            for mode in ("continuation", "reversion"):
                continuation = 20.0 * aligned + lane_index
                reward = (
                    continuation if mode == "continuation" else -continuation
                )
                rows.append(
                    {
                        "sequence": sequence,
                        "physical_opportunity_id": physical_id,
                        "lane_event_id": f"{physical_id}-e{lane_index}",
                        "lane_id": f"tf{timeframe}m-lane",
                        "timeframe": timeframe,
                        "session_id": signal.date().isoformat(),
                        "signal_side": "bull",
                        "signal_dt": lane_signal,
                        "entry_dt": signal + timedelta(minutes=1),
                        "context_dt": lane_signal,
                        "mode": mode,
                        "net_pnl_ticks": reward,
                        "exit_known_dt": signal + timedelta(minutes=10),
                        "trade_direction": (
                            1 if mode == "continuation" else -1
                        ),
                        "trend_state": "up" if aligned > 0 else "down",
                        "return_60m": 0.01 * aligned,
                        "vwap_slope_15": 2.0 * aligned,
                        "close_vs_vwap": 10.0 * aligned,
                        "signal_ratio": 1.2 + 0.1 * lane_index,
                        "latched_outside_window": False,
                        "zone_break_trigger": lane_index == 0,
                    }
                )
    return pd.DataFrame(rows)


def _development() -> pd.DataFrame:
    size = 43334
    index = np.arange(size)
    aligned = np.where(index % 2 == 0, -1.0, 1.0)
    sequences = np.array(
        [
            "NQ 03-26",
            "NQ 06-26",
            "NQ 09-26",
            "ES 03-26",
            "RTY 03-26",
            "YM 03-26",
            "YM 06-26",
        ]
    )
    return pd.DataFrame(
        {
            "sequence": sequences[index % len(sequences)],
            "physical_opportunity_id": [f"dev-{value}" for value in index],
            "matched_advantage_ticks": 40.0 * aligned,
            "trend_state_aligned": aligned,
            "return_60m_aligned": 0.01 * aligned,
            "vwap_slope_15_aligned": 2.0 * aligned,
            "close_vs_vwap_aligned": 10.0 * aligned,
            "close_vs_vwap_abs": 10.0 + (index % 3),
        }
    )


def test_canonical_lane_uses_latest_signal_without_reward_selection():
    rows = _confirmation_rows("GC 04-26")
    physical = _canonical_confirmation_rows(rows)
    assert set(physical["canonical_timeframe"]) == {1}
    assert set(physical["canonical_lane_id"]) == {"tf1m-lane"}
    assert set(physical["continuation_net_ticks"]) == {-19.0, 21.0}


def test_confirmation_scores_each_unused_opportunity_once():
    sources = {
        sequence: _confirmation_rows(sequence)
        for sequence in ("GC 04-26", "NG 03-26", "MNQ 03-26")
    }
    result = run_representation_confirmation(_development(), sources)
    assert result["manifest"]["counts"]["development_opportunities"] == 43334
    assert result["manifest"]["counts"]["confirmation_opportunities"] == 12
    assert result["manifest"]["counts"]["decisions"] == 12
    assert result["manifest"]["counts"]["execution_rows"] == 36
    assert len(
        {
            row["physical_opportunity_id"]
            for row in result["decision_rows"]
        }
    ) == 12
    assert {
        row["representation_id"] for row in result["fit_rows"]
    } == {"trend_state", "directional_momentum", "vwap_location"}
