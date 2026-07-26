"""Tests for the numeric-claim and structural linters used by Triage + Scribe."""

from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.agent.roles._linter import (
    MAX_TRIAGE_REASON_CHARS,
    MIN_TRIAGE_REASON_CHARS,
    LintResult,
    validate_artifact_markdown,
    validate_triage_reason,
)
from ta_foundation.research_ledger import Repository, get_repository
from ta_foundation.research_ledger.models import Candidate


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


def _candidate(
    *,
    candidate_id: str = "c_t_001",
    pf_dev: float | None = 1.62,
    n_trades_dev: int | None = 47,
    pf_oos: float | None = None,
    n_trades_oos: int | None = None,
    pf_holdout: float | None = None,
    n_trades_holdout: int | None = None,
    expectancy_dev: float | None = None,
) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        run_id="r_x",
        hypothesis_id="h_x",
        rank_in_run=1,
        params_json="{}",
        n_trades_dev=n_trades_dev,
        pf_dev=pf_dev,
        expectancy_dev=expectancy_dev,
        n_trades_oos=n_trades_oos,
        pf_oos=pf_oos,
        expectancy_oos=None,
        n_trades_holdout=n_trades_holdout,
        pf_holdout=pf_holdout,
        expectancy_holdout=None,
        gate_verdict="rejected",
        gate_reasons_json=None,
        slippage_stress_pass=None,
        folds_distribution=None,
        triage_state=None,
        triage_reason=None,
        triaged_at=None,
        triaged_by=None,
        holdout_attempted=0,
        notes_json=None,
    )


# ===========================================================================
# validate_triage_reason
# ===========================================================================


def test_triage_reason_valid_with_matching_numbers() -> None:
    c = _candidate(pf_dev=1.62, n_trades_dev=47)
    reason = (
        "Candidate failed the adjusted t-test under multiple-comparison correction. "
        "PF=1.62 on 47 trades is below the fund-grade threshold and lacks an OOS slice "
        "for confirmation. Mechanism plausible but underpowered."
    )
    result = validate_triage_reason(reason, c)
    assert result.ok, result.violations


def test_triage_reason_rejects_too_short() -> None:
    c = _candidate()
    result = validate_triage_reason("too short", c)
    assert not result.ok
    assert any(v["code"] == "too_short" for v in result.violations)


def test_triage_reason_rejects_too_long() -> None:
    c = _candidate()
    reason = "x" * (MAX_TRIAGE_REASON_CHARS + 50)
    result = validate_triage_reason(reason, c)
    assert not result.ok
    assert any(v["code"] == "too_long" for v in result.violations)


def test_triage_reason_rejects_hallucinated_pf() -> None:
    c = _candidate(pf_dev=1.62, n_trades_dev=47)
    reason = (
        "Candidate appears strong with PF=2.85 across 47 trades, suggesting durable edge. "
        "Recommended for hardening pipeline pending OOS confirmation against deltas."
    )
    result = validate_triage_reason(reason, c)
    assert not result.ok
    assert any(v["code"] == "unmatched_float" and v["token"] == "2.85"
               for v in result.violations)


def test_triage_reason_rejects_hallucinated_trade_count() -> None:
    c = _candidate(pf_dev=1.62, n_trades_dev=47)
    reason = (
        "Candidate failed across 250 trades with PF=1.62, indicating noise rather than "
        "edge. Forward observation is not warranted at this sample size."
    )
    result = validate_triage_reason(reason, c)
    assert not result.ok
    assert any(v["code"] == "unmatched_int" and v["token"] == "250"
               for v in result.violations)


def test_triage_reason_accepts_year_and_small_ints() -> None:
    c = _candidate(pf_dev=1.62, n_trades_dev=47)
    reason = (
        "In 2026 the strategy produced PF=1.62 over 47 trades, with 1 OOS fold available. "
        "Mechanism plausible but sample size below the 2x cost-stress threshold."
    )
    result = validate_triage_reason(reason, c)
    assert result.ok, result.violations


def test_triage_reason_allows_pf_within_tolerance() -> None:
    c = _candidate(pf_dev=1.624, n_trades_dev=47)
    reason = (
        "Candidate reported PF=1.62 on 47 trades; below adjusted significance and lacking "
        "OOS coverage. Mechanism survives narrative review but the sample is unconvincing."
    )
    result = validate_triage_reason(reason, c)
    assert result.ok


