"""Phase D.3 — shadow-health prose layer tests.

The Scribe consumes a ``ShadowHealthReport`` (Phase D.2 deterministic
aggregator) and produces an LLM-narrated markdown letter. The contract
under test:

    1. Happy path: a well-behaved LLM (echoing numbers from the data
       block) produces a draft whose numerical claims lint clean.
    2. Hallucinated numbers trigger a retry and eventually a HITL flag
       when retries are exhausted.
    3. An empty day (no candidates enrolled in shadow) writes a
       deterministic stub without invoking the LLM.
    4. ``run_shadow_health_pass`` joins D.2 and D.3 end-to-end on a real
       in-memory ledger fixture.

The LLM is injected — no Ollama is required.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ta_foundation.agent.roles import scribe as scribe_mod
from ta_foundation.agent.roles.scribe import (
    run_shadow_health_pass,
    write_shadow_health_letter_draft,
)
from ta_foundation.research_ledger import Repository, get_repository
from ta_foundation.shadow.health import (
    AnomalyFlag,
    CandidateShadowHealth,
    ShadowHealthReport,
)


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(scribe_mod, "INBOX_ROOT", tmp_path / "inbox")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


def _register_shadow_candidate(repo: Repository, *, cid: str) -> None:
    hyp_id = f"h_{cid}"
    if repo.get_hypothesis(hyp_id) is None:
        repo.register_hypothesis(
            hypothesis_id=hyp_id,
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
                "stop_ticks": 20,
                "target_ticks": 150,
            },
            mechanism=(
                "Opening-range sweep + reclaim traps breakout chasers; the "
                "body-midpoint retrace lets the fade enter where the trapped "
                "longs have to exit, providing the directional impulse."
            ),
            registered_by="human:test",
        )
    run_id = f"r_{cid}"
    if repo.get_run(run_id) is None:
        repo.start_run(
            run_id=run_id, hypothesis_id=hyp_id, mode="hardened",
            config_hash="abc", yaml_path="x", artifact_dir="x",
        )
        repo.complete_run(run_id)
    repo.record_candidate(
        candidate_id=cid, run_id=run_id, rank_in_run=1, params={},
        gate_verdict="survivor",
        n_trades_dev=133, pf_dev=3.88,
    )
    repo.set_triage(
        candidate_id=cid, state="shadow",
        reason="enrolled for forward observation in the scribe fixture",
        triaged_by="test",
    )


def _make_report(*, candidates: list[CandidateShadowHealth],
                   anomalies: list[AnomalyFlag] | None = None,
                   report_date: str = "2026-05-08") -> ShadowHealthReport:
    return ShadowHealthReport(
        report_date=report_date,
        trailing_window=30,
        candidates_active=len(candidates),
        total_signals_today=sum(c.n_signals_today for c in candidates),
        total_resolved_today=sum(c.n_resolved_today for c in candidates),
        total_no_fill_today=sum(c.n_no_fill_today for c in candidates),
        total_open_positions=sum(c.n_open_positions for c in candidates),
        total_net_pnl_today=round(sum(c.net_pnl_today for c in candidates), 2),
        candidates=candidates,
        anomalies=anomalies or [],
    )


def _make_candidate_health(
    *, candidate_id: str = "c_obr_001",
    family: str = "orb_failure_reclaim",
    instrument: str = "NQ",
    n_signals_today: int = 2,
    n_resolved_today: int = 1,
    n_no_fill_today: int = 0,
    n_open_positions: int = 1,
    net_pnl_today: float = 750.00,
    trailing_window_n: int = 30,
    trailing_pf: float | None = 1.96,
    trailing_win_rate: float | None = 0.233,
    trailing_expectancy_net: float | None = 84.15,
) -> CandidateShadowHealth:
    return CandidateShadowHealth(
        candidate_id=candidate_id,
        family=family,
        instrument=instrument,
        n_signals_today=n_signals_today,
        n_resolved_today=n_resolved_today,
        n_no_fill_today=n_no_fill_today,
        n_open_positions=n_open_positions,
        net_pnl_today=net_pnl_today,
        trailing_window_n=trailing_window_n,
        trailing_pf=trailing_pf,
        trailing_win_rate=trailing_win_rate,
        trailing_expectancy_net=trailing_expectancy_net,
        first_signal_ts_denver="2026-05-08T07:35:00-06:00",
        last_signal_ts_denver="2026-05-08T13:50:00-06:00",
    )


def _good_health_llm():
    """Echoes numbers verbatim from the data block — guaranteed lint-clean."""
    def llm(system: str, user: str) -> str:
        pf_match = re.search(r"trailing_pf=([\d.]+)", user)
        wr_pct_match = re.search(r"trailing_win_rate_pct=([\d.]+)", user)
        exp_match = re.search(r"trailing_expectancy_net=([\d.]+)", user)
        net_match = re.search(r"net_pnl_today=([\d.]+)", user)
        cid_match = re.search(r"candidate_id=(\S+)", user)
        date_match = re.search(r"report_date:\s*(\S+)", user)
        pf = pf_match.group(1) if pf_match else "1.96"
        wr_pct = wr_pct_match.group(1) if wr_pct_match else "23.3"
        exp = exp_match.group(1) if exp_match else "84.15"
        net = net_match.group(1) if net_match else "750.00"
        cid = cid_match.group(1) if cid_match else "c_obr_001"
        rd = date_match.group(1) if date_match else "2026-05-08"
        body = (
            f"# Shadow health for {rd}\n\n"
            f"Candidate {cid} produced today's only resolved trade, closing at "
            f"a net of {net} on the session. Trailing edge stands at PF={pf} "
            f"with expectancy {exp} and a win rate near {wr_pct} percent over "
            "the trailing window. Open positions remain under management.\n\n"
            "No anomalies require operator action today. Continue forward "
            "observation through the standard end-of-week review.\n"
        )
        return body
    return llm


# ---------------------------------------------------------------------------
# Happy path / draft persistence
# ---------------------------------------------------------------------------


def test_shadow_health_letter_happy_path(repo: Repository) -> None:
    _register_shadow_candidate(repo, cid="c_obr_001")
    report = _make_report(candidates=[_make_candidate_health(candidate_id="c_obr_001")])
    out = write_shadow_health_letter_draft(
        repo, health_report=report, llm_call=_good_health_llm(),
    )
    assert out.draft_path is not None
    assert not out.hitl_flagged
    draft = Path(out.draft_path)
    assert draft.exists()
    body = draft.read_text(encoding="utf-8")
    assert body.startswith("---")
    assert "type: shadow_health_letter" in body
    assert "report_date: 2026-05-08" in body
    assert "candidate_id: c_obr_001" in body
    assert "PF=1.96" in body
    assert out.candidates_considered == 1
    assert out.anomalies_in_window == 0


def test_shadow_health_letter_hallucination_then_retry_succeeds(repo: Repository) -> None:
    _register_shadow_candidate(repo, cid="c_obr_001")
    report = _make_report(candidates=[_make_candidate_health(candidate_id="c_obr_001")])
    calls = {"n": 0}

    def llm(system: str, user: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return ("# Shadow health for 2026-05-08\n\n"
                    "Candidate c_obr_001 produced a trailing PF=9.87 with "
                    "expectancy 1234.56 today, which is completely "
                    "fabricated to make the linter trip. Pad the body "
                    "out to clear the minimum-length floor so the "
                    "unmatched-float check is what blocks the artifact "
                    "rather than the length gate. More padding follows "
                    "to comfortably reach the minimum body length.\n")
        return _good_health_llm()(system, user)

    out = write_shadow_health_letter_draft(
        repo, health_report=report, llm_call=llm, max_retries=2,
    )
    assert out.draft_path is not None
    assert not out.hitl_flagged
    assert calls["n"] == 2


def test_shadow_health_letter_hitl_flag_after_max_retries(repo: Repository) -> None:
    _register_shadow_candidate(repo, cid="c_obr_001")
    report = _make_report(candidates=[_make_candidate_health(candidate_id="c_obr_001")])

    def hallucinating(system: str, user: str) -> str:
        return ("# Shadow health for 2026-05-08\n\n"
                "Candidate c_obr_001 reported a trailing PF=7.77 today "
                "with expectancy 9999.99 and a net pnl of 4242.42. "
                "All three of these numbers are fabrications well "
                "outside the data block, so the linter is supposed to "
                "reject them. The body is padded to clear the 300-char "
                "floor so the rejection is over content, not length.\n")

    out = write_shadow_health_letter_draft(
        repo, health_report=report, llm_call=hallucinating, max_retries=1,
    )
    assert out.draft_path is None
    assert out.hitl_flagged
    fail_path = scribe_mod._shadow_health_inbox_path("2026-05-08").with_name(
        "2026-05-08_LINT_FAIL.md"
    )
    assert fail_path.exists()


def test_shadow_health_letter_empty_day_writes_stub(repo: Repository) -> None:
    # No candidates enrolled in shadow.
    report = _make_report(candidates=[])
    sentinel = {"called": False}

    def must_not_be_called(system: str, user: str) -> str:
        sentinel["called"] = True
        return ""

    out = write_shadow_health_letter_draft(
        repo, health_report=report, llm_call=must_not_be_called,
    )
    assert sentinel["called"] is False
    assert out.draft_path is not None
    body = Path(out.draft_path).read_text(encoding="utf-8")
    assert "No candidates were enrolled" in body
    assert "candidates_active: 0" in body


# ---------------------------------------------------------------------------
# End-to-end pass that joins D.2 → D.3
# ---------------------------------------------------------------------------


def test_run_shadow_health_pass_end_to_end(repo: Repository) -> None:
    _register_shadow_candidate(repo, cid="c_obr_001")
    # Seed one resolved win and one resolved loss so the trailing PF /
    # expectancy come out as finite numbers (need both gross profit and
    # gross loss to be non-zero for PF).
    repo.insert_shadow_signal_if_absent(
        candidate_id="c_obr_001",
        ts="2026-05-07T13:35:00Z",
        instrument="NQ",
        direction="short",
        planned_entry=100.0, planned_stop=101.0, planned_target=92.5,
        realized_outcome={
            "status": "resolved", "result": "loss",
            "exit_ts_utc": "2026-05-07T13:50:00Z",
            "profit_net": -100.0,
        },
    )
    repo.insert_shadow_signal_if_absent(
        candidate_id="c_obr_001",
        ts="2026-05-08T13:35:00Z",
        instrument="NQ",
        direction="short",
        planned_entry=100.0, planned_stop=101.0, planned_target=92.5,
        realized_outcome={
            "status": "resolved", "result": "win",
            "exit_ts_utc": "2026-05-08T13:50:00Z",
            "profit_net": 750.0,
            "profit_ticks": 150.0,
        },
    )
    out = run_shadow_health_pass(
        repo,
        llm_call=_good_health_llm(),
        for_date="2026-05-08",
        trailing_window=30,
        open_position_age_warn_hours=24 * 365,
    )
    assert out.draft_path is not None
    assert not out.hitl_flagged
    body = Path(out.draft_path).read_text(encoding="utf-8")
    assert "report_date: 2026-05-08" in body
    assert "c_obr_001" in body
