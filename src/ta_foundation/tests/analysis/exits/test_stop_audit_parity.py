"""Stop-audit trajectory parity comparator tests (pure Python, no NT).

Strategy: generate a known stop trajectory from the Python replica, render it into a
synthetic StopAuditCsvPath dump (the bar-close path emits TRAIL rows only, with the
first row's oldStop reset to 0), then assert the comparator reports perfect agreement
— and that injecting a single bad stop / wrong ATR is caught at the right bar.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from ta_foundation.analysis.exits.nt_atr_trail_parity import (
    NtAtrTrailConfig,
    replicate_nt_atr_trail,
    replicate_nt_atr_trail_trajectory,
)
from ta_foundation.analysis.exits.stop_audit_parity import (
    assign_trade_ids,
    diff_trade_atr,
    diff_trade_stop_trajectory,
    parse_stop_audit,
    parity_report,
)

T0 = datetime(2026, 5, 1, 9, 0)
CFG = NtAtrTrailConfig(atr_period=14, atr_multiple=2.0, stop_ticks=60, tick_size=0.25)


def _bars(rows):
    return pd.DataFrame([
        {"dt": T0 + timedelta(minutes=m), "high": h, "low": l, "close": c, "atr": a}
        for (m, h, l, c, a) in rows
    ])


# Rising-high tape: long 100, atr 4 -> offset 8. Stops ratchet 96,100,104 then fill 104.
RISING = _bars([
    (1, 104, 100, 104, 4.0),
    (2, 108, 104, 108, 4.0),
    (3, 112, 108, 112, 4.0),
    (4, 112, 95, 96, 4.0),
])


def _audit_from_trajectory(traj, *, entry_price, direction, policy="AtrTrail"):
    """Render a replica trajectory into the AppendStopAudit CSV schema (TRAIL rows
    only, first oldStop=0, as the bar-close backtest path writes them)."""
    t = traj[traj["event"] == "TRAIL"].reset_index(drop=True)
    rows = []
    for i, r in enumerate(t.itertuples(index=False)):
        rows.append({
            "time": r.dt, "event": "TRAIL", "policy": policy,
            "dir": "long" if direction > 0 else "short",
            "entry": entry_price, "market": r.favorable, "favorable": r.favorable,
            "atr": r.atr, "peakProfit": 0.0,
            "oldStop": 0.0 if i == 0 else r.old_stop, "newStop": r.stop,
        })
    return pd.DataFrame(rows)


def _entries(entry_dt, entry_price, direction):
    return pd.DataFrame([{"entry_dt": entry_dt, "entry_price": entry_price, "direction": direction}])


def test_trajectory_exit_matches_exit_only_replica():
    # The refactor's guard: the trajectory replay and the exit-only replica agree.
    rep = replicate_nt_atr_trail_trajectory(entry_dt=T0, entry_price=100.0, direction=1,
                                            bars=RISING, cfg=CFG)
    ex = replicate_nt_atr_trail(entry_dt=T0, entry_price=100.0, direction=1, bars=RISING, cfg=CFG)
    assert rep["exit"] == ex
    # final ratcheted stop == fill price for this filling trade
    trail = rep["trajectory"][rep["trajectory"]["event"] == "TRAIL"]
    assert float(trail["stop"].iloc[-1]) == ex["exit_price"] == 104.0
    assert list(trail["stop"]) == [96.0, 100.0, 104.0]


def test_self_consistent_audit_passes():
    rep = replicate_nt_atr_trail_trajectory(entry_dt=T0, entry_price=100.0, direction=1,
                                            bars=RISING, cfg=CFG)
    audit = _audit_from_trajectory(rep["trajectory"], entry_price=100.0, direction=1)
    out = parity_report(audit, RISING, CFG, entries=_entries(T0, 100.0, 1))
    assert out["passed"] is True
    assert out["n_trades"] == 1
    assert out["overall_stop_match_rate"] == 1.0
    assert out["summary"]["n_events"].iloc[0] == 3


def test_injected_bad_stop_is_caught_at_the_bar():
    rep = replicate_nt_atr_trail_trajectory(entry_dt=T0, entry_price=100.0, direction=1,
                                            bars=RISING, cfg=CFG)
    audit = _audit_from_trajectory(rep["trajectory"], entry_price=100.0, direction=1)
    bad_dt = audit["time"].iloc[1]
    audit.loc[1, "newStop"] = audit.loc[1, "newStop"] + 0.5  # +2 ticks drift on 2nd ratchet
    out = parity_report(audit, RISING, CFG, entries=_entries(T0, 100.0, 1))
    assert out["passed"] is False
    s = out["summary"].iloc[0]
    assert s["stop_match_rate"] == pytest.approx(2 / 3)
    assert s["max_diff_ticks"] == pytest.approx(2.0)
    assert s["first_divergence_dt"] == bad_dt


def test_segments_two_trades():
    # Two long trades at different entry prices -> two trade_ids.
    a = _audit_from_trajectory(
        replicate_nt_atr_trail_trajectory(entry_dt=T0, entry_price=100.0, direction=1,
                                          bars=RISING, cfg=CFG)["trajectory"],
        entry_price=100.0, direction=1)
    b = _audit_from_trajectory(
        replicate_nt_atr_trail_trajectory(entry_dt=T0, entry_price=100.0, direction=1,
                                          bars=RISING, cfg=CFG)["trajectory"],
        entry_price=200.0, direction=1)
    b["time"] = b["time"] + timedelta(hours=2)
    df = parse_stop_audit(pd.concat([a, b], ignore_index=True))
    assert df["trade_id"].nunique() == 2
    assert sorted(df["trade_id"].unique()) == [0, 1]


def test_assign_trade_ids_on_oldstop_zero_boundary():
    rows = [
        {"time": T0 + timedelta(minutes=1), "event": "TRAIL", "dir": "long",
         "entry": 100.0, "oldStop": 0.0, "newStop": 96.0},
        {"time": T0 + timedelta(minutes=2), "event": "TRAIL", "dir": "long",
         "entry": 100.0, "oldStop": 96.0, "newStop": 100.0},
        {"time": T0 + timedelta(minutes=9), "event": "TRAIL", "dir": "long",
         "entry": 100.0, "oldStop": 0.0, "newStop": 90.0},  # oldStop 0 again -> new trade
    ]
    df = assign_trade_ids(pd.DataFrame(rows))
    assert list(df["trade_id"]) == [0, 0, 1]


def test_atr_parity_flags_wrong_smoothing():
    audit = pd.DataFrame([
        {"time": T0 + timedelta(minutes=1), "event": "TRAIL", "atr": 4.0},
        {"time": T0 + timedelta(minutes=2), "event": "TRAIL", "atr": 4.0},
    ])
    bars_match = _bars([(1, 104, 100, 104, 4.0), (2, 108, 104, 108, 4.0)])
    ok = diff_trade_atr(audit, bars_match, rel_tol=0.05)
    assert ok["atr_match_rate"] == 1.0 and ok["median_abs_diff"] == 0.0

    bars_wrong = _bars([(1, 104, 100, 104, 8.0), (2, 108, 104, 108, 8.0)])  # 2x off
    bad = diff_trade_atr(audit, bars_wrong, rel_tol=0.05)
    assert bad["atr_match_rate"] == 0.0


def test_entries_with_non_ratcheting_trade_do_not_shift_anchors():
    # Live-observed 2026-06-12: trades that never ratchet write NO audit rows, so
    # Trades.csv has more entries than the audit has segments. Index pairing shifted
    # every later trade onto the wrong entry (match collapsed to ~0%); anchors must
    # be matched by time+direction instead.
    rep = replicate_nt_atr_trail_trajectory(entry_dt=T0, entry_price=100.0, direction=1,
                                            bars=RISING, cfg=CFG)
    a = _audit_from_trajectory(rep["trajectory"], entry_price=100.0, direction=1)

    later = RISING.copy()
    later["dt"] = later["dt"] + timedelta(hours=4)
    rep_b = replicate_nt_atr_trail_trajectory(entry_dt=T0 + timedelta(hours=4),
                                              entry_price=100.0, direction=1,
                                              bars=later, cfg=CFG)
    b = _audit_from_trajectory(rep_b["trajectory"], entry_price=100.0, direction=1)
    b["entry"] = 100.0

    audit = pd.concat([a, b], ignore_index=True)
    # 3 entries, but the MIDDLE one (a short) never ratcheted -> no audit segment.
    entries = pd.DataFrame([
        {"entry_dt": T0, "entry_price": 100.0, "direction": 1},
        {"entry_dt": T0 + timedelta(hours=2), "entry_price": 150.0, "direction": -1},
        {"entry_dt": T0 + timedelta(hours=4), "entry_price": 100.0, "direction": 1},
    ])
    out = parity_report(audit, pd.concat([RISING, later], ignore_index=True), CFG,
                        entries=entries)
    assert out["passed"] is True, out["summary"]
    assert out["n_trades"] == 2
    assert out["overall_stop_match_rate"] == 1.0
    # The second audit segment anchored on the THIRD entry, not the skipped short.
    assert out["summary"]["entry_dt"].iloc[1] == T0 + timedelta(hours=4)


def test_non_trail_exit_bounds_diff_but_trail_exit_does_not():
    # Live-observed 2026-06-12: NT's daily-risk flatten ("Buy to cover") closes the
    # position while the trail-only replica keeps ratcheting — those post-exit replica
    # events are out of scope. But when NT says the exit WAS the trail stop, replica
    # events after NT's last ratchet must stay divergences (frozen-C#-trail guard).
    rep = replicate_nt_atr_trail_trajectory(entry_dt=T0, entry_price=100.0, direction=1,
                                            bars=RISING, cfg=CFG)
    audit = _audit_from_trajectory(rep["trajectory"], entry_price=100.0, direction=1)
    audit = audit.iloc[:1]  # NT flattened after the first ratchet -> audit ends there

    entries_risk = _entries(T0, 100.0, 1)
    entries_risk["exit_name"] = "Buy to cover"
    out = parity_report(audit, RISING, CFG, entries=entries_risk)
    assert out["passed"] is True
    assert out["summary"]["non_trail_exit"].iloc[0]
    assert out["summary"]["n_events"].iloc[0] == 1  # bounded at NT's last audit event

    entries_trail = _entries(T0, 100.0, 1)
    entries_trail["exit_name"] = "Stop loss"
    out2 = parity_report(audit, RISING, CFG, entries=entries_trail)
    assert out2["passed"] is False  # replica's later ratchets count against NT
    assert not out2["summary"]["non_trail_exit"].iloc[0]
    assert out2["summary"]["n_events"].iloc[0] == 3


def test_replica_silent_when_nt_moved_is_divergence():
    # NT logged a TRAIL the replica never proposed (empty replica trajectory).
    audit = pd.DataFrame([
        {"event": "TRAIL", "time": T0 + timedelta(minutes=1), "newStop": 96.0},
    ])
    empty = pd.DataFrame(columns=["dt", "event", "favorable", "atr", "old_stop", "stop"])
    out = diff_trade_stop_trajectory(audit, empty, tick_size=0.25, stop_tol_ticks=1.0)
    assert out["n_events"] == 1 and out["n_match"] == 0
    assert out["stop_match_rate"] == 0.0
