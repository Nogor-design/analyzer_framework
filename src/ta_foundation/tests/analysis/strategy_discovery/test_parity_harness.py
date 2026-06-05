"""
Tests for the C#↔Python entry-signal parity harness.

These don't run NinjaTrader; they pin the Python side and the diff logic so the
harness itself is trustworthy when an NT log is fed to it.
"""
from __future__ import annotations

import pandas as pd
import pytest

from ta_foundation.analysis.strategy_discovery.parity_harness import (
    diff_signals,
    export_signal_bars,
    parse_nt_signal_log,
)


def _flat(dt, price=100.0):
    # Zero-range bar: size_ticks == 0, so no candle pattern fires.
    return {"dt": dt, "open": price, "high": price, "low": price, "close": price, "volume": 1}


def _bars_with_one_engulfing():
    idx = pd.date_range("2026-02-03 08:30", periods=10, freq="1min", tz="America/Denver")
    rows = [_flat(t) for t in idx]
    # Bar 5: bullish bar that engulfs the prior bar's body and clears 4 ticks.
    rows[5] = {
        "dt": idx[5],
        "open": 98.75,   # <= prior close (100.0)
        "high": 100.75,
        "low": 98.50,
        "close": 100.50,  # >= prior open (100.0); body 1.75; range 9 ticks
        "volume": 10,
    }
    return pd.DataFrame(rows), idx


def test_export_finds_the_engulfing_bar():
    bars, idx = _bars_with_one_engulfing()
    out = export_signal_bars(
        bars, structure="engulfing_bullish", timeframe_minutes=1,
        tick_size=0.25, warmup_bars=0,
    )
    assert len(out) == 1
    assert out.iloc[0]["dt"] == idx[5]
    assert out.iloc[0]["direction"] == 1
    # NT-local wall clock, no offset, matches Time[0] print format.
    assert out.iloc[0]["nt_time"] == "2026-02-03T08:35:00"


def test_warmup_gate_drops_early_signals():
    bars, _ = _bars_with_one_engulfing()
    out = export_signal_bars(
        bars, structure="engulfing_bullish", timeframe_minutes=1,
        tick_size=0.25, warmup_bars=50,  # bar 5 < 50 → dropped
    )
    assert out.empty


def test_diff_clean_against_self():
    bars, _ = _bars_with_one_engulfing()
    out = export_signal_bars(bars, structure="engulfing_bullish", warmup_bars=0)
    # Simulate an NT log that fired on exactly the same bars.
    fake_log = "\n".join(
        f"[SDF-SIGNAL] {t} dir=1 sig=EngulfingBullish bar=99" for t in out["nt_time"]
    )
    nt_df = parse_nt_signal_log(fake_log)
    summary = diff_signals(out, nt_df)
    assert summary["clean"] is True
    assert summary["matched"] == len(out)
    assert summary["missing_in_nt"] == []
    assert summary["extra_in_nt"] == []


def test_diff_flags_missing_and_extra():
    py_df = pd.DataFrame({"nt_time": ["2026-02-03T08:35:00", "2026-02-03T08:40:00"], "direction": [1, 1]})
    nt_df = pd.DataFrame({"nt_time": ["2026-02-03T08:40:00", "2026-02-03T08:45:00"], "direction": [1, -1]})
    summary = diff_signals(py_df, nt_df)
    assert summary["matched"] == 1
    assert summary["missing_in_nt"] == ["2026-02-03T08:35:00"]
    assert summary["extra_in_nt"] == ["2026-02-03T08:45:00"]
    assert summary["clean"] is False


def test_parse_nt_signal_log_extracts_lines():
    text = (
        "some noise\n"
        "[SDF-SIGNAL] 2026-02-03T08:35:00 dir=1 sig=EngulfingBullish bar=312\n"
        "[SDF-SIGNAL] 2026-02-03T09:10:00 dir=-1 sig=PinBarBearish bar=347\n"
        "[SDF] ENTRY Long | bar=312\n"
    )
    df = parse_nt_signal_log(text)
    assert list(df["nt_time"]) == ["2026-02-03T08:35:00", "2026-02-03T09:10:00"]
    assert list(df["direction"]) == [1, -1]


def test_unknown_structure_raises():
    bars, _ = _bars_with_one_engulfing()
    with pytest.raises(ValueError):
        export_signal_bars(bars, structure="not_a_pattern")


def test_empty_bars_returns_empty():
    out = export_signal_bars(pd.DataFrame(), structure="doji")
    assert out.empty
