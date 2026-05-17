from __future__ import annotations

"""Tests for the consolidated decision-dashboard view-model assembler."""

import json
from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_decision_dashboard import (
    CheckSummary,
    SEVERITY_FAIL,
    SEVERITY_NONE,
    SEVERITY_OK,
    SEVERITY_WARN,
    _compute_adjusted_score,
    build_decision_dashboard,
)


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "sessions")
    yield
    opt_session.set_storage_root(None)


def _make_session(tmp_path: Path):
    session = opt_session.create_session(
        label="dashboard test",
        strategy_id="FakeStrategy",
        seed_template_path=str(tmp_path / "seed.xml"),
        instrument="NQ 06-26",
    )
    session.update(oos_from_date="2026-04-14", oos_to_date="2026-05-14")
    return session


def _write_review(session, *, rows: list[dict], recommendations: list[dict], rejected: list[dict]):
    review_dir = session.directory / "deployment_package" / "final_backtest_handoff" / "final_backtest_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "evaluated_candidates.json").write_text(json.dumps({
        "schema_version": 1, "config": {}, "candidate_count": len(rows), "rows": rows,
    }), encoding="utf-8")
    (review_dir / "recommendations.json").write_text(json.dumps({
        "schema_version": 1, "config": {},
        "recommended_count": len(recommendations), "candidate_count": len(rows),
        "recommendations": recommendations, "rejected": rejected,
    }), encoding="utf-8")


def _write_manifest(session, decision_state: str):
    pkg = session.directory / "deployment_package"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "manifest.json").write_text(json.dumps({
        "decision_state": decision_state,
    }), encoding="utf-8")


def test_no_review_returns_empty_status(tmp_path: Path):
    session = _make_session(tmp_path)
    dash = build_decision_dashboard(session)
    assert dash.status == "no_review"
    assert dash.candidate_rows == []
    assert "run the final fixed Backtest phase" in dash.status_reason


def test_basic_dashboard_ranks_recommended_first(tmp_path: Path):
    session = _make_session(tmp_path)
    _write_manifest(session, "candidate_ready_for_operator_review")
    _write_review(session,
        rows=[
            {"run_id": "F_001", "status": "pass", "score": 90.0, "mode": "breakout",
             "session_bucket": "Pre-Market", "total_net_profit": 19880.0,
             "profit_factor": 5.01, "max_drawdown": -1500.0, "trades": 17,
             "percent_days_traded": 54.84, "direction": "long",
             "risk_shape": "wide_stop_high_rr"},
            {"run_id": "F_002", "status": "pass", "score": 60.0, "mode": "breakout",
             "session_bucket": "London Late", "total_net_profit": 4760.0,
             "profit_factor": 7.8, "max_drawdown": -275.0, "trades": 16,
             "percent_days_traded": 51.61, "direction": "long",
             "risk_shape": "wide_stop_low_rr"},
            {"run_id": "F_003", "status": "reject", "score": 30.0, "mode": "breakout",
             "session_bucket": "London Early", "total_net_profit": 1000.0,
             "profit_factor": 1.1, "max_drawdown": -800.0, "trades": 8,
             "percent_days_traded": 30.0, "direction": "long",
             "risk_shape": "narrow_stop"},
        ],
        recommendations=[
            {"run_id": "F_001", "rank": 1, "reason": "best for Pre-Market"},
            {"run_id": "F_002", "rank": 2, "reason": "best for London Late"},
        ],
        rejected=[
            {"run_id": "F_003", "reason": "too few trades"},
        ],
    )

    dash = build_decision_dashboard(session)
    assert dash.status == "ok"
    assert dash.decision_state == "candidate_ready_for_operator_review"
    assert dash.recommended_count == 2
    assert dash.rejected_count == 1
    # First two rows are the recommended ones in rank order.
    assert [r.run_id for r in dash.candidate_rows[:2]] == ["F_001", "F_002"]
    assert dash.candidate_rows[0].status == "recommended"
    assert dash.candidate_rows[0].rank == 1
    assert dash.candidate_rows[2].status == "rejected"
    # All four checks are present per row, each marked SEVERITY_NONE because
    # no per-check JSON was written.
    for row in dash.candidate_rows:
        check_names = [c.name for c in row.checks]
        assert check_names == ["bootstrap", "walkforward", "neighborhood", "shadow"]
        assert all(c.severity == SEVERITY_NONE for c in row.checks)


