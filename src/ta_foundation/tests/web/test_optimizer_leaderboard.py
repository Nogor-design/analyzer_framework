"""Tests for the session-scoped leaderboard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_leaderboard import (
    DEFAULT_TOP_N,
    LeaderboardError,
    build_leaderboard,
)


@pytest.fixture
def storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "opt_sessions")
    yield
    opt_session.set_storage_root(None)


def _make_session() -> opt_session.OptimizerSession:
    return opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path="C:/fake/seed.xml",
        instrument="NQ 06-26",
    )


def _write_recipe(
    session: opt_session.OptimizerSession,
    stages: list[dict],
) -> None:
    payload = {
        "recipe_version": 1,
        "recipe_id": "rec_test",
        "recipe_name": "leaderboard_test",
        "strategy_id": "FakeStrategy",
        "base_matrix": [],
        "stages": stages,
    }
    (session.directory / "recipe.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_scored_rows(
    session: opt_session.OptimizerSession,
    stage_id: str,
    rows: list[dict],
    selected_ids: list[str] | None = None,
) -> None:
    stage_dir = session.directory / "parsed_results" / stage_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "scored_rows.json").write_text(json.dumps(rows), encoding="utf-8")
    if selected_ids is not None:
        selected = [
            r for r in rows if str(r.get("candidate_id")) in set(selected_ids)
        ]
        (stage_dir / "selected.json").write_text(json.dumps(selected), encoding="utf-8")


def _write_evaluated(session: opt_session.OptimizerSession, rows: list[dict]) -> None:
    review_dir = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "final_backtest_review"
    )
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "evaluated_candidates.json").write_text(
        json.dumps({"rows": rows}), encoding="utf-8"
    )


def _write_recommendations(
    session: opt_session.OptimizerSession,
    *,
    recommended: list[dict],
    rejected: list[dict] | None = None,
) -> None:
    review_dir = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "final_backtest_review"
    )
    review_dir.mkdir(parents=True, exist_ok=True)
    payload = {"recommendations": recommended, "rejected": rejected or []}
    (review_dir / "recommendations.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _stage_row(
    *,
    stage: str,
    cid: str,
    pf: float,
    net: float,
    dd: float,
    trades: int,
    parent: str | None = None,
    template_id: str = "T_X",
) -> dict:
    return {
        "candidate_id": cid,
        "template_id": template_id,
        "bucket_id": "b_default",
        "parent_candidate_id": parent,
        "optimizer_target": "MaxProfitFactor",
        "profit_factor": pf,
        "total_net_profit": net,
        "max_drawdown": dd,
        "trades": trades,
        "param_StartTimeH": 9,
    }


# ---------------------------------------------------------------------------
# Core behavior
# ---------------------------------------------------------------------------

def test_top_n_ordering_by_profit_factor(storage):
    session = _make_session()
    _write_recipe(session, stages=[
        {"stage_id": "stage_1", "stage_type": "optimizer"},
    ])
    _write_scored_rows(session, "stage_1", rows=[
        _stage_row(stage="stage_1", cid="stage_1__T_X__row01", pf=2.5, net=1000, dd=-100, trades=50),
        _stage_row(stage="stage_1", cid="stage_1__T_X__row02", pf=1.8, net=800,  dd=-200, trades=40),
        _stage_row(stage="stage_1", cid="stage_1__T_X__row03", pf=3.1, net=600,  dd=-50,  trades=30),
    ], selected_ids=["stage_1__T_X__row01"])

    report = build_leaderboard(session, metric="profit_factor", min_trades=1, top_n=2)
    assert [r.candidate_id for r in report.rows] == [
        "stage_1__T_X__row03",  # PF 3.1 wins
        "stage_1__T_X__row01",  # PF 2.5 next
    ]
    assert report.rows[0].metric_value == pytest.approx(3.1)
    assert report.total_scanned == 3
    assert report.total_after_min_trades == 3


def test_min_trades_default_uses_strictest_stage(storage):
    session = _make_session()
    _write_recipe(session, stages=[
        {"stage_id": "stage_1", "stage_type": "optimizer",
         "selection": {"min_trades": 20}},
        {"stage_id": "stage_2", "stage_type": "optimizer",
         "selection": {"min_trades": 50}},
    ])
    _write_scored_rows(session, "stage_1", rows=[
        _stage_row(stage="stage_1", cid="stage_1__T_X__row01", pf=9.9, net=100, dd=-10, trades=10),
        _stage_row(stage="stage_1", cid="stage_1__T_X__row02", pf=2.0, net=500, dd=-50, trades=60),
    ])
    _write_scored_rows(session, "stage_2", rows=[
        _stage_row(stage="stage_2", cid="stage_2__T_X__row01", pf=1.5, net=400, dd=-40, trades=80),
    ])

    report = build_leaderboard(session, metric="profit_factor")
    assert report.default_min_trades == 50
    assert report.applied_min_trades == 50
    cids = {r.candidate_id for r in report.rows}
    assert "stage_1__T_X__row01" not in cids  # 10 trades, gated out
    assert "stage_1__T_X__row02" in cids      # 60 trades, passes
    assert "stage_2__T_X__row01" in cids      # 80 trades, passes


def test_min_trades_override_wins(storage):
    session = _make_session()
    _write_recipe(session, stages=[
        {"stage_id": "stage_1", "stage_type": "optimizer",
         "selection": {"min_trades": 100}},
    ])
    _write_scored_rows(session, "stage_1", rows=[
        _stage_row(stage="stage_1", cid="stage_1__T_X__row01", pf=2.0, net=100, dd=-10, trades=20),
    ])
    report = build_leaderboard(session, metric="profit_factor", min_trades=5)
    assert report.applied_min_trades == 5
    assert len(report.rows) == 1


def test_no_min_trades_in_recipe_defaults_to_one(storage):
    session = _make_session()
    _write_recipe(session, stages=[{"stage_id": "stage_1", "stage_type": "optimizer"}])
    _write_scored_rows(session, "stage_1", rows=[
        _stage_row(stage="stage_1", cid="stage_1__T_X__row01", pf=2.0, net=100, dd=-10, trades=5),
    ])
    report = build_leaderboard(session, metric="profit_factor")
    assert report.default_min_trades == 1
    assert len(report.rows) == 1


# ---------------------------------------------------------------------------
# Mixed stage + finalist rows + beats-finalist flag
# ---------------------------------------------------------------------------

def test_mixed_stage_and_finalist_rows_with_beats_flag(storage):
    session = _make_session()
    _write_recipe(session, stages=[
        {"stage_id": "stage_1", "stage_type": "optimizer"},
        {"stage_id": "final_backtest", "stage_type": "fixed_backtest"},
    ])
    _write_scored_rows(session, "stage_1", rows=[
        # Selected → becomes finalist's parent
        _stage_row(stage="stage_1", cid="stage_1__T_X__row01", pf=2.0, net=1000, dd=-100, trades=50),
        # Unselected — beats the finalist parent's IS PF (2.0)
        _stage_row(stage="stage_1", cid="stage_1__T_X__row02", pf=2.5, net=900,  dd=-80,  trades=40),
        # Unselected — does not beat
        _stage_row(stage="stage_1", cid="stage_1__T_X__row03", pf=1.7, net=600,  dd=-70,  trades=35),
    ], selected_ids=["stage_1__T_X__row01"])
    _write_evaluated(session, rows=[
        {
            "run_id": "F_001",
            "template_id": "final_backtest__F_001",
            "parent_candidate_id": "stage_1__T_X__row01",
            "profit_factor": 1.5,   # OOS degraded
            "total_net_profit": 700,
            "max_drawdown": -120,
            "trades": 48,
            "score": 7.0,
        }
    ])
    _write_recommendations(session, recommended=[{"run_id": "F_001", "rank": 1}])

    report = build_leaderboard(session, metric="profit_factor", min_trades=1)
    by_cid = {r.candidate_id: r for r in report.rows}

    assert "F_001" in by_cid
    assert by_cid["F_001"].status == "finalist_recommended"
    assert by_cid["F_001"].beats_best_finalist is False  # finalists themselves never flag

    # IS baseline = finalist's parent row PF (2.0); row02 (PF 2.5) beats it.
    assert by_cid["stage_1__T_X__row02"].beats_best_finalist is True
    assert by_cid["stage_1__T_X__row01"].beats_best_finalist is False  # equal, not greater
    assert by_cid["stage_1__T_X__row03"].beats_best_finalist is False

    # Best finalist metric reflects OOS finalist row, not the parent.
    assert report.best_finalist_metric == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# Sort direction for max_drawdown_abs (lower is better)
# ---------------------------------------------------------------------------

def test_max_drawdown_abs_sorts_ascending(storage):
    session = _make_session()
    _write_recipe(session, stages=[{"stage_id": "stage_1", "stage_type": "optimizer"}])
    _write_scored_rows(session, "stage_1", rows=[
        _stage_row(stage="stage_1", cid="stage_1__T_X__row01", pf=1.0, net=100, dd=-300, trades=20),
        _stage_row(stage="stage_1", cid="stage_1__T_X__row02", pf=1.0, net=100, dd=-50,  trades=20),
        _stage_row(stage="stage_1", cid="stage_1__T_X__row03", pf=1.0, net=100, dd=-150, trades=20),
    ])
    report = build_leaderboard(session, metric="max_drawdown_abs", min_trades=1)
    assert [r.candidate_id for r in report.rows] == [
        "stage_1__T_X__row02",  # |50|
        "stage_1__T_X__row03",  # |150|
        "stage_1__T_X__row01",  # |300|
    ]


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

def test_missing_scored_rows_for_one_stage_records_note(storage):
    session = _make_session()
    _write_recipe(session, stages=[
        {"stage_id": "stage_1", "stage_type": "optimizer"},
        {"stage_id": "stage_2", "stage_type": "optimizer"},
    ])
    _write_scored_rows(session, "stage_1", rows=[
        _stage_row(stage="stage_1", cid="stage_1__T_X__row01", pf=2.0, net=100, dd=-10, trades=20),
    ])
    # stage_2 dir exists but no scored_rows.json
    (session.directory / "parsed_results" / "stage_2").mkdir(parents=True)

    report = build_leaderboard(session, metric="profit_factor", min_trades=1)
    assert any("stage_2" in note and "no scored_rows" in note for note in report.banner_notes)
    assert len(report.rows) == 1


def test_empty_session_returns_empty_report(storage):
    session = _make_session()
    report = build_leaderboard(session, metric="profit_factor", min_trades=1)
    assert report.rows == []
    assert report.total_scanned == 0
    assert any("No scored rows" in note for note in report.banner_notes)


def test_no_finalists_but_stage_rows_present(storage):
    session = _make_session()
    _write_recipe(session, stages=[{"stage_id": "stage_1", "stage_type": "optimizer"}])
    _write_scored_rows(session, "stage_1", rows=[
        _stage_row(stage="stage_1", cid="stage_1__T_X__row01", pf=2.0, net=100, dd=-10, trades=20),
    ])
    report = build_leaderboard(session, metric="profit_factor", min_trades=1)
    assert len(report.rows) == 1
    assert report.best_finalist_metric is None
    assert all(not r.beats_best_finalist for r in report.rows)
    assert any("No final-backtest review" in n for n in report.banner_notes)


# ---------------------------------------------------------------------------
# Status tagging
# ---------------------------------------------------------------------------

def test_selected_vs_evaluated_status_tags(storage):
    session = _make_session()
    _write_recipe(session, stages=[{"stage_id": "stage_1", "stage_type": "optimizer"}])
    _write_scored_rows(session, "stage_1", rows=[
        _stage_row(stage="stage_1", cid="stage_1__T_X__row01", pf=2.0, net=100, dd=-10, trades=20),
        _stage_row(stage="stage_1", cid="stage_1__T_X__row02", pf=1.5, net=50,  dd=-10, trades=20),
    ], selected_ids=["stage_1__T_X__row01"])

    report = build_leaderboard(session, metric="profit_factor", min_trades=1)
    by_cid = {r.candidate_id: r.status for r in report.rows}
    assert by_cid["stage_1__T_X__row01"] == "selected"
    assert by_cid["stage_1__T_X__row02"] == "evaluated"


def test_finalist_status_reflects_recommendations(storage):
    session = _make_session()
    _write_recipe(session, stages=[{"stage_id": "final_backtest", "stage_type": "fixed_backtest"}])
    _write_evaluated(session, rows=[
        {"run_id": "F_001", "profit_factor": 1.5, "total_net_profit": 100,
         "max_drawdown": -10, "trades": 20},
        {"run_id": "F_002", "profit_factor": 1.0, "total_net_profit": 50,
         "max_drawdown": -20, "trades": 20},
        {"run_id": "F_003", "profit_factor": 1.2, "total_net_profit": 80,
         "max_drawdown": -15, "trades": 20},
    ])
    _write_recommendations(
        session,
        recommended=[{"run_id": "F_001"}],
        rejected=[{"run_id": "F_002", "reason": "DD too high"}],
    )

    report = build_leaderboard(session, metric="profit_factor", min_trades=1)
    by_cid = {r.candidate_id: r.status for r in report.rows}
    assert by_cid["F_001"] == "finalist_recommended"
    assert by_cid["F_002"] == "finalist_rejected"
    assert by_cid["F_003"] == "finalist_evaluated"


# ---------------------------------------------------------------------------
# Invalid input
# ---------------------------------------------------------------------------

def test_invalid_metric_raises(storage):
    session = _make_session()
    with pytest.raises(LeaderboardError):
        build_leaderboard(session, metric="not_a_real_metric")


def test_invalid_top_n_raises(storage):
    session = _make_session()
    with pytest.raises(LeaderboardError):
        build_leaderboard(session, top_n=0)


def test_default_top_n_is_50(storage):
    session = _make_session()
    _write_recipe(session, stages=[{"stage_id": "stage_1", "stage_type": "optimizer"}])
    _write_scored_rows(session, "stage_1", rows=[
        _stage_row(stage="stage_1", cid=f"stage_1__T_X__row{i:02d}",
                   pf=1.0 + i * 0.01, net=100, dd=-10, trades=20)
        for i in range(60)
    ])
    report = build_leaderboard(session, metric="profit_factor", min_trades=1)
    assert report.top_n == DEFAULT_TOP_N
    assert len(report.rows) == DEFAULT_TOP_N
