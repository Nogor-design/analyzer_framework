"""Tests for the Hypothesis Author role (C.1) and family-coverage cap (C.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_foundation.agent.roles import hypothesis_author as author_mod
from ta_foundation.agent.roles.hypothesis_author import (
    DEFAULT_SESSION_QUOTA,
    DEFAULT_WEEKLY_QUOTA,
    parse_proposals,
    propose_hypotheses,
)
from ta_foundation.research_ledger import Repository, get_repository


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path, monkeypatch):
    from ta_foundation.agent import inbox as inbox_mod
    inbox_root = tmp_path / "inbox"
    monkeypatch.setattr(author_mod, "INBOX_ROOT", inbox_root)
    monkeypatch.setattr(inbox_mod, "INBOX_ROOT", inbox_root)
    monkeypatch.setattr(inbox_mod, "REJECTED_ROOT", tmp_path / "rejected")
    # author_probe writes a YAML under discovery/generated/ — keep tmp-local.
    monkeypatch.chdir(tmp_path)
    from ta_foundation.agent.tools.write import author_probe as ap_mod
    monkeypatch.setattr(ap_mod, "GENERATED_PROBE_DIR", tmp_path / "discovery/generated")
    return tmp_path


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


def _good_proposal(
    *,
    family: str = "vwap_reject_fade",
    instrument: str = "NQ",
    timeframe: str = "5m",
    direction: str = "long",
    params: dict | None = None,
    mechanism: str | None = None,
    session_window: str = "ny_open_06_07_denver",
    revival_reason: str | None = None,
) -> dict:
    return {
        "family": family,
        "instrument": instrument,
        "timeframe": timeframe,
        "session_window": session_window,
        "direction": direction,
        "params": params or {
            "min_distance_ticks": 4, "max_distance_ticks": 12,
            "stop_ticks": 8, "target_ticks": 24,
        },
        "mechanism": mechanism or (
            "Trapped breakout buyers fading back through VWAP after a momentum "
            "probe fails to hold above the developing volume-weighted reference. "
            "Counterparties are the late chasers exiting their losing positions."
        ),
        "revival_reason": revival_reason,
    }


def _llm_returning(proposals: list[dict]):
    def llm(system: str, user: str) -> str:
        return json.dumps({"proposals": proposals})
    return llm


# ===========================================================================
# parse_proposals
# ===========================================================================


def test_parse_direct_json() -> None:
    data, err = parse_proposals('{"proposals": [{"x": 1}]}')
    assert err is None
    assert data == [{"x": 1}]


def test_parse_strips_code_fences() -> None:
    raw = "```json\n" + json.dumps({"proposals": [{"x": 1}]}) + "\n```"
    data, err = parse_proposals(raw)
    assert err is None
    assert data == [{"x": 1}]


def test_parse_finds_embedded_json() -> None:
    raw = "Sure! Here you go:\n" + json.dumps({"proposals": []}) + "\nThanks!"
    data, err = parse_proposals(raw)
    assert err is None and data == []


def test_parse_missing_proposals_list() -> None:
    data, err = parse_proposals('{"hypotheses": []}')
    assert data is None and err["code"] == "no_proposals_list"


def test_parse_invalid_json() -> None:
    # Starts with '{' so the parser tries json.loads → fails as invalid_json.
    data, err = parse_proposals("{not real")
    assert data is None and err["code"] == "invalid_json"


def test_parse_no_json_at_all() -> None:
    data, err = parse_proposals("just a sentence with no JSON anywhere")
    assert data is None and err["code"] == "no_json_found"


def test_parse_non_string() -> None:
    data, err = parse_proposals(None)  # type: ignore[arg-type]
    assert data is None and err["code"] == "non_string_response"


# ===========================================================================
# propose_hypotheses — happy path
# ===========================================================================


def test_proposes_and_registers_single_hypothesis(repo: Repository) -> None:
    llm = _llm_returning([_good_proposal()])
    report = propose_hypotheses(repo, llm_call=llm, n_proposals=3)
    assert report.parsed == 1
    assert report.accepted == 1
    assert report.rejected == 0
    rec = report.proposals[0]
    assert rec.accepted
    assert rec.hypothesis_id is not None
    # Check the registered hypothesis is in the ledger.
    h = repo.get_hypothesis(rec.hypothesis_id)
    assert h is not None and h.family == "vwap_reject_fade"
    # Draft was written.
    assert rec.draft_path and Path(rec.draft_path).exists()


def test_proposes_multiple_diverse_families(repo: Repository) -> None:
    proposals = [
        _good_proposal(family="vwap_reject_fade"),
        _good_proposal(family="orb_breakout",
                       params={"orb_minutes": 15, "signal_type": "break_close",
                                "stop_ticks": 8, "target_ticks": 50}),
        _good_proposal(family="overnight_high_low_sweep_reclaim",
                       params={"level_type": "onh", "sweep_min_ticks": 4,
                                "reclaim_within_bars": 2,
                                "stop_ticks": 8, "target_ticks": 24}),
    ]
    report = propose_hypotheses(repo, llm_call=_llm_returning(proposals),
                                  n_proposals=5)
    assert report.accepted == 3
    families = {p.family for p in report.proposals if p.accepted}
    assert families == {"vwap_reject_fade", "orb_breakout",
                          "overnight_high_low_sweep_reclaim"}


# ===========================================================================
# propose_hypotheses — validation rejections
# ===========================================================================


def test_rejects_forbidden_family(repo: Repository) -> None:
    bad = _good_proposal(family="legacy_imported")
    report = propose_hypotheses(repo, llm_call=_llm_returning([bad]),
                                  n_proposals=3)
    assert report.accepted == 0
    assert report.rejected == 1
    assert report.proposals[0].rejection_code == "forbidden_family"


def test_rejects_unknown_family(repo: Repository) -> None:
    bad = _good_proposal(family="not_a_real_family")
    report = propose_hypotheses(repo, llm_call=_llm_returning([bad]),
                                  n_proposals=3)
    assert report.accepted == 0
    # author_probe catches it at the precondition stage.
    assert report.proposals[0].rejection_code == "unknown_family"


def test_rejects_short_mechanism(repo: Repository) -> None:
    bad = _good_proposal(mechanism="too short")
    report = propose_hypotheses(repo, llm_call=_llm_returning([bad]))
    assert report.accepted == 0
    assert report.proposals[0].rejection_code == "mechanism_too_short"


def test_rejects_params_outside_whitelist(repo: Repository) -> None:
    bad = _good_proposal(params={"min_distance_ticks": -5})
    report = propose_hypotheses(repo, llm_call=_llm_returning([bad]))
    assert report.accepted == 0
    assert report.proposals[0].rejection_code == "params_not_in_whitelist"


def test_rejects_missing_field(repo: Repository) -> None:
    bad = _good_proposal()
    bad.pop("instrument")
    report = propose_hypotheses(repo, llm_call=_llm_returning([bad]))
    assert report.proposals[0].rejection_code == "missing_field"


def test_rejects_bad_direction(repo: Repository) -> None:
    bad = _good_proposal(direction="sideways")
    report = propose_hypotheses(repo, llm_call=_llm_returning([bad]))
    assert report.proposals[0].rejection_code == "bad_direction"


def test_duplicate_proposal_rejected(repo: Repository) -> None:
    proposals = [_good_proposal(), _good_proposal()]  # identical
    report = propose_hypotheses(repo, llm_call=_llm_returning(proposals),
                                  n_proposals=5)
    assert report.accepted == 1
    rejected = [p for p in report.proposals if not p.accepted]
    assert len(rejected) == 1
    assert rejected[0].rejection_code == "duplicate_hypothesis"


# ===========================================================================
# Coverage cap (C.3)
# ===========================================================================


def test_coverage_cap_blocks_over_concentration(repo: Repository) -> None:
    # 5 identical-family proposals (different params each), session_quota=5,
    # cap = 40% × 5 = 2 in one family. The 3rd onwards must be rejected.
    family = "vwap_reject_fade"
    proposals = [
        _good_proposal(family=family, params={
            "min_distance_ticks": 4 + i, "stop_ticks": 8, "target_ticks": 24,
        }, mechanism=f"Variant {i}: " + "x" * 60)
        for i in range(5)
    ]
    report = propose_hypotheses(repo, llm_call=_llm_returning(proposals),
                                  n_proposals=5, session_quota=5)
    accepted = [p for p in report.proposals if p.accepted]
    assert len(accepted) == 2
    cap_rejects = [p for p in report.proposals
                    if p.rejection_code == "coverage_cap_exceeded"]
    assert len(cap_rejects) == 3


def test_coverage_cap_allows_diverse_mix(repo: Repository) -> None:
    # 5 proposals, each a different family → no cap violation.
    proposals = [
        _good_proposal(family="vwap_reject_fade"),
        _good_proposal(family="orb_breakout", params={
            "orb_minutes": 5, "signal_type": "break_close",
            "stop_ticks": 8, "target_ticks": 30,
        }),
        _good_proposal(family="orb_failure_reclaim", params={
            "orb_minutes": 5, "sweep_min_ticks": 4,
            "reclaim_within_bars": 1, "fill_mode": "body_midpoint",
            "stop_ticks": 8, "target_ticks": 50,
        }),
        _good_proposal(family="prior_high_low_failed_breakout", params={
            "level_type": "prior_high", "break_buffer_ticks": 4,
            "max_failure_bars": 3, "stop_ticks": 8, "target_ticks": 30,
        }),
        _good_proposal(family="large_candle_origin_retest", params={
            "candle_size_ticks_min": 20, "retrace_pct": 0.5,
            "stop_ticks": 8, "target_ticks": 30,
        }),
    ]
    report = propose_hypotheses(repo, llm_call=_llm_returning(proposals),
                                  n_proposals=5, session_quota=5)
    assert report.accepted == 5


# ===========================================================================
# Quota enforcement
# ===========================================================================


def test_session_quota_caps_accepted_count(repo: Repository) -> None:
    # 3 proposals across 3 different families (so coverage cap is fine),
    # but session_quota=2 → only first 2 considered; 3rd hits session_quota_full.
    proposals = [
        _good_proposal(family="vwap_reject_fade"),
        _good_proposal(family="orb_breakout", params={
            "orb_minutes": 5, "signal_type": "break_close",
            "stop_ticks": 8, "target_ticks": 30,
        }),
        _good_proposal(family="prior_high_low_failed_breakout", params={
            "level_type": "prior_high", "break_buffer_ticks": 4,
            "max_failure_bars": 3, "stop_ticks": 8, "target_ticks": 30,
        }),
    ]
    report = propose_hypotheses(repo, llm_call=_llm_returning(proposals),
                                  n_proposals=3, session_quota=2)
    assert report.accepted == 2
    quota_rejects = [p for p in report.proposals
                      if p.rejection_code == "session_quota_full"]
    assert len(quota_rejects) == 1


def test_weekly_quota_exhausted_aborts_session(repo: Repository) -> None:
    # Pre-seed 25 author hypotheses so weekly quota is full.
    for i in range(25):
        repo.register_hypothesis(
            hypothesis_id=f"h_pre_{i:03d}",
            family="vwap_reject_fade",
            instrument="NQ", timeframe="5m",
            params={"min_distance_ticks": 4, "stop_ticks": 8,
                     "target_ticks": 24 + i, "max_distance_ticks": 12},
            mechanism=(f"Variant {i}: " + "x" * 80),
            registered_by="agent:hypothesis_author",
        )
    report = propose_hypotheses(repo, llm_call=_llm_returning([_good_proposal()]),
                                  n_proposals=5, weekly_quota=25)
    assert report.accepted == 0
    assert any(f["code"] == "weekly_quota_exhausted" for f in report.failures)


# ===========================================================================
# LLM failures
# ===========================================================================


def test_unparseable_response_retries(repo: Repository) -> None:
    calls = {"n": 0}

    def llm(system: str, user: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return "I can't comply."
        return json.dumps({"proposals": [_good_proposal()]})

    report = propose_hypotheses(repo, llm_call=llm, max_retries=1)
    assert report.accepted == 1
    assert calls["n"] == 2


def test_llm_exception_recorded(repo: Repository) -> None:
    def llm(system: str, user: str) -> str:
        raise RuntimeError("model crashed")

    report = propose_hypotheses(repo, llm_call=llm, max_retries=0)
    assert report.accepted == 0
    assert any(f["code"] == "llm_exception" for f in report.failures)


def test_always_unparseable_hits_failure_record(repo: Repository) -> None:
    def llm(system: str, user: str) -> str:
        return "still cannot comply"

    report = propose_hypotheses(repo, llm_call=llm, max_retries=1)
    assert report.parsed == 0
    assert report.accepted == 0
    assert report.failures  # at least one failure recorded


# ===========================================================================
# Inbox integration (proposal accept/reject)
# ===========================================================================


def test_proposal_draft_content_includes_metadata(repo: Repository) -> None:
    report = propose_hypotheses(repo, llm_call=_llm_returning([_good_proposal()]))
    rec = report.proposals[0]
    body = Path(rec.draft_path).read_text(encoding="utf-8")
    assert "hypothesis_id: " + rec.hypothesis_id in body
    assert "Family:** vwap_reject_fade" in body
    assert "min_distance_ticks" in body  # params block rendered


def test_inbox_reject_retires_proposal_hypothesis(repo: Repository) -> None:
    from ta_foundation.agent import inbox as inbox_mod
    report = propose_hypotheses(repo, llm_call=_llm_returning([_good_proposal()]))
    rec = report.proposals[0]
    draft_id = f"proposals/{rec.hypothesis_id}"
    out = inbox_mod.reject_draft(
        repo, draft_id,
        reason="mechanism is too generic; need more specific counterparty",
        rejected_by="human:test",
    )
    assert out["ok"]
    assert out["retired_hypothesis_id"] == rec.hypothesis_id
    h = repo.get_hypothesis(rec.hypothesis_id)
    assert h is not None and h.status == "retired"


def test_inbox_accept_moves_proposal_draft(repo: Repository) -> None:
    from ta_foundation.agent import inbox as inbox_mod
    report = propose_hypotheses(repo, llm_call=_llm_returning([_good_proposal()]))
    rec = report.proposals[0]
    draft_id = f"proposals/{rec.hypothesis_id}"
    out = inbox_mod.accept_draft(repo, draft_id, accepted_by="human:test")
    assert out["ok"]
    final = Path(out["final_path"])
    assert final.exists()
    # The hypothesis remains 'open' — accepting is just signoff.
    h = repo.get_hypothesis(rec.hypothesis_id)
    assert h is not None and h.status == "open"