def test_adjusted_score_penalizes_fail_more_than_warn():
    warn_adjusted = _compute_adjusted_score(
        100.0,
        [CheckSummary(name="bootstrap", severity=SEVERITY_WARN, headline="warn")],
    )
    fail_adjusted = _compute_adjusted_score(
        100.0,
        [CheckSummary(name="walkforward", severity=SEVERITY_FAIL, headline="fail")],
    )

    assert warn_adjusted == 90.0
    assert fail_adjusted == 70.0
    assert fail_adjusted < warn_adjusted


def test_adjusted_score_none_severity_has_no_penalty():
    adjusted = _compute_adjusted_score(
        42.0,
        [
            CheckSummary(name="bootstrap", severity=SEVERITY_NONE, headline="not run"),
            CheckSummary(name="walkforward", severity=SEVERITY_OK, headline="ok"),
        ],
    )

    assert adjusted == 42.0


def test_adjusted_rank_is_unique_one_indexed_and_sorted_by_adjusted_score(tmp_path: Path):
    session = _make_session(tmp_path)
    _write_review(session,
        rows=[
            {"run_id": "F_001", "status": "pass", "score": 90.0, "trades": 20},
            {"run_id": "F_002", "status": "pass", "score": 80.0, "trades": 20},
            {"run_id": "F_003", "status": "pass", "score": 70.0, "trades": 5},
        ],
        recommendations=[
            {"run_id": "F_001", "rank": 1, "reason": "highest raw score"},
            {"run_id": "F_002", "rank": 2, "reason": "cleaner checks"},
            {"run_id": "F_003", "rank": 3, "reason": "warning only"},
        ],
        rejected=[],
    )
    pkg = session.directory / "deployment_package"
    (pkg / "walkforward").mkdir()
    (pkg / "walkforward" / "stability.json").write_text(json.dumps({
        "candidate_stability": [{
            "candidate_run_id": "F_001",
            "windows_run": 3,
            "windows_with_pf_above_1": 0,
            "pf_median": 0.0,
            "stability_flags": ["PF median collapsed"],
        }],
    }), encoding="utf-8")
    (pkg / "shadow").mkdir()
    (pkg / "shadow" / "comparison.json").write_text(json.dumps({
        "comparisons": [{
            "candidate_run_id": "F_001",
            "backtest_trades": 20,
            "shadow_trades": 0,
            "divergence_flags": ["shadow window produced zero trades"],
        }],
    }), encoding="utf-8")
    (pkg / "robustness").mkdir()
    (pkg / "robustness" / "robustness.json").write_text(json.dumps({
        "bootstrap_results": [{
            "run_id": "F_003",
            "trade_count": 5,
            "profit_factor": {"observed": 2.0, "p_at_or_above_observed": 0.5},
        }],
    }), encoding="utf-8")

    dash = build_decision_dashboard(session)

    assert [row.run_id for row in dash.candidate_rows] == ["F_002", "F_003", "F_001"]
    assert [row.adjusted_rank for row in dash.candidate_rows] == [1, 2, 3]
    assert len({row.adjusted_rank for row in dash.candidate_rows}) == 3
    assert [row.adjusted_score for row in dash.candidate_rows] == [80.0, 60.0, 30.0]
    assert dash.candidate_rows[0].to_dict()["adjusted_rank"] == 1
    assert dash.candidate_rows[0].to_dict()["adjusted_score"] == 80.0


def test_score_none_gets_adjusted_score_from_penalties(tmp_path: Path):
    session = _make_session(tmp_path)
    _write_review(session,
        rows=[{"run_id": "F_001", "status": "pass", "score": None, "trades": 5}],
        recommendations=[],
        rejected=[],
    )
    pkg = session.directory / "deployment_package"
    (pkg / "robustness").mkdir()
    (pkg / "robustness" / "robustness.json").write_text(json.dumps({
        "bootstrap_results": [{
            "run_id": "F_001",
            "trade_count": 5,
            "profit_factor": {"observed": 2.0, "p_at_or_above_observed": 0.5},
        }],
    }), encoding="utf-8")
    (pkg / "walkforward").mkdir()
    (pkg / "walkforward" / "stability.json").write_text(json.dumps({
        "candidate_stability": [{
            "candidate_run_id": "F_001",
            "windows_run": 1,
            "windows_with_pf_above_1": 0,
            "pf_median": 0.0,
            "stability_flags": ["PF median collapsed"],
        }],
    }), encoding="utf-8")

    dash = build_decision_dashboard(session)

    assert dash.candidate_rows[0].score is None
    assert dash.candidate_rows[0].adjusted_score == -40.0
    assert dash.candidate_rows[0].adjusted_rank == 1


