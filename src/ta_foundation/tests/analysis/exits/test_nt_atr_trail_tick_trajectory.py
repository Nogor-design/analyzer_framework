"""Live-leg tick trajectory replica tests (Phase 2 of the parity loop, pure Python).

Pins the THREE live-path semantics that differ from the backtest leg (audited from
PantheonMaster.cs 2026-06-12): the ratchet is FLOORED at the INIT stop (live never
widens — lastSubmittedStopPrice starts AT the initial stop, not 0), the
side-of-market guard skips proposals within 2 ticks of the market, and events are
per-ChangeOrder ticks rather than bar closes.

Hand-verified arithmetic uses CFG: initial stop = entry -/+ 4 ticks = 1.0 point,
trail pull = AtrTrailMultiple(2.0) * atr.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ta_foundation.analysis.exits.nt_atr_trail_parity import (
    NtAtrTrailConfig,
    replicate_nt_atr_trail_tick_trajectory,
)
from ta_foundation.analysis.exits.stop_audit_parity import parity_report

T0 = datetime(2026, 5, 1, 9, 0)
CFG = NtAtrTrailConfig(atr_period=14, atr_multiple=2.0, stop_ticks=4, tick_size=0.25)


def _run(prices, *, entry=100.0, direction=1, atr=1.0):
    p = np.asarray(prices, dtype=float)
    dts = np.array([np.datetime64(T0) + np.timedelta64(i * 250, "ms") for i in range(len(p))])
    return replicate_nt_atr_trail_tick_trajectory(
        entry_price=entry, direction=direction, tick_prices=p, tick_dts=dts,
        tick_atr=np.full(len(p), float(atr)), cfg=CFG,
    )


def test_live_ratchet_is_floored_at_init_stop():
    # Pull = 2.0; price tops at 100.5 -> best proposal 98.5 < INIT 99.0. The live
    # path NEVER widens (backtest's zero-sentinel would have submitted 98.0 on the
    # first bar) -> no TRAIL events, stop stays at the INIT 99.0.
    out = _run([100.0, 100.25, 100.5, 100.25, 100.0])
    assert list(out["trajectory"]["event"]) == ["INIT"]
    assert float(out["trajectory"]["stop"].iloc[0]) == 99.0
    assert out["exit"]["reason"] == "no_exit_in_window"


def test_live_trail_ratchets_and_fills_at_resting_stop():
    # ext 102 -> proposal 100.0 (first to clear the 99.0 floor), ext 103 -> 101.0.
    # Then 100.75 trades through the RESTING 101.0 stop -> exit at the stop price.
    out = _run([100.0, 101.0, 102.0, 103.0, 100.75])
    trails = out["trajectory"][out["trajectory"]["event"] == "TRAIL"]
    assert list(trails["stop"]) == [100.0, 101.0]
    assert list(trails["old_stop"]) == [99.0, 100.0]      # ChangeOrder chain from INIT
    assert out["exit"]["reason"] == "trail_stop"
    assert out["exit"]["exit_price"] == 101.0


def test_side_of_market_guard_blocks_near_market_proposals():
    # Tiny ATR (0.125 -> pull 0.25): every proposal sits 1 tick under the running
    # high, INSIDE the 2-tick guard buffer, and on pullbacks the proposal stays
    # pinned near the high while price moves AWAY-side -> guard never clears.
    # C# would refuse these ChangeOrders (broker-rejectable near-market stops);
    # the stop must remain the INIT 99.0 and the collapse fills there.
    out = _run([100.0, 101.0, 102.0, 98.9], atr=0.125)
    assert list(out["trajectory"]["event"]) == ["INIT"]
    assert out["exit"]["reason"] == "trail_stop"
    assert out["exit"]["exit_price"] == 99.0


def test_short_direction_mirrors():
    # INIT 101.0; ext 98 -> proposal 100.0, ext 97 -> 99.0; 99.25 fills the resting 99.0.
    out = _run([100.0, 99.0, 98.0, 97.0, 99.25], direction=-1)
    trails = out["trajectory"][out["trajectory"]["event"] == "TRAIL"]
    assert list(trails["stop"]) == [100.0, 99.0]
    assert out["exit"]["reason"] == "trail_stop"
    assert out["exit"]["exit_price"] == 99.0


def test_parity_report_with_tick_replay_self_consistent():
    # Render the replica's own trajectory into a live-path audit (INIT + TRAIL rows)
    # and grade through parity_report(replay=...) -> perfect PASS.
    prices = [100.0, 101.0, 102.0, 103.0, 100.75]
    traj = _run(prices)["trajectory"]
    audit = pd.DataFrame([
        {"time": r.dt, "event": r.event, "policy": "AtrTrail",
         "dir": "long", "entry": 100.0, "market": r.favorable, "favorable": r.favorable,
         "atr": 1.0 if r.event == "TRAIL" else float("nan"), "peakProfit": 0.0,
         "oldStop": r.old_stop, "newStop": r.stop}
        for r in traj.itertuples(index=False)
    ])
    bars = pd.DataFrame([{"dt": T0 + timedelta(minutes=1), "high": 103.0, "low": 99.0,
                          "close": 100.75, "atr": 1.0}])

    def replay(entry_dt, entry_price, direction):
        return _run(prices, entry=entry_price, direction=direction)

    rep = parity_report(audit, bars, CFG, replay=replay)
    assert rep["passed"] is True, rep["summary"]
    assert rep["n_trades"] == 1
    assert rep["overall_stop_match_rate"] == 1.0
    assert rep["summary"]["n_events"].iloc[0] == 2
