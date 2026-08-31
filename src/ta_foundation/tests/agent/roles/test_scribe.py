"""Tests for the Scribe role — post-mortem and weekly letter generation.

Like the Triage tests, the LLM is injected so no Ollama is required.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ta_foundation.agent.roles import scribe as scribe_mod
from ta_foundation.agent.roles.scribe import (
    has_post_mortem_draft_or_final,
    run_post_mortem_pass,
    write_post_mortem_draft,
    write_weekly_letter_draft,
)
from ta_foundation.research_ledger import Repository, get_repository
from ta_foundation.research_ledger.models import Candidate


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(scribe_mod, "INBOX_ROOT", tmp_path / "inbox")
    monkeypatch.setattr(scribe_mod, "LETTERS_FINAL_DIR", tmp_path / "letters")
    monkeypatch.chdir(tmp_path)  # so discovery/graveyard/ is also tmp-local
    return tmp_path


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


def _seed_graveyard_candidate(
    repo: Repository,
    *, cid: str = "c_001",
    pf_dev: float = 1.62, n_trades_dev: int = 47,
) -> Candidate:
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
        params={}, gate_verdict="rejected",
        n_trades_dev=n_trades_dev, pf_dev=pf_dev,
    )
    repo.set_triage(
        candidate_id=cid, state="graveyard",
        reason="failed adjusted t-test under multiple-comparison correction",
        triaged_by="test",
    )
    return repo.get_candidate(cid)  # type: ignore[return-value]


def _good_post_mortem_llm():
    """Returns an LLM that produces a body using only metrics from the prompt."""
    def llm(system: str, user: str) -> str:
        import re
        pf_match = re.search(r"pf_dev: ([\d.]+)", user)
        n_match = re.search(r"n_trades_dev: (\d+)", user)
        cid_match = re.search(r"candidate_id: (\S+)", user)
        pf = pf_match.group(1) if pf_match else "1.62"
        n = n_match.group(1) if n_match else "47"
        cid = cid_match.group(1) if cid_match else "c_001"
        body = (
            f"# Post-mortem for {cid}\n\n"
            f"Candidate {cid} reached PF={pf} across {n} trades on the dev slice. "
            "The adjusted t-test under multiple-comparison correction did not clear "
            "the fund-grade threshold, and the sample size is below the gate floor. "
            "Mechanism remains plausible but underpowered; further data collection "
            "in the next quarter could revisit the structural thesis if conditions "
            "haven't shifted.\n"
        )
        return body
    return llm


# ===========================================================================
# write_post_mortem_draft
# ===========================================================================


def test_post_mortem_draft_happy_path(repo: Repository) -> None:
    c = _seed_graveyard_candidate(repo)
    path, violations = write_post_mortem_draft(repo, c, _good_post_mortem_llm())
    assert path is not None
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert body.startswith("---")
    assert "candidate_id: c_001" in body
    assert "PF=1.62" in body
    assert violations == []


def test_post_mortem_draft_strips_code_fences(repo: Repository) -> None:
    c = _seed_graveyard_candidate(repo)

    def fenced_llm(system: str, user: str) -> str:
        return ("```markdown\n"
                "# Post-mortem for c_001\n\n"
                "Candidate c_001 reached PF=1.62 across 47 trades on dev. "
                "Adjusted t-test failed under multiple-comparison correction. "
                "Mechanism is plausible but the sample size is too small to "
                "support deployment. Further data collection may revisit the "
                "structural thesis next quarter if conditions change.\n"
                "```")
    path, _ = write_post_mortem_draft(repo, c, fenced_llm)
    assert path is not None
    assert "```" not in path.read_text(encoding="utf-8")


def test_post_mortem_draft_retries_on_hallucination(repo: Repository) -> None:
    c = _seed_graveyard_candidate(repo)
    calls = {"n": 0}

    def llm(system: str, user: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return ("# Post-mortem for c_001\n\nCandidate c_001 produced PF=9.99 "
                    "across 9999 trades on dev. Hallucinated numbers throughout "
                    "this attempt that should trigger the linter to fail and "
                    "force a retry from the calling pipeline.\n")
        return _good_post_mortem_llm()(system, user)

    path, violations = write_post_mortem_draft(repo, c, llm)
    assert path is not None and path.exists()
    assert calls["n"] == 2
    assert any(v["code"] == "unmatched_float" for v in violations)


def test_post_mortem_draft_hitl_after_max_retries(repo: Repository) -> None:
    c = _seed_graveyard_candidate(repo)

    def hallucinating_llm(system: str, user: str) -> str:
        return ("# Post-mortem for c_001\n\n"
                "Candidate c_001 produced PF=8.88 across 8888 trades on the dev "
                "slice. Numbers in this attempt are completely fabricated, well "
                "outside anything in the data block above, which is exactly what "
                "the linter is supposed to catch and surface to the operator. "
                "Padding text follows to ensure the body comfortably clears the "
                "200-character minimum the Scribe enforces, so the unmatched-float "
                "violation rather than length is the gating issue when the linter "
                "runs against this draft.\n")

    path, violations = write_post_mortem_draft(repo, c, hallucinating_llm, max_retries=1)
    assert path is None
    assert any(v["code"] == "unmatched_float" for v in violations)
    # A LINT_FAIL placeholder draft should exist instead.
    fail_path = scribe_mod._post_mortem_inbox_dir() / "c_001_LINT_FAIL.md"
    assert fail_path.exists()


# ===========================================================================
# run_post_mortem_pass
# ===========================================================================


def test_post_mortem_pass_processes_only_graveyard(repo: Repository) -> None:
    _seed_graveyard_candidate(repo, cid="c_001")
    # Seed a non-graveyard candidate too.
    repo.record_candidate(
        candidate_id="c_002", run_id="r_x", rank_in_run=2, params={},
        gate_verdict="survivor",
        n_trades_dev=120, pf_dev=1.85,
    )
    report = run_post_mortem_pass(repo, llm_call=_good_post_mortem_llm(), limit=10)
    assert report.scanned == 1
    assert report.written == 1
    assert "c_001" in report.by_candidate


def test_post_mortem_pass_skips_existing_drafts(repo: Repository) -> None:
    _seed_graveyard_candidate(repo)
    first = run_post_mortem_pass(repo, llm_call=_good_post_mortem_llm())
    second = run_post_mortem_pass(repo, llm_call=_good_post_mortem_llm())
    assert first.written == 1
    assert second.written == 0
    assert second.skipped_existing == 1


def test_post_mortem_pass_records_hitl_failures(repo: Repository) -> None:
    _seed_graveyard_candidate(repo)

    def bad_llm(system: str, user: str) -> str:
        return ""

    report = run_post_mortem_pass(repo, llm_call=bad_llm, limit=10, max_retries=0)
    assert report.written == 0
    assert report.hitl_flagged == 1
    assert report.failures[0]["candidate_id"] == "c_001"


def test_post_mortem_idempotency_after_final_acceptance(
    repo: Repository, tmp_path: Path
) -> None:
    c = _seed_graveyard_candidate(repo)
    write_post_mortem_draft(repo, c, _good_post_mortem_llm())
    # Simulate acceptance by moving the file into discovery/graveyard/.
    final = Path("discovery/graveyard") / f"{c.candidate_id}.md"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_text(
        scribe_mod._post_mortem_draft_path(c.candidate_id).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    scribe_mod._post_mortem_draft_path(c.candidate_id).unlink()
    # Second pass should see the final file and skip.
    assert has_post_mortem_draft_or_final(c.candidate_id) is True
    report = run_post_mortem_pass(repo, llm_call=_good_post_mortem_llm())
    assert report.skipped_existing == 1
    assert report.written == 0


# ===========================================================================
# write_weekly_letter_draft
# ===========================================================================


def _seed_candidate_in_window(
    repo: Repository, *, cid: str, days_ago: int,
    pf_dev: float = 1.62, n_trades_dev: int = 47,
    gate_verdict: str = "rejected",
) -> None:
    if repo.get_hypothesis("h_w") is None:
        repo.register_hypothesis(
            hypothesis_id="h_w", family="vwap_reject_fade",
            instrument="NQ", timeframe="5m",
            params={"min_distance_ticks": 4, "stop_ticks": 8, "target_ticks": 24},
            mechanism=("A reasonable mechanism paragraph that comfortably exceeds the "
                       "50-character minimum the repository enforces."),
            registered_by="test",
        )
    run_id = f"r_w_{cid}"
    # Backdate the run's started_at via raw SQL so it falls inside the window.
    started_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)
                   ).strftime("%Y-%m-%dT%H:%M:%SZ")
    repo.start_run(run_id=run_id, hypothesis_id="h_w", mode="hardened",
                   config_hash="abc", yaml_path="x", artifact_dir="x")
    repo.complete_run(run_id)
    repo.conn.execute("UPDATE runs SET started_at = ? WHERE run_id = ?",
                       (started_at, run_id))
    repo.record_candidate(
        candidate_id=cid, run_id=run_id, rank_in_run=1, params={},
        gate_verdict=gate_verdict,
        n_trades_dev=n_trades_dev, pf_dev=pf_dev,
    )


def _good_letter_llm():
    def llm(system: str, user: str) -> str:
        # Echo the candidate ids and the numbers from the prompt's data block.
        import re
        pf_dev_vals = re.findall(r"pf_dev=([\d.]+|None)", user)
        n_dev_vals = re.findall(r"n_trades_dev=(\d+|None)", user)
        cid_vals = re.findall(r"candidate_id=(\S+)", user)
        pf_clean = [v for v in pf_dev_vals if v != "None"]
        n_clean = [v for v in n_dev_vals if v != "None"]
        body = ["# Weekly research letter\n",
                f"This week the ledger recorded activity across {len(cid_vals)} candidates."]
        for cid, pf, n in zip(cid_vals, pf_clean, n_clean):
            body.append(f"- {cid}: PF={pf} across {n} trades.")
        body.append(
            "\nGraveyard rates remained consistent with the rejection-heavy "
            "narrative expected of the program. No survivors entered the shadow "
            "queue this week, and forward observation continues. Plan for next "
            "week: focus follow-up probes on the families showing the most "
            "graveyard concentration.\n"
        )
        return "\n".join(body)
    return llm


def test_weekly_letter_with_window_data(repo: Repository) -> None:
    today = date.today()
    week_start = today - timedelta(days=6)
    _seed_candidate_in_window(repo, cid="c_001", days_ago=2,
                                pf_dev=1.62, n_trades_dev=47)
    _seed_candidate_in_window(repo, cid="c_002", days_ago=4,
                                pf_dev=2.10, n_trades_dev=120,
                                gate_verdict="survivor")
    report = write_weekly_letter_draft(repo, llm_call=_good_letter_llm(),
                                         week_start=week_start)
    assert report.draft_path
    body = Path(report.draft_path).read_text(encoding="utf-8")
    assert "c_001" in body
    assert "c_002" in body
    assert report.hitl_flagged is False


def test_weekly_letter_empty_window_writes_stub(repo: Repository) -> None:
    def llm_should_not_be_called(system: str, user: str) -> str:
        raise AssertionError("LLM should not be called for empty windows")

    report = write_weekly_letter_draft(repo, llm_call=llm_should_not_be_called)
    assert report.candidates_considered == 0
    assert report.draft_path is not None
    body = Path(report.draft_path).read_text(encoding="utf-8")
    assert "No new candidates" in body


def test_weekly_letter_hitl_on_hallucination(repo: Repository) -> None:
    today = date.today()
    # A rolling seven-day window, matching test_weekly_letter_with_window_data.
    # Using the ISO week start instead made this test calendar-dependent: on a
    # Monday or Tuesday the candidate seeded two days ago falls in the *previous*
    # week, so the letter had nothing to consider, the LLM was never called, and
    # the hallucination linter under test never ran -- the assertion then failed
    # for a reason unrelated to HITL behaviour.
    week_start = today - timedelta(days=6)
    _seed_candidate_in_window(repo, cid="c_001", days_ago=2)

    def hallucinating_llm(system: str, user: str) -> str:
        return ("# Weekly research letter\n\n"
                "This week the ledger recorded activity with PF=9.99 across "
                "9999 trades on candidate c_001 — wildly hallucinated numbers "
                "that should be rejected by the linter and surfaced to the "
                "operator for HITL review and reprompt. "
                + ("Padding to ensure the body clears the 400-char minimum "
                   "the weekly letter Scribe enforces, so the unmatched-float "
                   "violation is the gating issue when the linter runs.\n") * 2)

    report = write_weekly_letter_draft(repo, llm_call=hallucinating_llm,
                                         week_start=week_start, max_retries=1)
    assert report.hitl_flagged is True
    assert any(v["code"] == "unmatched_float" for v in report.violations)