def test_bootstrap_severity_low_trade_count(tmp_path: Path):
    session = _make_session(tmp_path)
    _write_review(session, rows=[{"run_id": "F_001", "status": "pass", "score": 50.0,
                                  "trades": 5}], recommendations=[], rejected=[])
    rob = session.directory / "deployment_package" / "robustness"
    rob.mkdir(parents=True)
    (rob / "robustness.json").write_text(json.dumps({
        "bootstrap_results": [{
            "run_id": "F_001", "trade_count": 5,
            "profit_factor": {"observed": 2.5, "p_at_or_above_observed": 0.45},
        }],
    }), encoding="utf-8")
    dash = build_decision_dashboard(session)
    check = next(c for c in dash.candidate_rows[0].checks if c.name == "bootstrap")
    assert check.severity == SEVERITY_WARN
    assert any("only 5 trades" in f for f in check.flags)
    assert "2.50" in check.headline


def test_bootstrap_severity_extreme_p_value(tmp_path: Path):
    session = _make_session(tmp_path)
    _write_review(session, rows=[{"run_id": "F_001", "status": "pass", "score": 50.0,
                                  "trades": 50}], recommendations=[], rejected=[])
    rob = session.directory / "deployment_package" / "robustness"
    rob.mkdir(parents=True)
    (rob / "robustness.json").write_text(json.dumps({
        "bootstrap_results": [{
            "run_id": "F_001", "trade_count": 50,
            "profit_factor": {"observed": 5.0, "p_at_or_above_observed": 0.97},
        }],
    }), encoding="utf-8")
    dash = build_decision_dashboard(session)
    check = next(c for c in dash.candidate_rows[0].checks if c.name == "bootstrap")
    assert check.severity == SEVERITY_WARN
    assert any("ordering artifact" in f for f in check.flags)


def test_walkforward_severity_collapse_is_fail(tmp_path: Path):
    session = _make_session(tmp_path)
    _write_review(session, rows=[{"run_id": "F_001", "status": "pass", "score": 50.0,
                                  "trades": 17}], recommendations=[], rejected=[])
    wf = session.directory / "deployment_package" / "walkforward"
    wf.mkdir(parents=True)
    (wf / "stability.json").write_text(json.dumps({
        "candidate_stability": [{
            "candidate_run_id": "F_001",
            "windows_run": 3, "windows_with_pf_above_1": 0,
            "pf_median": 0.0,
            "stability_flags": ["PF median collapsed (reference 5.01 → median 0.00)"],
        }],
    }), encoding="utf-8")
    dash = build_decision_dashboard(session)
    check = next(c for c in dash.candidate_rows[0].checks if c.name == "walkforward")
    assert check.severity == SEVERITY_FAIL
    assert "0/3 PF>1" in check.headline
    assert any("collapsed" in f for f in check.flags)


def test_neighborhood_severity_needle_peak(tmp_path: Path):
    session = _make_session(tmp_path)
    _write_review(session, rows=[{"run_id": "F_001", "status": "pass", "score": 50.0,
                                  "trades": 17}], recommendations=[], rejected=[])
    nb = session.directory / "deployment_package" / "neighborhood"
    nb.mkdir(parents=True)
    (nb / "stability.json").write_text(json.dumps({
        "candidate_stability": [{
            "candidate_run_id": "F_001",
            "cells_run": 4, "cells_with_pf_above_1": 2,
            "pf_median": 0.85,
            "stability_flags": ["needle peak: center PF 5.01 → neighborhood median 0.85"],
        }],
    }), encoding="utf-8")
    dash = build_decision_dashboard(session)
    check = next(c for c in dash.candidate_rows[0].checks if c.name == "neighborhood")
    assert check.severity == SEVERITY_FAIL
    assert any("needle peak" in f for f in check.flags)


