from __future__ import annotations

import pandas as pd

from ta_foundation.analysis.entry_strategies.orb.signals import detect_orb


TZ = "America/Denver"


def _orb_bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-05 07:30", periods=len(rows), freq="1min", tz=TZ)
    return pd.DataFrame(
        {
            "dt": idx,
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [100] * len(rows),
        }
    )


def _params(direction: int) -> dict:
    return {
        "signal_type": "failure_reclaim",
        "orb_minutes": 5,
        "session_open_hour": 7,
        "session_open_minute": 30,
        "session_close_hour": 10,
        "direction": direction,
        "min_range_ticks": 4,
        "tick_size": 0.25,
        "min_sweep_ticks": 1,
        "close_back_ticks": 1,
        "max_reclaim_bars": 2,
        "one_signal_per_side": True,
    }


def test_orb_failure_reclaim_after_high_sweep_emits_short() -> None:
    bars = _orb_bars(
        [
            (100.0, 101.0, 99.0, 100.0),
            (100.0, 101.5, 99.5, 100.5),
            (100.5, 102.0, 100.0, 101.0),
            (101.0, 101.75, 99.75, 100.25),
            (100.25, 101.25, 99.25, 100.0),
            (100.0, 102.50, 100.0, 102.10),  # sweep above OR high, no reclaim yet
            (102.0, 102.20, 100.75, 101.50),  # close back inside by 1 tick
        ]
    )

    signals = detect_orb(bars, _params(-1))

    assert len(signals) == 1
    sig = signals.iloc[0]
    assert int(sig["direction"]) == -1
    assert sig["signal_type"] == "failure_reclaim"
    assert sig["swept_side"] == "orb_high"
    assert int(sig["bars_to_reclaim"]) == 1


def test_orb_failure_reclaim_after_low_sweep_emits_long() -> None:
    bars = _orb_bars(
        [
            (100.0, 101.0, 99.0, 100.0),
            (100.0, 101.5, 99.5, 100.5),
            (100.5, 102.0, 100.0, 101.0),
            (101.0, 101.75, 99.75, 100.25),
            (100.25, 101.25, 99.25, 100.0),
            (100.0, 100.50, 98.75, 99.00),  # sweep below OR low, no reclaim yet
            (99.0, 100.25, 99.00, 99.75),   # close back inside by 1 tick
        ]
    )

    signals = detect_orb(bars, _params(1))

    assert len(signals) == 1
    sig = signals.iloc[0]
    assert int(sig["direction"]) == 1
    assert sig["signal_type"] == "failure_reclaim"
    assert sig["swept_side"] == "orb_low"
    assert int(sig["bars_to_reclaim"]) == 1
