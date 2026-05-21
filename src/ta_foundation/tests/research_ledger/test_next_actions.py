"""Tests for the dry-run pipeline inspector (Phase 0 exit criterion)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ta_foundation.research_ledger import Repository, get_repository
from ta_foundation.research_ledger.cli_next_actions import (
    inspect_pipeline,
    next_action,
)


def _cand(**kw) -> SimpleNamespace:
    base = dict(
        candidate_id="c_x_001",
        hypothesis_id="h_x",
        gate_verdict="pending",
        triage_state=None,
        holdout_attempted=0,
        n_trades_dev=None, pf_dev=None,
        n_trades_oos=None, pf_oos=None,
        n_trades_holdout=None, pf_holdout=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ---- next_action: terminal states -----------------------------------------


@pytest.mark.parametrize("state", ["shadow", "graveyard", "decayed"])
def test_terminal_triage_states_have_no_transition(state: str) -> None:
    a = next_action(_cand(gate_verdict="survivor", triage_state=state))
    assert a.terminal is True
    assert a.next_tool is None
    assert a.stage == state


# ---- next_action: verdict-driven transitions ------------------------------


def test_rejected_goes_to_graveyard() -> None:
    a = next_action(_cand(gate_verdict="rejected"))
    assert a.next_tool == "set_triage_state(graveyard)"
    assert a.terminal is False


def test_pending_untriaged_goes_to_triage() -> None:
    a = next_action(_cand(gate_verdict="pending"))
    assert a.next_tool == "triage-pass"


def test_pending_in_research_is_a_manual_hold() -> None:
    a = next_action(_cand(gate_verdict="pending", triage_state="research"))
    assert a.next_tool is None
    assert a.terminal is True
    assert a.stage == "research"


# ---- next_action: survivor ladder -----------------------------------------


def test_fresh_survivor_is_promoted_to_hardening() -> None:
    a = next_action(_cand(gate_verdict="survivor"))
    assert a.next_tool == "promote_to_hardening"


def test_promoted_survivor_awaits_hardened_run() -> None:
    a = next_action(_cand(gate_verdict="survivor", triage_state="hardening_queue"))
    assert a.next_tool == "run_probe(mode=hardened)"


def test_hardened_survivor_requests_locked_holdout() -> None:
    a = next_action(_cand(gate_verdict="survivor", n_trades_dev=120,
                          pf_dev=2.0, n_trades_oos=40, pf_oos=1.8))
    assert a.next_tool == "request_locked_holdout"


def test_holdout_locked_survivor_awaits_holdout_run() -> None:
    a = next_action(_cand(gate_verdict="survivor", n_trades_dev=120, pf_dev=2.0,
                          n_trades_oos=40, pf_oos=1.8, holdout_attempted=1))
    assert a.next_tool == "run_probe(mode=locked_holdout)"


def test_passed_holdout_enrolls_in_shadow() -> None:
    a = next_action(_cand(gate_verdict="survivor", n_trades_dev=120, pf_dev=2.0,
                          n_trades_oos=40, pf_oos=1.8, holdout_attempted=1,
                          n_trades_holdout=30, pf_holdout=1.5))
    assert a.next_tool == "set_triage_state(shadow)"


def test_failed_holdout_goes_to_graveyard() -> None:
    a = next_action(_cand(gate_verdict="survivor", n_trades_dev=120, pf_dev=2.0,
                          n_trades_oos=40, pf_oos=1.8, holdout_attempted=1,
                          n_trades_holdout=30, pf_holdout=0.8))
    assert a.next_tool == "set_triage_state(graveyard)"
    assert a.stage == "holdout-failed"


# ---- inspect_pipeline: integration ----------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


def _seed(repo: Repository) -> None:
    repo.register_hypothesis(
        hypothesis_id="h_x", family="orb_failure_reclaim", instrument="NQ",
        timeframe="1m",
        params={"orb_minutes": 5}, registered_by="test",
        mechanism=("A mechanism paragraph that comfortably clears the "
                   "fifty-character minimum the repository enforces."),
    )
    repo.start_run(run_id="r_x", hypothesis_id="h_x", mode="fast_probe",
                   config_hash="c" * 64, yaml_path="x", artifact_dir="x")
    repo.complete_run("r_x")
    repo.record_candidate(candidate_id="c_x_001", run_id="r_x", rank_in_run=1,
                          params={}, gate_verdict="pending")
    repo.record_candidate(candidate_id="c_x_002", run_id="r_x", rank_in_run=2,
                          params={}, gate_verdict="survivor")
    repo.record_candidate(candidate_id="c_x_003", run_id="r_x", rank_in_run=3,
                          params={}, gate_verdict="rejected")
    repo.set_triage(candidate_id="c_x_003", state="graveyard",
                    reason="failed the gate under the honest correction",
                    triaged_by="test")


def test_inspect_pipeline_excludes_terminal_by_default(repo: Repository) -> None:
    _seed(repo)
    actions = inspect_pipeline(repo)
    ids = {a.candidate_id for a in actions}
    # c_x_003 is graveyarded (terminal) -> excluded.
    assert ids == {"c_x_001", "c_x_002"}


def test_inspect_pipeline_includes_terminal_on_request(repo: Repository) -> None:
    _seed(repo)
    actions = inspect_pipeline(repo, include_terminal=True)
    assert {a.candidate_id for a in actions} == {"c_x_001", "c_x_002", "c_x_003"}


def test_inspect_pipeline_filters_by_next_tool(repo: Repository) -> None:
    _seed(repo)
    actions = inspect_pipeline(repo, next_tool="promote_to_hardening")
    assert [a.candidate_id for a in actions] == ["c_x_002"]