def test_shadow_zero_trades_is_fail(tmp_path: Path):
    session = _make_session(tmp_path)
    _write_review(session, rows=[{"run_id": "F_001", "status": "pass", "score": 50.0,
                                  "trades": 17}], recommendations=[], rejected=[])
    sh = session.directory / "deployment_package" / "shadow"
    sh.mkdir(parents=True)
    (sh / "comparison.json").write_text(json.dumps({
        "comparisons": [{
            "candidate_run_id": "F_001",
            "backtest_trades": 17, "shadow_trades": 0,
            "divergence_flags": ["shadow window produced zero trades"],
        }],
    }), encoding="utf-8")
    dash = build_decision_dashboard(session)
    check = next(c for c in dash.candidate_rows[0].checks if c.name == "shadow")
    assert check.severity == SEVERITY_FAIL
    assert "BT 17 → shadow 0" in check.headline


def test_all_checks_ok_when_data_clean(tmp_path: Path):
    session = _make_session(tmp_path)
    _write_review(session, rows=[{"run_id": "F_001", "status": "pass", "score": 50.0,
                                  "trades": 50}], recommendations=[], rejected=[])
    pkg = session.directory / "deployment_package"
    (pkg / "robustness").mkdir()
    (pkg / "robustness" / "robustness.json").write_text(json.dumps({
        "bootstrap_results": [{"run_id": "F_001", "trade_count": 50,
                               "profit_factor": {"observed": 2.0,
                                                 "p_at_or_above_observed": 0.50}}],
    }), encoding="utf-8")
    (pkg / "walkforward").mkdir()
    (pkg / "walkforward" / "stability.json").write_text(json.dumps({
        "candidate_stability": [{"candidate_run_id": "F_001",
                                 "windows_run": 4, "windows_with_pf_above_1": 4,
                                 "pf_median": 2.1, "stability_flags": []}],
    }), encoding="utf-8")
    (pkg / "neighborhood").mkdir()
    (pkg / "neighborhood" / "stability.json").write_text(json.dumps({
        "candidate_stability": [{"candidate_run_id": "F_001",
                                 "cells_run": 8, "cells_with_pf_above_1": 8,
                                 "pf_median": 2.0, "stability_flags": []}],
    }), encoding="utf-8")
    (pkg / "shadow").mkdir()
    (pkg / "shadow" / "comparison.json").write_text(json.dumps({
        "comparisons": [{"candidate_run_id": "F_001",
                         "backtest_trades": 50, "shadow_trades": 12,
                         "divergence_flags": []}],
    }), encoding="utf-8")
    dash = build_decision_dashboard(session)
    severities = [c.severity for c in dash.candidate_rows[0].checks]
    assert severities == [SEVERITY_OK, SEVERITY_OK, SEVERITY_OK, SEVERITY_OK]


def test_artifact_links_only_include_existing_files(tmp_path: Path):
    session = _make_session(tmp_path)
    _write_review(session, rows=[{"run_id": "F_001", "status": "pass", "score": 50.0,
                                  "trades": 17}], recommendations=[], rejected=[])
    pkg = session.directory / "deployment_package"
    (pkg / "END_USER_DECISION.md").write_text("Decision state: ready\n", encoding="utf-8")
    (pkg / "walkforward").mkdir()
    (pkg / "walkforward" / "stability.md").write_text("# walk-forward\n", encoding="utf-8")
    dash = build_decision_dashboard(session)
    names = set(dash.artifact_links.keys())
    assert "End-user decision (Markdown)" in names
    assert "Walk-forward stability (Markdown)" in names
    assert "Shadow execution (Markdown)" not in names


def test_headline_and_notes_parsed_from_decision_md(tmp_path: Path):
    session = _make_session(tmp_path)
    _write_review(session, rows=[{"run_id": "F_001", "status": "pass", "score": 50.0,
                                  "trades": 17}], recommendations=[], rejected=[])
    pkg = session.directory / "deployment_package"
    (pkg / "END_USER_DECISION.md").write_text(
        "# End User\n\n"
        "Decision state: `candidate_ready_for_operator_review`\n\n"
        "## Decision Notes\n\n"
        "- First note about the candidate.\n"
        "- Second concern to look at.\n\n"
        "## Other Section\n\n"
        "- Not a decision note.\n",
        encoding="utf-8",
    )
    dash = build_decision_dashboard(session)
    assert dash.headline == "candidate_ready_for_operator_review"
    assert dash.decision_notes == [
        "First note about the candidate.",
        "Second concern to look at.",
    ]
