"""Phase D.1 — Shadow runner unit tests.

These tests assert the three contracts the design doc commits to:

    1. A shadow-enrolled candidate, given a synthetic bar window that
       contains a valid ORB failure/reclaim setup, produces the expected
       number of shadow_signals rows.
    2. Re-running the pass over identical data is a no-op: the unique
       index prevents duplicate inserts and the cursor advances exactly
       once.
    3. An open position resolves (stop or target) once forward bars
       reach the stop/target/timeout window — the realized_outcome_json
       payload transitions pending → open → resolved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from ta_foundation.research_ledger import get_repository
from ta_foundation.shadow import InMemoryBarsDataSource, run_shadow_pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_failure_reclaim_bars() -> pd.DataFrame:
    """Build a NY-open 5-minute ORB failure/reclaim setup at 7:30 Denver.

    Layout (Denver-local naive timestamps, 1m bars):
      07:30..07:34  OR window. high = 102.50, low = 100.00 (10 ticks).
      07:35         Sweep+reclaim bar. high=103.50 (4 ticks above OR high),
                    open=103.00, close=102.00 (closes back inside). Direction
                    = short. Body midpoint = (103.00 + 102.00)/2 = 102.50.
      07:36..       Fill window — bar 07:36 high reaches 102.50 → fill at
                    102.50. Stop = 102.50 + 4*0.25 = 103.50. Target = 102.50
                    - 4*0.25 = 101.50. Forward bars drop straight to the
                    target on 07:38.
    """
    bars = []
    base = pd.Timestamp("2026-04-15 07:30:00", tz="America/Denver")
    # OR window
    bars.append((base + pd.Timedelta(minutes=0), 100.00, 100.50, 100.00, 100.50, 100))
    bars.append((base + pd.Timedelta(minutes=1), 100.50, 101.50, 100.50, 101.00, 100))
    bars.append((base + pd.Timedelta(minutes=2), 101.00, 102.50, 101.00, 102.00, 100))
    bars.append((base + pd.Timedelta(minutes=3), 102.00, 102.50, 101.50, 102.50, 100))
    bars.append((base + pd.Timedelta(minutes=4), 102.50, 102.50, 102.00, 102.25, 100))
    # Sweep + reclaim
    bars.append((base + pd.Timedelta(minutes=5), 103.00, 103.50, 102.00, 102.00, 250))
    # Fill bar — high revisits 102.50
    bars.append((base + pd.Timedelta(minutes=6), 102.00, 102.50, 101.80, 102.20, 150))
    # Target hit shortly after
    bars.append((base + pd.Timedelta(minutes=7), 102.20, 102.30, 101.40, 101.50, 200))
    bars.append((base + pd.Timedelta(minutes=8), 101.50, 101.60, 101.20, 101.30, 200))

    df = pd.DataFrame(
        bars, columns=["dt", "open", "high", "low", "close", "volume"]
    )
    return df


def _make_shadow_candidate(repo, *, candidate_id="c_test_orb_fr_01"):
    repo.register_hypothesis(
        hypothesis_id="h_test_orb_fr_01",
        family="orb_failure_reclaim",
        instrument="NQ",
        timeframe="5m",
        session_window="ny_open_730",
        direction="both",
        params={
            "orb_minutes": 5,
            "sweep_min_ticks": 4,
            "reclaim_within_bars": 1,
            "fill_mode": "body_midpoint",
            "stop_ticks": 4,
            "target_ticks": 4,
        },
        mechanism=(
            "Opening-range sweep + reclaim traps breakout chasers; the body-"
            "midpoint retrace lets the fade enter where the trapped longs "
            "have to exit, providing the directional impulse."
        ),
        registered_by="human:test",
    )
    repo.start_run(
        run_id="r_test_orb_fr_01",
        hypothesis_id="h_test_orb_fr_01",
        mode="hardened",
        config_hash="test_config_hash",
        yaml_path="discovery/_test.yaml",
        artifact_dir=".ta_artifacts/_test",
    )
    repo.record_candidate(
        candidate_id=candidate_id,
        run_id="r_test_orb_fr_01",
        rank_in_run=1,
        params={
            "orb_minutes": 5,
            "sweep_min_ticks": 4,
            "reclaim_within_bars": 1,
            "fill_mode": "body_midpoint",
            "stop_ticks": 4,
            "target_ticks": 4,
        },
        gate_verdict="survivor",
        n_trades_dev=100,
        pf_dev=3.0,
        n_trades_holdout=30,
        pf_holdout=1.5,
        notes={
            "discovery_family": "orb",
            "session_filter": {
                "session_open_hour": 7,
                "session_open_minute": 30,
                "session_close_hour": 10,
            },
            "entry_timing": {"body_midpoint": {"enabled": True, "fill_timeout_bars": 5}},
            "outcome": {
                "take_profit": [4],
                "stop": [4],
                "max_bars_timeout": 10,
                "timeout_result": "at_close",
                "tick_size": 0.25,
                "tick_value": 5.0,
                "commission_per_side": 0.0,
                "slippage_ticks": 0,
            },
            "discovery_params": {
                "orb_minutes": 5,
                "min_range_ticks": 8,
                "min_sweep_ticks": 4.0,
                "max_reclaim_bars": 1,
                "require_close_beyond": True,
                "tick_size": 0.25,
                "direction": 0,
            },
        },
    )
    repo.set_triage(
        candidate_id=candidate_id,
        state="shadow",
        reason="enrolled for forward observation test fixture",
        triaged_by="test",
    )
    return candidate_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_shadow_pass_inserts_signal(tmp_path: Path) -> None:
    repo = get_repository(tmp_path / "ledger.db")
    cid = _make_shadow_candidate(repo)
    bars = _build_failure_reclaim_bars()
    loader = InMemoryBarsDataSource({"NQ": bars})

    report = run_shadow_pass(repo=repo, bars_loader=loader)

    assert report.n_candidates == 1
    cr = report.candidates[0]
    assert cr.error is None, cr.error
    assert cr.skipped_reason is None, cr.skipped_reason
    assert cr.signals_inserted >= 1, f"expected at least one signal, got {cr}"

    signals = repo.list_shadow_signals(candidate_id=cid)
    assert len(signals) >= 1
    s = signals[0]
    assert s.instrument == "NQ"
    assert s.direction == "short"
    assert s.planned_entry == pytest.approx(102.50)
    assert s.planned_stop == pytest.approx(102.50 + 4 * 0.25)
    assert s.planned_target == pytest.approx(102.50 - 4 * 0.25)


def test_shadow_pass_is_idempotent(tmp_path: Path) -> None:
    repo = get_repository(tmp_path / "ledger.db")
    cid = _make_shadow_candidate(repo)
    bars = _build_failure_reclaim_bars()
    loader = InMemoryBarsDataSource({"NQ": bars})

    first = run_shadow_pass(repo=repo, bars_loader=loader)
    n_first = first.total_signals_inserted
    assert n_first >= 1
    n_stored_first = len(repo.list_shadow_signals(candidate_id=cid))
    assert n_stored_first == n_first

    # Cursor advanced — a second pass over the same source must not
    # duplicate the previously-stored rows. The unique index is the
    # ultimate guarantee; we assert the total count is unchanged.
    second = run_shadow_pass(repo=repo, bars_loader=loader)
    assert second.total_signals_inserted == 0
    n_stored_second = len(repo.list_shadow_signals(candidate_id=cid))
    assert n_stored_second == n_stored_first


def test_unique_index_blocks_duplicate_insert(tmp_path: Path) -> None:
    """Repository-level idempotency: INSERT OR IGNORE must reject duplicates
    on (candidate_id, ts, direction). This is what protects the runner if a
    bar happens to be re-presented across cursor windows."""
    repo = get_repository(tmp_path / "ledger.db")
    cid = _make_shadow_candidate(repo)
    first = repo.insert_shadow_signal_if_absent(
        candidate_id=cid,
        ts="2026-04-15T13:35:00Z",
        instrument="NQ",
        direction="short",
        planned_entry=102.5,
        planned_stop=103.5,
        planned_target=101.5,
        realized_outcome={"status": "pending"},
    )
    second = repo.insert_shadow_signal_if_absent(
        candidate_id=cid,
        ts="2026-04-15T13:35:00Z",
        instrument="NQ",
        direction="short",
        planned_entry=102.5,
        planned_stop=103.5,
        planned_target=101.5,
        realized_outcome={"status": "pending"},
    )
    assert first is not None and first > 0
    assert second is None
    assert len(repo.list_shadow_signals(candidate_id=cid)) == 1


def test_open_signal_resolves_to_target(tmp_path: Path) -> None:
    repo = get_repository(tmp_path / "ledger.db")
    cid = _make_shadow_candidate(repo)
    bars = _build_failure_reclaim_bars()
    loader = InMemoryBarsDataSource({"NQ": bars})

    run_shadow_pass(repo=repo, bars_loader=loader)
    signals = repo.list_shadow_signals(candidate_id=cid)
    assert signals, "expected at least one signal after first pass"
    s = signals[0]
    payload = json.loads(s.realized_outcome_json)
    # After a single pass over the full bar window, the signal should be
    # filled and resolved (target hit on 07:38, well within the synthetic
    # 10-bar timeout window).
    assert payload["status"] == "resolved", payload
    assert payload["result"] == "win"
    assert payload["exit_price"] == pytest.approx(102.50 - 4 * 0.25)


def test_runner_auto_disables_decayed_candidate(tmp_path: Path) -> None:
    """End-to-end Phase D.3: a shadow candidate accumulating resolved
    trades with strongly negative ``profit_ticks`` is auto-disabled by
    the runner. ``triage_state`` transitions to ``'decayed'`` and a
    ``decay_disable`` journal entry is written.

    We bypass signal generation by directly inserting ``shadow_signals``
    rows with a ``resolved`` payload — the goal is to assert the
    runner-side wiring, not the simulator's ability to produce losses.
    """
    repo = get_repository(tmp_path / "ledger.db")
    cid = _make_shadow_candidate(repo)

    # Preload 25 resolved losing trades. With tp=4, sl=4 and pf_dev=3.0
    # the implied σ is small (~4 ticks), so a string of -4 tick losses
    # quickly clears H=5σ. Real Phase D series are much longer; we keep
    # this tight so the test is fast and unambiguous.
    base_ts = pd.Timestamp("2026-04-15 13:35:00", tz="UTC")
    for i in range(25):
        ts = (base_ts + pd.Timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        sig_id = repo.insert_shadow_signal_if_absent(
            candidate_id=cid,
            ts=ts,
            instrument="NQ",
            direction="short",
            planned_entry=100.0,
            planned_stop=101.0,
            planned_target=99.0,
            realized_outcome={
                "status": "resolved",
                "result": "loss",
                "profit_ticks": -4.0,
                "profit_net": -20.0,
            },
        )
        assert sig_id is not None

    # Empty bars source — we only want the decay update path to execute,
    # not signal generation. The runner gracefully handles the empty
    # window.
    loader = InMemoryBarsDataSource({"NQ": pd.DataFrame(
        columns=["dt", "open", "high", "low", "close", "volume"]
    )})

    report = run_shadow_pass(repo=repo, bars_loader=loader)

    assert report.n_candidates == 1
    cr = report.candidates[0]
    assert cr.error is None, cr.error
    assert cr.decay_trades_consumed == 25, cr
    assert cr.decay_triggered is True, cr
    assert report.total_decay_triggered == 1

    # Candidate is now graveyarded as decayed.
    refreshed = repo.get_candidate(cid)
    assert refreshed.triage_state == "decayed", refreshed.triage_state
    assert refreshed.triaged_by == "agent:shadow_decay"
    assert "Phase D.3" in (refreshed.triage_reason or "")

    # Decay state is persisted and shows triggered=True.
    state = repo.get_decay_state(cid)
    assert state is not None
    assert state["triggered"] is True
    assert state["last_signal_id"] is not None

    # Auto-disable journal entry is recorded as a distinct row.
    journal = repo.list_journal(tool_name="decay_disable", limit=10)
    assert len(journal) == 1
    assert json.loads(journal[0].inputs_json)["candidate_id"] == cid


def test_runner_decay_update_is_idempotent_across_passes(tmp_path: Path) -> None:
    """Re-running the shadow pass after decay has triggered must not
    journal the auto-disable a second time, and the CUSUM watermark
    keeps advancing past already-consumed signals without double-counting.
    """
    repo = get_repository(tmp_path / "ledger.db")
    cid = _make_shadow_candidate(repo)
    base_ts = pd.Timestamp("2026-04-15 13:35:00", tz="UTC")
    for i in range(25):
        ts = (base_ts + pd.Timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        repo.insert_shadow_signal_if_absent(
            candidate_id=cid, ts=ts, instrument="NQ", direction="short",
            planned_entry=100.0, planned_stop=101.0, planned_target=99.0,
            realized_outcome={
                "status": "resolved", "result": "loss",
                "profit_ticks": -4.0, "profit_net": -20.0,
            },
        )

    loader = InMemoryBarsDataSource({"NQ": pd.DataFrame(
        columns=["dt", "open", "high", "low", "close", "volume"]
    )})

    first = run_shadow_pass(repo=repo, bars_loader=loader)
    assert first.candidates[0].decay_triggered is True

    # Once the candidate is in 'decayed' it is no longer picked up by
    # the runner's shadow filter — the second pass should report zero
    # candidates and zero new journal rows.
    second = run_shadow_pass(repo=repo, bars_loader=loader)
    assert second.n_candidates == 0
    assert second.total_decay_triggered == 0

    journal = repo.list_journal(tool_name="decay_disable", limit=10)
    assert len(journal) == 1, "decay_disable should be journaled exactly once"


def test_unsupported_family_is_skipped_cleanly(tmp_path: Path) -> None:
    repo = get_repository(tmp_path / "ledger.db")
    repo.register_hypothesis(
        hypothesis_id="h_unsupported_01",
        family="vwap_reject_fade",
        instrument="NQ",
        timeframe="1m",
        session_window=None,
        direction="long",
        params={"min_distance_ticks": 4, "stop_ticks": 8, "target_ticks": 24},
        mechanism=(
            "Trapped breakout participants fade back through VWAP after a "
            "momentum probe fails to hold above the developing volume-weighted "
            "reference."
        ),
        registered_by="human:test",
    )
    repo.start_run(
        run_id="r_unsupported_01",
        hypothesis_id="h_unsupported_01",
        mode="hardened",
        config_hash="x",
        yaml_path="x",
        artifact_dir="x",
    )
    repo.record_candidate(
        candidate_id="c_unsupported_01",
        run_id="r_unsupported_01",
        rank_in_run=1,
        params={"min_distance_ticks": 4, "stop_ticks": 8, "target_ticks": 24},
        gate_verdict="survivor",
    )
    repo.set_triage(
        candidate_id="c_unsupported_01",
        state="shadow",
        reason="enrolled for shadow test of unsupported-family path",
        triaged_by="test",
    )

    loader = InMemoryBarsDataSource({"NQ": _build_failure_reclaim_bars()})
    report = run_shadow_pass(repo=repo, bars_loader=loader)

    assert report.n_candidates == 1
    cr = report.candidates[0]
    assert cr.skipped_reason is not None
    assert "family_not_supported" in cr.skipped_reason
    assert cr.error is None
    assert repo.list_shadow_signals(candidate_id="c_unsupported_01") == []
