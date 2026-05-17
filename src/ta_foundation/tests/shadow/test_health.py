"""Phase D.2 — daily shadow-health aggregator + markdown emitter tests.

The contract under test:

    1. ``compute_daily_health`` buckets signals by Denver session day on
       fire-time and resolve-time correctly, and surfaces the right
       aggregate counts.
    2. Trailing PF / win rate / expectancy are computed over the most
       recent N resolved trades by exit timestamp.
    3. ``render_health_markdown`` only renders numbers present in the
       computed report — there is no prose interpolation. This makes the
       artifact trivially safe to feed the numerical-claim linter (B.3).
    4. Anomaly detection flags long-stale open positions.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ta_foundation.research_ledger import get_repository
from ta_foundation.shadow import (
    compute_daily_health,
    render_health_markdown,
)


def _register_candidate(repo, *, candidate_id: str) -> str:
    repo.register_hypothesis(
        hypothesis_id=f"h_{candidate_id}",
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
        run_id=f"r_{candidate_id}",
        hypothesis_id=f"h_{candidate_id}",
        mode="hardened",
        config_hash="x",
        yaml_path="x",
        artifact_dir="x",
    )
    repo.record_candidate(
        candidate_id=candidate_id,
        run_id=f"r_{candidate_id}",
        rank_in_run=1,
        params={"orb_minutes": 5, "stop_ticks": 4, "target_ticks": 4},
        gate_verdict="survivor",
    )
    repo.set_triage(
        candidate_id=candidate_id,
        state="shadow",
        reason="enrolled for forward observation test fixture",
        triaged_by="test",
    )
    return candidate_id


def _insert_signal(
    repo,
    *,
    candidate_id: str,
    ts_utc: str,
    direction: str,
    payload: dict,
) -> int:
    new_id = repo.insert_shadow_signal_if_absent(
        candidate_id=candidate_id,
        ts=ts_utc,
        instrument="NQ",
        direction=direction,
        planned_entry=100.0,
        planned_stop=99.0,
        planned_target=104.0,
        realized_outcome=payload,
    )
    assert new_id is not None
    return new_id


def test_daily_health_buckets_by_denver_day(tmp_path: Path) -> None:
    repo = get_repository(tmp_path / "ledger.db")
    cid = _register_candidate(repo, candidate_id="c_h_01")

    # 2026-04-15 Denver covers 06:00 UTC 2026-04-15 .. 06:00 UTC 2026-04-16.
    # Fire one signal that day with resolution same day.
    _insert_signal(
        repo,
        candidate_id=cid,
        ts_utc="2026-04-15T13:35:00Z",  # 07:35 Denver
        direction="short",
        payload={
            "status": "resolved",
            "result": "win",
            "exit_ts_utc": "2026-04-15T13:45:00Z",
            "profit_net": 150.0,
            "profit_ticks": 4.0,
        },
    )
    # Another fired on 2026-04-15 Denver but unresolved (open).
    _insert_signal(
        repo,
        candidate_id=cid,
        ts_utc="2026-04-15T20:00:00Z",  # 14:00 Denver
        direction="long",
        payload={"status": "open", "fill_ts_utc": "2026-04-15T20:05:00Z"},
    )
    # And a no_fill on the previous Denver day.
    _insert_signal(
        repo,
        candidate_id=cid,
        ts_utc="2026-04-14T16:30:00Z",
        direction="short",
        payload={"status": "no_fill", "fill_timeout_bars": 5},
    )

    report = compute_daily_health(
        repo,
        for_date="2026-04-15",
        trailing_window=10,
        open_position_age_warn_hours=24 * 365,  # disable for this test
    )

    assert report.report_date == "2026-04-15"
    assert report.candidates_active == 1
    assert report.total_signals_today == 2
    assert report.total_resolved_today == 1
    assert report.total_no_fill_today == 0  # the no_fill is on the prior day
    assert report.total_open_positions == 1
    assert report.total_net_pnl_today == pytest.approx(150.0)

    c = report.candidates[0]
    assert c.candidate_id == cid
    assert c.family == "orb_failure_reclaim"
    assert c.n_signals_today == 2
    assert c.n_resolved_today == 1
    assert c.n_open_positions == 1
    assert c.net_pnl_today == pytest.approx(150.0)


def test_trailing_pf_uses_latest_resolved_window(tmp_path: Path) -> None:
    repo = get_repository(tmp_path / "ledger.db")
    cid = _register_candidate(repo, candidate_id="c_h_02")

    # Five resolved trades. The latest 3 by exit_ts have nets (+200, +100, -50)
    # → gross_profit=300, gross_loss=50, pf=6.0, win_rate=2/3.
    trades = [
        ("2026-04-10T14:00:00Z", "loss", -300.0),  # older — should fall outside window=3
        ("2026-04-11T14:00:00Z", "win", +50.0),     # older — outside window=3
        ("2026-04-12T14:00:00Z", "loss", -50.0),    # in window
        ("2026-04-13T14:00:00Z", "win", +100.0),    # in window
        ("2026-04-14T14:00:00Z", "win", +200.0),    # in window
    ]
    for i, (exit_ts, result, pnl) in enumerate(trades):
        _insert_signal(
            repo,
            candidate_id=cid,
            ts_utc=exit_ts.replace("T14:00", "T13:30"),
            direction="long" if pnl >= 0 else "short",
            payload={
                "status": "resolved",
                "result": result,
                "exit_ts_utc": exit_ts,
                "profit_net": pnl,
            },
        )

    report = compute_daily_health(
        repo,
        for_date="2026-04-15",
        trailing_window=3,
        open_position_age_warn_hours=24 * 365,
    )
    c = report.candidates[0]
    assert c.trailing_window_n == 3
    assert c.trailing_pf == pytest.approx(6.0)
    assert c.trailing_win_rate == pytest.approx(2 / 3, abs=1e-3)
    assert c.trailing_expectancy_net == pytest.approx((200 + 100 - 50) / 3, abs=0.01)


def test_stale_open_position_flagged(tmp_path: Path) -> None:
    repo = get_repository(tmp_path / "ledger.db")
    cid = _register_candidate(repo, candidate_id="c_h_03")
    _insert_signal(
        repo,
        candidate_id=cid,
        ts_utc="2000-01-01T00:00:00Z",  # very stale
        direction="short",
        payload={"status": "open", "fill_ts_utc": "2000-01-01T00:05:00Z"},
    )
    report = compute_daily_health(
        repo,
        for_date="2026-04-15",
        trailing_window=10,
        open_position_age_warn_hours=1,
    )
    assert any(a.code == "stale_open_position" for a in report.anomalies)


def test_health_markdown_renders_summary_and_candidate_rows(tmp_path: Path) -> None:
    repo = get_repository(tmp_path / "ledger.db")
    cid = _register_candidate(repo, candidate_id="c_h_md_01")
    _insert_signal(
        repo,
        candidate_id=cid,
        ts_utc="2026-04-15T13:35:00Z",
        direction="short",
        payload={
            "status": "resolved",
            "result": "win",
            "exit_ts_utc": "2026-04-15T13:45:00Z",
            "profit_net": 150.0,
        },
    )
    report = compute_daily_health(
        repo,
        for_date="2026-04-15",
        trailing_window=10,
        open_position_age_warn_hours=24 * 365,
    )
    body = render_health_markdown(report)

    assert body.startswith("# Shadow Health — 2026-04-15")
    assert "total_signals_today | 1" in body
    assert "total_resolved_today | 1" in body
    assert "$150.00" in body  # rendered net pnl
    assert cid in body
    # No anomalies for this fixture — section should still render.
    assert "## Anomalies" in body
    assert "_None._" in body


def test_render_no_candidates_is_empty_safe(tmp_path: Path) -> None:
    repo = get_repository(tmp_path / "ledger.db")
    report = compute_daily_health(repo, for_date="2026-04-15", trailing_window=10)
    body = render_health_markdown(report)
    assert "Shadow Health — 2026-04-15" in body
    assert "_No candidates currently enrolled in shadow._" in body
    assert "_None._" in body
