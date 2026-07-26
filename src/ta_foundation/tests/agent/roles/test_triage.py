"""Tests for the Triage Analyst role.

The LLM call is injected so tests run without Ollama or any network. The
agent's classification logic is exercised exhaustively; the LLM is reduced
to a stub that returns canned JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

from ta_foundation.agent.roles.triage import (
    derive_triage_state,
    generate_triage_reason,
    parse_llm_reason,
    run_triage_pass,
)
from ta_foundation.research_ledger import Repository, get_repository
from ta_foundation.research_ledger.models import Candidate


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


def _make_candidate(
    *,
    candidate_id: str = "c_t_001",
    gate_verdict: str = "rejected",
    pf_dev: float | None = 1.62,
    n_trades_dev: int | None = 47,
    pf_oos: float | None = None,
    n_trades_oos: int | None = None,
    pf_holdout: float | None = None,
    n_trades_holdout: int | None = None,
    notes_json: str | None = None,
) -> Candidate:
    return Candidate(
        candidate_id=candidate_id, run_id="r", hypothesis_id="h",
        rank_in_run=1, params_json="{}",
        n_trades_dev=n_trades_dev, pf_dev=pf_dev, expectancy_dev=None,
        n_trades_oos=n_trades_oos, pf_oos=pf_oos, expectancy_oos=None,
        n_trades_holdout=n_trades_holdout, pf_holdout=pf_holdout,
        expectancy_holdout=None,
        gate_verdict=gate_verdict, gate_reasons_json=None,
        slippage_stress_pass=None, folds_distribution=None,
        triage_state=None, triage_reason=None, triaged_at=None,
        triaged_by=None, holdout_attempted=0, notes_json=notes_json,
    )


# =========================================================================
# derive_triage_state — deterministic classification
# =========================================================================


def test_state_rejected_becomes_graveyard() -> None:
    c = _make_candidate(gate_verdict="rejected")
    assert derive_triage_state(c) == "graveyard"


def test_state_pending_becomes_research() -> None:
    c = _make_candidate(gate_verdict="pending")
    assert derive_triage_state(c) == "research"


def test_state_survivor_without_holdout_becomes_hardening_queue() -> None:
    c = _make_candidate(gate_verdict="survivor", pf_holdout=None,
                        n_trades_holdout=None)
    assert derive_triage_state(c) == "hardening_queue"


def test_state_survivor_with_passing_holdout_becomes_shadow() -> None:
    c = _make_candidate(gate_verdict="survivor", pf_holdout=1.4,
                        n_trades_holdout=30)
    assert derive_triage_state(c) == "shadow"


def test_state_survivor_with_failing_holdout_becomes_graveyard() -> None:
    c = _make_candidate(gate_verdict="survivor", pf_holdout=0.92,
                        n_trades_holdout=20)
    assert derive_triage_state(c) == "graveyard"


def test_state_unknown_verdict_defaults_to_research() -> None:
    c = _make_candidate(gate_verdict="brilliant")
    assert derive_triage_state(c) == "research"


# =========================================================================
# parse_llm_reason — lenient extraction
# =========================================================================


def test_parse_direct_json() -> None:
    assert parse_llm_reason('{"reason": "hello"}') == "hello"


def test_parse_json_with_surrounding_prose() -> None:
    raw = "Sure! Here's my answer:\n{\"reason\": \"because reasons\"}\nThanks!"
    assert parse_llm_reason(raw) == "because reasons"


def test_parse_invalid_returns_none() -> None:
    assert parse_llm_reason("no json here") is None
    assert parse_llm_reason("{not real}") is None
    assert parse_llm_reason(None) is None  # type: ignore[arg-type]


def test_parse_missing_reason_field_returns_none() -> None:
    assert parse_llm_reason('{"state": "graveyard"}') is None


# =========================================================================
# generate_triage_reason — LLM stub + linter retry loop
# =========================================================================


def _good_reason_for(c: Candidate, state: str) -> str:
    return (
        f"Candidate is moved to {state}. Dev showed PF={c.pf_dev} on "
        f"{c.n_trades_dev} trades, which is consistent with the deterministic "
        "rule applied. Mechanism and sample size both within expectations."
    )


def test_generate_reason_succeeds_first_try() -> None:
    c = _make_candidate(pf_dev=1.62, n_trades_dev=47)

    def llm(system: str, user: str) -> str:
        return json.dumps({"reason": _good_reason_for(c, "graveyard")})

    reason, violations = generate_triage_reason(c, "graveyard", llm)
    assert reason is not None
    assert violations == []


def test_generate_reason_retries_after_hallucination() -> None:
    c = _make_candidate(pf_dev=1.62, n_trades_dev=47)
    calls = {"n": 0}

    def llm(system: str, user: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"reason":
                "Candidate is moved to graveyard. PF was 2.85 on 250 trades, well below "
                "fund-grade threshold; mechanism unclear and sample too small."})
        return json.dumps({"reason": _good_reason_for(c, "graveyard")})

    reason, violations = generate_triage_reason(c, "graveyard", llm)
    assert reason is not None
    assert calls["n"] == 2
    # Accumulated violations from the first attempt are surfaced.
    assert any(v["code"] == "unmatched_float" for v in violations)


def test_generate_reason_fails_after_max_retries() -> None:
    c = _make_candidate(pf_dev=1.62, n_trades_dev=47)

    def llm(system: str, user: str) -> str:
        # Always hallucinates.
        return json.dumps({"reason":
            "Candidate is moved to graveyard. PF was 9.99 on 9999 trades, well below "
            "fund-grade threshold; mechanism unclear and sample too small."})

    reason, violations = generate_triage_reason(c, "graveyard", llm, max_retries=1)
    assert reason is None
    assert any(v["code"] == "unmatched_float" for v in violations)


def test_generate_reason_handles_unparseable_response() -> None:
    c = _make_candidate(pf_dev=1.62, n_trades_dev=47)

    def llm(system: str, user: str) -> str:
        return "I cannot comply with this request."

    reason, violations = generate_triage_reason(c, "graveyard", llm, max_retries=0)
    assert reason is None
    assert any(v["code"] == "unparseable_response" for v in violations)


def test_generate_reason_feeds_violations_back_into_prompt() -> None:
    c = _make_candidate(pf_dev=1.62, n_trades_dev=47)
    captured: list[str] = []

    def llm(system: str, user: str) -> str:
        captured.append(user)
        # First attempt hallucinates so we can confirm the second prompt
        # includes the violation feedback.
        if len(captured) == 1:
            return json.dumps({"reason":
                "Candidate moves to graveyard with PF=2.85 across 250 trades — clear "
                "noise. Mechanism not compelling enough to keep for research."})
        return json.dumps({"reason": _good_reason_for(c, "graveyard")})

    generate_triage_reason(c, "graveyard", llm)
    assert "unmatched_float" in captured[1] or "previous attempt" in captured[1].lower()


# =========================================================================
# run_triage_pass — end-to-end against a real ledger
# =========================================================================


def _seed_candidate(
    repo: Repository,
    *,
    cid: str,
    gate_verdict: str = "rejected",
    pf_dev: float = 1.62,
    n_trades_dev: int = 47,
    pf_holdout: float | None = None,
    n_trades_holdout: int | None = None,
) -> None:
    if repo.get_hypothesis("h_x") is None:
        repo.register_hypothesis(
            hypothesis_id="h_x", family="vwap_reject_fade",
            instrument="NQ", timeframe="5m",
            params={"min_distance_ticks": 4, "stop_ticks": 8, "target_ticks": 24},
            mechanism=("A reasonable mechanism paragraph that comfortably exceeds the "
                       "50-character minimum the repository enforces."),
            registered_by="test",
        )
    if repo.get_run("r_x") is None:
        repo.start_run(run_id="r_x", hypothesis_id="h_x", mode="hardened",
                       config_hash="abc", yaml_path="x", artifact_dir="x")
        repo.complete_run("r_x")
    repo.record_candidate(
        candidate_id=cid, run_id="r_x",
        rank_in_run=int(cid[-3:]) if cid[-3:].isdigit() else 1,
        params={}, gate_verdict=gate_verdict,
        n_trades_dev=n_trades_dev, pf_dev=pf_dev,
        n_trades_holdout=n_trades_holdout, pf_holdout=pf_holdout,
    )


def _good_llm(reason_for_state: dict[str, str] = None):
    def llm(system: str, user: str) -> str:
        # Default reason adapts to whichever candidate is in the prompt.
        # We just need numbers that match. Trick: pull pf_dev / n_trades_dev
        # back out of the prompt.
        import re
        pf_match = re.search(r"pf_dev: ([\d.]+)", user)
        n_match = re.search(r"n_trades_dev: (\d+)", user)
        pf = pf_match.group(1) if pf_match else "1.62"
        n = n_match.group(1) if n_match else "47"
        reason = (
            f"Candidate triaged with PF={pf} across {n} trades. The deterministic "
            "classification rule applied to this gate verdict produced the chosen "
            "state. Sample and mechanism both consistent with that decision."
        )
        return json.dumps({"reason": reason})
    return llm


def test_run_triage_pass_processes_untriaged_only(repo: Repository) -> None:
    _seed_candidate(repo, cid="c_001")
    _seed_candidate(repo, cid="c_002")
    # Pre-triage one of them.
    repo.set_triage(candidate_id="c_002", state="research",
                    reason="manually pre-triaged for the test scenario here",
                    triaged_by="human")
    report = run_triage_pass(repo, llm_call=_good_llm(), limit=10)
    assert report.scanned == 1
    assert report.triaged == 1
    assert report.by_state == {"graveyard": 1}
    # The pre-triaged one is unchanged.
    c2 = repo.get_candidate("c_002")
    assert c2 is not None and c2.triage_state == "research"


def test_run_triage_pass_classifies_by_state(repo: Repository) -> None:
    _seed_candidate(repo, cid="c_001", gate_verdict="rejected")
    _seed_candidate(repo, cid="c_002", gate_verdict="pending",
                    pf_dev=1.10, n_trades_dev=80)
    _seed_candidate(repo, cid="c_003", gate_verdict="survivor",
                    pf_dev=1.85, n_trades_dev=120)
    _seed_candidate(repo, cid="c_004", gate_verdict="survivor",
                    pf_dev=1.85, n_trades_dev=120,
                    pf_holdout=1.4, n_trades_holdout=30)
    report = run_triage_pass(repo, llm_call=_good_llm(), limit=10)
    assert report.triaged == 4
    assert report.by_state == {
        "graveyard": 1,
        "research": 1,
        "hardening_queue": 1,
        "shadow": 1,
    }
    states = {repo.get_candidate(c).triage_state  # type: ignore[union-attr]
              for c in ("c_001", "c_002", "c_003", "c_004")}
    assert states == {"graveyard", "research", "hardening_queue", "shadow"}


def test_run_triage_pass_hitl_on_llm_failure(repo: Repository) -> None:
    _seed_candidate(repo, cid="c_001")

    def bad_llm(system: str, user: str) -> str:
        return "I refuse to respond."

    report = run_triage_pass(repo, llm_call=bad_llm, limit=10, max_retries=1)
    assert report.triaged == 0
    assert report.hitl_flagged == 1
    assert report.failures[0]["candidate_id"] == "c_001"
    # Candidate left untriaged.
    c = repo.get_candidate("c_001")
    assert c is not None and c.triage_state is None


def test_run_triage_pass_respects_limit(repo: Repository) -> None:
    for i in range(1, 6):
        _seed_candidate(repo, cid=f"c_{i:03d}")
    report = run_triage_pass(repo, llm_call=_good_llm(), limit=3)
    assert report.scanned == 3
    assert report.triaged == 3
    # The remaining two are still untriaged.
    untriaged_after = repo.list_candidates(untriaged_only=True)
    assert len(untriaged_after) == 2


def test_run_triage_pass_is_idempotent_when_called_twice(repo: Repository) -> None:
    _seed_candidate(repo, cid="c_001")
    first = run_triage_pass(repo, llm_call=_good_llm(), limit=10)
    second = run_triage_pass(repo, llm_call=_good_llm(), limit=10)
    assert first.triaged == 1
    assert second.scanned == 0
    assert second.triaged == 0