def test_triage_reason_rejects_non_string() -> None:
    c = _candidate()
    result = validate_triage_reason(None, c)  # type: ignore[arg-type]
    assert not result.ok
    assert result.violations[0]["code"] == "not_a_string"


# ===========================================================================
# validate_artifact_markdown — frontmatter + cites
# ===========================================================================


def _seed_real_candidate(repo: Repository, *, candidate_id: str = "c_real_001",
                          pf_dev: float = 1.62, n_trades_dev: int = 47) -> None:
    repo.register_hypothesis(
        hypothesis_id="h_real",
        family="vwap_reject_fade",
        instrument="NQ",
        timeframe="5m",
        params={"min_distance_ticks": 4, "stop_ticks": 8, "target_ticks": 24},
        mechanism=("A reasonable mechanism paragraph that comfortably exceeds the "
                   "50-character minimum the repository enforces for pre-registration."),
        registered_by="test",
    )
    repo.start_run(run_id="r_real", hypothesis_id="h_real", mode="hardened",
                   config_hash="abc", yaml_path="x", artifact_dir="x")
    repo.complete_run("r_real")
    repo.record_candidate(
        candidate_id=candidate_id, run_id="r_real", rank_in_run=1, params={},
        gate_verdict="rejected", n_trades_dev=n_trades_dev, pf_dev=pf_dev,
    )


def test_artifact_lint_missing_frontmatter(repo: Repository) -> None:
    result = validate_artifact_markdown("just a markdown body", repo)
    assert not result.ok
    assert result.violations[0]["code"] == "missing_frontmatter"


def test_artifact_lint_unknown_cite(repo: Repository) -> None:
    markdown = (
        "---\n"
        "cites:\n"
        "  - candidate_id: c_does_not_exist\n"
        "---\n"
        "# Post-mortem\n\nBody mentions c_does_not_exist.\n"
    )
    result = validate_artifact_markdown(markdown, repo)
    assert not result.ok
    assert any(v["code"] == "unknown_candidate_cite" for v in result.violations)


def test_artifact_lint_unmatched_pf_in_body(repo: Repository) -> None:
    _seed_real_candidate(repo)
    markdown = (
        "---\n"
        "cites:\n"
        "  - candidate_id: c_real_001\n"
        "---\n"
        "# Weekly letter\n\n"
        "Candidate c_real_001 showed PF=2.99 across 47 trades.\n"
    )
    result = validate_artifact_markdown(markdown, repo)
    assert not result.ok
    assert any(v["code"] == "unmatched_float" and v["token"] == "2.99"
               for v in result.violations)


def test_artifact_lint_accepts_aggregated_cites(repo: Repository) -> None:
    _seed_real_candidate(repo, candidate_id="c_a", pf_dev=1.62, n_trades_dev=47)
    repo.record_candidate(
        candidate_id="c_b", run_id="r_real", rank_in_run=2, params={},
        gate_verdict="rejected", n_trades_dev=120, pf_dev=2.10,
    )
    markdown = (
        "---\n"
        "cites:\n"
        "  - candidate_id: c_a\n"
        "  - candidate_id: c_b\n"
        "---\n"
        "Letter cites c_a (PF=1.62 on 47 trades) and c_b (PF=2.10 on 120 trades).\n"
    )
    result = validate_artifact_markdown(markdown, repo)
    assert result.ok, result.violations


def test_artifact_lint_malformed_cites(repo: Repository) -> None:
    markdown = "---\ncites: not_a_list\n---\nbody\n"
    result = validate_artifact_markdown(markdown, repo)
    assert not result.ok
    assert result.violations[0]["code"] == "malformed_cites"


def test_artifact_lint_skips_year_tokens(repo: Repository) -> None:
    _seed_real_candidate(repo)
    markdown = (
        "---\n"
        "cites:\n"
        "  - candidate_id: c_real_001\n"
        "---\n"
        "Over the period 2024 through 2026 candidate c_real_001 reported PF=1.62 "
        "across 47 trades.\n"
    )
    result = validate_artifact_markdown(markdown, repo)
    assert result.ok
