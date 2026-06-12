"""Tests for the shared Pantheon stop engine + trail battery.

These lock the Python mirror of ``PantheonStopEngine.cs`` to its expected
behaviour and cross-check it against the pre-existing AtrTrail parity replica
(``nt_atr_trail_parity.replicate_nt_atr_trail_tick``) so the extraction is proven
faithful, not just internally consistent.
"""
from __future__ import annotations

import numpy as np
import pytest

from ta_foundation.analysis.exits.nt_atr_trail_parity import (
    NtAtrTrailConfig,
    replicate_nt_atr_trail_tick,
)
from ta_foundation.analysis.exits.pantheon_stop_engine import (
    LONG,
    SHORT,
    PantheonStopConfig,
    PantheonStopPolicy,
    initial_stop,
)
from ta_foundation.analysis.exits.pantheon_trail_battery import (
    ALL_POLICIES,
    path_trend_then_reverse,
    replay_trail,
    run_battery,
)

TICK = 0.25
PV = 20.0


def _cfg(policy, **kw):
    return PantheonStopConfig(policy=policy, tick_size=TICK, point_value=PV, **kw)


# ── Per-policy behaviour ────────────────────────────────────────────────────────

@pytest.mark.parametrize("policy", [PantheonStopPolicy.FIXED_STOP, PantheonStopPolicy.FIXED_RR])
@pytest.mark.parametrize("direction", [LONG, SHORT])
def test_fixed_policies_never_trail(policy, direction):
    cfg = _cfg(policy)
    prices = path_trend_then_reverse(direction=direction)
    rep = replay_trail(direction, 20000.0, prices, cfg, atr=15.0)
    assert rep.n_stop_moves == 0
    # the stop stays exactly at the initial protective level
    assert rep.final_stop == pytest.approx(initial_stop(direction, 20000.0, cfg))


@pytest.mark.parametrize("direction", [LONG, SHORT])
def test_atr_trail_locks_profit_on_reversal(direction):
    cfg = _cfg(PantheonStopPolicy.ATR_TRAIL)
    prices = path_trend_then_reverse(direction=direction)
    rep = replay_trail(direction, 20000.0, prices, cfg, atr=15.0)
    assert rep.exit_reason == "trail_stop"
    assert rep.n_stop_moves > 0
    assert rep.pnl_ticks > 0  # the trail got us out in profit, not at the initial stop


@pytest.mark.parametrize("direction", [LONG, SHORT])
def test_breakeven_arms_once_at_entry_plus(direction):
    cfg = _cfg(PantheonStopPolicy.BREAK_EVEN_ONLY, breakeven_trigger_ticks=30, breakeven_plus_ticks=4)
    prices = path_trend_then_reverse(direction=direction)
    rep = replay_trail(direction, 20000.0, prices, cfg, atr=15.0)
    assert rep.n_stop_moves == 1  # arms exactly once, never trails after
    expected = 20000.0 + direction * cfg.breakeven_plus_ticks * TICK
    assert rep.final_stop == pytest.approx(expected)


@pytest.mark.parametrize("direction", [LONG, SHORT])
def test_giveback_locks_fraction_of_peak(direction):
    cfg = _cfg(PantheonStopPolicy.GIVEBACK, giveback_pct=0.40)
    prices = path_trend_then_reverse(direction=direction)
    rep = replay_trail(direction, 20000.0, prices, cfg, atr=15.0)
    assert rep.exit_reason == "trail_stop"
    # peak favourable excursion in this path is run_ticks=200 ticks => 50 points.
    # Giveback protects (1-0.40)=60% => ~120 ticks. Allow slack for tick rounding/path.
    assert rep.pnl_ticks == pytest.approx(120.0, abs=6.0)


@pytest.mark.parametrize("direction", [LONG, SHORT])
def test_gap_through_fills_at_initial_stop(direction):
    # A one-tick gap straight past the stop: no policy can save it; fill at initial stop.
    cfg = _cfg(PantheonStopPolicy.ATR_TRAIL)
    entry = 20000.0
    init = initial_stop(direction, entry, cfg)
    gap = np.array([entry, entry - direction * 200 * TICK])  # jumps well past the stop
    rep = replay_trail(direction, entry, gap, cfg, atr=15.0)
    assert rep.exit_reason == "trail_stop"
    assert rep.exit_price == pytest.approx(init)


# ── Cross-check vs the pre-existing AtrTrail parity replica ──────────────────────

@pytest.mark.parametrize("direction", [LONG, SHORT])
def test_atr_trail_matches_parity_replica(direction):
    """The extracted engine's AtrTrail exit must equal the independent
    ``replicate_nt_atr_trail_tick`` reference on the same tick path."""
    entry = 20000.0
    prices = path_trend_then_reverse(direction=direction)
    atr_const = 15.0

    cfg = _cfg(PantheonStopPolicy.ATR_TRAIL, stop_ticks=60, atr_trail_multiple=2.0)
    # guard off so the comparison isolates the trail math (the reference has no guard)
    rep = replay_trail(direction, entry, prices, cfg, atr=atr_const, apply_market_guard=False)

    ref_cfg = NtAtrTrailConfig(atr_period=14, atr_multiple=2.0, stop_ticks=60, tick_size=TICK)
    ref = replicate_nt_atr_trail_tick(
        entry_price=entry, direction=direction,
        tick_prices=np.asarray(prices, dtype=float),
        tick_dts=np.arange(len(prices)),
        tick_atr=np.full(len(prices), atr_const),
        cfg=ref_cfg,
    )
    assert ref["reason"] == "trail_stop"
    assert rep.exit_reason == "trail_stop"
    assert rep.exit_price == pytest.approx(float(ref["exit_price"]), abs=TICK)


# ── Invariants across the whole battery ─────────────────────────────────────────

def test_battery_has_no_invariant_violations():
    df = run_battery()
    assert len(df) > 0
    violations = df[~(df["stop_monotonic_ok"] & df["stop_side_ok"])]
    assert violations.empty, violations.to_string(index=False)


def test_battery_exercises_every_policy():
    df = run_battery()
    assert set(df["policy"]) == {p.value for p in ALL_POLICIES}
    # trailing policies must actually move the stop somewhere in the battery
    for pol in ("AtrTrail", "Giveback", "BreakEvenOnly"):
        assert df[df["policy"] == pol]["n_stop_moves"].max() > 0
