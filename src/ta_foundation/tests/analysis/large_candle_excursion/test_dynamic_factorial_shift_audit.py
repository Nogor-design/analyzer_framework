from __future__ import annotations

from datetime import timedelta

import pandas as pd

from ta_foundation.analysis.large_candle_excursion.dynamic_factorial_shift_audit import (
    CONFIRMATION_SEQUENCES,
    DISCOVERY_SEQUENCES,
    SENSITIVITY_SEQUENCES,
    run_factorial_shift_audit,
)


def _rows(sequence: str) -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-05 10:00", tz="America/Denver")
    for session in range(25):
        for timeframe in (1, 2, 3, 5):
            for lookback in (5, 10, 20):
                for multiplier in (1.5, 1.6, 1.7):
                    lane = f"{sequence}-{session}-{timeframe}-{lookback}-{multiplier}"
                    signal = start + timedelta(days=session, minutes=timeframe)
                    physical = f"physical-{lane}"
                    for mode in ("continuation", "reversion"):
                        reward = (multiplier - 1.6) * 20 + (1 if mode == "continuation" else -1)
                        rows.append({
                            "sequence": sequence,
                            "lane_event_id": lane,
                            "physical_opportunity_id": physical,
                            "session_id": signal.date().isoformat(),
                            "timeframe": timeframe,
                            "lookback": lookback,
                            "multiplier": multiplier,
                            "mode": mode,
                            "signal_dt": signal,
                            "entry_dt": signal + timedelta(minutes=1),
                            "exit_known_dt": signal + timedelta(minutes=2),
                            "trade_direction": 1 if mode == "continuation" else -1,
                            "net_pnl_ticks": reward,
                        })
    return pd.DataFrame(rows)


def test_complete_factorial_and_transition_cardinality():
    sequences = DISCOVERY_SEQUENCES + CONFIRMATION_SEQUENCES + SENSITIVITY_SEQUENCES
    result = run_factorial_shift_audit({name: _rows(name) for name in sequences})
    assert len(result["exact_rows"]) == 10 * 72
    assert len(result["contrast_rows"]) == 10 * 12
    assert len(result["transition_rows"]) == 10 * 4 * 2 * 4
    assert len(result["capacity_rows"]) == 10 * 25 * 72
    assert result["summary"]["panel"]["passing_static_cells"] == 0
    assert len(result["summary"]["contrast_panel"]) == 12
