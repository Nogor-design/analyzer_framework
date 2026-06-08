"""NT AtrTrail parity-replica tests (deterministic synthetic bars)."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ta_foundation.analysis.exits.nt_atr_trail_parity import (
    NtAtrTrailConfig,
    _round_to_tick,
    compute_atr,
    replicate_nt_atr_trail,
    replicate_nt_atr_trail_tick,
)

T0 = datetime(2026, 5, 1, 9, 0)


def _bars(rows):
    # rows: list of (minute_offset, high, low, close, atr)
    return pd.DataFrame([
        {"dt": T0 + timedelta(minutes=m), "high": h, "low": l, "close": c, "atr": a}
        for (m, h, l, c, a) in rows
    ])


CFG = NtAtrTrailConfig(atr_period=14, atr_multiple=2.0, stop_ticks=60, tick_size=0.25)


def test_round_to_tick():
    assert _round_to_tick(104.07, 0.25) == 104.0
    assert _round_to_tick(104.20, 0.25) == 104.25


def test_initial_stop_fires():
    # long entry 100 -> initial stop 100 - 60*0.25 = 85. First bar dips to 84.
    bars = _bars([(1, 101, 84, 100, 4.0)])
    r = replicate_nt_atr_trail(entry_dt=T0, entry_price=100.0, direction=1, bars=bars, cfg=CFG)
    assert r["exit_price"] == 85.0 and r["reason"] == "trail_stop"


def test_trail_ratchets_up_then_fills():
    # rising highs ratchet the stop up (high_since - 2*ATR); ATR fixed at 4 -> offset 8.
    # stops: t1 104-8=96, t2 108-8=100, t3 112-8=104; t4 low 95 <= 104 -> exit 104.
    bars = _bars([
        (1, 104, 100, 104, 4.0),
        (2, 108, 104, 108, 4.0),
        (3, 112, 108, 112, 4.0),
        (4, 112, 95, 96, 4.0),
    ])
    r = replicate_nt_atr_trail(entry_dt=T0, entry_price=100.0, direction=1, bars=bars, cfg=CFG)
    assert r["reason"] == "trail_stop"
    assert r["exit_price"] == 104.0


def test_short_trail_ratchets_down():
    # short entry 100 -> initial stop 115. falling lows ratchet stop down (low_since + 8).
    # t1 96+8=104, t2 92+8=100, t3 88+8=96; t4 high 97 >= 96 -> exit 96.
    bars = _bars([
        (1, 100, 96, 96, 4.0),
        (2, 96, 92, 92, 4.0),
        (3, 92, 88, 88, 4.0),
        (4, 97, 88, 90, 4.0),
    ])
    r = replicate_nt_atr_trail(entry_dt=T0, entry_price=100.0, direction=-1, bars=bars, cfg=CFG)
    assert r["reason"] == "trail_stop"
    assert r["exit_price"] == 96.0


def test_no_exit_in_window():
    # price rises away, trail never caught -> NT would have exited via session/signal.
    bars = _bars([(1, 104, 101, 104, 4.0), (2, 108, 105, 108, 4.0)])
    r = replicate_nt_atr_trail(entry_dt=T0, entry_price=100.0, direction=1, bars=bars, cfg=CFG)
    assert r["reason"] == "no_exit_in_window" and r["exit_price"] is None


def test_stop_only_ratchets_never_loosens():
    # a lower high later must NOT pull the long stop back down.
    bars = _bars([
        (1, 120, 100, 120, 4.0),   # stop -> 112
        (2, 105, 101, 102, 4.0),   # proposed 105-8=97 < 112 -> ignored (ratchet)
        (3, 113, 111, 112, 4.0),   # low 111 <= 112 -> exit at 112
    ])
    r = replicate_nt_atr_trail(entry_dt=T0, entry_price=100.0, direction=1, bars=bars, cfg=CFG)
    assert r["exit_price"] == 112.0


def test_tick_trail_initial_and_ratchet():
    cfg = NtAtrTrailConfig()
    dts = np.arange(5)
    # long entry 100, initial stop 85, atr const 4 -> offset 8.
    # prices 100,104,108,112,95 -> stop_active 85,92,96,100,104 -> fill at 95<=104 => 104.
    prices = np.array([100.0, 104, 108, 112, 95])
    atr = np.full(5, 4.0)
    r = replicate_nt_atr_trail_tick(entry_price=100.0, direction=1,
                                    tick_prices=prices, tick_dts=dts, tick_atr=atr, cfg=cfg)
    assert r["reason"] == "trail_stop" and r["exit_price"] == 104.0


def test_tick_trail_tighter_than_bar_on_intrabar_spike():
    # An intrabar SPIKE high the bar-close never records makes the tick trail
    # ratchet higher -> tighter stop -> earlier/ higher exit than the bar model.
    cfg = NtAtrTrailConfig()
    dts = np.arange(4)
    prices = np.array([100.0, 120.0, 101.0, 108.0])  # spike to 120 then settle
    atr = np.full(4, 4.0)  # offset 8 -> after spike stop ~ 112
    r = replicate_nt_atr_trail_tick(entry_price=100.0, direction=1,
                                    tick_prices=prices, tick_dts=dts, tick_atr=atr, cfg=cfg)
    # spike 120 sets stop 112 (active next tick); 101 <= 112 -> exit at 112.
    assert r["exit_price"] == 112.0


def test_tick_trail_no_exit():
    cfg = NtAtrTrailConfig()
    r = replicate_nt_atr_trail_tick(entry_price=100.0, direction=1,
                                    tick_prices=np.array([101.0, 103, 105]),
                                    tick_dts=np.arange(3), tick_atr=np.full(3, 4.0), cfg=cfg)
    assert r["reason"] == "no_exit_in_window"


def test_compute_atr_modes_differ():
    # VARYING true range (alternating wide/narrow bars) — Wilder and SMA only
    # diverge when TR is non-constant. This is exactly the parity sensitivity:
    # on steady tape they agree, on bursty tape they don't (the real-data test).
    rng = np.random.default_rng(7)
    base = np.cumsum(rng.normal(0, 1, 60)) + 100
    span = np.where(np.arange(60) % 2 == 0, 0.5, 6.0)  # bursty ranges
    bars = pd.DataFrame({"high": base + span, "low": base - span, "close": base})
    w = compute_atr(bars, 14, "wilder")
    s = compute_atr(bars, 14, "sma")
    assert w.iloc[-1] > 0 and s.iloc[-1] > 0
    assert abs(w.iloc[-1] - s.iloc[-1]) > 1e-6  # genuinely different smoothings
